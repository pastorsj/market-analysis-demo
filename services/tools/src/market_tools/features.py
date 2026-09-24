"""Array math for market features, analogue ranking, and co-movement.

Every function takes an array module ``xp``. Production passes CuPy so the work
runs on the GPU; the unit tests pass NumPy so the same code is checked in CI.
"""

from __future__ import annotations

from typing import Any

VOLUME_BASELINE_SESSIONS = 20
SHOCK_RETURN_THRESHOLD_PP = 5.0
SHOCK_VOLUME_RATIO_THRESHOLD = 2.0


def shift(xp: Any, values: Any, periods: int) -> Any:
    """Shift a 1-D float array forward, filling the gap with NaN."""
    result = xp.full(values.shape, xp.nan, dtype=xp.float64)
    if periods < len(values):
        result[periods:] = values[: len(values) - periods]
    return result


def pct_change(xp: Any, current: Any, previous: Any) -> Any:
    safe = xp.where(previous > 0, previous, xp.nan)
    return (current / safe - 1.0) * 100.0


def prior_median(xp: Any, values: Any, window: int = VOLUME_BASELINE_SESSIONS) -> Any:
    """Median of the ``window`` values strictly before each position (NaN until full)."""
    result = xp.full(values.shape, xp.nan, dtype=xp.float64)
    if len(values) <= window:
        return result
    windows = xp.lib.stride_tricks.sliding_window_view(values[:-1], window)
    result[window:] = xp.median(windows, axis=1)
    return result


def daily_features(xp: Any, open_: Any, close: Any, volume: Any) -> dict[str, Any]:
    """Point-in-time daily features for one instrument's session-ordered bars."""
    prior_close = shift(xp, close, 1)
    baseline = prior_median(xp, volume.astype(xp.float64))
    return {
        "prior_close": prior_close,
        "return_1d_pct": pct_change(xp, close, prior_close),
        "return_5d_pct": pct_change(xp, close, shift(xp, close, 5)),
        "opening_gap_pct": pct_change(xp, open_, prior_close),
        "open_to_close_pct": pct_change(xp, close, open_),
        "volume_baseline_median": baseline,
        "volume_ratio": xp.where(baseline > 0, volume / xp.where(baseline > 0, baseline, 1.0), xp.nan),
    }


def is_shock(abnormal_return_pp: float | None, volume_ratio: float | None) -> bool | None:
    """Detector flag: |benchmark-relative return| >= 5 pp OR volume >= 2x its baseline."""
    if abnormal_return_pp is None:
        return None
    return abs(abnormal_return_pp) >= SHOCK_RETURN_THRESHOLD_PP or bool(
        volume_ratio is not None and volume_ratio >= SHOCK_VOLUME_RATIO_THRESHOLD
    )


def standardize(xp: Any, matrix: Any) -> Any:
    """Z-score each column using only the rows supplied (the point-in-time history)."""
    mean = xp.nanmean(matrix, axis=0)
    std = xp.nanstd(matrix, axis=0)
    return (matrix - mean) / xp.where(std > 0, std, 1.0)


def select_analogues(
    xp: Any,
    history: Any,
    query: Any,
    positions: Any,
    query_position: int,
    *,
    top_k: int,
    exclusion_sessions: int = 10,
    spacing_sessions: int = 5,
) -> list[tuple[int, float]]:
    """Rank earlier sessions by standardized Euclidean distance to the query.

    ``history`` holds the raw feature rows for candidate sessions, ``query`` the
    target row, and ``positions`` each candidate's session index. Candidates in the
    ``exclusion_sessions`` before the target are skipped so an episode does not
    match itself, and picks closer than ``spacing_sessions`` to an earlier pick are
    skipped so one episode does not fill every slot.
    """
    usable = (positions <= query_position - exclusion_sessions) & ~xp.isnan(history).any(axis=1)
    rows = xp.nonzero(usable)[0]
    if len(rows) == 0:
        return []
    scaled = standardize(xp, xp.concatenate([history[rows], query[None, :]]))
    distances = xp.sqrt(((scaled[:-1] - scaled[-1]) ** 2).sum(axis=1))
    order = [int(index) for index in xp.argsort(distances).tolist()]
    picked: list[tuple[int, float]] = []
    taken: list[int] = []
    for index in order:
        position = int(positions[rows[index]])
        if all(abs(position - other) >= spacing_sessions for other in taken):
            picked.append((int(rows[index]), float(distances[index])))
            taken.append(position)
        if len(picked) == top_k:
            break
    return picked


def correlation_edges(
    xp: Any, returns: Any, names: list[str], threshold: float
) -> list[tuple[str, str, float]]:
    """Undirected edges between instruments whose return correlation meets ``threshold``."""
    matrix = xp.corrcoef(returns, rowvar=False)
    edges = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            value = float(matrix[i, j])
            if value == value and abs(value) >= threshold:
                edges.append((names[i], names[j], round(value, 4)))
    return edges
