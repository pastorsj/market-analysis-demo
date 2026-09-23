"""Closed, content-free preparation receipts for the pinned OpenShell runtime.

The caller persists the receipt at
/srv/market-shock/openshell/prepared/runtime.json. This module reads artifacts,
but never launches commands, reads version output, or stores artifact contents.
The caller must qualify the executable version before creating its receipt.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping


VERSION = "0.0.116"
SCHEMA = "market-shock-openshell-runtime-v1"
FILE_ROLES = frozenset({
    "binary", "gateway", "supervisor", "gateway_config", "policy", "env",
    "provider_list", "base_policy",
})
FIELDS = frozenset({"schema_version", "version", "image_id", "source_build_input", "files"})
HEX = re.compile(r"[a-f0-9]{64}")


class ReceiptError(ValueError):
    """An artifact or preparation receipt does not match its pinned identity."""


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _parents(path: Path) -> list[tuple[Path, tuple[int, ...]]]:
    if not path.is_absolute() or ".." in path.parts:
        raise ReceiptError("artifact_path")
    result = []
    for parent in reversed(path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ReceiptError("artifact_parent")
        result.append((parent, (info.st_dev, info.st_ino, info.st_mode)))
    return result


def _digest(path: Path) -> str:
    descriptor = None
    try:
        parents = _parents(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ReceiptError("artifact_file")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        if _identity(before) != _identity(os.fstat(descriptor)) or _identity(before) != _identity(path.lstat()):
            raise ReceiptError("artifact_changed")
        if parents != _parents(path):
            raise ReceiptError("artifact_parent_changed")
        return digest.hexdigest()
    except OSError:
        raise ReceiptError("artifact_unreadable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _pins(version: str, image_id: str, source_build_input: str) -> None:
    if version != VERSION:
        raise ReceiptError("version_pin")
    if not isinstance(image_id, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None:
        raise ReceiptError("image_pin")
    if not isinstance(source_build_input, str) or HEX.fullmatch(source_build_input) is None:
        raise ReceiptError("source_pin")


def create_receipt(
    files: Mapping[str, Path], *, image_id: str, source_build_input: str,
    version: str = VERSION,
) -> dict[str, Any]:
    """Hash exactly the required prepared artifacts after version qualification."""
    _pins(version, image_id, source_build_input)
    if not isinstance(files, Mapping) or set(files) != FILE_ROLES:
        raise ReceiptError("file_roles")
    rows = {}
    for role in sorted(FILE_ROLES):
        if not isinstance(files[role], (str, Path)):
            raise ReceiptError("artifact_path")
        path = Path(files[role])
        rows[role] = {"path": str(path), "sha256": _digest(path)}
    if len({row["path"] for row in rows.values()}) != len(rows):
        raise ReceiptError("duplicate_artifact")
    return {"schema_version": SCHEMA, "version": version, "image_id": image_id,
            "source_build_input": source_build_input, "files": rows}


def validate_receipt(
    receipt: Mapping[str, Any], *, image_id: str, source_build_input: str,
    version: str = VERSION,
) -> None:
    """Require exact pins and recompute every artifact digest; fail closed."""
    _pins(version, image_id, source_build_input)
    if not isinstance(receipt, Mapping) or set(receipt) != FIELDS:
        raise ReceiptError("receipt_shape")
    if (receipt["schema_version"], receipt["version"], receipt["image_id"], receipt["source_build_input"]) != (SCHEMA, version, image_id, source_build_input):
        raise ReceiptError("receipt_identity")
    rows = receipt["files"]
    if not isinstance(rows, Mapping) or set(rows) != FILE_ROLES:
        raise ReceiptError("file_roles")
    files = {}
    for role, row in rows.items():
        if not isinstance(row, Mapping) or set(row) != {"path", "sha256"}:
            raise ReceiptError("file_shape")
        if not isinstance(row["path"], str) or not isinstance(row["sha256"], str) or HEX.fullmatch(row["sha256"]) is None:
            raise ReceiptError("file_identity")
        path = Path(row["path"])
        if str(path) != row["path"]:
            raise ReceiptError("artifact_path")
        files[role] = path
    current = create_receipt(files, image_id=image_id, source_build_input=source_build_input, version=version)
    if current != receipt:
        raise ReceiptError("artifact_digest_drift")
