#!/usr/bin/env python3
"""Fail-closed contracts for immutable market captures and snapshot-v2 trees."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse


RAW_FIELDS = ("raw_open", "raw_high", "raw_low", "raw_close", "raw_volume")
ADJUSTED_FIELDS = (
    "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume"
)
BAR_COLUMNS = frozenset({
    "instrument_id", "session_date", "bar_start", "bar_end", *RAW_FIELDS,
    *ADJUSTED_FIELDS, "price_basis", "source_id", "source_row_id", "captured_at",
    "vintage_status",
})
GATES = frozenset({"fixture", "reconstruction", "release"})
VINTAGES = frozenset({"archived_at_cutoff", "reconstructed_later", "unknown"})
KAGGLE_CAPTURE_ADAPTER_VERSION = 4
KAGGLE_CAPTURE_OBSERVED_KEYS = (
    "observed_start", "observed_end_inclusive", "observed_rows",
    "observed_rows_by_symbol", "mapping",
)


@dataclass(frozen=True)
class ContractIssue:
    code: str
    detail: str


class MarketContractError(ValueError):
    """A stable set of validation failures, suitable for operator diagnostics."""

    def __init__(self, issues: Iterable[ContractIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{item.code}: {item.detail}" for item in self.issues))


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def content_id(prefix: str, value: Any, length: int = 16) -> str:
    return f"{prefix}-{sha256_bytes(canonical_json(value))[:length]}"


def kaggle_capture_identity(capture: dict[str, Any]) -> dict[str, Any]:
    """Return the complete semantic material bound into a Kaggle capture ID."""
    return {
        "adapter_version": capture.get("adapter_version"),
        "request": capture.get("request"),
        "archive_sha256": capture.get("archive", {}).get("sha256"),
        "member_sha256": capture.get("member", {}).get("sha256"),
        "metadata_stable": capture.get("metadata_stable"),
        "metadata_mode": capture.get("metadata_mode"),
        "request_mode": capture.get("request_mode"),
        "observed": {key: capture.get(key) for key in KAGGLE_CAPTURE_OBSERVED_KEYS},
        "captured_at": capture.get("captured_at"),
    }


def kaggle_capture_id(capture: dict[str, Any]) -> str:
    return content_id("capture", kaggle_capture_identity(capture))


def parse_utc(value: Any, field: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise MarketContractError([ContractIssue("invalid_utc_time", field)])
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise MarketContractError([ContractIssue("invalid_utc_time", field)]) from exc
    if parsed.tzinfo != timezone.utc:
        raise MarketContractError([ContractIssue("invalid_utc_time", field)])
    return parsed


def parse_date(value: Any, field: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MarketContractError([ContractIssue("invalid_date", field)]) from exc


def safe_relative(root: Path, relative: str) -> Path:
    candidate_rel = Path(relative)
    if candidate_rel.is_absolute() or not candidate_rel.parts or ".." in candidate_rel.parts:
        raise MarketContractError([ContractIssue("artifact_path", relative)])
    root = root.resolve()
    candidate = (root / candidate_rel).resolve()
    if root not in candidate.parents:
        raise MarketContractError([ContractIssue("artifact_escape", relative)])
    return candidate


def artifact_record(root: Path, path: Path, records: int) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    suffix = path.suffix.lower()
    media = "application/vnd.apache.parquet" if suffix == ".parquet" else "application/json"
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "records": records,
        "media_type": media,
    }


def validate_source_profile(source_id: str, profile: dict[str, Any]) -> None:
    issues: list[ContractIssue] = []
    required = {
        "adapter", "dataset_version", "dataset_ref", "dataset_id",
        "download_url", "metadata_url", "attribution_url",
        "provider", "license_id", "redistribution", "archive_bytes", "archive_sha256",
        "member", "member_bytes", "member_sha256", "header_rows", "price_basis",
        "fields", "symbols", "vintage_status",
    }
    if set(profile) != required:
        issues.append(ContractIssue("source_shape", source_id))
    version = profile.get("dataset_version")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        issues.append(ContractIssue("dataset_version", source_id))
    dataset_ref = profile.get("dataset_ref")
    ref_parts = str(dataset_ref).split("/")
    if (
        len(ref_parts) != 2
        or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", part) for part in ref_parts)
    ):
        issues.append(ContractIssue("dataset_identity", source_id))
    dataset_id = profile.get("dataset_id")
    if isinstance(dataset_id, bool) or not isinstance(dataset_id, int) or dataset_id <= 0:
        issues.append(ContractIssue("dataset_identity", source_id))

    expected_paths = {
        "download_url": f"/api/v1/datasets/download/{dataset_ref}",
        "metadata_url": f"/api/v1/datasets/view/{dataset_ref}",
        "attribution_url": f"/datasets/{dataset_ref}",
    }
    for name, expected_path in expected_paths.items():
        parsed = urlparse(str(profile.get(name, "")))
        expected_query = [("datasetVersionNumber", str(version))] if name == "download_url" else []
        if (
            parsed.scheme != "https" or parsed.netloc != "www.kaggle.com"
            or parsed.path != expected_path or parsed.params or parsed.fragment
            or parse_qsl(parsed.query, keep_blank_values=True) != expected_query
        ):
            code = "unpinned_source" if name == "download_url" else "source_url"
            issues.append(ContractIssue(code, f"{source_id}.{name}"))
    if profile.get("license_id") != "CC0: Public Domain":
        issues.append(ContractIssue("unproved_rights", source_id))
    if profile.get("vintage_status") != "reconstructed_later":
        issues.append(ContractIssue("false_vintage", source_id))
    for name in ("archive_sha256", "member_sha256"):
        digest = str(profile.get(name, ""))
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            issues.append(ContractIssue("invalid_digest", f"{source_id}.{name}"))
    if issues:
        raise MarketContractError(issues)


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketContractError([ContractIssue(code, path.name)]) from exc
    if not isinstance(value, dict):
        raise MarketContractError([ContractIssue(code, path.name)])
    return value


def _parquet_rows(paths: list[Path]) -> tuple[list[dict[str, Any]], set[str]]:
    try:
        import pyarrow.dataset as ds
    except ImportError as exc:
        raise MarketContractError([ContractIssue("pyarrow_unavailable", "snapshot")]) from exc
    try:
        table = ds.dataset([str(path) for path in paths], format="parquet").to_table()
    except Exception as exc:
        raise MarketContractError([ContractIssue("parquet_unreadable", type(exc).__name__)]) from exc
    return table.to_pylist(), set(table.column_names)


def _verify_artifacts(root: Path, manifest: dict[str, Any]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return [ContractIssue("artifact_manifest", "artifacts")]
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {
            "path", "sha256", "bytes", "records", "media_type"
        }:
            issues.append(ContractIssue("artifact_shape", repr(item)))
            continue
        relative = item["path"]
        if relative in seen:
            issues.append(ContractIssue("duplicate_artifact", relative))
        seen.add(relative)
        try:
            path = safe_relative(root, relative)
        except MarketContractError as exc:
            issues.extend(exc.issues)
            continue
        if not path.is_file():
            issues.append(ContractIssue("artifact_missing", relative))
            continue
        if path.stat().st_size != item["bytes"]:
            issues.append(ContractIssue("artifact_size", relative))
        if sha256_file(path) != item["sha256"]:
            issues.append(ContractIssue("artifact_digest", relative))
        if path.suffix == ".parquet":
            try:
                import pyarrow.parquet as pq
                if pq.ParquetFile(path).metadata.num_rows != item["records"]:
                    issues.append(ContractIssue("artifact_records", relative))
            except Exception as exc:
                issues.append(ContractIssue("parquet_unreadable", f"{relative}:{type(exc).__name__}"))
    return issues


def _validate_times_and_values(rows: list[dict[str, Any]]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    keys: set[tuple[str, str]] = set()
    row_ids: set[str] = set()
    for row in rows:
        symbol = str(row.get("instrument_id", ""))
        session = str(row.get("session_date", ""))
        key = (symbol, session)
        if key in keys:
            issues.append(ContractIssue("duplicate_canonical_key", f"{symbol}:{session}"))
        keys.add(key)
        source_row_id = str(row.get("source_row_id", ""))
        if not source_row_id or source_row_id in row_ids:
            issues.append(ContractIssue("duplicate_source_row", source_row_id))
        row_ids.add(source_row_id)
        try:
            start = parse_utc(row.get("bar_start"), "bar_start")
            end = parse_utc(row.get("bar_end"), "bar_end")
            capture = parse_utc(row.get("captured_at"), "captured_at")
            if not start < end <= capture:
                issues.append(ContractIssue("market_time_order", f"{symbol}:{session}"))
            if start.date().isoformat() != session:
                issues.append(ContractIssue("session_time_mismatch", f"{symbol}:{session}"))
        except MarketContractError as exc:
            issues.extend(exc.issues)
        if row.get("vintage_status") not in VINTAGES:
            issues.append(ContractIssue("invalid_vintage", f"{symbol}:{session}"))
        present = [row.get(name) for name in ADJUSTED_FIELDS[:4]]
        numeric = [row.get(name) for name in (*RAW_FIELDS, *ADJUSTED_FIELDS) if row.get(name) is not None]
        try:
            if any(not math.isfinite(float(value)) for value in numeric):
                issues.append(ContractIssue("non_finite_number", f"{symbol}:{session}"))
            if all(value is not None for value in present):
                open_, high, low, close = map(float, present)
                if min(open_, high, low, close) < 0 or high < max(open_, close) or low > min(open_, close):
                    issues.append(ContractIssue("ohlc_invariant", f"{symbol}:{session}"))
            volume = row.get("volume")
            if volume is not None and (float(volume) < 0 or float(volume) != int(float(volume))):
                issues.append(ContractIssue("invalid_volume", f"{symbol}:{session}"))
        except (TypeError, ValueError, OverflowError):
            issues.append(ContractIssue("invalid_numeric", f"{symbol}:{session}"))
    return issues


def _observed_field_coverage(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    symbols = sorted({str(row["instrument_id"]) for row in rows})
    result: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        selected = [row for row in rows if row["instrument_id"] == symbol]
        present = [
            field for field in (*RAW_FIELDS, *ADJUSTED_FIELDS)
            if selected and all(row.get(field) is not None for row in selected)
        ]
        partial = [
            field for field in (*RAW_FIELDS, *ADJUSTED_FIELDS)
            if any(row.get(field) is not None for row in selected) and field not in present
        ]
        result[symbol] = {
            "rows": len(selected),
            "start": min(row["session_date"] for row in selected),
            "end_inclusive": max(row["session_date"] for row in selected),
            "present_fields": present,
            "partial_fields": partial,
            "price_basis": sorted({row["price_basis"] for row in selected}),
        }
    return result


def _small_parquet(root: Path, name: str, issues: list[ContractIssue]) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
        return pq.read_table(root / name).to_pylist()
    except Exception as exc:
        issues.append(ContractIssue("parquet_unreadable", f"{name}:{type(exc).__name__}"))
        return []


def _validate_market_tables(
    root: Path, manifest: dict[str, Any], rows: list[dict[str, Any]],
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    sessions = _small_parquet(root, "sessions.parquet", issues)
    coverage = _small_parquet(root, "coverage.parquet", issues)
    actions = _small_parquet(root, "actions.parquet", issues)
    instruments = _small_parquet(root, "instruments.parquet", issues)
    required_paths = {"sessions.parquet", "coverage.parquet", "actions.parquet", "instruments.parquet"}
    declared = {item.get("path") for item in manifest.get("artifacts", []) if isinstance(item, dict)}
    bars = {path.relative_to(root).as_posix() for path in root.glob("bars/interval=1d/year=*/part-*.parquet")}
    omitted = (required_paths | bars) - declared
    if omitted:
        issues.append(ContractIssue("artifact_omission", ",".join(sorted(omitted))))
    session_by_date: dict[str, dict[str, Any]] = {}
    for session in sessions:
        key = str(session.get("session_date", ""))
        if key in session_by_date:
            issues.append(ContractIssue("duplicate_session", key))
        session_by_date[key] = session
        try:
            opened = parse_utc(session.get("open_at"), "session.open_at")
            closed = parse_utc(session.get("close_at"), "session.close_at")
            if opened >= closed or opened.date().isoformat() != key:
                issues.append(ContractIssue("session_time_order", key))
        except MarketContractError as exc:
            issues.extend(exc.issues)
    observed_dates = {str(row.get("session_date")) for row in rows}
    for row in rows:
        session = session_by_date.get(str(row.get("session_date")))
        if not session or row.get("bar_start") != session.get("open_at") or row.get("bar_end") != session.get("close_at"):
            issues.append(ContractIssue("bar_session_binding", str(row.get("source_row_id"))))
    for session_date, session in session_by_date.items():
        if bool(session.get("observed_any")) != (session_date in observed_dates):
            issues.append(ContractIssue("session_observation_drift", session_date))
    universe = manifest.get("universe", {})
    roles = {
        **{symbol: "target" for symbol in universe.get("targets", [])},
        **{symbol: "peer" for symbol in universe.get("peers", [])},
        **{symbol: "required_benchmark" for symbol in universe.get("required_benchmarks", [])},
        **{symbol: "optional" for symbol in universe.get("optional_instruments", [])},
    }
    coverage_by_symbol = {row.get("instrument_id"): row for row in coverage}
    if len(coverage_by_symbol) != len(coverage) or set(coverage_by_symbol) != set(roles):
        issues.append(ContractIssue("coverage_instrument_drift", "coverage/universe"))
    fields = _observed_field_coverage(rows) if rows else {}
    all_sessions = set(session_by_date)
    for symbol, role in roles.items():
        actual = coverage_by_symbol.get(symbol)
        if not actual:
            continue
        selected = [row for row in rows if row.get("instrument_id") == symbol]
        seen = {row["session_date"] for row in selected}
        missing = all_sessions - seen
        status = (
            "present" if selected and not missing else "partial" if selected
            else "missing_optional" if role == "optional" else "missing_required"
        )
        expected = {
            "role": role, "status": status, "rows": len(selected),
            "start": min(seen) if seen else None, "end_inclusive": max(seen) if seen else None,
            "missing_sessions": len(missing),
            "present_fields": json.dumps(fields.get(symbol, {}).get("present_fields", []), separators=(",", ":")),
        }
        for key, value in expected.items():
            if actual.get(key) != value:
                issues.append(ContractIssue("coverage_row_drift", f"{symbol}:{key}"))
        if role != "optional" and status != "present":
            issues.append(ContractIssue("required_session_coverage", symbol))
    for action in actions:
        if action.get("instrument_id") == "NONE":
            continue
        try:
            published = parse_utc(action.get("published_at"), "action.published_at")
            effective = parse_utc(action.get("effective_at"), "action.effective_at")
            captured = parse_utc(action.get("captured_at"), "action.captured_at")
            if max(published, effective) > captured:
                issues.append(ContractIssue("action_time_order", str(action.get("instrument_id"))))
        except MarketContractError as exc:
            issues.extend(exc.issues)
        digest = str(action.get("source_sha256", ""))
        try:
            factor = float(action.get("factor") or 0)
        except (TypeError, ValueError, OverflowError):
            factor = float("nan")
        if (
            not math.isfinite(factor) or factor <= 0
            or not str(action.get("attribution_url", "")).startswith("https://")
            or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
        ):
            issues.append(ContractIssue("action_contract", str(action.get("instrument_id"))))
    if {row.get("instrument_id") for row in instruments} != set(roles):
        issues.append(ContractIssue("instrument_table_drift", "universe"))
    try:
        quality = _load_json(root / "quality.json", "quality_unreadable")
        if quality != manifest.get("quality") or quality.get("status") != "pass":
            issues.append(ContractIssue("quality_drift", str(quality.get("status"))))
        raw_observed = any(any(row.get(field) is not None for field in RAW_FIELDS) for row in rows)
        if quality.get("raw_fields_available") is not raw_observed:
            issues.append(ContractIssue("raw_availability_drift", str(raw_observed)))
    except MarketContractError as exc:
        issues.extend(exc.issues)
    for source in manifest.get("sources", []):
        name = f"lineage/{source.get('source_id')}-{source.get('capture_id')}.json"
        try:
            path = safe_relative(root, name)
            if not path.is_file() or sha256_file(path) != source.get("capture_sha256"):
                issues.append(ContractIssue("source_lineage_drift", str(source.get("source_id"))))
            else:
                capture = _load_json(path, "source_lineage_unreadable")
                if capture.get("capture_id") != source.get("capture_id") or capture.get("source_id") != source.get("source_id"):
                    issues.append(ContractIssue("source_lineage_identity", str(source.get("source_id"))))
                elif capture.get("adapter") == "kaggle_cc0":
                    stable = capture.get("metadata_stable")
                    expected_capture = kaggle_capture_id(capture)
                    if (
                        capture.get("adapter_version") != KAGGLE_CAPTURE_ADAPTER_VERSION
                        or not isinstance(stable, dict)
                        or sha256_bytes(canonical_json(stable)) != capture.get("metadata_stable_sha256")
                        or expected_capture != capture.get("capture_id")
                    ):
                        issues.append(ContractIssue("source_lineage_content_identity", str(source.get("source_id"))))
                elif capture.get("adapter") == "local_file":
                    expected_identity = {
                        "adapter_version": 2, "mapping": capture.get("mapping"),
                        "captured_at": capture.get("captured_at"),
                    }
                    if capture.get("capture_identity") != expected_identity or content_id("capture", expected_identity) != capture.get("capture_id"):
                        issues.append(ContractIssue("source_lineage_content_identity", str(source.get("source_id"))))
        except MarketContractError as exc:
            issues.extend(exc.issues)
    return issues


def validate_snapshot(root: Path, gate: str = "reconstruction") -> dict[str, Any]:
    """Validate every byte and recompute coverage; no requested constant is trusted."""
    root = root.resolve()
    manifest = _load_json(root / "manifest.json", "manifest_unreadable")
    issues: list[ContractIssue] = []
    if manifest.get("schema_version") != 2 or manifest.get("snapshot_kind") != "market":
        issues.append(ContractIssue("manifest_version", str(manifest.get("schema_version"))))
    if manifest.get("normalizer_version") != 2:
        issues.append(ContractIssue("normalizer_version", str(manifest.get("normalizer_version"))))
    expected_snapshot_id = content_id("market", {
        "normalizer_version": manifest.get("normalizer_version"),
        "captures": sorted(
            (source.get("capture_id"), source.get("capture_sha256"))
            for source in manifest.get("sources", [])
        ),
        "window": manifest.get("requested_window"),
        "universe_sha256": manifest.get("universe_sha256"),
        "actions_sha256": manifest.get("actions_sha256"),
        "gate": gate, "captured_at": manifest.get("captured_at"),
    })
    if manifest.get("snapshot_id") != expected_snapshot_id:
        issues.append(ContractIssue("snapshot_identity", str(manifest.get("snapshot_id"))))
    if gate not in GATES or manifest.get("gate") != gate:
        issues.append(ContractIssue("gate_mismatch", f"requested={gate}"))
    tier = manifest.get("data_tier")
    vintage = manifest.get("vintage_status")
    if gate == "reconstruction" and (tier != "cc0_reconstruction" or vintage != "reconstructed_later"):
        issues.append(ContractIssue("reconstruction_contract", f"{tier}:{vintage}"))
    if gate == "release" and (tier != "entitled_local" or vintage != "archived_at_cutoff"):
        issues.append(ContractIssue("release_contract", f"{tier}:{vintage}"))
    try:
        captured = parse_utc(manifest.get("captured_at"), "captured_at")
        parse_utc(manifest.get("created_at"), "created_at")
        requested = manifest.get("requested_window", {})
        requested_start = parse_date(requested.get("start"), "requested_window.start")
        requested_end = parse_date(requested.get("end_exclusive"), "requested_window.end_exclusive")
        if requested_start >= requested_end:
            issues.append(ContractIssue("invalid_window", "requested_window"))
    except MarketContractError as exc:
        issues.extend(exc.issues)
        captured = None
        requested_start = requested_end = None
    for source in manifest.get("sources", []):
        source_id = str(source.get("source_id", "unknown"))
        if not str(source.get("attribution_url", "")).startswith("https://"):
            issues.append(ContractIssue("attribution_url", source_id))
        if source.get("vintage_status") not in VINTAGES:
            issues.append(ContractIssue("invalid_vintage", source_id))
        try:
            source_capture = parse_utc(source.get("captured_at"), f"{source_id}.captured_at")
            published = source.get("published_at")
            revised = source.get("source_revised_at")
            if published and parse_utc(published, f"{source_id}.published_at") > source_capture:
                issues.append(ContractIssue("source_time_order", source_id))
            if revised and parse_utc(revised, f"{source_id}.source_revised_at") > source_capture:
                issues.append(ContractIssue("source_time_order", source_id))
            if captured and source_capture > captured:
                issues.append(ContractIssue("capture_time_order", source_id))
            proof = source.get("archive_proof")
            if source.get("vintage_status") == "archived_at_cutoff":
                if not isinstance(proof, dict):
                    issues.append(ContractIssue("archive_proof_missing", source_id))
                else:
                    proved = parse_utc(proof.get("proved_at"), f"{source_id}.archive_proof.proved_at")
                    proof_path = safe_relative(root, str(proof.get("path", "")))
                    if (
                        proved > source_capture
                        or not str(proof.get("canonical_url", "")).startswith("https://")
                        or parse_date(proof.get("coverage_end_exclusive")) < requested_end
                        or not proof_path.is_file()
                        or proof_path.stat().st_size != proof.get("bytes")
                        or sha256_file(proof_path) != proof.get("sha256")
                    ):
                        issues.append(ContractIssue("archive_proof_invalid", source_id))
            elif proof is not None:
                issues.append(ContractIssue("archive_proof_vintage", source_id))
        except MarketContractError as exc:
            issues.extend(exc.issues)
    if gate == "release" and any(
        source.get("vintage_status") != "archived_at_cutoff"
        or source.get("redistribution") not in {"allowed", "local_only"}
        or not source.get("archive_proof")
        for source in manifest.get("sources", [])
    ):
        issues.append(ContractIssue("release_source_unproved", "nested source declarations"))
    issues.extend(_verify_artifacts(root, manifest))
    bars = sorted(root.glob("bars/interval=1d/year=*/part-*.parquet"))
    if not bars:
        issues.append(ContractIssue("bars_missing", "bars/interval=1d"))
        rows: list[dict[str, Any]] = []
        columns: set[str] = set()
    else:
        try:
            rows, columns = _parquet_rows(bars)
        except MarketContractError as exc:
            issues.extend(exc.issues)
            rows, columns = [], set()
    if columns and columns != BAR_COLUMNS:
        issues.append(ContractIssue("bar_schema", ",".join(sorted(columns ^ BAR_COLUMNS))))
    if rows:
        issues.extend(_validate_times_and_values(rows))
        dates = [parse_date(row["session_date"]) for row in rows]
        if requested_start and min(dates) < requested_start:
            issues.append(ContractIssue("window_underflow", min(dates).isoformat()))
        if requested_end and max(dates) >= requested_end:
            issues.append(ContractIssue("window_overflow", max(dates).isoformat()))
        observed = manifest.get("observed_coverage", {})
        expected_observed = {
            "start": min(dates).isoformat(),
            "end_inclusive": max(dates).isoformat(),
            "rows": len(rows),
        }
        if observed != expected_observed:
            issues.append(ContractIssue("observed_coverage_drift", repr(expected_observed)))
        field_coverage = _observed_field_coverage(rows)
        if manifest.get("field_coverage") != field_coverage:
            issues.append(ContractIssue("field_coverage_drift", "manifest != parquet"))
        issues.extend(_validate_market_tables(root, manifest, rows))
    if issues:
        raise MarketContractError(issues)
    return manifest


def cutoff_rows(rows: Iterable[dict[str, Any]], cutoff: datetime) -> list[dict[str, Any]]:
    """Market cutoff is observation/session time; capture time remains disclosure only."""
    if cutoff.tzinfo is None:
        raise MarketContractError([ContractIssue("naive_cutoff", "cutoff")])
    return [row for row in rows if parse_utc(row["bar_end"], "bar_end") <= cutoff]
