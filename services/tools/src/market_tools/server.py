"""Streamable HTTP MCP server exposing seven read-only, cutoff-bounded GPU tools."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import document_tools, market_tools
from .config import Settings
from .dashboard import build_dashboard
from .models import ToolResult
from .runtime import Runtime

log = logging.getLogger(__name__)
state: dict[str, object] = {"ready": False, "reason": "starting"}
runtime: Runtime | None = None

Ticker = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$", description="Ticker symbol, e.g. NVDA.")]
AsOf = Annotated[datetime, Field(description="Evidence cutoff (timezone-aware). Nothing later is used.")]


def _probe(candidate: Runtime) -> None:
    """Run every tool once so readiness proves the GPU paths, not just imports."""
    ticker = candidate.scenario.targets[0]
    cutoff = candidate.scenario.sessions[-1].close_at
    checks = {
        "get_price_context": market_tools.get_price_context(candidate, ticker, cutoff),
        "detect_market_shock": market_tools.detect_market_shock(candidate, ticker, cutoff),
        "find_historical_analogues": market_tools.find_historical_analogues(candidate, ticker, cutoff),
        "map_comovement": market_tools.map_comovement(candidate, ticker, cutoff),
        "predict_volatility_risk": market_tools.predict_volatility_risk(candidate, ticker, cutoff),
        "search_news": document_tools.search_news(
            candidate, ticker, cutoff, "quarterly results", lookback_days=365
        ),
        "project_news_topics": document_tools.project_news_topics(candidate, ticker, cutoff),
    }
    failed = [name for name, value in checks.items() if value.receipt is None]
    if failed:
        raise RuntimeError(f"startup probe produced no GPU result for: {', '.join(failed)}")


@asynccontextmanager
async def lifespan(_server: MCPServer):
    global runtime
    try:
        candidate = Runtime(Settings.from_env())
        _probe(candidate)
    except Exception as exc:
        state.update(ready=False, reason=getattr(exc, "code", type(exc).__name__))
        log.exception("tools startup failed")
        raise
    runtime = candidate
    scenario = candidate.scenario
    state.update(
        ready=True,
        reason="ready",
        device=candidate.device,
        scenario_id=scenario.scenario_id,
        scenario_manifest_sha256=scenario.manifest_sha256,
        targets=list(scenario.targets),
        first_session=scenario.sessions[0].session_date,
        last_session=scenario.sessions[-1].session_date,
    )
    try:
        yield {}
    finally:
        runtime = None
        state.update(ready=False, reason="stopped")


mcp = MCPServer("market-shock-tools", version="2.0.0", lifespan=lifespan)
read_only = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _runtime() -> Runtime:
    if runtime is None:
        raise RuntimeError("tools runtime is not ready")
    return runtime


@mcp.tool(annotations=read_only, structured_output=True)
def get_price_context(ticker: Ticker, as_of: AsOf) -> ToolResult:
    """Daily price, returns, opening gap, volume ratio, and sector benchmarks for the last
    completed session at the cutoff."""
    return market_tools.get_price_context(_runtime(), ticker, as_of)


@mcp.tool(annotations=read_only, structured_output=True)
def detect_market_shock(ticker: Ticker, as_of: AsOf) -> ToolResult:
    """Whether the session's move was unusually large: benchmark-relative return and volume
    versus its prior 20-session median, with an explicit threshold flag."""
    return market_tools.detect_market_shock(_runtime(), ticker, as_of)


@mcp.tool(annotations=read_only, structured_output=True)
def find_historical_analogues(
    ticker: Ticker,
    as_of: AsOf,
    top_k: Annotated[int, Field(ge=1, le=10)] = 5,
    scope: Literal["same_ticker", "all_targets"] = "same_ticker",
) -> ToolResult:
    """Earlier sessions whose signed return, benchmark-relative return, and volume ratio were
    most similar to the latest session. Use scope=all_targets to search other companies too."""
    return market_tools.find_historical_analogues(_runtime(), ticker, as_of, top_k, scope)


@mcp.tool(annotations=read_only, structured_output=True)
def map_comovement(
    ticker: Ticker,
    as_of: AsOf,
    lookback_sessions: Annotated[int, Field(ge=20, le=250)] = 60,
    threshold: Annotated[float, Field(ge=0.2, le=0.95)] = 0.5,
) -> ToolResult:
    """Instruments linked to the ticker by prior return correlation (within two links), and how
    each moved on the latest session. Shows common exposure, not causation."""
    return market_tools.map_comovement(_runtime(), ticker, as_of, lookback_sessions, threshold)


@mcp.tool(annotations=read_only, structured_output=True)
def predict_volatility_risk(ticker: Ticker, as_of: AsOf) -> ToolResult:
    """Estimated annualized realized volatility over the next five sessions from an XGBoost model
    trained before the cutoff. Not a price forecast."""
    return market_tools.predict_volatility_risk(_runtime(), ticker, as_of)


@mcp.tool(annotations=read_only, structured_output=True)
def search_news(
    ticker: Ticker,
    as_of: AsOf,
    query: Annotated[str, Field(min_length=1, max_length=500)],
    top_k: Annotated[int, Field(ge=1, le=10)] = 5,
    lookback_days: Annotated[int, Field(ge=1, le=1500)] = 90,
) -> ToolResult:
    """Filings, company releases, and news available before the cutoff, ranked by semantic
    similarity to the query. Use lookback_days=1 for same-day sources."""
    return document_tools.search_news(_runtime(), ticker, as_of, query, top_k, lookback_days)


@mcp.tool(annotations=read_only, structured_output=True)
def project_news_topics(
    ticker: Ticker,
    as_of: AsOf,
    max_documents: Annotated[int, Field(ge=3, le=99)] = 24,
    dimensions: Literal[2, 3] = 2,
) -> ToolResult:
    """Map the newest documents available at the cutoff by embedding similarity (cuML UMAP)."""
    return document_tools.project_news_topics(_runtime(), ticker, as_of, max_documents, dimensions)


@mcp.resource(
    "market://dashboard/{ticker}/{as_of}",
    name="market-dashboard",
    description="Watchlist, price series, and recent documents for a date.",
    mime_type="application/json",
)
def dashboard_resource(ticker: str, as_of: str) -> dict[str, object]:
    return build_dashboard(_runtime(), ticker, as_of)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"service": "tools", **state}, status_code=200 if state["ready"] else 503)


app = mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0", stateless_http=True)
