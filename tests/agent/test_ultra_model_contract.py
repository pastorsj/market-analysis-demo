"""Ultra is the active capable model; Sol remains historical/evaluation only."""

import pytest
from pydantic import ValidationError

from market_agent.config import CAPABLE_MODEL, LOCAL_MODEL, LUNA_MODEL, SOL_MODEL, Settings
from market_agent.schemas import ModelAttempt


def test_ultra_default_and_no_sol_runtime_fallback():
    assert Settings().remote_model == CAPABLE_MODEL == "nvidia/nvidia/nemotron-3-ultra"
    assert Settings().local_model == LOCAL_MODEL
    assert Settings().judge_model == LUNA_MODEL
    for unsupported in (
        SOL_MODEL,
        "nvidia/nvidia/llama-3.1-nemotron-ultra-253b-v1",
        "nvidia/nvidia/nemotron-3-ultra-evals",
    ):
        with pytest.raises(ValidationError):
            Settings(remote_model=unsupported)


@pytest.mark.parametrize("model", [CAPABLE_MODEL, SOL_MODEL])
def test_current_and_historical_capable_receipts_remain_readable(model):
    receipt = dict(
        role="agent_reasoning",
        algorithm="switchyard_escalation",
        destination_class="internal_inference",
        configured_model=model,
        model_assertion=model,
        identity_evidence="direct_provider_verified",
        state="succeeded",
        application_call_id="call-ultra-test",
        application_request_id="request-ultra-test",
        latency_ms=1,
        validation_status="valid",
        selected_tier="capable",
    )
    assert ModelAttempt.model_validate(receipt).configured_model == model
    with pytest.raises(ValidationError):
        ModelAttempt.model_validate({**receipt, "selected_tier": "judge"})
    with pytest.raises(ValidationError):
        ModelAttempt.model_validate({**receipt, "model_assertion": "unapproved-model"})
