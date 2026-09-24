"""Point-in-time market dashboard: watchlist, price series, and recent documents."""

from __future__ import annotations

from datetime import date
from typing import Any

from .features import is_shock
from .runtime import Runtime, Timer

SERIES_SESSIONS = 63
LIMITATIONS = (
    "Market prices are a later reconstruction, not an archived-at-cutoff feed.",
    "Most filings are metadata only; news items are curated one-sentence summaries.",
)


class DashboardError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _watch_row(runtime: Runtime, ticker: str, session_date: str) -> dict[str, Any]:
    rows = runtime.market.rows(ticker, session_date, 1)
    row = rows[-1] if rows and rows[-1]["session_date"] == session_date else None
    required, _ = runtime.market.benchmarks(ticker)
    benchmark = required[0] if required else "SPY"
    bench = runtime.market.rows(benchmark, session_date, 1)
    bench_return = bench[-1]["return_1d_pct"] if bench and bench[-1]["session_date"] == session_date else None
    own = row["return_1d_pct"] if row else None
    relative = None if own is None or bench_return is None else own - bench_return
    return {
        "ticker": ticker,
        "available": row is not None,
        "close": row["adjusted_close"] if row else None,
        "return_1d_pct": own,
        "return_5d_pct": row["return_5d_pct"] if row else None,
        "opening_gap_pct": row["opening_gap_pct"] if row else None,
        "volume_ratio": row["volume_ratio"] if row else None,
        "benchmark": benchmark,
        "benchmark_return_1d_pct": bench_return,
        "benchmark_relative_return_pp": relative,
        "is_shock": is_shock(relative, row["volume_ratio"] if row else None),
    }


def _series(runtime: Runtime, ticker: str, benchmark: str, session_date: str) -> list[dict[str, Any]]:
    own = {row["session_date"]: row for row in runtime.market.rows(ticker, session_date, SERIES_SESSIONS)}
    ref = {row["session_date"]: row for row in runtime.market.rows(benchmark, session_date, SERIES_SESSIONS)}
    sessions = sorted(set(own) & set(ref))
    if not sessions:
        return []
    first_own, first_ref = own[sessions[0]]["adjusted_close"], ref[sessions[0]]["adjusted_close"]
    return [
        {
            "session_date": day,
            "ticker_close": own[day]["adjusted_close"],
            "benchmark_close": ref[day]["adjusted_close"],
            "ticker_return_pct": (own[day]["adjusted_close"] / first_own - 1) * 100,
            "benchmark_return_pct": (ref[day]["adjusted_close"] / first_ref - 1) * 100,
            "volume": own[day]["volume"],
        }
        for day in sessions
    ]


def build_dashboard(runtime: Runtime, ticker: str, as_of: str) -> dict[str, Any]:
    timer = Timer()
    scenario = runtime.scenario
    if ticker not in scenario.targets:
        raise DashboardError("unsupported_ticker")
    try:
        requested = date.fromisoformat(as_of).isoformat()
    except ValueError as exc:
        raise DashboardError("invalid_date") from exc
    if not runtime.market.in_coverage(requested):
        raise DashboardError("outside_coverage")
    session = next(item for item in reversed(scenario.sessions) if item.session_date <= requested)
    watchlist = [_watch_row(runtime, symbol, session.session_date) for symbol in scenario.targets]
    selected = next(row for row in watchlist if row["ticker"] == ticker)
    documents = scenario.eligible_documents(ticker, session.close_at)[:4]
    return {
        "requested_as_of": requested,
        "resolved_session": session.session_date,
        "cutoff_at": session.close_at.isoformat().replace("+00:00", "Z"),
        "selected_ticker": ticker,
        "coverage": {
            "first_session": runtime.market.first_session,
            "last_session": runtime.market.last_session,
            "scenario_id": scenario.scenario_id,
        },
        "watchlist": watchlist,
        "series": {
            "ticker": ticker,
            "benchmark": selected["benchmark"],
            "points": _series(runtime, ticker, selected["benchmark"], session.session_date),
        },
        "documents": [
            {
                "title": row["title"],
                "url": row["canonical_url"],
                "source_type": row["source_type"],
                "content_scope": row["content_scope"],
                "available_at": row["available_at"],
                "excerpt": row["text"][:600],
            }
            for row in documents
        ],
        "receipt": timer.receipt(runtime, "cudf").model_dump(mode="json"),
        "limitations": list(LIMITATIONS),
    }
