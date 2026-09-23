"""Submission discipline, routed-call receipts, and Relay middleware ordering."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import HumanMessage
from langchain_nvidia_switchyard import SwitchyardRoutingMiddleware
from nemo_relay.integrations.deepagents import NemoRelayDeepAgentsMiddleware

from .config import LOCAL_MODEL
from .generation_health import generation_health
from .deep_answers import submission_validation_error
from .deep_evidence import EvidenceCollector
from .deep_submission import EvidenceSubmissionRepair, current_skill_loaded, typed_submission_correction, valid_skill_call
from .schemas import ModelAttempt, TokenCounts
from .security import SecurityRecorder
from .switchyard_adapter import RelayHeaderCompatibilityMiddleware


def _tokens(body: Mapping[str, object]) -> TokenCounts:
    usage = body.get("usage") if isinstance(body.get("usage"), Mapping) else {}
    prompt = int(usage.get("input_tokens", 0) or 0)
    completion = int(usage.get("output_tokens", 0) or 0)
    return TokenCounts(
        prompt=prompt,
        completion=completion,
        total=max(prompt + completion, int(usage.get("total_tokens", 0) or 0)),
    )


def _route_failure_class(exc: Exception) -> str:
    """Map the provider's observable failure into the stable public taxonomy."""

    primary_detail = str(exc).lower()
    details: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    context_overflow = False
    invalid_tool_json = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        context_overflow |= isinstance(current, ContextOverflowError)
        invalid_tool_json |= isinstance(current, ValueError) and "response.invalid_tool_calls" in str(current)
        if getattr(current, "status_code", None) in {400, 413, 422}:
            context_overflow |= any(marker in str(current).lower() for marker in (
                "maximum context length", "context_length_exceeded", "context window exceeded",
            ))
        details.extend((type(current).__name__, str(current), repr(current)))
        current = current.__cause__ or current.__context__
    detail = " ".join(details).lower()
    if primary_detail == "identity_mismatch":
        return "identity_mismatch"
    if context_overflow or (
        "openaiinvalidrequesterror" in detail and "error code: 400" in detail
        and any(marker in detail for marker in ("maximum context length", "context_length_exceeded"))
    ):
        return "context_length_exceeded"
    if invalid_tool_json:
        return "invalid_json"
    if "timeout" in type(exc).__name__.lower():
        return "timeout"
    if (
        "unable to complete request: max_output_tokens" in detail
        and "received model group=" in detail
    ):
        return "provider_error"
    # The internal OpenAI-compatible gateway uses this 404 when an advertised
    # model group has no currently available provider target. The request did
    # reach the gateway, so presenting it as a generic transport/contact error
    # sends the visitor and operator in the wrong direction.
    if (
        "openaimodelnotfounderror" in detail
        and "received model group=" in detail
        and "404" in detail
    ):
        return "route_unavailable"
    return "transport_error"


