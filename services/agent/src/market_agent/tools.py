"""The seven evidence tools the agent can call, bound to the turn's scope.

Each tool forwards to the MCP tools service. The wrapper, not the model, supplies
the cutoff, checks the ticker is in scope, and records the typed result in the
turn's ledger. Results with evidence dated after the cutoff are rejected.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from mcp import Client

from .config import TOOLS_URL
from .context import ToolResult, TurnContext
from .schemas import ToolSummary

MAX_CALLS_PER_TURN = 16
MARKET_TOOLS = {
    "get_price_context",
    "detect_market_shock",
    "find_historical_analogues",
    "map_comovement",
    "predict_volatility_risk",
}


async def mcp_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with Client(TOOLS_URL, read_timeout_seconds=90) as client:
        result = await client.call_tool(name, arguments)
    if result.is_error or not isinstance(result.structured_content, dict):
        raise RuntimeError(f"{name} failed in the tools service")
    return result.structured_content


def _compact(value: Any, depth: int = 0) -> Any:
    """Bound what the model sees; the full result stays in the ledger."""
    if isinstance(value, str):
        return value if len(value) <= 600 else value[:597] + "..."
    if isinstance(value, dict):
        return (
            {key: _compact(item, depth + 1) for key, item in list(value.items())[:30]} if depth < 4 else "…"
        )
    if isinstance(value, list):
        return [_compact(item, depth + 1) for item in value[:12]] if depth < 4 else "…"
    return value


def _for_model(result: ToolResult) -> dict[str, Any]:
    return {
        "outcome": result.outcome,
        "data": _compact(result.data),
        "sources": [
            {
                "citation_id": item.citation_id,
                "title": item.title,
                "source_type": item.source_type,
                "available_at": item.available_at.isoformat(),
                "excerpt": item.excerpt[:400],
            }
            for item in result.citations[:12]
        ],
        "limitations": [item.message for item in result.limitations],
        "warnings": result.warnings,
    }


async def call_tool(context: TurnContext, name: str, ticker: str, **arguments: Any) -> dict[str, Any]:
    """Run one scoped tool call and return a compact, citable view of the result."""
    scope = context.scope
    ticker = ticker.strip().upper()
    if scope.status != "resolved" or scope.as_of is None:
        return {"outcome": "blocked", "message": "No company and date are set yet; ask the user for them."}
    if ticker not in scope.members:
        return {
            "outcome": "blocked",
            "message": f"{ticker} is outside this investigation. Allowed: {', '.join(scope.members)}.",
        }
    as_of = context.market_cutoff if name in MARKET_TOOLS and context.market_cutoff else scope.as_of
    request = {"ticker": ticker, "as_of": as_of.isoformat(), **arguments}
    key = json.dumps([name, request], sort_keys=True)
    async with context.lock:
        if key in context.cache:
            return context.cache[key]
        if len(context.cache) >= MAX_CALLS_PER_TURN:
            return {"outcome": "blocked", "message": "The evidence-call limit for this turn was reached."}
        context.cache[key] = {"outcome": "pending"}
        span = f"tool-{len(context.cache)}"
    await context.progress(
        span=span, kind="tool", name=name, state="running", detail={"ticker": ticker, **arguments}
    )
    try:
        result = ToolResult.model_validate(await mcp_call(name, request))
        late = [item.citation_id for item in result.citations if item.available_at > scope.as_of]
        if late:
            raise ValueError(f"{name} returned evidence dated after the cutoff")
    except Exception as exc:
        context.ledger.failures.append(
            ToolSummary(tool=name, ticker=ticker, outcome="failed", summary=str(exc))
        )
        await context.progress(span=span, kind="tool", name=name, state="failed", detail={"error": str(exc)})
        view = {"outcome": "failed", "message": f"{name} failed; continue with other evidence."}
    else:
        context.ledger.results.append((ticker, result))
        receipt = result.receipt.model_dump() if result.receipt else {}
        await context.progress(
            span=span,
            kind="tool",
            name=name,
            state="succeeded",
            detail={
                "ticker": ticker,
                "outcome": result.outcome,
                "citations": len(result.citations),
                **receipt,
            },
        )
        view = _for_model(result)
    context.cache[key] = view
    return view


# The tools the agent sees. Docstrings are the descriptions shown to the model.


@tool
async def get_price_context(ticker: str, runtime: ToolRuntime) -> dict:
    """Price, daily/5-day returns, opening gap, intraday return, volume ratio, and sector
    benchmarks for the investigation's market session."""
    return await call_tool(runtime.context, "get_price_context", ticker)


@tool
async def detect_market_shock(ticker: str, runtime: ToolRuntime) -> dict:
    """Whether the session's move was unusually large versus its benchmark and normal volume."""
    return await call_tool(runtime.context, "detect_market_shock", ticker)


@tool
async def search_news(
    ticker: str, query: str, runtime: ToolRuntime, top_k: int = 5, lookback_days: int = 90
) -> dict:
    """Filings, company releases, and news available before the cutoff, ranked by similarity
    to `query`. Use lookback_days=1 for same-day sources (max 1500)."""
    return await call_tool(
        runtime.context,
        "search_news",
        ticker,
        query=query[:500],
        top_k=max(1, min(top_k, 10)),
        lookback_days=max(1, min(lookback_days, 1500)),
    )


@tool
async def find_historical_analogues(
    ticker: str,
    runtime: ToolRuntime,
    top_k: int = 5,
    scope: Literal["same_ticker", "all_targets"] = "same_ticker",
) -> dict:
    """Earlier sessions most similar to this session by signed return, benchmark-relative return,
    and volume ratio. scope=all_targets also searches the other companies."""
    return await call_tool(
        runtime.context, "find_historical_analogues", ticker, top_k=max(1, min(top_k, 10)), scope=scope
    )


@tool
async def map_comovement(ticker: str, runtime: ToolRuntime, lookback_sessions: int = 60) -> dict:
    """Instruments that usually move with the ticker (prior return correlation, up to two links)
    and how each moved on the session. Shows shared exposure, not causation."""
    return await call_tool(
        runtime.context, "map_comovement", ticker, lookback_sessions=max(20, min(lookback_sessions, 250))
    )


@tool
async def predict_volatility_risk(ticker: str, runtime: ToolRuntime) -> dict:
    """Estimated annualized volatility over the next five sessions from a model trained before
    the cutoff. Not a price forecast."""
    return await call_tool(runtime.context, "predict_volatility_risk", ticker)


@tool
async def project_news_topics(ticker: str, runtime: ToolRuntime, max_documents: int = 24) -> dict:
    """A 2-D map of the newest documents available at the cutoff, grouped by embedding similarity."""
    return await call_tool(
        runtime.context, "project_news_topics", ticker, max_documents=max(3, min(max_documents, 99))
    )


EVIDENCE_TOOLS = [
    get_price_context,
    detect_market_shock,
    search_news,
    find_historical_analogues,
    map_comovement,
    predict_volatility_risk,
    project_news_topics,
]
