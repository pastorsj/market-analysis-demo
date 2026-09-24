#!/usr/bin/env python3
"""Fail-closed checks on the Compose runtime: image receipts, executables, and published ports.

compose.yaml is the single source of each service's command; these checks only
guard invariants that Compose itself cannot express.

Usage:
  process_contract.py receipt PATH               print "service<TAB>name<TAB>id<TAB>build_input" rows
  process_contract.py compose < config.json      no entrypoint overrides; only model sets a command
  process_contract.py static-ports < config.json only web publishes 127.0.0.1:3000
  process_contract.py running-ports < ps.jsonl   same invariant for running containers
  process_contract.py running SERVICE IMAGE_ENTRYPOINT CONTAINER_ENTRYPOINT EXPECTED_CMD CONTAINER_CMD
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

COMPOSE_SERVICES = frozenset({"web", "tools", "model"})
MODEL_IMAGE = (
    "vllm/vllm-openai:v0.27.1@sha256:0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967"
)
IMAGE_NAMES = {
    "web": "market-shock-web:latest",
    "agent": "market-shock-agent:latest",
    "tools": "market-shock-tools:latest",
    "model": MODEL_IMAGE,
}
PUBLISHED = ("web", "127.0.0.1", 3000, 3000)
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}")
HEX_DIGEST = re.compile(r"[a-f0-9]{64}")
MAX_BYTES = 1_048_576


class ContractError(ValueError):
    """The rendered or running runtime does not satisfy an invariant."""


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ContractError("duplicate JSON key")
    return result


def _loads(raw: bytes | str) -> Any:
    return json.loads(raw, object_pairs_hook=_closed_object)


def _read_private(path: Path) -> bytes:
    """Read an owner-only regular file without following symlinks."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise ContractError("receipt unreadable") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 65_536
        ):
            raise ContractError("receipt must be an owner-only (0600) regular file")
        return os.read(descriptor, 65_537)
    finally:
        os.close(descriptor)


def runtime_receipt(path: Path) -> dict[str, dict[str, str]]:
    """Validate runtime-images.json and return its per-service rows."""
    receipt = _loads(_read_private(path))
    if not isinstance(receipt, dict) or set(receipt) != {"schema_version", "images"}:
        raise ContractError("runtime receipt shape")
    images = receipt["images"]
    if receipt["schema_version"] != 1 or not isinstance(images, dict) or set(images) != set(IMAGE_NAMES):
        raise ContractError("runtime receipt shape")
    for service, name in IMAGE_NAMES.items():
        row = images[service]
        fields = {"name", "id"} | ({"build_input_sha256"} if service != "model" else set())
        if not isinstance(row, dict) or set(row) != fields or row["name"] != name:
            raise ContractError(f"runtime receipt row: {service}")
        if not IMAGE_ID.fullmatch(str(row["id"])):
            raise ContractError(f"runtime receipt image id: {service}")
        if service != "model" and not HEX_DIGEST.fullmatch(str(row["build_input_sha256"])):
            raise ContractError(f"runtime receipt build input: {service}")
    return images


def _services(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_BYTES:
        raise ContractError("Compose rendering size")
    document = _loads(raw)
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict) or set(services) != COMPOSE_SERVICES:
        raise ContractError("Compose must define exactly web, tools, and model")
    return services


def validate_compose_config(raw: bytes) -> None:
    """Built images keep their own entrypoint/command; only the stock vLLM image takes a command."""
    for service, row in _services(raw).items():
        if row.get("entrypoint") is not None:
            raise ContractError(f"Compose entrypoint override: {service}")
        command = row.get("command")
        if service == "model":
            if not isinstance(command, list) or not command:
                raise ContractError("Compose model command missing")
        elif command is not None:
            raise ContractError(f"Compose command override: {service}")


def validate_static_ports(raw: bytes) -> None:
    published = []
    for service, row in _services(raw).items():
        for port in row.get("ports") or []:
            published.append(
                (service, port.get("host_ip"), int(port.get("published", 0)), port.get("target"))
            )
    if published != [PUBLISHED]:
        raise ContractError("only web may publish a host port, on 127.0.0.1:3000")


def validate_running_ports(lines: list[str]) -> None:
    published = set()
    for line in lines:
        if not line.strip():
            continue
        value = _loads(line)
        for row in value if isinstance(value, list) else [value]:
            service = row.get("Service")
            if service not in COMPOSE_SERVICES:
                raise ContractError(f"unexpected Compose container: {service}")
            for port in row.get("Publishers") or []:
                if port.get("PublishedPort"):
                    published.add((service, port.get("URL"), port["PublishedPort"], port.get("TargetPort")))
    if not published <= {PUBLISHED}:
        raise ContractError("only web may publish a host port, on 127.0.0.1:3000")


def validate_running_process(
    service: str, image_entrypoint: str, container_entrypoint: str, expected_command: str, command: str
) -> None:
    """A running container executes its image's entrypoint with the expected command."""
    if service not in COMPOSE_SERVICES:
        raise ContractError("unknown service")
    if _loads(container_entrypoint) != _loads(image_entrypoint):
        raise ContractError(f"running entrypoint drift: {service}")
    if _loads(command) != _loads(expected_command):
        raise ContractError(f"running command drift: {service}")


def main(argv: list[str]) -> int:
    try:
        match argv:
            case ["receipt", path]:
                for service, row in runtime_receipt(Path(path)).items():
                    print(service, row["name"], row["id"], row.get("build_input_sha256", "-"), sep="\t")
            case ["compose"]:
                validate_compose_config(sys.stdin.buffer.read(MAX_BYTES + 1))
            case ["static-ports"]:
                validate_static_ports(sys.stdin.buffer.read(MAX_BYTES + 1))
            case ["running-ports"]:
                validate_running_ports(sys.stdin.read(MAX_BYTES + 1).splitlines())
            case ["running", service, *documents] if len(documents) == 4:
                validate_running_process(service, *documents)
            case _:
                raise ContractError("usage: see module docstring")
    except (
        ContractError,
        json.JSONDecodeError,
        UnicodeError,
        TypeError,
        ValueError,
        AttributeError,
    ) as error:
        print(f"process contract: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
