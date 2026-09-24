"""Bounded, cutoff-aware cuDF access to one validated scenario-v2 market corpus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path
from typing import Any
from collections.abc import Callable, Iterable
from zoneinfo import ZoneInfo


MARKET_FIELDS = frozenset(
    {
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "raw_volume",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "volume",
    }
)
EMBED_ID = "nvidia/Nemotron-3-Embed-1B-BF16"
EMBED_REV = "9e0b24858b1195815ecb1188ffa1b73bcea7b30a"
NORMALIZED_DOCUMENT_KEYS = {
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


class MarketStoreError(RuntimeError):
    """Stable fail-closed runtime error; ``code`` is safe for health output."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class MarketWindow:
    ticker: str
    requested_at: datetime
    resolved_session: str | None
    rows: tuple[dict[str, Any], ...]
    status: str
    missing_fields: tuple[str, ...]
    price_basis: tuple[str, ...]
    vintage_status: str
    source_ids: tuple[str, ...]
    gpu_executed: bool


Reader = Callable[[list[Path], list[str], list[tuple[str, str, str]]], Iterable[dict[str, Any]]]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                value.update(block)
    except OSError as exc:
        raise MarketStoreError("artifact_unreadable", path.name) from exc
    return value.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketStoreError("manifest_unreadable", path.name) from exc
    if not isinstance(value, dict):
        raise MarketStoreError("manifest_shape", path.name)
    return value


def child(root: Path, relative: str) -> Path:
    item = Path(relative)
    if item.is_absolute() or not item.parts or ".." in item.parts:
        raise MarketStoreError("artifact_path", relative)
    candidate = (root / item).resolve()
    if root not in candidate.parents:
        raise MarketStoreError("artifact_escape", relative)
    return candidate


def _cudf_reader(
    paths: list[Path],
    columns: list[str],
    filters: list[tuple[str, str, str]],
) -> Iterable[dict[str, Any]]:
    try:
        import cudf
        import cupy as cp
    except ImportError as exc:
        raise MarketStoreError("gpu_reader_unavailable", "cuDF/CuPy import") from exc
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            raise MarketStoreError("gpu_reader_unavailable", "CUDA device")
        frame = cudf.read_parquet(
            [str(path) for path in paths],
            columns=columns,
            filters=filters,
        )
        return frame.to_arrow().to_pylist()
    except MarketStoreError:
        raise
    except Exception as exc:
        raise MarketStoreError("market_read_failed", type(exc).__name__) from exc


