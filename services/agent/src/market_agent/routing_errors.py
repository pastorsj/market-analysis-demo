"""Stable routed-call failure classification and narrow retry eligibility."""

from __future__ import annotations

import asyncio

import httpx
from langchain_core.exceptions import ContextOverflowError
from openai import APIConnectionError, APITimeoutError

_CONTEXT_MARKERS = ("maximum context length", "context_length_exceeded", "context window exceeded")


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    """Return a finite cause/context chain, including malformed cyclic chains."""

    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _timed_out(chain: tuple[BaseException, ...]) -> bool:
    return any(
        isinstance(item, (asyncio.TimeoutError, APITimeoutError, httpx.TimeoutException))
        or "timeout" in type(item).__name__.lower() for item in chain
    )


def route_failure_class(exc: Exception) -> str:
    """Map the provider's observable failure into the stable public taxonomy."""

    primary_detail = str(exc).lower()
    chain = _exception_chain(exc)
    detail = " ".join(value for item in chain for value in (type(item).__name__, str(item), repr(item))).lower()
    context_overflow = any(
        isinstance(item, ContextOverflowError)
        or (
            getattr(item, "status_code", None) in {400, 413, 422}
            and any(marker in str(item).lower() for marker in _CONTEXT_MARKERS)
        )
        for item in chain
    )
    if primary_detail == "identity_mismatch":
        return "identity_mismatch"
    if context_overflow or (
        "openaiinvalidrequesterror" in detail
        and "error code: 400" in detail
        and any(marker in detail for marker in _CONTEXT_MARKERS[:2])
    ):
        return "context_length_exceeded"
    if any(isinstance(item, ValueError) and "response.invalid_tool_calls" in str(item) for item in chain):
        return "invalid_json"
    if _timed_out(chain):
        return "timeout"
    if "unable to complete request: max_output_tokens" in detail and "received model group=" in detail:
        return "provider_error"
    # This is an HTTP response from the internal OpenAI-compatible gateway,
    # not a failure to contact it.
    if "openaimodelnotfounderror" in detail and "received model group=" in detail and "404" in detail:
        return "route_unavailable"
    return "transport_error"


def retryable_remote_connection_error(exc: Exception) -> bool:
    """Accept only response-free connection errors; reject timeouts and HTTP errors."""

    chain = _exception_chain(exc)
    if _timed_out(chain):
        return False
    contacted = any(
        getattr(item, "status_code", None) is not None or getattr(item, "response", None) is not None
        for item in chain
    )
    if contacted:
        return False
    return any(isinstance(item, (APIConnectionError, httpx.ConnectError, ConnectionError)) for item in chain)
