"""The agent's HTTP API (served on :2024, reached by the browser through web's /api proxy)."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from time import monotonic
from uuid import UUID

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from mcp import Client

from .agent import MarketAgent
from .catalog import Coverage, EventCatalog
from .config import (
    CAPABLE_MODEL,
    EMBED_MODEL,
    JUDGE_MODEL,
    LOCAL_MODEL,
    MAX_TURNS,
    MODEL_URL,
    SPECULATOR_MODEL,
    TOOLS_URL,
    Settings,
)
from .relay_tracing import RelayTracing
from .runner import Busy, Runner, new_investigation
from .schemas import CreateInvestigation, FollowUp, Investigation
from .scope import ScopeError, follow_up, resolve
from .store import Store, load_key, open_checkpointer

log = logging.getLogger(__name__)
REMOTE_REQUIRED = (
    "Research needs the remote routing endpoint: NeMo Switchyard asks a remote judge model to review "
    "every agent step. Set REMOTE_ROUTING_ENABLED=true with NVIDIA_BASE_URL and NVIDIA_INFERENCE_API_KEY."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    coverage = Coverage.load(settings.scenario_root)
    try:
        events = EventCatalog.load(settings.events_root, coverage)
    except Exception:
        log.exception("curated event catalog unavailable")
        events = None
    key = load_key(settings.state_root / "agent.key")
    store = Store(settings.state_root / "agent-v2.sqlite3", key, settings.secrets)
    checkpointer, connection = await open_checkpointer(settings.state_root / "checkpoints-v2.sqlite3", key)
    tracing = RelayTracing()
    await tracing.start()
    agent = MarketAgent(settings, coverage, checkpointer) if settings.remote_enabled else None
    app.state.settings, app.state.coverage, app.state.events = settings, coverage, events
    app.state.store, app.state.runner = store, Runner(store, agent, coverage)
    app.state.health = {"checked": 0.0}
    try:
        yield
    finally:
        await connection.close()
        await tracing.shutdown()
        store.close()


app = FastAPI(title="Market Shock Research Agent", lifespan=lifespan, docs_url=None, redoc_url=None)


# Health ---------------------------------------------------------------------


async def dependencies(request: Request) -> dict[str, bool]:
    """Tools and local model reachability, cached for a few seconds."""
    health = request.app.state.health
    if monotonic() - health["checked"] < 5:
        return health["value"]
    value = {"tools": False, "model": False, "events": request.app.state.events is not None}
    async with httpx.AsyncClient(timeout=3) as client:
        try:
            value["tools"] = (await client.get(TOOLS_URL.removesuffix("/mcp") + "/health")).status_code == 200
        except httpx.HTTPError:
            pass
        try:
            models = (await client.get(MODEL_URL + "/models")).json().get("data", [])
            value["model"] = any(item.get("id") == LOCAL_MODEL for item in models)
        except (httpx.HTTPError, ValueError):
            pass
    health.update(checked=monotonic(), value=value)
    return value


def not_ready_reason(request: Request, deps: dict[str, bool]) -> str | None:
    if not request.app.state.settings.remote_enabled:
        return "remote_routing_disabled"
    missing = [name for name, ok in deps.items() if not ok]
    return f"unavailable: {', '.join(missing)}" if missing else None


@app.get("/health/live")
async def live():
    return {"service": "agent", "live": True}


@app.get("/health/ready")
async def ready(request: Request):
    deps = await dependencies(request)
    reason = not_ready_reason(request, deps)
    return {"service": "agent", "ready": reason is None, "reason": reason, **deps}


@app.get("/api/status")
async def status(request: Request):
    settings, coverage = request.app.state.settings, request.app.state.coverage
    deps = await dependencies(request)
    reason = not_ready_reason(request, deps)
    return {
        "ready": reason is None,
        "reason": REMOTE_REQUIRED if reason == "remote_routing_disabled" else reason,
        "remote_routing_enabled": settings.remote_enabled,
        "dependencies": deps,
        "companies": coverage.companies(),
        "coverage": {
            "scenario_id": coverage.scenario_id,
            "first_session": coverage.first_session.isoformat(),
            "last_session": coverage.last_session.isoformat(),
        },
        "models": [
            {"id": LOCAL_MODEL, "role": "agent reasoning", "where": "local"},
            {"id": SPECULATOR_MODEL, "role": "speculative decoding", "where": "local"},
            {"id": EMBED_MODEL, "role": "document embeddings", "where": "local"},
            {"id": JUDGE_MODEL, "role": "Switchyard routing judge", "where": "remote"},
            {"id": CAPABLE_MODEL, "role": "escalated reasoning", "where": "remote"},
        ],
        "max_turns": MAX_TURNS,
        "langsmith_project_url": settings.langsmith_project_url,
    }


# Reference data --------------------------------------------------------------


@app.get("/api/shock-events")
async def shock_events(request: Request):
    events: EventCatalog | None = request.app.state.events
    if events is None:
        raise HTTPException(
            503, "Curated events are unavailable; you can still ask about any supported company."
        )
    return events.public()


@app.get("/api/dashboard")
async def dashboard(ticker: str, as_of: str):
    async with Client(TOOLS_URL, read_timeout_seconds=60) as client:
        result = await client.read_resource(f"market://dashboard/{ticker}/{as_of}")
    contents = list(result.contents)
    text = getattr(contents[0], "text", None) if contents else None
    if not isinstance(text, str):
        raise HTTPException(502, "The tools service returned no dashboard.")
    return json.loads(text)


# Investigations ---------------------------------------------------------------


def _load(request: Request, investigation_id: UUID) -> Investigation:
    investigation = request.app.state.store.get(investigation_id)
    if investigation is None:
        raise HTTPException(404, "Investigation not found.")
    return investigation


def _with_events(request: Request, investigation: Investigation) -> Investigation:
    return investigation.model_copy(
        update={"events": request.app.state.store.events(investigation.investigation_id)}
    )


def _start(request: Request, investigation: Investigation, question: str, *, retry: bool = False) -> None:
    if not request.app.state.settings.remote_enabled:
        raise HTTPException(503, REMOTE_REQUIRED)
    try:
        request.app.state.runner.start(investigation, question, retry=retry)
    except Busy as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/investigations", status_code=202, response_model=Investigation)
async def create_investigation(body: CreateInvestigation, request: Request):
    try:
        scope = resolve(body, request.app.state.coverage, request.app.state.events)
    except ScopeError as exc:
        raise HTTPException(422, str(exc)) from exc
    investigation = new_investigation(scope)
    _start(request, investigation, body.question)
    return _with_events(request, investigation)


@app.get("/api/investigations/{investigation_id}", response_model=Investigation)
async def get_investigation(investigation_id: UUID, request: Request):
    return _with_events(request, _load(request, investigation_id))


@app.post("/api/investigations/{investigation_id}/turns", status_code=202, response_model=Investigation)
async def add_turn(investigation_id: UUID, body: FollowUp, request: Request):
    investigation = _load(request, investigation_id)
    if investigation.status == "running":
        raise HTTPException(409, "This investigation is still running.")
    if len(investigation.turns) >= MAX_TURNS:
        raise HTTPException(409, f"An investigation holds up to {MAX_TURNS} questions. Start a new one.")
    investigation.scope = follow_up(investigation.scope, body.question, request.app.state.coverage)
    _start(request, investigation, body.question)
    return _with_events(request, investigation)


@app.post("/api/investigations/{investigation_id}/retry", status_code=202, response_model=Investigation)
async def retry(investigation_id: UUID, request: Request):
    investigation = _load(request, investigation_id)
    if investigation.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "Only a failed or cancelled question can be retried.")
    _start(request, investigation, investigation.turns[-1].question, retry=True)
    return _with_events(request, investigation)


@app.post("/api/investigations/{investigation_id}/cancel", response_model=Investigation)
async def cancel(investigation_id: UUID, request: Request):
    _load(request, investigation_id)
    await request.app.state.runner.cancel(investigation_id)
    return _with_events(request, _load(request, investigation_id))


@app.get("/api/investigations/{investigation_id}/stream")
async def stream(investigation_id: UUID, request: Request, last_event_id: str | None = Header(default=None)):
    """Server-sent progress events until the running turn finishes (resumable with Last-Event-ID)."""
    _load(request, investigation_id)
    store, runner = request.app.state.store, request.app.state.runner

    async def events():
        after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        while True:
            for event in store.events(investigation_id, after):
                after = event.sequence
                yield f"id: {after}\nevent: progress\ndata: {event.model_dump_json()}\n\n"
            investigation = store.get(investigation_id)
            if investigation.status != "running" and runner.running_id != investigation_id:
                yield f"event: done\ndata: {json.dumps({'status': investigation.status})}\n\n"
                return
            if await request.is_disconnected():
                return
            try:
                await runner.wait_for_change()
            except asyncio.CancelledError:
                return

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
