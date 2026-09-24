"""Market tools: price context, shock detection, analogues, co-movement, volatility risk."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from . import evidence
from .features import (
    SHOCK_RETURN_THRESHOLD_PP,
    SHOCK_VOLUME_RATIO_THRESHOLD,
    correlation_edges,
    is_shock,
    select_analogues,
)
from .models import Artifact, ToolResult, stable_id
from .runtime import RECONSTRUCTION_WARNING, Runtime, Timer, coverage, limitation, no_data, result

PRICE_FIELDS = (
    "adjusted_open",
    "adjusted_high",
    "adjusted_low",
    "adjusted_close",
    "volume",
    "return_1d_pct",
    "return_5d_pct",
    "opening_gap_pct",
    "open_to_close_pct",
    "volume_baseline_median",
    "volume_ratio",
)


def _fmt(value: float | None, pattern: str, missing: str = "unavailable") -> str:
    return missing if value is None else format(value, pattern)


def _bar(runtime: Runtime, ticker: str, session_date: str, count: int = 1) -> list[dict[str, Any]]:
    """Bars ending exactly at ``session_date``; empty if that session has no bar."""
    rows = runtime.market.rows(ticker, session_date, count)
    return rows if rows and rows[-1]["session_date"] == session_date else []


def _describe(row: dict[str, Any]) -> str:
    return (
        f"{row['instrument_id']} on {row['session_date']}: close {_fmt(row['adjusted_close'], '.2f')}, "
        f"daily return {_fmt(row['return_1d_pct'], '+.2f')}%, "
        f"opening gap {_fmt(row['opening_gap_pct'], '+.2f')}%, "
        f"open-to-close {_fmt(row['open_to_close_pct'], '+.2f')}%, "
        f"five-session return {_fmt(row['return_5d_pct'], '+.2f')}%, "
        f"volume {_fmt(row['volume'], ',.0f')} shares "
        f"({_fmt(row['volume_ratio'], '.2f')}x the prior 20-session median)."
    )


def _resolve(runtime: Runtime, tool: str, ticker: str, as_of: datetime) -> tuple[str, dict] | ToolResult:
    session = runtime.session(as_of)
    if session is None:
        return no_data(
            tool, as_of, ticker, "market_window", "outside_coverage", "The cutoff is outside market coverage."
        )
    rows = _bar(runtime, ticker, session.session_date)
    if not rows:
        return no_data(
            tool,
            as_of,
            ticker,
            "instrument",
            "no_bar_for_session",
            f"No {ticker} bar exists for the completed session {session.session_date}.",
        )
    return session.session_date, rows[-1]


def get_price_context(runtime: Runtime, ticker: str, as_of: datetime) -> ToolResult:
    timer = Timer()
    resolved = _resolve(runtime, "get_price_context", ticker, as_of)
    if isinstance(resolved, ToolResult):
        return resolved
    session_date, target = resolved
    required, optional = runtime.market.benchmarks(ticker)
    optional = tuple(symbol for symbol in optional if symbol in runtime.market.instruments)
    pairs = [evidence.market_row(runtime.scenario, target, _describe(target))]
    items = [coverage("instrument", ticker, 1, 1)]
    limits, benchmarks = [], []
    for symbol, is_required in [(item, True) for item in required] + [(item, False) for item in optional]:
        rows = _bar(runtime, symbol, session_date)
        items.append(coverage("instrument", symbol, len(rows), 1, is_required))
        if not rows:
            code = "required_benchmark_unavailable" if is_required else "optional_benchmark_unavailable"
            limits.append(limitation(code, f"{symbol} has no bar for {session_date}.", symbol))
            continue
        pairs.append(evidence.market_row(runtime.scenario, rows[-1], _describe(rows[-1])))
        benchmarks.append(
            {"ticker": symbol, "required": is_required, **{k: rows[-1][k] for k in PRICE_FIELDS}}
        )
    actions = runtime.known_actions(ticker, as_of)
    pairs.extend(evidence.corporate_action(runtime.scenario, row) for row in actions)
    data = {
        "summary": _describe(target),
        "resolved_session": session_date,
        "ticker": ticker,
        **{key: target[key] for key in PRICE_FIELDS},
        "benchmarks": benchmarks,
        "corporate_actions_published_by_cutoff": actions,
        "price_basis": "provider_adjusted",
    }
    return result(
        "get_price_context",
        as_of,
        "partial" if limits else "ok",
        data,
        items,
        limits,
        receipt=timer.receipt(runtime, "cudf"),
        pairs=pairs,
        warnings=[RECONSTRUCTION_WARNING],
    )


def detect_market_shock(runtime: Runtime, ticker: str, as_of: datetime) -> ToolResult:
    timer = Timer()
    resolved = _resolve(runtime, "detect_market_shock", ticker, as_of)
    if isinstance(resolved, ToolResult):
        return resolved
    session_date, target = resolved
    required, _ = runtime.market.benchmarks(ticker)
    benchmark = required[0] if required else "SPY"
    bench_rows = _bar(runtime, benchmark, session_date)
    bench_return = bench_rows[-1]["return_1d_pct"] if bench_rows else None
    own_return, ratio = target["return_1d_pct"], target["volume_ratio"]
    abnormal = None if own_return is None or bench_return is None else own_return - bench_return
    flag = is_shock(abnormal, ratio)
    limits = []
    if bench_return is None:
        limits.append(
            limitation("benchmark_unavailable", f"{benchmark} has no return for {session_date}.", benchmark)
        )
    if ratio is None:
        limits.append(
            limitation("insufficient_volume_history", "Fewer than 20 prior sessions of volume.", ticker)
        )
    summary = (
        f"{ticker} returned {_fmt(own_return, '+.2f')}% on {session_date} versus {benchmark} "
        f"{_fmt(bench_return, '+.2f')}%, a benchmark-relative move of {_fmt(abnormal, '+.2f')} "
        f"percentage points, on {_fmt(ratio, '.2f')}x its prior 20-session median volume. "
        f"Detector flag is_shock={flag}: |benchmark-relative return| >= {SHOCK_RETURN_THRESHOLD_PP:g} pp "
        f"or volume ratio >= {SHOCK_VOLUME_RATIO_THRESHOLD:g}. The flag measures size, not cause."
    )
    pairs = [evidence.market_row(runtime.scenario, target, _describe(target))]
    if bench_rows:
        pairs.append(evidence.market_row(runtime.scenario, bench_rows[-1], _describe(bench_rows[-1])))
    data = {
        "summary": summary,
        "resolved_session": session_date,
        "return_1d_pct": own_return,
        "benchmark": benchmark,
        "benchmark_return_1d_pct": bench_return,
        "benchmark_relative_return_pp": abnormal,
        "volume_ratio": ratio,
        "is_shock": flag,
        "detector_rule": {
            "abs_benchmark_relative_return_pp_at_least": SHOCK_RETURN_THRESHOLD_PP,
            "volume_ratio_at_least": SHOCK_VOLUME_RATIO_THRESHOLD,
            "combination": "or",
        },
    }
    items = [coverage("instrument", ticker, 1, 1), coverage("instrument", benchmark, len(bench_rows), 1)]
    if ratio is None:
        items.append(coverage("market_window", f"{ticker}:volume_baseline", 0, 1, required=False))
    return result(
        "detect_market_shock",
        as_of,
        "partial" if limits else "ok",
        data,
        items,
        limits,
        receipt=timer.receipt(runtime, "cudf"),
        pairs=pairs,
        warnings=[RECONSTRUCTION_WARNING],
    )


def _analogue_frame(runtime: Runtime, tickers: list[str], session_date: str, *, inclusive: bool) -> Any:
    """Feature rows for ``tickers`` up to ``session_date``, joined to each ticker's benchmark return."""
    frames = []
    for ticker in tickers:
        history = runtime.market.history(ticker, session_date, inclusive=inclusive)
        required, _ = runtime.market.benchmarks(ticker)
        bench = runtime.market.history(required[0] if required else "SPY", session_date, inclusive=inclusive)
        bench = bench[["session_date", "return_1d_pct"]].rename(
            columns={"return_1d_pct": "benchmark_return_pct"}
        )
        frames.append(history.merge(bench, on="session_date", how="inner"))
    frame = runtime.cudf.concat(frames, ignore_index=True)
    frame["relative_return_pp"] = frame["return_1d_pct"] - frame["benchmark_return_pct"]
    return frame


