"""Immutable, fail-closed view of one validated Phase 09 publication."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, make_dataclass
from datetime import date, datetime, UTC
from pathlib import Path
from typing import Any, Literal

EMBED_MODEL = "nvidia/Nemotron-3-Embed-1B-BF16"
EMBED_REVISION = "9e0b24858b1195815ecb1188ffa1b73bcea7b30a"
_ROOT_KEYS = {
    "schema_version",
    "scenario_id",
    "snapshot_id",
    "recipe_version",
    "data_tier",
    "vintage_status",
    "created_at",
    "cutoff_policy",
    "coverage",
    "market",
    "documents",
    "readiness",
    "embedding",
    "semantic",
    "artifacts",
}
_MARKET_KEYS = {
    "case_id",
    "snapshot_id",
    "snapshot_manifest_sha256",
    "bank_sha256",
    "contract_sha256",
    "tickers",
    "requested_date",
    "resolved_completed_session",
    "lookback_sessions",
    "required_instruments",
    "optional_instruments",
    "required_fields",
    "required_interval",
    "required_price_basis",
    "requires_actions",
    "vintage_requirement",
    "status",
    "missing_items",
}
_DOCUMENT_KEYS = {
    "case_id",
    "document_snapshot_id",
    "document_manifest_sha256",
    "bank_sha256",
    "contract_sha256",
    "issuer_ids",
    "cutoff",
    "required_source_kinds",
    "matched_requirement_ids",
    "matched_evidence_ids",
    "status",
    "missing_requirements",
}
_DOC_KEYS = {
    "document_id",
    "chunk_id",
    "source_id",
    "revision",
    "source_type",
    "canonical_url",
    "title",
    "issuer_ids",
    "published_at",
    "available_at",
    "captured_at",
    "vintage_status",
    "content_scope",
    "text",
    "content_sha256",
}
_FIELDS = {
    f"{basis}_{name}" for basis in ("raw", "adjusted") for name in ("open", "high", "low", "close")
} | {"raw_volume", "volume"}


class CoverageError(ValueError):
    def __init__(self, code: str, detail: object):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _need(value: bool, code: str, detail: object) -> None:
    if not value:
        raise CoverageError(code, detail)


MarketSession = make_dataclass(
    "MarketSession",
    (("session_date", "date"), ("open_at", "datetime"), ("close_at", "datetime")),
    frozen=True,
    module=__name__,
)
DocumentSupport = make_dataclass(
    "DocumentSupport",
    (("issuer_id", "str"), ("source_kind", "str"), ("published_at", "datetime")),
    frozen=True,
    module=__name__,
)
CaseReadiness = make_dataclass(
    "CaseReadiness",
    (
        ("case_id", "str"),
        ("market_status", "str"),
        ("document_status", "str"),
        ("market_interval", "str | None"),
        ("document_cutoff", "datetime | None"),
    ),
    frozen=True,
    module=__name__,
)


@dataclass(frozen=True)
class CoverageCatalog:
    scenario_id: str
    scenario_manifest_sha256: str
    gate: Literal["reconstruction", "release"]
    data_tier: str
    vintage_status: str
    cutoff_policy: str
    verification: Literal["phase09_v2", "explicit_test"]
    market_snapshot_id: str
    market_manifest_sha256: str
    document_snapshot_id: str
    document_manifest_sha256: str
    market_readiness_sha256: str
    document_readiness_sha256: str
    bank_sha256: str
    targets: tuple[str, ...]
    peers: tuple[str, ...]
    required_benchmarks: tuple[str, ...]
    optional_instruments: tuple[str, ...]
    sessions: tuple[MarketSession, ...]
    market_fields: tuple[tuple[str, tuple[str, ...]], ...]
    benchmark_policy: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    document_issuers: tuple[str, ...]
    document_source_kinds: tuple[str, ...]
    document_support: tuple[DocumentSupport, ...]
    readiness: tuple[CaseReadiness, ...]
    limitations: tuple[str, ...]

    def fields_for(self, ticker: str) -> tuple[str, ...]:
        return dict(self.market_fields).get(ticker.upper(), ())

    def resolve_completed_session(self, cutoff: date | datetime) -> MarketSession | None:
        if isinstance(cutoff, datetime):
            _need(cutoff.tzinfo is not None, "naive_cutoff", "datetime")
            return next(
                (row for row in reversed(self.sessions) if row.close_at <= cutoff.astimezone(UTC)),
                None,
            )
        return next((row for row in reversed(self.sessions) if row.session_date <= cutoff), None)

    def supports_document(self, issuer: str, source_kind: str, cutoff: datetime) -> bool:
        _need(cutoff.tzinfo is not None, "naive_cutoff", "document cutoff")
        instant = cutoff.astimezone(UTC)
        return any(
            row.issuer_id == issuer.upper() and row.source_kind == source_kind and row.published_at <= instant
            for row in self.document_support
        )

    @classmethod
    def load(cls, root: Path, *, gate: Literal["reconstruction", "release"]) -> CoverageCatalog:
        return _load(root, gate)


def _read(
    path: Path, code: str, *, lines: bool = False, detail: object | None = None
) -> dict[str, Any] | list[Any]:
    try:
        text = path.read_text(encoding="utf-8")
        value = (
            [json.loads(line) for line in text.splitlines() if line.strip()] if lines else json.loads(text)
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageError(code, path.name if detail is None else detail) from exc
    _need(lines or isinstance(value, dict), code, "object required")
    return value


def _path(root: Path, relative: object) -> Path:
    item = Path(str(relative))
    _need(not item.is_absolute() and bool(item.parts) and ".." not in item.parts, "artifact_path", relative)
    child = (root / item).resolve()
    _need(root == child or root in child.parents, "artifact_escape", relative)
    return child


def _sha(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise CoverageError("artifact_missing", path.name) from exc


def _utc(value: object, code: str) -> datetime:
    _need(isinstance(value, str) and value.endswith("Z"), code, value)
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CoverageError(code, value) from exc


def _artifacts(root: Path, values: object) -> None:
    _need(isinstance(values, list), "artifact_shape", root.name)
    seen: set[str] = set()
    for row in values:
        _need(isinstance(row, dict) and {"path", "sha256", "bytes"} <= set(row), "artifact_shape", root.name)
        name = str(row["path"])
        _need(name not in seen, "artifact_duplicate", name)
        seen.add(name)
        path = _path(root, name)
        _need(
            path.is_file()
            and type(row["bytes"]) is int
            and path.stat().st_size == row["bytes"]
            and _sha(path) == row["sha256"],
            "artifact_digest",
            name,
        )


def _proof(root: Path, source: dict[str, Any]) -> bool:
    proof = source.get("archive_proof")
    if not isinstance(proof, dict) or not {
        "path",
        "sha256",
        "bytes",
        "canonical_url",
        "proved_at",
        "coverage_end_exclusive",
    } <= set(proof):
        return False
    path = _path(root, proof["path"])
    return (
        str(proof["canonical_url"]).startswith("https://")
        and _utc(proof["proved_at"], "archive_proof") <= _utc(source.get("captured_at"), "captured_at")
        and path.is_file()
        and path.stat().st_size == proof["bytes"]
        and _sha(path) == proof["sha256"]
    )


def _nested(root: Path, binding: object, kind: str, gate: str) -> tuple[Path, dict[str, Any]]:
    _need(
        isinstance(binding, dict) and set(binding) == {"path", "snapshot_id", "manifest_sha256"},
        "nested_binding",
        kind,
    )
    nested = _path(root, binding["path"])
    manifest = _read(nested / "manifest.json", "nested_manifest")
    _need(_sha(nested / "manifest.json") == binding["manifest_sha256"], "nested_manifest_digest", kind)
    sources = manifest.get("sources")
    _need(
        isinstance(sources, list) and sources and all(isinstance(row, dict) for row in sources),
        "nested_sources",
        kind,
    )
    captures = sorted(
        (row.get("capture_id"), row.get("capture_sha256" if kind == "market" else "manifest_sha256"))
        for row in sources
    )
    identity = {
        "normalizer_version": manifest.get("normalizer_version"),
        "captures": captures,
        "gate": gate,
        "captured_at": manifest.get("captured_at"),
        **(
            {
                "window": manifest.get("requested_window"),
                "universe_sha256": manifest.get("universe_sha256"),
                "actions_sha256": manifest.get("actions_sha256"),
            }
            if kind == "market"
            else {"corpus_sha256": manifest.get("corpus_sha256")}
        ),
    }
    tier = "cc0_reconstruction" if kind == "market" else "authoritative_reconstruction"
    expected = (
        (tier, "reconstructed_later")
        if gate == "reconstruction"
        else ("entitled_local", "archived_at_cutoff")
    )
    _need(
        manifest.get("schema_version") == 2
        and manifest.get("snapshot_kind") == kind
        and manifest.get("snapshot_id")
        == binding["snapshot_id"]
        == f"{kind}-"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()[:16],
        "nested_identity",
        kind,
    )
    _need(
        manifest.get("gate") == gate
        and (manifest.get("data_tier"), manifest.get("vintage_status")) == expected,
        "nested_gate",
        kind,
    )
    _utc(manifest.get("created_at"), "nested_created_at")
    _utc(manifest.get("captured_at"), "nested_captured_at")
    _artifacts(nested, manifest.get("artifacts"))
    for source in sources:
        _utc(source.get("captured_at"), "source_captured_at")
        if kind == "market":
            _need(
                _sha(_path(nested, f"lineage/{source.get('source_id')}-{source.get('capture_id')}.json"))
                == source.get("capture_sha256"),
                "market_source_binding",
                source.get("capture_id"),
            )
            proof_root = nested
        else:
            proof_root = _path(nested, source.get("path"))
            capture = _read(proof_root / "capture.json", "document_capture")
            _need(
                _sha(proof_root / "capture.json") == source.get("manifest_sha256")
                and capture.get("capture_id") == source.get("capture_id")
                and capture.get("adapter") == source.get("adapter")
                and capture.get("captured_at") == source.get("captured_at")
                and capture.get("vintage_status") == source.get("vintage_status"),
                "document_source_binding",
                source.get("capture_id"),
            )
        if gate == "release":
            _need(
                bool(source.get("license_id"))
                and source.get("redistribution") in {"allowed", "metadata_only", "local_only"}
                and source.get("vintage_status") == "archived_at_cutoff"
                and _proof(proof_root, source),
                "release_source_unproved",
                source.get("capture_id"),
            )
    return nested, manifest


def _ready(
    root: Path, name: str, binding: object, nested: dict[str, Any], manifest_sha: str
) -> list[dict[str, Any]]:
    _need(
        isinstance(binding, dict)
        and set(binding) == {"path", "sha256", "records", "snapshot_id", "bank_sha256", "contracts_sha256"}
        and binding.get("snapshot_id") == nested.get("snapshot_id"),
        "readiness_binding",
        name,
    )
    path = _path(root, binding["path"])
    _need(_sha(path) == binding["sha256"], "readiness_digest", name)
    rows = _read(path, "readiness_unreadable", lines=True, detail=name)
    keys = _MARKET_KEYS if name == "market" else _DOCUMENT_KEYS
    snap, manifest_key, missing_key = (
        ("snapshot_id", "snapshot_manifest_sha256", "missing_items")
        if name == "market"
        else ("document_snapshot_id", "document_manifest_sha256", "missing_requirements")
    )
    _need(len(rows) == binding["records"] == 250, "readiness_count", name)
    seen: set[str] = set()
    for row in rows:
        _need(
            isinstance(row, dict)
            and set(row) == keys
            and isinstance(row.get("case_id"), str)
            and row["case_id"] not in seen
            and row.get(snap) == nested["snapshot_id"]
            and row.get(manifest_key) == manifest_sha,
            "readiness_row_binding",
            name,
        )
        seen.add(row["case_id"])
        missing = row[missing_key]
        _need(
            row.get("status")
            in {"ready", "not_applicable", "expected_missing", "needs_scope_resolution", "blocked"}
            and isinstance(missing, list)
            and all(isinstance(item, dict) for item in missing),
            "readiness_status",
            row["case_id"],
        )
        _need(
            not (
                name == "market"
                and row["status"] == "not_applicable"
                and any(
                    (
                        row["tickers"],
                        row["required_instruments"],
                        row["optional_instruments"],
                        row["required_fields"],
                        row["required_interval"] is not None,
                        row["requires_actions"],
                        row["lookback_sessions"],
                        row["required_price_basis"] != "not_applicable",
                    )
                )
            )
            and not (
                name == "market" and row["status"] != "not_applicable" and row["required_interval"] != "1d"
            )
            and not (
                row["status"] == "ready"
                and any(
                    name != "market"
                    or item.get("code")
                    not in {"optional_instrument_unavailable", "subdaily_chronology_unavailable"}
                    for item in missing
                )
            )
            and not (
                row["status"] in {"expected_missing", "needs_scope_resolution", "blocked"} and not missing
            )
            and not (
                row["status"] == "needs_scope_resolution"
                and not any("unresolved" in str(item.get("code", "")) for item in missing)
            ),
            "readiness_status",
            row["case_id"],
        )
    contracts = sorted((row["case_id"], row["contract_sha256"]) for row in rows)
    banks = {row["bank_sha256"] for row in rows}
    digests = banks | {value for _, value in contracts}
    _need(
        len(seen) == 250
        and banks == {binding["bank_sha256"]}
        and len(set(value for _, value in contracts)) == 250
        and all(
            isinstance(value, str) and len(value) == 64 and set(value) <= set("abcdef0123456789")
            for value in digests
        )
        and hashlib.sha256(
            json.dumps(contracts, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        == binding["contracts_sha256"],
        "readiness_aggregate_binding",
        name,
    )
    return rows


def _document_readiness_supported(
    row: dict[str, Any], by_id: dict[str, dict[str, Any]], issuers: tuple[str, ...], kinds: tuple[str, ...]
) -> bool:
    """Validate one bound readiness profile without assuming a fixed corpus."""
    cutoff = _utc(row["cutoff"], "document_cutoff") if row["cutoff"] else None
    matched = [by_id.get(item) for item in row["matched_evidence_ids"]]
    valid_matches = all(
        item is not None
        and (cutoff is None or _utc(item["published_at"], "document_cutoff") <= cutoff)
        and bool(set(item["issuer_ids"]) & set(row["issuer_ids"]))
        and (not row["required_source_kinds"] or item["source_type"] in row["required_source_kinds"])
        for item in matched
    )
    required, missing, status = row["required_source_kinds"], row["missing_requirements"], row["status"]
    codes = [item.get("code") for item in missing]
    if any(not isinstance(code, str) or not code for code in codes):
        return False
    if not required:
        expected, valid_profile = "not_applicable", not missing
    elif any("unresolved" in code for code in codes):
        expected, valid_profile = "needs_scope_resolution", True
    elif not missing:
        expected, valid_profile = "ready", True
    elif status in {"blocked", "expected_missing"}:
        expected, valid_profile = (
            status,
            all(code.startswith("unsupported_") or code == "event_requirement_unavailable" for code in codes),
        )
    else:
        return False
    if status == "ready":
        observed = {(issuer, item["source_type"]) for item in matched for issuer in item["issuer_ids"]}
        expected_support = {(issuer, kind) for issuer in row["issuer_ids"] for kind in required}
        valid_profile = (
            valid_profile
            and set(row["issuer_ids"]) <= set(issuers)
            and set(required) <= set(kinds)
            and expected_support <= observed
        )
    return valid_matches and valid_profile and status == expected


def _load(root: Path, gate: Literal["reconstruction", "release"]) -> CoverageCatalog:
    _need(gate in {"reconstruction", "release"}, "gate", gate)
    root = root.resolve()
    manifest_path = root / "manifest.json"
    manifest = _read(manifest_path, "manifest_unreadable")
    _need(
        set(manifest) == _ROOT_KEYS
        and manifest.get("schema_version") == 2
        and manifest.get("recipe_version") == 3
        and isinstance(manifest.get("coverage"), dict)
        and isinstance(manifest.get("readiness"), dict)
        and set(manifest["readiness"]) == {"market", "documents"},
        "scenario_schema",
        "schema-v2 recipe-v3 required",
    )
    scenario = str(manifest.get("scenario_id", ""))
    tier, vintage = (
        ("cc0_reconstruction", "reconstructed_later")
        if gate == "reconstruction"
        else ("entitled_local", "archived_at_cutoff")
    )
    _need(
        manifest.get("snapshot_id") == scenario
        and scenario.startswith("market-shock-v2-")
        and (manifest.get("data_tier"), manifest.get("vintage_status")) == (tier, vintage)
        and manifest.get("cutoff_policy") == "observation_or_published_at_lte_case_cutoff",
        "scenario_gate",
        gate,
    )
    _utc(manifest.get("created_at"), "scenario_created_at")
    market_root, market = _nested(root, manifest["market"], "market", gate)
    _, documents = _nested(root, manifest["documents"], "documents", gate)
    _artifacts(root, manifest["artifacts"])
    market_rows = _ready(
        root, "market", manifest["readiness"]["market"], market, manifest["market"]["manifest_sha256"]
    )
    document_rows = _ready(
        root,
        "documents",
        manifest["readiness"]["documents"],
        documents,
        manifest["documents"]["manifest_sha256"],
    )
    paired = {row["case_id"]: row for row in document_rows}
    _need(
        not any(
            row["case_id"] not in paired
            or (row["bank_sha256"], row["contract_sha256"])
            != (paired[row["case_id"]]["bank_sha256"], paired[row["case_id"]]["contract_sha256"])
            for row in market_rows
        ),
        "readiness_cross_binding",
        "market/documents",
    )
    identity = {
        "recipe_version": 3,
        "created_at": manifest["created_at"],
        "market_manifest_sha256": manifest["market"]["manifest_sha256"],
        "document_manifest_sha256": manifest["documents"]["manifest_sha256"],
        "market_readiness_sha256": manifest["readiness"]["market"]["sha256"],
        "document_readiness_sha256": manifest["readiness"]["documents"]["sha256"],
        "embedding_model": f"{EMBED_MODEL}@{EMBED_REVISION}",
    }
    identity_sha = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    _need(scenario == f"market-shock-v2-{identity_sha[:16]}", "scenario_identity", scenario)
    expected_embed = {
        "model_id": EMBED_MODEL,
        "revision": EMBED_REVISION,
        "tokenizer_revision": EMBED_REVISION,
        "dimension": 2048,
        "model_dtype": "bfloat16",
        "storage_dtype": "float32",
        "query_role": "query",
        "passage_role": "passage",
        "normalized": True,
        "max_tokens": 4096,
        "implementation": "pinned_model",
    }
    _need(manifest["embedding"] == expected_embed, "embedding_contract", scenario)
    semantic = manifest["semantic"]
    _need(isinstance(semantic, dict), "semantic_not_ready", scenario)
    receipt = semantic.get("gpu_receipt")
    index = _path(root, semantic.get("index_path"))
    expected_receipt = {
        "engine": "cuvs",
        "device": receipt.get("device") if isinstance(receipt, dict) else None,
        "gpu_executed": True,
        "fallback_used": False,
        "model_id": EMBED_MODEL,
        "revision": EMBED_REVISION,
        "dimension": 2048,
    }
    _need(
        semantic.get("status") == "ready"
        and semantic.get("scenario_input_sha256") == identity_sha
        and semantic.get("market_manifest_sha256") == identity["market_manifest_sha256"]
        and semantic.get("document_manifest_sha256") == identity["document_manifest_sha256"]
        and _sha(index) == semantic.get("index_sha256")
        and receipt == expected_receipt,
        "semantic_not_ready",
        scenario,
    )
    coverage = manifest["coverage"]
    universe = market.get("universe", {})
    quality = market.get("quality", {})
    observed = documents.get("observed_coverage", {})
    expected_coverage = {
        "market_snapshot_id": market["snapshot_id"],
        "document_snapshot_id": documents["snapshot_id"],
        "targets": universe.get("targets"),
        "required_benchmarks": universe.get("required_benchmarks"),
        "optional_gaps": [row for row in quality.get("gaps", []) if not row.get("required")],
        "market_date_coverage": market.get("observed_coverage"),
        "market_fields": market.get("field_coverage"),
        "document_coverage": observed,
        "document_gaps": documents.get("gaps"),
        "session_index": coverage.get("session_index"),
    }
    _need(coverage == expected_coverage, "coverage_drift", scenario)
    session_binding = coverage.get("session_index", {})
    session_path = _path(root, session_binding.get("path"))
    _need(_sha(session_path) == session_binding.get("sha256"), "session_index_digest", scenario)
    session_data = _read(session_path, "session_index_unreadable")
    _need(
        set(session_data) == {"schema_version", "calendar", "timezone", "sessions"}
        and (session_data.get("schema_version"), session_data.get("calendar"), session_data.get("timezone"))
        == (1, "XNYS", "America/New_York")
        and isinstance(session_data.get("sessions"), list)
        and len(session_data["sessions"]) == session_binding.get("records"),
        "session_index_schema",
        scenario,
    )
    try:
        sessions = tuple(
            MarketSession(
                date.fromisoformat(row["session_date"]),
                _utc(row["open_at"], "session_time"),
                _utc(row["close_at"], "session_time"),
            )
            for row in session_data["sessions"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CoverageError("session_index_values", scenario) from exc
    _need(
        bool(sessions)
        and list(sessions) == sorted(sessions, key=lambda row: row.session_date)
        and len({row.session_date for row in sessions}) == len(sessions)
        and not any(row.open_at >= row.close_at for row in sessions),
        "session_index_values",
        scenario,
    )
    normalized = next(
        (row for row in manifest["artifacts"] if row.get("path") == "processed/documents.jsonl"), None
    )
    _need(bool(normalized), "document_binding", "processed/documents.jsonl")
    docs = _read(
        _path(root, normalized["path"]), "document_binding", lines=True, detail="normalized evidence"
    )
    support: list[DocumentSupport] = []
    chunks: list[str] = []
    for row in docs:
        _need(
            isinstance(row, dict)
            and set(row) == _DOC_KEYS
            and isinstance(row["issuer_ids"], list)
            and row["issuer_ids"]
            and row["available_at"] == row["published_at"]
            and row["chunk_id"] == row["document_id"] + "-c0"
            and row["source_id"] == row["document_id"]
            and hashlib.sha256(row["text"].encode()).hexdigest() == row["content_sha256"],
            "document_binding",
            "normalized evidence",
        )
        published = _utc(row["published_at"], "document_cutoff")
        captured = _utc(row["captured_at"], "document_capture")
        _need(
            published <= captured
            and row["vintage_status"] == vintage
            and str(row["canonical_url"]).startswith("https://"),
            "document_binding",
            row["document_id"],
        )
        chunks.append(row["chunk_id"])
        support.extend(DocumentSupport(issuer, row["source_type"], published) for issuer in row["issuer_ids"])
    _need(bool(support), "document_coverage", scenario)
    issuers, kinds = (
        tuple(sorted({row.issuer_id for row in support})),
        tuple(sorted({row.source_kind for row in support})),
    )
    _need(
        issuers == tuple(observed.get("issuers", ()))
        and kinds == tuple(observed.get("source_kinds", ()))
        and len(docs) == observed.get("documents")
        and (
            min(row.published_at for row in support).isoformat().replace("+00:00", "Z"),
            max(row.published_at for row in support).isoformat().replace("+00:00", "Z"),
        )
        == (observed.get("published_start"), observed.get("published_end")),
        "document_coverage",
        scenario,
    )
    _need(
        chunks == semantic.get("eligible_document_ids")
        and len(chunks) == semantic.get("eligible_document_count") == len(set(chunks)),
        "semantic_document_identity",
        scenario,
    )
    fields = market.get("field_coverage", {})
    _need(
        isinstance(fields, dict)
        and all(
            isinstance(value, dict)
            and isinstance(value.get("present_fields"), list)
            and set(value["present_fields"]) <= _FIELDS
            for value in fields.values()
        ),
        "field_coverage",
        scenario,
    )
    known = (
        set(universe.get("targets", ()))
        | set(universe.get("peers", ()))
        | set(universe.get("required_benchmarks", ()))
        | set(universe.get("optional_instruments", ()))
    )
    required = (
        set(universe.get("targets", ()))
        | set(universe.get("peers", ()))
        | set(universe.get("required_benchmarks", ()))
    )
    _need(
        required <= set(fields) <= known
        and all(
            fields[name].get("rows") == len(sessions)
            and fields[name].get("start") == sessions[0].session_date.isoformat()
            and fields[name].get("end_inclusive") == sessions[-1].session_date.isoformat()
            for name in required
        ),
        "field_coverage",
        scenario,
    )
    dates = {row.session_date.isoformat() for row in sessions}
    all_fields = {field for value in fields.values() for field in value["present_fields"]}
    for row in market_rows:
        _need(
            set(row["tickers"]) <= set(universe["targets"])
            and set(row["required_instruments"]) <= known
            and set(row["optional_instruments"]) <= known
            and set(row["required_fields"]) <= all_fields
            and (row["resolved_completed_session"] is None or row["resolved_completed_session"] in dates)
            and (
                row["status"] != "ready"
                or set(row["tickers"]) <= set(row["required_instruments"])
                and all(
                    set(row["required_fields"]) <= set(fields[name]["present_fields"])
                    for name in row["tickers"]
                )
                and all(
                    "adjusted_close" in fields[name]["present_fields"]
                    for name in set(row["required_instruments"]) - set(row["tickers"])
                )
            ),
            "market_readiness_support",
            row["case_id"],
        )
    by_id = {row["document_id"]: row for row in docs}
    for row in document_rows:
        _need(
            _document_readiness_supported(row, by_id, issuers, kinds),
            "document_readiness_support",
            row["case_id"],
        )
    policy = tuple(
        (name, tuple(value.get("required", ())), tuple(value.get("optional", ())))
        for name, value in sorted(market.get("benchmark_policy", {}).items())
    )
    _need(
        {item for _, required_items, _ in policy for item in required_items}
        == set(universe["required_benchmarks"])
        and {item for _, _, optional_items in policy for item in optional_items}
        <= set(universe["optional_instruments"]),
        "benchmark_policy",
        scenario,
    )
    readiness = tuple(
        CaseReadiness(
            row["case_id"],
            row["status"],
            paired[row["case_id"]]["status"],
            row["required_interval"],
            _utc(paired[row["case_id"]]["cutoff"], "document_cutoff")
            if paired[row["case_id"]]["cutoff"]
            else None,
        )
        for row in market_rows
    )
    limitations = tuple(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for row in coverage.get("optional_gaps", []) + coverage.get("document_gaps", [])
        if isinstance(row, dict)
    )
    return CoverageCatalog(
        scenario,
        _sha(manifest_path),
        gate,
        tier,
        vintage,
        manifest["cutoff_policy"],
        "phase09_v2",
        market["snapshot_id"],
        identity["market_manifest_sha256"],
        documents["snapshot_id"],
        identity["document_manifest_sha256"],
        identity["market_readiness_sha256"],
        identity["document_readiness_sha256"],
        manifest["readiness"]["market"]["bank_sha256"],
        tuple(coverage["targets"]),
        tuple(universe.get("peers", ())),
        tuple(coverage["required_benchmarks"]),
        tuple(universe.get("optional_instruments", ())),
        sessions,
        tuple((name, tuple(sorted(value["present_fields"]))) for name, value in sorted(fields.items())),
        policy,
        issuers,
        kinds,
        tuple(sorted(support, key=lambda row: (row.issuer_id, row.source_kind, row.published_at))),
        readiness,
        limitations,
    )
