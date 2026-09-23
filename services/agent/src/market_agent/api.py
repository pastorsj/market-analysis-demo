"""Public investigation REST and resumable event-stream boundary."""

import asyncio, hashlib, json
from datetime import datetime; from uuid import UUID, uuid4; from zoneinfo import ZoneInfo
from time import perf_counter
from fastapi import APIRouter, Depends, HTTPException, Request; from fastapi.responses import StreamingResponse
from .generation_health import require_generation
from .schemas import MAX_INVESTIGATION_TURNS
from .event_catalog import EventRequestError, resolve_bound_policy
from .policy import resolve_follow_up, resolve_policy; from .schemas import FinalReport, InvestigationRecord, InvestigationRequest, InvestigationScope, ProgressPayload, TrajectoryEvent, TurnRequest, active_progress, progress_event_type; from .security import SecurityRecorder

router = APIRouter(prefix="/api", dependencies=[Depends(require_generation)])
_STATUSES = {"help": "completed", "refusal": "refused", "clarification": "needs_input", "no_data": "completed", "partial": "completed", "success": "completed"}
_EVENT_LIMIT, _MIN_TURN_EVENT_RESERVE = 300, 28
class _ProgressCapacityError(OverflowError): pass


def now() -> datetime: return datetime.now(ZoneInfo("America/New_York"))


def _event(record: InvestigationRecord, event_type: str, label: str, detail: str = "", payload: dict | None = None) -> None:
    bounded_detail = detail if len(detail) <= 1200 else detail[:1197] + "..."
    record.events.append(TrajectoryEvent(sequence=len(record.events) + 1, event_type=event_type, label=label, detail=bounded_detail, payload=payload or {}, occurred_at=now(), tool=(payload or {}).get("tool"))); record.updated_at = now()


def _load(request: Request, identifier: UUID) -> InvestigationRecord:
    record = request.app.state.store.get(identifier)
    if record is None: raise HTTPException(status_code=404, detail="investigation not found")
    if _contains_secret(record, request): raise HTTPException(status_code=500, detail="persisted record failed the secret boundary")
    return record


def _contains_secret(value: object, request: Request) -> bool:
    try:
        wire = value.model_dump_json() if hasattr(value, "model_dump_json") else json.dumps(value, sort_keys=True, default=str)
        secrets = (item.get_secret_value() if hasattr(item, "get_secret_value") else item for item in getattr(request.app.state, "security_secrets", {}).values())
        return any(isinstance(item, str) and item and (item in wire or json.dumps(item, ensure_ascii=False)[1:-1] in wire) for item in secrets)
    except Exception: return True


def _save(record: InvestigationRecord, request: Request, *, create: bool = False) -> bool:
    if _contains_secret(record, request): raise RuntimeError("secret boundary rejected persistence")
    accepted = InvestigationRecord.model_validate(record.model_dump(mode="json"))
    return request.app.state.store.save(accepted, create=True) if create else request.app.state.store.save(accepted) is not False


@router.get("/shock-events")
async def shock_events(request: Request) -> dict:
    catalog = getattr(request.app.state, "event_catalog", None)
    if catalog is None:
        raise HTTPException(
            status_code=503,
            detail="Known shock events are temporarily unavailable. You can still use custom research scope.",
        )
    return catalog.public_payload()


@router.post("/investigations", response_model=InvestigationRecord, status_code=201)
async def create_investigation(body: InvestigationRequest, request: Request, evaluation_id: UUID | None = None) -> InvestigationRecord:
    if _contains_secret(body, request): raise HTTPException(status_code=400, detail="request rejected by the secret boundary")
    try: decision = resolve_bound_policy(body, request.app.state.catalog, getattr(request.app.state, "event_catalog", None))
    except EventRequestError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    timestamp = now(); record = InvestigationRecord(investigation_id=evaluation_id or uuid4(), created_at=timestamp, updated_at=timestamp, status="created", request=decision.request, scope=decision.scope)
    _event(record, "scope", "Scope resolved", decision.scope.explanation, decision.scope.model_dump(mode="json")); created = _save(record, request, create=evaluation_id is not None)
    if created is False: raise HTTPException(status_code=409, detail="investigation already exists")
    return record


