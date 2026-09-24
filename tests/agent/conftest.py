"""Shared fixtures: a small synthetic scenario, scripted chat models, and a fake tools service."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from market_agent.catalog import Coverage, Session
from market_agent.config import Settings
from market_agent.event_schema import Event

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def coverage() -> Coverage:
    day, sessions = date(2025, 1, 2), []
    while day <= date(2025, 3, 31):
        if day.weekday() < 5 and day != date(2025, 1, 20):
            sessions.append(Session(day, datetime(day.year, day.month, day.day, 21, tzinfo=UTC)))
        day += timedelta(days=1)
    return Coverage(
        "scenario-test", "0" * 64, ("NVDA", "AMD", "JPM", "GS", "SCHW"), ("AVGO",), tuple(sessions)
    )


class Events:
    def __init__(self, *events: Event):
        self.events = {item.event_id: item for item in events}

    def get(self, event_id: str) -> Event | None:
        return self.events.get(event_id)


@pytest.fixture
def events() -> Events:
    raw = json.loads((REPO / "tests/fixtures/event-nvda-deepseek.json").read_text())
    return Events(Event.model_validate(raw))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        state_root=tmp_path,
        scenario_root=tmp_path,
        events_root=tmp_path,
        skills_root=REPO / "services/agent/skills",
        remote_enabled=True,
        remote_url="http://remote.invalid/v1",
        remote_key="test-remote-key",
        langsmith_project_url=None,
    )


class ScriptedChat(ChatOpenAI):
    """A ChatOpenAI whose replies come from ``respond(messages)`` instead of the network."""

    respond: Any = None

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.respond(messages)
        message.response_metadata = {"model_name": self.model_name}
        return ChatResult(generations=[ChatGeneration(message=message)])


def scripted(model: str, respond) -> ScriptedChat:
    return ScriptedChat(model=model, api_key="test", respond=respond)


def calls(*steps: list[dict[str, Any]]):
    """A responder that returns one AIMessage with the given tool calls per step."""
    remaining = list(steps)

    def respond(_messages):
        tool_calls = remaining.pop(0)
        return AIMessage(
            content="",
            tool_calls=[{**call, "id": f"call-{i}-{len(remaining)}"} for i, call in enumerate(tool_calls)],
        )

    return respond


def tool_result(name: str, ticker: str, citation_id: str, available_at: str = "2025-01-27T21:00:00Z") -> dict:
    return {
        "tool": name,
        "as_of": "2025-01-27T21:00:00Z",
        "outcome": "ok",
        "data": {"summary": f"{ticker} fell 16.97% on 2025-01-27."},
        "citations": [
            {
                "citation_id": citation_id,
                "evidence_id": "ev-test",
                "title": f"{ticker} daily market record",
                "url": "https://example.com/source",
                "source_type": "market",
                "published_at": available_at,
                "available_at": available_at,
                "excerpt": f"{ticker} closed lower.",
                "content_sha256": "0" * 64,
            }
        ],
        "receipt": {
            "engine": "cudf",
            "device": "test GPU",
            "duration_ms": 1.0,
            "scenario_id": "scenario-test",
        },
        "artifacts": [],
        "limitations": [],
        "warnings": [],
    }