class RoutedCallObserver:
    """Record every real judge/efficient/capable network call and UI span."""

    def __init__(self, recorder: SecurityRecorder, progress: Any):
        self.recorder, self.progress = recorder, progress
        self.attempts: list[ModelAttempt] = []
        self.counter = 0
        self.failure: str | None = None

    async def __call__(
        self,
        model_id: str,
        tier: str,
        request: Mapping[str, object],
        invoke: Callable[[], Awaitable[Mapping[str, object]]],
    ) -> Mapping[str, object]:
        self.failure = None
        if self.counter >= 48:
            self.failure = "agent_turn_limit"
            raise RuntimeError("physical model-call limit reached")
        self.counter += 1
        key = f"model-{self.counter}"
        call_id = f"call-{uuid4().hex}"
        request_id = f"request-{uuid4().hex}"
        boundary, destination = (
            ("local_model", "local_model")
            if model_id == LOCAL_MODEL
            else ("frontier_model", "internal_inference")
        )
        started = perf_counter()
        display = "Routing judge" if tier == "judge" else "Agent reasoning"
        await self.progress(
            key=key,
            kind="model",
            display_name=display,
            state="started",
            route_mode="switchyard_escalation",
            configured_model=model_id,
            selected_tier=tier,
        )
        observation = self.recorder.expect_network(boundary, destination)
        try:
            body = await invoke()
            assertion = (
                body.get("model") if isinstance(body.get("model"), str) else None
            )
            if assertion != model_id:
                raise RuntimeError("identity_mismatch")
        except asyncio.CancelledError:
            self.recorder.finish_network(
                observation,
                attempt="attempted",
                outcome="failed",
                call_id=call_id,
                application_request_id=request_id,
            )
            raise
        except Exception as exc:
            failure = _route_failure_class(exc)
            generation_health.observe_failure(model_id, failure)
            self.failure = failure
            attempt = ModelAttempt(
                role="routing_judge" if tier == "judge" else "routing_candidate",
                algorithm="switchyard_escalation",
                destination_class=destination,
                configured_model=model_id,
                identity_evidence="unavailable",
                state="failed",
                failure_class=failure,
                application_call_id=call_id,
                application_request_id=request_id,
                latency_ms=max(0.0, (perf_counter() - started) * 1000),
                validation_status="invalid",
                selected_tier=tier,
            )
            self.attempts.append(attempt)
            self.recorder.finish_network(
                observation,
                attempt="attempted",
                outcome="failed",
                call_id=call_id,
                application_request_id=request_id,
            )
            await self.progress(
                key=key,
                kind="model",
                display_name=display,
                state="failed",
                route_mode="switchyard_escalation",
                **_attempt_progress(attempt),
            )
            raise
        attempt = ModelAttempt(
            role="routing_judge" if tier == "judge" else "agent_reasoning",
            algorithm="switchyard_escalation",
            destination_class=destination,
            configured_model=model_id,
            model_assertion=model_id,
            identity_evidence="direct_provider_verified",
            state="succeeded",
            application_call_id=call_id,
            application_request_id=request_id,
            latency_ms=max(0.0, (perf_counter() - started) * 1000),
            tokens=_tokens(body),
            validation_status="valid",
            selected_tier=tier,
        )
        self.failure = None
        self.attempts.append(attempt)
        self.recorder.finish_network(
            observation,
            attempt="attempted",
            outcome="succeeded",
            call_id=call_id,
            application_request_id=request_id,
            identity_evidence="direct_provider_verified",
        )
        await self.progress(
            key=key,
            kind="model",
            display_name=display,
            state="completed",
            route_mode="switchyard_escalation",
            **_attempt_progress(attempt),
        )
        return body


class AgentActivityCallback(AsyncCallbackHandler):
    """Expose skill and agent work without logging prompts or chain-of-thought."""

    def __init__(self, progress: Any):
        self.progress = progress
        self.pending: dict[UUID, tuple[str, str]] = {}

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = str((serialized or {}).get("name", ""))
        if name not in {"read_file", "task"}:
            return
        label = "Skill load" if name == "read_file" else "Subagent analysis"
        key = f"activity-{run_id.hex}"
        self.pending[run_id] = (key, label)
        await self.progress(
            key=key, kind="planning", display_name=label, state="started"
        )

    async def on_tool_end(self, output, *, run_id, **kwargs):
        if item := self.pending.pop(run_id, None):
            await self.progress(
                key=item[0], kind="planning", display_name=item[1], state="completed"
            )

    async def on_tool_error(self, error, *, run_id, **kwargs):
        if item := self.pending.pop(run_id, None):
            await self.progress(
                key=item[0], kind="planning", display_name=item[1], state="failed"
            )


