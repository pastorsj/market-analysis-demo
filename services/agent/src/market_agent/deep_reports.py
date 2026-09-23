"""Assemble and verify submitted answers against the retained evidence."""

from __future__ import annotations

from typing import Any

from .deep_analogue import (
    _analogue_claim_citation_ids,
    _correct_analogue_contract_prose,
    _correct_analogue_contract_uncertainty,
)
from .deep_answers import AnswerSubmission, GuideAnswer, ResearchAnswer
from .deep_evidence import EvidenceCollector
from .deep_middleware import RoutedCallObserver
from .evidence import EvidenceRun
from .planning import EvidencePlan, Intent
from .policy import PolicyDecision, PolicyKind
from .presentation import ReportPresenter, present_adaptively
from .reporting import assemble_model_report, no_data_report, policy_report
from .schemas import AnswerDraft, DraftClaim, GuideDraft
from .security import SecurityRecorder
from .synthesis import SynthesisResult, accepted_evidence
from .verification import verify_report


def _research_draft(
    answer: ResearchAnswer,
    plan: EvidencePlan,
    run: EvidenceRun,
    decision: PolicyDecision,
) -> AnswerDraft:
    """Attach the model's prose only to evidence accepted for this run."""
    _, _, citations, *_ = accepted_evidence(decision.scope, plan, run)
    available = tuple(item.citation_id for item in citations)
    requested = tuple(
        dict.fromkeys(item for item in answer.citation_ids if item in available)
    )
    selected = (
        (
            _analogue_claim_citation_ids(citations, run)
            if Intent.ANALOGUE in plan.intents
            else requested
        )
        or requested
        or available[:12]
    )
    if not selected:
        raise ValueError("invalid_evidence")
    members = plan.resolved_members
    label = " and ".join(members)
    cutoff = (
        (decision.effective_session_day or decision.scope.as_of.date()).isoformat()
        if decision.scope.as_of
        else "the selected cutoff"
    )
    uncertainty = tuple(dict.fromkeys(answer.uncertainty)) or (
        "Causal attribution is limited to the cutoff-qualified evidence returned by the selected tools.",
    )
    if Intent.ANALOGUE in plan.intents:
        uncertainty = _correct_analogue_contract_uncertainty(uncertainty, run)
    answer_text = (
        _correct_analogue_contract_prose(answer.answer, run)
        if Intent.ANALOGUE in plan.intents
        else answer.answer
    )
    claim = DraftClaim(
        text=answer_text,
        kind="inference",
        confidence=0.7,
        citation_ids=selected,
        tickers=members,
    )
    return AnswerDraft(
        title=f"{label} market analysis for {cutoff}",
        summary=answer_text,
        claims=(claim,),
        uncertainty=uncertainty,
    )


async def finalize_answer(
    *,
    base: dict[str, Any],
    collector: EvidenceCollector,
    observer: RoutedCallObserver,
    submission: AnswerSubmission,
    decision: PolicyDecision,
    presenter: ReportPresenter | None,
    recorder: SecurityRecorder,
    progress: Any,
) -> dict[str, Any]:
    """Apply the same citation and report contracts to every terminal submission."""
    attempts = tuple(observer.attempts)
    plan, run = collector.finish()
    state = {
        **base,
        "model_attempts": [item.model_dump(mode="json") for item in attempts],
        "plan": plan.model_dump(mode="json") if plan else None,
        "evidence": run.model_dump(mode="json") if run else None,
    }
    answer = submission.answer
    if answer is None:
        return {
            **state,
            "terminal": "synthesis_failure",
            "failure_code": "invalid_schema",
        }
    synthesis_attempt = next(
        (
            item
            for item in reversed(attempts)
            if item.role == "agent_reasoning" and item.state == "succeeded"
        ),
        None,
    )
    if synthesis_attempt is None:
        return {
            **state,
            "terminal": "route_failure",
            "failure_code": "response_incomplete",
        }
    try:
        if decision.kind != PolicyKind.SUPPORTED:
            if not isinstance(answer, GuideAnswer):
                raise ValueError("invalid_schema")
            guide = GuideDraft(
                title=answer.title,
                summary=answer.summary,
                suggested_questions=answer.suggested_questions,
            )
            report = verify_report(policy_report(decision, guide, attempts))
            terminal = (
                "refusal"
                if decision.kind == PolicyKind.REFUSAL
                else (
                    "help"
                    if decision.kind
                    in {PolicyKind.PRODUCT_HELP, PolicyKind.CONVERSATION}
                    else (
                        "clarification"
                        if decision.kind == PolicyKind.CLARIFICATION
                        else "partial"
                    )
                )
            )
        elif plan is None or run is None:
            raise ValueError("invalid_evidence")
        elif all(
            item.result and item.result["outcome"] == "no_data" for item in run.records
        ):
            if not isinstance(answer, ResearchAnswer):
                raise ValueError("invalid_schema")
            guide = GuideDraft(
                title=f"No eligible {decision.scope.ticker} evidence",
                summary=answer.answer,
                suggested_questions=(),
            )
            report = verify_report(
                no_data_report(
                    decision.scope,
                    plan,
                    run,
                    "switchyard_escalation",
                    guide,
                    attempts,
                ),
                plan,
                run,
            )
            terminal = "no_data"
        else:
            if not isinstance(answer, ResearchAnswer):
                raise ValueError("invalid_schema")
            draft = _research_draft(answer, plan, run, decision)
            presented = await present_adaptively(
                presenter,
                draft.summary,
                question=decision.request.question,
                recorder=recorder,
                progress=progress,
            )
            if presented.attempt is not None:
                attempts = (*attempts, presented.attempt)
            if presented.limitation is not None:
                draft = draft.model_copy(
                    update={
                        "uncertainty": tuple(
                            dict.fromkeys(
                                (
                                    *draft.uncertainty,
                                    presented.limitation,
                                )
                            )
                        )[:12]
                    }
                )
            elif presented.attempt is not None:
                formatted_claims = tuple(
                    item.model_copy(update={"text": presented.markdown})
                    for item in draft.claims
                )
                draft = draft.model_copy(
                    update={
                        "summary": presented.markdown,
                        "claims": formatted_claims,
                    }
                )
            state = {
                **state,
                "model_attempts": [item.model_dump(mode="json") for item in attempts],
            }
            report = verify_report(
                assemble_model_report(
                    decision.scope,
                    plan,
                    run,
                    SynthesisResult(draft, synthesis_attempt),
                    "switchyard_escalation",
                    attempts,
                ),
                plan,
                run,
            )
            terminal = "partial" if run.status == "partial" else "success"
    except Exception as exc:
        code = (
            str(exc)
            if str(exc)
            in {
                "invalid_citation",
                "invalid_scope",
                "invalid_evidence",
                "invalid_schema",
            }
            else "invalid_evidence"
        )
        return {**state, "terminal": "synthesis_failure", "failure_code": code}
    return {
        **state,
        "report": report.model_dump(mode="json"),
        "terminal": terminal,
        "failure_code": None,
        "node_trace": [*base["node_trace"], "report"],
    }
