"""The Deep Agent: skills, seven evidence tools, Switchyard routing, and Relay tracing.

``create_deep_agent`` is used as designed: SkillsMiddleware lists every skill's
description and the model reads the one it needs, the evidence tools are bound to
the turn's scope through the runtime context, and the final answer is a typed
structured response (``ToolStrategy``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import nemo_relay
from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRequest,
    ToolCallLimitMiddleware,
    dynamic_prompt,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage, RemoveMessage
from nemo_relay.integrations.deepagents import NemoRelayDeepAgentsCallbackHandler, add_nemo_relay_integration
from pydantic import BaseModel, Field

from .catalog import Coverage
from .config import LOCAL_MODEL, Settings
from .context import TurnContext, current_turn
from .routing import chat_models, switchyard_middleware
from .tools import EVIDENCE_TOOLS

SYSTEM_PROMPT = (Path(__file__).parent / "prompts/system.md").read_text()

# One agent, no general-purpose subagent, and a read-only filesystem limited to skills.
# Summarization is off because it would call the local model outside Switchyard.
register_harness_profile(
    f"openai:{LOCAL_MODEL}",
    HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_tools=frozenset({"ls", "write_file", "edit_file", "delete", "execute", "glob", "grep"}),
        excluded_middleware=frozenset({"SummarizationMiddleware"}),
    ),
)


class Answer(BaseModel):
    """The final answer to the user's current question."""

    answer: str = Field(min_length=1, max_length=6000, description="The answer in plain Markdown.")
    citation_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="citation_id values, returned by tools in this turn, that support the answer.",
    )
    uncertainty: list[str] = Field(
        default_factory=list, max_length=6, description="Important caveats or missing evidence."
    )
    suggested_questions: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Only when the question could not be answered: concrete questions that can be.",
    )


class AgentError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def briefing(context: TurnContext, coverage: Coverage) -> str:
    """Facts about this turn's scope and the application, appended to the system prompt."""
    scope = context.scope
    companies = ", ".join(f"{item['name']} ({item['symbol']})" for item in coverage.companies())
    lines = [
        "## This investigation",
        f"- Companies in scope: {', '.join(scope.members) or 'none yet'} (primary: {scope.ticker or 'none'})",
        f"- Evidence cutoff: {scope.as_of.isoformat() if scope.as_of else 'not set'}",
        f"- Market session analysed: {scope.session.isoformat() if scope.session else 'not set'}",
    ]
    if scope.missing:
        lines.append(f"- Missing before tools can run: {', '.join(scope.missing)}")
    if scope.note:
        lines.append(f"- Note: {scope.note}")
    lines += [
        "## About this application",
        f"- Supported companies: {companies}",
        f"- Daily market data: {coverage.first_session} to {coverage.last_session} (later reconstruction).",
        "- Documents: SEC filing metadata, a few company releases, and curated one-sentence news summaries.",
        "- Models: NVIDIA Nemotron 3.5 Lightning runs locally; NeMo Switchyard asks a remote judge model "
        "whether each step should be redone by the remote Nemotron 3 Ultra model. Remote calls send the "
        "conversation and tool evidence to that endpoint.",
        "- Tools run on the local GPU (cuDF, cuVS, cuGraph, cuML, XGBoost). NeMo Relay records traces.",
    ]
    return "\n".join(lines)


class _SkillLoads(AsyncCallbackHandler):
    """Report skill reads as progress spans (the evidence tools report themselves)."""

    def __init__(self, context: TurnContext):
        self.context, self.open = context, {}

    async def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):
        if (serialized or {}).get("name") == "read_file":
            path = (inputs or {}).get("file_path", "")
            self.open[run_id] = f"skill-{run_id.hex[:12]}"
            await self.context.progress(
                span=self.open[run_id],
                kind="skill",
                name="Skill load",
                state="running",
                detail={"path": path},
            )

    async def on_tool_end(self, output, *, run_id, **kwargs):
        if span := self.open.pop(run_id, None):
            await self.context.progress(span=span, kind="skill", name="Skill load", state="succeeded")

    async def on_tool_error(self, error, *, run_id, **kwargs):
        if span := self.open.pop(run_id, None):
            await self.context.progress(span=span, kind="skill", name="Skill load", state="failed")


def _loaded_skill(messages: list[Any]) -> str | None:
    """The last skill the agent read during the current turn."""
    start = max((i for i, message in enumerate(messages) if isinstance(message, HumanMessage)), default=0)
    skill = None
    for message in messages[start:]:
        for call in getattr(message, "tool_calls", None) or ():
            path = str(call.get("args", {}).get("file_path", ""))
            if call.get("name") == "read_file" and path.startswith("/skills/") and path.endswith("/SKILL.md"):
                skill = path.split("/")[2]
    return skill


class MarketAgent:
    def __init__(self, settings: Settings, coverage: Coverage, checkpointer: Any, models: dict | None = None):
        self.coverage = coverage

        @dynamic_prompt
        def add_briefing(request: ModelRequest) -> str:
            return f"{request.system_prompt}\n\n{briefing(request.runtime.context, coverage)}"

        models = models or chat_models(settings)
        agent = add_nemo_relay_integration(
            model=models[LOCAL_MODEL],
            tools=EVIDENCE_TOOLS,
            system_prompt=SYSTEM_PROMPT,
            skills=["/skills/"],
            backend=FilesystemBackend(root_dir=settings.skills_root.parent, virtual_mode=True),
            permissions=[
                FilesystemPermission(operations=["read"], paths=["/skills/**"], mode="allow"),
                FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
            ],
            middleware=[
                add_briefing,
                switchyard_middleware(models),
                ModelCallLimitMiddleware(run_limit=14, exit_behavior="error"),
                ToolCallLimitMiddleware(run_limit=24),
            ],
            response_format=ToolStrategy(Answer),
            context_schema=TurnContext,
            subagents=[],
            checkpointer=checkpointer,
            name="market-investigator",
        )
        self.graph = create_deep_agent(**agent)

    @staticmethod
    def _config(context: TurnContext) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": f"investigation:{context.investigation_id}"},
            "callbacks": [NemoRelayDeepAgentsCallbackHandler(), _SkillLoads(context)],
            "recursion_limit": 80,
        }

    async def drop_last_turn(self, context: TurnContext) -> None:
        """Before a retry, remove the failed turn's messages so the next attempt starts clean."""
        config = self._config(context)
        state = await self.graph.aget_state(config)
        messages = state.values.get("messages", []) if state and state.values else []
        turn_id = f"turn-{context.turn}"
        start = next((i for i, message in enumerate(messages) if message.id == turn_id), None)
        if start is not None:
            await self.graph.aupdate_state(
                config, {"messages": [RemoveMessage(id=m.id) for m in messages[start:]]}
            )

    async def run_turn(self, context: TurnContext, question: str) -> tuple[Answer, str | None]:
        token = current_turn.set(context)
        try:
            with nemo_relay.scope.scope(
                "market-investigation",
                nemo_relay.ScopeType.Agent,
                data={"investigation_id": context.investigation_id, "turn": context.turn},
            ):
                state = await self.graph.ainvoke(
                    {"messages": [HumanMessage(content=question, id=f"turn-{context.turn}")]},
                    config=self._config(context),
                    context=context,
                )
        finally:
            current_turn.reset(token)
        answer = state.get("structured_response")
        if not isinstance(answer, Answer):
            raise AgentError("no_answer", "The agent finished without a structured answer.")
        return answer, _loaded_skill(state.get("messages", []))
