#!/usr/bin/env python3
"""Exact, scenario-bound contract for curated market-shock events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

from scripts.data.document_contract import (
    DocumentContractError,
    read_regular_bytes,
    validate_document_row,
    validate_publication_proof,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = ROOT / "data/schemas/shock-event-catalog.schema.json"
COMPONENTS = ("market", "documents", "licensed_news", "derived_features")
FORBIDDEN_RUNTIME_KEYS = frozenset({
    "answer", "answer_key", "expected_answer", "expected_output", "grader",
    "grading", "oracle", "reference_answer",
})
FEATURE_ROW_KEYS = frozenset({
    "instrument_id", "session_date", "feature_at", "return_1d",
    "absolute_return_pct", "volume_ratio", "source_id", "source_row_id",
    "input_source_ids", "input_source_row_ids", "future_outcome_excluded",
})


@dataclass(frozen=True)
class EventIssue:
    code: str
    detail: str


class EventContractError(ValueError):
    def __init__(self, issues: Iterable[EventIssue]):
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
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def _schema(schema_path: Path) -> tuple[dict[str, Any], bytes]:
    import jsonschema

    try:
        body = schema_path.read_bytes()
        value = json.loads(body)
        jsonschema.Draft202012Validator.check_schema(value)
    except (OSError, UnicodeError, json.JSONDecodeError, jsonschema.SchemaError) as exc:
        raise EventContractError([EventIssue("event_schema", type(exc).__name__)]) from exc
    return value, body


def load_event_catalog(
    path: Path, schema_path: Path = DEFAULT_SCHEMA,
) -> tuple[dict[str, Any], str, str]:
    """Parse YAML once, validate its exact JSON schema, then enforce semantics."""
    import jsonschema
    import yaml

    try:
        body = path.read_bytes()
        value = yaml.safe_load(body)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise EventContractError([EventIssue("event_catalog", type(exc).__name__)]) from exc
    schema, schema_body = _schema(schema_path)
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker(),
    )
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.path) or "root"
        raise EventContractError([EventIssue("event_schema", f"{location}: {first.message}")])
    assert isinstance(value, dict)
    issues: list[EventIssue] = []
    forbidden = sorted(set(_walk_keys(value)) & FORBIDDEN_RUNTIME_KEYS)
    if forbidden:
        issues.append(EventIssue("runtime_answer_key", ",".join(forbidden)))

    supported = value["supported_tickers"]
    if supported != sorted(supported):
        issues.append(EventIssue("ticker_order", "supported_tickers must be sorted"))
    categories = value["categories"]
    category_ids = [item["category_id"] for item in categories]
    category_orders = [item["sort_order"] for item in categories]
    if len(set(category_ids)) != len(category_ids):
        issues.append(EventIssue("duplicate_category", "category_id"))
    if category_orders != list(range(1, len(categories) + 1)):
        issues.append(EventIssue("category_order", "sort_order must be contiguous"))

    event_ids: set[str] = set()
    question_ids: set[str] = set()
    question_texts: set[str] = set()
    event_orders: list[int] = []
    for event in value["events"]:
        event_id = event["event_id"]
        if event_id in event_ids:
            issues.append(EventIssue("duplicate_event", event_id))
        event_ids.add(event_id)
        event_orders.append(event["sort_order"])
        if event["category_id"] not in category_ids:
            issues.append(EventIssue("unknown_category", event_id))
        tickers = event["analysis_tickers"]
        context = event["context_instruments"]
        if tickers[0] != event["primary_ticker"]:
            issues.append(EventIssue("primary_ticker_order", event_id))
        unknown = sorted((set(tickers) | set(context)) - set(supported))
        if unknown:
            issues.append(EventIssue("unsupported_ticker", f"{event_id}:{','.join(unknown)}"))
        overlap = sorted(set(tickers) & set(context))
        if overlap:
            issues.append(EventIssue("context_overlap", f"{event_id}:{','.join(overlap)}"))
        try:
            start = date.fromisoformat(event["start_session"])
            session = date.fromisoformat(event["event_session"])
            end = date.fromisoformat(event["end_session"])
            cutoff = datetime.fromisoformat(event["default_cutoff"].replace("Z", "+00:00"))
            if not start <= session <= end or cutoff.date() < session:
                issues.append(EventIssue("event_chronology", event_id))
        except (TypeError, ValueError):
            issues.append(EventIssue("event_chronology", event_id))
        for question in event["questions"]:
            question_id = question["question_id"]
            normalized = " ".join(question["text"].casefold().split())
            if question_id in question_ids:
                issues.append(EventIssue("duplicate_question", question_id))
            if normalized in question_texts:
                issues.append(EventIssue("duplicate_question_text", question_id))
            question_ids.add(question_id)
            question_texts.add(normalized)
        gap_keys: set[tuple[str, str]] = set()
        for gap in event["known_gaps"]:
            key = (gap["layer"], gap["code"])
            if key in gap_keys:
                issues.append(EventIssue("duplicate_gap", f"{event_id}:{key[1]}"))
            gap_keys.add(key)
            if gap["code"] == "unsupported_missing_news" and gap["layer"] != "licensed_news":
                issues.append(EventIssue("gap_layer", f"{event_id}:{gap['code']}"))
            if gap["code"] != "unsupported_missing_news" and gap["layer"] != "documents":
                issues.append(EventIssue("gap_layer", f"{event_id}:{gap['code']}"))
    if event_orders != list(range(1, len(value["events"]) + 1)):
        issues.append(EventIssue("event_order", "sort_order must be contiguous"))
    if issues:
        raise EventContractError(issues)
    return value, sha256_bytes(body), sha256_bytes(schema_body)


def _safe_child(root: Path, relative: str) -> Path:
    item = Path(relative)
    if item.is_absolute() or not item.parts or ".." in item.parts:
        raise EventContractError([EventIssue("binding_path", relative)])
    candidate = (root / item).resolve()
    if root not in candidate.parents:
        raise EventContractError([EventIssue("binding_escape", relative)])
    return candidate


def _json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventContractError([EventIssue(code, type(exc).__name__)]) from exc
    if not isinstance(value, dict):
        raise EventContractError([EventIssue(code, "root")])
    return value


def _bound_bytes(
    root: Path, artifacts: Any, relative: str, *, digest: str | None, code: str,
) -> bytes:
    """Read one manifest-listed regular artifact and bind its exact bytes."""
    matches = [
        item for item in artifacts or []
        if isinstance(item, dict) and item.get("path") == relative
    ] if isinstance(artifacts, list) else []
    if len(matches) != 1:
        raise EventContractError([EventIssue(code, relative)])
    record = matches[0]
    if (
        not isinstance(record.get("sha256"), str)
        or type(record.get("bytes")) is not int
        or digest is not None and record["sha256"] != digest
    ):
        raise EventContractError([EventIssue(code, relative)])
    try:
        path = _safe_child(root, relative)
        body = read_regular_bytes(path, root=root)
    except (OSError, EventContractError) as exc:
        raise EventContractError([EventIssue(code, relative)]) from exc
    if len(body) != record["bytes"] or sha256_bytes(body) != record["sha256"]:
        raise EventContractError([EventIssue(code, relative)])
    return body


def _capture_bytes(
    root: Path, manifest: dict[str, Any], row: dict[str, Any], artifact_key: str,
    digest_key: str, *, required: bool,
) -> bytes | None:
    artifact = row.get(artifact_key)
    digest = row.get(digest_key)
    if artifact is None and not required:
        return None
    capture_id = row.get("capture_id")
    if (
        not isinstance(capture_id, str) or not capture_id
        or not isinstance(artifact, str) or not artifact
        or not isinstance(digest, str) or len(digest) != 64
    ):
        raise EventContractError([EventIssue("document_proof_binding", str(row.get("evidence_id")))])
    relative = f"captures/{capture_id}/{artifact}"
    return _bound_bytes(
        root, manifest.get("artifacts"), relative,
        digest=digest, code="document_proof_binding",
    )


def _document_rows(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Read evidence only after its row and publication proof bytes validate."""
    try:
        body = _bound_bytes(
            root, manifest.get("artifacts"), "documents.parquet",
            digest=None, code="document_news_binding",
        )
        import pyarrow as pa
        import pyarrow.parquet as pq
        parquet = pq.ParquetFile(pa.BufferReader(body))
        record = next(
            item for item in manifest["artifacts"]
            if item.get("path") == "documents.parquet"
        )
        if parquet.metadata.num_rows != record.get("records"):
            raise EventContractError([EventIssue("document_news_binding", "documents.parquet")])
        values = parquet.read().to_pylist()
    except EventContractError:
        raise
    except Exception as exc:
        raise EventContractError([EventIssue("document_news_rows", type(exc).__name__)]) from exc

    issuers = manifest.get("issuers")
    if not isinstance(issuers, dict):
        raise EventContractError([EventIssue("document_news_rows", "issuers")])
    rows: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise EventContractError([EventIssue("document_news_rows", "row")])
        try:
            validate_document_row(value, issuers)
        except DocumentContractError as exc:
            detail = exc.issues[0].code if exc.issues else "document_row"
            raise EventContractError([
                EventIssue("document_proof_contract", f"{value.get('evidence_id')}:{detail}"),
            ]) from exc
        proved = False
        if value.get("source_kind") != "licensed_news_metadata":
            proof = _capture_bytes(
                root, manifest, value, "publication_proof_artifact",
                "publication_proof_sha256", required=True,
            )
            source = _capture_bytes(
                root, manifest, value, "capture_artifact", "source_sha256", required=False,
            )
            index = _capture_bytes(
                root, manifest, value, "publication_index_artifact",
                "publication_index_sha256", required=False,
            )
            issuer = _capture_bytes(
                root, manifest, value, "publication_issuer_artifact",
                "publication_issuer_sha256", required=False,
            )
            assert proof is not None
            try:
                validate_publication_proof(value, proof, source, index, issuer)
            except DocumentContractError as exc:
                detail = exc.issues[0].code if exc.issues else "publication_proof"
                raise EventContractError([
                    EventIssue("document_proof_contract", f"{value.get('evidence_id')}:{detail}"),
                ]) from exc
            proved = True
        rows.append({
            "evidence_id": value["evidence_id"],
            "issuer_id": value["issuer_id"],
            "event_id": value["event_id"],
            "source_kind": value["source_kind"],
            "published_at": value["published_at"],
            "available_at": value.get("available_at") or value["published_at"],
            "publication_proved": proved,
        })
    return rows


