"""Fail-closed final-report verification against its exact plan and evidence run."""

from __future__ import annotations

import json, re

from .evidence import EvidenceRun; from .planning import EvidencePlan; from .schemas import FinalReport, legacy_routing_projection; from .synthesis import accepted_evidence, evidence_reference_tickers


class VerificationError(ValueError):
    def __init__(self, code: str): self.code = code; super().__init__(code)


def _fail(code: str) -> None: raise VerificationError(code)


def _absence_sentence(value: str) -> str: value = re.sub(r"^[\s>*_`-]+|[\s.!?*_`]+$", "", value.lower()); return re.sub(r"\s+", " ", re.sub(r"\b(?:benchmark|comparison|data|evidence|for|is|was|were)\b", "", value)).strip()


def _mode(report: FinalReport) -> None:
    route = report.routing; inert = route.configured_model == "deterministic" and route.returned_model is None and not route.remote_attempted and route.requested_mode == route.effective_mode
    if report.answer_mode not in {"deterministic_policy", "deterministic_evidence", "model_synthesis"} or route.fallback_used: _fail("answer_mode_integrity")
    if report.answer_mode == "deterministic_policy" and (not inert or any((report.claims, report.citations, report.receipts, report.artifacts, report.model_attempts, report.switchyard_trials))): _fail("answer_mode_integrity")
    if report.answer_mode == "deterministic_evidence" and (not inert or report.model_attempts or report.switchyard_trials): _fail("answer_mode_integrity")
    if report.answer_mode == "model_synthesis":
        attempts = report.model_attempts; attempt = next((item for item in reversed(attempts) if item.role in {"agent_reasoning", "answer_synthesis"}), None) if attempts else _fail("answer_mode_integrity"); trials = {item.trial_id: item for item in report.switchyard_trials}
        formatters = [item for item in attempts if item.role == "report_formatting"]
        core = [item for item in attempts if item.role != "report_formatting"]
        routed = [item.switchyard_trial_id for item in core if item.switchyard_trial_id is not None]
        formatter_valid = not formatters or len(formatters) == 1 and formatters[0] is attempts[-1] and formatters[0].algorithm == "direct_frontier" and ((formatters[0].state == "succeeded" and formatters[0].validation_status == "valid") or (formatters[0].state == "failed" and formatters[0].validation_status == "invalid"))
        if attempt is None or not formatter_valid or any(item.state != "succeeded" or item.validation_status != "valid" or item.switchyard_trial_id is not None and item.switchyard_trial_id not in trials for item in core) or set(routed) != set(trials): _fail("answer_mode_integrity")
        expected = legacy_routing_projection(attempt, report.routing.requested_mode).model_copy(update={"remote_attempted": any(item.destination_class == "internal_inference" for item in core)})
        if report.routing != expected: _fail("answer_mode_integrity")


def verify_report(report: FinalReport, plan: EvidencePlan | None = None, evidence: EvidenceRun | None = None) -> FinalReport:
    try: FinalReport.model_validate(report.model_dump(mode="json"))
    except Exception: _fail("report_schema")
    _mode(report)
    if plan is None and evidence is None and report.answer_mode in {"deterministic_policy", "model_synthesis"}:
        if any((report.claims, report.citations, report.receipts, report.artifacts)): _fail("answer_mode_integrity")
        return report
    if report.answer_mode == "deterministic_policy":
        if plan is not None or evidence is not None: _fail("answer_mode_integrity")
        return report
    if not isinstance(plan, EvidencePlan) or not isinstance(evidence, EvidenceRun): _fail("evidence_integrity")
    try: accepted = accepted_evidence(report.scope, plan, evidence)
    except (KeyError, TypeError, ValueError) as exc: _fail(str(exc) if str(exc) in {"scope_integrity", "citation_integrity", "artifact_integrity"} else "evidence_integrity")
    _, rows, citations, receipts, artifacts = accepted
    for actual, expected, code in ((report.citations, citations, "citation_integrity"), (report.receipts, receipts, "receipt_integrity"), (report.artifacts, artifacts, "artifact_integrity")):
        if actual != expected: _fail(code)
    ids = {item.citation_id for item in citations}; claims = report.claims
    if len({item.claim_id for item in claims}) != len(claims) or any(set(item.citation_ids) - ids or item.kind != "limitation" and not item.citation_ids or item.kind == "inference" and item.confidence >= 1 for item in claims): _fail("claim_evidence")
    if all(row["outcome"] == "no_data" for row in rows) and any(item.kind != "limitation" for item in claims): _fail("outcome_integrity")
    if not ({item.message for item in evidence.limitations} | {item["message"] for row in rows for item in row["limitations"] if item["code"] != "optional_benchmark_unavailable"}) <= set(report.uncertainty): _fail("outcome_integrity")
    if report.answer_mode == "deterministic_evidence" and not all(row["outcome"] in plan.deterministic_answer_outcomes for row in rows): _fail("answer_mode_integrity")
    fields = (report.title, report.summary, *(item.text for item in report.claims), *report.uncertainty); text = " ".join(fields); source = json.dumps(rows, sort_keys=True)
    references = set(evidence_reference_tickers(evidence)); allowed = set(plan.resolved_members) | references; missing = {item["key"] for row in rows for item in row["coverage"] if item["dimension"] == "instrument" and item["status"] == "missing"}
    known_instruments = set(report.scope.supported_universe) | references | missing; sentences = [sentence for field in fields for sentence in re.split(r"(?<=[.!?])\s+|\n+", field)]
    foreign = {token for token in re.findall(r"\b[A-Z][A-Z0-9.-]{1,9}\b", text) if token in known_instruments and token not in allowed}; safe_absence = {token: {_absence_sentence(item["message"]) for row in rows for item in row["limitations"] if item["code"] == "optional_benchmark_unavailable" and token in item["affected"]} for token in missing}
    if any(token not in missing or any(_absence_sentence(sentence) not in safe_absence[token] for sentence in sentences if re.search(rf"(?<![A-Z0-9.-]){re.escape(token)}(?![A-Z0-9.-])", sentence)) for token in foreign) or "deepseek" in text.lower() and "deepseek" not in source.lower() or re.search(r"\bR1\b", text) and not re.search(r"\bR1\b", source): _fail("foreign_scope")
    if any(value in text.lower() for value in ("application fallback", "absent structured response", "guaranteed return", "risk-free", "i executed", "i placed the order")): _fail("answer_mode_integrity")
    return report
