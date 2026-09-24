"""Validate and load the immutable qualified scenario-v2 bundle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from collections.abc import Callable

from .market_store import (
    EMBED_ID,
    EMBED_REV,
    MarketStore,
    MarketStoreError,
    Reader,
    child,
    digest,
    load_json,
    validate_derived,
    validate_semantic,
    validate_tables,
)

ROOT_KEYS = {
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
MARKET_KEYS = {
    "schema_version",
    "snapshot_kind",
    "snapshot_id",
    "normalizer_version",
    "gate",
    "data_tier",
    "vintage_status",
    "created_at",
    "captured_at",
    "requested_window",
    "observed_coverage",
    "calendar",
    "timezone",
    "universe",
    "benchmark_policy",
    "field_coverage",
    "universe_sha256",
    "actions_sha256",
    "sources",
    "artifacts",
    "quality",
}
DOCUMENT_KEYS = {
    "schema_version",
    "snapshot_kind",
    "snapshot_id",
    "normalizer_version",
    "gate",
    "data_tier",
    "vintage_status",
    "created_at",
    "captured_at",
    "cutoff_policy",
    "issuers",
    "corpus_sha256",
    "requirements",
    "observed_coverage",
    "sources",
    "artifacts",
    "gaps",
}
MARKET_READY_KEYS = {
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
DOC_READY_KEYS = {
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
ROOT_REQUIRED = {
    "processed/documents.jsonl",
    "processed/graph.json",
    "processed/analogue_features.json",
    "processed/risk-training.json",
    "processed/projection_inputs.json",
    "processed/session-index.json",
    "indexes/embedding_ids.json",
    "indexes/embeddings.f32",
    "indexes/cuvs-brute-force.bin",
    "indexes/cuvs-index.json",
    "models/risk-model.json",
    "models/risk-model.ubj",
    "models/risk-model.receipt.json",
    "readiness/market-cases.jsonl",
    "readiness/document-cases.jsonl",
}
TableReader = Callable[[Path, list[str] | None], list[dict[str, Any]]]
RecordCounter = Callable[[Path], int]


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fail(code: str, detail: str) -> None:
    raise MarketStoreError(code, detail)


def _exact(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(code, "unknown or missing fields")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _content_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{hashlib.sha256(_canonical(value)).hexdigest()[:16]}"


def _utc(value: Any, field: str) -> None:
    try:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise MarketStoreError("invalid_utc_time", field) from exc


def _jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        rows = tuple(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketStoreError("artifact_unreadable", path.name) from exc
    if any(not isinstance(row, dict) for row in rows):
        _fail("artifact_shape", path.name)
    return rows


def _parquet_records(path: Path) -> int:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise MarketStoreError("parquet_metadata_unavailable", path.name) from exc
    try:
        return pq.ParquetFile(path).metadata.num_rows
    except Exception as exc:
        raise MarketStoreError("parquet_unreadable", path.name) from exc


def _gpu_table(path: Path, columns: list[str] | None) -> list[dict[str, Any]]:
    try:
        import cudf
        import cupy as cp
    except ImportError as exc:
        raise MarketStoreError("gpu_reader_unavailable", "metadata reader") from exc
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            _fail("gpu_reader_unavailable", "metadata device")
        return cudf.read_parquet(str(path), columns=columns).to_arrow().to_pylist()
    except MarketStoreError:
        raise
    except Exception as exc:
        raise MarketStoreError("parquet_gpu_read_failed", path.name) from exc


def _records(path: Path, relative: str, parquet: RecordCounter) -> int:
    if path.suffix == ".parquet":
        return parquet(path)
    if path.suffix == ".jsonl":
        return len(_jsonl(path))
    if relative == "indexes/embedding_ids.json":
        value = json.loads(path.read_text(encoding="utf-8"))
        return len(value) if isinstance(value, list) else -1
    if relative == "processed/session-index.json":
        return len(load_json(path).get("sessions", []))
    if relative == "processed/graph.json":
        return len(load_json(path).get("edges", []))
    if relative in {"processed/analogue_features.json", "processed/risk-training.json"}:
        return len(load_json(path).get("rows", []))
    if relative == "processed/projection_inputs.json":
        return len(load_json(path).get("documents", []))
    if path.suffix == ".f32":
        return path.stat().st_size // (2048 * 4) if path.stat().st_size % (2048 * 4) == 0 else -1
    return 1


def _artifacts(root: Path, manifest: dict[str, Any], *, top: bool, parquet: RecordCounter) -> set[str]:
    expected = (
        {"path", "sha256", "bytes", "media_type", "records", "source_ids"}
        if top
        else {"path", "sha256", "bytes", "media_type", "records"}
    )
    values = manifest.get("artifacts")
    if not isinstance(values, list) or not values:
        _fail("artifact_manifest", root.name)
    seen: set[str] = set()
    for item in values:
        _exact(item, expected, "artifact_shape")
        relative = item["path"]
        if relative in seen:
            _fail("duplicate_artifact", relative)
        seen.add(relative)
        path = child(root, relative)
        if top and Path(relative).parts[0] not in {
            "raw",
            "processed",
            "indexes",
            "models",
            "reports",
            "logs",
            "market",
            "documents",
            "readiness",
        }:
            _fail("artifact_path", relative)
        if not path.is_file():
            _fail("artifact_missing", relative)
        if path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            _fail("artifact_digest", relative)
        if _records(path, relative, parquet) != item["records"]:
            _fail("artifact_records", relative)
    return seen


def _source(root: Path, name: str, source: dict[str, Any], nested: dict[str, Any], gate: str) -> None:
    base = root if name == "market" else child(root, source["path"])
    relative = (
        f"lineage/{source['source_id']}-{source['capture_id']}.json" if name == "market" else "capture.json"
    )
    capture = load_json(child(base, relative))
    expected_digest = source["capture_sha256"] if name == "market" else source["manifest_sha256"]
    if (
        digest(child(base, relative)) != expected_digest
        or capture.get("capture_id") != source["capture_id"]
        or (name == "market" and capture.get("source_id") != source["source_id"])
        or (
            name == "documents"
            and (
                capture.get("adapter") != source["adapter"]
                or capture.get("captured_at") != source["captured_at"]
                or capture.get("vintage_status") != source["vintage_status"]
            )
        )
    ):
        _fail("source_lineage_drift", name)
    proof = source.get("archive_proof")
    if gate != "release":
        if proof is not None or source.get("vintage_status") != "reconstructed_later":
            _fail("source_vintage_drift", name)
        return
    proof = _exact(
        proof,
        {"path", "sha256", "bytes", "canonical_url", "proved_at", "coverage_end_exclusive"},
        "archive_proof_shape",
    )
    _utc(proof["proved_at"], "archive_proof.proved_at")
    path = child(base, proof["path"])
    valid_end = (
        name != "market" or proof["coverage_end_exclusive"] >= nested["requested_window"]["end_exclusive"]
    )
    if (
        not path.is_file()
        or path.stat().st_size != proof["bytes"]
        or digest(path) != proof["sha256"]
        or not proof["canonical_url"].startswith("https://")
        or parse_time(proof["proved_at"]) > parse_time(source["captured_at"])
        or not valid_end
    ):
        _fail("archive_proof_invalid", name)


def _nested(
    root: Path, manifest: dict[str, Any], name: str, parquet: RecordCounter
) -> tuple[Path, dict[str, Any]]:
    binding = _exact(manifest.get(name), {"path", "snapshot_id", "manifest_sha256"}, "nested_binding_shape")
    nested_root = child(root, binding["path"])
    path = nested_root / "manifest.json"
    if not path.is_file() or digest(path) != binding["manifest_sha256"]:
        _fail("nested_manifest_digest", name)
    value = load_json(path)
    _exact(value, MARKET_KEYS if name == "market" else DOCUMENT_KEYS, "nested_manifest_shape")
    gate = "reconstruction" if manifest["data_tier"] == "cc0_reconstruction" else "release"
    tier = (
        ("cc0_reconstruction" if name == "market" else "authoritative_reconstruction")
        if gate == "reconstruction"
        else "entitled_local"
    )
    if (
        value.get("schema_version") != 2
        or value.get("snapshot_kind") != name
        or value.get("normalizer_version") != 2
        or value.get("gate") != gate
    ):
        _fail("nested_contract", name)
    expected_vintage = "reconstructed_later" if gate == "reconstruction" else "archived_at_cutoff"
    if value.get("data_tier") != tier or value.get("vintage_status") != expected_vintage:
        _fail("nested_vintage", name)
    _utc(value.get("created_at"), f"{name}.created_at")
    _utc(value.get("captured_at"), f"{name}.captured_at")
    for source in value.get("sources", []):
        _source(nested_root, name, source, value, gate)
        if gate == "release":
            allowed = (
                {"allowed", "local_only"} if name == "market" else {"allowed", "metadata_only", "local_only"}
            )
            if (
                source.get("vintage_status") != "archived_at_cutoff"
                or source.get("redistribution") not in allowed
                or not source.get("archive_proof")
                or (
                    name == "documents"
                    and (source.get("adapter") != "local_news_metadata" or not source.get("license_id"))
                )
            ):
                _fail("release_source_unproved", name)
    paths = _artifacts(nested_root, value, top=False, parquet=parquet)
    if name == "market":
        required = {"sessions.parquet", "coverage.parquet", "actions.parquet", "instruments.parquet"}
        bars = {
            p.relative_to(nested_root).as_posix()
            for p in nested_root.glob("bars/interval=1d/year=*/part-*.parquet")
        }
        expected_id = _content_id(
            "market",
            {
                "normalizer_version": value["normalizer_version"],
                "captures": sorted((s.get("capture_id"), s.get("capture_sha256")) for s in value["sources"]),
                "window": value["requested_window"],
                "universe_sha256": value["universe_sha256"],
                "actions_sha256": value["actions_sha256"],
                "gate": gate,
                "captured_at": value["captured_at"],
            },
        )
        if (
            not bars
            or not required | bars <= paths
            or paths & {p for p in paths if p.startswith("bars/")} != bars
        ):
            _fail("market_artifact_declaration", "daily tables")
    else:
        expected_id = _content_id(
            "documents",
            {
                "normalizer_version": value["normalizer_version"],
                "captures": sorted((s.get("capture_id"), s.get("manifest_sha256")) for s in value["sources"]),
                "corpus_sha256": value["corpus_sha256"],
                "gate": gate,
                "captured_at": value["captured_at"],
            },
        )
        if not {"documents.parquet", "coverage.parquet"} <= paths:
            _fail("document_artifact_declaration", "canonical tables")
    if value["snapshot_id"] != binding["snapshot_id"] or value["snapshot_id"] != expected_id:
        _fail("nested_snapshot_identity", name)
    return nested_root, value


def _readiness(root: Path, manifest: dict[str, Any], name: str) -> tuple[dict[str, Any], ...]:
    item = _exact(
        manifest["readiness"].get(name),
        {"path", "sha256", "records", "snapshot_id", "bank_sha256", "contracts_sha256"},
        "readiness_shape",
    )
    path = child(root, item["path"])
    if not path.is_file() or digest(path) != item["sha256"]:
        _fail("readiness_digest", name)
    rows = _jsonl(path)
    keys = MARKET_READY_KEYS if name == "market" else DOC_READY_KEYS
    if len(rows) != 250 or item["records"] != 250 or len({r.get("case_id") for r in rows}) != 250:
        _fail("readiness_count", name)
    bank: set[str] = set()
    contracts: list[tuple[str, str]] = []
    nested = manifest[name]
    missing_key = "missing_items" if name == "market" else "missing_requirements"
    for row in rows:
        _exact(row, keys, "readiness_row_shape")
        status = row["status"]
        missing = row[missing_key]
        snapshot_key = "snapshot_id" if name == "market" else "document_snapshot_id"
        digest_key = "snapshot_manifest_sha256" if name == "market" else "document_manifest_sha256"
        if (
            row[snapshot_key] != nested["snapshot_id"]
            or row[digest_key] != nested["manifest_sha256"]
            or status
            not in {"ready", "not_applicable", "expected_missing", "needs_scope_resolution", "blocked"}
            or not isinstance(missing, list)
        ):
            _fail("readiness_row_binding", f"{name}:{row['case_id']}")
        blocking = (
            missing
            if name == "documents"
            else [
                x
                for x in missing
                if x.get("code") not in {"optional_instrument_unavailable", "subdaily_chronology_unavailable"}
            ]
        )
        if status == "ready" and blocking:
            _fail("readiness_false_ready", f"{name}:{row['case_id']}")
        if status in {"expected_missing", "needs_scope_resolution", "blocked"} and not missing:
            _fail("readiness_missing_reason", f"{name}:{row['case_id']}")
        if name == "market" and (
            (
                status == "not_applicable"
                and any(
                    (
                        row["tickers"],
                        row["required_instruments"],
                        row["optional_instruments"],
                        row["required_fields"],
                        row["required_interval"] is not None,
                        row["requires_actions"],
                    )
                )
            )
            or (status != "not_applicable" and row["required_interval"] != "1d")
        ):
            _fail("readiness_market_contract", row["case_id"])
        bank.add(row["bank_sha256"])
        contracts.append((row["case_id"], row["contract_sha256"]))
    aggregate = hashlib.sha256(_canonical(sorted(contracts))).hexdigest()
    if (
        bank != {item["bank_sha256"]}
        or aggregate != item["contracts_sha256"]
        or len({x[1] for x in contracts}) != 250
    ):
        _fail("readiness_aggregate_binding", name)
    return rows


def validate_scenario(
    root: Path, *, table_reader: TableReader | None = None, record_counter: RecordCounter | None = None
) -> dict[str, Any]:
    """Return verified metadata or one stable fail-closed error."""
    try:
        root = root.resolve()
        manifest = load_json(root / "manifest.json")
        _exact(manifest, ROOT_KEYS, "manifest_shape")
        if (
            manifest["schema_version"] != 2
            or manifest["recipe_version"] != 3
            or manifest["snapshot_id"] != manifest["scenario_id"]
            or root.name != manifest["scenario_id"]
            or manifest["data_tier"] not in {"cc0_reconstruction", "entitled_local"}
            or manifest["vintage_status"]
            != (
                "reconstructed_later"
                if manifest["data_tier"] == "cc0_reconstruction"
                else "archived_at_cutoff"
            )
            or manifest["cutoff_policy"] != "observation_or_published_at_lte_case_cutoff"
        ):
            _fail("scenario_contract", "identity/tier/cutoff")
        _utc(manifest["created_at"], "created_at")
        parquet = record_counter or _parquet_records
        paths = _artifacts(root, manifest, top=True, parquet=parquet)
        if not ROOT_REQUIRED <= paths:
            _fail("artifact_omission", ",".join(sorted(ROOT_REQUIRED - paths)))
        market_root, market = _nested(root, manifest, "market", parquet)
        document_root, documents = _nested(root, manifest, "documents", parquet)
        rows, nested_docs = validate_tables(
            root, manifest, market_root, market, document_root, documents, table_reader or _gpu_table
        )
        market_ready = _readiness(root, manifest, "market")
        document_ready = _readiness(root, manifest, "documents")
        if {r["case_id"] for r in market_ready} != {r["case_id"] for r in document_ready} or {
            r["bank_sha256"] for r in market_ready
        } != {r["bank_sha256"] for r in document_ready}:
            _fail("readiness_cross_binding", "market/documents")
        normalized = _jsonl(root / "processed/documents.jsonl")
        validate_derived(root, manifest, normalized)
        validate_semantic(root, manifest, normalized, nested_docs)
        evidence = {r["document_id"] for r in normalized}
        if any(not set(r["matched_evidence_ids"]) <= evidence for r in document_ready):
            _fail("readiness_evidence_binding", "documents")
        identity = {
            "recipe_version": manifest["recipe_version"],
            "created_at": manifest["created_at"],
            "market_manifest_sha256": manifest["market"]["manifest_sha256"],
            "document_manifest_sha256": manifest["documents"]["manifest_sha256"],
            "market_readiness_sha256": manifest["readiness"]["market"]["sha256"],
            "document_readiness_sha256": manifest["readiness"]["documents"]["sha256"],
            "embedding_model": f"{EMBED_ID}@{EMBED_REV}",
        }
        if manifest["scenario_id"] != _content_id("market-shock-v2", identity):
            _fail("scenario_identity", manifest["scenario_id"])
        return {"manifest": manifest, "market": market, "documents": documents, "sessions": rows}
    except MarketStoreError:
        raise
    except Exception as exc:
        raise MarketStoreError("artifact_contract", type(exc).__name__) from exc


@dataclass(frozen=True)
class ArtifactBundle:
    documents: tuple[dict, ...]
    relations: tuple[dict, ...]
    manifest_sha256: str
    root: Path
    store: MarketStore

    def eligible_documents(self, ticker: str, as_of: datetime) -> list[dict]:
        seen: set[tuple[str, str]] = set()
        rows = []
        for row in sorted(self.documents, key=lambda item: (item["available_at"], item["source_id"])):
            key = (row["source_id"], row.get("revision", "1"))
            if (
                ticker in row.get("issuer_ids", [])
                and parse_time(row["available_at"]) <= as_of
                and key not in seen
            ):
                seen.add(key)
                rows.append(row)
        return rows

    def health(self) -> dict:
        return self.store.health()


def load_bundle(
    root: Path,
    *,
    reader: Reader | None = None,
    reader_is_gpu: bool = True,
    strict_gpu: bool = True,
    table_reader: TableReader | None = None,
    record_counter: RecordCounter | None = None,
) -> ArtifactBundle:
    candidate = root.resolve()
    if not (candidate / "manifest.json").is_file():
        candidate = (candidate / "scenario").resolve()
    validated = validate_scenario(candidate, table_reader=table_reader, record_counter=record_counter)
    store = MarketStore(
        candidate, reader=reader, reader_is_gpu=reader_is_gpu, strict_gpu=strict_gpu, _validated=validated
    )
    documents = _jsonl(candidate / "processed/documents.jsonl")
    graph = load_json(candidate / "processed/graph.json")
    if not documents:
        _fail("document_corpus_empty", "scenario-v2")
    return ArtifactBundle(
        documents=documents,
        relations=tuple(graph["edges"]),
        manifest_sha256=store.manifest_sha256,
        root=candidate,
        store=store,
    )
