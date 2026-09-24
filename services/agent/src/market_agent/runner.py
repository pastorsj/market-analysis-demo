"""Run investigation turns as background tasks and publish their progress.

One turn runs at a time (the local model and GPU are shared). Each turn's spans are
appended to the event log and fanned out to live SSE streams.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langgraph.errors import GraphRecursionError

from .agent import AgentError, MarketAgent
from .catalog import Coverage
from .context import TurnContext
from .report import build_report
from .routing import RouteError
from .schemas import Event, Investigation, Scope, Span, Turn, TurnError
from .store import Store

log = logging.getLogger(__name__)


class Busy(RuntimeError):
    """Another turn is already running."""


def now() -> datetime:
    return datetime.now(UTC)


class Runner:
    def __init__(self, store: Store, agent: MarketAgent | None, coverage: Coverage):
        self.store, self.agent, self.coverage = store, agent, coverage
        self.task: asyncio.Task | None = None
        self.running_id: UUID | None = None
        self.changed = asyncio.Condition()
        self.sequence: dict[UUID, int] = {}
        self.starts: dict[tuple[UUID, str], datetime] = {}
        self.version = 0
        self.attempt = 0

    # Streams --------------------------------------------------------------

    async def wait_for_change(self, seen: int, timeout: float = 15) -> None:
        """Wait until something changes after version ``seen`` (or the timeout passes)."""
        async with self.changed:
            try:
                await asyncio.wait_for(self.changed.wait_for(lambda: self.version != seen), timeout)
            except TimeoutError:
                pass

    async def _notify(self) -> None:
        async with self.changed:
            self.version += 1
            self.changed.notify_all()

    # Turns ----------------------------------------------------------------

    def _market_cutoff(self, scope: Scope) -> datetime | None:
        return next(
            (item.close_at for item in self.coverage.sessions if item.session_date == scope.session), None
        )

    def start(self, investigation: Investigation, question: str, *, retry: bool = False) -> None:
        if self.agent is None:
            raise RuntimeError("remote routing is disabled")
        if self.task is not None and not self.task.done():
            raise Busy("Another investigation is running. Wait for it to finish or cancel it.")
        if retry:
            investigation.turns[-1] = investigation.turns[-1].model_copy(
                update={
                    "status": "running",
                    "started_at": now(),
                    "ended_at": None,
                    "error": None,
                    "report": None,
                }
            )
        else:
            investigation.turns.append(
                Turn(
                    number=len(investigation.turns) + 1, question=question, status="running", started_at=now()
                )
            )
        investigation.status, investigation.updated_at = "running", now()
        self.store.save(investigation)
        self.running_id = investigation.investigation_id
        self.attempt += 1
        self.task = asyncio.create_task(self._run(investigation, retry=retry))

    async def cancel(self, investigation_id: UUID) -> bool:
        if self.running_id != investigation_id or self.task is None or self.task.done():
            return False
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        investigation = self.store.get(investigation_id)
        if investigation and investigation.status == "running":  # cancelled before the task started
            ended = now()
            investigation.turns[-1] = investigation.turns[-1].model_copy(
                update={
                    "status": "cancelled",
                    "ended_at": ended,
                    "error": TurnError(code="cancelled", message="Cancelled by the user."),
                }
            )
            investigation.status, investigation.updated_at = "cancelled", ended
            self.store.save(investigation)
            self.running_id = None
            await self._notify()
        return True

    async def _run(self, investigation: Investigation, *, retry: bool) -> None:
        turn = investigation.turns[-1]
        context = TurnContext(
            investigation_id=str(investigation.investigation_id),
            turn=turn.number,
            scope=investigation.scope,
            market_cutoff=self._market_cutoff(investigation.scope),
            progress=lambda **span: self._progress(investigation.investigation_id, turn.number, **span),
        )
        update: dict[str, Any] = {}
        try:
            await context.progress(span="turn", kind="turn", name=f"Turn {turn.number}", state="running")
            if retry:
                await self.agent.drop_last_turn(context)
            answer, skill = await self.agent.run_turn(context, turn.question)
            update = {"status": "completed", "skill": skill, "report": build_report(answer, context)}
        except asyncio.CancelledError:
            update = {
                "status": "cancelled",
                "error": TurnError(code="cancelled", message="Cancelled by the user."),
            }
        except (AgentError, RouteError) as exc:
            update = {"status": "failed", "error": TurnError(code=exc.code, message=str(exc))}
        except (ModelCallLimitExceededError, GraphRecursionError):
            message = "The agent used its step budget without finishing. Try a narrower question."
            update = {"status": "failed", "error": TurnError(code="step_limit", message=message)}
        except Exception as exc:
            log.exception("turn failed")
            message = f"{type(exc).__name__}: {exc}"[:300]
            update = {"status": "failed", "error": TurnError(code="agent_error", message=message)}
        finally:
            ended = now()
            final = investigation.turns[-1].model_copy(
                update={**update, "ended_at": ended, "model_calls": context.model_calls}
            )
            investigation.turns[-1] = final
            investigation.status, investigation.updated_at = final.status, ended
            state = {"completed": "succeeded", "failed": "failed", "cancelled": "cancelled"}[final.status]
            try:
                await context.progress(span="turn", kind="turn", name=f"Turn {turn.number}", state=state)
                self.store.save(investigation)
            except Exception:
                log.exception("could not store the finished turn")
                investigation.turns[-1] = final.model_copy(
                    update={
                        "status": "failed",
                        "report": None,
                        "error": TurnError(code="store_error", message="The result could not be stored."),
                    }
                )
                investigation.status = "failed"
                self.store.save(investigation)
            finally:
                self.running_id = None
                await self._notify()

    async def _progress(
        self,
        investigation_id: UUID,
        turn: int,
        *,
        span: str,
        kind: str,
        name: str,
        state: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        sequence = self.sequence.get(investigation_id)
        if sequence is None:
            existing = self.store.events(investigation_id)
            sequence = existing[-1].sequence if existing else 0
        sequence += 1
        self.sequence[investigation_id] = sequence
        at = now()
        span_id = f"t{turn}.{self.attempt}-{span}"
        started = at if state == "running" else self.starts.pop((investigation_id, span_id), at)
        if state == "running":
            self.starts[(investigation_id, span_id)] = at
        event = Event(
            sequence=sequence,
            turn=turn,
            at=at,
            span=Span(
                span_id=span_id,
                parent_id=None if kind == "turn" else f"t{turn}.{self.attempt}-turn",
                kind=kind,
                name=name,
                state=state,
                started_at=started,
                ended_at=None if state == "running" else at,
                detail=detail or {},
            ),
        )
        self.store.append(investigation_id, event)
        await self._notify()


def fail_interrupted(store: Store) -> None:
    """Turns still marked running when the service starts were interrupted by a restart."""
    for investigation in store.running():
        investigation.turns[-1] = investigation.turns[-1].model_copy(
            update={
                "status": "failed",
                "ended_at": now(),
                "error": TurnError(code="interrupted", message="The agent restarted while this was running."),
            }
        )
        investigation.status = "failed"
        store.save(investigation)


def new_investigation(scope: Scope) -> Investigation:
    created = now()
    return Investigation(
        investigation_id=uuid4(),
        created_at=created,
        updated_at=created,
        status="running",
        scope=scope,
        turns=[],
    )