def _market_rows(root: Path, manifest: dict[str, Any]) -> dict[tuple[str, str], frozenset[str]]:
    """Read only digest-bound fields needed to prove event-window coverage."""
    artifacts = [item for item in manifest.get("artifacts", []) if isinstance(item, dict) and str(item.get("path", "")).startswith("bars/interval=1d/")]
    if not artifacts:
        raise EventContractError([EventIssue("market_bar_binding", "missing")])
    rows: dict[tuple[str, str], frozenset[str]] = {}
    try:
        import pyarrow.parquet as pq
        for item in artifacts:
            path = _safe_child(root, str(item.get("path", "")))
            if not path.is_file() or path.stat().st_size != item.get("bytes") or sha256_file(path) != item.get("sha256"):
                raise EventContractError([EventIssue("market_bar_binding", str(item.get("path")))])
            table = pq.read_table(path, columns=["instrument_id", "session_date", "adjusted_close", "volume"])
            if table.num_rows != item.get("records"):
                raise EventContractError([EventIssue("market_bar_binding", "records")])
            for value in table.to_pylist():
                key = (value.get("instrument_id"), value.get("session_date"))
                if not all(isinstance(part, str) and part for part in key) or key in rows:
                    raise EventContractError([EventIssue("market_bar_rows", "identity")])
                rows[key] = frozenset(name for name in ("adjusted_close", "volume") if value.get(name) is not None)
    except EventContractError:
        raise
    except Exception as exc:
        raise EventContractError([EventIssue("market_bar_rows", type(exc).__name__)]) from exc
    return rows


