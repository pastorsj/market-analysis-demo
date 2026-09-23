"""Public routing failures classify errors without exposing provider payloads."""

import json

import pytest
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage
from langchain_nvidia_switchyard.response_mapper import SwitchyardResponseMapper

from market_agent.deep_middleware import _route_failure_class, RoutedCallObserver
from market_agent.config import LOCAL_MODEL
from market_agent.security import SecurityRecorder


def wrapped(cause):
    outer = RuntimeError("provider boundary failed")
    outer.__cause__ = cause
    return outer


@pytest.mark.parametrize("error", [
    ContextOverflowError("context budget exceeded"),
    wrapped(ContextOverflowError("context budget exceeded")),
    RuntimeError("internal error: OpenAIInvalidRequestError: Error code: 400 - maximum context length is 32768 tokens"),
])
def test_context_overflow_is_not_reported_as_network_contact_failure(error):
    assert _route_failure_class(error) == "context_length_exceeded"


def test_actual_switchyard_mapper_invalid_tool_json_has_distinct_failure():
    message = AIMessage(content="", invalid_tool_calls=[{
        "name": "submit_answer", "args": "{", "id": "call-fixture", "error": "invalid",
    }])
    with pytest.raises(ValueError) as caught:
        SwitchyardResponseMapper.to_switchyard(message, model_name="fixture")
    assert _route_failure_class(caught.value) == "invalid_json"
    assert _route_failure_class(wrapped(caught.value)) == "invalid_json"


@pytest.mark.parametrize("status, message, expected", [
    (400, "maximum context length exceeded", "context_length_exceeded"),
    (413, "context window exceeded", "context_length_exceeded"),
    (400, "invalid request parameter", "transport_error"),
    (500, "maximum context length diagnostic", "transport_error"),
])
def test_provider_status_and_context_marker_must_agree(status, message, expected):
    error = RuntimeError(message)
    error.status_code = status
    assert _route_failure_class(error) == expected


@pytest.mark.parametrize("error, expected", [
    (TimeoutError("deadline exceeded"), "timeout"),
    (ConnectionError("unreachable"), "transport_error"),
    (ValueError("unrelated invalid JSON in configuration"), "transport_error"),
    (RuntimeError("maximum context length mentioned in unrelated text"), "transport_error"),
    (RuntimeError("identity_mismatch"), "identity_mismatch"),
])
def test_unrelated_errors_do_not_trigger_broad_retry_or_context_classification(error, expected):
    assert _route_failure_class(error) == expected


@pytest.mark.asyncio
async def test_observer_records_only_safe_classification_for_context_error():
    captured = []
    async def progress(**event):
        captured.append(event)
    async def invoke():
        raise ContextOverflowError("sensitive-provider-payload: do not expose")
    observer = RoutedCallObserver(SecurityRecorder("investigation-errors-1234", "turn-errors-1234"), progress)
    with pytest.raises(ContextOverflowError):
        await observer(LOCAL_MODEL, "efficient", {}, invoke)
    assert observer.failure == "context_length_exceeded"
    assert observer.attempts[0].failure_class == "context_length_exceeded"
    serialized = json.dumps({"events": captured, "attempt": observer.attempts[0].model_dump(mode="json")}, default=str)
    assert "sensitive-provider-payload" not in serialized
