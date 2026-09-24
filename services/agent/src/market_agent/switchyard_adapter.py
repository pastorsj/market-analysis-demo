"""Drive Switchyard's escalation stream through LangChain model clients.

The NVIDIA LangChain middleware currently accepts an object with ``run`` while
the pinned Switchyard escalation API exposes ``run_stream``.  This adapter only
bridges those two public contracts; Switchyard still owns every routing
decision and every target request.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, cast
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_nvidia_switchyard import LangChainLlmClient
from langchain_nvidia_switchyard.request_mapper import SwitchyardRequestMapper
from langchain_nvidia_switchyard.response_mapper import SwitchyardResponseMapper
from switchyard.libsy import LlmResponse, Step

from .config import CAPABLE_MODEL


class TargetClient(Protocol):
    async def call(self, request: Mapping[str, object]) -> Mapping[str, object]: ...


TargetObserver = Callable[
    [str, str, Mapping[str, object], Callable[[], Awaitable[Mapping[str, object]]]],
    Awaitable[Mapping[str, object]],
]


class RelayHeaderCompatibilityMiddleware(AgentMiddleware[Any, Any, Any]):
    """Remove Relay transport headers before Switchyard's neutral mapper.

    Relay carries propagation headers in LangChain's ``extra_headers``
    model setting for providers other than ChatNVIDIA.  The pinned NVIDIA
    Switchyard request mapper intentionally rejects settings outside its
    provider-neutral schema.  Relay remains the outer tracing boundary; this
    narrow bridge removes only that transport-only field before routing.
    """

    @staticmethod
    def _without_transport_headers(request: ModelRequest[Any]) -> ModelRequest[Any]:
        settings = dict(request.model_settings)
        settings.pop("extra_headers", None)
        return request.override(model_settings=settings)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._without_transport_headers(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._without_transport_headers(request))


class ProviderVerifiedLlmClient(LangChainLlmClient):
    """Preserve the provider's raw model assertion before response mapping."""

    def __init__(self, model: BaseChatModel) -> None:
        super().__init__(model)
        self.model = model

    async def call(self, request: Mapping[str, object]) -> Mapping[str, object]:
        invocation = SwitchyardRequestMapper.from_switchyard(request)
        options = dict(invocation.options)
        # Submission middleware's short local budget travels through routing.
        # Ultra needs room for both reasoning and the complete typed tool call;
        # apply its bounded budget after selecting the actual target.
        if self._model_name() == CAPABLE_MODEL:
            options.pop("max_tokens", None)
            options["max_completion_tokens"] = 4096
        model: Any = self.model
        if invocation.tools:
            model = self.model.bind_tools(
                invocation.tools,
                tool_choice=cast(Any, invocation.tool_choice),
            )
        response = await model.ainvoke(
            invocation.messages,
            stop=invocation.stop,
            **options,
        )
        if not isinstance(response, AIMessage):
            raise ValueError(f"target returned {type(response).__name__} instead of AIMessage")
        configured = self._model_name()
        asserted = response.response_metadata.get("model_name")
        if asserted != configured:
            raise RuntimeError("identity_mismatch")
        # LangGraph fans parallel tool calls into separate branches and merges
        # their message updates by ID. Some OpenAI-compatible providers omit an
        # assistant-message ID; without one, the same parent assistant message
        # is duplicated once per branch and becomes invalid conversation
        # history on the next routed call. Stamp a process-unique transport ID
        # before the response crosses back through Switchyard.
        if response.id is None:
            response = response.model_copy(update={"id": f"switchyard-message-{uuid4().hex}"})
        return SwitchyardResponseMapper.to_switchyard(response, model_name=asserted)


def _aggregate(response: object) -> Mapping[str, object]:
    match response:
        case LlmResponse.Agg(body):
            return body
        case _:
            raise TypeError("buffered LangChain routing requires an aggregate response")


class EscalationAlgorithmAdapter:
    """Expose current ``run_stream`` escalation through middleware's ``run`` API."""

    def __init__(
        self,
        algorithm: Any,
        *,
        clients: Mapping[str, TargetClient],
        models: Mapping[str, list[str]],
        session_id: str,
        observe: TargetObserver | None = None,
    ) -> None:
        self._algorithm = algorithm
        self._clients = dict(clients)
        self._models = {name: list(values) for name, values in models.items()}
        self._session_id = session_id
        self._observe = observe

    async def _call(
        self,
        model_id: str,
        tier: str,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        client = self._clients.get(model_id)
        if client is None:
            raise RuntimeError(f"Switchyard selected unknown model {model_id!r}")
        # ``run_stream`` returns the algorithm-rewritten request plus an ordered
        # list of eligible targets.  Stamp the target selected by the host into
        # the request exactly as Switchyard's reference driver does.  The
        # LangChain client is bound to that same target, so the request and
        # transport cannot disagree about model identity.
        target_request = {**request, "model": model_id}
        if self._observe is not None:
            return await self._observe(
                model_id,
                tier,
                target_request,
                lambda: client.call(target_request),
            )
        return await client.call(target_request)

    def _tier(self, model_id: str) -> str:
        for tier in ("judge", "efficient", "capable"):
            if model_id in self._models.get(tier, ()):
                return tier
        return "unknown"

    async def run(
        self,
        request: Mapping[str, object],
    ) -> tuple[list[Mapping[str, object]], Mapping[str, object]]:
        decisions: list[Mapping[str, object]] = []
        headers = {"x-switchyard-session-id": self._session_id}
        async for step in self._algorithm.run_stream(request, self._models, headers=headers):
            match step:
                case Step.CallModel(call):
                    if not call.models:
                        error = RuntimeError("Switchyard requested a model call without a target")
                        call.fail(error)
                        raise error
                    model_id = call.models[0]
                    tier = self._tier(model_id)
                    try:
                        body = await self._call(model_id, tier, call.request)
                    except BaseException as exc:
                        call.fail(exc)
                        raise
                    call.respond(LlmResponse.Agg(body))
                    decisions.append(
                        {
                            "algorithm": call.algorithm,
                            "selected_model": model_id,
                            "selected_tier": tier,
                            "phase": "routing_call",
                        }
                    )
                case Step.Done(outcome):
                    if not outcome.selected_model_ids:
                        raise RuntimeError("Switchyard completed without selecting a model")
                    selected = outcome.selected_model_ids[0]
                    tier = self._tier(selected)
                    response = outcome.response
                    if response is None:
                        response = LlmResponse.Agg(await self._call(selected, tier, outcome.request))
                    metadata = outcome.metadata
                    decision: dict[str, object] = {
                        "algorithm": metadata.algorithm if metadata else "llm_classifier",
                        "selected_model": selected,
                        "selected_tier": tier,
                        "phase": "route_outcome",
                    }
                    if metadata is not None:
                        decision["outcome_id"] = metadata.outcome_id
                    decisions.append(decision)
                    return decisions, _aggregate(response)
        raise RuntimeError("Switchyard escalation stream ended without an outcome")


__all__ = [
    "EscalationAlgorithmAdapter",
    "ProviderVerifiedLlmClient",
    "RelayHeaderCompatibilityMiddleware",
]
