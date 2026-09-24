"""Switchyard escalation routing for every agent model step, with one Relay span per call.

Each Deep Agent reasoning step goes through ``SwitchyardRoutingMiddleware``. The
escalation classifier asks the judge (Luna) whether the local model's step is good
enough; if not, the capable model (Ultra) redoes it. Every physical request is
recorded as a ``ModelCall``, a progress span, and a Relay LLM span.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

import httpx
import nemo_relay
from langchain_core.messages import AIMessage
from langchain_nvidia_switchyard import LangChainLlmClient, SwitchyardRoutingMiddleware
from langchain_nvidia_switchyard.request_mapper import SwitchyardRequestMapper
from langchain_nvidia_switchyard.response_mapper import SwitchyardResponseMapper
from langchain_nvidia_switchyard.routed_chat_model import _SwitchyardChatModel
from langchain_openai import ChatOpenAI
from openai import APIConnectionError
from switchyard.libsy import EscalationClassifierConfig, LlmClassifierConfig, LlmResponse, Step, algorithms

from .context import current_turn
from .config import CAPABLE_MODEL, JUDGE_MODEL, LOCAL_MODEL, MODEL_URL, Settings
from .schemas import ModelCall

ESCALATION_PROMPT = (Path(__file__).parent / "prompts/escalation.md").read_text()
TIERS = {"judge": [JUDGE_MODEL], "efficient": [LOCAL_MODEL], "capable": [CAPABLE_MODEL]}


class RouteError(RuntimeError):
    """A model call or routing decision failed; ``code`` is shown to the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def chat_models(settings: Settings) -> dict[str, ChatOpenAI]:
    common = {"max_retries": 0, "timeout": 120, "use_responses_api": False}
    models = {
        LOCAL_MODEL: ChatOpenAI(
            model=LOCAL_MODEL,
            base_url=MODEL_URL,
            api_key="local-model",
            temperature=0,
            max_completion_tokens=2048,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            **common,
        )
    }
    if settings.remote_enabled:
        remote = {"base_url": settings.remote_url, "api_key": settings.remote_key, **common}
        models[JUDGE_MODEL] = ChatOpenAI(model=JUDGE_MODEL, max_completion_tokens=1024, **remote)
        models[CAPABLE_MODEL] = ChatOpenAI(model=CAPABLE_MODEL, max_completion_tokens=4096, **remote)
    return models


class VerifiedClient(LangChainLlmClient):
    """SDK client plus two checks the SDK does not do.

    The provider must report the model we asked for, and responses without a message
    ID get one: LangGraph merges parallel tool-call branches by message ID.
    """

    async def call(self, request: Mapping[str, object]) -> Mapping[str, object]:
        invocation = SwitchyardRequestMapper.from_switchyard(request)
        model: Any = self.model
        if invocation.tools:
            model = model.bind_tools(invocation.tools, tool_choice=cast(Any, invocation.tool_choice))
        response = await model.ainvoke(invocation.messages, stop=invocation.stop, **invocation.options)
        if not isinstance(response, AIMessage):
            raise RouteError("invalid_response", "the model returned no message")
        asserted = response.response_metadata.get("model_name")
        if asserted != self._model_name():
            raise RouteError(
                "identity_mismatch", f"asked for {self._model_name()}, provider answered as {asserted}"
            )
        if response.id is None:
            response = response.model_copy(update={"id": f"msg-{uuid4().hex}"})
        return SwitchyardResponseMapper.to_switchyard(response, model_name=asserted)


def _failure(exc: BaseException) -> str:
    if isinstance(exc, RouteError):
        return exc.code
    text = f"{type(exc).__name__} {exc}".lower()
    if "timeout" in text:
        return "timeout"
    if "context length" in text or "context_length" in text:
        return "context_length_exceeded"
    return "provider_error"