def _expect(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise MarketStoreError(code, "unknown or missing fields")
    return value


def validate_tables(
    root: Path,
    manifest: dict[str, Any],
    market_root: Path,
    market: dict[str, Any],
    document_root: Path,
    documents: dict[str, Any],
    table: Callable[[Path, list[str] | None], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    coverage = _expect(
        manifest["coverage"],
        {
            "market_snapshot_id",
            "document_snapshot_id",
            "targets",
            "required_benchmarks",
            "optional_gaps",
            "market_date_coverage",
            "market_fields",
            "document_coverage",
            "document_gaps",
            "session_index",
        },
        "coverage_shape",
    )
    expected = {
        "market_snapshot_id": market["snapshot_id"],
        "document_snapshot_id": documents["snapshot_id"],
        "targets": market["universe"]["targets"],
        "required_benchmarks": market["universe"]["required_benchmarks"],
        "optional_gaps": [g for g in market["quality"]["gaps"] if not g.get("required")],
        "market_date_coverage": market["observed_coverage"],
        "market_fields": market["field_coverage"],
        "document_coverage": documents["observed_coverage"],
        "document_gaps": documents["gaps"],
        "session_index": coverage["session_index"],
    }
    if (
        coverage != expected
        or coverage["targets"] != ["NVDA", "AMD", "JPM", "GS", "SCHW"]
        or set(coverage["required_benchmarks"]) != {"QQQ", "SPY", "XLF"}
    ):
        raise MarketStoreError("coverage_drift", "scenario/nested")
    binding = _expect(coverage["session_index"], {"path", "sha256", "records"}, "session_index_shape")
    path = child(root, binding["path"])
    indexed = load_json(path)
    rows = table(market_root / "sessions.parquet", ["session_date", "open_at", "close_at"])
    rows = sorted(
        ({k: r[k] for k in ("session_date", "open_at", "close_at")} for r in rows),
        key=lambda r: r["session_date"],
    )
    if (
        set(indexed) != {"schema_version", "calendar", "timezone", "sessions"}
        or indexed.get("schema_version") != 1
        or indexed.get("calendar") != "XNYS"
        or indexed.get("timezone") != "America/New_York"
        or indexed.get("sessions") != rows
        or digest(path) != binding["sha256"]
        or len(rows) != binding["records"]
    ):
        raise MarketStoreError("session_index_drift", "market/sessions.parquet")
    covered = {r["instrument_id"]: r for r in table(market_root / "coverage.parquet", None)}
    roles = {
        **{x: "target" for x in market["universe"]["targets"]},
        **{x: "peer" for x in market["universe"]["peers"]},
        **{x: "required_benchmark" for x in market["universe"]["required_benchmarks"]},
        **{x: "optional" for x in market["universe"]["optional_instruments"]},
    }
    if set(covered) != set(roles):
        raise MarketStoreError("false_coverage", "coverage/universe")
    for symbol, fields in market["field_coverage"].items():
        row = covered.get(symbol, {})
        present = json.loads(row.get("present_fields", "[]"))
        if (
            row.get("status") != "present"
            or row.get("role") != roles.get(symbol)
            or row.get("rows") != fields["rows"]
            or row.get("start") != fields["start"]
            or row.get("end_inclusive") != fields["end_inclusive"]
            or present != fields["present_fields"]
            or row.get("vintage_status") != market["vintage_status"]
        ):
            raise MarketStoreError("false_coverage", symbol)
    for symbol in (*market["universe"]["targets"], *market["universe"]["required_benchmarks"]):
        if covered[symbol].get("missing_sessions") != 0 or covered[symbol].get("rows") != len(rows):
            raise MarketStoreError("false_coverage", symbol)
    nested_docs = table(
        document_root / "documents.parquet", ["evidence_id", "issuer_id", "source_kind", "published_at"]
    )
    direct = {
        "documents": len(nested_docs),
        "coverage_rows": len(table(document_root / "coverage.parquet", None)),
        "issuers": sorted({r["issuer_id"] for r in nested_docs}),
        "source_kinds": sorted({r["source_kind"] for r in nested_docs}),
        "published_start": min(r["published_at"] for r in nested_docs),
        "published_end": max(r["published_at"] for r in nested_docs),
    }
    if documents["observed_coverage"] != direct:
        raise MarketStoreError("document_coverage_drift", "canonical tables")
    return rows, nested_docs


def validate_derived(root: Path, manifest: dict[str, Any], normalized: tuple[dict[str, Any], ...]) -> None:
    binding = {
        "market_manifest_sha256": manifest["market"]["manifest_sha256"],
        "document_manifest_sha256": manifest["documents"]["manifest_sha256"],
        "market_readiness_sha256": manifest["readiness"]["market"]["sha256"],
        "document_readiness_sha256": manifest["readiness"]["documents"]["sha256"],
    }
    graph = load_json(root / "processed/graph.json")
    analogue = load_json(root / "processed/analogue_features.json")
    training = load_json(root / "processed/risk-training.json")
    projection = load_json(root / "processed/projection_inputs.json")
    _expect(
        graph, {"schema_version", "input_binding", "cutoff_policy", "provenance", "edges"}, "derived_shape"
    )
    _expect(analogue, {"schema_version", "input_binding", "basis", "cutoff_policy", "rows"}, "derived_shape")
    _expect(
        training,
        {
            "schema_version",
            "input_binding",
            "basis",
            "training_cutoff",
            "cutoff_policy",
            "rows",
            "label_contract",
        },
        "derived_shape",
    )
    _expect(projection, {"schema_version", "input_binding", "cutoff_policy", "documents"}, "derived_shape")
    for name, value in (
        ("graph", graph),
        ("analogue", analogue),
        ("risk-training", training),
        ("projection", projection),
    ):
        if value["schema_version"] != 2 or value["input_binding"] != binding:
            raise MarketStoreError("derived_input_binding", name)
    sources = graph["provenance"].get("source_ids")
    if (
        graph["cutoff_policy"] != "valid_from_lte_query_cutoff"
        or not isinstance(sources, list)
        or any(
            not r.get("source_id") or not r.get("source_row_id") or r.get("input_source_ids") != sources
            for r in graph["edges"]
        )
    ):
        raise MarketStoreError("derived_provenance", "graph")
    if analogue["cutoff_policy"] != "feature_at_lt_query_cutoff" or any(
        not r.get("source_id")
        or not r.get("source_row_id")
        or not r.get("input_source_row_ids")
        or r.get("future_outcome_excluded") is not True
        for r in analogue["rows"]
    ):
        raise MarketStoreError("derived_provenance", "analogue")
    labels = {
        "name": "realized_volatility_5d",
        "units": "annualized_decimal_standard_deviation",
        "horizon_completed_sessions": 5,
        "annualization_sessions": 252,
    }
    cutoff = training["training_cutoff"]
    if (
        training["cutoff_policy"] != "label_available_at_lte_training_and_query_cutoff"
        or training["label_contract"] != labels
        or len(training["rows"]) < 100
        or any(
            r.get("feature_at", "") >= r.get("label_available_at", "")
            or r.get("label_available_at", "") > cutoff
            or len(r.get("label_source_row_ids", [])) != 5
            or not r.get("label_source_ids")
            for r in training["rows"]
        )
    ):
        raise MarketStoreError("risk_training_contract", "labels/cutoff/provenance")
    expected_projection = [
        {
            "chunk_id": r["chunk_id"],
            "issuer_ids": r["issuer_ids"],
            "published_at": r["published_at"],
            "content_sha256": r["content_sha256"],
        }
        for r in normalized
    ]
    if (
        projection["cutoff_policy"] != "published_at_lte_query_cutoff"
        or projection["documents"] != expected_projection
    ):
        raise MarketStoreError("projection_input_drift", "normalized documents")
    risk = load_json(root / "models/risk-model.json")
    receipt = load_json(root / "models/risk-model.receipt.json")
    risk_keys = {
        "schema_version",
        "status",
        "market_snapshot_id",
        "input_binding",
        "training_cutoff",
        "training_rows",
        "training_input",
        "training_input_sha256",
        "future_outcomes_excluded_from_features",
        "output_contract",
        "model_path",
        "model_sha256",
        "model_bytes",
        "receipt_path",
        "receipt_sha256",
        "algorithm",
        "algorithm_version",
    }
    receipt_keys = {
        "schema_version",
        "kind",
        "algorithm",
        "algorithm_version",
        "parameters",
        "features",
        "label",
        "label_units",
        "horizon_completed_sessions",
        "output_kind",
        "training_rows",
        "training_cutoff",
        "training_input_sha256",
        "input_binding",
        "model_sha256",
        "model_bytes",
        "device",
        "gpu_executed",
        "fallback_used",
    }
    _expect(risk, risk_keys, "risk_model_shape")
    _expect(receipt, receipt_keys, "risk_receipt_shape")
    model = root / "models/risk-model.ubj"
    output = {
        "kind": "continuous_realized_volatility",
        "units": "annualized_decimal_standard_deviation",
        "horizon_completed_sessions": 5,
        "probability": False,
        "band_thresholds": None,
    }
    expected = (
        risk["schema_version"] == 2
        and risk["status"] == "ready"
        and risk["market_snapshot_id"] == manifest["market"]["snapshot_id"]
        and risk["input_binding"] == binding
        and risk["training_input"] == "processed/risk-training.json"
        and risk["training_input_sha256"] == digest(root / risk["training_input"])
        and risk["training_cutoff"] == cutoff
        and risk["training_rows"] == len(training["rows"])
        and risk["future_outcomes_excluded_from_features"] is True
        and risk["output_contract"] == output
        and risk["model_path"] == "risk-model.ubj"
        and risk["model_sha256"] == digest(model)
        and risk["model_bytes"] == model.stat().st_size
        and risk["receipt_path"] == "risk-model.receipt.json"
        and risk["receipt_sha256"] == digest(root / "models/risk-model.receipt.json")
    )
    receipt_ok = (
        receipt["schema_version"] == 1
        and receipt["kind"] == "risk_model_training_receipt"
        and receipt["input_binding"] == binding
        and receipt["training_input_sha256"] == risk["training_input_sha256"]
        and receipt["training_rows"] == risk["training_rows"]
        and receipt["training_cutoff"] == cutoff
        and receipt["model_sha256"] == risk["model_sha256"]
        and receipt["model_bytes"] == risk["model_bytes"]
        and receipt["algorithm"] == risk["algorithm"] == "xgboost.XGBRegressor"
        and receipt["algorithm_version"] == risk["algorithm_version"]
        and receipt["device"] == "cuda:0"
        and receipt["gpu_executed"] is True
        and receipt["fallback_used"] is False
        and receipt["output_kind"] == output["kind"]
        and receipt["label_units"] == output["units"]
        and receipt["horizon_completed_sessions"] == 5
    )
    if not expected or not receipt_ok:
        raise MarketStoreError("risk_model_binding", "model/receipt")


def validate_semantic(
    root: Path,
    manifest: dict[str, Any],
    normalized: tuple[dict[str, Any], ...],
    nested_docs: list[dict[str, Any]],
) -> None:
    embedding = _expect(
        manifest["embedding"],
        {
            "model_id",
            "revision",
            "tokenizer_revision",
            "dimension",
            "model_dtype",
            "storage_dtype",
            "query_role",
            "passage_role",
            "normalized",
            "max_tokens",
            "implementation",
        },
        "embedding_shape",
    )
    expected_embed = {
        "model_id": EMBED_ID,
        "revision": EMBED_REV,
        "tokenizer_revision": EMBED_REV,
        "dimension": 2048,
        "model_dtype": "bfloat16",
        "storage_dtype": "float32",
        "query_role": "query",
        "passage_role": "passage",
        "normalized": True,
        "max_tokens": 4096,
        "implementation": "pinned_model",
    }
    if embedding != expected_embed or "llama" in embedding["model_id"].lower():
        raise MarketStoreError("embedding_contract_drift", "pinned Nemotron")
    semantic = _expect(
        manifest["semantic"],
        {
            "status",
            "market_manifest_sha256",
            "document_manifest_sha256",
            "scenario_input_sha256",
            "eligible_document_ids",
            "eligible_document_count",
            "index_path",
            "index_sha256",
            "gpu_receipt",
        },
        "semantic_shape",
    )
    receipt = _expect(
        semantic["gpu_receipt"],
        {"engine", "device", "gpu_executed", "fallback_used", "model_id", "revision", "dimension"},
        "semantic_receipt_shape",
    )
    ids = [r["chunk_id"] for r in normalized]
    embedded = json.loads((root / "indexes/embedding_ids.json").read_text(encoding="utf-8"))
    evidence = [r["evidence_id"] for r in nested_docs]
    if (
        any(
            set(r) != NORMALIZED_DOCUMENT_KEYS
            or r["chunk_id"] != r["document_id"] + "-c0"
            or r["source_id"] != r["document_id"]
            or hashlib.sha256(r["text"].encode()).hexdigest() != r["content_sha256"]
            for r in normalized
        )
        or [r["document_id"] for r in normalized] != evidence
        or ids != embedded
        or ids != semantic["eligible_document_ids"]
        or len(ids) != len(set(ids))
        or len(ids) != semantic["eligible_document_count"]
    ):
        raise MarketStoreError("semantic_document_identity", "normalized/documents/index")
    identity = {
        "recipe_version": manifest["recipe_version"],
        "created_at": manifest["created_at"],
        "market_manifest_sha256": manifest["market"]["manifest_sha256"],
        "document_manifest_sha256": manifest["documents"]["manifest_sha256"],
        "market_readiness_sha256": manifest["readiness"]["market"]["sha256"],
        "document_readiness_sha256": manifest["readiness"]["documents"]["sha256"],
        "embedding_model": f"{EMBED_ID}@{EMBED_REV}",
    }
    identity_sha = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    index = load_json(root / "indexes/cuvs-index.json")
    index_keys = {
        "kind",
        "metric",
        "dimension",
        "normalized",
        "rows",
        "fixture_only",
        "model_id",
        "revision",
        "index_path",
        "index_sha256",
        "attention",
        "gpu_executed",
        "device",
        "scenario_input_sha256",
        "market_manifest_sha256",
        "document_manifest_sha256",
        "risk_model_metadata_sha256",
        "eligible_document_ids_sha256",
    }
    _expect(index, index_keys, "semantic_index_shape")
    ids_sha = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
    index_path = child(root, semantic["index_path"])
    valid = (
        semantic["status"] == "ready"
        and semantic["scenario_input_sha256"] == identity_sha
        and semantic["market_manifest_sha256"] == identity["market_manifest_sha256"]
        and semantic["document_manifest_sha256"] == identity["document_manifest_sha256"]
        and digest(index_path) == semantic["index_sha256"]
        and receipt
        == {
            "engine": "cuvs",
            "device": receipt["device"],
            "gpu_executed": True,
            "fallback_used": False,
            "model_id": EMBED_ID,
            "revision": EMBED_REV,
            "dimension": 2048,
        }
    )
    required = {
        "kind": "cuvs_brute_force",
        "metric": "inner_product",
        "dimension": 2048,
        "normalized": True,
        "rows": len(ids),
        "fixture_only": False,
        "model_id": EMBED_ID,
        "revision": EMBED_REV,
        "index_path": semantic["index_path"],
        "index_sha256": semantic["index_sha256"],
        "gpu_executed": True,
        "scenario_input_sha256": identity_sha,
        "market_manifest_sha256": identity["market_manifest_sha256"],
        "document_manifest_sha256": identity["document_manifest_sha256"],
        "risk_model_metadata_sha256": digest(root / "models/risk-model.json"),
        "eligible_document_ids_sha256": ids_sha,
    }
    if not valid or any(index.get(k) != v for k, v in required.items()):
        raise MarketStoreError("semantic_binding", "Nemotron/cuVS")


class MarketStore:
    """Retains verified metadata and performs predicate/projected Parquet reads."""

    def __init__(
        self,
        scenario_root: Path,
        *,
        reader: Reader | None = None,
        reader_is_gpu: bool = True,
        strict_gpu: bool = True,
        _validated: dict[str, Any] | None = None,
    ):
        self.root = scenario_root.resolve()
        if strict_gpu and reader is not None:
            raise MarketStoreError("gpu_reader_unavailable", "strict reader contract")
        if _validated is None:
            from .artifacts import validate_scenario

            _validated = validate_scenario(self.root)
        self.manifest = _validated["manifest"]
        self.manifest_sha256 = digest(self.root / "manifest.json")
        self.market = _validated["market"]
        self.documents_manifest = _validated["documents"]
        self.market_root = self.root / self.manifest["market"]["path"]
        self.document_root = self.root / self.manifest["documents"]["path"]
        self.sessions = tuple(_validated["sessions"])
        self.actions = tuple(_validated.get("actions", ()))
        self.reader = reader or _cudf_reader
        self.reader_is_gpu = reader is None
        self.field_coverage = self.market["field_coverage"]
        self.benchmark_policy = self.market["benchmark_policy"]
        self.universe = self.market["universe"]
        self.sources = {item["source_id"]: item for item in self.market["sources"]}
        declared = sorted(
            str(item["path"])
            for item in self.market["artifacts"]
            if str(item["path"]).startswith("bars/interval=1d/year=")
            and str(item["path"]).endswith(".parquet")
        )
        self.bar_paths = [child(self.market_root, relative) for relative in declared]
        if not self.bar_paths:
            raise MarketStoreError("market_artifact_missing", "daily bars")

    def _query_day(self, as_of: datetime) -> str:
        if as_of.tzinfo is None:
            raise MarketStoreError("naive_cutoff", "as_of")
        return as_of.astimezone(ZoneInfo("America/New_York")).date().isoformat()

    def _inside_coverage(self, as_of: datetime) -> bool:
        day = self._query_day(as_of)
        observed = self.market["observed_coverage"]
        return observed["start"] <= day <= observed["end_inclusive"]

    def completed_session(self, as_of: datetime) -> str | None:
        if not self._inside_coverage(as_of):
            return None
        cutoff = as_of.astimezone(UTC)
        eligible = [
            row["session_date"]
            for row in self.sessions
            if datetime.fromisoformat(row["close_at"].replace("Z", "+00:00")) <= cutoff
        ]
        return eligible[-1] if eligible else None

    def benchmark_symbols(self, ticker: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if ticker not in self.universe["targets"]:
            return (), ()
        sector = "semiconductor" if ticker in {"NVDA", "AMD"} else "financial"
        policy = self.benchmark_policy[sector]
        return tuple(policy["required"]), tuple(policy["optional"])

    def window(
        self,
        ticker: str,
        as_of: datetime,
        *,
        lookback: int = 2,
        fields: tuple[str, ...] = ("adjusted_close",),
    ) -> MarketWindow:
        if not 1 <= lookback <= 252:
            raise MarketStoreError("lookback_bounds", str(lookback))
        unknown = sorted(set(fields) - MARKET_FIELDS)
        if unknown:
            raise MarketStoreError("unknown_market_field", ",".join(unknown))
        coverage = self.field_coverage.get(ticker)
        if coverage is None:
            return self._empty(ticker, as_of, "unsupported_instrument", fields)
        if not self._inside_coverage(as_of):
            return self._empty(ticker, as_of, "outside_coverage", ())
        resolved = self.completed_session(as_of)
        present = set(coverage["present_fields"])
        missing = tuple(sorted(set(fields) - present))
        if missing:
            return self._empty(ticker, as_of, "unsupported_fields", missing, resolved)
        if resolved is None or not coverage["start"] <= resolved <= coverage["end_inclusive"]:
            return self._empty(ticker, as_of, "empty", (), resolved)
        eligible = [
            row["session_date"]
            for row in self.sessions
            if coverage["start"] <= row["session_date"] <= resolved
        ]
        expected = eligible[-lookback:]
        start = expected[0]
        years = range(int(start[:4]), int(resolved[:4]) + 1)
        paths = [p for p in self.bar_paths if int(p.parent.name.rsplit("=", 1)[-1]) in years]
        columns = list(
            dict.fromkeys(
                [
                    "instrument_id",
                    "session_date",
                    "bar_start",
                    "bar_end",
                    *sorted(present & MARKET_FIELDS),
                    "price_basis",
                    "source_id",
                    "source_row_id",
                    "captured_at",
                    "vintage_status",
                ]
            )
        )
        try:
            rows = list(
                self.reader(
                    paths,
                    columns,
                    [
                        ("instrument_id", "==", ticker),
                        ("session_date", ">=", start),
                        ("session_date", "<=", resolved),
                    ],
                )
            )
        except MarketStoreError:
            raise
        except Exception as exc:
            raise MarketStoreError("market_read_failed", type(exc).__name__) from exc
        rows.sort(key=lambda row: row["session_date"])
        observed = [row.get("session_date") for row in rows]
        if observed != expected:
            raise MarketStoreError("false_coverage", f"{ticker}:session slice")
        closes = {row["session_date"]: row["close_at"] for row in self.sessions}
        if any(
            row.get("instrument_id") != ticker
            or not start <= str(row.get("session_date", "")) <= resolved
            or str(row.get("bar_end", "")) > closes.get(str(row.get("session_date")), "")
            for row in rows
        ):
            raise MarketStoreError("market_filter_violation", ticker)
        for row in rows:
            source = self.sources.get(row.get("source_id"))
            if source is None:
                raise MarketStoreError("source_lineage_missing", str(row.get("source_id")))
            row.update(
                {
                    "attribution_url": source["attribution_url"],
                    "source_capture_id": source["capture_id"],
                    "source_capture_sha256": source["capture_sha256"],
                }
            )
        return MarketWindow(
            ticker,
            as_of,
            resolved,
            tuple(rows),
            "ready",
            (),
            tuple(coverage["price_basis"]),
            self.market["vintage_status"],
            tuple(sorted({row["source_id"] for row in rows})),
            bool(rows and self.reader_is_gpu),
        )

    def _empty(
        self,
        ticker: str,
        as_of: datetime,
        status: str,
        missing: tuple[str, ...],
        resolved: str | None = None,
    ) -> MarketWindow:
        coverage = self.field_coverage.get(ticker, {})
        return MarketWindow(
            ticker,
            as_of,
            resolved,
            (),
            status,
            missing,
            tuple(coverage.get("price_basis", ())),
            self.market.get("vintage_status", "unknown"),
            (),
            False,
        )

    def health(self) -> dict[str, Any]:
        semantic = self.manifest["semantic"]
        risk = load_json(self.root / "models/risk-model.json")
        return {
            "scenario_id": self.manifest["scenario_id"],
            "scenario_manifest_sha256": self.manifest_sha256,
            "market_snapshot_id": self.market["snapshot_id"],
            "market_manifest_sha256": self.manifest["market"]["manifest_sha256"],
            "document_snapshot_id": self.documents_manifest["snapshot_id"],
            "document_manifest_sha256": self.manifest["documents"]["manifest_sha256"],
            "market_readiness_sha256": self.manifest["readiness"]["market"]["sha256"],
            "document_readiness_sha256": self.manifest["readiness"]["documents"]["sha256"],
            "data_tier": self.manifest["data_tier"],
            "vintage_status": self.manifest["vintage_status"],
            "targets": self.market["universe"]["targets"],
            "required_benchmarks": self.market["universe"]["required_benchmarks"],
            "optional_instruments": self.market["universe"]["optional_instruments"],
            "date_coverage": self.market["observed_coverage"],
            "field_coverage": self.market["field_coverage"],
            "document_coverage": self.documents_manifest["observed_coverage"],
            "document_gaps": self.documents_manifest["gaps"],
            "semantic": {
                "model_id": self.manifest["embedding"]["model_id"],
                "revision": self.manifest["embedding"]["revision"],
                "dimension": self.manifest["embedding"]["dimension"],
                "index_sha256": semantic["index_sha256"],
            },
            "risk_model": {
                "algorithm": risk["algorithm"],
                "algorithm_version": risk["algorithm_version"],
                "model_sha256": risk["model_sha256"],
                "training_cutoff": risk["training_cutoff"],
                "output_contract": risk["output_contract"],
            },
        }
