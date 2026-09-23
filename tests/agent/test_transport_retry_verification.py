"""Bounded transport retries remain explicit in verified report provenance."""

from datetime import datetime, timezone

import pytest

from market_agent.config import CAPABLE_MODEL, LOCAL_MODEL, LUNA_MODEL, SOL_MODEL
from market_agent.schemas import FinalReport, InvestigationScope, ModelAttempt, legacy_routing_projection
from market_agent.verification import VerificationError, verify_report


def _success(*, request_id: str, call_id: str, role: str = "agent_reasoning", model: str = CAPABLE_MODEL, tier: str = "capable") -> ModelAttempt:
    return ModelAttempt(
        role=role,
        algorithm="switchyard_escalation",
        destination_class="local_model" if model == LOCAL_MODEL else "internal_inference",
        configured_model=model,
        model_assertion=model,
        identity_evidence="direct_provider_verified",
        state="succeeded",
        application_call_id=call_id,
        application_request_id=request_id,
        latency_ms=25,
        tokens={"prompt": 10, "completion": 5, "total": 15},
        validation_status="valid",
        selected_tier=tier,
    )


def _failed(success: ModelAttempt, *, call_id: str) -> ModelAttempt:
    return success.model_copy(update={
        "model_assertion": None,
        "identity_evidence": "unavailable",
        "state": "failed",
        "failure_class": "transport_error",
        "application_call_id": call_id,
        "latency_ms": 5,
        "tokens": success.tokens.model_copy(update={"prompt": 0, "completion": 0, "total": 0}),
        "validation_status": "invalid",
    })


def _report(attempts: tuple[ModelAttempt, ...], final: ModelAttempt | None = None) -> FinalReport:
    selected = final or next(item for item in reversed(attempts) if item.state == "succeeded" and item.role == "agent_reasoning")
    routing = legacy_routing_projection(selected, "switchyard_escalation").model_copy(update={"remote_attempted": True})
    return FinalReport(
        title="Verified answer",
        summary="The answer retained its complete model provenance.",
        scope=InvestigationScope(status="supported", action="answer", ticker="NVDA", supported_universe=("NVDA",), explanation="Verified scope.", resolved_tickers=("NVDA",)),
        claims=[],
        citations=[],
        routing=routing,
        answer_mode="model_synthesis",
        model_attempts=attempts,
        generated_at=datetime.now(timezone.utc),
    )


def test_report_accepts_one_adjacent_remote_transport_retry_per_request():
    judge = _success(request_id="request-judge-0001", call_id="call-judge-success", role="routing_judge", model=LUNA_MODEL, tier="judge")
    capable = _success(request_id="request-capable-01", call_id="call-capable-success")
    report = _report((_failed(judge, call_id="call-judge-failed"), judge, _failed(capable, call_id="call-capable-failed"), capable))

    assert verify_report(report) is report


@pytest.mark.parametrize("case", ["wrong_failure", "nonzero_tokens", "wrong_target", "same_call", "nonadjacent", "third_attempt", "local_retry", "direct_retry"])
def test_report_rejects_every_other_duplicate_request_shape(case: str):
    success = _success(request_id="request-capable-01", call_id="call-capable-success")
    failed = _failed(success, call_id="call-capable-failed")
    attempts: tuple[ModelAttempt, ...] = (failed, success)
    final = success
    if case == "wrong_failure":
        attempts = (failed.model_copy(update={"failure_class": "timeout"}), success)
    elif case == "nonzero_tokens":
        attempts = (failed.model_copy(update={"tokens": failed.tokens.model_copy(update={"prompt": 1, "total": 1})}), success)
    elif case == "wrong_target":
        other = _success(request_id=success.application_request_id, call_id=success.application_call_id, model=SOL_MODEL)
        attempts, final = (failed, other), other
    elif case == "same_call":
        attempts = (failed.model_copy(update={"application_call_id": success.application_call_id}), success)
    elif case == "nonadjacent":
        judge = _success(request_id="request-judge-0001", call_id="call-judge-success", role="routing_judge", model=LUNA_MODEL, tier="judge")
        attempts = (failed, judge, success)
    elif case == "third_attempt":
        attempts = (failed, success, success.model_copy(update={"application_call_id": "call-capable-third"}))
    elif case == "local_retry":
        local = _success(request_id=success.application_request_id, call_id=success.application_call_id, model=LOCAL_MODEL, tier="efficient")
        attempts, final = (_failed(local, call_id=failed.application_call_id), local), local
    elif case == "direct_retry":
        direct = success.model_copy(update={"role": "answer_synthesis", "algorithm": "direct_frontier", "selected_tier": None})
        attempts, final = (_failed(direct, call_id=failed.application_call_id), direct), direct

    with pytest.raises(VerificationError, match="answer_mode_integrity"):
        verify_report(_report(attempts, final))
