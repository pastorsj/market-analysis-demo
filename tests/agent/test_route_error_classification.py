"""Public routing failures classify errors without exposing provider payloads."""

import json

import httpx
import pytest
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage
from langchain_nvidia_switchyard.response_mapper import SwitchyardResponseMapper
from openai import APIConnectionError

from market_agent.deep_middleware import _route_failure_class, RoutedCallObserver
from market_agent.config import CAPABLE_MODEL, LOCAL_MODEL
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
    (wrapped(TimeoutError("nested deadline exceeded")), "timeout"),
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


@pytest.mark.asyncio
async def test_remote_connection_failure_retries_once_with_distinct_audit_rows():
    captured = []
    calls = 0

    async def progress(**event):
        captured.append(event)

    async def invoke():
        nonlocal calls
        calls += 1
        if calls == 1:
            cause = httpx.ConnectError(
                "temporary name resolution failure",
                request=httpx.Request("POST", "https://inference.example.test/v1"),
            )
            error = APIConnectionError(
                request=httpx.Request("POST", "https://inference.example.test/v1")
            )
            error.__cause__ = cause
            raise error
        return {"model": CAPABLE_MODEL, "usage": {"total_tokens": 3}}

    recorder = SecurityRecorder("investigation-retry-1234", "turn-retry-1234")
    observer = RoutedCallObserver(recorder, progress)
    body = await observer(CAPABLE_MODEL, "capable", {}, invoke)

    assert body["model"] == CAPABLE_MODEL
    assert calls == 2
    assert [item.state for item in observer.attempts] == ["failed", "succeeded"]
    assert {item.role for item in observer.attempts} == {"agent_reasoning"}
    assert [item.failure_class for item in observer.attempts] == [
        "transport_error",
        None,
    ]
    assert len({item.application_call_id for item in observer.attempts}) == 2
    assert len({item.application_request_id for item in observer.attempts}) == 1
    assert [event["state"] for event in captured] == [
        "started",
        "failed",
        "started",
        "completed",
    ]
    assert len({event["key"] for event in captured}) == 2

    recorder.record_policy()
    recorder.reconcile({
        "evidence": {"records": []},
        "model_attempts": [
            item.model_dump(mode="json") for item in observer.attempts
        ],
    })
    receipt = recorder.finalize({})
    assert receipt.completeness == "verified"
    assert len(receipt.network_egress) == 2
    assert len({item.observation_id for item in receipt.network_egress}) == 2
    assert len({item.call_id for item in receipt.network_egress}) == 2
    assert len({item.application_request_id for item in receipt.network_egress}) == 1
    assert [item.outcome for item in receipt.network_egress] == [
        "failed",
        "succeeded",
    ]


@pytest.mark.asyncio
async def test_remote_connection_failure_gets_only_one_retry():
    captured = []
    calls = 0

    async def progress(**event):
        captured.append(event)

    async def invoke():
        nonlocal calls
        calls += 1
        raise ConnectionError("unreachable")

    observer = RoutedCallObserver(
        SecurityRecorder("investigation-retry-limit-1234", "turn-retry-limit-1234"),
        progress,
    )
    with pytest.raises(ConnectionError):
        await observer(CAPABLE_MODEL, "capable", {}, invoke)

    assert calls == 2
    assert len(observer.attempts) == 2
    assert len({item.application_call_id for item in observer.attempts}) == 2
    assert len({item.application_request_id for item in observer.attempts}) == 1
    assert [event["state"] for event in captured] == [
        "started",
        "failed",
        "started",
        "failed",
    ]


@pytest.mark.asyncio
async def test_remote_connection_retry_respects_physical_call_cap():
    captured = []
    calls = 0

    async def progress(**event):
        captured.append(event)

    async def invoke():
        nonlocal calls
        calls += 1
        raise ConnectionError("unreachable")

    observer = RoutedCallObserver(
        SecurityRecorder("investigation-retry-cap-1234", "turn-retry-cap-1234"),
        progress,
    )
    observer.counter = 47
    with pytest.raises(ConnectionError):
        await observer(CAPABLE_MODEL, "capable", {}, invoke)

    assert calls == 1
    assert observer.counter == 48
    assert len(observer.attempts) == 1
    assert [event["state"] for event in captured] == ["started", "failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    TimeoutError("deadline exceeded"),
    RuntimeError("identity_mismatch"),
    ValueError("response.invalid_tool_calls contains malformed JSON"),
    RuntimeError(
        "Unable to complete request: max_output_tokens; received model group=capable"
    ),
])
async def test_remote_non_connection_failures_are_not_retried(error):
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        raise error

    observer = RoutedCallObserver(
        SecurityRecorder("investigation-no-retry-1234", "turn-no-retry-1234"),
        lambda **event: _async_none(),
    )
    with pytest.raises(type(error)):
        await observer(CAPABLE_MODEL, "capable", {}, invoke)
    assert calls == 1
    assert len(observer.attempts) == 1


@pytest.mark.asyncio
async def test_remote_http_failure_is_not_retried_even_with_connection_cause():
    class HttpFailure(RuntimeError):
        status_code = 503

    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        error = HttpFailure("provider unavailable")
        error.__cause__ = ConnectionError("upstream closed")
        raise error

    observer = RoutedCallObserver(
        SecurityRecorder("investigation-http-1234", "turn-http-1234"),
        lambda **event: _async_none(),
    )
    with pytest.raises(HttpFailure):
        await observer(CAPABLE_MODEL, "capable", {}, invoke)
    assert calls == 1
    assert len(observer.attempts) == 1


@pytest.mark.asyncio
async def test_local_connection_failure_is_not_retried():
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        raise ConnectionError("local service unreachable")

    observer = RoutedCallObserver(
        SecurityRecorder("investigation-local-1234", "turn-local-1234"),
        lambda **event: _async_none(),
    )
    with pytest.raises(ConnectionError):
        await observer(LOCAL_MODEL, "efficient", {}, invoke)
    assert calls == 1
    assert len(observer.attempts) == 1


async def _async_none():
    return None
