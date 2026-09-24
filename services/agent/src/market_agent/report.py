"""Turn the agent's structured answer and the turn's tool ledger into a report."""

from __future__ import annotations

import re

from .agent import AgentError, Answer
from .context import TurnContext
from .schemas import Artifact, Report

_CITATION_ID = re.compile(r"\s*\(?\bcit-[a-f0-9]{12,64}\b\)?")


def build_report(answer: Answer, context: TurnContext) -> Report:
    """Keep only citations the tools actually returned; fail if evidence was ignored."""
    ledger = context.ledger
    available = ledger.citations()
    cited = [available[item] for item in dict.fromkeys(answer.citation_ids) if item in available]
    if available and not cited:
        raise AgentError("uncited_answer", "The answer did not cite any of the evidence the tools returned.")
    artifacts: dict[str, Artifact] = {}
    limitations: list[str] = []
    for _, result in ledger.results:
        for artifact in result.artifacts:
            artifacts.setdefault(artifact.artifact_id, artifact)
        limitations += [item.message for item in result.limitations] + result.warnings
    limitations += [f"{item.tool} failed: {item.summary}" for item in ledger.failures]
    return Report(
        kind="research" if ledger.results else "guide",
        answer=_CITATION_ID.sub("", answer.answer).strip(),
        citations=cited,
        uncertainty=answer.uncertainty,
        suggested_questions=answer.suggested_questions,
        limitations=list(dict.fromkeys(limitations)),
        tools=ledger.summaries(),
        artifacts=list(artifacts.values()),
    )
