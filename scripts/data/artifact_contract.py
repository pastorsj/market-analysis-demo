"""Fail-closed validation for immutable prepared bundles; never repairs data."""

from __future__ import annotations

import ast
from array import array
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from typing import Any, Iterable

EMBED_MODEL = "nvidia/Nemotron-3-Embed-1B-BF16"
EMBED_REVISION = "9e0b24858b1195815ecb1188ffa1b73bcea7b30a"
ALLOWED_ROOTS = frozenset({"raw", "processed", "indexes", "models", "reports", "logs"})
V2_ROOTS = ALLOWED_ROOTS | frozenset({"market", "documents", "readiness"})
MANIFEST_KEYS = frozenset({"schema_version", "scenario_id", "snapshot_id", "fixture_tier", "created_at", "supported_universe", "date_coverage", "embedding", "artifacts"})
EMBED_KEYS = frozenset({"model_id", "revision", "tokenizer_revision", "dimension", "model_dtype", "storage_dtype", "query_role", "passage_role", "normalized", "max_tokens", "implementation"})
ARTIFACT_KEYS = frozenset({"path", "sha256", "bytes", "media_type", "records", "source_ids"})
SEMANTIC_RECEIPT_KEYS = frozenset({"engine", "device", "gpu_executed", "fallback_used", "model_id", "revision", "dimension"})
SEMANTIC_INDEX_KEYS = frozenset({"kind", "metric", "dimension", "normalized", "rows", "fixture_only", "model_id", "revision", "index_path", "index_sha256", "attention", "gpu_executed", "device", "scenario_input_sha256", "market_manifest_sha256", "document_manifest_sha256", "risk_model_metadata_sha256", "eligible_document_ids_sha256"})


@dataclass(frozen=True)
class ArtifactIssue:
    code: str
    detail: str


