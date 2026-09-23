#!/usr/bin/env python3
"""Independently qualify immutable Phase 9 data and the strict-GPU runtime."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import random
import re
import socket
import stat
import tempfile
import time
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb
import httpx
import yaml

from scripts.data.artifact_contract import load_manifest, sha256_file

MARKET_FIELDS = (
    "raw_open", "raw_high", "raw_low", "raw_close", "raw_volume",
    "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume",
)
TOOL_RESULT_KEYS = {
    "schema_version", "tool", "as_of", "outcome", "coverage", "limitations", "evidence",
    "citations", "receipt", "data", "artifacts", "warnings",
}
RECEIPT_KEYS = {
    "engine", "device", "gpu_executed", "fallback_used", "duration_ms",
    "artifact_manifest_sha256", "scenario_id", "market_manifest_sha256",
    "document_manifest_sha256", "market_readiness_sha256", "document_readiness_sha256",
}
COVERAGE_KEYS = {"dimension", "key", "status", "required", "observed_count", "expected_count"}
LIMITATION_KEYS = {"code", "message", "affected"}
COVERAGE_DIMENSIONS = {
    "instrument", "market_window", "documents", "analogue_candidates",
    "graph_paths", "risk_model", "projection_documents",
}
VALUE_KEYS = {"close", "volume", "return_pct", "benchmark_return_pct", "market_adjusted_return_pct", "volume_ratio"}
EXPECTED_READINESS = {
    "market": {"ready": 198, "not_applicable": 51, "needs_scope_resolution": 1},
}
CRITICAL_READINESS = {
    "market": {"advanced-001": "ready", "advanced-020": "needs_scope_resolution", "shock-005": "ready", "multi-007": "ready", "guardrail-009": "ready"},
    "documents": {"advanced-006": "ready", "advanced-010": "ready", "shock-009": "ready", "shock-024": "ready", "shock-041": "ready", "sources-004": "ready", "sources-009": "ready", "sources-011": "ready"},
}
def _normalized_license(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())

SCENARIO_ID = re.compile(r"market-shock-v2-[0-9a-f]{16}")
MARKET_SNAPSHOT_ID = re.compile(r"market-[0-9a-f]{16}")
DOCUMENT_SNAPSHOT_ID = re.compile(r"documents-[0-9a-f]{16}")
MARKET_CAPTURE_ID = re.compile(r"capture-[0-9a-f]{16}")
PUBLICATION_RECEIPT_KEYS = {
    "schema_version", "scenario_id", "manifest_sha256", "market_snapshot_id",
    "document_snapshot_id", "status",
}
RUNTIME_IMAGE_NAMES = {
    "web": "market-shock-web:latest",
    "agent": "market-shock-agent:latest",
    "tools": "market-shock-tools:latest",
    "model": (
        "vllm/vllm-openai:v0.27.1@"
        "sha256:0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967"
    ),
}
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _stable_regular_bytes(
    path: Path, root: Path, code: str, *, limit: int = 65_536,
    expected_mode: int | None = None,
) -> bytes:
    """Read one immutable view without accepting linked mutation paths."""

    path, root = _absolute(path), _absolute(root)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(code) from exc
    current = root
    parent_identities: list[tuple[Path, tuple[int, ...]]] = []
    identity_fields = (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
        "st_mtime_ns", "st_ctime_ns",
    )
    try:
        root_metadata = os.lstat(current)
        if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
            raise RuntimeError(code)
        parent_identities.append((current, tuple(
            getattr(root_metadata, field) for field in identity_fields
        )))
        for part in relative.parts[:-1]:
            current /= part
            metadata = os.lstat(current)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError(code)
            parent_identities.append((current, tuple(
                getattr(metadata, field) for field in identity_fields
            )))
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise RuntimeError(code)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow)
    except OSError as exc:
        raise RuntimeError(code) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > limit
            or (expected_mode is not None and stat.S_IMODE(before.st_mode) != expected_mode)
        ):
            raise RuntimeError(code)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65_536):
            chunks.append(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeError(code) from exc
    finally:
        os.close(descriptor)
    try:
        bound = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(code) from exc
    parents_stable = True
    try:
        for parent, expected in parent_identities:
            metadata = os.lstat(parent)
            observed = tuple(getattr(metadata, field) for field in identity_fields)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or observed != expected
            ):
                parents_stable = False
                break
    except OSError:
        parents_stable = False
    if (
        len(raw) != before.st_size
        or any(getattr(before, field) != getattr(after, field) for field in identity_fields)
        or any(getattr(after, field) != getattr(bound, field) for field in identity_fields)
        or not parents_stable
    ):
        raise RuntimeError(code)
    return raw


def _closed_json_object(raw: bytes, code: str) -> dict[str, Any]:
    def closed(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=closed)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(code) from exc
    if not isinstance(value, dict):
        raise RuntimeError(code)
    return value


def _runtime_images_receipt(runtime_root: Path) -> tuple[dict[str, Any], bytes]:
    """Return one stable, closed view of the exact four-image runtime receipt."""

    root = runtime_root.resolve(strict=True)
    runtime_path = root / "manifests/runtime-images.json"
    raw = _stable_regular_bytes(
        runtime_path,
        root,
        "runtime images receipt is not a stable single-link regular file",
        expected_mode=0o600,
    )
    receipt = _closed_json_object(raw, "runtime images receipt is malformed")
    if (
        set(receipt) != {"schema_version", "images"}
        or type(receipt.get("schema_version")) is not int
        or receipt["schema_version"] != 1
        or not isinstance(receipt.get("images"), dict)
        or set(receipt["images"]) != set(RUNTIME_IMAGE_NAMES)
    ):
        raise RuntimeError("runtime images receipt drift")
    for service, expected_name in RUNTIME_IMAGE_NAMES.items():
        row = receipt["images"][service]
        expected_fields = {"name", "id"} | (
            {"build_input_sha256"} if service != "model" else set()
        )
        if (
            not isinstance(row, dict)
            or set(row) != expected_fields
            or type(row.get("name")) is not str
            or row["name"] != expected_name
            or type(row.get("id")) is not str
            or IMAGE_ID.fullmatch(row["id"]) is None
            or (
                service != "model"
                and (
                    type(row.get("build_input_sha256")) is not str
                    or HEX_DIGEST.fullmatch(row["build_input_sha256"]) is None
                )
            )
        ):
            raise RuntimeError("runtime images receipt drift")
    return receipt, raw


def _candidate_pair(scenario: Path | None, publication_receipt: Path | None) -> None:
    if (scenario is None) != (publication_receipt is None):
        raise ValueError("qualification_candidate_receipt_pair")


def _report_target(path: Path, root: Path, scenario: Path | None) -> Path:
    if scenario is None:
        return path
    runtime_root = root.resolve(strict=True)
    expected = runtime_root / "reports/data/candidates" / scenario.name / "phase-09-qualification.json"
    supplied = _absolute(path if path.is_absolute() else runtime_root / path)
    if supplied != expected:
        raise ValueError("qualification_candidate_report_path")
    return supplied


def _ensure_plain_parent(root: Path, parent: Path) -> None:
    runtime_root, parent = root.resolve(strict=True), _absolute(parent)
    try:
        relative = parent.relative_to(runtime_root)
    except ValueError as exc:
        raise ValueError("qualification_candidate_report_path") from exc
    current = runtime_root
    for part in relative.parts:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o750)
                metadata = os.lstat(current)
            except OSError as exc:
                raise ValueError("qualification_candidate_report_path") from exc
        except OSError as exc:
            raise ValueError("qualification_candidate_report_path") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ValueError("qualification_candidate_report_path")


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    if "text/event-stream" not in response.headers.get("content-type", ""):
        return response.json()
    messages = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            messages.append(json.loads(line[5:].strip()))
    if not messages:
        raise RuntimeError("empty MCP event stream")
    return messages[-1]


class MCPCaller:
    def __init__(self, url: str):
        self.url = url; self.client = httpx.Client(timeout=60.0)
        headers = {"accept": "application/json, text/event-stream"}
        response = self.client.post(url, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "phase09-qualifier", "version": "1"}},
        })
        value = _parse_response(response)
        if value.get("error"):
            raise RuntimeError(f"MCP initialize failed: {value['error']}")
        session = response.headers.get("mcp-session-id")
        self.headers = {**headers, **({"mcp-session-id": session} if session else {})}
        self.client.post(url, headers=self.headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.identifier = 1

    def __call__(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.identifier += 1
        value = _parse_response(self.client.post(self.url, headers=self.headers, json={
            "jsonrpc": "2.0", "id": self.identifier, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }))
        if value.get("error"):
            raise RuntimeError(f"MCP {name} failed: {value['error']}")
        result = value["result"]
        if result.get("isError"):
            raise RuntimeError(f"MCP {name} returned an error: {result.get('content')}")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        content = result.get("content", [])
        text = next((item.get("text") for item in content if item.get("type") == "text"), None)
        if not text:
            raise RuntimeError(f"MCP {name} omitted structured output")
        return json.loads(text)

    def close(self) -> None:
        self.client.close()


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("latency sample is empty")
    ordered = sorted(values); position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _readiness(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    statuses = {status: sum(row["status"] == status for row in rows) for status in sorted({row["status"] for row in rows})}
    return rows, statuses


def _document_readiness_expectations(rows: list[dict[str, Any]]) -> tuple[dict[str, int], bool]:
    """Derive the expected profile from each bound case and its observed gaps.

    ``blocked`` versus ``expected_missing`` is a policy property already bound
    to the case contract; availability changes (including newly added news)
    are independently checked through the presence or absence of gaps.
    """
    expected: list[str] = []
    valid = True
    for row in rows:
        required = row.get("required_source_kinds")
        missing = row.get("missing_requirements")
        status = row.get("status")
        if not isinstance(required, list) or not isinstance(missing, list):
            valid = False
            continue
        codes = [item.get("code") for item in missing if isinstance(item, dict)]
        if len(codes) != len(missing) or any(not isinstance(code, str) or not code for code in codes):
            valid = False
            continue
        if not required:
            derived = "not_applicable"
            valid = valid and not missing
        elif any("unresolved" in code for code in codes):
            derived = "needs_scope_resolution"
        elif not missing:
            derived = "ready"
        elif status in {"blocked", "expected_missing"}:
            derived = status
            valid = valid and all(
                code.startswith("unsupported_") or code == "event_requirement_unavailable"
                for code in codes
            )
        else:
            valid = False
            continue
        expected.append(derived)
        valid = valid and status == derived
    counts = {value: expected.count(value) for value in sorted(set(expected))}
    return counts, valid and len(expected) == len(rows)


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


def _coverage_valid(value: Any) -> bool:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        return False
    for item in value:
        if not isinstance(item, dict) or set(item) != COVERAGE_KEYS:
            return False
        observed, expected = item.get("observed_count"), item.get("expected_count")
        if (
            item.get("dimension") not in COVERAGE_DIMENSIONS
            or not isinstance(item.get("key"), str)
            or not re.fullmatch(r"[A-Za-z0-9_.:/@+\-]{1,160}", item["key"])
            or item.get("status") not in {"available", "partial", "missing"}
            or type(item.get("required")) is not bool
            or type(observed) is not int or not 0 <= observed <= 1_000_000
            or expected is not None and (type(expected) is not int or not 1 <= expected <= 1_000_000)
            or item["status"] == "missing" and observed != 0
            or item["status"] == "available" and observed == 0
        ):
            return False
    return True


def _limitations_valid(value: Any) -> bool:
    if not isinstance(value, list) or len(value) > 20:
        return False
    for item in value:
        affected = item.get("affected") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict) or set(item) != LIMITATION_KEYS
            or not isinstance(item.get("code"), str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item["code"])
            or not isinstance(item.get("message"), str) or not 1 <= len(item["message"].strip()) <= 500
            or not isinstance(affected, list) or len(affected) > 20
            or any(not isinstance(entry, str) or not 1 <= len(entry.strip()) <= 160 for entry in affected)
        ):
            return False
    return True


def _outcome_valid(outcome: Any, coverage: list[dict[str, Any]], limitations: list[dict[str, Any]], artifacts: list[Any]) -> bool:
    unavailable = [item for item in coverage if item["status"] != "available"]
    if outcome == "ok":
        return not unavailable and not limitations
    if outcome == "partial":
        return bool(unavailable and limitations and any(item["status"] != "missing" for item in coverage))
    if outcome == "no_data":
        return bool(limitations and not artifacts and any(
            item["required"] and item["status"] == "missing" for item in coverage
        ))
    return False


def _tool_result_valid(value: Any, tool: str) -> bool:
    if not isinstance(value, dict) or set(value) != TOOL_RESULT_KEYS:
        return False
    collections = ("evidence", "citations", "artifacts", "warnings")
    if (
        value.get("schema_version") != "2.0" or value.get("tool") != tool
        or not isinstance(value.get("receipt"), dict) or set(value["receipt"]) != RECEIPT_KEYS
        or not isinstance(value.get("data"), dict)
        or not isinstance(value["data"].get("summary"), str) or not value["data"]["summary"].strip()
        or any(not isinstance(value.get(key), list) for key in collections)
        or not _coverage_valid(value.get("coverage"))
        or not _limitations_valid(value.get("limitations"))
        or not _outcome_valid(value.get("outcome"), value["coverage"], value["limitations"], value["artifacts"])
    ):
        return False
    evidence_ids = [item.get("evidence_id") for item in value["evidence"] if isinstance(item, dict)]
    citation_ids = [item.get("evidence_id") for item in value["citations"] if isinstance(item, dict)]
    return bool(
        len(evidence_ids) == len(value["evidence"]) == len(set(evidence_ids))
        and len(citation_ids) == len(value["citations"]) == len(set(citation_ids))
        and set(evidence_ids) == set(citation_ids)
    )


def _qualification_outcome_valid(value: dict[str, Any]) -> bool:
    if value.get("outcome") == "ok":
        return True
    unavailable = [item for item in value["coverage"] if item["status"] != "available"]
    optional = {item["key"] for item in unavailable if item["required"] is False}
    limitations = value["limitations"]
    affected = {item for limit in limitations for item in limit["affected"]}
    return bool(
        value.get("outcome") == "partial" and optional
        and len(optional) == len(unavailable)
        and all(limit["code"] == "optional_benchmark_unavailable" for limit in limitations)
        and affected == optional
    )


def _artifact_digests(manifest: dict[str, Any]) -> dict[str, str]:
    return {item["path"]: item["sha256"] for item in manifest["artifacts"]}


def _tool_observation(
    value: dict[str, Any], tool: str, arguments: dict[str, Any], inputs: dict[str, Any],
) -> dict[str, Any]:
    receipt = value.get("receipt")
    if not _tool_result_valid(value, tool):
        raise RuntimeError(f"tool_contract_shape:{tool}")
    if not _qualification_outcome_valid(value):
        raise RuntimeError(f"tool_qualification_outcome:{tool}:{value['outcome']}")
    try:
        returned_at = datetime.fromisoformat(str(value["as_of"]).replace("Z", "+00:00"))
        requested_at = datetime.fromisoformat(str(arguments["as_of"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"tool_contract_time:{tool}") from exc
    binding = {
        "artifact_manifest_sha256": inputs["scenario_manifest_sha256"],
        "scenario_id": inputs["scenario_id"],
        "market_manifest_sha256": inputs["market_manifest_sha256"],
        "document_manifest_sha256": inputs["document_manifest_sha256"],
        "market_readiness_sha256": inputs["market_readiness_sha256"],
        "document_readiness_sha256": inputs["document_readiness_sha256"],
    }
    if (
        returned_at.tzinfo is None or requested_at.tzinfo is None or returned_at != requested_at
        or any(receipt.get(key) != expected for key, expected in binding.items())
        or receipt.get("engine") != "cudf" or receipt.get("gpu_executed") is not True
        or receipt.get("fallback_used") is not False or not isinstance(receipt.get("device"), str)
        or not receipt["device"].strip() or isinstance(receipt.get("duration_ms"), bool)
        or not isinstance(receipt.get("duration_ms"), (int, float))
        or receipt["duration_ms"] < 0
    ):
        raise RuntimeError(f"tool_receipt_binding:{tool}")
    coverage, limitations, data = map(_plain, (value["coverage"], value["limitations"], value["data"]))
    return {
        "outcome": value["outcome"], "tool": tool, "schema_version": value["schema_version"],
        "as_of": value["as_of"], "request": _plain(arguments),
        "coverage": coverage, "coverage_sha256": _digest(coverage),
        "limitations": limitations, "limitations_sha256": _digest(limitations),
        "data": data, "data_sha256": _digest(data), "receipt": _plain(receipt),
    }


def _recorded_tool_valid(value: Any, tool: str, inputs: dict[str, Any]) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "outcome", "tool", "schema_version", "as_of", "request", "coverage", "coverage_sha256",
        "limitations", "limitations_sha256", "data", "data_sha256", "receipt",
    }:
        return False
    receipt = value.get("receipt")
    binding = {
        "artifact_manifest_sha256": inputs.get("scenario_manifest_sha256"),
        "scenario_id": inputs.get("scenario_id"),
        "market_manifest_sha256": inputs.get("market_manifest_sha256"),
        "document_manifest_sha256": inputs.get("document_manifest_sha256"),
        "market_readiness_sha256": inputs.get("market_readiness_sha256"),
        "document_readiness_sha256": inputs.get("document_readiness_sha256"),
    }
    try:
        timestamp = datetime.fromisoformat(str(value["as_of"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return bool(
        _qualification_outcome_valid(value) and value.get("tool") == tool
        and value.get("schema_version") == "2.0" and timestamp.tzinfo is not None
        and isinstance(value.get("request"), dict)
        and value["request"].get("as_of") == value.get("as_of")
        and _coverage_valid(value.get("coverage")) and _limitations_valid(value.get("limitations"))
        and _outcome_valid(value["outcome"], value["coverage"], value["limitations"], [])
        and value.get("coverage_sha256") == _digest(value["coverage"])
        and value.get("limitations_sha256") == _digest(value["limitations"])
        and isinstance(receipt, dict) and set(receipt) == RECEIPT_KEYS
        and all(receipt.get(key) == expected for key, expected in binding.items())
        and receipt.get("engine") == "cudf" and receipt.get("gpu_executed") is True
        and receipt.get("fallback_used") is False and isinstance(receipt.get("device"), str)
        and bool(receipt["device"].strip()) and not isinstance(receipt.get("duration_ms"), bool)
        and isinstance(receipt.get("duration_ms"), (int, float))
        and receipt["duration_ms"] >= 0
        and isinstance(value.get("data"), dict)
        and isinstance(value["data"].get("summary"), str) and bool(value["data"]["summary"].strip())
        and value.get("data_sha256") == _digest(value["data"])
    )


def _tools_health_identity(tools_url: str) -> dict[str, Any]:
    parsed = urlparse(tools_url)
    response = httpx.get(f"{parsed.scheme}://{parsed.netloc}/health", timeout=10.0)
    response.raise_for_status(); value = response.json()
    keys = (
        "scenario_id", "scenario_manifest_sha256", "market_manifest_sha256",
        "document_manifest_sha256", "market_readiness_sha256", "document_readiness_sha256",
    )
    if value.get("ready") is not True or any(not value.get(key) for key in keys):
        raise RuntimeError("tools health identity is incomplete")
    return {"ready": True, **{key: value[key] for key in keys}}


def _scenario_path(runtime_root: Path, candidate: Path | None = None) -> Path:
    """Resolve either the published alias or one unpublished immutable candidate."""

    root = runtime_root.resolve(strict=True)
    if candidate is None:
        return (root / "scenario").resolve(strict=True)
    candidates = root / "candidates"
    try:
        candidates_metadata = os.lstat(candidates)
    except OSError as exc:
        raise ValueError("qualification_candidate_path") from exc
    if not stat.S_ISDIR(candidates_metadata.st_mode) or stat.S_ISLNK(candidates_metadata.st_mode):
        raise ValueError("qualification_candidate_path")
    supplied = _absolute(candidate if candidate.is_absolute() else root / candidate)
    if supplied.parent != candidates or SCENARIO_ID.fullmatch(supplied.name) is None:
        raise ValueError("qualification_candidate_path")
    try:
        supplied_metadata = os.lstat(supplied)
    except OSError as exc:
        raise ValueError("qualification_candidate_path") from exc
    if stat.S_ISLNK(supplied_metadata.st_mode):
        raise ValueError("qualification_candidate_symlink")
    if not stat.S_ISDIR(supplied_metadata.st_mode) or supplied.resolve(strict=True) != supplied:
        raise ValueError("qualification_candidate_path")
    return supplied


def _publication_receipt(
    runtime_root: Path, scenario_root: Path, manifest: dict[str, Any],
    publication_receipt: Path | None,
) -> tuple[dict[str, Any], bytes]:
    candidate_mode = publication_receipt is not None
    if candidate_mode:
        expected = runtime_root / "manifests/scenario-candidates" / f"{scenario_root.name}.json"
        supplied = _absolute(
            publication_receipt if publication_receipt.is_absolute()
            else runtime_root / publication_receipt
        )
        if supplied != expected:
            raise RuntimeError("candidate publication receipt path drift")
        publication_path = supplied
    else:
        publication_path = runtime_root / "manifests/scenario-current.json"
    raw = _stable_regular_bytes(
        publication_path, runtime_root,
        "scenario publication receipt is not a stable single-link regular file",
    )
    publication = _closed_json_object(raw, "scenario publication receipt is malformed")
    scenario_sha = sha256_file(scenario_root / "manifest.json")
    expected_value = {
        "schema_version": 2,
        "scenario_id": manifest.get("scenario_id"),
        "manifest_sha256": scenario_sha,
        "market_snapshot_id": manifest.get("market", {}).get("snapshot_id"),
        "document_snapshot_id": manifest.get("documents", {}).get("snapshot_id"),
        "status": "ready",
    }
    if (
        set(publication) != PUBLICATION_RECEIPT_KEYS
        or type(publication.get("schema_version")) is not int
        or publication != expected_value
        or SCENARIO_ID.fullmatch(str(publication.get("scenario_id", ""))) is None
        or MARKET_SNAPSHOT_ID.fullmatch(str(publication.get("market_snapshot_id", ""))) is None
        or DOCUMENT_SNAPSHOT_ID.fullmatch(str(publication.get("document_snapshot_id", ""))) is None
        or not re.fullmatch(r"[0-9a-f]{64}", str(publication.get("manifest_sha256", "")))
    ):
        raise RuntimeError("scenario publication receipt drift")
    canonical = json.dumps(
        publication, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode() + b"\n"
    if candidate_mode and raw != canonical:
        raise RuntimeError("candidate publication receipt is not canonical")
    return publication, raw


def _expected_inputs(
    runtime_root: Path, scenario_root: Path, manifest: dict[str, Any],
    tools_health: dict[str, Any] | None = None,
    publication_receipt: Path | None = None,
) -> dict[str, Any]:
    market = json.loads((scenario_root / manifest["market"]["path"] / "manifest.json").read_text(encoding="utf-8"))
    documents = json.loads((scenario_root / manifest["documents"]["path"] / "manifest.json").read_text(encoding="utf-8"))
    _, publication_raw = _publication_receipt(
        runtime_root, scenario_root, manifest, publication_receipt,
    )
    runtime, runtime_raw = _runtime_images_receipt(runtime_root)
    tools_image = runtime["images"]["tools"]
    scenario_sha = sha256_file(scenario_root / "manifest.json")
    expected_health = {
        "ready": True, "scenario_id": manifest["scenario_id"],
        "scenario_manifest_sha256": scenario_sha,
        "market_manifest_sha256": manifest["market"]["manifest_sha256"],
        "document_manifest_sha256": manifest["documents"]["manifest_sha256"],
        "market_readiness_sha256": manifest["readiness"]["market"]["sha256"],
        "document_readiness_sha256": manifest["readiness"]["documents"]["sha256"],
    }
    if tools_health != expected_health:
        raise RuntimeError("tools health does not match the published scenario")
    return {
        "scenario_id": manifest["scenario_id"],
        "scenario_manifest_sha256": scenario_sha,
        "publication_receipt_sha256": hashlib.sha256(publication_raw).hexdigest(),
        "runtime_images_receipt_sha256": hashlib.sha256(runtime_raw).hexdigest(),
        "tools_image_id": tools_image["id"],
        "qualifier_source_sha256": sha256_file(Path(__file__).resolve()),
        "tools_health": tools_health,
        "market_snapshot_id": manifest["market"]["snapshot_id"],
        "market_manifest_sha256": manifest["market"]["manifest_sha256"],
        "document_snapshot_id": manifest["documents"]["snapshot_id"],
        "document_manifest_sha256": manifest["documents"]["manifest_sha256"],
        "market_readiness_sha256": manifest["readiness"]["market"]["sha256"],
        "market_readiness_bank_sha256": manifest["readiness"]["market"]["bank_sha256"],
        "market_readiness_contracts_sha256": manifest["readiness"]["market"]["contracts_sha256"],
        "document_readiness_sha256": manifest["readiness"]["documents"]["sha256"],
        "document_readiness_bank_sha256": manifest["readiness"]["documents"]["bank_sha256"],
        "document_readiness_contracts_sha256": manifest["readiness"]["documents"]["contracts_sha256"],
        "session_index_sha256": manifest["coverage"]["session_index"]["sha256"],
        "semantic_scenario_input_sha256": manifest["semantic"]["scenario_input_sha256"],
        "semantic_index_sha256": manifest["semantic"]["index_sha256"],
        "artifact_digests": {
            "scenario": _artifact_digests(manifest),
            "market": _artifact_digests(market),
            "documents": _artifact_digests(documents),
        },
    }


def _query_dicts(connection: duckdb.DuckDBPyConnection, query: str, values: list[Any]) -> list[dict[str, Any]]:
    result = connection.execute(query, values)
    columns = [column[0] for column in result.description]
    return _plain([dict(zip(columns, row, strict=True)) for row in result.fetchall()])


def _capture_file(root: Path, descriptor: dict[str, Any], expected_path: str) -> dict[str, Any]:
    relative = Path(expected_path)
    if descriptor.get("path") != expected_path or relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("raw capture path drift")
    path = root / relative
    observed = {"path": expected_path, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    if observed["bytes"] != descriptor.get("bytes") or observed["sha256"] != descriptor.get("sha256"):
        raise RuntimeError("raw capture digest drift")
    return observed


def _market_capture_controls(runtime_root: Path, market_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    declarations = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "data/market/sources.yaml").read_text(encoding="utf-8")
    )["sources"]
    controls = []
    for source in manifest["sources"]:
        capture_id, source_id = str(source["capture_id"]), str(source["source_id"])
        if MARKET_CAPTURE_ID.fullmatch(capture_id) is None:
            raise RuntimeError("raw capture identity drift")
        root = runtime_root / "raw/captures-v2" / capture_id
        raw_manifest = root / "capture.json"
        lineage = market_root / f"lineage/{source_id}-{capture_id}.json"
        if (
            root.name != capture_id or not raw_manifest.is_file() or raw_manifest.read_bytes() != lineage.read_bytes()
            or sha256_file(raw_manifest) != source["capture_sha256"]
        ):
            raise RuntimeError("raw capture lineage drift")
        capture = json.loads(raw_manifest.read_text(encoding="utf-8"))
        if capture.get("capture_id") != capture_id or capture.get("source_id") != source_id:
            raise RuntimeError("raw capture manifest drift")
        observed: dict[str, Any] = {
            "source_id": source_id, "capture_id": capture_id, "adapter": capture.get("adapter"),
            "capture_manifest_sha256": sha256_file(raw_manifest),
        }
        if capture.get("adapter") == "kaggle_cc0":
            profile = declarations.get(source_id)
            if not profile:
                raise RuntimeError("raw capture declaration missing")
            request_mode, metadata_mode = capture.get("request_mode"), capture.get("metadata_mode")
            if (
                capture.get("adapter_version") != 4
                or request_mode not in {
                    "version_pinned_api", "operator_supplied_pinned_archive", "verified_legacy_cache",
                }
                or metadata_mode not in {
                    "current_dataset_api", "operator_supplied_current_dataset_metadata",
                    "verified_legacy_cache",
                }
                or ("verified_legacy_cache" in {request_mode, metadata_mode}
                    and request_mode != metadata_mode)
                or capture.get("sanitized_urls") != {
                    "metadata": profile["metadata_url"], "download": profile["download_url"],
                    "attribution": profile["attribution_url"],
                }
            ):
                raise RuntimeError("raw capture request contract drift")
            request = capture.get("request")
            if (
                not isinstance(request, dict)
                or set(request) != {
                    "source_id", "adapter", "dataset_version", "start", "end_exclusive",
                    "symbols", "fields",
                }
                or request.get("source_id") != source_id
                or request.get("adapter") != "kaggle_cc0"
                or request.get("dataset_version") != profile["dataset_version"]
                or request.get("symbols") != profile["symbols"]
                or request.get("fields") != profile["fields"]
            ):
                raise RuntimeError("raw capture request contract drift")
            try:
                request_start = datetime.strptime(request["start"], "%Y-%m-%d").date()
                request_end = datetime.strptime(request["end_exclusive"], "%Y-%m-%d").date()
            except (TypeError, ValueError) as exc:
                raise RuntimeError("raw capture request contract drift") from exc
            if request_start >= request_end:
                raise RuntimeError("raw capture request contract drift")
            identity = {
                "adapter_version": capture.get("adapter_version"),
                "request": request,
                "archive_sha256": capture.get("archive", {}).get("sha256"),
                "member_sha256": capture.get("member", {}).get("sha256"),
                "metadata_stable": capture.get("metadata_stable"),
                "metadata_mode": metadata_mode,
                "request_mode": request_mode,
                "observed": {key: capture.get(key) for key in (
                    "observed_start", "observed_end_inclusive", "observed_rows",
                    "observed_rows_by_symbol", "mapping",
                )},
                "captured_at": capture.get("captured_at"),
            }
            expected_capture_id = "capture-" + hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode()).hexdigest()[:16]
            if expected_capture_id != capture_id:
                raise RuntimeError("raw capture content identity drift")
            archive = _capture_file(root, capture["archive"], "archive.zip")
            member = _capture_file(root, capture["member"], profile["member"])
            metadata = _capture_file(root, capture["metadata"], "metadata.json")
            try:
                raw_metadata = json.loads((root / "metadata.json").read_bytes())
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("raw capture metadata invalid") from exc
            if not isinstance(raw_metadata, dict):
                raise RuntimeError("raw capture metadata invalid")
            nested_version = raw_metadata.get("currentVersion")
            raw_version = raw_metadata.get("currentVersionNumber")
            if raw_version is None and isinstance(nested_version, dict):
                raw_version = nested_version.get("versionNumber")
            if isinstance(raw_version, bool) or not isinstance(raw_version, int) or raw_version < 1:
                raise RuntimeError("raw capture metadata version invalid")
            version = raw_version
            dataset_ref = raw_metadata.get("ref", raw_metadata.get("datasetRef"))
            dataset_id = raw_metadata.get("id", raw_metadata.get("datasetId"))
            if (
                dataset_ref != profile["dataset_ref"]
                or isinstance(dataset_id, bool) or not isinstance(dataset_id, int)
                or dataset_id != profile["dataset_id"]
            ):
                raise RuntimeError("raw capture metadata identity drift")
            license_value = raw_metadata.get("licenseName", raw_metadata.get("license"))
            if isinstance(license_value, dict):
                license_value = license_value.get("name")
            if _normalized_license(license_value) != _normalized_license(profile["license_id"]):
                raise RuntimeError("raw capture metadata rights drift")
            if version < profile["dataset_version"]:
                raise RuntimeError("raw capture metadata version before archive")
            version_updated_at = raw_metadata.get("lastUpdated")
            if version_updated_at is None and isinstance(nested_version, dict):
                version_updated_at = nested_version.get("lastUpdated")
            stable = capture.get("metadata_stable", {})
            expected_stable = {
                "schema_version": 2,
                "metadata_scope": "current_dataset",
                "requested_archive_version": profile["dataset_version"],
                "observed_current_version": version,
                "license": str(license_value),
                "dataset_ref": dataset_ref,
                "dataset_id": dataset_id,
                "observed_current_version_updated_at": version_updated_at,
                "declared_archive_sha256": profile["archive_sha256"],
                "declared_archive_bytes": profile["archive_bytes"],
                "declared_member": profile["member"],
                "declared_member_sha256": profile["member_sha256"],
                "declared_member_bytes": profile["member_bytes"],
            }
            if (
                (archive["bytes"], archive["sha256"]) != (profile["archive_bytes"], profile["archive_sha256"])
                or (member["bytes"], member["sha256"]) != (profile["member_bytes"], profile["member_sha256"])
                or stable != expected_stable
                or hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != capture.get("metadata_stable_sha256")
            ):
                raise RuntimeError("raw capture pinned declaration drift")
            with zipfile.ZipFile(root / "archive.zip") as package:
                packaged = package.read(profile["member"])
            if hashlib.sha256(packaged).hexdigest() != member["sha256"] or len(packaged) != member["bytes"]:
                raise RuntimeError("raw capture archive/member mismatch")
            observed.update(archive=archive, member=member, metadata=metadata,
                            metadata_stable_sha256=capture.get("metadata_stable_sha256"))
        elif capture.get("adapter") == "local_file":
            observed["files"] = {
                role: _capture_file(root, descriptor, descriptor["path"])
                for role, descriptor in sorted(capture.get("files", {}).items())
            }
        else:
            raise RuntimeError("raw capture adapter drift")
        controls.append(observed)
    return controls


def _market_observation(
    connection: duckdb.DuckDBPyConnection, pattern: str, market_root: Path,
    market_manifest: dict[str, Any], session_values: list[dict[str, str]], runtime_root: Path,
) -> dict[str, Any]:
    symbols = (
        market_manifest["universe"]["targets"] + market_manifest["universe"]["peers"]
        + market_manifest["universe"]["required_benchmarks"]
    )
    stats: dict[str, Any] = {}
    field_sql = ",".join(f"count({field}) AS {field}_rows" for field in MARKET_FIELDS)
    all_sessions = [row["session_date"] for row in session_values]
    for symbol in symbols:
        row = _query_dicts(connection, f"""
            SELECT count(*) AS rows, count(DISTINCT session_date) AS distinct_sessions,
                   min(session_date) AS start, max(session_date) AS end_inclusive,
                   count(DISTINCT source_id) AS source_count, {field_sql}
            FROM read_parquet(?) WHERE instrument_id=?
        """, [pattern, symbol])[0]
        observed = [item[0] for item in connection.execute(
            "SELECT session_date FROM read_parquet(?) WHERE instrument_id=? ORDER BY session_date",
            [pattern, symbol],
        ).fetchall()]
        start, end = row["start"], row["end_inclusive"]
        expected = [value for value in all_sessions if start is not None and start <= value <= end]
        row["missing_sessions"] = sorted(set(expected) - set(observed))
        row["duplicate_sessions"] = len(observed) - len(set(observed))
        stats[symbol] = row
    lineage = []
    for item in market_manifest["artifacts"]:
        if item["path"].startswith("lineage/") and item["path"].endswith(".json"):
            value = json.loads((market_root / item["path"]).read_text(encoding="utf-8"))
            lineage.append({
                "source_id": value["source_id"], "capture_id": value["capture_id"],
                "archive": value.get("archive"), "member": value.get("member"),
                "metadata_sha256": value.get("metadata", {}).get("sha256"),
                "dataset_version": value.get("request", {}).get("dataset_version"),
                "observed_rows": value.get("observed_rows"),
                "observed_rows_by_symbol": value.get("observed_rows_by_symbol"),
                "observed_start": value.get("observed_start"),
                "observed_end_inclusive": value.get("observed_end_inclusive"),
                "vintage_status": value.get("vintage_status"),
            })
    actions = _query_dicts(
        connection, "SELECT * FROM read_parquet(?) ORDER BY instrument_id,effective_at",
        [str(market_root / "actions.parquet")],
    )
    total = connection.execute("SELECT count(*) FROM read_parquet(?)", [pattern]).fetchone()[0]
    return {
        "direct_rows": total, "symbols": stats, "actions": actions,
        "observed_coverage": market_manifest["observed_coverage"],
        "field_coverage": market_manifest["field_coverage"],
        "quality": market_manifest["quality"], "universe": market_manifest["universe"],
        "benchmark_policy": market_manifest["benchmark_policy"],
        "sources": market_manifest["sources"], "capture_lineage": sorted(lineage, key=lambda row: row["source_id"]),
        "raw_capture_controls": _market_capture_controls(runtime_root, market_root, market_manifest),
    }


def _document_observation(
    connection: duckdb.DuckDBPyConnection, documents_root: Path,
    document_manifest: dict[str, Any],
) -> dict[str, Any]:
    documents_path = str(documents_root / "documents.parquet")
    coverage_path = str(documents_root / "coverage.parquet")
    groups = _query_dicts(connection, """
        SELECT issuer_id,source_kind,count(*) AS records,min(published_at) AS published_start,
               max(published_at) AS published_end,
               sum(CASE WHEN content_scope<>'metadata_only' THEN 1 ELSE 0 END) AS substantive_records,
               sum(CASE WHEN capture_artifact IS NOT NULL THEN 1 ELSE 0 END) AS raw_captures,
               sum(CASE WHEN content_scope='metadata_only' THEN 1 ELSE 0 END) AS metadata_only
        FROM read_parquet(?) GROUP BY issuer_id,source_kind ORDER BY issuer_id,source_kind
    """, [documents_path])
    coverage_statuses = _query_dicts(connection, """
        SELECT status,count(*) AS records FROM read_parquet(?) GROUP BY status ORDER BY status
    """, [coverage_path])
    capture_controls = []
    for source in document_manifest["sources"]:
        root = documents_root / source["path"]
        capture_path = root / "capture.json"
        if sha256_file(capture_path) != source["manifest_sha256"]:
            raise RuntimeError("document raw capture manifest drift")
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
        descriptors = list(capture.get("raw_artifacts", []))
        descriptors += [capture[key] for key in ("file", "records_artifact", "gaps_artifact") if capture.get(key)]
        observed = []
        for descriptor in descriptors:
            observed.append(_capture_file(root, descriptor, descriptor["path"]))
        capture_controls.append({
            "capture_id": source["capture_id"], "adapter": source["adapter"],
            "manifest_sha256": source["manifest_sha256"], "artifacts": observed,
        })
    return {
        "direct_rows": connection.execute("SELECT count(*) FROM read_parquet(?)", [documents_path]).fetchone()[0],
        "groups": groups,
        "coverage_rows": connection.execute("SELECT count(*) FROM read_parquet(?)", [coverage_path]).fetchone()[0],
        "coverage_statuses": coverage_statuses,
        "coverage": document_manifest["observed_coverage"], "gaps": document_manifest["gaps"],
        "requirements": document_manifest["requirements"], "sources": document_manifest["sources"],
        "raw_capture_controls": capture_controls,
    }


def _news_disclosure_valid(documents: dict[str, Any]) -> bool:
    groups = documents.get("groups", [])
    news_records = sum(
        int(item.get("records", 0)) for item in groups
        if isinstance(item, dict) and item.get("source_kind") == "licensed_news_metadata"
    )
    if news_records > 0:
        return True
    return any(
        isinstance(item, dict) and item.get("code") == "unsupported_missing_news"
        for item in documents.get("gaps", [])
    )


def _readiness_observation(scenario_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("market", "documents"):
        binding = manifest["readiness"][name]
        rows, statuses = _readiness(scenario_root / binding["path"])
        status_by_case = {row["case_id"]: row["status"] for row in rows}
        critical = {case_id: status_by_case.get(case_id) for case_id in CRITICAL_READINESS[name]}
        expected = EXPECTED_READINESS.get(name)
        baselined = True
        if name == "documents":
            expected, baselined = _document_readiness_expectations(rows)
        assert expected is not None
        result[name] = {
            "rows": len(rows), "statuses": statuses,
            "expected_statuses": expected, "critical_cases": critical,
            "accepted": statuses == expected and critical == CRITICAL_READINESS[name] and baselined,
            "case_ids_sha256": hashlib.sha256(json.dumps(
                sorted(row["case_id"] for row in rows), separators=(",", ":"),
            ).encode()).hexdigest(),
            "unsupported": [
                {"case_id": row["case_id"], "status": row["status"],
                 "reasons": row["missing_items"] if name == "market" else row["missing_requirements"]}
                for row in rows if row["status"] in {"expected_missing", "needs_scope_resolution", "blocked"}
            ],
        }
    return result


def _control(connection: duckdb.DuckDBPyConnection, pattern: str, ticker: str, benchmark: str, session: str) -> dict[str, Any]:
    target = connection.execute(
        "SELECT session_date,adjusted_close,volume FROM read_parquet(?) WHERE instrument_id=? AND session_date<=? ORDER BY session_date DESC LIMIT 21",
        [pattern, ticker, session],
    ).fetchall()[::-1]
    comparison = connection.execute(
        "SELECT session_date,adjusted_close FROM read_parquet(?) WHERE instrument_id=? AND session_date<=? ORDER BY session_date DESC LIMIT 2",
        [pattern, benchmark, session],
    ).fetchall()[::-1]
    if len(target) != 21 or len(comparison) != 2 or target[-1][0] != session or comparison[-1][0] != session:
        raise RuntimeError(f"control coverage missing: {ticker}/{benchmark}:{session}")
    target_return = (float(target[-1][1]) / float(target[-2][1]) - 1) * 100
    benchmark_return = (float(comparison[-1][1]) / float(comparison[-2][1]) - 1) * 100
    volumes = sorted(float(row[2]) for row in target[:-1] if row[2] is not None)
    median = (volumes[9] + volumes[10]) / 2
    return {
        "close": float(target[-1][1]), "volume": int(target[-1][2]), "return_pct": target_return,
        "benchmark_return_pct": benchmark_return, "market_adjusted_return_pct": target_return - benchmark_return,
        "volume_ratio": float(target[-1][2]) / median,
    }


def _close(actual: Any, expected: float, tolerance: float) -> bool:
    return actual is not None and math.isclose(float(actual), expected, rel_tol=tolerance, abs_tol=tolerance)


def _parity_checks(values: dict[str, Any], tools: list[dict[str, Any]], tolerance: float) -> dict[str, bool]:
    expected, actual = values.get("expected", {}), values.get("actual", {})
    if set(expected) != VALUE_KEYS or set(actual) != VALUE_KEYS:
        raise ValueError("qualification_value_shape")
    return {
        "close": _close(actual["close"], expected["close"], tolerance),
        "volume": actual["volume"] == expected["volume"],
        "return": _close(actual["return_pct"], expected["return_pct"], tolerance),
        "benchmark_return": _close(actual["benchmark_return_pct"], expected["benchmark_return_pct"], tolerance),
        "relative_return": _close(actual["market_adjusted_return_pct"], expected["market_adjusted_return_pct"], tolerance),
        "volume_ratio": _close(actual["volume_ratio"], expected["volume_ratio"], tolerance),
        "gpu": all(item.get("receipt", {}).get("gpu_executed") is True for item in tools),
    }


def _environment_observation(tools_url: str) -> dict[str, Any]:
    parsed = urlparse(tools_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise RuntimeError("qualifier tools URL must be an internal HTTP service")
    try:
        address = socket.gethostbyname(parsed.hostname)
    except OSError as exc:
        raise RuntimeError("qualifier tools host did not resolve") from exc
    try:
        routes = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()[1:]
        default_route = any(len(line.split()) > 1 and line.split()[1] == "00000000" for line in routes)
    except OSError:
        default_route = None
    return {
        "observation_source": "container_runtime", "containerized": Path("/.dockerenv").exists(),
        "tools_scheme": parsed.scheme, "tools_host": parsed.hostname, "tools_address": address,
        "default_route_present": default_route,
        "host_paths_recorded": False, "credentials_recorded": False,
    }


def _isolated_environment(value: Any) -> bool:
    try:
        internal = ipaddress.ip_address(value["tools_address"]).is_private
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        value.get("containerized") is True and value.get("tools_scheme") == "http"
        and value.get("tools_host") == "tools" and internal
        and value.get("default_route_present") is False
    )


def _seeded_requests(
    targets: list[str], benchmark_policy: dict[str, Any], sessions: list[dict[str, str]], samples: int,
) -> list[dict[str, str]]:
    eligible = [row for row in sessions[20:] if row["session_date"] <= "2025-04-17"]
    if set(targets) != {"NVDA", "AMD", "JPM", "GS", "SCHW"} or len(eligible) < 2:
        raise RuntimeError("qualification canonical target/session coverage missing")
    generator = random.Random(20250913); requests = []
    for index in range(samples):
        ticker = targets[index % len(targets)]
        sector = "semiconductor" if ticker in {"NVDA", "AMD"} else "financial"
        session = eligible[generator.randrange(len(eligible))]
        requests.append({
            "ticker": ticker, "benchmark": benchmark_policy[sector]["required"][0],
            "session": session["session_date"], "as_of": session["close_at"],
        })
    return requests


def qualify(
    root: Path, tools_url: str, gate: str, samples: int,
    *, caller: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    scenario: Path | None = None, publication_receipt: Path | None = None,
) -> dict[str, Any]:
    _candidate_pair(scenario, publication_receipt)
    started = datetime.now(timezone.utc)
    environment = _environment_observation(tools_url)
    if not _isolated_environment(environment):
        raise RuntimeError("qualification requires the isolated Compose tools network")
    runtime_root = root.resolve()
    scenario_root = _scenario_path(runtime_root, scenario)
    manifest = load_manifest(scenario_root / "manifest.json")
    input_binding = _expected_inputs(
        runtime_root, scenario_root, manifest, _tools_health_identity(tools_url), publication_receipt,
    )
    expected_tier = "cc0_reconstruction" if gate == "reconstruction" else "entitled_local"
    if manifest["data_tier"] != expected_tier:
        raise RuntimeError(f"{gate} qualification requires {expected_tier}, got {manifest['data_tier']}")
    market_root = scenario_root / manifest["market"]["path"]
    documents_root = scenario_root / manifest["documents"]["path"]
    pattern = str(market_root / "bars/interval=1d/year=*/part-*.parquet")
    connection = duckdb.connect(":memory:")
    market_manifest = json.loads((market_root / "manifest.json").read_text(encoding="utf-8"))
    document_manifest = json.loads((documents_root / "manifest.json").read_text(encoding="utf-8"))
    target_symbols = manifest["coverage"]["targets"]
    readiness = _readiness_observation(scenario_root, manifest)
    if readiness["market"]["rows"] != 250 or readiness["documents"]["rows"] != 250:
        raise RuntimeError("qualification requires both complete 250-case readiness sets")
    sessions = manifest["coverage"]["session_index"]
    session_values = json.loads((scenario_root / sessions["path"]).read_text(encoding="utf-8"))["sessions"]
    market_observation = _market_observation(
        connection, pattern, market_root, market_manifest, session_values, runtime_root,
    )
    document_observation = _document_observation(connection, documents_root, document_manifest)
    requests = _seeded_requests(target_symbols, market_manifest["benchmark_policy"], session_values, samples)
    owned_caller = MCPCaller(tools_url) if caller is None else None
    invoke = owned_caller or caller
    assert invoke is not None
    failures, parity, latencies = [], [], []
    if not all(item["accepted"] for item in readiness.values()):
        failures.append({"code": "readiness_contract"})
    try:
        for ordinal, request in enumerate(requests):
            ticker, benchmark, session, as_of = (
                request["ticker"], request["benchmark"], request["session"], request["as_of"],
            )
            begin = time.perf_counter()
            try:
                expected = _control(connection, pattern, ticker, benchmark, session)
                context_args, shock_args = {"ticker": ticker, "as_of": as_of}, {"ticker": ticker, "as_of": as_of}
                context = invoke("get_price_context", context_args)
                shock = invoke("detect_market_shock", shock_args)
                tool_observations = [
                    _tool_observation(context, "get_price_context", context_args, input_binding),
                    _tool_observation(shock, "detect_market_shock", shock_args, input_binding),
                ]
                performance = {row["ticker"]: row for row in context["data"]["performance"]}
                actual = {
                    "close": performance.get(ticker, {}).get("close"),
                    "volume": performance.get(ticker, {}).get("volume"),
                    "return_pct": shock["data"].get("return_pct"),
                    "benchmark_return_pct": shock["data"].get("benchmark_return_pct"),
                    "market_adjusted_return_pct": shock["data"].get("market_adjusted_return_pct"),
                    "volume_ratio": shock["data"].get("volume_ratio"),
                }
                values = {"expected": expected, "actual": actual}
                checks = _parity_checks(values, tool_observations, 1e-6)
                if not all(checks.values()):
                    failures.append({"sample": ordinal, "ticker": ticker, "session": session, "code": "parity_mismatch", "checks": checks})
                parity.append({
                    "sample": ordinal, "ticker": ticker, "benchmark": benchmark,
                    "session": session, "as_of": as_of, "values": values,
                    "checks": checks, "tools": tool_observations,
                })
                latencies.append((time.perf_counter() - begin) * 1000)
            except Exception as exc:
                failures.append({
                    "sample": ordinal, "ticker": ticker, "session": session,
                    "code": "control_or_tool_error", "error_type": type(exc).__name__,
                })
    finally:
        if owned_caller:
            owned_caller.close()
    completed = datetime.now(timezone.utc)
    report = {
        "schema_version": 1, "phase": "09-data-recovery", "status": "pass" if not failures else "fail",
        "run_id": hashlib.sha256(f"{manifest['scenario_id']}:{started.isoformat()}:{samples}".encode()).hexdigest()[:20],
        "started_at": started.isoformat().replace("+00:00", "Z"), "completed_at": completed.isoformat().replace("+00:00", "Z"),
        "environment": environment,
        "gate": gate, "data_tier": manifest["data_tier"], "vintage_status": manifest["vintage_status"],
        "inputs": input_binding,
        "market": market_observation, "documents": document_observation,
        "readiness": readiness,
        "semantic": manifest["semantic"],
        "parity": {"seed": 20250913, "requested": samples, "completed": len(parity), "tolerance": 1e-6, "samples": parity},
        "latency_ms": {"samples": latencies, "p50": _quantile(latencies, .5) if latencies else None, "p95": _quantile(latencies, .95) if latencies else None, "max": max(latencies) if latencies else None},
        "failures": failures,
        "release_eligibility": (
            {"status": "eligible", "reasons": []} if gate == "release" else
            {"status": "not_eligible", "reasons": [
                "current-capture reconstruction", "no entitled archived-at-cutoff market input supplied",
            ]}
        ),
    }
    return report


def atomic_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, default=str); stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _atomic_new_report(path: Path, report: dict[str, Any]) -> None:
    """Publish a candidate report exactly once without a replacement window."""

    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, default=str)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError("qualification_candidate_report_exists") from exc
        os.unlink(temporary)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _recomputed_sections(runtime_root: Path, scenario: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    market_root = scenario / manifest["market"]["path"]
    documents_root = scenario / manifest["documents"]["path"]
    market_manifest = json.loads((market_root / "manifest.json").read_text(encoding="utf-8"))
    document_manifest = json.loads((documents_root / "manifest.json").read_text(encoding="utf-8"))
    session_path = scenario / manifest["coverage"]["session_index"]["path"]
    sessions = json.loads(session_path.read_text(encoding="utf-8"))["sessions"]
    connection = duckdb.connect(":memory:")
    try:
        return {
            "market": _market_observation(
                connection, str(market_root / "bars/interval=1d/year=*/part-*.parquet"),
                market_root, market_manifest, sessions, runtime_root,
            ),
            "documents": _document_observation(connection, documents_root, document_manifest),
            "readiness": _readiness_observation(scenario, manifest),
        }
    finally:
        connection.close()


def _expected_parity_controls(
    scenario: Path, manifest: dict[str, Any], samples: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    market_root = scenario / manifest["market"]["path"]
    pattern = str(market_root / "bars/interval=1d/year=*/part-*.parquet")
    connection = duckdb.connect(":memory:")
    try:
        return {
            int(item["sample"]): _control(connection, pattern, item["ticker"], item["benchmark"], item["session"])
            for item in samples
        }
    finally:
        connection.close()


def _expected_request_records(scenario: Path, manifest: dict[str, Any], samples: int) -> list[dict[str, str]]:
    market_root = scenario / manifest["market"]["path"]
    market = json.loads((market_root / "manifest.json").read_text(encoding="utf-8"))
    session_path = scenario / manifest["coverage"]["session_index"]["path"]
    sessions = json.loads(session_path.read_text(encoding="utf-8"))["sessions"]
    return _seeded_requests(manifest["coverage"]["targets"], market["benchmark_policy"], sessions, samples)


def _recorded_actual(item: dict[str, Any]) -> dict[str, Any]:
    price, shock = item["tools"]
    performance = {row["ticker"]: row for row in price["data"].get("performance", [])}
    target = performance.get(item["ticker"], {})
    return {
        "close": target.get("close"), "volume": target.get("volume"),
        "return_pct": shock["data"].get("return_pct"),
        "benchmark_return_pct": shock["data"].get("benchmark_return_pct"),
        "market_adjusted_return_pct": shock["data"].get("market_adjusted_return_pct"),
        "volume_ratio": shock["data"].get("volume_ratio"),
    }


def _validate_report_value(
    report: dict[str, Any], root: Path, *, scenario: Path | None = None,
    publication_receipt: Path | None = None,
) -> dict[str, Any]:
    _candidate_pair(scenario, publication_receipt)
    required = {
        "schema_version", "phase", "status", "run_id", "started_at", "completed_at", "environment",
        "gate", "data_tier", "vintage_status", "inputs", "market", "documents", "readiness",
        "semantic", "parity", "latency_ms", "failures", "release_eligibility",
    }
    if set(report) != required or report.get("schema_version") != 1:
        raise ValueError("qualification_report_shape")
    runtime_root = root.resolve()
    scenario_root = _scenario_path(runtime_root, scenario)
    manifest = load_manifest(scenario_root / "manifest.json")
    recorded_health = report.get("inputs", {}).get("tools_health")
    if report["inputs"] != _expected_inputs(
        runtime_root, scenario_root, manifest, recorded_health, publication_receipt,
    ):
        raise ValueError("qualification_input_drift")
    try:
        started = datetime.fromisoformat(report["started_at"].replace("Z", "+00:00"))
        completed = datetime.fromisoformat(report["completed_at"].replace("Z", "+00:00"))
        expected_run_id = hashlib.sha256(
            f"{manifest['scenario_id']}:{started.isoformat()}:{report['parity']['requested']}".encode()
        ).hexdigest()[:20]
    except (KeyError, TypeError, ValueError):
        raise ValueError("qualification_run_identity") from None
    if (
        started.tzinfo is None or completed.tzinfo is None or completed < started
        or report.get("run_id") != expected_run_id
    ):
        raise ValueError("qualification_run_identity")
    expected_sections = _recomputed_sections(runtime_root, scenario_root, manifest)
    for name in ("market", "documents", "readiness"):
        if report[name] != expected_sections[name]:
            raise ValueError(f"qualification_{name}_drift")
    if (
        report["data_tier"] != manifest["data_tier"]
        or report["vintage_status"] != manifest["vintage_status"]
        or report["semantic"] != manifest["semantic"]
        or report["gate"] != ("reconstruction" if manifest["data_tier"] == "cc0_reconstruction" else "release")
    ):
        raise ValueError("qualification_contract_drift")
    failures, parity, latency = report["failures"], report["parity"], report["latency_ms"]
    readiness_failed = not all(item.get("accepted") is True for item in report["readiness"].values())
    readiness_failures = [item for item in failures if item.get("code") == "readiness_contract"]
    if bool(readiness_failures) != readiness_failed or len(readiness_failures) > 1:
        raise ValueError("qualification_readiness_acceptance")
    if report["status"] != ("pass" if not failures else "fail"):
        raise ValueError("qualification_status_drift")
    samples = parity.get("samples", [])
    if (
        set(parity) != {"seed", "requested", "completed", "tolerance", "samples"}
        or parity.get("seed") != 20250913 or parity.get("requested", 0) < 100
        or parity.get("completed") != len(samples) or parity.get("tolerance") != 1e-6
    ):
        raise ValueError("qualification_denominator")
    check_keys = {"close", "volume", "return", "benchmark_return", "relative_return", "volume_ratio", "gpu"}
    parity_ordinals = {item.get("sample") for item in samples}
    sampled_failures = [item for item in failures if "sample" in item]
    failure_ordinals = {item.get("sample") for item in sampled_failures}
    if (
        any(set(item.get("checks", {})) != check_keys for item in samples)
        or parity_ordinals | failure_ordinals != set(range(parity["requested"]))
        or len(parity_ordinals) != len(samples) or len(failure_ordinals) != len(sampled_failures)
        or any(item.get("code") not in {"readiness_contract", "parity_mismatch", "control_or_tool_error"} for item in failures)
    ):
        raise ValueError("qualification_sample_accounting")
    expected_controls = _expected_parity_controls(scenario_root, manifest, samples)
    expected_requests = _expected_request_records(scenario_root, manifest, parity["requested"])
    for item in samples:
        expected_request = expected_requests[item["sample"]]
        observations = item.get("tools")
        if (
            set(item) != {"sample", "ticker", "benchmark", "session", "as_of", "values", "checks", "tools"}
            or not isinstance(observations, list) or len(observations) != 2
            or not all(_recorded_tool_valid(value, tool, report["inputs"]) for value, tool in zip(
                observations, ("get_price_context", "detect_market_shock"), strict=True,
            ))
        ):
            raise ValueError("qualification_tool_receipt")
        observed_request = {key: item.get(key) for key in ("ticker", "benchmark", "session", "as_of")}
        expected_args = {"ticker": item["ticker"], "as_of": item["as_of"]}
        if observed_request != expected_request or any(value.get("request") != expected_args for value in observations):
            raise ValueError("qualification_request_set_drift")
        if item.get("values", {}).get("expected") != expected_controls.get(item["sample"]):
            raise ValueError("qualification_control_drift")
        try:
            recorded_actual = _recorded_actual(item)
        except (KeyError, TypeError):
            raise ValueError("qualification_actual_binding") from None
        if item.get("values", {}).get("actual") != recorded_actual:
            raise ValueError("qualification_actual_binding")
        try:
            recomputed_checks = _parity_checks(item["values"], observations, parity["tolerance"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("qualification_value_shape") from None
        if item["checks"] != recomputed_checks:
            raise ValueError("qualification_check_drift")
    mismatch_ordinals = {item["sample"] for item in samples if not all(item["checks"].values())}
    recorded_mismatches = {item.get("sample") for item in failures if item.get("code") == "parity_mismatch"}
    if mismatch_ordinals != recorded_mismatches:
        raise ValueError("qualification_omitted_failure")
    latency_samples = latency["samples"]
    if len(latency_samples) != parity["completed"]:
        raise ValueError("qualification_latency_denominator")
    expected = {
        "p50": _quantile(latency_samples, .5) if latency_samples else None,
        "p95": _quantile(latency_samples, .95) if latency_samples else None,
        "max": max(latency_samples) if latency_samples else None,
    }
    if any(latency[key] != value for key, value in expected.items()):
        raise ValueError("qualification_percentile_drift")
    release = report["release_eligibility"]
    if (report["gate"] == "reconstruction" and release.get("status") != "not_eligible") or (
        report["gate"] == "release" and release != {"status": "eligible", "reasons": []}
    ):
        raise ValueError("qualification_false_release")
    if report["gate"] == "reconstruction":
        raw_fields = ("raw_open_rows", "raw_high_rows", "raw_low_rows", "raw_close_rows", "raw_volume_rows")
        targets = report["market"].get("universe", {}).get("targets", [])
        optional = report["market"].get("universe", {}).get("optional_instruments", [])
        market_gaps = report["market"].get("quality", {}).get("gaps", [])
        if (
            report["market"].get("quality", {}).get("raw_fields_available") is not False
            or any(report["market"].get("symbols", {}).get(symbol, {}).get(field) != 0 for symbol in targets for field in raw_fields)
            or "KRE" not in optional
            or not any(item.get("code") == "optional_instrument_unavailable" and item.get("instrument_id") == "KRE" for item in market_gaps)
            or not _news_disclosure_valid(report["documents"])
        ):
            raise ValueError("qualification_gap_disclosure")
    environment = report["environment"]
    environment_keys = {
        "observation_source", "containerized", "tools_scheme", "tools_host", "tools_address",
        "default_route_present", "host_paths_recorded", "credentials_recorded",
    }
    if (
        not isinstance(environment, dict) or set(environment) != environment_keys
        or environment.get("observation_source") != "container_runtime"
        or environment.get("tools_scheme") != "http" or not environment.get("tools_host")
        or not environment.get("tools_address") or not isinstance(environment.get("containerized"), bool)
        or environment.get("default_route_present") not in {True, False, None}
        or environment.get("host_paths_recorded") is not False
        or environment.get("credentials_recorded") is not False
    ):
        raise ValueError("qualification_environment_observation")
    if not _isolated_environment(environment):
        raise ValueError("qualification_network_isolation")
    return report


def validate_report_value(
    report: dict[str, Any], root: Path, *, scenario: Path | None = None,
    publication_receipt: Path | None = None,
) -> dict[str, Any]:
    """Authoritatively validate an already captured report value."""

    return _validate_report_value(
        report, root, scenario=scenario, publication_receipt=publication_receipt,
    )


def validate_report(
    path: Path, root: Path, *, scenario: Path | None = None,
    publication_receipt: Path | None = None,
) -> dict[str, Any]:
    _candidate_pair(scenario, publication_receipt)
    path = _report_target(path, root, scenario)
    try:
        raw = _stable_regular_bytes(
            path, root.resolve(strict=True), "qualification_report_unreadable", limit=64 * 1024**2,
        )
        report = _closed_json_object(raw, "qualification_report_unreadable")
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        raise ValueError("qualification_report_unreadable") from exc
    return validate_report_value(
        report, root, scenario=scenario, publication_receipt=publication_receipt,
    )


def publish_report(
    path: Path, report: dict[str, Any], root: Path, *, scenario: Path | None = None,
    publication_receipt: Path | None = None,
) -> None:
    _candidate_pair(scenario, publication_receipt)
    path = _report_target(path, root, scenario)
    _validate_report_value(
        report, root, scenario=scenario, publication_receipt=publication_receipt,
    )
    if scenario is not None and (path.exists() or path.is_symlink()):
        raise ValueError("qualification_candidate_report_exists")
    if scenario is not None:
        _ensure_plain_parent(root, path.parent)
        created = False
        try:
            _atomic_new_report(path, report)
            created = True
            validate_report(
                path, root, scenario=scenario, publication_receipt=publication_receipt,
            )
        except Exception:
            if created and path.exists() and path.is_file() and not path.is_symlink():
                path.unlink()
            raise
        return
    prior = path.read_bytes() if path.is_file() else None
    try:
        atomic_report(path, report)
        validate_report(
            path, root, scenario=scenario, publication_receipt=publication_receipt,
        )
    except Exception:
        if prior is None:
            if path.exists(): path.unlink()
        else:
            descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.rollback.", dir=path.parent)
            os.fchmod(descriptor, 0o644)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(prior); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/srv/market-shock"))
    parser.add_argument("--tools-url", default="http://tools:8000/mcp")
    parser.add_argument("--gate", choices=("reconstruction", "release"), default="reconstruction")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--publication-receipt", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 100: parser.error("--samples must be at least 100")
    if (args.scenario is None) != (args.publication_receipt is None):
        parser.error("--scenario and --publication-receipt must be supplied together")
    if args.output is None:
        args.output = (
            args.root / "reports/data/candidates" / args.scenario.name / "phase-09-qualification.json"
            if args.scenario is not None else
            args.root / "reports/data/phase-09-qualification.json"
        )
    elif args.scenario is not None:
        try:
            args.output = _report_target(args.output, args.root, args.scenario)
        except (OSError, ValueError):
            parser.error("candidate --output must be its candidate-specific qualification path")
    report = qualify(
        args.root, args.tools_url, args.gate, args.samples,
        scenario=args.scenario, publication_receipt=args.publication_receipt,
    )
    publish_report(
        args.output, report, args.root,
        scenario=args.scenario, publication_receipt=args.publication_receipt,
    )
    print(json.dumps({"status": report["status"], "run_id": report["run_id"], "failures": len(report["failures"])}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
