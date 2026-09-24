"""Fail-closed host GPU telemetry for the supported DGX Spark runtime."""

from __future__ import annotations

import csv
import io
import re
import subprocess
from typing import Any
from collections.abc import Mapping


SUPPORTED_GPU = "NVIDIA GB10"
MEMORY_REPORTED = "measured"
MEMORY_UNIFIED_NOT_EXPOSED = "unsupported_unified_memory"
MEMORY_UNAVAILABLE = "unavailable"
GPU_UUID = re.compile(r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
GPU_FIELDS = {
    "gpu_device_name",
    "gpu_device_uuid",
    "gpu_memory_used_mib",
    "gpu_memory_used_status",
    "gpu_utilization_percent",
}


def unavailable_gpu_telemetry() -> dict[str, object]:
    return {
        "gpu_device_name": None,
        "gpu_device_uuid": None,
        "gpu_memory_used_mib": None,
        "gpu_memory_used_status": MEMORY_UNAVAILABLE,
        "gpu_utilization_percent": None,
    }


def parse_nvidia_smi(output: str) -> dict[str, object]:
    """Parse the exact single-GB10 query used by the rehearsal harness."""

    rows = list(csv.reader(io.StringIO(output)))
    if len(rows) != 1 or len(rows[0]) != 4:
        raise ValueError("gpu_telemetry_shape")
    model, uuid, memory_text, utilization_text = (value.strip() for value in rows[0])
    if model != SUPPORTED_GPU:
        raise ValueError("gpu_telemetry_model")
    if GPU_UUID.fullmatch(uuid) is None:
        raise ValueError("gpu_telemetry_uuid")
    try:
        utilization = int(utilization_text)
    except ValueError as exc:
        raise ValueError("gpu_telemetry_utilization") from exc
    if not 0 <= utilization <= 100:
        raise ValueError("gpu_telemetry_utilization")
    if memory_text == "[N/A]":
        memory: int | None = None
        observation = MEMORY_UNIFIED_NOT_EXPOSED
    else:
        try:
            memory = int(memory_text)
        except ValueError as exc:
            raise ValueError("gpu_telemetry_memory") from exc
        if memory < 0:
            raise ValueError("gpu_telemetry_memory")
        observation = MEMORY_REPORTED
    return {
        "gpu_device_name": model,
        "gpu_device_uuid": uuid,
        "gpu_memory_used_mib": memory,
        "gpu_memory_used_status": observation,
        "gpu_utilization_percent": utilization,
    }


def capture_gpu_telemetry() -> dict[str, object]:
    """Capture telemetry without turning an observation error into a zero."""

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,uuid,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return parse_nvidia_smi(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return unavailable_gpu_telemetry()


def valid_gpu_telemetry(value: Mapping[str, Any]) -> bool:
    """Accept reported memory or the GB10's explicit unified-memory NVML gap."""

    if value.get("gpu_device_name") != SUPPORTED_GPU:
        return False
    if GPU_UUID.fullmatch(str(value.get("gpu_device_uuid", ""))) is None:
        return False
    utilization = value.get("gpu_utilization_percent")
    if type(utilization) is not int or not 0 <= utilization <= 100:
        return False
    memory = value.get("gpu_memory_used_mib")
    observation = value.get("gpu_memory_used_status")
    return (observation == MEMORY_REPORTED and type(memory) is int and memory >= 0) or (
        observation == MEMORY_UNIFIED_NOT_EXPOSED and memory is None
    )