class ArtifactValidationError(ValueError):
    def __init__(self, issues: Iterable[ArtifactIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{i.code}: {i.detail}" for i in self.issues))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def semantic_index_valid(semantic: Any, index: Any, identifiers: list[str], *, risk_sha256: str) -> bool:
    """Return whether a ready semantic bundle is the exact pinned Nemotron/cuVS contract."""
    if not isinstance(semantic, dict) or not isinstance(index, dict) or set(index) != SEMANTIC_INDEX_KEYS:
        return False
    receipt = semantic.get("gpu_receipt")
    if not isinstance(receipt, dict) or set(receipt) != SEMANTIC_RECEIPT_KEYS:
        return False
    ids_sha = hashlib.sha256(json.dumps(identifiers, separators=(",", ":")).encode()).hexdigest()
    expected = {"kind": "cuvs_brute_force", "metric": "inner_product", "dimension": 2048,
                "normalized": True, "rows": len(identifiers), "fixture_only": False,
                "model_id": EMBED_MODEL, "revision": EMBED_REVISION,
                "index_path": "indexes/cuvs-brute-force.bin", "index_sha256": semantic.get("index_sha256"),
                "gpu_executed": True, "scenario_input_sha256": semantic.get("scenario_input_sha256"),
                "market_manifest_sha256": semantic.get("market_manifest_sha256"),
                "document_manifest_sha256": semantic.get("document_manifest_sha256"),
                "risk_model_metadata_sha256": risk_sha256, "eligible_document_ids_sha256": ids_sha}
    receipt_expected = {"engine": "cuvs", "device": receipt.get("device"), "gpu_executed": True,
                        "fallback_used": False, "model_id": EMBED_MODEL,
                        "revision": EMBED_REVISION, "dimension": 2048}
    exact_types = (type(receipt.get("gpu_executed")) is bool and type(receipt.get("fallback_used")) is bool
                   and type(receipt.get("dimension")) is int and type(index.get("dimension")) is int
                   and type(index.get("rows")) is int and all(type(index.get(key)) is bool for key in ("normalized", "fixture_only", "gpu_executed")))
    return (exact_types and receipt == receipt_expected and receipt["device"] == "NVIDIA GB10"
            and index.get("device") == receipt["device"] and index.get("attention") in {"flash_attention_2", "sdpa"}
            and all(index.get(key) == value for key, value in expected.items()))


def _npy_sections(value: bytes) -> list[tuple[str, tuple[int, ...], bytes]] | None:
    """Parse the closed NPY subset written by pinned cuVS without importing NumPy."""
    sections: list[tuple[str, tuple[int, ...], bytes]] = []
    offset = 4
    sizes = {"<i4": 4, "<u8": 8, "<f4": 4, "|u1": 1}
    try:
        while offset < len(value):
            if value[offset:offset + 6] != b"\x93NUMPY": return None
            major, minor = value[offset + 6:offset + 8]
            width = 2 if major == 1 else 4 if major in {2, 3} else 0
            if not width or minor != 0: return None
            header_size = int.from_bytes(value[offset + 8:offset + 8 + width], "little")
            header_start = offset + 8 + width; header_end = header_start + header_size
            header = ast.literal_eval(value[header_start:header_end].decode("latin1").strip())
            if set(header) != {"descr", "fortran_order", "shape"} or header["fortran_order"] is not False:
                return None
            dtype, shape = header["descr"], header["shape"]
            if dtype not in sizes or not isinstance(shape, tuple) or any(type(item) is not int or item < 0 for item in shape):
                return None
            count = math.prod(shape) if shape else 1; data_end = header_end + count * sizes[dtype]
            if data_end > len(value): return None
            sections.append((dtype, shape, value[header_end:data_end])); offset = data_end
    except (SyntaxError, ValueError, UnicodeError, TypeError, IndexError):
        return None
    return sections if offset == len(value) else None


def _semantic_bytes_valid(vectors: bytes, binary: bytes, rows: int) -> bool:
    values = array("f"); values.frombytes(vectors)
    if sys.byteorder != "little": values.byteswap()
    if len(values) != rows * 2048 or any(not math.isfinite(value) for value in values): return False
    if any(abs(math.sqrt(math.fsum(value * value for value in values[start:start + 2048])) - 1.0) > 2e-3
           for start in range(0, len(values), 2048)): return False
    sections = _npy_sections(binary)
    expected = [("<i4", ()), ("<u8", ()), ("<u8", ()), ("<i4", ()),
                ("<f4", ()), ("|u1", ()), ("<f4", (rows, 2048)), ("|u1", ())]
    if binary[:4] != b"<f4\0" or sections is None or [(kind, shape) for kind, shape, _ in sections] != expected:
        return False
    formats = {"<i4": "<i", "<u8": "<Q", "<f4": "<f", "|u1": "<B"}
    scalars = [struct.unpack(formats[kind], data)[0] for kind, shape, data in sections if not shape]
    return scalars == [0, rows, 2048, 6, 2.0, 1, 0] and sections[6][2] == vectors


def semantic_artifacts_valid(artifacts: Any, eligible_count: Any, *, root: Path) -> bool:
    """Validate exact vector contents and the pinned cuVS brute-force wire format."""
    if not isinstance(artifacts, list) or type(eligible_count) is not int or eligible_count <= 0:
        return False
    selected = {path: [row for row in artifacts if isinstance(row, dict) and row.get("path") == path] for path in (
        "indexes/embedding_ids.json", "indexes/embeddings.f32", "indexes/cuvs-brute-force.bin", "indexes/cuvs-index.json",
    )}
    if any(len(rows) != 1 for rows in selected.values()):
        return False
    ids, vectors, binary, metadata = (selected[path][0] for path in selected)
    metadata_valid = (type(ids.get("records")) is int and ids["records"] == eligible_count
            and type(vectors.get("records")) is int and vectors["records"] == eligible_count
            and type(vectors.get("bytes")) is int and vectors["bytes"] == eligible_count * 2048 * 4
            and type(binary.get("records")) is int and binary["records"] == 1
            and type(binary.get("bytes")) is int and binary["bytes"] > 0
            and type(metadata.get("records")) is int and metadata["records"] == 1)
    if not metadata_valid: return False
    try:
        resolved = root.resolve(); vector_path = (resolved / "indexes/embeddings.f32").resolve()
        binary_path = (resolved / "indexes/cuvs-brute-force.bin").resolve()
        if resolved not in vector_path.parents or resolved not in binary_path.parents: return False
        vector_bytes, binary_bytes = vector_path.read_bytes(), binary_path.read_bytes()
    except OSError:
        return False
    return len(vector_bytes) == vectors["bytes"] and len(binary_bytes) == binary["bytes"] \
        and _semantic_bytes_valid(vector_bytes, binary_bytes, eligible_count)


def _utc(value: Any, field: str, issues: list[ArtifactIssue]) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        issues.append(ArtifactIssue("invalid_utc_time", field)); return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        issues.append(ArtifactIssue("invalid_utc_time", field)); return None


def validate_source_manifest(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != {"version", "sources"} or payload.get("version") != 1:
        raise ArtifactValidationError([ArtifactIssue("invalid_source_manifest", "expected version 1 and sources only")])
    issues: list[ArtifactIssue] = []
    sources, seen = payload.get("sources"), set()
    required = {"id", "kind", "provider", "canonical_url", "license_id", "redistribution", "content_sha256", "retrieved_at"}
    allowed = required | {"published_at", "notes"}
    if not isinstance(sources, list) or not sources:
        raise ArtifactValidationError([ArtifactIssue("invalid_source_manifest", "sources must be non-empty")])
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or not required.issubset(source) or not set(source).issubset(allowed):
            issues.append(ArtifactIssue("invalid_source", str(index))); continue
        source_id, digest = source["id"], source["content_sha256"]
        if source_id in seen: issues.append(ArtifactIssue("duplicate_source", source_id))
        seen.add(source_id)
        if not str(source["canonical_url"]).startswith("https://"): issues.append(ArtifactIssue("unsafe_source_url", source_id))
        if source["redistribution"] not in {"allowed", "metadata_only", "local_only", "blocked"}: issues.append(ArtifactIssue("invalid_redistribution", source_id))
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest): issues.append(ArtifactIssue("invalid_source_digest", source_id))
        retrieved = _utc(source["retrieved_at"], f"{source_id}.retrieved_at", issues)
        published = _utc(source.get("published_at"), f"{source_id}.published_at", issues) if source.get("published_at") else None
        if retrieved and published and retrieved < published: issues.append(ArtifactIssue("time_inversion", source_id))
    if issues: raise ArtifactValidationError(issues)


def load_manifest(
    path: Path, *, root: Path | None = None, allow_test_embeddings: bool = False,
    allow_pending_semantic: bool = False,
) -> dict[str, Any]:
    path, issues = path.resolve(), []
    bundle_root = (root or path.parent).resolve()
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ArtifactValidationError([ArtifactIssue("manifest_unreadable", str(exc))]) from exc
    if isinstance(payload, dict) and type(payload.get("schema_version")) is int and payload.get("schema_version") == 2:
        return _load_v2(
            path, bundle_root, payload, allow_test_embeddings=allow_test_embeddings,
            allow_pending_semantic=allow_pending_semantic,
        )
    if not allow_test_embeddings:
        raise ArtifactValidationError([
            ArtifactIssue("legacy_fixture_forbidden", "production requires scenario schema version 2")
        ])
    if not isinstance(payload, dict) or set(payload) != MANIFEST_KEYS: issues.append(ArtifactIssue("manifest_shape", "unknown or missing fields"))
    if payload.get("schema_version") != 1 or payload.get("scenario_id") != "deepseek-nvda-v1" or payload.get("snapshot_id") != "market-shock-corpus-v1": issues.append(ArtifactIssue("manifest_identity", "unexpected identity"))
    _utc(payload.get("created_at"), "created_at", issues)
    coverage = payload.get("date_coverage", {})
    if not isinstance(coverage, dict) or set(coverage) != {"start", "end", "timezone", "calendar"} or coverage.get("timezone") != "America/New_York" or coverage.get("calendar") != "XNYS": issues.append(ArtifactIssue("coverage_contract", "calendar mismatch"))
    embedding = payload.get("embedding", {})
    if not isinstance(embedding, dict) or set(embedding) != EMBED_KEYS: issues.append(ArtifactIssue("embedding_shape", "unknown or missing fields"))
    else:
        expected = {"model_id": EMBED_MODEL, "revision": EMBED_REVISION, "tokenizer_revision": EMBED_REVISION, "dimension": 2048, "model_dtype": "bfloat16", "storage_dtype": "float32", "query_role": "query", "passage_role": "passage", "normalized": True, "max_tokens": 4096}
        for key, value in expected.items():
            if embedding.get(key) != value: issues.append(ArtifactIssue("embedding_contract_drift", key))
        if embedding.get("implementation") != "pinned_model" and not (allow_test_embeddings and embedding.get("implementation") == "deterministic_fake_test_only" and payload.get("fixture_tier") == "ci"): issues.append(ArtifactIssue("test_embedding_forbidden", str(embedding.get("implementation"))))
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) < 6: issues.append(ArtifactIssue("artifact_count", "at least six required")); artifacts = []
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != ARTIFACT_KEYS: issues.append(ArtifactIssue("artifact_shape", repr(item))); continue
        rel = Path(item["path"])
        if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] not in ALLOWED_ROOTS: issues.append(ArtifactIssue("artifact_path", item["path"])); continue
        if item["path"] in seen: issues.append(ArtifactIssue("duplicate_artifact", item["path"]))
        seen.add(item["path"]); candidate = (bundle_root / rel).resolve()
        if bundle_root not in candidate.parents: issues.append(ArtifactIssue("artifact_escape", item["path"]))
        elif not candidate.is_file(): issues.append(ArtifactIssue("artifact_missing", item["path"]))
        else:
            if candidate.stat().st_size != item["bytes"]: issues.append(ArtifactIssue("artifact_size", item["path"]))
            if sha256_file(candidate) != item["sha256"]: issues.append(ArtifactIssue("artifact_digest", item["path"]))
    if issues: raise ArtifactValidationError(issues)
    return payload


