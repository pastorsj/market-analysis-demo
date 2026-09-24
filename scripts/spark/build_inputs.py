#!/usr/bin/env python3
"""Compute closed, deterministic source digests for the three local images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
from collections.abc import Iterable


SCHEMA_VERSION = "market-shock-build-input-v1"
LABEL_SCHEMA = "com.nvidia.market-shock.build-input-schema"
LABEL_DIGEST = "com.nvidia.market-shock.build-input-sha256"
SERVICES = ("web", "agent", "tools")
CONTEXTS = {
    "web": Path("apps/web"),
    "agent": Path("services/agent"),
    "tools": Path("services/tools"),
}
FILES = {
    "web": (
        ".dockerignore",
        "Dockerfile",
        "index.html",
        "nginx.conf",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "vite.config.ts",
    ),
    "agent": (".dockerignore", "Dockerfile", "pyproject.toml"),
    "tools": (".dockerignore", "Dockerfile", "pyproject.toml"),
}
DIRECTORIES = {
    "web": ("src",),
    "agent": ("skills", "src"),
    "tools": ("src",),
}
COPY_SOURCES = {
    "web": (
        "package.json",
        "pnpm-lock.yaml",
        "index.html",
        "tsconfig.json",
        "vite.config.ts",
        "src",
        "nginx.conf",
    ),
    "agent": ("pyproject.toml", "src", "skills"),
    "tools": ("pyproject.toml", "src"),
}
IGNORED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "node_modules",
    "dist",
    "test-results",
    "playwright-report",
}


class BuildInputError(ValueError):
    """The declared build inputs are missing or unsafe."""


def _read_regular_bytes(path: Path, service: str, relative: str) -> tuple[os.stat_result, bytes]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise BuildInputError(f"unsafe build input: {service}/{relative}")
            chunks = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            after = os.fstat(descriptor)
            bound = path.lstat()
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise BuildInputError(f"unreadable build input: {service}/{relative}") from exc
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_nlink,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if identity(before) != identity(after) or identity(after) != identity(bound):
        raise BuildInputError(f"changed build input: {service}/{relative}")
    return after, b"".join(chunks)


def _context_path(root: Path, service: str) -> Path:
    root = root.resolve(strict=True)
    context = root
    try:
        for part in CONTEXTS[service].parts:
            context /= part
            info = context.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise BuildInputError(f"unsafe build context: {service}")
    except OSError as exc:
        raise BuildInputError(f"unsafe build context: {service}") from exc
    return context


def _validate_copy_contract(context: Path, service: str) -> None:
    try:
        _, raw = _read_regular_bytes(context / "Dockerfile", service, "Dockerfile")
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise BuildInputError(f"unreadable Dockerfile: {service}") from exc
    sources: list[str] = []
    for line in lines:
        stripped = line.strip()
        if "--mount" in stripped.lower():
            raise BuildInputError(f"unsupported RUN mount: {service}")
        instruction = stripped.split(maxsplit=1)[0].upper() if stripped else ""
        if instruction == "ADD":
            raise BuildInputError(f"unsupported ADD instruction: {service}")
        if instruction != "COPY":
            continue
        try:
            fields = shlex.split(stripped)
        except ValueError as exc:
            raise BuildInputError(f"unsupported COPY instruction: {service}") from exc
        if len(fields) < 3 or fields[0].upper() != "COPY":
            raise BuildInputError(f"unsupported COPY instruction: {service}")
        if fields[1].startswith("--from="):
            continue
        if any(field.startswith("--") or any(mark in field for mark in "*?[") for field in fields[1:-1]):
            raise BuildInputError(f"unsupported COPY source: {service}")
        sources.extend(fields[1:-1])
    if tuple(sources) != COPY_SOURCES[service]:
        raise BuildInputError(f"Dockerfile COPY inventory drift: {service}")


def _paths(context: Path, service: str) -> Iterable[Path]:
    for relative in FILES[service]:
        yield context / relative
    for relative in DIRECTORIES[service]:
        directory = context / relative
        if not directory.is_dir() or directory.is_symlink():
            raise BuildInputError(f"unsafe build-input directory: {service}/{relative}")
        for path in directory.rglob("*"):
            nested = path.relative_to(context)
            if any(part in IGNORED_PARTS or part.endswith(".egg-info") for part in nested.parts):
                continue
            if path.is_symlink():
                raise BuildInputError(f"symlink build input: {service}/{nested.as_posix()}")
            if path.is_file():
                yield path
            elif not path.is_dir():
                raise BuildInputError(f"special build input: {service}/{nested.as_posix()}")


def _directory_paths(context: Path, service: str) -> Iterable[Path]:
    for relative in DIRECTORIES[service]:
        directory = context / relative
        if not directory.is_dir() or directory.is_symlink():
            raise BuildInputError(f"unsafe build-input directory: {service}/{relative}")
        yield directory
        for path in directory.rglob("*"):
            nested = path.relative_to(context)
            if any(part in IGNORED_PARTS or part.endswith(".egg-info") for part in nested.parts):
                continue
            if path.is_symlink():
                raise BuildInputError(f"symlink build input: {service}/{nested.as_posix()}")
            if path.is_dir():
                yield path


def service_build_input_manifest(root: Path, service: str) -> dict[str, object]:
    if service not in SERVICES:
        raise BuildInputError(f"unknown service: {service}")
    context = _context_path(root, service)
    _validate_copy_contract(context, service)
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for path in sorted(_paths(context, service), key=lambda item: item.relative_to(context).as_posix()):
        relative = path.relative_to(context).as_posix()
        if relative in seen:
            raise BuildInputError(f"duplicate build input: {service}/{relative}")
        seen.add(relative)
        info, raw = _read_regular_bytes(path, service, relative)
        rows.append(
            {
                "path": relative,
                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    if len(rows) < len(FILES[service]) + len(DIRECTORIES[service]):
        raise BuildInputError(f"incomplete build input: {service}")
    directories = []
    for path in sorted(
        _directory_paths(context, service), key=lambda item: item.relative_to(context).as_posix()
    ):
        relative = path.relative_to(context).as_posix()
        try:
            info = path.lstat()
        except OSError as exc:
            raise BuildInputError(f"unreadable build-input directory: {service}/{relative}") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise BuildInputError(f"unsafe build-input directory: {service}/{relative}")
        directories.append({"path": relative, "mode": f"{stat.S_IMODE(info.st_mode):04o}"})
    _context_path(root, service)
    return {"schema_version": SCHEMA_VERSION, "service": service, "files": rows, "directories": directories}


def service_build_input_digest(root: Path, service: str) -> str:
    manifest = service_build_input_manifest(root, service)
    raw = (
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
        + b"\n"
    )
    return hashlib.sha256(raw).hexdigest()


def build_input_digests(root: Path) -> dict[str, str]:
    return {service: service_build_input_digest(root, service) for service in SERVICES}


def create_build_snapshot(root: Path, destination: Path) -> dict[str, str]:
    try:
        destination.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink() or any(destination.iterdir()):
            raise BuildInputError("snapshot destination must be an empty directory")
    except OSError as exc:
        raise BuildInputError("snapshot destination is unsafe") from exc
    before = build_input_digests(root)
    for service in SERVICES:
        source_context = _context_path(root, service)
        target_context = destination / CONTEXTS[service]
        target_context.mkdir(parents=True)
        manifest = service_build_input_manifest(root, service)
        directory_modes = {row["path"]: int(row["mode"], 8) for row in manifest["directories"]}
        for relative in sorted(directory_modes, key=lambda value: (value.count("/"), value)):
            (target_context / relative).mkdir()
        for row in manifest["files"]:
            relative = str(row["path"])
            _, raw = _read_regular_bytes(source_context / relative, service, relative)
            target = target_context / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
            try:
                descriptor = os.open(target, flags, int(row["mode"], 8))
                try:
                    view = memoryview(raw)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("short snapshot write")
                        view = view[written:]
                finally:
                    os.close(descriptor)
                target.chmod(int(row["mode"], 8))
            except OSError as exc:
                raise BuildInputError(f"snapshot write failed: {service}/{relative}") from exc
        for relative, mode in sorted(
            directory_modes.items(), key=lambda item: (-item[0].count("/"), item[0])
        ):
            (target_context / relative).chmod(mode)
    snapshot = build_input_digests(destination)
    after = build_input_digests(root)
    if before != snapshot or snapshot != after:
        raise BuildInputError("build inputs changed while snapshotting")
    return snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--service", choices=SERVICES)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--snapshot-root", type=Path)
    args = parser.parse_args()
    if sum((args.service is not None, args.json, args.snapshot_root is not None)) != 1:
        parser.error("choose exactly one of --service, --json, or --snapshot-root")
    return args


def main() -> int:
    args = parse_args()
    if args.service:
        print(service_build_input_digest(args.root, args.service))
    elif args.snapshot_root:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "services": create_build_snapshot(args.root, args.snapshot_root),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        print(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "services": build_input_digests(args.root)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