def _analogue_rows(root: Path, scenario: dict[str, Any]) -> set[tuple[str, str]]:
    artifacts = scenario.get("artifacts")
    matches = [item for item in artifacts if isinstance(item, dict) and item.get("path") == "processed/analogue_features.json"] if isinstance(artifacts, list) else []
    if len(matches) != 1:
        return set()
    item = matches[0]
    path = _safe_child(root, item["path"])
    if not path.is_file() or path.stat().st_size != item.get("bytes") or sha256_file(path) != item.get("sha256"):
        raise EventContractError([EventIssue("derived_feature_binding", item["path"])])
    value = _json(path, "derived_feature_rows")
    readiness = scenario.get("readiness")
    expected_binding = {
        "market_manifest_sha256": scenario.get("market", {}).get("manifest_sha256"),
        "document_manifest_sha256": scenario.get("documents", {}).get("manifest_sha256"),
        "market_readiness_sha256": readiness.get("market", {}).get("sha256") if isinstance(readiness, dict) else None,
        "document_readiness_sha256": readiness.get("documents", {}).get("sha256") if isinstance(readiness, dict) else None,
    }
    if (
        set(value) != {"schema_version", "input_binding", "basis", "cutoff_policy", "rows"}
        or value.get("schema_version") != 2
        or value.get("input_binding") != expected_binding
        or any(not isinstance(digest, str) or len(digest) != 64 for digest in expected_binding.values())
        or value.get("basis") != "provider_adjusted"
        or value.get("cutoff_policy") != "feature_at_lt_query_cutoff"
    ):
        raise EventContractError([EventIssue("derived_feature_binding", item["path"])])
    rows = value.get("rows")
    if not isinstance(rows, list) or item.get("records") != len(rows):
        raise EventContractError([EventIssue("derived_feature_rows", "shape")])
    identities: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != FEATURE_ROW_KEYS:
            raise EventContractError([EventIssue("derived_feature_rows", "contract")])
        identity = (row.get("instrument_id"), row.get("session_date"))
        numeric = (row.get("return_1d"), row.get("absolute_return_pct"), row.get("volume_ratio"))
        try:
            feature_at = datetime.fromisoformat(str(row.get("feature_at", "")).replace("Z", "+00:00"))
            valid_numbers = all(
                value is not None and not isinstance(value, bool) and math.isfinite(float(value))
                for value in numeric[:2]
            ) and (
                numeric[2] is None
                or not isinstance(numeric[2], bool) and math.isfinite(float(numeric[2]))
            )
        except (TypeError, ValueError):
            feature_at, valid_numbers = None, False
        if (
            not all(isinstance(part, str) and part for part in identity)
            or identity in identities
            or feature_at is None or feature_at.tzinfo is None
            or feature_at.date().isoformat() != identity[1]
            or not valid_numbers
            or not isinstance(row.get("source_id"), str) or not row["source_id"]
            or not isinstance(row.get("source_row_id"), str) or not row["source_row_id"]
            or not isinstance(row.get("input_source_ids"), list) or not row["input_source_ids"]
            or not all(isinstance(value, str) and value for value in row["input_source_ids"])
            or row["source_id"] not in row["input_source_ids"]
            or not isinstance(row.get("input_source_row_ids"), list) or not row["input_source_row_ids"]
            or not all(isinstance(value, str) and value for value in row["input_source_row_ids"])
            or row["source_row_id"] not in row["input_source_row_ids"]
            or row.get("future_outcome_excluded") is not True
        ):
            raise EventContractError([EventIssue("derived_feature_rows", "contract")])
        identities.add(identity)
    return identities