def _safe_child(root: Path, relative: str, issues: list[ArtifactIssue]) -> Path | None:
    item = Path(relative)
    if item.is_absolute() or not item.parts or ".." in item.parts:
        issues.append(ArtifactIssue("artifact_path", relative)); return None
    candidate = (root / item).resolve()
    if root not in candidate.parents:
        issues.append(ArtifactIssue("artifact_escape", relative)); return None
    return candidate


READINESS_STATUSES = frozenset({
    "ready", "not_applicable", "expected_missing", "needs_scope_resolution", "blocked",
})
MARKET_READINESS_KEYS = frozenset({
    "case_id", "snapshot_id", "snapshot_manifest_sha256", "bank_sha256", "contract_sha256",
    "tickers", "requested_date", "resolved_completed_session", "lookback_sessions",
    "required_instruments", "optional_instruments", "required_fields", "required_interval", "required_price_basis",
    "requires_actions", "vintage_requirement", "status", "missing_items",
})
DOCUMENT_READINESS_KEYS = frozenset({
    "case_id", "document_snapshot_id", "document_manifest_sha256", "bank_sha256",
    "contract_sha256", "issuer_ids", "cutoff", "required_source_kinds",
    "matched_requirement_ids", "matched_evidence_ids", "status", "missing_requirements",
})