class RequireAnswerSubmissionMiddleware(AgentMiddleware[Any, Any, Any]):
    """Repair one attempted plain-text final into the typed answer tool call.

    Deep Agents must remain free to choose skills and evidence tools.  We therefore
    do not set a global tool choice (which would force a tool on every reasoning
    turn). Only a tool-free final or duplicate skill load gets one short,
    ordinarily routed correction turn. A second miss fails fast instead of
    entering an unbounded agent loop.
    """

    def __init__(
        self, selected_skill: str | None = None,
        collector: EvidenceCollector | None = None,
    ):
        self.selected_skill = selected_skill
        self.collector = collector
        self.evidence_repair = EvidenceSubmissionRepair(collector, selected_skill)

    @staticmethod
    def _tool_names(response: ModelResponse[Any]) -> list[str]:
        return [
            str(call.get("name", "")) for message in response.result
            for call in (getattr(message, "tool_calls", None) or ())]

    @staticmethod
    def _keep_first_submission(response: ModelResponse[Any]) -> ModelResponse[Any]:
        result: list[Any] = []
        kept = False
        for message in response.result:
            calls = list(getattr(message, "tool_calls", None) or ())
            if not calls:
                result.append(message)
                continue
            selected = next(
                (call for call in calls
                 if not kept and call.get("name") == "submit_answer"),
                None)
            if selected is None:
                continue
            kept = True
            additional = dict(getattr(message, "additional_kwargs", {}) or {})
            additional.pop("tool_calls", None)
            result.append(
                message.model_copy(
                    update={
                        "content": "",
                        "tool_calls": [selected],
                        "additional_kwargs": additional,
                    }
                )
            )
        return ModelResponse(result=result, structured_response=response.structured_response)

    async def awrap_model_call(self, request: ModelRequest[Any],
                               handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]]) -> ModelResponse[Any]:
        raw_handler = handler

        async def guarded(inner: ModelRequest[Any]) -> ModelResponse[Any]:
            allowed = {
                str((item.get("function") or item).get("name", ""))
                if isinstance(item, Mapping) else str(getattr(item, "name", ""))
                for item in inner.tools
            }
            result = await raw_handler(inner)
            if set(self._tool_names(result)) <= allowed:
                return result
            prompt = HumanMessage(content=(
                "Your preceding response requested an unavailable tool. Retry once using "
                "only tools displayed in this request. Do not invent or reuse a hidden tool."
            ))
            retry = inner.override(messages=[*inner.messages, prompt],
                                   model_settings={**inner.model_settings, "max_completion_tokens": 800})
            result = await raw_handler(retry)
            if not set(self._tool_names(result)) <= allowed:
                raise RuntimeError("the Deep Agent requested an unavailable tool after correction")
            return result

        handler = guarded
        response = await self._generate_submission(request, handler)
        response = await self.evidence_repair.correct(request, response, handler)
        error = submission_validation_error(request.tools, response.result)
        if error is None:
            return response
        # A return-direct tool ends the graph even when LangChain rejects its
        # arguments. Repair through the routed model before executing that tool.
        repaired = await handler(
            request.override(
                messages=[
                    *request.messages,
                    HumanMessage(content=typed_submission_correction(request.tools, response, error)),
                ],
                tools=[
                    item
                    for item in request.tools
                    if getattr(item, "name", None) == "submit_answer"
                ],
                tool_choice=None,
                model_settings={**request.model_settings, "max_completion_tokens": 800},
            )
        )
        if self._tool_names(repaired) != ["submit_answer"] or submission_validation_error(request.tools, repaired.result):
            raise RuntimeError("the Deep Agent did not produce a valid typed final submission")
        return repaired

    async def _generate_submission(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        completed_tools = (
            {
                record.identity.tool
                for record in self.collector.records.values()
                if record.result is not None
            }
            if self.collector is not None
            else set()
        )
        evidence_complete = (
            self.selected_skill == "market-dislocation"
            and {"get_price_context", "detect_market_shock", "search_news"}
            <= completed_tools
        )
        if evidence_complete:
            submission_tools = [
                item
                for item in request.tools
                if getattr(item, "name", None) == "submit_answer"
            ]
            if len(submission_tools) != 1:
                raise RuntimeError("the typed answer tool is unavailable")
            settings = {**request.model_settings, "max_completion_tokens": 800}
            messages = [
                *request.messages,
                HumanMessage(
                    content=(
                        "The current turn's bounded evidence pass is complete. No evidence "
                        "tools remain. Call submit_answer exactly once now."
                    )
                ),
            ]
            submission_request = request.override(
                messages=messages,
                tools=submission_tools,
                tool_choice=None,
                model_settings=settings,
            )
            response = await handler(submission_request)
            names = self._tool_names(response)
            if names and set(names) == {"submit_answer"}:
                return self._keep_first_submission(response)
            return await self._repair_submission(submission_request, response, handler)
        response = await handler(request)
        tool_names = self._tool_names(response)
        selected = self.selected_skill or "market-research-guide"
        skill_loaded = current_skill_loaded(request.messages, selected)
        duplicate_skill = skill_loaded and "read_file" in tool_names
        if tool_names and not duplicate_skill:
            return response
        if not skill_loaded:
            repaired = await handler(
                request.override(
                    messages=[
                        *request.messages,
                        HumanMessage(
                            content=(
                                "Before answering, load the selected skill. Call read_file exactly "
                                f"once with file_path=/skills/{selected}/SKILL.md and limit=1000. "
                                "Even an out-of-scope question requires the guide skill."
                            )
                        ),
                    ],
                    tools=[
                        item
                        for item in request.tools
                        if getattr(item, "name", None) == "read_file"
                    ],
                    tool_choice=None,
                    model_settings={**request.model_settings, "max_completion_tokens": 800},
                )
            )
            if valid_skill_call(repaired.result, selected):
                return repaired
            raise RuntimeError("the Deep Agent did not load its required skill")
        return await self._repair_submission(request, response, handler)

    async def _repair_submission(
        self,
        request: ModelRequest[Any],
        response: ModelResponse[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Give a plain-text finish one routed chance to submit its typed answer."""
        messages = list(request.messages)
        if not self._tool_names(response):
            messages.extend(response.result)
        messages.append(
            HumanMessage(
                content=(
                    "Your preceding response attempted to finish without the required typed "
                    "submission. Do not read another skill and do not reply in plain text. "
                    "Call submit_answer exactly once now, using its displayed JSON schema."
                )
            )
        )
        settings = {**request.model_settings, "max_completion_tokens": 800}
        repaired = await handler(
            request.override(
                messages=messages, tool_choice=None, model_settings=settings
            )
        )
        repaired_names = self._tool_names(repaired)
        if repaired_names == ["submit_answer"]:
            return repaired
        raise RuntimeError("the Deep Agent did not produce its typed final submission")


def _attempt_progress(attempt: ModelAttempt) -> dict[str, Any]:
    return {
        "configured_model": attempt.configured_model,
        "model_assertion": attempt.model_assertion,
        "identity_evidence": attempt.identity_evidence,
        "selected_tier": attempt.selected_tier,
        "tokens": attempt.tokens,
        "transport_ms": attempt.latency_ms,
        "attempt": attempt,
        "switchyard_trials": (),
    }


def _relay_outside_switchyard(middleware: list[Any]) -> list[Any]:
    """Keep Relay immediately outside its narrow Switchyard compatibility shim."""
    ordered = list(middleware)
    relay = next(
        item for item in ordered if isinstance(item, NemoRelayDeepAgentsMiddleware)
    )
    compatibility = next(
        item for item in ordered if isinstance(item, RelayHeaderCompatibilityMiddleware)
    )
    submission = next(
        item for item in ordered if isinstance(item, RequireAnswerSubmissionMiddleware)
    )
    ordered.remove(relay)
    ordered.remove(compatibility)
    ordered.remove(submission)
    switchyard_index = next(
        index
        for index, item in enumerate(ordered)
        if isinstance(item, SwitchyardRoutingMiddleware)
    )
    ordered[switchyard_index:switchyard_index] = [submission, relay, compatibility]
    return ordered
