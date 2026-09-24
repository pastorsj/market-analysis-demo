"""HTTP lifecycle: create, stream, follow up, retry, cancel, and fail-closed routing."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from market_agent import app as app_module
from market_agent.agent import AgentError, Answer
from market_agent.config import MAX_TURNS
from market_agent.runner import Runner
from market_agent.store import Store


class FakeAgent:
    """Stands in for MarketAgent: records calls and follows a per-turn plan."""

    def __init__(self):
        self.plan: list = []
        self.dropped = 0
        self.release = asyncio.Event()
        self.release.set()

    async def drop_last_turn(self, context):
        self.dropped += 1

    async def run_turn(self, context, question):
        await context.progress(span="tool-1", kind="tool", name="get_price_context", state="running")
        await self.release.wait()
        await context.progress(span="tool-1", kind="tool", name="get_price_context", state="succeeded")
        step = self.plan.pop(0) if self.plan else "ok"
        if step == "fail":
            raise AgentError("no_answer", "The agent finished without a structured answer.")
        return Answer(answer=f"Answer to: {question}"), "market-dislocation"


@pytest.fixture
def agent():
    return FakeAgent()


@pytest.fixture
async def client(settings, coverage, events, agent, tmp_path):
    state = app_module.app.state
    state.settings, state.coverage, state.events = settings, coverage, events
    state.store = Store(tmp_path / "agent.sqlite3", b"k" * 32, settings.secrets)
    state.runner = Runner(state.store, agent, coverage)
    state.health = {"checked": 0.0}
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    state.store.close()


async def settle(http, identifier):
    for _ in range(100):
        body = (await http.get(f"/api/investigations/{identifier}")).json()
        if body["status"] != "running":
            return body
        await asyncio.sleep(0.01)
    raise AssertionError("turn did not finish")


async def create(http, question="How did NVDA trade on 2025-01-27?"):
    response = await http.post("/api/investigations", json={"question": question})
    assert response.status_code == 202, response.text
    return response.json()["investigation_id"]


async def test_create_runs_a_turn_in_the_background(client):
    identifier = await create(client)
    body = await settle(client, identifier)
    assert body["status"] == "completed"
    assert body["scope"]["ticker"] == "NVDA"
    turn = body["turns"][0]
    assert turn["skill"] == "market-dislocation" and turn["report"]["kind"] == "guide"
    kinds = [event["span"]["kind"] for event in body["events"]]
    assert kinds[0] == "turn" and "tool" in kinds


async def test_stream_replays_events_then_reports_done(client):
    identifier = await create(client)
    await settle(client, identifier)
    response = await client.get(f"/api/investigations/{identifier}/stream", headers={"Last-Event-ID": "1"})
    blocks = [block for block in response.text.split("\n\n") if block]
    assert blocks[0].startswith("id: 2\n")
    assert blocks[-1].startswith("event: done") and '"completed"' in blocks[-1]


async def test_follow_ups_keep_scope_and_stop_at_the_turn_limit(client):
    identifier = await create(client)
    for _ in range(2, MAX_TURNS + 1):
        await settle(client, identifier)
        response = await client.post(f"/api/investigations/{identifier}/turns", json={"question": "And AMD?"})
        assert response.status_code == 202
    body = await settle(client, identifier)
    assert len(body["turns"]) == MAX_TURNS and body["scope"]["members"] == ["NVDA", "AMD"]
    response = await client.post(f"/api/investigations/{identifier}/turns", json={"question": "More?"})
    assert response.status_code == 409


async def test_failed_turn_can_be_retried_and_drops_the_failed_messages(client, agent):
    agent.plan = ["fail"]
    identifier = await create(client)
    body = await settle(client, identifier)
    assert body["status"] == "failed" and body["turns"][0]["error"]["code"] == "no_answer"
    assert (await client.post(f"/api/investigations/{identifier}/retry")).status_code == 202
    body = await settle(client, identifier)
    assert body["status"] == "completed" and agent.dropped == 1 and len(body["turns"]) == 1


async def test_cancel_stops_the_running_turn_and_allows_a_new_one(client, agent):
    agent.release.clear()
    identifier = await create(client)
    assert (
        await client.post("/api/investigations", json={"question": "GS on 2025-01-27"})
    ).status_code == 409
    body = (await client.post(f"/api/investigations/{identifier}/cancel")).json()
    assert body["status"] == "cancelled"
    assert not body["events"] or body["events"][-1]["span"]["state"] == "cancelled"
    agent.release.set()
    await create(client, "GS on 2025-01-27")


async def test_research_is_refused_when_remote_routing_is_off(client, settings):
    app_module.app.state.settings = settings.__class__(**{**settings.__dict__, "remote_enabled": False})
    response = await client.post("/api/investigations", json={"question": "NVDA on 2025-01-27"})
    assert response.status_code == 503 and "REMOTE_ROUTING_ENABLED" in response.json()["detail"]


async def test_unknown_event_is_a_client_error(client):
    response = await client.post(
        "/api/investigations", json={"question": "Tell me", "event_id": "missing-event"}
    )
    assert response.status_code == 422


async def test_shock_events_hide_internal_qualification(client, events):
    app_module.app.state.events = type("Catalog", (), {"public": lambda self: {"events": []}})()
    assert (await client.get("/api/shock-events")).json() == {"events": []}


async def test_status_lists_companies_and_models(client, monkeypatch):
    async def deps(_request):
        return {"tools": True, "model": True, "events": True}

    monkeypatch.setattr(app_module, "dependencies", deps)
    body = (await client.get("/api/status")).json()
    assert body["ready"] is True
    assert body["companies"][0] == {"symbol": "NVDA", "name": "NVIDIA"}
    assert "test-remote-key" not in json.dumps(body)
