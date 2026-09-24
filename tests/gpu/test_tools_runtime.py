"""GPU tests against the prepared scenario. Run with scripts/spark/test-tools-gpu.sh."""

from datetime import UTC, datetime

import pytest

pytest.importorskip("cudf")

from market_tools import document_tools, market_tools  # noqa: E402
from market_tools.config import Settings  # noqa: E402
from market_tools.dashboard import build_dashboard  # noqa: E402
from market_tools.runtime import Runtime  # noqa: E402

pytestmark = pytest.mark.gpu
CUTOFF = datetime(2025, 1, 27, 21, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def runtime():
    return Runtime(Settings.from_env())


def _no_future_evidence(result):
    assert all(item.available_at <= result.as_of for item in result.evidence)
    assert all(item.available_at <= result.as_of for item in result.citations)


@pytest.mark.parametrize(
    "tool",
    [
        market_tools.get_price_context,
        market_tools.detect_market_shock,
        market_tools.find_historical_analogues,
        market_tools.map_comovement,
        market_tools.predict_volatility_risk,
    ],
)
def test_market_tools_run_on_gpu_and_respect_the_cutoff(runtime, tool):
    result = tool(runtime, "NVDA", CUTOFF)
    assert result.outcome in {"ok", "partial"}
    assert result.receipt is not None and result.receipt.gpu_executed
    _no_future_evidence(result)


def test_shock_numbers_for_the_deepseek_session(runtime):
    data = market_tools.detect_market_shock(runtime, "NVDA", CUTOFF).data
    assert data["return_1d_pct"] == pytest.approx(-16.97, abs=0.01)
    assert data["benchmark"] == "QQQ"
    assert data["is_shock"] is True


def test_analogues_are_earlier_and_outside_the_exclusion_window(runtime):
    data = market_tools.find_historical_analogues(runtime, "SCHW", datetime(2023, 3, 13, 20, tzinfo=UTC)).data
    sessions = [item.session_date for item in runtime.scenario.sessions]
    target = sessions.index("2023-03-13")
    for row in data["analogues"]:
        assert sessions.index(row["session_date"]) <= target - 10


def test_search_window_and_topic_map_use_the_newest_documents(runtime):
    same_day = document_tools.search_news(runtime, "NVDA", CUTOFF, "market reaction", lookback_days=1)
    assert all(match["available_at"][:10] >= "2025-01-26" for match in same_day.data["matches"])
    topics = document_tools.project_news_topics(runtime, "JPM", datetime(2025, 1, 15, 21, tzinfo=UTC))
    assert topics.data["newest"] == "2025-01-15"
    _no_future_evidence(topics)


def test_volatility_model_is_unavailable_before_its_training_cutoff(runtime):
    result = market_tools.predict_volatility_risk(runtime, "NVDA", datetime(2023, 1, 3, 21, tzinfo=UTC))
    assert result.outcome == "no_data"
    assert result.limitations[0].code == "model_trained_after_cutoff"


def test_dashboard_has_every_target(runtime):
    payload = build_dashboard(runtime, "NVDA", "2025-01-27")
    assert [row["ticker"] for row in payload["watchlist"]] == list(runtime.scenario.targets)
    assert payload["series"]["points"][-1]["session_date"] == "2025-01-27"