def _feature_matrix(runtime: Runtime, frame: Any) -> Any:
    cp = runtime.cp
    ratio = frame["volume_ratio"].astype("float64").to_cupy(na_value=cp.nan)
    return cp.stack(
        [
            frame["return_1d_pct"].astype("float64").to_cupy(na_value=cp.nan),
            frame["relative_return_pp"].astype("float64").to_cupy(na_value=cp.nan),
            cp.log(cp.where(ratio > 0, ratio, cp.nan)),
        ],
        axis=1,
    )


def find_historical_analogues(
    runtime: Runtime, ticker: str, as_of: datetime, top_k: int = 5, scope: str = "same_ticker"
) -> ToolResult:
    timer = Timer()
    tool = "find_historical_analogues"
    resolved = _resolve(runtime, tool, ticker, as_of)
    if isinstance(resolved, ToolResult):
        return resolved
    session_date, target = resolved
    tickers = [ticker] if scope == "same_ticker" else list(runtime.scenario.targets)
    target_frame = _analogue_frame(runtime, [ticker], session_date, inclusive=True)
    target_frame = target_frame[target_frame["session_date"] == session_date]
    candidates = _analogue_frame(runtime, tickers, session_date, inclusive=False)
    query = _feature_matrix(runtime, target_frame)
    if len(target_frame) != 1 or bool(runtime.cp.isnan(query).any()):
        return no_data(
            tool,
            as_of,
            ticker,
            "analogue_candidates",
            "target_features_unavailable",
            "The target session lacks a return, benchmark return, or 20-session volume baseline.",
        )
    picks = select_analogues(
        runtime.cp,
        _feature_matrix(runtime, candidates),
        query[0],
        candidates["position"].to_cupy(),
        int(target["position"]),
        top_k=top_k,
    )
    target_relative = float(target_frame["relative_return_pp"].iloc[0])
    rows = candidates.to_pandas().to_dict("records")
    analogues, pairs = [], [evidence.market_row(runtime.scenario, target, _describe(target))]
    for index, distance in picks:
        row = {
            key: (None if isinstance(v, float) and not math.isfinite(v) else v)
            for key, v in rows[index].items()
        }
        pairs.append(evidence.market_row(runtime.scenario, row, _describe(row)))
        analogues.append(
            {
                "ticker": row["instrument_id"],
                "session_date": row["session_date"],
                "return_1d_pct": row["return_1d_pct"],
                "benchmark_relative_return_pp": row["relative_return_pp"],
                "volume_ratio": row["volume_ratio"],
                "distance": round(distance, 4),
                "same_direction": (row["return_1d_pct"] > 0) == (target["return_1d_pct"] > 0),
                "evidence_id": pairs[-1][0].evidence_id,
            }
        )
    method = (
        "Standardized Euclidean distance over signed daily return, benchmark-relative return, and "
        "log volume ratio. Standardization uses only sessions before the target. Sessions in the "
        "10 before the target are excluded, and picks are at least 5 sessions apart."
    )
    summary = (
        f"Found {len(analogues)} {'earlier ' + ticker if scope == 'same_ticker' else 'earlier target-universe'} "
        f"sessions most similar to {ticker} on {session_date} (return {_fmt(target['return_1d_pct'], '+.2f')}%, "
        f"benchmark-relative {target_relative:+.2f} pp, volume {_fmt(target['volume_ratio'], '.2f')}x). "
        f"{sum(item['same_direction'] for item in analogues)} of them moved in the same direction."
    )
    ranking = evidence.computed(
        runtime.scenario,
        tool,
        as_of,
        {"summary": summary, "method": method, "analogues": analogues},
        [item.evidence_id for item, _ in pairs],
    )
    table = Artifact(
        artifact_id=stable_id("artifact", runtime.scenario.scenario_id, tool, ticker, session_date, scope),
        kind="analogue_table",
        title=f"Measured analogues for {ticker} on {session_date}",
        data={"target_session": session_date, "method": method, "rows": analogues},
    )
    limits = []
    if len(analogues) < top_k:
        limits.append(
            limitation("few_analogue_candidates", f"Only {len(analogues)} candidates qualified.", ticker)
        )
    return result(
        tool,
        as_of,
        "partial" if limits else "ok",
        {
            "summary": summary,
            "method": method,
            "scope": scope,
            "target": {
                "ticker": ticker,
                "session_date": session_date,
                "return_1d_pct": target["return_1d_pct"],
                "benchmark_relative_return_pp": target_relative,
                "volume_ratio": target["volume_ratio"],
            },
            "analogues": analogues,
        },
        [coverage("analogue_candidates", ticker, len(analogues), top_k)],
        limits,
        receipt=timer.receipt(runtime, "cudf"),
        pairs=[*pairs, ranking],
        artifacts=[table] if analogues else [],
        warnings=["Similar measurements do not imply a similar cause or outcome."],
    )


