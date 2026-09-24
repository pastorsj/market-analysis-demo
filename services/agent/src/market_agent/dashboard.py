"""Same-origin, failure-closed dashboard API."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request


router = APIRouter()
_UNAVAILABLE = "Historical dashboard data is temporarily unavailable. Try again in a moment."


@router.get("/api/dashboard")
async def get_dashboard(
    request: Request,
    ticker: str = Query("NVDA", min_length=2, max_length=4),
    as_of: date | None = Query(None),
) -> dict[str, object]:
    catalog = request.app.state.catalog
    symbol = ticker.upper()
    if ticker != symbol or symbol not in catalog.targets:
        raise HTTPException(status_code=422, detail="Choose one of the supported company tickers.")
    requested = as_of or catalog.sessions[-1].session_date
    if requested < catalog.sessions[0].session_date or requested > catalog.sessions[-1].session_date:
        raise HTTPException(status_code=422, detail="Choose a date within the available historical coverage.")
    session = catalog.resolve_completed_session(requested)
    if session is None:
        raise HTTPException(status_code=422, detail="No completed market session is available for that date.")
    try:
        value = await request.app.state.market_tool_client.read_dashboard(symbol, requested.isoformat())
        coverage = value["coverage"]
        expected_cutoff = session.close_at.isoformat().replace("+00:00", "Z")
        if (
            value["resolved_session"] != session.session_date.isoformat()
            or value["cutoff_at"] != expected_cutoff
            or coverage["scenario_id"] != catalog.scenario_id
            or coverage["first_session"] != catalog.sessions[0].session_date.isoformat()
            or coverage["last_session"] != catalog.sessions[-1].session_date.isoformat()
            or coverage["session_count"] != len(catalog.sessions)
            or coverage["vintage_status"] != catalog.vintage_status
        ):
            raise ValueError("dashboard coverage mismatch")
        return value
    except Exception as exc:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE) from exc