def _latest_turn_id(record: InvestigationRecord) -> str | None:
    return next((str(item.payload["turn_id"]) for item in reversed(record.events) if item.event_type == "planning" and "turn_id" in item.payload), None)


def _require_turn_capacity(record: InvestigationRecord) -> None:
    if len(record.turns) >= MAX_INVESTIGATION_TURNS:
        raise HTTPException(status_code=409, detail=f"This demo supports up to {MAX_INVESTIGATION_TURNS} submitted turns per investigation, including retries. Start a new investigation.")
    if len(record.events) > _EVENT_LIMIT - _MIN_TURN_EVENT_RESERVE:
        raise HTTPException(status_code=409, detail="investigation event capacity reached; start a new investigation")


class _ProgressWriter:
    """Serialize concurrent span signals into the encrypted investigation record."""

    def __init__(self, record: InvestigationRecord, request: Request, turn_id: str):
        locks = getattr(request.app.state, "progress_locks", None)
        if locks is None: locks = request.app.state.progress_locks = {}
        self.record, self.request, self.turn_id = record, request, turn_id
        self.lock = locks.setdefault(str(record.investigation_id), asyncio.Lock())
        self.active = {span_id: (span, None) for span_id, span in active_progress(record.events).items() if span.turn_id == turn_id}

    def _span_id(self, key: str) -> str:
        return "span-" + hashlib.sha256(f"{self.turn_id}:{key}".encode()).hexdigest()[:16]

    async def __call__(self, *, key: str, kind: str, display_name: str, state: str, parent_key: str | None = "investigation", **detail) -> None:
        await self._write(self._span_id(key), kind, display_name, state, None if parent_key is None else self._span_id(parent_key), **detail)

    async def _write(self, span_id: str, kind: str, display_name: str, state: str, parent: str | None, **detail) -> None:
        async with self.lock:
            planned = detail.get("planned_call_count")
            # Reserve a second evidence wave plus all model, report, verification,
            # root-closure, and terminal events in the bounded deep-agent loop.
            if kind == "planning" and state == "completed" and isinstance(planned, int) and len(self.record.events) + 4 * planned + 19 > _EVENT_LIMIT:
                raise _ProgressCapacityError("progress event capacity cannot hold the planned turn")
            pair = self.active.pop(span_id, None) if state != "started" else None
            if state == "started":
                if span_id in self.active: return
                payload = ProgressPayload(turn_id=self.turn_id, span_id=span_id, parent_span_id=parent, kind=kind, display_name=display_name, state="started", started_at=now(), **detail)
                self.active[span_id] = (payload, perf_counter())
            else:
                if pair is None: raise RuntimeError("progress completion lacks a start")
                started, monotonic = pair; completed = now()
                elapsed = max(0.0, (perf_counter() - monotonic) * 1000) if monotonic is not None else max(0.0, (completed - started.started_at).total_seconds() * 1000)
                values = started.model_dump(mode="python", exclude={"state", "completed_at", "elapsed_ms"}) | detail
                payload = ProgressPayload(**values, state=state, completed_at=completed, elapsed_ms=elapsed)
            phrase = "started" if payload.state == "started" else "reused" if payload.state == "reused" else "cancelled" if payload.state == "cancelled" else "failed" if payload.state == "failed" else "completed"
            label = "Investigation started" if payload.display_name == "Investigation" and payload.state == "started" else payload.display_name
            previous_updated = self.record.updated_at; _event(self.record, progress_event_type(payload), label, f"{payload.display_name} {phrase}.", payload.model_dump(mode="json"))
            try: _save(self.record, self.request)
            except Exception:
                self.record.events.pop(); self.record.updated_at = previous_updated
                if state == "started": self.active.pop(span_id, None)
                elif pair is not None: self.active[span_id] = pair
                raise

    async def close_open(self, state: str) -> None:
        for span_id, (span, _) in reversed(tuple(self.active.items())):
            if span.display_name == "Investigation": continue
            detail = {"outcome": "failed"} if span.kind == "tool" else {}
            await self._write(span_id, span.kind, span.display_name, state, span.parent_span_id, **detail)


