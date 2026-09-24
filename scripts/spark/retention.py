"""Offline, manifest-bound retention of stopped kiosk generations; dry-run by default.

Historical generations are preserved forever. This never deletes evaluation reports,
datasets, model files, OpenShell providers, or remote LangSmith data.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path("/srv/market-shock")
MIN_FREE_BYTES = 20 * 1024**3
MAX_TRACE_BYTES = 128 * 1024**2
MAX_STATE_BYTES = 2 * 1024**3
MAX_ARCHIVES = 3
MAX_ARCHIVE_BYTES = 512 * 1024**2
MAX_AGE_SECONDS = 86400
MARKER = ".visitor-generation.json"
LOCK = ".retention.lock"
STATE_FILES = {"agent.key", "switchyard-routing.jsonl", MARKER} | {
    name + suffix
    for name in ("investigations.sqlite3", "checkpoints.sqlite3")
    for suffix in ("", "-wal", "-shm", "-journal")
}
GUIDANCE = (
    "Stop with ./demo stop; inventory: python3 scripts/spark/retention.py inventory "
    "> /tmp/market-retention.json; preview reset or expire with "
    "python3 scripts/spark/retention.py reset --manifest /tmp/market-retention.json "
    "(or expire); review targets, then repeat with --apply. Re-inventory after each action. "
    "Historical archives are preserved; low disk may require operator-managed storage."
)


class RetentionError(RuntimeError):
    pass


def identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read_file(path, *, digest=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RetentionError("unsafe file")
        result = hashlib.sha256() if digest else bytearray()
        while chunk := os.read(fd, 1024 * 1024):
            if digest:
                result.update(chunk)
            else:
                result.extend(chunk)
                if len(result) > 16 * 1024 * 1024:
                    raise RetentionError("metadata too large")
        if identity(before) != identity(os.fstat(fd)) or identity(before) != identity(path.lstat()):
            raise RetentionError("file changed during inventory")
        return result.hexdigest() if digest else bytes(result)
    finally:
        os.close(fd)


def directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise RetentionError("unsafe directory or symlink")
    return info


def _runtime_directory_fd(root):
    """Open the fixed runtime directory without following its final component."""
    root = Path(root)
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        observed, bound = os.fstat(fd), root.lstat()
        if (
            not stat.S_ISDIR(observed.st_mode)
            or identity(observed) != identity(bound)
            or observed.st_uid != os.geteuid()
            or stat.S_IMODE(observed.st_mode) & 0o022
        ):
            raise RetentionError("unsafe runtime directory for retention lock")
        return fd
    except (OSError, RetentionError):
        try:
            os.close(fd)
        except (UnboundLocalError, OSError):
            pass
        raise RetentionError("unsafe runtime directory for retention lock") from None


def _validate_lock_fd(fd, root_fd):
    info = os.fstat(fd)
    bound = os.stat(LOCK, dir_fd=root_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or identity(info) != identity(bound)
    ):
        raise RetentionError("retention lock must be an owner-only regular file")


def _validate_runtime_lock_fd(fd, root):
    """Bind an inherited lock descriptor to the non-replaceable runtime directory."""
    info = os.fstat(fd)
    bound = Path(root).lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
        or identity(info) != identity(bound)
    ):
        raise RetentionError("invalid inherited retention lock")


def acquire_retention_lock(root=ROOT):
    """Lock the fixed runtime directory; the validated sentinel is not the mutex."""
    root_fd = _runtime_directory_fd(root)
    lock_fd = None
    try:
        lock_fd = os.open(
            LOCK,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=root_fd,
        )
        _validate_lock_fd(lock_fd, root_fd)
        try:
            fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RetentionError("retention/start operation already in progress") from None
        _validate_lock_fd(lock_fd, root_fd)
        _validate_runtime_lock_fd(root_fd, root)
        os.close(lock_fd)
        lock_fd = None
        return root_fd
    except (OSError, RetentionError):
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(root_fd)
        if isinstance(sys.exception(), RetentionError):
            raise
        raise RetentionError("unsafe retention lock") from None


def verify_retention_lock(fd, root=ROOT):
    """Validate and lock an inherited descriptor used by the guarded start."""
    if type(fd) is not int or fd < 3:
        raise RetentionError("invalid inherited retention lock")
    root_fd = _runtime_directory_fd(root)
    sentinel_fd = None
    try:
        _validate_runtime_lock_fd(fd, root)
        if identity(os.fstat(fd)) != identity(os.fstat(root_fd)):
            raise RetentionError("invalid inherited retention lock")
        sentinel_fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        _validate_lock_fd(sentinel_fd, root_fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RetentionError("retention/start operation already in progress") from None
        _validate_runtime_lock_fd(fd, root)
    except OSError:
        raise RetentionError("invalid inherited retention lock") from None
    finally:
        if sentinel_fd is not None:
            os.close(sentinel_fd)
        os.close(root_fd)


def guarded_start(command, root=ROOT):
    """Run one startup command while retaining exclusive ownership of the lock."""
    if not command or any(not isinstance(item, str) or not item or "\0" in item for item in command):
        raise RetentionError("invalid guarded start command")
    fd = acquire_retention_lock(root)
    try:
        env = dict(os.environ)
        env["SPARK_RETENTION_LOCK_FD"] = str(fd)
        return subprocess.run(command, env=env, pass_fds=(fd,), check=False).returncode
    finally:
        os.close(fd)


def write_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def agent_stopped():
    env = dict(os.environ)
    for name, suffix in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ):
        env[name] = str(ROOT / "openshell" / suffix)
    try:
        result = subprocess.run(
            [
                str(ROOT / "openshell/0.0.116/openshell"),
                "-g",
                "market-shock",
                "sandbox",
                "get",
                "market-agent",
                "--output",
                "json",
            ],
            env=env,
            capture_output=True,
            timeout=30,
            check=True,
            text=True,
        )
        if json.loads(result.stdout).get("phase") != "Stopped":
            return False
        identifiers = subprocess.run(
            ["docker", "ps", "--quiet", "--no-trunc"],
            capture_output=True,
            timeout=15,
            check=True,
            text=True,
        ).stdout.splitlines()
        if not identifiers:
            return True
        census = json.loads(
            subprocess.run(
                ["docker", "inspect", *identifiers],
                capture_output=True,
                timeout=30,
                check=True,
                text=True,
            ).stdout
        )
        if not isinstance(census, list) or len(census) != len(identifiers):
            return False
        protected = (ROOT / "state", ROOT / "traces")
        for container in census:
            if not isinstance(container, dict) or not isinstance(container.get("State"), dict):
                return False
            if container["State"].get("Running") is not True:
                continue
            mounts = container.get("Mounts")
            if not isinstance(mounts, list):
                return False
            for mount in mounts:
                if not isinstance(mount, dict):
                    return False
                if mount.get("Type") != "bind" or mount.get("RW") is not True:
                    continue
                source = mount.get("Source")
                if not isinstance(source, str) or not source.startswith("/"):
                    return False
                source_path = Path(os.path.normpath(source))
                if any(
                    source_path == target or source_path in target.parents or target in source_path.parents
                    for target in protected
                ):
                    return False
        return True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


class Retention:
    def __init__(self, root=ROOT, *, stopped=agent_stopped, clock=time.time, test_root=False):
        self.root = Path(root)
        if (
            not self.root.is_absolute()
            or self.root.resolve() != self.root
            or (not test_root and self.root != ROOT)
        ):
            raise RetentionError("unsafe runtime root")
        directory(self.root)
        self.stopped, self.clock = stopped, clock

    def files(self, base, *, hashes=True):
        rows = []
        for area in ("state", "traces"):
            path = base / area
            before = directory(path)
            for item in sorted(path.iterdir()):
                allowed = STATE_FILES if area == "state" else {"relay-events.jsonl"}
                if item.name not in allowed:
                    raise RetentionError("unsafe unrecognized generation file; preserve for operator review")
                info = item.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise RetentionError("unsafe generation file or symlink")
                row = dict(path=f"{area}/{item.name}", size=info.st_size, mode=stat.S_IMODE(info.st_mode))
                if hashes:
                    row["sha256"] = read_file(item, digest=True)
                    if identity(info) != identity(item.lstat()):
                        raise RetentionError("file changed during inventory")
                rows.append(row)
            if identity(before) != identity(path.lstat()):
                raise RetentionError("directory changed during inventory")
        return rows

    def marker(self, base):
        path = base / "state" / MARKER
        if not path.exists() and not path.is_symlink():
            return None
        value = json.loads(read_file(path))
        if (
            path.stat().st_mode & 0o777 != 0o600
            or path.stat().st_uid != os.geteuid()
            or set(value) != {"id", "created", "classification"}
            or value["classification"] != "visitor"
            or str(uuid.UUID(value["id"])) != value["id"]
            or not isinstance(value["created"], (int, float))
        ):
            raise RetentionError("invalid visitor marker")
        return value

    def inventory(self, *, hashes=True):
        if (self.root / ".retention-incomplete").exists():
            raise RetentionError("incomplete reset; preserve both generations and request operator recovery")
        rows = self.files(self.root, hashes=hashes)
        marker = self.marker(self.root)
        archives = []
        parent = self.root / "visitor-archives"
        if parent.exists() or parent.is_symlink():
            directory(parent)
            for path in sorted(parent.iterdir()):
                directory(path)
                if str(uuid.UUID(path.name)) != path.name:
                    raise RetentionError("unsafe archive identity")
                raw_manifest = read_file(path / "manifest.json")
                record = json.loads(raw_manifest)
                files = self.files(path, hashes=hashes)
                expected = record.get("files", [])
                if not hashes:
                    expected = [{k: v for k, v in row.items() if k != "sha256"} for row in expected]
                if (
                    set(record) != {"id", "created", "classification", "files"}
                    or record.get("id") != path.name
                    or record.get("classification") not in ("historical", "visitor")
                    or files != expected
                    or set(p.name for p in path.iterdir()) != {"state", "traces", "manifest.json"}
                ):
                    raise RetentionError("archive manifest mismatch")
                archived_marker = self.marker(path)
                if (record["classification"] == "visitor") != (archived_marker is not None):
                    raise RetentionError("archive classification mismatch")
                if archived_marker and record["created"] != archived_marker["created"]:
                    raise RetentionError("archive generation time mismatch")
                archives.append({**record, "files": files, "manifest_bytes": len(raw_manifest)})
        return dict(
            schema_version=1,
            root=str(self.root),
            classification="visitor" if marker else "historical",
            active=rows,
            archives=archives,
        )

    def expiry_targets(self, inventory):
        rows = sorted(
            (a for a in inventory["archives"] if a["classification"] == "visitor"),
            key=lambda a: (a["created"], a["id"]),
        )
        targets = []
        while rows and (
            len(rows) > MAX_ARCHIVES
            or sum(sum(r["size"] for r in a["files"]) + a["manifest_bytes"] for a in rows) > MAX_ARCHIVE_BYTES
            or self.clock() - rows[0]["created"] >= MAX_AGE_SECONDS
        ):
            targets.append(rows.pop(0)["id"])
        return targets

    def check_budget(self, *, free_bytes=None):
        inventory = self.inventory(hashes=False)
        free = shutil.disk_usage(self.root).free if free_bytes is None else free_bytes
        state = sum(r["size"] for r in inventory["active"] if r["path"].startswith("state/"))
        trace = sum(r["size"] for r in inventory["active"] if r["path"].startswith("traces/"))
        failed = []
        if free < MIN_FREE_BYTES:
            failed.append("free disk below 20 GiB")
        if state > MAX_STATE_BYTES:
            failed.append("state/checkpoint family exceeds 2 GiB")
        if trace > MAX_TRACE_BYTES:
            failed.append("Relay traces exceed 128 MiB")
        if self.expiry_targets(inventory):
            failed.append("visitor archives exceed 24 hours / three archives / 512 MiB")
        if failed:
            raise RetentionError("retention budget: " + "; ".join(failed) + ". " + GUIDANCE)
        return dict(ok=True, free_bytes=free, state_bytes=state, trace_bytes=trace)

    def validate(self, manifest):
        if self.root.resolve() != self.root:
            raise RetentionError("unsafe root changed during operation")
        if manifest != self.inventory():
            raise RetentionError("prior manifest changed; inventory again")
        if not self.stopped():
            raise RetentionError("agent must be confirmed stopped; unknown status refuses mutation")

    def reset(self, manifest, *, apply=False):
        if manifest != self.inventory():
            raise RetentionError("prior manifest changed; inventory again")
        noop = manifest["classification"] == "visitor" and all(
            r["path"] == f"state/{MARKER}" for r in manifest["active"]
        )
        result = dict(
            apply=apply, noop=noop, classification=manifest["classification"], targets=["state", "traces"]
        )
        if not apply or noop:
            return result
        self.validate(manifest)
        archive_root = self.root / "visitor-archives"
        archive_root.mkdir(mode=0o700, exist_ok=True)
        if stat.S_IMODE(directory(archive_root).st_mode) != 0o700:
            raise RetentionError("archive directory must be owner-only (0700)")
        archive_id = str(uuid.uuid4())
        archive = archive_root / archive_id
        archive.mkdir(mode=0o700)
        pending = self.root / ".retention-incomplete"
        write_json(pending, {"archive": archive_id})
        # No unlink occurs here: interruption preserves all bytes and blocks start.
        for area in ("state", "traces"):
            directory(self.root / area)
            os.rename(self.root / area, archive / area)
        if self.files(archive) != manifest["active"]:
            raise RetentionError("generation changed during reset; preserved incomplete archive")
        for area in ("state", "traces"):
            fd = os.open(archive / area, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fchmod(fd, 0o700)
                for row in (r for r in manifest["active"] if r["path"].startswith(area + "/")):
                    name = Path(row["path"]).name
                    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        info = os.fstat(child)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise RetentionError("unsafe archived file")
                        os.fchmod(child, 0o600)
                    finally:
                        os.close(child)
            finally:
                os.close(fd)
        marker = self.marker(archive)
        created = marker["created"] if marker else self.clock()
        record = dict(
            id=archive_id,
            created=created,
            classification=manifest["classification"],
            files=self.files(archive),
        )
        write_json(archive / "manifest.json", record)
        for area in ("state", "traces"):
            (self.root / area).mkdir(mode=0o700)
        write_json(
            self.root / "state" / MARKER,
            dict(id=str(uuid.uuid4()), created=self.clock(), classification="visitor"),
        )
        pending.unlink()
        return {**result, "archive": archive_id}

    def expire(self, manifest, *, apply=False):
        if manifest != self.inventory():
            raise RetentionError("prior manifest changed; inventory again")
        targets = self.expiry_targets(manifest)
        if apply and targets:
            self.validate(manifest)
            for name in targets:
                archive = self.root / "visitor-archives" / name
                record = next(a for a in manifest["archives"] if a["id"] == name)
                if self.files(archive) != record["files"] or not self.stopped():
                    raise RetentionError("archive changed or agent not stopped")
                self.remove_archive(archive, record)
        return dict(apply=apply, targets=targets)

    def remove_archive(self, archive, record):
        # Anchored directory descriptors and per-file digests avoid following links.
        fd = os.open(archive, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if identity(os.fstat(fd)) != identity(archive.lstat()):
                raise RetentionError("archive directory changed during expiry")
            for area in ("state", "traces"):
                child = os.open(area, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    for row in (r for r in record["files"] if r["path"].startswith(area + "/")):
                        name = Path(row["path"]).name
                        path = Path(f"/proc/self/fd/{child}") / name
                        before = path.lstat()
                        if (
                            read_file(path, digest=True) != row["sha256"]
                            or before.st_size != row["size"]
                            or stat.S_IMODE(before.st_mode) != row["mode"]
                            or identity(before) != identity(path.lstat())
                        ):
                            raise RetentionError("archive changed during expiry")
                        os.unlink(name, dir_fd=child)
                finally:
                    os.close(child)
                os.rmdir(area, dir_fd=fd)
            os.unlink("manifest.json", dir_fd=fd)
        finally:
            os.close(fd)
        archive.rmdir()


def main():
    try:
        if sys.argv[1:2] == ["guard-start"]:
            if len(sys.argv) < 4 or sys.argv[2] != "--":
                raise RetentionError("guard-start requires a command after --")
            raise SystemExit(guarded_start(sys.argv[3:]))
        if sys.argv[1:2] == ["verify-start-lock"]:
            if len(sys.argv) != 3 or not sys.argv[2].isdigit():
                raise RetentionError("verify-start-lock requires one descriptor")
            verify_retention_lock(int(sys.argv[2]))
            return
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("action", choices=("inventory", "check", "reset", "expire"))
        parser.add_argument("--manifest", type=Path)
        parser.add_argument("--apply", action="store_true")
        args = parser.parse_args()
        store = Retention()
        if args.apply and args.action not in ("reset", "expire"):
            raise RetentionError("--apply is only valid for reset/expire")
        if args.action in ("inventory", "check"):
            result = store.inventory() if args.action == "inventory" else store.check_budget()
        else:
            if args.manifest is None:
                raise RetentionError("reset/expire require --manifest from prior inventory")
            manifest = json.loads(read_file(args.manifest))
            if args.apply:
                fd = acquire_retention_lock()
                try:
                    result = getattr(store, args.action)(manifest, apply=True)
                finally:
                    os.close(fd)
            else:
                result = getattr(store, args.action)(manifest)
        print(json.dumps(result, sort_keys=True))
    except (RetentionError, OSError, ValueError, KeyError, TypeError) as error:
        # Never include file contents or arbitrary upstream output in operator errors.
        message = (
            str(error) if isinstance(error, RetentionError) else "retention inventory/operation failed closed"
        )
        raise SystemExit(message) from None


if __name__ == "__main__":
    main()