def _readiness_rows(
    name: str, path: Path, binding: dict[str, Any], nested_binding: dict[str, Any],
    issues: list[ArtifactIssue],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(ArtifactIssue("readiness_unreadable", f"{name}:{type(exc).__name__}"))
        return []
    expected_keys = MARKET_READINESS_KEYS if name == "market" else DOCUMENT_READINESS_KEYS
    missing_key = "missing_items" if name == "market" else "missing_requirements"
    snapshot_key = "snapshot_id" if name == "market" else "document_snapshot_id"
    manifest_key = "snapshot_manifest_sha256" if name == "market" else "document_manifest_sha256"
    seen: set[str] = set()
    bank_digests: set[str] = set()
    contract_digests: set[str] = set()
    for row in rows:
        case_id = str(row.get("case_id", ""))
        if set(row) != expected_keys:
            issues.append(ArtifactIssue("readiness_row_shape", f"{name}:{case_id}"))
        if case_id in seen or not case_id:
            issues.append(ArtifactIssue("readiness_case_identity", f"{name}:{case_id}"))
        seen.add(case_id)
        if row.get(snapshot_key) != binding.get("snapshot_id") or row.get(manifest_key) != nested_binding.get("manifest_sha256"):
            issues.append(ArtifactIssue("readiness_input_binding", f"{name}:{case_id}"))
        status = row.get("status")
        missing = row.get(missing_key)
        if status not in READINESS_STATUSES or not isinstance(missing, list):
            issues.append(ArtifactIssue("readiness_status", f"{name}:{case_id}"))
            continue
        if name == "market" and status == "not_applicable" and any((
            row.get("tickers"), row.get("required_instruments"), row.get("optional_instruments"),
            row.get("required_fields"), row.get("required_interval") is not None,
            row.get("requires_actions"),
        )):
            issues.append(ArtifactIssue("readiness_false_not_applicable", f"{name}:{case_id}"))
        if name == "market" and status != "not_applicable" and row.get("required_interval") != "1d":
            issues.append(ArtifactIssue("readiness_interval", f"{name}:{case_id}"))
        blocking = missing
        if name == "market":
            blocking = [item for item in missing if item.get("code") not in {
                "optional_instrument_unavailable", "subdaily_chronology_unavailable",
            }]
        if status == "ready" and blocking:
            issues.append(ArtifactIssue("readiness_false_ready", f"{name}:{case_id}"))
        if status in {"expected_missing", "needs_scope_resolution", "blocked"} and not missing:
            issues.append(ArtifactIssue("readiness_missing_reason", f"{name}:{case_id}"))
        if status == "needs_scope_resolution" and not any(
            "unresolved" in str(item.get("code", "")) for item in missing
        ):
            issues.append(ArtifactIssue("readiness_scope_reason", f"{name}:{case_id}"))
        for key, target in (("bank_sha256", bank_digests), ("contract_sha256", contract_digests)):
            value = str(row.get(key, ""))
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                issues.append(ArtifactIssue("readiness_digest", f"{name}:{case_id}:{key}"))
            target.add(value)
    if len(seen) != 250 or len(bank_digests) != 1 or len(contract_digests) != 250:
        issues.append(ArtifactIssue(
            "readiness_set_integrity", f"{name}:cases={len(seen)}:banks={len(bank_digests)}:contracts={len(contract_digests)}",
        ))
    return rows


def _validate_derived(
    root: Path, payload: dict[str, Any], issues: list[ArtifactIssue], *, allow_pending: bool,
) -> None:
    binding = {
        "market_manifest_sha256": payload.get("market", {}).get("manifest_sha256"),
        "document_manifest_sha256": payload.get("documents", {}).get("manifest_sha256"),
        "market_readiness_sha256": payload.get("readiness", {}).get("market", {}).get("sha256"),
        "document_readiness_sha256": payload.get("readiness", {}).get("documents", {}).get("sha256"),
    }
    try:
        graph = json.loads((root / "processed/graph.json").read_text(encoding="utf-8"))
        analogue = json.loads((root / "processed/analogue_features.json").read_text(encoding="utf-8"))
        training = json.loads((root / "processed/risk-training.json").read_text(encoding="utf-8"))
        projection = json.loads((root / "processed/projection_inputs.json").read_text(encoding="utf-8"))
        risk_path = root / "models/risk-model.json"
        risk = json.loads(risk_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(ArtifactIssue("derived_artifact_unreadable", type(exc).__name__)); return
    for name, item in (("graph", graph), ("analogue", analogue), ("risk-training", training), ("projection", projection)):
        if item.get("schema_version") != 2 or item.get("input_binding") != binding:
            issues.append(ArtifactIssue("derived_input_binding", name))
    graph_sources = graph.get("provenance", {}).get("source_ids")
    if (
        graph.get("cutoff_policy") != "valid_from_lte_query_cutoff" or not isinstance(graph_sources, list)
        or any(not row.get("source_id") or not row.get("source_row_id")
               or row.get("input_source_ids") != graph_sources for row in graph.get("edges", []))
    ):
        issues.append(ArtifactIssue("derived_provenance", "graph"))
    analogue_rows = analogue.get("rows", [])
    if (
        analogue.get("cutoff_policy") != "feature_at_lt_query_cutoff" or not isinstance(analogue_rows, list)
        or any(not row.get("source_id") or not row.get("source_row_id") or not row.get("input_source_row_ids")
               or row.get("future_outcome_excluded") is not True
               or any(not math.isfinite(float(row[key])) for key in ("return_1d", "absolute_return_pct") if row.get(key) is not None)
               or row.get("volume_ratio") is not None and not math.isfinite(float(row["volume_ratio"]))
               for row in analogue_rows)
    ):
        issues.append(ArtifactIssue("derived_provenance", "analogue"))
    label_contract = {
        "name": "realized_volatility_5d", "units": "annualized_decimal_standard_deviation",
        "horizon_completed_sessions": 5, "annualization_sessions": 252,
    }
    training_rows, cutoff = training.get("rows", []), training.get("training_cutoff")
    if (
        training.get("cutoff_policy") != "label_available_at_lte_training_and_query_cutoff"
        or training.get("label_contract") != label_contract or not isinstance(training_rows, list)
        or any(row.get("feature_at", "") >= row.get("label_available_at", "")
               or row.get("label_available_at", "") > str(cutoff)
               or len(row.get("label_source_row_ids", [])) != 5 or not row.get("label_source_ids")
               or not math.isfinite(float(row.get("realized_volatility_5d", float("nan"))))
               for row in training_rows)
    ):
        issues.append(ArtifactIssue("risk_training_contract", "labels/cutoff/provenance"))
    normalized = [json.loads(line) for line in (root / "processed/documents.jsonl").read_text().splitlines() if line]
    expected_projection = [{
        "chunk_id": row["chunk_id"], "issuer_ids": row["issuer_ids"],
        "published_at": row["published_at"], "content_sha256": row["content_sha256"],
    } for row in normalized]
    if projection.get("cutoff_policy") != "published_at_lte_query_cutoff" or projection.get("documents") != expected_projection:
        issues.append(ArtifactIssue("projection_input_drift", "normalized documents"))
    expected_output = {
        "kind": "continuous_realized_volatility", "units": "annualized_decimal_standard_deviation",
        "horizon_completed_sessions": 5, "probability": False, "band_thresholds": None,
    }
    if (
        risk.get("schema_version") != 2 or risk.get("input_binding") != binding
        or risk.get("training_input") != "processed/risk-training.json"
        or risk.get("training_input_sha256") != sha256_file(root / "processed/risk-training.json")
        or risk.get("training_cutoff") != cutoff or risk.get("training_rows") != len(training_rows)
        or risk.get("output_contract") != expected_output
    ):
        issues.append(ArtifactIssue("risk_model_binding", "metadata")); return
    if risk.get("status") == "pending_gpu_training":
        if not allow_pending:
            issues.append(ArtifactIssue("risk_model_not_ready", "pending_gpu_training"))
        return
    if risk.get("model_path") != "risk-model.ubj" or risk.get("receipt_path") != "risk-model.receipt.json":
        issues.append(ArtifactIssue("risk_model_path", "model/receipt")); return
    model_path = root / "models/risk-model.ubj"
    receipt_path = root / "models/risk-model.receipt.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        receipt = {}
    if (
        risk.get("status") != "ready" or not model_path.is_file() or not receipt_path.is_file()
        or risk.get("model_sha256") != sha256_file(model_path) or risk.get("model_bytes") != model_path.stat().st_size
        or risk.get("receipt_sha256") != sha256_file(receipt_path)
        or receipt.get("input_binding") != binding or receipt.get("training_input_sha256") != risk.get("training_input_sha256")
        or receipt.get("training_rows") != len(training_rows) or receipt.get("training_cutoff") != cutoff
        or receipt.get("model_sha256") != risk.get("model_sha256") or receipt.get("model_bytes") != risk.get("model_bytes")
        or receipt.get("algorithm") != "xgboost.XGBRegressor" or not receipt.get("algorithm_version")
        or receipt.get("gpu_executed") is not True or receipt.get("fallback_used") is not False
    ):
        issues.append(ArtifactIssue("risk_model_receipt", "model/receipt binding"))


def _load_v2(
    path: Path,
    bundle_root: Path,
    payload: dict[str, Any],
    *,
    allow_test_embeddings: bool,
    allow_pending_semantic: bool,
) -> dict[str, Any]:
    issues: list[ArtifactIssue] = []
    expected_keys = {
        "schema_version", "scenario_id", "snapshot_id", "recipe_version", "data_tier", "vintage_status",
        "created_at", "cutoff_policy", "coverage", "market", "documents", "readiness",
        "embedding", "semantic", "artifacts",
    }
    if set(payload) != expected_keys:
        issues.append(ArtifactIssue("manifest_shape", "scenario-v2 unknown or missing fields"))
    if not str(payload.get("scenario_id", "")).startswith("market-shock-v2-"):
        issues.append(ArtifactIssue("manifest_identity", str(payload.get("scenario_id"))))
    if payload.get("snapshot_id") != payload.get("scenario_id"):
        issues.append(ArtifactIssue("manifest_identity", "scenario/snapshot mismatch"))
    if type(payload.get("recipe_version")) is not int or payload.get("recipe_version") != 3:
        issues.append(ArtifactIssue("recipe_version", str(payload.get("recipe_version"))))
    if bundle_root.name != str(payload.get("scenario_id", "")):
        issues.append(ArtifactIssue("directory_identity", bundle_root.name))
    if payload.get("data_tier") not in {"cc0_reconstruction", "entitled_local"}:
        issues.append(ArtifactIssue("data_tier", str(payload.get("data_tier"))))
    if payload.get("data_tier") == "cc0_reconstruction" and payload.get("vintage_status") != "reconstructed_later":
        issues.append(ArtifactIssue("false_vintage", str(payload.get("vintage_status"))))
    _utc(payload.get("created_at"), "created_at", issues)
    if payload.get("cutoff_policy") != "observation_or_published_at_lte_case_cutoff":
        issues.append(ArtifactIssue("cutoff_policy", str(payload.get("cutoff_policy"))))
    embedding = payload.get("embedding", {})
    expected_embed = {
        "model_id": EMBED_MODEL, "revision": EMBED_REVISION,
        "tokenizer_revision": EMBED_REVISION, "dimension": 2048,
        "model_dtype": "bfloat16", "storage_dtype": "float32", "query_role": "query",
        "passage_role": "passage", "normalized": True, "max_tokens": 4096,
    }
    if not isinstance(embedding, dict):
        issues.append(ArtifactIssue("embedding_shape", "scenario-v2"))
    else:
        if type(embedding.get("dimension")) is not int or type(embedding.get("max_tokens")) is not int or type(embedding.get("normalized")) is not bool:
            issues.append(ArtifactIssue("embedding_contract_drift", "exact JSON types"))
        for key, expected in expected_embed.items():
            if embedding.get(key) != expected:
                issues.append(ArtifactIssue("embedding_contract_drift", key))
        implementation = embedding.get("implementation")
        allowed_implementation = implementation == "pinned_model" or (
            allow_pending_semantic and implementation == "pending_gpu"
        ) or (
            allow_test_embeddings and implementation == "deterministic_fake_test_only"
            and payload.get("data_tier") == "ci_fixture"
        )
        if not allowed_implementation:
            issues.append(ArtifactIssue("test_embedding_forbidden", str(implementation)))
        if "llama" in str(embedding.get("model_id", "")).lower():
            issues.append(ArtifactIssue("forbidden_model", str(embedding.get("model_id"))))
    market = payload.get("market", {})
    documents = payload.get("documents", {})
    nested_payloads: dict[str, dict[str, Any]] = {}
    nested_roots: dict[str, Path] = {}
    for name, binding in (("market", market), ("documents", documents)):
        if not isinstance(binding, dict) or set(binding) != {"path", "snapshot_id", "manifest_sha256"}:
            issues.append(ArtifactIssue("nested_binding_shape", name)); continue
        nested_root = _safe_child(bundle_root, binding["path"], issues)
        if nested_root is None:
            continue
        nested_roots[name] = nested_root
        manifest_path = nested_root / "manifest.json"
        if not manifest_path.is_file():
            issues.append(ArtifactIssue("nested_manifest_missing", name)); continue
        if sha256_file(manifest_path) != binding["manifest_sha256"]:
            issues.append(ArtifactIssue("nested_manifest_digest", name)); continue
        try:
            if name == "market":
                from scripts.data.market_contract import validate_snapshot
                nested = validate_snapshot(nested_root, "reconstruction" if payload.get("data_tier") == "cc0_reconstruction" else "release")
            else:
                from scripts.data.document_contract import validate_document_snapshot
                nested = validate_document_snapshot(nested_root, "reconstruction" if payload.get("data_tier") == "cc0_reconstruction" else "release")
            if nested.get("snapshot_id") != binding["snapshot_id"]:
                issues.append(ArtifactIssue("nested_snapshot_identity", name))
            nested_payloads[name] = nested
        except Exception as exc:
            nested_issues = getattr(exc, "issues", ())
            detail = nested_issues[0].code if nested_issues else type(exc).__name__
            issues.append(ArtifactIssue("nested_contract", f"{name}:{detail}"))
    readiness = payload.get("readiness", {})
    readiness_rows: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(readiness, dict) or set(readiness) != {"market", "documents"}:
        issues.append(ArtifactIssue("readiness_shape", "scenario-v2"))
    else:
        for name, item in readiness.items():
            if not isinstance(item, dict) or set(item) != {
                "path", "sha256", "records", "snapshot_id", "bank_sha256", "contracts_sha256",
            }:
                issues.append(ArtifactIssue("readiness_shape", name)); continue
            candidate = _safe_child(bundle_root, item["path"], issues)
            if candidate is None:
                continue
            if not candidate.is_file():
                issues.append(ArtifactIssue("readiness_missing", name)); continue
            if sha256_file(candidate) != item["sha256"]:
                issues.append(ArtifactIssue("readiness_digest", name))
            records = sum(1 for line in candidate.read_text(encoding="utf-8").splitlines() if line.strip())
            if records != item["records"] or records != 250:
                issues.append(ArtifactIssue("readiness_count", f"{name}:{records}"))
            nested_binding = market if name == "market" else documents
            readiness_rows[name] = _readiness_rows(name, candidate, item, nested_binding, issues)
            if readiness_rows[name]:
                bank_digests = {row.get("bank_sha256") for row in readiness_rows[name]}
                contract_bindings = sorted(
                    (row.get("case_id"), row.get("contract_sha256")) for row in readiness_rows[name]
                )
                contracts_digest = hashlib.sha256(json.dumps(
                    contract_bindings, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                ).encode()).hexdigest()
                if bank_digests != {item.get("bank_sha256")} or contracts_digest != item.get("contracts_sha256"):
                    issues.append(ArtifactIssue("readiness_aggregate_binding", name))
    identity = {
        "recipe_version": payload.get("recipe_version"),
        "created_at": payload.get("created_at"),
        "market_manifest_sha256": market.get("manifest_sha256"),
        "document_manifest_sha256": documents.get("manifest_sha256"),
        "market_readiness_sha256": readiness.get("market", {}).get("sha256"),
        "document_readiness_sha256": readiness.get("documents", {}).get("sha256"),
        "embedding_model": f"{EMBED_MODEL}@{EMBED_REVISION}",
    }
    identity_bytes = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    expected_scenario = "market-shock-v2-" + hashlib.sha256(identity_bytes).hexdigest()[:16]
    if payload.get("scenario_id") != expected_scenario or payload.get("snapshot_id") != expected_scenario:
        issues.append(ArtifactIssue("scenario_identity", expected_scenario))
    if readiness_rows.get("market") and readiness_rows.get("documents"):
        market_cases = {row.get("case_id") for row in readiness_rows["market"]}
        document_cases = {row.get("case_id") for row in readiness_rows["documents"]}
        market_banks = {row.get("bank_sha256") for row in readiness_rows["market"]}
        document_banks = {row.get("bank_sha256") for row in readiness_rows["documents"]}
        if market_cases != document_cases or market_banks != document_banks:
            issues.append(ArtifactIssue("readiness_cross_binding", "market/documents"))
    semantic = payload.get("semantic", {})
    if not isinstance(semantic, dict) or set(semantic) != {
        "status", "market_manifest_sha256", "document_manifest_sha256",
        "scenario_input_sha256", "eligible_document_ids", "eligible_document_count",
        "index_path", "index_sha256", "gpu_receipt",
    }:
        issues.append(ArtifactIssue("semantic_shape", "scenario-v2"))
    else:
        if semantic.get("market_manifest_sha256") != market.get("manifest_sha256"):
            issues.append(ArtifactIssue("semantic_input_drift", "market"))
        if semantic.get("document_manifest_sha256") != documents.get("manifest_sha256"):
            issues.append(ArtifactIssue("semantic_input_drift", "documents"))
        if semantic.get("scenario_input_sha256") != hashlib.sha256(identity_bytes).hexdigest():
            issues.append(ArtifactIssue("semantic_input_drift", "scenario identity"))
        identifiers = semantic.get("eligible_document_ids")
        if not isinstance(identifiers, list) or type(semantic.get("eligible_document_count")) is not int or len(identifiers) != semantic.get("eligible_document_count") or len(identifiers) != len(set(identifiers)):
            issues.append(ArtifactIssue("semantic_document_identity", "eligible documents"))
        if semantic.get("status") == "ready":
            receipt = semantic.get("gpu_receipt")
            if not isinstance(receipt, dict) or receipt.get("gpu_executed") is not True or receipt.get("fallback_used") is not False:
                issues.append(ArtifactIssue("semantic_gpu_receipt", "strict GPU required"))
            if semantic.get("index_path") != "indexes/cuvs-brute-force.bin":
                issues.append(ArtifactIssue("semantic_index_path", str(semantic.get("index_path"))))
            index_candidate = _safe_child(bundle_root, "indexes/cuvs-brute-force.bin", issues)
            if index_candidate is None or not index_candidate.is_file() or sha256_file(index_candidate) != semantic.get("index_sha256"):
                issues.append(ArtifactIssue("semantic_index_digest", str(semantic.get("index_path"))))
        elif not (allow_pending_semantic and semantic.get("status") == "pending_gpu"):
            issues.append(ArtifactIssue("semantic_not_ready", str(semantic.get("status"))))
    artifacts = payload.get("artifacts", [])
    seen: set[str] = set()
    if not isinstance(artifacts, list):
        issues.append(ArtifactIssue("artifact_shape", "scenario-v2")); artifacts = []
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != ARTIFACT_KEYS:
            issues.append(ArtifactIssue("artifact_shape", repr(item))); continue
        relative = item["path"]
        rel = Path(relative)
        if rel.is_absolute() or not rel.parts or rel.parts[0] not in V2_ROOTS or ".." in rel.parts:
            issues.append(ArtifactIssue("artifact_path", relative)); continue
        if relative in seen:
            issues.append(ArtifactIssue("duplicate_artifact", relative))
        seen.add(relative)
        candidate = _safe_child(bundle_root, relative, issues)
        if candidate is None:
            continue
        if not candidate.is_file():
            issues.append(ArtifactIssue("artifact_missing", relative))
        elif candidate.stat().st_size != item["bytes"]:
            issues.append(ArtifactIssue("artifact_size", relative))
        elif sha256_file(candidate) != item["sha256"]:
            issues.append(ArtifactIssue("artifact_digest", relative))
        else:
            observed_records = item.get("records")
            try:
                if candidate.suffix == ".jsonl":
                    observed_records = sum(1 for line in candidate.read_text(encoding="utf-8").splitlines() if line.strip())
                elif relative == "indexes/embedding_ids.json":
                    observed_records = len(json.loads(candidate.read_text(encoding="utf-8")))
                elif relative == "processed/session-index.json":
                    observed_records = len(json.loads(candidate.read_text(encoding="utf-8"))["sessions"])
                elif relative == "processed/graph.json":
                    observed_records = len(json.loads(candidate.read_text(encoding="utf-8"))["edges"])
                elif candidate.suffix == ".f32":
                    observed_records = candidate.stat().st_size // (2048 * 4)
                if observed_records != item.get("records"):
                    issues.append(ArtifactIssue("artifact_records", relative))
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                issues.append(ArtifactIssue("artifact_records", relative))
    if semantic.get("status") == "ready" and not semantic_artifacts_valid(
        artifacts, semantic.get("eligible_document_count"), root=bundle_root,
    ):
        issues.append(ArtifactIssue("semantic_index_artifact", "vector/index rows/bytes"))
    coverage = payload.get("coverage", {})
    market_coverage = market.get("snapshot_id") and payload.get("coverage", {}).get("market_snapshot_id")
    if market_coverage != market.get("snapshot_id") or coverage.get("document_snapshot_id") != documents.get("snapshot_id"):
        issues.append(ArtifactIssue("coverage_binding", "nested snapshot IDs"))
    nested_market = nested_payloads.get("market", {})
    nested_documents = nested_payloads.get("documents", {})
    expected_coverage = {
        "market_snapshot_id": nested_market.get("snapshot_id"),
        "document_snapshot_id": nested_documents.get("snapshot_id"),
        "targets": nested_market.get("universe", {}).get("targets"),
        "required_benchmarks": nested_market.get("universe", {}).get("required_benchmarks"),
        "optional_gaps": [gap for gap in nested_market.get("quality", {}).get("gaps", []) if not gap.get("required")],
        "market_date_coverage": nested_market.get("observed_coverage"),
        "market_fields": nested_market.get("field_coverage"),
        "document_coverage": nested_documents.get("observed_coverage"),
        "document_gaps": nested_documents.get("gaps"),
        "session_index": coverage.get("session_index"),
    }
    if coverage != expected_coverage:
        issues.append(ArtifactIssue("coverage_drift", "scenario/nested manifests"))
    session_binding = coverage.get("session_index", {}) if isinstance(coverage, dict) else {}
    session_path = _safe_child(bundle_root, str(session_binding.get("path", "")), issues)
    if session_path and session_path.is_file() and "market" in nested_roots:
        try:
            session_index = json.loads(session_path.read_text(encoding="utf-8"))
            import pyarrow.parquet as pq
            parquet_sessions = sorted(
                ({key: row[key] for key in ("session_date", "open_at", "close_at")}
                 for row in pq.read_table(nested_roots["market"] / "sessions.parquet").to_pylist()),
                key=lambda row: row["session_date"],
            )
            if (
                set(session_index) != {"schema_version", "calendar", "timezone", "sessions"}
                or session_index.get("schema_version") != 1
                or session_index.get("calendar") != "XNYS"
                or session_index.get("timezone") != "America/New_York"
                or session_index.get("sessions") != parquet_sessions
                or session_binding.get("sha256") != sha256_file(session_path)
                or session_binding.get("records") != len(parquet_sessions)
            ):
                issues.append(ArtifactIssue("session_index_drift", "market/sessions.parquet"))
        except Exception as exc:
            issues.append(ArtifactIssue("session_index_unreadable", type(exc).__name__))
    try:
        normalized_documents = [
            json.loads(line) for line in (bundle_root / "processed/documents.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        normalized_ids = [row["chunk_id"] for row in normalized_documents]
        embedding_ids = json.loads((bundle_root / "indexes/embedding_ids.json").read_text(encoding="utf-8"))
        if normalized_ids != semantic.get("eligible_document_ids") or embedding_ids != normalized_ids:
            issues.append(ArtifactIssue("semantic_document_identity", "documents/index IDs"))
        document_evidence_ids = {row["document_id"] for row in normalized_documents}
        for row in readiness_rows.get("documents", []):
            if not set(row.get("matched_evidence_ids", [])) <= document_evidence_ids:
                issues.append(ArtifactIssue("readiness_evidence_binding", str(row.get("case_id"))))
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        issues.append(ArtifactIssue("semantic_document_unreadable", type(exc).__name__))
    _validate_derived(bundle_root, payload, issues, allow_pending=allow_pending_semantic)
    if semantic.get("status") == "ready":
        try:
            index_meta = json.loads((bundle_root / "indexes/cuvs-index.json").read_text(encoding="utf-8"))
            identifiers = semantic.get("eligible_document_ids")
            if not isinstance(identifiers, list) or not semantic_index_valid(
                semantic, index_meta, identifiers,
                risk_sha256=sha256_file(bundle_root / "models/risk-model.json"),
            ):
                issues.append(ArtifactIssue("semantic_index_metadata", "cuvs-index.json"))
        except (OSError, json.JSONDecodeError):
            issues.append(ArtifactIssue("semantic_index_metadata", "unreadable"))
    if issues:
        raise ArtifactValidationError(issues)
    return payload