def map_comovement(
    runtime: Runtime, ticker: str, as_of: datetime, lookback_sessions: int = 60, threshold: float = 0.5
) -> ToolResult:
    """Correlation network over prior returns, then who moved with the ticker on the session."""
    timer = Timer()
    tool = "map_comovement"
    resolved = _resolve(runtime, tool, ticker, as_of)
    if isinstance(resolved, ToolResult):
        return resolved
    session_date, target = resolved
    scenario = runtime.scenario
    universe = [
        name
        for name in dict.fromkeys(
            (*scenario.targets, *scenario.peers, *scenario.benchmarks, *scenario.optional_instruments)
        )
        if name in runtime.market.instruments
    ]
    previous = [item.session_date for item in scenario.sessions if item.session_date < session_date]
    if len(previous) < lookback_sessions:
        return no_data(
            tool, as_of, ticker, "comovement", "insufficient_history", "Not enough prior sessions."
        )
    names, returns = runtime.market.returns_matrix(universe, previous[-1], lookback_sessions)
    if ticker not in names:
        return no_data(
            tool, as_of, ticker, "comovement", "insufficient_history", f"{ticker} lacks prior returns."
        )
    edges = correlation_edges(runtime.cp, returns, names, threshold)
    frame = runtime.cudf.DataFrame(
        {"src": [a for a, _, _ in edges], "dst": [b for _, b, _ in edges], "weight": [w for *_, w in edges]}
    )
    hops: dict[str, int] = {ticker: 0}
    if edges:
        graph = runtime.cugraph.Graph(directed=False)
        graph.from_cudf_edgelist(frame, source="src", destination="dst", edge_attr="weight")
        if ticker in set(frame["src"].to_arrow().to_pylist()) | set(frame["dst"].to_arrow().to_pylist()):
            visited = runtime.cugraph.bfs(graph, start=ticker, depth_limit=2).to_pandas()
            hops = {row.vertex: int(row.distance) for row in visited.itertuples() if 0 <= row.distance <= 2}
    correlation = {b if a == ticker else a: w for a, b, w in edges if ticker in (a, b)}
    nodes, pairs = [], [evidence.market_row(scenario, target, _describe(target))]
    for name, distance in sorted(hops.items(), key=lambda item: (item[1], item[0])):
        if name == ticker:
            continue
        rows = _bar(runtime, name, session_date)
        move = rows[-1]["return_1d_pct"] if rows else None
        if rows:
            pairs.append(evidence.market_row(scenario, rows[-1], _describe(rows[-1])))
        nodes.append(
            {
                "ticker": name,
                "hops": distance,
                "correlation_with_target": correlation.get(name),
                "session_return_pct": move,
            }
        )
    summary = (
        f"Over the {lookback_sessions} sessions before {session_date}, {len(nodes)} instruments were within two "
        f"links of {ticker} in a network joining pairs whose daily-return correlation was at least {threshold:g}. "
        + " ".join(
            f"{node['ticker']} ({node['hops']} hop{'s' if node['hops'] > 1 else ''}) moved "
            f"{_fmt(node['session_return_pct'], '+.2f')}% on the session."
            for node in nodes[:8]
        )
    )
    network = evidence.computed(
        scenario, tool, as_of, {"summary": summary, "edges": edges, "nodes": nodes}, [pairs[0][0].evidence_id]
    )
    graph_artifact = Artifact(
        artifact_id=stable_id(
            "artifact", scenario.scenario_id, tool, ticker, session_date, lookback_sessions
        ),
        kind="comovement_graph",
        title=f"{ticker} co-movement network before {session_date}",
        data={
            "target": ticker,
            "session_date": session_date,
            "nodes": nodes,
            "edges": [list(e) for e in edges],
        },
    )
    return result(
        tool,
        as_of,
        "ok" if nodes else "partial",
        {
            "summary": summary,
            "resolved_session": session_date,
            "lookback_sessions": lookback_sessions,
            "correlation_threshold": threshold,
            "target_session_return_pct": target["return_1d_pct"],
            "connected": nodes,
        },
        [coverage("comovement", ticker, len(nodes) or 1, 1)],
        []
        if nodes
        else [limitation("no_linked_instruments", "No instrument met the correlation threshold.", ticker)],
        receipt=timer.receipt(runtime, "cugraph"),
        pairs=[*pairs, network],
        artifacts=[graph_artifact],
        warnings=[
            "Correlated moves show exposure to common factors, not that one company's shock caused another's."
        ],
    )


