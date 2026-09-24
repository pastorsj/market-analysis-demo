"""Production Streamable HTTP MCP server with fail-closed GPU readiness."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import re
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from .artifacts import ArtifactBundle, load_bundle
from .config import Settings
from .dashboard import build_dashboard
from .engine import ToolEngine
from .models import ToolResult

TARGETS = ("NVDA", "AMD", "JPM", "GS", "SCHW")
BENCHMARKS = ("QQQ", "SPY", "XLF")
SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
state: dict[str, object] = {"ready": False, "reason": "starting", "tools": 0}
engine: ToolEngine | None = None


class StartupProofError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _attr(value: object, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def _receipt(result: object, family: str, bundle: ArtifactBundle) -> None:
    receipt = _attr(result, "receipt")
    store = bundle.store
    if store is None:
        raise StartupProofError("scenario_store_missing")
    expected = {
        "engine": family,
        "gpu_executed": True,
        "fallback_used": False,
        "artifact_manifest_sha256": bundle.manifest_sha256,
        "scenario_id": store.manifest["scenario_id"],
        "market_manifest_sha256": store.manifest["market"]["manifest_sha256"],
        "document_manifest_sha256": store.manifest["documents"]["manifest_sha256"],
        "market_readiness_sha256": store.manifest["readiness"]["market"]["sha256"],
        "document_readiness_sha256": store.manifest["readiness"]["documents"]["sha256"],
    }
    if (
        receipt is None
        or not _attr(receipt, "device")
        or any(_attr(receipt, key) != value for key, value in expected.items())
    ):
        raise StartupProofError(f"{family.replace('-', '_')}_gpu_probe_failed")


def prove_runtime(bundle: ArtifactBundle, candidate: ToolEngine) -> dict[str, str]:
    """Execute every production GPU family before publishing readiness."""
    store = bundle.store
    if (
        store is None
        or store.manifest.get("schema_version") != 2
        or store.manifest.get("recipe_version") != 3
    ):
        raise StartupProofError("scenario_store_missing")
    cutoff = datetime.fromisoformat(store.sessions[-1]["close_at"].replace("Z", "+00:00"))
    target_fields = ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume")
    for symbol in TARGETS:
        probe = store.window(symbol, cutoff, lookback=2, fields=target_fields)
        if probe.status != "ready" or len(probe.rows) != 2 or not probe.gpu_executed:
            raise StartupProofError(f"market_gpu_probe_{symbol.lower()}_failed")
    for symbol in BENCHMARKS:
        probe = store.window(symbol, cutoff, lookback=2, fields=("adjusted_close",))
        if probe.status != "ready" or len(probe.rows) != 2 or not probe.gpu_executed:
            raise StartupProofError(f"market_gpu_probe_{symbol.lower()}_failed")
    for symbol in ("NVDA", "JPM"):
        result = candidate.get_price_context(symbol, cutoff)
        _receipt(result, "cudf", bundle)
        if not _attr(result, "evidence"):
            raise StartupProofError("cudf_gpu_probe_failed")
    semantic = candidate.search_news("NVDA", cutoff, "runtime readiness", top_k=2)
    _receipt(semantic, "cuvs", bundle)
    graph = candidate.trace_shock_propagation("NVDA", cutoff, max_depth=1)
    _receipt(graph, "cugraph", bundle)
    risk = candidate.predict_volatility_risk("NVDA", cutoff)
    _receipt(risk, "xgboost-gpu", bundle)
    topics = candidate.project_news_topics("NVDA", cutoff, dimensions=2, max_documents=4)
    _receipt(topics, "cuml", bundle)
    if (
        not _attr(semantic, "evidence")
        or not _attr(graph, "data").get("paths")
        or not _attr(topics, "artifacts")
    ):
        raise StartupProofError("evidence_gpu_probe_failed")
    return {name: "pass" for name in ("market", "semantic", "graph", "risk", "topics")}


def safe_reason(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and SAFE_CODE.fullmatch(code):
        return code
    message = str(exc).lower()
    if any(token in message for token in ("nemotron", "embed", "cuvs")):
        return "semantic_gpu_probe_failed"
    if any(token in message for token in ("cuda", "cudf", "cupy", "gpu")):
        return "gpu_runtime_unavailable"
    if isinstance(exc, ValueError):
        return "configuration_invalid"
    return "startup_validation_failed"


def _gaps(values: object) -> list[dict[str, object]]:
    safe = []
    for value in values if isinstance(values, list) else []:
        code = value.get("code") if isinstance(value, dict) else None
        if not isinstance(code, str) or not SAFE_CODE.fullmatch(code):
            continue
        item: dict[str, object] = {"code": code}
        for key in ("instrument_id", "issuer_id", "source_kind", "event_id", "required"):
            candidate = value.get(key)
            if (
                isinstance(candidate, (bool, int))
                or isinstance(candidate, str)
                and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", candidate)
            ):
                item[key] = candidate
        safe.append(item)
    return safe


def safe_health(bundle: ArtifactBundle, probes: dict[str, str]) -> dict[str, object]:
    value = bundle.health()
    keys = (
        "scenario_id",
        "scenario_manifest_sha256",
        "market_snapshot_id",
        "market_manifest_sha256",
        "document_snapshot_id",
        "document_manifest_sha256",
        "market_readiness_sha256",
        "document_readiness_sha256",
        "data_tier",
        "vintage_status",
        "targets",
        "required_benchmarks",
        "optional_instruments",
        "date_coverage",
        "field_coverage",
        "document_coverage",
        "semantic",
        "risk_model",
    )
    result = {key: value[key] for key in keys}
    result["optional_gaps"] = _gaps(bundle.store.market["quality"]["gaps"] if bundle.store else [])
    result["document_gaps"] = _gaps(value.get("document_gaps"))
    result["gpu_probes"] = probes
    return result


def _publish(**values: object) -> None:
    state.clear()
    state.update(values)


@asynccontextmanager
async def lifespan(_server: MCPServer):
    global engine
    _publish(ready=False, reason="starting", tools=0)
    try:
        settings = Settings.from_env()
        if not settings.strict_gpu:
            raise StartupProofError("strict_gpu_required")
        bundle = load_bundle(settings.root, strict_gpu=settings.strict_gpu)
        candidate = ToolEngine(
            bundle,
            strict_gpu=settings.strict_gpu,
            embed_path=settings.embed_path if settings.strict_gpu else None,
        )
        probes = prove_runtime(bundle, candidate)
        engine = candidate
        _publish(ready=True, reason="ready", tools=7, **safe_health(bundle, probes))
    except Exception as exc:
        engine = None
        _publish(ready=False, reason=safe_reason(exc), tools=0)
        raise
    try:
        yield {"engine": engine}
    finally:
        engine = None
        _publish(ready=False, reason="stopped", tools=0)


mcp = MCPServer("market-shock-tools", version="1.0.0", lifespan=lifespan)
read_only = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _engine() -> ToolEngine:
    if engine is None:
        raise RuntimeError("artifact bundle is not ready")
    return engine


@mcp.tool(
    description="Detect abnormal return, volume and volatility for one stock/date.",
    annotations=read_only,
    structured_output=True,
)
def detect_market_shock(ticker: str, as_of: datetime) -> ToolResult:
    return _engine().detect_market_shock(ticker, as_of)


@mcp.tool(
    description="Compare a company with semiconductor and broad-market benchmarks.",
    annotations=read_only,
    structured_output=True,
)
def get_price_context(ticker: str, as_of: datetime) -> ToolResult:
    return _engine().get_price_context(ticker, as_of)


@mcp.tool(
    description="Retrieve cutoff-qualified company reporting and primary sources.",
    annotations=read_only,
    structured_output=True,
)
def search_news(ticker: str, as_of: datetime, query: str, top_k: int = 5) -> ToolResult:
    return _engine().search_news(ticker, as_of, query, top_k)


@mcp.tool(
    description="Find quantitative historical patterns without claiming causal identity.",
    annotations=read_only,
    structured_output=True,
)
def find_historical_analogues(ticker: str, as_of: datetime, top_k: int = 3) -> ToolResult:
    return _engine().find_historical_analogues(ticker, as_of, top_k)


@mcp.tool(
    description="Trace bounded, cited company and market relationship paths.",
    annotations=read_only,
    structured_output=True,
)
def trace_shock_propagation(ticker: str, as_of: datetime, max_depth: int = 2) -> ToolResult:
    return _engine().trace_shock_propagation(ticker, as_of, max_depth)


@mcp.tool(
    description="Estimate five-session annualized realized volatility.",
    annotations=read_only,
    structured_output=True,
)
def predict_volatility_risk(ticker: str, as_of: datetime) -> ToolResult:
    return _engine().predict_volatility_risk(ticker, as_of)


@mcp.tool(
    description="Create a bounded two- or three-dimensional evidence topic map.",
    annotations=read_only,
    structured_output=True,
)
def project_news_topics(
    ticker: str, as_of: datetime, dimensions: int = 2, max_documents: int = 99
) -> ToolResult:
    return _engine().project_news_topics(ticker, as_of, dimensions, max_documents)


@mcp.resource(
    "market://dashboard/{ticker}/{as_of}",
    name="market-dashboard",
    description="Cutoff-filtered five-company market dashboard projection.",
    mime_type="application/json",
)
def dashboard_resource(ticker: str, as_of: str) -> dict[str, object]:
    return build_dashboard(_engine(), ticker, as_of)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse(
        {"service": "tools", "live": True, **state}, status_code=200 if state["ready"] else 503
    )


app = mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0")
