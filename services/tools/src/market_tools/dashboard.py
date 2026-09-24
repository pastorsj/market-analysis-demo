"""Truthful point-in-time projection for the read-only dashboard resource."""

from __future__ import annotations

from datetime import date, datetime, UTC
import math
import time
from typing import Any


TARGETS = ("NVDA", "AMD", "JPM", "GS", "SCHW")
OPERATIONS = {"get_price_context", "detect_market_shock", "search_news", "market_series"}
SOURCE_TYPES = {"market", "news", "filing", "release", "relationship", "model"}


class DashboardProjectionError(RuntimeError):
    """Stable failure code for an invalid or unavailable dashboard projection."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _wire(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        result = value.model_dump(mode="json")
        if isinstance(result, dict):
            return result
    raise DashboardProjectionError("invalid_tool_result")


def _utc(value: object, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise DashboardProjectionError(code) from exc
    if parsed.tzinfo is None:
        raise DashboardProjectionError(code)
    return parsed.astimezone(UTC)


def _z(value: object, code: str = "invalid_timestamp") -> str:
    return _utc(value, code).isoformat().replace("+00:00", "Z")


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DashboardProjectionError("invalid_numeric_value")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DashboardProjectionError("invalid_numeric_value") from exc
    if not math.isfinite(result):
        raise DashboardProjectionError("invalid_numeric_value")
    return result


def _receipt(result: dict[str, Any], operation: str, ticker: str) -> dict[str, object]:
    value = _wire(result.get("receipt"))
    if (
        operation not in OPERATIONS
        or not isinstance(value.get("engine"), str)
        or not isinstance(value.get("device"), str)
    ):
        raise DashboardProjectionError("invalid_receipt")
    duration = _number(value.get("duration_ms"))
    if (
        duration is None
        or duration < 0
        or type(value.get("gpu_executed")) is not bool
        or value.get("fallback_used") is not False
        or (value["engine"] == "deterministic") == value["gpu_executed"]
    ):
        raise DashboardProjectionError("invalid_receipt")
    return {
        "operation": operation,
        "ticker": ticker,
        "engine": value["engine"],
        "device": value["device"],
        "gpu_executed": value["gpu_executed"],
        "fallback_used": False,
        "duration_ms": duration,
    }


def _limitations(result: dict[str, Any]) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for row in result.get("limitations", []):
        item = _wire(row)
        affected = item.get("affected", [])
        if (
            isinstance(item.get("code"), str)
            and isinstance(item.get("message"), str)
            and isinstance(affected, list)
            and all(isinstance(value, str) for value in affected)
        ):
            values.append({"code": item["code"], "message": item["message"], "affected": affected})
    return values


def _merge_limitations(values: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for row in values:
        current = merged.setdefault(
            str(row["code"]), {"code": row["code"], "message": row["message"], "affected": []}
        )
        if row["message"] not in str(current["message"]):
            current["message"] = f"{current['message']} {row['message']}"
        current["affected"] = list(dict.fromkeys([*current["affected"], *row["affected"]]))
    return list(merged.values())[:20]


def _result(engine: object, operation: str, ticker: str, cutoff: datetime) -> dict[str, Any]:
    if operation == "get_price_context":
        value = engine.get_price_context(ticker, cutoff)
    elif operation == "detect_market_shock":
        value = engine.detect_market_shock(ticker, cutoff)
    else:
        value = engine.search_news(ticker, cutoff, "material company developments and market context", 4)
    result = _wire(value)
    if result.get("outcome") not in {"ok", "partial", "no_data"}:
        raise DashboardProjectionError("invalid_tool_result")
    return result


def _series(
    engine: object, ticker: str, benchmark: str, cutoff: datetime, resolved: str
) -> tuple[dict, list[dict]]:
    store = engine.bundle.store
    windows, receipts = {}, []
    for symbol, fields in ((ticker, ("adjusted_close", "volume")), (benchmark, ("adjusted_close",))):
        started = time.perf_counter()
        window = store.window(symbol, cutoff, lookback=63, fields=fields)
        duration = (time.perf_counter() - started) * 1000
        if (
            window.status != "ready"
            or not window.rows
            or not window.gpu_executed
            or window.resolved_session != resolved
        ):
            raise DashboardProjectionError("series_gpu_unavailable")
        if any(
            str(row.get("session_date", "")) > resolved
            or _utc(row.get("bar_end"), "invalid_series_time") > cutoff
            for row in window.rows
        ):
            raise DashboardProjectionError("future_series_value")
        windows[symbol] = {row["session_date"]: row for row in window.rows}
        receipts.append(
            {
                "operation": "market_series",
                "ticker": symbol,
                "engine": "cudf",
                "device": str(engine.device),
                "gpu_executed": True,
                "fallback_used": False,
                "duration_ms": duration,
            }
        )
    sessions = sorted(set(windows[ticker]) & set(windows[benchmark]))[-63:]
    if not sessions:
        raise DashboardProjectionError("series_unavailable")
    first_ticker = _number(windows[ticker][sessions[0]].get("adjusted_close"))
    first_benchmark = _number(windows[benchmark][sessions[0]].get("adjusted_close"))
    if first_ticker is None or first_benchmark is None or first_ticker <= 0 or first_benchmark <= 0:
        raise DashboardProjectionError("invalid_series_value")
    points = []
    for session in sessions:
        target, reference = windows[ticker][session], windows[benchmark][session]
        close, benchmark_close = (
            _number(target.get("adjusted_close")),
            _number(reference.get("adjusted_close")),
        )
        if close is None or benchmark_close is None or close <= 0 or benchmark_close <= 0:
            raise DashboardProjectionError("invalid_series_value")
        volume = _number(target.get("volume"))
        points.append(
            {
                "session_date": session,
                "ticker_close": close,
                "benchmark_close": benchmark_close,
                "ticker_normalized_return_pct": (close / first_ticker - 1) * 100,
                "benchmark_normalized_return_pct": (benchmark_close / first_benchmark - 1) * 100,
                "volume": volume,
            }
        )
    return {"ticker": ticker, "benchmark": benchmark, "points": points}, receipts


def _evidence(result: dict[str, Any], cutoff: datetime) -> list[dict[str, object]]:
    values = []
    for item in result.get("citations", [])[:4]:
        row = _wire(item)
        available = _utc(row.get("available_at"), "invalid_evidence_time")
        if available > cutoff:
            raise DashboardProjectionError("future_evidence")
        url, source_type = str(row.get("url", "")), row.get("source_type")
        if not url.startswith("https://") or source_type not in SOURCE_TYPES:
            raise DashboardProjectionError("invalid_evidence")
        if not all(
            isinstance(row.get(key), str) and row[key]
            for key in ("citation_id", "evidence_id", "title", "excerpt")
        ):
            raise DashboardProjectionError("invalid_evidence")
        values.append(
            {
                "citation_id": row["citation_id"],
                "title": row["title"],
                "url": url,
                "source_type": source_type,
                "published_at": _z(row.get("published_at"), "invalid_evidence_time"),
                "available_at": available.isoformat().replace("+00:00", "Z"),
                "excerpt": row["excerpt"],
            }
        )
    return values


def build_dashboard(engine: object, ticker: str, as_of: str) -> dict[str, object]:
    """Build one exact dashboard response from verified tools and market rows."""
    if ticker not in TARGETS:
        raise DashboardProjectionError("unsupported_ticker")
    try:
        requested = date.fromisoformat(as_of)
    except (TypeError, ValueError) as exc:
        raise DashboardProjectionError("invalid_date") from exc
    store = engine.bundle.store
    sessions = store.sessions
    first, last = sessions[0]["session_date"], sessions[-1]["session_date"]
    if not first <= requested.isoformat() <= last:
        raise DashboardProjectionError("outside_coverage")
    session = next((row for row in reversed(sessions) if row["session_date"] <= requested.isoformat()), None)
    if session is None:
        raise DashboardProjectionError("outside_coverage")
    resolved, cutoff = session["session_date"], _utc(session["close_at"], "invalid_session_time")
    receipts, limits, watchlist = (
        [],
        [
            {
                "code": "historical_reconstruction_not_historical_vintage",
                "message": "Market prices were reconstructed later and are not an archived-at-cutoff feed.",
                "affected": ["market_data"],
            },
            {
                "code": "company_evidence_not_licensed_news",
                "message": "Company evidence excludes licensed news in this reconstruction.",
                "affected": ["company_evidence"],
            },
        ],
        [],
    )
    for symbol in TARGETS:
        price = _result(engine, "get_price_context", symbol, cutoff)
        shock = _result(engine, "detect_market_shock", symbol, cutoff)
        receipts.extend(
            (_receipt(price, "get_price_context", symbol), _receipt(shock, "detect_market_shock", symbol))
        )
        limits.extend((*_limitations(price), *_limitations(shock)))
        performance = next(
            (row for row in price.get("data", {}).get("performance", []) if row.get("ticker") == symbol), None
        )
        price_session = (performance or {}).get("resolved_session") or price.get("data", {}).get(
            "resolved_session"
        )
        if price_session != resolved:
            raise DashboardProjectionError("session_mismatch")
        required = store.benchmark_symbols(symbol)[0]
        benchmark = shock.get("data", {}).get("benchmark") or (required[0] if required else None)
        if not required or benchmark != required[0]:
            raise DashboardProjectionError("benchmark_mismatch")
        price_outcome, shock_outcome = price["outcome"], shock["outcome"]
        outcome = (
            "no_data"
            if price_outcome == "no_data"
            else "partial"
            if price_outcome != "ok" or shock_outcome != "ok"
            else "ok"
        )
        values = performance or {}
        shock_values = {} if outcome == "no_data" else shock.get("data", {})
        watchlist.append(
            {
                "ticker": symbol,
                "outcome": outcome,
                "resolved_session": price_session,
                "close": _number(values.get("close")),
                "return_1_session_pct": _number(values.get("return_1_session_pct")),
                "return_5_sessions_pct": _number(values.get("return_5_sessions_pct")),
                "opening_gap_pct": _number(values.get("opening_gap_pct")),
                "volume_ratio": _number(values.get("volume_ratio")),
                "benchmark": benchmark,
                "benchmark_return_pct": _number(shock_values.get("benchmark_return_pct")),
                "market_adjusted_return_pct": _number(shock_values.get("market_adjusted_return_pct")),
                "is_shock": shock_values.get("is_shock")
                if type(shock_values.get("is_shock")) is bool
                else None,
            }
        )
    selected = next(row for row in watchlist if row["ticker"] == ticker)
    series, series_receipts = _series(engine, ticker, str(selected["benchmark"]), cutoff, resolved)
    receipts.extend(series_receipts)
    evidence_result = _result(engine, "search_news", ticker, cutoff)
    receipts.append(_receipt(evidence_result, "search_news", ticker))
    limits.extend(_limitations(evidence_result))
    return {
        "schema_version": "market-dashboard-v1",
        "mode": "historical_reconstruction",
        "requested_as_of": requested.isoformat(),
        "resolved_session": resolved,
        "cutoff_at": cutoff.isoformat().replace("+00:00", "Z"),
        "selected_ticker": ticker,
        "coverage": {
            "first_session": first,
            "last_session": last,
            "session_count": len(sessions),
            "scenario_id": store.manifest["scenario_id"],
            "vintage_status": store.manifest["vintage_status"],
        },
        "watchlist": watchlist,
        "series": series,
        "evidence": _evidence(evidence_result, cutoff),
        "receipts": receipts,
        "limitations": _merge_limitations(limits),
    }