def predict_volatility_risk(runtime: Runtime, ticker: str, as_of: datetime) -> ToolResult:
    timer = Timer()
    tool = "predict_volatility_risk"
    resolved = _resolve(runtime, tool, ticker, as_of)
    if isinstance(resolved, ToolResult):
        return resolved
    session_date, target = resolved
    metadata = runtime.scenario.risk_model
    if metadata["training_cutoff"] > as_of.isoformat().replace("+00:00", "Z"):
        return no_data(
            tool,
            as_of,
            ticker,
            "risk_model",
            "model_trained_after_cutoff",
            f"The volatility model was trained through {metadata['training_cutoff']}, after this cutoff.",
        )
    if target["return_1d_pct"] is None or target["volume_ratio"] is None:
        return no_data(
            tool,
            as_of,
            ticker,
            "risk_model",
            "risk_inputs_unavailable",
            "The session lacks a daily return or 20-session volume baseline.",
        )
    features = runtime.cp.asarray(
        [[abs(target["return_1d_pct"]), target["volume_ratio"]]], dtype=runtime.cp.float32
    )
    prediction = float(runtime.risk_model.inplace_predict(features)[0])
    if not math.isfinite(prediction) or prediction < 0:
        return no_data(
            tool, as_of, ticker, "risk_model", "invalid_prediction", "The model returned an invalid value."
        )
    horizon = metadata["output_contract"]["horizon_completed_sessions"]
    summary = (
        f"An XGBoost model trained through {metadata['training_cutoff'][:10]} on absolute daily return and "
        f"volume ratio estimates {prediction:.1%} annualized realized volatility for {ticker} over the next "
        f"{horizon} sessions after {session_date}."
    )
    pairs = [evidence.market_row(runtime.scenario, target, _describe(target))]
    estimate = evidence.computed(
        runtime.scenario,
        tool,
        as_of,
        {
            "summary": summary,
            "predicted_annualized_volatility": prediction,
            "model_sha256": metadata["model_sha256"],
        },
        [pairs[0][0].evidence_id],
    )
    return result(
        tool,
        as_of,
        "ok",
        {
            "summary": summary,
            "resolved_session": session_date,
            "predicted_annualized_volatility": prediction,
            "horizon_sessions": horizon,
            "inputs": {
                "abs_return_1d_pct": abs(target["return_1d_pct"]),
                "volume_ratio": target["volume_ratio"],
            },
            "training_cutoff": metadata["training_cutoff"],
            "training_rows": metadata["training_rows"],
        },
        [coverage("risk_model", ticker, 1, 1)],
        receipt=timer.receipt(runtime, "xgboost-gpu"),
        pairs=[*pairs, estimate],
        warnings=["This estimates volatility, not price direction, and is not investment advice."],
    )
