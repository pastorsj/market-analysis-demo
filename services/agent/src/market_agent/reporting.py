"""Named report constructors over policy decisions and accepted evidence."""

from __future__ import annotations

from datetime import datetime, timezone; from .evidence import EvidenceRun; from .planning import EvidencePlan, Intent; from .policy import PolicyDecision, PolicyKind; from .schemas import Claim, FinalReport, GuideDraft, InvestigationScope, ModelAttempt, RouteMode, RoutingReceipt, SwitchyardTrialBundle, legacy_routing_projection; from .synthesis import SynthesisResult, accepted_evidence


class ReportAssemblyError(ValueError): pass
def _need(ok, message): return None if ok else (_ for _ in ()).throw(ReportAssemblyError(message))


def _route(mode: RouteMode) -> RoutingReceipt: return RoutingReceipt(requested_mode=mode, effective_mode=mode, configured_model="deterministic", reason="No model was required for this deterministic answer.")


def _answer_attempt(attempts: tuple[ModelAttempt, ...]) -> ModelAttempt:
    return next((item for item in reversed(attempts) if item.role in {"agent_reasoning", "answer_synthesis"} and item.state == "succeeded"), None) or (_ for _ in ()).throw(ReportAssemblyError("successful agent reasoning attempt required"))


def _routing(attempt: ModelAttempt, mode: RouteMode, attempts: tuple[ModelAttempt, ...]) -> RoutingReceipt:
    receipt = legacy_routing_projection(attempt, mode)
    return receipt.model_copy(update={"remote_attempted": any(item.destination_class == "internal_inference" for item in attempts if item.role != "report_formatting")})


def policy_report(decision: PolicyDecision, guide: GuideDraft | None = None, attempts: tuple[ModelAttempt, ...] = (), trials: tuple[SwitchyardTrialBundle, ...] = ()) -> FinalReport:
    titles = {PolicyKind.PRODUCT_HELP: "Market-shock analysis help", PolicyKind.CONVERSATION: "Market question scope", PolicyKind.REFUSAL: "Request not supported", PolicyKind.CLARIFICATION: "More scope is needed", PolicyKind.PARTIAL_SUPPORT: "Request is outside validated scope", PolicyKind.NO_DATA: f"No eligible {decision.scope.ticker} evidence"}; _need(decision.kind in titles, "supported investigation requires evidence")
    reason = "missing_news" if decision.kind == PolicyKind.NO_DATA and decision.reason.name == "KNOWN_EVIDENCE_GAP" else None
    if guide is None:
        _need(decision.kind == PolicyKind.REFUSAL, "only hard safety refusals are model-free"); return FinalReport(title=titles[decision.kind], summary=decision.scope.explanation, scope=decision.scope, no_data_reasons=[], claims=[], citations=[], uncertainty=[], routing=_route(decision.request.route_mode), answer_mode="deterministic_policy", generated_at=datetime.now(timezone.utc))
    attempt = _answer_attempt(attempts); _need(decision.kind != PolicyKind.NO_DATA or reason, "agent guide attempts required"); return FinalReport(title=guide.title, summary=guide.summary + (" Suggested questions: " + " ".join(guide.suggested_questions) if guide.suggested_questions else ""), scope=decision.scope, no_data_reasons=[] if reason is None else [reason], claims=[], citations=[], uncertainty=[], routing=_routing(attempt, decision.request.route_mode, attempts), answer_mode="model_synthesis", model_attempts=attempts, switchyard_trials=trials, generated_at=datetime.now(timezone.utc))


def _authority(scope: InvestigationScope, plan: EvidencePlan, run: EvidenceRun):
    try: return accepted_evidence(scope, plan, run, normalize_scope=True)
    except (KeyError, TypeError, ValueError) as exc: raise ReportAssemblyError("accepted evidence run required") from exc


def _limits(run: EvidenceRun, rows: list[dict]) -> list[str]: values = [item.message for item in run.limitations] + [item["message"] for row in rows for item in row["limitations"] if item["code"] != "optional_benchmark_unavailable"]; return list(dict.fromkeys(values))[:12]


