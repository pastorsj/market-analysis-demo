"""One Relay-instrumented, Switchyard-routed LangChain Deep Agent."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import nemo_relay
from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import RemoveMessage
from langchain_nvidia_switchyard import SwitchyardRoutingMiddleware
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from nemo_relay.integrations.deepagents import (
    NemoRelayDeepAgentsCallbackHandler,
    add_nemo_relay_integration,
)
from switchyard.libsy import EscalationClassifierConfig, LlmClassifierConfig, algorithms

from .config import LOCAL_MODEL, LUNA_MODEL, CAPABLE_MODEL
from .coverage import CoverageCatalog

# Retain the existing contract-test import surface while implementations live
# in focused modules. Production orchestration remains in MarketDeepAgent.
from .deep_analogue import (
    _analogue_claim_citation_ids,
    _correct_analogue_contract_prose,
    _correct_analogue_contract_uncertainty,
)
from .deep_answers import (
    AnswerSubmission,
    ResearchAnswer,
    _submission_tool,
)
from .deep_evidence import EvidenceCollector, _agent_evidence_view, _tools
from .deep_middleware import (
    AgentActivityCallback,
    RequireAnswerSubmissionMiddleware,
    RoutedCallObserver,
    _relay_outside_switchyard,
)
from .deep_reports import finalize_answer
from .deep_skills import MARKET_ESCALATION_PROMPT, ScopedSkillsBackend, _prompt, _required_tools, _select_skill
from .evidence import EvidenceExecutor
from .event_catalog import resolve_bound_policy
from .policy import resolve_policy
from .presentation import ReportPresenter
from .schemas import InvestigationRequest, InvestigationScope, TurnRequest
from .security import SecurityRecorder
from .skill_registry import SkillRegistry, load_skill_registry
from .switchyard_adapter import (
    EscalationAlgorithmAdapter,
    ProviderVerifiedLlmClient,
    RelayHeaderCompatibilityMiddleware,
)

SKILL_ID = "market-agent-skills/deep-agent-1.0.0"


class _TracedTerminalFailure(RuntimeError):
    """Carry an already-sanitized terminal result beyond the Relay scope."""

    def __init__(self, result: dict[str, Any]):
        super().__init__(str(result.get("failure_code") or "agent_failure"))
        self.result = result


# Deep Agents adds a general-purpose ``task`` subagent by default. This
# vertical slice is intentionally one agent: skills and bounded market tools
# provide specialization, while every model turn stays on Relay + Switchyard.
register_harness_profile(
    f"openai:{LOCAL_MODEL}",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
    ),
)


class MarketDeepAgent:
    """Build one scoped Deep Agent whose every model turn uses escalation routing."""

    def __init__(
        self,
        settings: Any,
        catalog: CoverageCatalog,
        executor: EvidenceExecutor,
        checkpointer: Any,
        event_catalog: Any = None,
    ):
        (
            self.settings,
            self.catalog,
            self.executor,
            self.checkpointer,
            self.event_catalog,
        ) = (settings, catalog, executor, checkpointer, event_catalog)
        self.skill_registry: SkillRegistry = load_skill_registry(
            Path(settings.skills_root)
        )
        common = {
            "max_retries": 0,
            "timeout": 120,
            "max_completion_tokens": 1200,
            "use_responses_api": False,
        }
        self.efficient = ChatOpenAI(
            model=LOCAL_MODEL,
            base_url=settings.model_url,
            api_key="local-model",
            temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            **common,
        )
        self.judge = self.capable = None
        self.routing_algorithm = None
        self.presenter = None
        if settings.remote_enabled:
            remote = {
                "base_url": settings.remote_url,
                "api_key": settings.remote_key.get_secret_value(),
                **common,
            }
            self.judge = ChatOpenAI(model=LUNA_MODEL, **remote)
            self.capable = ChatOpenAI(model=CAPABLE_MODEL, **{**remote, "max_completion_tokens": 4096})
            presentation_skill = self.skill_registry.render_prompt(
                ("report-presentation",)
            )
            # Layout is a bounded structural plan, not another research pass.
            # Keep Ultra's research reasoning enabled; disable it only here.
            layout_model = self.capable.model_copy(update={
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False, "force_nonempty_content": True}},
                "max_tokens": 512, "temperature": 0, "model_kwargs": {"seed": 42},
            })
            self.presenter = ReportPresenter(layout_model, presentation_skill)
            self.routing_algorithm = algorithms.llm_classifier(
                LlmClassifierConfig.escalation(
                    config=EscalationClassifierConfig(
                        confirmations=1,
                        recent_turn_window=28,
                        window_message_chars=4000,
                        prompt=MARKET_ESCALATION_PROMPT,
                    )
                )
            )

    async def invoke(
        self,
        request: InvestigationRequest | TurnRequest,
        *,
        investigation_id: str,
        turn_id: str,
        recorder: SecurityRecorder,
        progress: Any,
        fresh_checkpoint: bool = False,
        resolved_scope: InvestigationScope | None = None,
        prior_skill: str | None = None,
    ) -> dict[str, Any]:
        # Keep one Relay propagation root alive for the complete turn. In
        # particular, the capable-model presentation call must remain a child of
        # the same investigation trace as Deep Agent and Switchyard.
        try:
            with nemo_relay.scope.scope(
                "market-investigation",
                nemo_relay.ScopeType.Agent,
                data={"investigation_id": investigation_id, "turn_id": turn_id},
            ):
                result = await self._invoke_scoped(
                    request,
                    investigation_id=investigation_id,
                    turn_id=turn_id,
                    recorder=recorder,
                    progress=progress,
                    fresh_checkpoint=fresh_checkpoint,
                    resolved_scope=resolved_scope,
                    prior_skill=prior_skill,
                )
                if result.get("terminal") in {"route_failure", "synthesis_failure"}:
                    # `_invoke_scoped` translates expected provider/agent
                    # exceptions into the public typed result. Re-raise only
                    # across the trace boundary so Relay marks the root ERROR,
                    # then return the same result unchanged below.
                    raise _TracedTerminalFailure(result)
                return result
        except _TracedTerminalFailure as exc:
            return exc.result

    async def _invoke_scoped(
        self,
        request: InvestigationRequest | TurnRequest,
        *,
        investigation_id: str,
        turn_id: str,
        recorder: SecurityRecorder,
        progress: Any,
        fresh_checkpoint: bool = False,
        resolved_scope: InvestigationScope | None = None,
        prior_skill: str | None = None,
    ) -> dict[str, Any]:
        normalized = (
            request.model_copy(update={"route_mode": "switchyard_escalation"})
            if isinstance(request, InvestigationRequest)
            else InvestigationRequest(
                question=request.question, route_mode="switchyard_escalation"
            )
        )
        decision = resolve_bound_policy(
            normalized, self.catalog, self.event_catalog, recorder=recorder
        )
        if resolved_scope is not None and not normalized.event_id:
            decision = resolve_policy(normalized, self.catalog, _comparison_members=resolved_scope.resolved_tickers)
            # The API already resolved anaphoric follow-ups against stored scope;
            # parsing its primary-only request again must not discard the peers.
            known = set(
                self.catalog.targets
                + self.catalog.peers
                + self.catalog.required_benchmarks
                + self.catalog.optional_instruments
            )
            if (
                resolved_scope.ticker != decision.scope.ticker
                or resolved_scope.as_of != decision.scope.as_of
                or resolved_scope.status != decision.scope.status
                or not set(resolved_scope.resolved_tickers) <= known
                or not set(decision.scope.resolved_tickers)
                <= set(resolved_scope.resolved_tickers)
            ):
                raise ValueError(
                    "resolved scope does not match the current policy boundary"
                )
            decision = replace(decision, scope=resolved_scope)
        selected_skill = _select_skill(decision, prior_skill)
        base = {
            "request": decision.request.model_dump(mode="json"),
            "scope": decision.scope.model_dump(mode="json"),
            "selected_skill": selected_skill,
            "plan": None,
            "evidence": None,
            "model_attempts": [],
            "switchyard_trials": [],
            "report": None,
            "node_trace": ["resolve_policy", "deep_agent"],
        }
        if (
            not self.settings.remote_enabled
            or self.judge is None
            or self.capable is None
            or self.routing_algorithm is None
        ):
            return {
                **base,
                "terminal": "route_failure",
                "failure_code": "route_unavailable",
            }
        skill = self.skill_registry[selected_skill]
        collector = EvidenceCollector(
            self.executor, decision, recorder, progress, selected_skill,
            _required_tools(selected_skill, skill.required_tools, decision.request.question),
        )
        observer = RoutedCallObserver(recorder, progress)
        submission = AnswerSubmission()
        clients = {
            LOCAL_MODEL: ProviderVerifiedLlmClient(self.efficient),
            LUNA_MODEL: ProviderVerifiedLlmClient(self.judge),
            CAPABLE_MODEL: ProviderVerifiedLlmClient(self.capable),
        }
        adapter = EscalationAlgorithmAdapter(
            self.routing_algorithm,
            clients=clients,
            models={
                "judge": [LUNA_MODEL],
                "efficient": [LOCAL_MODEL],
                "capable": [CAPABLE_MODEL],
                "any": [LOCAL_MODEL, CAPABLE_MODEL, LUNA_MODEL],
            },
            session_id=investigation_id,
            observe=observer,
        )
        backend = ScopedSkillsBackend(
            root_dir=Path(self.settings.skills_root).parent,
            selected_skill=selected_skill,
        )
        permissions = [
            FilesystemPermission(
                operations=["read"],
                paths=[f"/skills/{selected_skill}/**"],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["read", "write"], paths=["/**"], mode="deny"
            ),
        ]
        skill_reader_description = f"Read the complete selected skill only from /skills/{selected_skill}/SKILL.md with limit=1000. Never read a directory, batch files, or repeat the skill read."

        def middleware():
            return [
                # Replace Deep Agents' default instance by name. Checkpointed
                # skill metadata is intentionally not rendered: this runtime
                # selects a fresh scoped skill for every conversation turn.
                SkillsMiddleware(
                    backend=backend, sources=["/skills/"], system_prompt=None,
                ),
                FilesystemMiddleware(
                    backend=backend,
                    tools=["read_file"],
                    custom_tool_descriptions={"read_file": skill_reader_description},
                    _permissions=permissions,
                ),
                RelayHeaderCompatibilityMiddleware(),
                RequireAnswerSubmissionMiddleware(selected_skill, collector),
                SwitchyardRoutingMiddleware(adapter),
                ModelCallLimitMiddleware(run_limit=12, exit_behavior="error"),
            ]

        evidence_tools_by_name = {item.name: item for item in _tools(collector)}
        evidence_tools = [evidence_tools_by_name[name] for name in skill.allowed_tools
                          if name != "search_news" or selected_skill not in {"market-dislocation", "peer-comparison"} or name in collector.required_tools]
        parent_tools = [*evidence_tools, _submission_tool(submission, decision)]
        agent_kwargs = add_nemo_relay_integration(
            model=self.efficient,
            tools=parent_tools,
            system_prompt=_prompt(
                decision, selected_skill, self.catalog,
                remote_enabled=self.settings.remote_enabled,
            ),
            middleware=middleware(),
            subagents=[],
            skills=["/skills/"],
            permissions=permissions,
            backend=backend,
            checkpointer=self.checkpointer,
            name="market-investigator",
        )
        agent_kwargs["middleware"] = _relay_outside_switchyard(
            agent_kwargs["middleware"]
        )
        agent = create_deep_agent(**agent_kwargs)
        thread_id = f"market-agent:{investigation_id}"
        config = {
            "callbacks": [
                NemoRelayDeepAgentsCallbackHandler(),
                AgentActivityCallback(progress),
            ],
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 64,
        }
        try:
            messages: list[object] = [
                {"role": "user", "content": decision.request.question}
            ]
            if fresh_checkpoint:
                messages.insert(0, RemoveMessage(id=REMOVE_ALL_MESSAGES))
            await agent.ainvoke({"messages": messages}, config=config)
        except asyncio.CancelledError:
            raise
        except (ModelCallLimitExceededError, GraphRecursionError):
            try:
                failed_plan, failed_run = collector.finish()
            except Exception:
                failed_plan, failed_run = None, None
            return {
                **base,
                "model_attempts": [
                    item.model_dump(mode="json") for item in observer.attempts
                ],
                "plan": failed_plan.model_dump(mode="json") if failed_plan else None,
                "evidence": failed_run.model_dump(mode="json") if failed_run else None,
                "terminal": "synthesis_failure",
                "failure_code": "agent_turn_limit",
            }
        except Exception:
            try:
                failed_plan, failed_run = collector.finish()
            except Exception:
                failed_plan, failed_run = None, None
            state = {
                **base,
                "model_attempts": [
                    item.model_dump(mode="json") for item in observer.attempts
                ],
                "plan": failed_plan.model_dump(mode="json") if failed_plan else None,
                "evidence": failed_run.model_dump(mode="json") if failed_run else None,
            }
            code = observer.failure or "agent_execution_error"
            return {
                **state,
                "terminal": (
                    "route_failure"
                    if code
                    in {
                        "route_unavailable",
                        "transport_error",
                        "timeout",
                        "provider_error",
                        "identity_mismatch",
                        "transport_contract",
                    }
                    else "synthesis_failure"
                ),
                "failure_code": code,
            }
        return await finalize_answer(
            base=base,
            collector=collector,
            observer=observer,
            submission=submission,
            decision=decision,
            presenter=self.presenter,
            recorder=recorder,
            progress=progress,
        )


__all__ = ["MarketDeepAgent", "SKILL_ID"]