async def _observed(model_id: str, tier: str, invoke: Callable[[], Awaitable[Mapping[str, object]]]):
    """Run one physical model request with a progress span, a Relay span, and a ModelCall."""
    turn = current_turn.get()
    role = "judge" if tier == "judge" else "agent"
    name = "Routing judge" if role == "judge" else "Agent reasoning"
    span = f"model-{uuid4().hex[:12]}"
    for attempt in range(2):
        started = perf_counter()
        if turn:
            await turn.progress(
                span=span, kind="model", name=name, state="running", detail={"model": model_id, "tier": tier}
            )
        try:
            body = await nemo_relay.llm.execute(
                model_id,
                nemo_relay.LLMRequest({}, {"model": model_id, "tier": tier}),
                lambda _request: invoke(),
                model_name=model_id,
                data={"tier": tier},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            retry = (
                attempt == 0
                and model_id != LOCAL_MODEL
                and isinstance(exc, (APIConnectionError, httpx.ConnectError))
            )
            call = ModelCall(
                role=role,
                model=model_id,
                tier=tier,
                state="failed",
                latency_ms=(perf_counter() - started) * 1000,
                failure=_failure(exc),
            )
            if turn:
                turn.model_calls.append(call)
                await turn.progress(
                    span=span, kind="model", name=name, state="failed", detail=call.model_dump()
                )
            if retry:
                span = f"model-{uuid4().hex[:12]}"
                await asyncio.sleep(0.25)
                continue
            raise
        usage = body.get("usage") if isinstance(body.get("usage"), Mapping) else {}
        call = ModelCall(
            role=role,
            model=model_id,
            tier=tier,
            state="succeeded",
            latency_ms=(perf_counter() - started) * 1000,
            prompt_tokens=int(usage.get("input_tokens", 0) or 0),
            completion_tokens=int(usage.get("output_tokens", 0) or 0),
        )
        if turn:
            turn.model_calls.append(call)
            await turn.progress(
                span=span, kind="model", name=name, state="succeeded", detail=call.model_dump()
            )
        return body
    raise AssertionError("unreachable")


class EscalationAdapter:
    """Drive Switchyard's ``run_stream`` escalation API through the middleware's ``run`` API.

    The pinned langchain-nvidia-switchyard middleware calls ``algorithm.run``, while the
    pinned Switchyard escalation algorithm only exposes ``run_stream``. Remove this
    adapter once the two releases line up.
    """

    def __init__(self, models: Mapping[str, ChatOpenAI]):
        self.clients = {model_id: VerifiedClient(model) for model_id, model in models.items()}
        self.algorithm = algorithms.llm_classifier(
            LlmClassifierConfig.escalation(
                config=EscalationClassifierConfig(
                    confirmations=1,
                    recent_turn_window=28,
                    window_message_chars=4000,
                    prompt=ESCALATION_PROMPT,
                )
            )
        )

    def _tier(self, model_id: str) -> str:
        return next(tier for tier, ids in TIERS.items() if model_id in ids)

    async def _call(self, model_id: str, request: Mapping[str, object]) -> Mapping[str, object]:
        target = {**request, "model": model_id}
        return await _observed(model_id, self._tier(model_id), lambda: self.clients[model_id].call(target))

    async def run(
        self, request: Mapping[str, object]
    ) -> tuple[list[Mapping[str, object]], Mapping[str, object]]:
        turn = current_turn.get()
        headers = {"x-switchyard-session-id": turn.investigation_id if turn else "unscoped"}
        decisions: list[Mapping[str, object]] = []
        models = {**TIERS, "any": [LOCAL_MODEL, CAPABLE_MODEL, JUDGE_MODEL]}
        async for step in self.algorithm.run_stream(request, models, headers=headers):
            match step:
                case Step.CallModel(call):
                    model_id = call.models[0]
                    try:
                        body = await self._call(model_id, call.request)
                    except BaseException as exc:
                        call.fail(exc)
                        raise
                    call.respond(LlmResponse.Agg(body))
                    decisions.append({"phase": "routing_call", "selected_model": model_id})
                case Step.Done(outcome):
                    evidence = getattr(outcome.metadata, "evidence", None) or {}
                    if evidence.get("source") == "fail_open":
                        # The judge's verdict could not be parsed. Switchyard would keep the
                        # local answer; fail the step instead of hiding the missing judgment.
                        raise RouteError(
                            "judge_verdict_invalid", "the routing judge returned an unreadable verdict"
                        )
                    selected = outcome.selected_model_ids[0]
                    response = outcome.response
                    if response is None:
                        response = LlmResponse.Agg(await self._call(selected, outcome.request))
                    decisions.append({"phase": "route_outcome", "selected_model": selected, **evidence})
                    match response:
                        case LlmResponse.Agg(body):
                            return decisions, body
                    raise RouteError("invalid_response", "Switchyard returned a streaming response")
        raise RouteError("invalid_response", "Switchyard ended without an outcome")


class _RoutedModel(_SwitchyardChatModel):
    """Switchyard's routed model with a name, so Relay can label the routed step.

    Relay's per-step span needs a model name and the SDK's routed model has none; the
    physical calls inside the step get their own spans named after the real model.
    """

    model_name: str = "switchyard-escalation"


def switchyard_middleware(models: Mapping[str, ChatOpenAI]) -> SwitchyardRoutingMiddleware:
    middleware = SwitchyardRoutingMiddleware(EscalationAdapter(models))
    middleware._model = _RoutedModel(algorithm=middleware._model.algorithm)
    return middleware