def load_scenario_binding(root: Path) -> dict[str, Any]:
    """Load only checksum-bound facts needed to qualify event publication."""
    root = root.resolve()
    outer_path = root / "manifest.json"
    outer = _json(outer_path, "scenario_manifest")
    if outer.get("schema_version") != 2 or not isinstance(outer.get("scenario_id"), str):
        raise EventContractError([EventIssue("scenario_manifest", "identity")])
    manifests: dict[str, dict[str, Any]] = {}
    refs: dict[str, dict[str, Any]] = {}
    for name in ("market", "documents"):
        ref = outer.get(name)
        if not isinstance(ref, dict) or set(ref) != {"path", "snapshot_id", "manifest_sha256"}:
            raise EventContractError([EventIssue("scenario_binding", name)])
        nested_path = _safe_child(root, f"{ref['path']}/manifest.json")
        if sha256_file(nested_path) != ref["manifest_sha256"]:
            raise EventContractError([EventIssue("scenario_binding_digest", name)])
        nested = _json(nested_path, f"{name}_manifest")
        if nested.get("snapshot_id") != ref["snapshot_id"]:
            raise EventContractError([EventIssue("scenario_binding_identity", name)])
        manifests[name] = nested
        refs[name] = ref

    coverage = outer.get("coverage")
    index_ref = coverage.get("session_index") if isinstance(coverage, dict) else None
    if not isinstance(index_ref, dict) or set(index_ref) != {"path", "records", "sha256"}:
        raise EventContractError([EventIssue("session_index", "binding")])
    index_path = _safe_child(root, index_ref["path"])
    if sha256_file(index_path) != index_ref["sha256"]:
        raise EventContractError([EventIssue("session_index", "digest")])
    index = _json(index_path, "session_index")
    sessions = index.get("sessions")
    if (
        set(index) != {"schema_version", "calendar", "timezone", "sessions"}
        or index.get("schema_version") != 1
        or index.get("calendar") != "XNYS"
        or index.get("timezone") != "America/New_York"
        or not isinstance(sessions, list)
        or len(sessions) != index_ref["records"]
    ):
        raise EventContractError([EventIssue("session_index", "shape")])
    dates = [item.get("session_date") for item in sessions if isinstance(item, dict)]
    if len(dates) != len(sessions) or dates != sorted(set(dates)):
        raise EventContractError([EventIssue("session_index", "sessions")])
    market_fields = coverage.get("market_fields")
    if not isinstance(market_fields, dict):
        market_fields = manifests["market"].get("field_coverage")
    if not isinstance(market_fields, dict):
        raise EventContractError([EventIssue("market_coverage", "field_coverage")])
    market_root = _safe_child(root, refs["market"]["path"])
    documents_root = _safe_child(root, refs["documents"]["path"])
    document_rows = _document_rows(documents_root, manifests["documents"])
    return {
        "binding": {
            "scenario_id": outer["scenario_id"],
            "scenario_manifest_sha256": sha256_file(outer_path),
            "market_snapshot_id": refs["market"]["snapshot_id"],
            "market_manifest_sha256": refs["market"]["manifest_sha256"],
            "document_snapshot_id": refs["documents"]["snapshot_id"],
            "document_manifest_sha256": refs["documents"]["manifest_sha256"],
        },
        "sessions": set(dates),
        "market_fields": market_fields,
        "market_rows": _market_rows(market_root, manifests["market"]),
        "document_manifest": manifests["documents"],
        "document_rows": document_rows,
        "licensed_news_rows": [row for row in document_rows if row["source_kind"] == "licensed_news_metadata"],
        "scenario_artifacts": outer.get("artifacts", []),
        "analogue_rows": _analogue_rows(root, outer),
    }


def _gap(code: str, layer: str, detail: str) -> dict[str, str]:
    return {"code": code, "layer": layer, "detail": detail}


def _component(status: str, gaps: list[dict[str, str]]) -> dict[str, Any]:
    return {"status": status, "gaps": sorted(gaps, key=lambda row: (row["code"], row["detail"]))}


