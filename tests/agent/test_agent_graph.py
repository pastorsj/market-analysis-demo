"""Run the real Deep Agents graph with scripted models and a fake tools service."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from market_agent import tools as tools_module
from market_agent.agent import MarketAgent
from market_agent.config import CAPABLE_MODEL, JUDGE_MODEL, LOCAL_MODEL
from market_agent.context import TurnContext
from market_agent.report import build_report
from market_agent.schemas import CreateInvestigation
from market_agent.scope import resolve

from .conftest import calls, scripted, tool_result

CITATION = "cit-aaaaaaaaaaaaaaaa"


def judge(verdict: bool = False):
    return lambda _messages: AIMessage(content=json.dumps({"escalate": verdict, "reason": "checked"}))


def build(settings, coverage, agent_steps, *, escalate=False, capable_steps=()):
    models = {
        LOCAL_MODEL: scripted(LOCAL_MODEL, calls(*agent_steps)),
        JUDGE_MODEL: scripted(JUDGE_MODEL, judge(escalate)),
        CAPABLE_MODEL: scripted(CAPABLE_MODEL, calls(*capable_steps) if capable_steps else judge()),
    }
    return MarketAgent(settings, coverage, InMemorySaver(), models=models)


def turn(coverage, question="How did NVDA trade on 2025-01-27?"):
    progress = []

    async def record(**span):
        progress.append(span)

    scope = resolve(CreateInvestigation(question=question), coverage, None)
    context = TurnContext(
        investigation_id="test-investigation",
        turn=1,
        scope=scope,
        market_cutoff=scope.as_of,
        progress=record,
    )
    return context, progress


@pytest.fixture
def fake_tools(monkeypatch):
    requests = []

    async def fake(name, arguments):
        requests.append((name, arguments))
        return tool_result(name, arguments["ticker"], CITATION)

    monkeypatch.setattr(tools_module, "mcp_call", fake)
    return requests


async def test_agent_reads_a_skill_uses_scoped_tools_and_returns_a_typed_answer(
    settings, coverage, fake_tools
):
    agent = build(
        settings,
        coverage,
        [
            [{"name": "read_file", "args": {"file_path": "/skills/market-dislocation/SKILL.md"}}],
            [
                {"name": "get_price_context", "args": {"ticker": "NVDA"}},
                {"name": "get_price_context", "args": {"ticker": "MSFT"}},
            ],
            [
                {
                    "name": "Answer",
                    "args": {
                        "answer": "NVDA fell 16.97%.",
                        "citation_ids": [CITATION, "cit-ffffffffffffffff"],
                    },
                }
            ],
        ],
    )
    context, progress = turn(coverage)
    answer, skill = await agent.run_turn(context, "How did NVDA trade on 2025-01-27?")

    assert skill == "market-dislocation"
    assert [name for name, _ in fake_tools] == ["get_price_context"]  # MSFT was blocked by scope
    assert fake_tools[0][1]["as_of"].startswith("2025-01-27T21:00:00")
    report = build_report(answer, context)
    assert report.kind == "research"
    assert [item.citation_id for item in report.citations] == [CITATION]  # invented ID dropped
    roles = {call.role for call in context.model_calls}
    assert roles == {"agent", "judge"}  # every step was routed through the judge
    assert {span["kind"] for span in progress} >= {"model", "tool", "skill"}


async def test_escalation_runs_the_capable_model(settings, coverage, fake_tools):
    agent = build(
        settings,
        coverage,
        [[{"name": "Answer", "args": {"answer": "A weak local answer."}}]],
        escalate=True,
        capable_steps=[[{"name": "Answer", "args": {"answer": "Please give me a company and date."}}]],
    )
    context, _ = turn(coverage, "Hello")
    answer, _ = await agent.run_turn(context, "Hello")
    assert answer.answer == "Please give me a company and date."
    assert any(call.tier == "capable" for call in context.model_calls)


async def test_tools_are_blocked_until_scope_is_resolved(coverage, fake_tools):
    context, _ = turn(coverage, "Hello")
    result = await tools_module.call_tool(context, "get_price_context", "NVDA")
    assert result["outcome"] == "blocked" and not fake_tools


async def test_results_with_evidence_after_the_cutoff_are_rejected(coverage, monkeypatch):
    async def late(name, arguments):
        return tool_result(name, arguments["ticker"], CITATION, available_at="2025-02-03T21:00:00Z")

    monkeypatch.setattr(tools_module, "mcp_call", late)
    context, _ = turn(coverage)
    result = await tools_module.call_tool(context, "search_news", "NVDA", query="chips")
    assert result["outcome"] == "failed"
    assert context.ledger.results == [] and context.ledger.failures[0].outcome == "failed"


async def test_repeated_calls_are_served_from_the_turn_cache(coverage, fake_tools):
    context, _ = turn(coverage)
    await tools_module.call_tool(context, "detect_market_shock", "NVDA")
    await tools_module.call_tool(context, "detect_market_shock", "nvda")
    assert len(fake_tools) == 1


async def test_reworded_repeat_calls_are_capped_per_tool_and_ticker(coverage, fake_tools):
    context, _ = turn(coverage)
    for query in ("chips", "chip demand", "chip stocks", "chip news"):
        result = await tools_module.call_tool(context, "search_news", "NVDA", query=query)
    assert result["outcome"] == "blocked" and len(fake_tools) == 3
