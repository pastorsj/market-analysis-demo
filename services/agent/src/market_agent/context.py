"""Per-turn state shared by the evidence tools, the router, and the runner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .schemas import Artifact, Citation, ModelCall, Receipt, Scope, ToolSummary


class _Limitation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str
    message: str


class ToolResult(BaseModel):
    """The parts of the tools service's ToolResult the agent relies on."""

    model_config = ConfigDict(extra="ignore")
    tool: str
    as_of: datetime
    outcome: Literal["ok", "partial", "no_data"]
    data: dict[str, Any]
    citations: list[Citation]
    limitations: list[_Limitation] = []
    warnings: list[str] = []
    receipt: Receipt | None = None
    artifacts: list[Artifact] = []


Progress = Callable[..., Awaitable[None]]


@dataclass
class Ledger:
    """Everything the tools returned during one turn."""

    results: list[tuple[str, ToolResult]] = field(default_factory=list)
    failures: list[ToolSummary] = field(default_factory=list)

    def citations(self) -> dict[str, Citation]:
        return {item.citation_id: item for _, result in self.results for item in result.citations}

    def summaries(self) -> list[ToolSummary]:
        return [
            ToolSummary(
                tool=result.tool,
                ticker=ticker,
                outcome=result.outcome,
                summary=str(result.data.get("summary", "")),
                receipt=result.receipt,
                limitations=[item.message for item in result.limitations],
            )
            for ticker, result in self.results
        ] + self.failures


@dataclass
class TurnContext:
    """Per-turn state: tools get it through LangGraph's runtime context, the router
    through ``current_turn``."""

    investigation_id: str
    turn: int
    scope: Scope
    market_cutoff: datetime | None  # close of the scope's market session
    progress: Progress
    ledger: Ledger = field(default_factory=Ledger)
    model_calls: list[ModelCall] = field(default_factory=list)
    cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


current_turn: ContextVar[TurnContext | None] = ContextVar("current_turn", default=None)