def no_data_report(scope: InvestigationScope, plan: EvidencePlan, evidence: EvidenceRun, requested_mode: RouteMode, guide: GuideDraft, attempts: tuple[ModelAttempt, ...], trials: tuple[SwitchyardTrialBundle, ...] = ()) -> FinalReport:
    scope, rows, citations, receipts, artifacts = _authority(scope, plan, evidence); empty = [row for row in rows if row["outcome"] == "no_data"]; _need("no_data" in plan.deterministic_answer_outcomes and bool(empty) and not any(row["outcome"] != "no_data" for row in rows), "no-data evidence required"); codes = {item["code"] for row in empty for item in row["limitations"]}; reasons = ["missing_news"] if all(row["tool"] == "search_news" for row in empty) and codes <= {"no_eligible_documents", "no_semantic_matches"} else []; attempt = _answer_attempt(attempts); _need(bool(reasons), "typed agent no-data result required"); return FinalReport(title=guide.title, summary=guide.summary, scope=scope, no_data_reasons=reasons, claims=[], citations=citations, uncertainty=_limits(evidence, rows), receipts=receipts, routing=_routing(attempt, requested_mode, attempts), answer_mode="model_synthesis", model_attempts=attempts, switchyard_trials=trials, artifacts=artifacts, generated_at=datetime.now(timezone.utc))


def _ranked_model_claims(plan: EvidencePlan, draft, citations):
    source_types = {item.citation_id: item.source_type for item in citations}
    def score(claim) -> int:
        text = claim.text.lower(); kinds = {source_types.get(item) for item in claim.citation_ids}
        rules = ((Intent.RISK, ("risk", "volatility", "probability", "predicted", "estimate")), (Intent.ANALOGUE, ("analogue", "analog", "distance", "similar")), (Intent.PROPAGATION, ("path", "relationship", "connected", " to ")), (Intent.TOPIC, ("topic", "cluster", "projection")))
        value = int(claim.text == draft.summary) + sum(100 for intent, terms in rules if intent in plan.intents and (intent == Intent.PROPAGATION and "relationship" in kinds or any(term in text for term in terms)))
        value += 120 * (len(plan.resolved_members) > 1 and "market" in kinds and "return" in text)
        value += 90 * bool({Intent.CATALYST, Intent.SOURCE} & set(plan.intents) and kinds & {"news", "filing", "release"})
        if Intent.SHOCK in plan.intents: value += next((points for term, points in (("market-adjusted return", 70), ("return", 60), ("volume ratio", 50), ("median volume", 50), ("shock score", 50), ("abnormal", 50)) if term in text), 0)
        if Intent.PRICE in plan.intents: value += 10 * any(term in text for term in ("return", "close", "open", "high", "low", "volume"))
        return value
    return sorted(enumerate(draft.claims), key=lambda item: (-score(item[1]), item[0]))


def _model_summary(plan: EvidencePlan, ranked) -> str:
    ordered = [claim for _, claim in ranked]
    if len(plan.resolved_members) <= 1: return ordered[0].text
    selected = []
    for ticker in plan.resolved_members:
        if (claim := next((item for item in ordered if ticker in item.tickers and item not in selected), None)) is not None: selected.append(claim)
    if len(selected) <= 1: return ordered[0].text
    summary = " ".join(claim.text for claim in selected)
    while len(summary) > 5000 and selected: selected.pop(); summary = " ".join(claim.text for claim in selected)
    return summary or ordered[0].text


def assemble_model_report(scope: InvestigationScope, plan: EvidencePlan, evidence: EvidenceRun, synthesis: SynthesisResult, requested_mode: RouteMode, attempts: tuple[ModelAttempt, ...] = (), trials: tuple[SwitchyardTrialBundle, ...] = ()) -> FinalReport:
    scope, rows, citations, receipts, artifacts = _authority(scope, plan, evidence); attempt = synthesis.attempt; attempts = attempts or (attempt,); _need(attempt in attempts and attempt.state == "succeeded" and attempt.validation_status == "valid" and (attempt.switchyard_trial_id is None) == (synthesis.trial_bundle is None), "valid synthesis result required"); ranked = _ranked_model_claims(plan, synthesis.draft, citations); title, extra = synthesis.draft.title, []; claims = [Claim(claim_id=f"claim-model-{index}", text=item.text, kind=item.kind, confidence=item.confidence, citation_ids=list(item.citation_ids)) for index, (_, item) in enumerate(ranked, 1)]; summary = _model_summary(plan, ranked)
    uncertainty = list(dict.fromkeys((*synthesis.draft.uncertainty, *_limits(evidence, rows), *extra)))[:12]; return FinalReport(title=title, summary=summary, scope=scope, claims=claims, citations=citations, uncertainty=uncertainty, receipts=receipts, routing=_routing(attempt, requested_mode, attempts), answer_mode="model_synthesis", model_attempts=attempts, switchyard_trials=trials, artifacts=artifacts, generated_at=datetime.now(timezone.utc))
