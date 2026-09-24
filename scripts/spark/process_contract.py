#!/usr/bin/env python3
"""Fail-closed executable contracts for the four-service demo runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


MAX_COMPOSE_BYTES = 1_048_576
SERVICE_NAMES = frozenset({"web", "agent", "tools", "model"})
IMAGE_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
MODEL_COMMAND = [
    "--model",
    "/models/nemotron-3.5-lightning-nvfp4-bee7596",
    "--served-model-name",
    "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4",
    "--port",
    "8001",
    "--moe-backend",
    "marlin",
    "--kv-cache-dtype",
    "fp8",
    "--enable-prefix-caching",
    "--max-model-len",
    "32768",
    "--max-num-seqs",
    "2",
    "--gpu-memory-utilization",
    "0.65",
    "--speculative_config.model",
    "/models/nemotron-3.5-lightning-dspark-8a01771",
    "--speculative_config.num_speculative_tokens",
    "3",
    "--mamba-backend",
    "flashinfer",
    "--mamba-cache-mode",
    "align",
    "--reasoning-parser",
    "nemotron_v3",
    "--enable-auto-tool-choice",
    "--tool-call-parser",
    "qwen3_coder",
    "--structured-outputs-config",
    '{"backend":"xgrammar","disable_any_whitespace":true}',
]


class ContractError(ValueError):
    """The rendered or running executable contract is not canonical."""


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON key")
        result[key] = value
    return result


def _loads(raw: bytes | str) -> Any:
    return json.loads(raw, object_pairs_hook=_closed_object)


def _receipt_bytes(path: Path, expected_mode: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
    except OSError as exc:
        raise ContractError("receipt unreadable") from exc
    try:
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != expected_mode
            or before.st_size > 65_536
        ):
            raise ContractError("receipt file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65_536):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        bound = path.lstat()
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
            raise ContractError("receipt changed")
        return b"".join(chunks)
    except OSError as exc:
        raise ContractError("receipt changed") from exc
    finally:
        os.close(descriptor)


def receipt_image_id(path: Path, role: str) -> str:
    """Return only an ID from a closed, stable, exact receipt schema."""

    if role == "market-prep":
        receipt = _loads(_receipt_bytes(path, 0o644))
        fields = {
            "schema_version",
            "image",
            "image_id",
            "architecture",
            "base_digest",
            "lock_sha256",
            "scope",
        }
        if not isinstance(receipt, dict) or set(receipt) != fields:
            raise ContractError("market-prep receipt shape")
        if (
            type(receipt["schema_version"]) is not int
            or receipt["schema_version"] != 1
            or receipt["image"] != "market-shock/market-prep:phase9"
            or receipt["architecture"] != "linux/arm64"
            or receipt["scope"] != "preparation_only"
            or IMAGE_ID.fullmatch(str(receipt["image_id"])) is None
            or IMAGE_ID.fullmatch(str(receipt["base_digest"])) is None
            or HEX_DIGEST.fullmatch(str(receipt["lock_sha256"])) is None
        ):
            raise ContractError("market-prep receipt binding")
        return str(receipt["image_id"])
    if role != "agent":
        raise ContractError("receipt role")
    receipt = _loads(_receipt_bytes(path, 0o600))
    if not isinstance(receipt, dict) or set(receipt) != {"schema_version", "images"}:
        raise ContractError("runtime receipt shape")
    images = receipt.get("images")
    names = {
        "web": "market-shock-web:latest",
        "agent": "market-shock-agent:latest",
        "tools": "market-shock-tools:latest",
        "model": "vllm/vllm-openai:v0.27.1@sha256:0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967",
    }
    if (
        type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != 1
        or not isinstance(images, dict)
        or set(images) != set(names)
    ):
        raise ContractError("runtime receipt shape")
    for service, name in names.items():
        row = images[service]
        fields = {"name", "id"} | ({"build_input_sha256"} if service != "model" else set())
        if (
            not isinstance(row, dict)
            or set(row) != fields
            or row.get("name") != name
            or IMAGE_ID.fullmatch(str(row.get("id"))) is None
        ):
            raise ContractError("runtime receipt binding")
        if service != "model" and HEX_DIGEST.fullmatch(str(row.get("build_input_sha256"))) is None:
            raise ContractError("runtime receipt binding")
    return str(images["agent"]["id"])


def validate_compose_config(raw: bytes) -> None:
    """Reject every Compose process override except the pinned model command."""

    if not raw or len(raw) > MAX_COMPOSE_BYTES:
        raise ContractError("Compose rendering size")
    document = _loads(raw)
    if not isinstance(document, dict):
        raise ContractError("Compose rendering shape")
    services = document.get("services")
    if not isinstance(services, dict) or set(services) != SERVICE_NAMES:
        raise ContractError("Compose service inventory")
    for service in sorted(SERVICE_NAMES):
        row = services[service]
        if not isinstance(row, dict):
            raise ContractError(f"Compose service shape: {service}")
        if service == "agent":
            if row.get("profiles") != ["image-only"] or row.get("entrypoint") != ["/bin/false"]:
                raise ContractError("Compose agent must be a disabled image-only role")
        elif row.get("entrypoint") is not None:
            raise ContractError(f"Compose entrypoint override: {service}")
        command = row.get("command")
        expected = MODEL_COMMAND if service == "model" else None
        if command != expected:
            raise ContractError(f"Compose command override: {service}")


def validate_running_process(
    service: str,
    image_entrypoint_raw: str,
    image_command_raw: str,
    container_entrypoint_raw: str,
    container_command_raw: str,
) -> None:
    """Bind a running container's executable to its receipt-pinned image."""

    if service not in SERVICE_NAMES:
        raise ContractError("unknown service")
    image_entrypoint = _loads(image_entrypoint_raw)
    image_command = _loads(image_command_raw)
    container_entrypoint = _loads(container_entrypoint_raw)
    container_command = _loads(container_command_raw)
    if container_entrypoint != image_entrypoint:
        raise ContractError(f"running entrypoint drift: {service}")
    expected_command = MODEL_COMMAND if service == "model" else image_command
    if container_command != expected_command:
        raise ContractError(f"running command drift: {service}")


def main(argv: list[str]) -> int:
    try:
        if argv == ["compose"]:
            raw = sys.stdin.buffer.read(MAX_COMPOSE_BYTES + 1)
            validate_compose_config(raw)
        elif len(argv) == 6 and argv[0] == "running":
            validate_running_process(argv[1], *argv[2:])
        elif len(argv) == 3 and argv[0] == "receipt-image":
            print(receipt_image_id(Path(argv[1]), argv[2]))
        else:
            raise ContractError("usage")
    except (ContractError, json.JSONDecodeError, UnicodeError, TypeError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