def _finalize_terminal(record: InvestigationRecord, recorder: SecurityRecorder, terminal: str, turn_id: str, event_start: int, prior_scope: InvestigationScope | None) -> None:
    receipt = recorder.finalize(record.model_dump(mode="json"), {"turn_id": turn_id, "terminal": terminal}); payload = {"turn_id": turn_id, "terminal": terminal, "security_receipt": receipt.model_dump(mode="json")}
    if receipt.completeness != "verified":
        del record.events[event_start:]; record.report, record.scope = None, prior_scope
        reason = receipt.violations[0].code if receipt.completeness == "violation" and receipt.violations else receipt.unknown_reasons[0] if receipt.unknown_reasons else "receipt_incomplete"
        payload["terminal"] = f"security_{receipt.completeness}"
        record.status, record.error = "failed", f"security_{receipt.completeness}:{reason}"; _event(record, "error", "Investigation failed", record.error, payload)
    elif record.report:
        payload["report"] = record.report.model_dump(mode="json"); _event(record, "report", "Investigation complete", record.report.summary, payload)
    elif terminal == "cancelled": _event(record, "cancelled", "Investigation cancelled", "No further tool calls will be started.", payload)
    else: _event(record, "error", "Investigation failed", record.error or "workflow_incomplete", payload)
    record.security_receipts.append(receipt)


async def _execute(record: InvestigationRecord, request: Request, *, retry: bool = False) -> InvestigationRecord:
    key, running = str(record.investigation_id), request.app.state.running.get(str(record.investigation_id))
    if record.status == "running" and running is not None and not running.done(): raise HTTPException(status_code=409, detail="investigation already running")
    task = asyncio.current_task(); request.app.state.running[key] = task
    try:
        return await _execute_owned(record, request, retry=retry)
    finally:
        if request.app.state.running.get(key) is task: request.app.state.running.pop(key, None)
        locks = getattr(request.app.state, "progress_locks", None)
        if isinstance(locks, dict): locks.pop(key, None)


async def _execute_owned(record: InvestigationRecord, request: Request, *, retry: bool) -> InvestigationRecord:
    key = str(record.investigation_id)
    resume = record.status == "running" and not retry; turn_id = _latest_turn_id(record) if resume else None; event_start, prior_scope = len(record.events), record.scope
    if turn_id is None:
        _require_turn_capacity(record)
        record.turns.append(record.request.question); turn_id = f"turn-{len(record.turns):04d}"
    rollback_start = next((index for index, item in enumerate(record.events) if item.payload.get("turn_id") == turn_id), event_start) if resume else event_start
    record.status, record.report, record.error = "running", None, None; _save(record, request)
    progress = _ProgressWriter(record, request, turn_id)
    if not resume: await progress(key="investigation", kind="report", display_name="Investigation", state="started", parent_key=None)
    recorder = SecurityRecorder(key, turn_id, getattr(request.app.state, "security_secrets", {})); terminal, final_status = "workflow_exception", "failed"
    next_scope, next_report, next_skill = record.scope, None, None
    try:
        state = await request.app.state.investigator.invoke(
            record.request,
            investigation_id=key,
            turn_id=turn_id,
            recorder=recorder,
            progress=progress,
            fresh_checkpoint=retry,
            resolved_scope=record.scope,
            prior_skill=record.active_skill,
        )
        next_scope = InvestigationScope.model_validate(state["scope"]) if state.get("scope") else record.scope; next_report = FinalReport.model_validate(state["report"]) if state.get("report") else None
        terminal = str(state.get("terminal") or "verification_failure"); recorder.reconcile(state); final_status = _STATUSES.get(terminal, "failed")
        if final_status in {"completed", "needs_input", "refused"}:
            next_skill = state.get("selected_skill")
        if not next_report: record.error = f"{terminal}:{state.get('failure_code') or 'workflow_incomplete'}"
    except asyncio.CancelledError: terminal, final_status = "cancelled", "cancelled"
    except _ProgressCapacityError: final_status, record.error = "failed", "workflow_exception:event_capacity"
    except Exception: final_status, record.error = "failed", "workflow_exception:internal_boundary"
    async def finish() -> InvestigationRecord:
        span_state = "completed" if final_status in {"completed", "needs_input", "refused"} else "cancelled" if final_status == "cancelled" else "failed"
        await progress.close_open("cancelled" if span_state == "cancelled" else "failed")
        await progress(key="investigation", kind="report", display_name="Investigation", state=span_state, parent_key=None, terminal=terminal)
        record.status, record.scope, record.report = final_status, next_scope, next_report
        _finalize_terminal(record, recorder, terminal, turn_id, rollback_start, prior_scope)
        if record.status in {"completed", "needs_input", "refused"} and next_skill:
            record.active_skill = next_skill
        _save(record, request)
        return record
    # Final persistence is not new research; cancellation must not split its receipt.
    finishing = asyncio.create_task(finish())
    while not finishing.done():
        try: await asyncio.shield(finishing)
        except asyncio.CancelledError: pass
    return finishing.result()


