"""Scope denials explain recovery without expanding tool authorization."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from market_agent.deep_evidence import EvidenceCollector
from market_agent.policy import PolicyKind


@pytest.mark.asyncio
@pytest.mark.parametrize("members", [("ALPHA",), ("BETA", "GAMMA"), ("DELTA", "EPSILON", "ZETA")])
@pytest.mark.parametrize("tool", ["get_price_context", "detect_market_shock"])
async def test_out_of_scope_denial_names_actual_members_and_recovery(members, tool):
    scope = SimpleNamespace(resolved_tickers=members, ticker=members[0], as_of=datetime(2024, 6, 7, tzinfo=timezone.utc))
    decision = SimpleNamespace(kind=PolicyKind.SUPPORTED, scope=scope)
    executor = AsyncMock()
    collector = EvidenceCollector(executor, decision, SimpleNamespace(), AsyncMock(), "peer-comparison")
    accepted = object()
    collector.records["previous-evidence"] = accepted

    result = await collector.call(tool, "outside")

    assert set(result) == {"outcome", "message"}
    assert result["outcome"] == "blocked"
    assert f"Allowed members: {', '.join(members)}." in result["message"]
    assert "Do not retry this ticker or explore other out-of-scope tickers" in result["message"]
    assert "accepted in-scope evidence" in result["message"]
    assert "disclose the unavailable comparison coverage" in result["message"]
    assert "submit_answer" in result["message"]
    assert "not evidence that no market data exists" in result["message"]
    assert collector.members == members
    assert collector.records == {"previous-evidence": accepted}
    assert not collector.calls
    assert not executor.mock_calls
