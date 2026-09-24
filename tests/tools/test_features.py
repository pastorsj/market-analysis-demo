"""Feature and ranking math, run with NumPy (production runs the same code on CuPy)."""

import numpy as np
import pytest

from market_tools.features import (
    correlation_edges,
    daily_features,
    is_shock,
    prior_median,
    select_analogues,
    standardize,
)


def test_daily_features_use_only_prior_sessions():
    close = np.array([100.0, 110.0, 99.0, 99.0, 99.0, 104.0])
    open_ = np.array([100.0, 105.0, 108.0, 99.0, 98.0, 100.0])
    volume = np.full(6, 10.0)
    features = daily_features(np, open_, close, volume)
    assert np.isnan(features["return_1d_pct"][0])
    assert features["return_1d_pct"][1] == pytest.approx(10.0)
    assert features["return_1d_pct"][2] == pytest.approx(-10.0)
    assert features["opening_gap_pct"][2] == pytest.approx((108 / 110 - 1) * 100)
    assert features["open_to_close_pct"][5] == pytest.approx(4.0)
    assert features["return_5d_pct"][5] == pytest.approx(4.0)


def test_volume_baseline_is_the_median_of_the_previous_twenty_sessions():
    volume = np.arange(1.0, 23.0)  # 1..22
    median = prior_median(np, volume)
    assert np.isnan(median[19])
    assert median[20] == pytest.approx(np.median(volume[0:20]))
    assert median[21] == pytest.approx(np.median(volume[1:21]))


def test_shock_flag_needs_a_benchmark_and_uses_either_threshold():
    assert is_shock(None, 5.0) is None
    assert is_shock(-5.0, 1.0) is True
    assert is_shock(1.0, 2.0) is True
    assert is_shock(4.9, 1.9) is False


def test_standardize_uses_only_supplied_rows():
    scaled = standardize(np, np.array([[1.0, 10.0], [3.0, 10.0]]))
    assert scaled[:, 0].tolist() == [-1.0, 1.0]
    assert scaled[:, 1].tolist() == [0.0, 0.0]


def test_analogues_skip_recent_sessions_and_repeated_episodes():
    # Row features: signed return, relative return, log volume ratio.
    history = np.array(
        [
            [-10.0, -8.0, 1.0],  # session 0: a similar sell-off
            [-9.5, -7.5, 1.0],  # session 1: same episode as session 0
            [9.0, 8.0, 1.0],  # session 20: similar size, opposite direction
            [-11.0, -9.0, 1.1],  # session 95: inside the exclusion window
            [0.1, 0.0, 0.0],  # session 40: quiet day
        ]
    )
    positions = np.array([0, 1, 20, 95, 40])
    picks = select_analogues(np, history, np.array([-10.0, -8.0, 1.0]), positions, 100, top_k=3)
    rows = [index for index, _ in picks]
    assert rows[0] == 0
    assert 1 not in rows  # within five sessions of an earlier pick
    assert 3 not in rows  # within ten sessions of the target
    assert rows.index(2) > rows.index(0)


def test_correlation_edges_keep_only_strong_pairs():
    base = np.linspace(-1, 1, 30)
    returns = np.stack([base, base * 2 + 0.01, np.cos(np.arange(30))], axis=1)
    edges = correlation_edges(np, returns, ["A", "B", "C"], threshold=0.9)
    assert edges == [("A", "B", 1.0)]
