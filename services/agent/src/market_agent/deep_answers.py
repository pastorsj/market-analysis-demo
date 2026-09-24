"""Typed terminal submissions and bounded answer prose."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .policy import PolicyDecision, PolicyKind

_RESEARCH_ANSWER_MAX = 2400
_SUBMISSION_ANSWER_MAX = 5000
_RAW_CITATION_ID = re.compile(r"\bcit-[a-f0-9]{12,64}\b", re.I)
_CITATION_ID_PARAGRAPH = re.compile(
    r"^[ \t]*citations?(?:\s+ids?)?\s*:[^\n]*" r"cit-[a-f0-9]{12,64}[^\n]*(?:\n|$)",
    re.I | re.M,
)
_CITATION_ID_LIST = re.compile(
    r"\s*citation\s+ids?\s*:\s*" r"(?:cit-[a-f0-9]{12,64}\s*[,;]?\s*)+\.?",
    re.I,
)
_INLINE_CITATION_CLAUSE = re.compile(
    r"\s*(?:\(\s*)?(?:(?:ranking|target|shock|model|observed)\s+)?"
    r"citations?(?:[ _]+ids?)?\s*:?\s*cit-[a-f0-9]{12,64}"
    r"(?:\s*\([^)]*\))?"
    r"(?:\s*(?:,|and)\s*cit-[a-f0-9]{12,64}(?:\s*\([^)]*\))?)*"
    r"\s*\)?\.?",
    re.I,
)


class ResearchAnswer(BaseModel):
    """Small terminal contract proven compatible with the live local model."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    answer: str = Field(min_length=1, max_length=_RESEARCH_ANSWER_MAX)
    citation_ids: tuple[str, ...] = Field(default=(), max_length=12)
    uncertainty: tuple[str, ...] = Field(default=(), max_length=6)


class ResearchAnswerInput(BaseModel):
    """Generous tool input normalized into the strict stored answer contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    answer: str = Field(min_length=1, max_length=_SUBMISSION_ANSWER_MAX)
    citation_ids: tuple[str, ...] = Field(default=(), max_length=12)
    uncertainty: tuple[str, ...] = Field(default=(), max_length=6)


def _normalize_answer_prose(value: str) -> str:
    """Remove internal citation syntax and retain complete prose within the UI bound."""
    cleaned = _CITATION_ID_PARAGRAPH.sub("", value)
    cleaned = _INLINE_CITATION_CLAUSE.sub(".", cleaned)
    cleaned = _CITATION_ID_LIST.sub(".", cleaned)
    cleaned = _RAW_CITATION_ID.sub("", cleaned)
    cleaned = re.sub(r"\s*\([\s,;]*\)", "", cleaned)
    cleaned = re.sub(r"\s*citation\s+ids?\s*:\s*[,;.\s]*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"[;:,]\s*\.", ".", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned).strip()
    if len(cleaned) <= _RESEARCH_ANSWER_MAX:
        return cleaned
    bounded = cleaned[:_RESEARCH_ANSWER_MAX]
    sentence_ends = [match.end() for match in re.finditer(r"[.!?](?=\s|$)", bounded)]
    if sentence_ends:
        return bounded[: sentence_ends[-1]].rstrip()
    paragraph_end = bounded.rfind("\n\n")
    if paragraph_end > 0:
        return bounded[:paragraph_end].rstrip()
    word_end = bounded.rfind(" ", 0, _RESEARCH_ANSWER_MAX - 1)
    return (bounded[:word_end] if word_end > 0 else bounded[:-1]).rstrip() + "…"


class GuideAnswer(BaseModel):
    """Narrow submission contract for policy paths that cannot produce research claims."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=5000)
    suggested_questions: tuple[str, ...] = Field(default=(), max_length=3)


def submission_validation_error(tools: list[Any], messages: list[Any]) -> str | None:
    """Describe invalid terminal fields without echoing generated content."""
    schema = next(
        (
            getattr(item, "args_schema", None)
            for item in tools
            if getattr(item, "name", None) == "submit_answer"
        ),
        None,
    )
    if schema is None:
        return None
    for message in messages:
        for call in getattr(message, "tool_calls", ()):
            if call.get("name") != "submit_answer":
                continue
            try:
                schema.model_validate(call.get("args", {}))
            except ValidationError as exc:
                return "; ".join(
                    f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                    for error in exc.errors(include_input=False, include_url=False)
                )
    return None


@dataclass
class AnswerSubmission:
    """Invocation-scoped sink for the agent's validated final answer."""

    answer: ResearchAnswer | GuideAnswer | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def accept(self, answer: ResearchAnswer | GuideAnswer) -> str:
        async with self.lock:
            if self.answer is not None:
                raise ValueError("the final answer was already submitted")
            self.answer = answer
        return "Validated answer accepted. End the task now."


def _submission_tool(submission: AnswerSubmission, decision: PolicyDecision) -> BaseTool:
    if decision.kind == PolicyKind.SUPPORTED:
        args_schema: type[BaseModel] = ResearchAnswerInput

        async def submit_answer(**payload: Any) -> str:
            raw = ResearchAnswerInput.model_validate(payload)
            answer = ResearchAnswer(
                answer=_normalize_answer_prose(raw.answer),
                citation_ids=raw.citation_ids,
                uncertainty=raw.uncertainty,
            )
            return await submission.accept(answer)

        description = (
            "Submit the final evidence-grounded answer exactly once, after loading one "
            "relevant skill and completing all required evidence work."
        )
    else:
        args_schema = GuideAnswer

        async def submit_answer(**payload: Any) -> str:
            guide = GuideAnswer.model_validate(payload)
            return await submission.accept(guide)

        description = (
            "Submit the final guide response exactly once. This policy path cannot submit "
            "research claims or answer mode."
        )

    return StructuredTool.from_function(
        name="submit_answer",
        description=description,
        coroutine=submit_answer,
        args_schema=args_schema,
        return_direct=True,
    )