def qualify_event(event: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    cutoff = datetime.fromisoformat(event["default_cutoff"].replace("Z", "+00:00"))
    news_available = any(
        row.get("issuer_id") == event["primary_ticker"]
        and row.get("event_id") == event["event_id"]
        and row.get("source_kind") == event["source_requirements"]["licensed_news"]["source_kind"]
        and datetime.fromisoformat(row.get("available_at", row["published_at"]).replace("Z", "+00:00")) <= cutoff
        for row in scenario.get("licensed_news_rows", [])
    )
    declared_news_gap = next((
        dict(item) for item in event["known_gaps"]
        if item["layer"] == "licensed_news" and item["code"] == "unsupported_missing_news"
    ), _gap(
        "unsupported_missing_news", "licensed_news",
        "No cutoff-qualified historical news is present for this event.",
    ))
    gaps = [
        dict(item) for item in event["known_gaps"]
        if not (item["layer"] == "licensed_news" and item["code"] == "unsupported_missing_news")
    ]
    if event["source_requirements"]["licensed_news"]["required_for_ready"] and not news_available:
        gaps.append(declared_news_gap)
    gap_keys = {(item["layer"], item["code"]) for item in gaps}
    for observed in scenario["document_manifest"].get("gaps", []):
        if not isinstance(observed, dict):
            continue
        if observed.get("event_id") not in {"all", event["event_id"]} \
                or observed.get("issuer_id") != event["primary_ticker"]:
            continue
        layer = "licensed_news" if observed.get("source_kind") == "licensed_news_metadata" else "documents"
        code = observed.get("code")
        if layer == "licensed_news" and code == "unsupported_missing_news" and news_available:
            continue
        if isinstance(code, str) and (layer, code) not in gap_keys:
            gaps.append(_gap(code, layer, f"The bound document snapshot reports {code.replace('_', ' ')}."))
            gap_keys.add((layer, code))
    sessions: set[str] = scenario["sessions"]
    fields: dict[str, Any] = scenario["market_fields"]
    rows: dict[tuple[str, str], frozenset[str]] = scenario["market_rows"]
    required = event["analysis_tickers"] + event["context_instruments"]
    market_gaps: list[dict[str, str]] = []
    for session_key in ("start_session", "event_session", "end_session"):
        if event[session_key] not in sessions:
            market_gaps.append(_gap(
                "market_session_unavailable", "market", f"{session_key} is not in the bound session index.",
            ))
    for ticker in required:
        coverage = fields.get(ticker)
        if not isinstance(coverage, dict):
            market_gaps.append(_gap(
                "market_instrument_unavailable", "market", f"{ticker} is absent from the bound market snapshot.",
            ))
            continue
        if coverage.get("start") > event["start_session"] or coverage.get("end_inclusive") < event["end_session"]:
            market_gaps.append(_gap(
                "market_window_unavailable", "market", f"{ticker} does not cover the complete event window.",
            ))
        needed = set(event["source_requirements"]["market"]["required_fields"])
        if ticker in event["context_instruments"]:
            needed = {"adjusted_close"}
        missing = sorted(needed - set(coverage.get("present_fields", [])))
        if missing:
            market_gaps.append(_gap(
                "market_field_unavailable", "market", f"{ticker} is missing {', '.join(missing)}.",
            ))
        expected_sessions = sorted(session for session in sessions if event["start_session"] <= session <= event["end_session"])
        if not expected_sessions or any(not needed <= rows.get((ticker, session), frozenset()) for session in expected_sessions):
            market_gaps.append(_gap(
                "market_window_unverified", "market", f"{ticker} has no digest-bound row for every session and required field in the event window.",
            ))
    market = _component("blocked" if market_gaps else "ready", market_gaps)

    document_manifest = scenario["document_manifest"]
    document_requirement = event["source_requirements"]["documents"]
    matches = [row for row in document_manifest.get("requirements", []) if isinstance(row, dict)
               and row.get("requirement_id") == document_requirement["requirement_id"]]
    document_gaps = [item for item in gaps if item["layer"] == "documents"]
    if len(matches) != 1:
        document_gaps.append(_gap(
            "document_requirement_unavailable", "documents", "The bound document snapshot does not contain the declared requirement.",
        ))
    else:
        match = matches[0]
        if (
            match.get("event_id") != event["event_id"]
            or match.get("issuer_id") != event["primary_ticker"]
            or match.get("cutoff") != event["default_cutoff"]
            or match.get("source_kinds") != document_requirement["source_kinds"]
        ):
            document_gaps.append(_gap(
                "document_requirement_drift", "documents", "The declared document requirement does not match the bound snapshot.",
            ))
        evidence = [row for row in scenario["document_rows"] if row["issuer_id"] == event["primary_ticker"] and row["event_id"] == event["event_id"] and row["source_kind"] in document_requirement["source_kinds"] and datetime.fromisoformat(row["available_at"].replace("Z", "+00:00")) <= cutoff and row["publication_proved"]]
        if not evidence:
            document_gaps.append(_gap(
                "document_evidence_unavailable", "documents", "The bound document snapshot has no cutoff-qualified, publication-proved evidence for the declared requirement.",
            ))
    documents = _component("partial" if document_gaps else "ready", document_gaps)

    news_gaps = [item for item in gaps if item["layer"] == "licensed_news"]
    licensed_news = _component("partial" if news_gaps else "ready", news_gaps)
    feature_gaps: list[dict[str, str]] = []
    if "historical-analogues" in event["source_requirements"]["derived_features"]["features"] \
            and (event["primary_ticker"], event["event_session"]) not in scenario["analogue_rows"]:
        feature_gaps.append(_gap(
            "derived_feature_unavailable", "derived_features", "The bound scenario has no qualified historical-analogue feature artifact.",
        ))
    derived = _component("partial" if feature_gaps else "ready", feature_gaps)
    all_gaps = sorted(
        market_gaps + document_gaps + news_gaps + feature_gaps,
        key=lambda row: (row["layer"], row["code"], row["detail"]),
    )
    status = "partial" if all_gaps else "ready"
    return {
        "status": status,
        "market": market,
        "documents": documents,
        "licensed_news": licensed_news,
        "derived_features": derived,
        "gaps": all_gaps,
    }


_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_ARTIFACT_ID = re.compile(r"^shock-events-[a-f0-9]{16}$")
_LAYERS = ("market", "documents", "licensed_news", "derived_features")
_CAPABILITIES = {
    "move-measurement", "evidence-review", "peer-comparison",
    "historical-analogues", "shock-propagation", "risk-analysis",
}


def _prepared_fail(code: str, detail: str) -> None:
    raise EventContractError([EventIssue(code, detail)])


def _prepared_object(value: Any, fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _prepared_fail(code, "shape")
    return value


def _prepared_list(value: Any, code: str, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _prepared_fail(code, "count")
    return value


def _prepared_text(value: Any, code: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        _prepared_fail(code, "text")
    return value


def _prepared_identifier(value: Any, code: str) -> str:
    text = _prepared_text(value, code, 1, 96)
    if _IDENTIFIER.fullmatch(text) is None:
        _prepared_fail(code, "identifier")
    return text


def _prepared_ticker(value: Any) -> str:
    if not isinstance(value, str) or _TICKER.fullmatch(value) is None:
        _prepared_fail("event_ticker", "ticker")
    return value


def _prepared_integer(value: Any, code: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or maximum is not None and value > maximum:
        _prepared_fail(code, "integer")
    return value


def _prepared_date(value: Any, code: str) -> date:
    if not isinstance(value, str):
        _prepared_fail(code, "date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _prepared_fail(code, "date")


def _prepared_datetime(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        _prepared_fail(code, "datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _prepared_fail(code, "datetime")
    if parsed.tzinfo is None:
        _prepared_fail(code, "timezone")
    return parsed


def _validate_prepared_binding(value: Any) -> None:
    binding = _prepared_object(value, {
        "scenario_id", "scenario_manifest_sha256", "market_snapshot_id",
        "market_manifest_sha256", "document_snapshot_id",
        "document_manifest_sha256",
    }, "event_scenario_binding")
    for name in ("scenario_id", "market_snapshot_id", "document_snapshot_id"):
        _prepared_text(binding[name], "event_scenario_binding", 3, 160)
    for name in (
        "scenario_manifest_sha256", "market_manifest_sha256",
        "document_manifest_sha256",
    ):
        if not isinstance(binding[name], str) or _DIGEST.fullmatch(binding[name]) is None:
            _prepared_fail("event_scenario_binding", name)


def _validate_prepared_gap(value: Any, expected_layer: str | None = None) -> tuple[str, str, str]:
    gap = _prepared_object(value, {"code", "layer", "detail"}, "event_gap")
    code = _prepared_text(gap["code"], "event_gap", 1, 96)
    if re.fullmatch(r"[a-z][a-z0-9_]*", code) is None:
        _prepared_fail("event_gap", "code")
    layer = gap["layer"]
    if layer not in _LAYERS or expected_layer is not None and layer != expected_layer:
        _prepared_fail("event_gap_layer", str(layer))
    detail = _prepared_text(gap["detail"], "event_gap", 10, 280)
    return layer, code, detail


def _validate_prepared_qualification(value: Any) -> str:
    qualification = _prepared_object(value, {"status", *_LAYERS, "gaps"}, "event_qualification")
    if qualification["status"] not in {"ready", "partial"}:
        _prepared_fail("event_status", "status")
    flattened: list[tuple[str, str, str]] = []
    ready = True
    for layer_name in _LAYERS:
        layer = _prepared_object(
            qualification[layer_name], {"status", "gaps"}, "event_layer_status",
        )
        allowed = {"ready", "blocked"} if layer_name == "market" else {"ready", "partial"}
        gaps = _prepared_list(layer["gaps"], "event_gap_projection", 0, 10_000)
        if layer["status"] not in allowed or (layer["status"] == "ready") != (not gaps):
            _prepared_fail("event_layer_status", layer_name)
        if layer_name == "market" and layer["status"] != "ready":
            _prepared_fail("event_market_not_ready", layer_name)
        ready = ready and layer["status"] == "ready"
        flattened.extend(_validate_prepared_gap(gap, layer_name) for gap in gaps)
    projected = [
        _validate_prepared_gap(gap) for gap in
        _prepared_list(qualification["gaps"], "event_gap_projection", 0, 40_000)
    ]
    if len(set(flattened)) != len(flattened) or sorted(projected) != sorted(flattened):
        _prepared_fail("event_gap_projection", "qualification")
    if (qualification["status"] == "ready") != ready:
        _prepared_fail("event_status", "qualification")
    return qualification["status"]


def _validate_prepared_requirements(value: Any) -> None:
    requirements = _prepared_object(
        value, {"market", "documents", "licensed_news", "derived_features"},
        "event_source_requirements",
    )
    market = _prepared_object(
        requirements["market"], {"required", "required_fields", "price_basis"},
        "event_market_requirement",
    )
    fields = _prepared_list(market["required_fields"], "event_market_requirement", 1, 2)
    if market["required"] is not True or market["price_basis"] != "provider_adjusted" \
            or fields != sorted(set(fields)) or not set(fields) <= {"adjusted_close", "volume"}:
        _prepared_fail("event_market_requirement", "values")
    documents = _prepared_object(
        requirements["documents"], {"required_for_ready", "requirement_id", "source_kinds"},
        "event_document_requirement",
    )
    kinds = _prepared_list(documents["source_kinds"], "event_document_requirement", 1, 3)
    _prepared_identifier(documents["requirement_id"], "event_document_requirement")
    if documents["required_for_ready"] is not True or kinds != sorted(set(kinds)) \
            or not set(kinds) <= {"company_release", "filing", "primary_source"}:
        _prepared_fail("event_document_requirement", "values")
    news = _prepared_object(
        requirements["licensed_news"],
        {"required_for_publication", "required_for_ready", "source_kind"},
        "event_news_requirement",
    )
    if news != {
        "required_for_publication": False,
        "required_for_ready": True,
        "source_kind": "licensed_news_metadata",
    }:
        _prepared_fail("event_news_requirement", "values")
    derived = _prepared_object(
        requirements["derived_features"], {"required_for_ready", "features"},
        "event_derived_requirement",
    )
    features = _prepared_list(derived["features"], "event_derived_requirement", 1, 4)
    order = ["event-returns", "relative-returns", "volume-context", "historical-analogues"]
    if derived["required_for_ready"] is not True \
            or features != [item for item in order if item in features]:
        _prepared_fail("event_derived_requirement", "values")


def _validate_prepared_event(value: Any) -> tuple[str, str, int, str, list[str]]:
    event = _prepared_object(value, {
        "event_id", "category_id", "title", "summary", "sort_order", "event_session",
        "source_dates", "primary_ticker", "analysis_tickers", "context_instruments", "start_session",
        "end_session", "default_cutoff", "questions", "source_requirements",
        "limitations", "qualification",
    }, "event_contract")
    event_id = _prepared_identifier(event["event_id"], "event_identity")
    category_id = _prepared_identifier(event["category_id"], "event_category")
    _prepared_text(event["title"], "event_contract", 5, 100)
    _prepared_text(event["summary"], "event_contract", 20, 420)
    sort_order = _prepared_integer(event["sort_order"], "event_order", 1, 10_000)
    start = _prepared_date(event["start_session"], "event_chronology")
    session = _prepared_date(event["event_session"], "event_chronology")
    end = _prepared_date(event["end_session"], "event_chronology")
    cutoff = _prepared_datetime(event["default_cutoff"], "event_cutoff")
    if not start <= session <= cutoff.date() <= end:
        _prepared_fail("event_chronology", event_id)
    source_dates = [_prepared_date(item, "event_source_dates") for item in _prepared_list(event["source_dates"], "event_source_dates", 1, 8)]
    if len(set(source_dates)) != len(source_dates) or not all(start <= item <= cutoff.date() for item in source_dates):
        _prepared_fail("event_source_dates", event_id)
    primary = _prepared_ticker(event["primary_ticker"])
    analysis = [_prepared_ticker(item) for item in _prepared_list(
        event["analysis_tickers"], "event_analysis_scope", 1, 5,
    )]
    context = [_prepared_ticker(item) for item in _prepared_list(
        event["context_instruments"], "event_context_scope", 1, 4,
    )]
    if len(set(analysis)) != len(analysis) or analysis[0] != primary:
        _prepared_fail("event_primary_scope", event_id)
    if len(set(context)) != len(context):
        _prepared_fail("event_context_scope", event_id)
    if set(analysis) & set(context):
        _prepared_fail("event_scope_overlap", event_id)
    question_ids: list[str] = []
    for question_value in _prepared_list(event["questions"], "event_questions", 3, 6):
        question = _prepared_object(
            question_value, {"question_id", "label", "capability", "text"}, "event_questions",
        )
        question_ids.append(_prepared_identifier(question["question_id"], "event_questions"))
        _prepared_text(question["label"], "event_questions", 3, 72)
        _prepared_text(question["text"], "event_questions", 12, 500)
        if question["capability"] not in _CAPABILITIES:
            _prepared_fail("event_questions", "capability")
    if len(set(question_ids)) != len(question_ids):
        _prepared_fail("event_questions", "identity")
    limitation_ids: list[str] = []
    for limitation_value in _prepared_list(event["limitations"], "event_limitations", 1, 20):
        limitation = _prepared_object(
            limitation_value, {"limitation_id", "detail"}, "event_limitations",
        )
        limitation_ids.append(_prepared_identifier(limitation["limitation_id"], "event_limitations"))
        _prepared_text(limitation["detail"], "event_limitations", 10, 280)
    if len(set(limitation_ids)) != len(limitation_ids):
        _prepared_fail("event_limitations", "identity")
    _validate_prepared_requirements(event["source_requirements"])
    status = _validate_prepared_qualification(event["qualification"])
    return event_id, category_id, sort_order, status, question_ids


def _validate_prepared_summary(value: Any) -> dict[str, int]:
    summary = _prepared_object(value, {
        "declared_events", "published_events", "ready_events", "partial_events",
        "excluded_events",
    }, "event_summary")
    for name in summary:
        _prepared_integer(summary[name], "event_summary", 0)
    return summary


def validate_prepared_catalog(payload: Any) -> None:
    """Enforce the fail-closed semantic contract used by the runtime reader."""
    catalog = _prepared_object(payload, {
        "schema_version", "artifact_id", "catalog_id", "calendar", "timezone",
        "binding", "categories", "events", "excluded_events", "summary",
    }, "event_artifact_catalog")
    if catalog["schema_version"] != 1 \
            or not isinstance(catalog["artifact_id"], str) \
            or _ARTIFACT_ID.fullmatch(catalog["artifact_id"]) is None \
            or catalog["catalog_id"] != "curated-shock-events-v1" \
            or catalog["calendar"] != "XNYS" \
            or catalog["timezone"] != "America/New_York":
        _prepared_fail("event_artifact_catalog", "identity")
    _validate_prepared_binding(catalog["binding"])
    categories = _prepared_list(catalog["categories"], "event_categories", 1, 32)
    category_ids: list[str] = []
    category_orders: list[int] = []
    for category_value in categories:
        category = _prepared_object(
            category_value, {"category_id", "label", "description", "sort_order"},
            "event_categories",
        )
        category_ids.append(_prepared_identifier(category["category_id"], "event_categories"))
        _prepared_text(category["label"], "event_categories", 3, 64)
        _prepared_text(category["description"], "event_categories", 10, 240)
        category_orders.append(_prepared_integer(category["sort_order"], "event_category_order", 1, 32))
    if len(set(category_ids)) != len(category_ids):
        _prepared_fail("event_categories", "identity")
    if category_orders != list(range(1, len(categories) + 1)):
        _prepared_fail("event_category_order", "order")
    events = _prepared_list(catalog["events"], "event_count", 1, 1_000)
    event_rows = [_validate_prepared_event(event) for event in events]
    event_ids = [item[0] for item in event_rows]
    if len(set(event_ids)) != len(event_ids):
        _prepared_fail("event_identity", "identity")
    if any(item[1] not in set(category_ids) for item in event_rows):
        _prepared_fail("event_category", "category")
    event_orders = [item[2] for item in event_rows]
    if event_orders != sorted(event_orders):
        _prepared_fail("event_order", "order")
    question_ids = [question_id for item in event_rows for question_id in item[4]]
    if len(set(question_ids)) != len(question_ids):
        _prepared_fail("event_question_identity", "identity")
    excluded = _prepared_list(catalog["excluded_events"], "event_exclusion", 0, 1_000)
    excluded_ids: list[str] = []
    for excluded_value in excluded:
        item = _prepared_object(excluded_value, {"event_id", "gaps"}, "event_exclusion")
        excluded_ids.append(_prepared_identifier(item["event_id"], "event_exclusion"))
        gaps = _prepared_list(item["gaps"], "event_exclusion", 1, 40_000)
        if any(_validate_prepared_gap(gap)[0] != "market" for gap in gaps):
            _prepared_fail("event_exclusion", "layer")
    summary = _validate_prepared_summary(catalog["summary"])
    ready = sum(item[3] == "ready" for item in event_rows)
    if summary != {
        "declared_events": len(events) + len(excluded),
        "published_events": len(events),
        "ready_events": ready,
        "partial_events": len(events) - ready,
        "excluded_events": len(excluded),
    }:
        _prepared_fail("event_summary", "projection")


def validate_event_artifact(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    manifest = _json(root / "manifest.json", "event_artifact_manifest")
    required = {
        "schema_version", "artifact_kind", "artifact_id", "catalog_id", "catalog_sha256",
        "schema_sha256", "binding", "summary", "artifacts",
    }
    if set(manifest) != required or manifest.get("schema_version") != 1 \
            or manifest.get("artifact_kind") != "shock-event-catalog-v1" \
            or manifest.get("artifact_id") != root.name \
            or not isinstance(manifest.get("artifact_id"), str) \
            or _ARTIFACT_ID.fullmatch(manifest["artifact_id"]) is None \
            or manifest.get("catalog_id") != "curated-shock-events-v1" \
            or not isinstance(manifest.get("catalog_sha256"), str) \
            or _DIGEST.fullmatch(manifest["catalog_sha256"]) is None \
            or not isinstance(manifest.get("schema_sha256"), str) \
            or _DIGEST.fullmatch(manifest["schema_sha256"]) is None:
        raise EventContractError([EventIssue("event_artifact_manifest", "shape")])
    _validate_prepared_binding(manifest.get("binding"))
    _validate_prepared_summary(manifest.get("summary"))
    if not isinstance(manifest.get("artifacts"), list) or len(manifest["artifacts"]) != 1:
        raise EventContractError([EventIssue("event_artifact_manifest", "artifacts")])
    record = manifest["artifacts"][0]
    if not isinstance(record, dict) \
            or set(record) != {"path", "sha256", "bytes", "records", "media_type"} \
            or record.get("path") != "catalog.json" \
            or not isinstance(record.get("sha256"), str) \
            or _DIGEST.fullmatch(record["sha256"]) is None \
            or type(record.get("bytes")) is not int or not 0 < record["bytes"] <= 8 * 1024 * 1024 \
            or type(record.get("records")) is not int or not 0 <= record["records"] <= 1_000 \
            or record.get("media_type") != "application/json" \
            or record["records"] != manifest["summary"]["published_events"]:
        raise EventContractError([EventIssue("event_artifact_manifest", "catalog record")])
    path = _safe_child(root, record["path"])
    if not path.is_file() or path.stat().st_size != record["bytes"] or sha256_file(path) != record["sha256"]:
        raise EventContractError([EventIssue("event_artifact_digest", record["path"])])
    payload = _json(path, "event_artifact_catalog")
    validate_prepared_catalog(payload)
    expected_id = "shock-events-" + sha256_bytes(canonical_json({
        "catalog_sha256": manifest["catalog_sha256"],
        "schema_sha256": manifest["schema_sha256"],
        "binding": manifest["binding"],
    }))[:16]
    if payload.get("artifact_id") != expected_id or expected_id != manifest["artifact_id"]:
        raise EventContractError([EventIssue("event_artifact_identity", expected_id)])
    if payload.get("catalog_id") != manifest["catalog_id"] \
            or payload.get("binding") != manifest["binding"] \
            or payload.get("summary") != manifest["summary"] \
            or len(payload["events"]) != record["records"]:
        raise EventContractError([EventIssue("event_artifact_projection", "manifest")])
    return manifest, payload
