"""Public API models. The web UI's types in apps/web/src/api/types.ts mirror these."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Ticker = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")]
Question = Annotated[str, Field(min_length=2, max_length=2000)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Requests -------------------------------------------------------------------


class CreateInvestigation(Model):
    question: Question
    ticker: Ticker | None = None
    as_of: date | datetime | None = None
    event_id: Annotated[str | None, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)] = None


class FollowUp(Model):
    question: Question


# Scope ----------------------------------------------------------------------


class Scope(Model):
    """The companies and cutoff every tool call in an investigation is bound to."""

    status: Literal["resolved", "needs_input"]
    ticker: Ticker | None = None
    members: tuple[Ticker, ...] = ()
    as_of: datetime | None = None  # evidence cutoff: nothing later may be used
    session: date | None = None  # last completed trading session at the cutoff
    event_id: str | None = None
    missing: tuple[Literal["ticker", "date"], ...] = ()
    note: str | None = None


# Reports --------------------------------------------------------------------


class Citation(Model):
    model_config = ConfigDict(extra="ignore")  # tools send extra provenance fields

    citation_id: str
    title: str
    url: str | None
    source_type: Literal["market", "news", "filing", "release", "relationship", "model"]
    published_at: datetime
    available_at: datetime
    excerpt: str


class Receipt(Model):
    model_config = ConfigDict(extra="ignore")  # tools send extra provenance fields

    engine: str
    device: str
    duration_ms: float


class ToolSummary(Model):
    tool: str
    ticker: str
    outcome: Literal["ok", "partial", "no_data", "failed"]
    summary: str
    receipt: Receipt | None = None
    limitations: list[str] = []


class Artifact(Model):
    model_config = ConfigDict(extra="ignore")  # tools send extra provenance fields

    artifact_id: str
    kind: Literal["analogue_table", "comovement_graph", "topic_projection"]
    title: str
    data: dict[str, Any]


class Report(Model):
    kind: Literal["research", "guide"]
    answer: str
    citations: list[Citation] = []
    uncertainty: list[str] = []
    suggested_questions: list[str] = []
    limitations: list[str] = []
    tools: list[ToolSummary] = []
    artifacts: list[Artifact] = []


# Model calls and progress ---------------------------------------------------


class ModelCall(Model):
    """One physical model request made while Switchyard routed a reasoning step."""

    role: Literal["judge", "agent"]
    model: str
    tier: Literal["judge", "efficient", "capable"]
    state: Literal["succeeded", "failed"]
    latency_ms: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failure: str | None = None


class Span(Model):
    span_id: str
    parent_id: str | None = None
    kind: Literal["turn", "model", "tool", "skill"]
    name: str
    state: Literal["running", "succeeded", "failed", "cancelled"]
    started_at: datetime
    ended_at: datetime | None = None
    detail: dict[str, Any] = {}


class Event(Model):
    sequence: int
    turn: int
    at: datetime
    span: Span


# Investigations -------------------------------------------------------------

TurnStatus = Literal["running", "completed", "failed", "cancelled"]


class TurnError(Model):
    code: str
    message: str


class Turn(Model):
    number: int
    question: str
    status: TurnStatus
    started_at: datetime
    ended_at: datetime | None = None
    skill: str | None = None
    report: Report | None = None
    error: TurnError | None = None
    model_calls: list[ModelCall] = []


class Investigation(Model):
    investigation_id: UUID
    created_at: datetime
    updated_at: datetime
    status: TurnStatus  # status of the latest turn
    scope: Scope
    turns: list[Turn]
    events: list[Event] = []  # filled from the event log when returned by the API