@router.post("/investigations/{identifier}/run", response_model=InvestigationRecord)
async def run_investigation(identifier: UUID, request: Request) -> InvestigationRecord:
    record = _load(request, identifier); return record if record.status in {"completed", "needs_input", "refused", "failed", "cancelled"} else await _execute(record, request)


@router.post("/investigations/{identifier}/turn", response_model=InvestigationRecord)
async def continue_investigation(identifier: UUID, body: TurnRequest, request: Request) -> InvestigationRecord:
    record = _load(request, identifier)
    if record.status == "running": raise HTTPException(status_code=409, detail="investigation already running")
    _require_turn_capacity(record)
    if _contains_secret(body, request): raise HTTPException(status_code=400, detail="request rejected by the secret boundary")
    if record.request.event_id:
        follow_up = InvestigationRequest(
            question=body.question, ticker=record.request.ticker, as_of=record.request.as_of,
            route_mode=body.route_mode or record.request.route_mode, event_id=record.request.event_id,
        )
        try: decision = resolve_bound_policy(follow_up, request.app.state.catalog, getattr(request.app.state, "event_catalog", None))
        except EventRequestError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        decision = resolve_follow_up(body, record.scope, record.request.route_mode, request.app.state.catalog) if record.scope else resolve_policy(InvestigationRequest(question=body.question, route_mode=body.route_mode or record.request.route_mode), request.app.state.catalog)
    record.request, record.scope = decision.request, decision.scope; _event(record, "scope", "Scope updated", decision.scope.explanation, decision.scope.model_dump(mode="json")); return await _execute(record, request)


@router.get("/investigations/{identifier}", response_model=InvestigationRecord)
async def get_investigation(identifier: UUID, request: Request) -> InvestigationRecord:
    return _load(request, identifier)


@router.post("/investigations/{identifier}/cancel", response_model=InvestigationRecord)
async def cancel_investigation(identifier: UUID, request: Request) -> InvestigationRecord:
    record = _load(request, identifier)
    if record.status not in {"completed", "refused", "failed", "cancelled"}:
        task = request.app.state.running.get(str(identifier))
        if task is not None:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
            return _load(request, identifier)
        event_start, turn_id = len(record.events), _latest_turn_id(record)
        if turn_id is None:
            record.turns.append(record.request.question); turn_id = f"turn-{len(record.turns):04d}"
        recorder = SecurityRecorder(str(identifier), turn_id, getattr(request.app.state, "security_secrets", {}))
        if event_start == len(record.events) and record.status == "created": recorder.record_policy()
        record.status = "cancelled"; _finalize_terminal(record, recorder, "cancelled", turn_id, event_start, record.scope); _save(record, request)
    return record


@router.post("/investigations/{identifier}/retry", response_model=InvestigationRecord)
async def retry_investigation(identifier: UUID, request: Request) -> InvestigationRecord:
    record = _load(request, identifier)
    if record.status not in {"failed", "cancelled"}: raise HTTPException(status_code=409, detail="only failed or cancelled investigations can be retried")
    return await _execute(record, request, retry=True)


@router.get("/investigations/{identifier}/events")
async def investigation_events(identifier: UUID, request: Request, after: int = 0) -> StreamingResponse:
    record = _load(request, identifier)
    header = request.headers.get("last-event-id")
    if header and header.isdigit(): after = max(after, int(header))

    async def stream():
        for item in record.events[max(0, after):]:
            data = json.dumps(item.model_dump(mode="json"), separators=(",", ":")); yield f"id: {item.sequence}\nevent: {item.event_type}\ndata: {data}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
