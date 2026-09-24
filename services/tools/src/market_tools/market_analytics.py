from __future__ import annotations

from typing import Any

from .market_store import MarketWindow


def _number(value: object) -> float | None:
    return None if value is None else float(value)


def _integer(value: object) -> int | None:
    return None if value is None else int(value)


def _scalar(value: object) -> float | None:
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def _price_result(
    window: MarketWindow,
    current: dict,
    one: float | None,
    five: float | None,
    gap: float | None,
    baseline: float | None,
    baseline_sessions: int,
    ratio: float | None,
    prior_close: float | None,
    intraday: float | None,
) -> dict[str, Any]:
    adjusted = {
        name: _number(current.get(name))
        for name in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close")
    }
    volume = _integer(current.get("volume"))
    return {
        "ticker": window.ticker,
        "resolved_session": window.resolved_session,
        **adjusted,
        "volume": volume,
        "return_1_session_pct": one,
        "return_5_sessions_pct": five,
        "opening_gap_pct": gap,
        "prior_adjusted_close": prior_close,
        "open_to_close_return_pct": intraday,
        "volume_baseline_median": baseline,
        "volume_baseline_sessions": baseline_sessions,
        "volume_ratio": ratio,
        "open": adjusted["adjusted_open"],
        "high": adjusted["adjusted_high"],
        "low": adjusted["adjusted_low"],
        "close": adjusted["adjusted_close"],
        "change_pct": one,
        "price_basis": "provider_adjusted",
        "vintage_status": window.vintage_status,
    }


def gpu_price_performance(window: MarketWindow) -> dict[str, Any] | None:
    """Compute every derived numeric price fact in cuDF; never host-fallback."""
    if window.status != "ready" or not window.rows:
        return None
    import cudf

    rows = window.rows
    current = rows[-1]
    frame = cudf.DataFrame(
        {
            key: [_number(row.get(key)) for row in rows]
            for key in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "volume")
        }
    )
    closes = frame["adjusted_close"]
    prior_1, prior_5 = closes.shift(1), closes.shift(5)
    prior_close = _scalar(prior_1.iloc[-1])
    intraday = None
    # Close-only benchmarks have all-null opens, which cuDF can infer as strings.
    if current.get("adjusted_open") is not None:
        opens = frame["adjusted_open"].where(frame["adjusted_open"] != 0.0)
        intraday = _scalar(((closes / opens - 1.0) * 100.0).iloc[-1])
    prior_1, prior_5 = prior_1.where(prior_1 != 0.0), prior_5.where(prior_5 != 0.0)
    returns_1 = (closes / prior_1 - 1.0) * 100.0
    returns_5 = (closes / prior_5 - 1.0) * 100.0
    one = _scalar(returns_1.iloc[-1])
    five = _scalar(returns_5.iloc[-1])
    gap = None
    if len(rows) > 1 and current.get("adjusted_open") is not None:
        gap = _scalar(((frame["adjusted_open"] / prior_1 - 1.0) * 100.0).iloc[-1])
    history = frame["volume"].iloc[:-1].tail(20).dropna()
    baseline = None if len(history) == 0 else _scalar(history.median())
    ratio = None if baseline in {None, 0.0} else _scalar((frame["volume"] / baseline).iloc[-1])
    return _price_result(
        window, current, one, five, gap, baseline, len(history), ratio, prior_close, intraday
    )


def gpu_shock_metrics(target: MarketWindow, benchmark: MarketWindow) -> dict[str, Any] | None:
    if target.status != "ready" or len(target.rows) < 2:
        return None
    target_values = gpu_price_performance(target)
    benchmark_values = (
        gpu_price_performance(benchmark) if benchmark.status == "ready" and len(benchmark.rows) >= 2 else None
    )
    target_return = target_values["return_1_session_pct"]
    benchmark_return = None if benchmark_values is None else benchmark_values["return_1_session_pct"]
    abnormal = None if target_return is None or benchmark_return is None else target_return - benchmark_return
    ratio = target_values["volume_ratio"]
    return {
        "return_pct": target_return,
        "benchmark_return_pct": benchmark_return,
        "market_adjusted_return_pct": abnormal,
        "volume_ratio": ratio,
        "volume_baseline_sessions": target_values["volume_baseline_sessions"],
        "detector_rule": {
            "absolute_market_adjusted_return_pp_threshold": 5.0,
            "volume_ratio_threshold": 2.0,
            "combination": "or",
            "requires_benchmark_return": True,
        },
        "is_shock": None
        if abnormal is None
        else abs(abnormal) >= 5.0 or bool(ratio is not None and ratio >= 2.0),
    }
