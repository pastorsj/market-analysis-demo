"""Fact-preserving capable-model presentation plans compiled to bounded Markdown."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import uuid4

import nemo_relay
from langchain_core.messages import AIMessage
from openai import LengthFinishReasonError

from .config import CAPABLE_MODEL
# Keep the public presentation API stable while isolating pure layout functions.
from .presentation_formatting import (
    PRESENTED_ANSWER_MAX as PRESENTED_ANSWER_MAX,
    PRESENTATION_LONG_ANSWER_CHARS as PRESENTATION_LONG_ANSWER_CHARS,
    PRESENTATION_STRUCTURED_ANSWER_CHARS as PRESENTATION_STRUCTURED_ANSWER_CHARS,
    PRESENTATION_STRUCTURED_BLOCKS as PRESENTATION_STRUCTURED_BLOCKS,
    Heading as Heading,
    Layout as Layout,
    PresentationStyle as PresentationStyle,
    PresentationPlan as PresentationPlan,
    SourceBlock as SourceBlock,
    PresentationEligibility as PresentationEligibility,
    presentation_eligibility as presentation_eligibility,
    source_blocks as source_blocks,
    compile_markdown as compile_markdown,
    presentation_packet as presentation_packet,
    presentation_plan_schema as presentation_plan_schema,
)
from .schemas import ModelAttempt, TokenCounts
from .security import SecurityRecorder


@dataclass(frozen=True, slots=True)
class PresentedAnswer:
    markdown: str
    attempt: ModelAttempt


@dataclass(frozen=True, slots=True)
class AdaptivePresentation:
    markdown: str
    eligibility: PresentationEligibility
    attempt: ModelAttempt | None = None
    limitation: str | None = None


class PresentationFailure(RuntimeError):
    """Typed failure retaining the auditable model attempt when one began."""

    def __init__(self, code: str, attempt: ModelAttempt):
        super().__init__(code)
        self.code = code
        self.attempt = attempt


def _usage(message: AIMessage) -> TokenCounts:
    usage = message.usage_metadata or {}
    prompt = int(usage.get("input_tokens", 0) or 0)
    completion = int(usage.get("output_tokens", 0) or 0)
    return TokenCounts(
        prompt=prompt,
        completion=completion,
        total=max(prompt + completion, int(usage.get("total_tokens", 0) or 0)),
    )


class ReportPresenter:
    """Run one traced, provider-verified capable-model call and compile its structural plan."""

    def __init__(self, model: Any, skill_prompt: str):
        self.model = model
        self.skill_prompt = skill_prompt

    async def present(
        self,
        answer: str,
        *,
        question: str,
        recorder: SecurityRecorder,
        progress: Callable[..., Awaitable[None]],
    ) -> PresentedAnswer:
        call_id = f"call-{uuid4().hex}"
        request_id = f"request-{uuid4().hex}"
        key = f"presentation-{call_id}"
        observation = recorder.expect_network("frontier_model", "internal_inference")
        started = perf_counter()
        await progress(
            key=key,
            kind="model",
            display_name="Answer formatting",
            state="started",
            route_mode="frontier_only",
            configured_model=CAPABLE_MODEL,
        )
        raw: AIMessage | None = None
        provider_started = False
        identity_verified = False
        provider_error: Exception | None = None
        try:
            blocks = source_blocks(answer)
            schema = presentation_plan_schema(len(blocks))
            structured = self.model.with_structured_output(
                schema,
                method="json_schema",
                include_raw=True,
                strict=True,
            )
            messages = [
                {"role": "system", "content": self.skill_prompt},
                {"role": "user", "content": presentation_packet(question, blocks)},
            ]
            relay_request = nemo_relay.LLMRequest({}, {"model": CAPABLE_MODEL, "messages": messages})

            async def invoke(request: Any) -> dict[str, Any]:
                nonlocal raw, provider_started, identity_verified, provider_error
                provider_started = True
                try:
                    result = await structured.ainvoke(request.content["messages"])
                    raw = result.get("raw") if isinstance(result, Mapping) else None
                    parsed = result.get("parsed") if isinstance(result, Mapping) else None
                    error = result.get("parsing_error") if isinstance(result, Mapping) else None
                    if not isinstance(raw, AIMessage):
                        raise ValueError("formatter response is missing its provider envelope")
                    asserted = raw.response_metadata.get("model_name")
                    if asserted != CAPABLE_MODEL:
                        raise RuntimeError("identity_mismatch")
                    identity_verified = True
                    if error is not None or not isinstance(parsed, schema):
                        raise ValueError("formatter returned an invalid presentation plan")
                    return {
                        "model": asserted,
                        "plan": parsed.model_dump(mode="json"),
                        "usage": _usage(raw).model_dump(mode="json"),
                    }
                except Exception as exc:
                    provider_error = exc
                    raise

            with nemo_relay.scope.scope(
                "market-report-presentation",
                nemo_relay.ScopeType.Agent,
                data={"model": CAPABLE_MODEL, "purpose": "answer_formatting"},
            ):
                result = await nemo_relay.llm.execute(
                    "internal-inference",
                    relay_request,
                    invoke,
                    model_name=CAPABLE_MODEL,
                    data={"purpose": "answer_formatting"},
                )
            plan = schema.model_validate(result["plan"])
            markdown = compile_markdown(blocks, plan)
        except asyncio.CancelledError:
            recorder.finish_network(
                observation,
                attempt="attempted",
                outcome="failed",
                call_id=call_id,
                application_request_id=request_id,
            )
            raise
        except Exception as exc:
            latency = max(0.0, (perf_counter() - started) * 1000)
            source_error = provider_error or exc
            truncated = isinstance(source_error, LengthFinishReasonError)
            if truncated:
                completion = source_error.completion
                usage = completion.usage
                raw = AIMessage(content="", usage_metadata={
                    "input_tokens": usage.prompt_tokens if usage else 0,
                    "output_tokens": usage.completion_tokens if usage else 0,
                    "total_tokens": usage.total_tokens if usage else 0,
                })
                identity_verified = completion.model == CAPABLE_MODEL
            invalid = isinstance(source_error, ValueError) and str(source_error) != "identity_mismatch"
            code = (
                "identity_mismatch"
                if str(source_error) == "identity_mismatch" or (truncated and not identity_verified)
                else "finish_length"
                if truncated
                else "invalid_schema"
                if invalid
                else "timeout"
                if "timeout" in type(exc).__name__.lower()
                else "transport_error"
            )
            attempt = ModelAttempt(
                role="report_formatting",
                algorithm="direct_frontier",
                destination_class="internal_inference",
                configured_model=CAPABLE_MODEL,
                model_assertion=CAPABLE_MODEL if identity_verified else None,
                identity_evidence="direct_provider_verified" if identity_verified else "unavailable",
                state="failed",
                failure_class=code,
                application_call_id=call_id,
                application_request_id=request_id,
                latency_ms=latency,
                tokens=_usage(raw) if isinstance(raw, AIMessage) else TokenCounts(),
                validation_status="invalid",
            )
            if provider_started:
                recorder.finish_network(
                    observation,
                    attempt="attempted",
                    # A schema/plan failure occurs after the provider returned a
                    # verified provider envelope. The network call succeeded even
                    # though the cosmetic application step is invalid.
                    outcome="succeeded" if identity_verified and code in {"invalid_schema", "finish_length"} else "failed",
                    call_id=call_id,
                    application_request_id=request_id,
                    identity_evidence="direct_provider_verified" if identity_verified else None,
                )
            else:
                recorder.finish_network(
                    observation,
                    attempt="not_attempted",
                    outcome="blocked",
                    limitation="The report presentation input failed validation before provider contact.",
                )
            await progress(
                key=key,
                kind="model",
                display_name="Answer formatting",
                state="failed",
                route_mode="frontier_only",
                configured_model=attempt.configured_model,
                model_assertion=attempt.model_assertion,
                identity_evidence=attempt.identity_evidence,
                tokens=attempt.tokens,
                transport_ms=attempt.latency_ms,
                attempt=attempt,
                switchyard_trials=(),
            )
            raise PresentationFailure(code, attempt) from exc

        assert raw is not None
        attempt = ModelAttempt(
            role="report_formatting",
            algorithm="direct_frontier",
            destination_class="internal_inference",
            configured_model=CAPABLE_MODEL,
            model_assertion=CAPABLE_MODEL,
            identity_evidence="direct_provider_verified",
            state="succeeded",
            application_call_id=call_id,
            application_request_id=request_id,
            latency_ms=max(0.0, (perf_counter() - started) * 1000),
            tokens=_usage(raw),
            validation_status="valid",
        )
        recorder.finish_network(
            observation,
            attempt="attempted",
            outcome="succeeded",
            call_id=call_id,
            application_request_id=request_id,
            identity_evidence="direct_provider_verified",
        )
        await progress(
            key=key,
            kind="model",
            display_name="Answer formatting",
            state="completed",
            route_mode="frontier_only",
            configured_model=attempt.configured_model,
            model_assertion=attempt.model_assertion,
            identity_evidence=attempt.identity_evidence,
            tokens=attempt.tokens,
            transport_ms=attempt.latency_ms,
            attempt=attempt,
            switchyard_trials=(),
        )
        return PresentedAnswer(markdown=markdown, attempt=attempt)


async def present_adaptively(
    presenter: ReportPresenter | None,
    answer: str,
    *,
    question: str,
    recorder: SecurityRecorder,
    progress: Callable[..., Awaitable[None]],
) -> AdaptivePresentation:
    """Format only substantial answers and preserve the grounded prose on failure."""
    eligibility = presentation_eligibility(answer)
    if not eligibility.eligible:
        return AdaptivePresentation(markdown=answer, eligibility=eligibility)
    if presenter is None:
        return AdaptivePresentation(
            markdown=answer,
            eligibility=eligibility,
            limitation=(
                "Enhanced report formatting was unavailable; "
                "the original evidence-grounded answer is shown."
            ),
        )
    try:
        presented = await presenter.present(
            answer,
            question=question,
            recorder=recorder,
            progress=progress,
        )
    except PresentationFailure as exc:
        return AdaptivePresentation(
            markdown=answer,
            eligibility=eligibility,
            attempt=exc.attempt,
            limitation=(
                "Enhanced report formatting was unavailable; "
                "the original evidence-grounded answer is shown."
            ),
        )
    return AdaptivePresentation(
        markdown=presented.markdown,
        eligibility=eligibility,
        attempt=presented.attempt,
    )


__all__ = [
    "AdaptivePresentation",
    "PRESENTED_ANSWER_MAX",
    "PRESENTATION_LONG_ANSWER_CHARS",
    "PRESENTATION_STRUCTURED_ANSWER_CHARS",
    "PRESENTATION_STRUCTURED_BLOCKS",
    "PresentationEligibility",
    "PresentationFailure",
    "PresentationPlan",
    "PresentationStyle",
    "PresentedAnswer",
    "ReportPresenter",
    "compile_markdown",
    "present_adaptively",
    "presentation_eligibility",
    "presentation_packet",
    "presentation_plan_schema",
    "source_blocks",
]
