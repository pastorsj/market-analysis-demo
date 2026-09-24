from __future__ import annotations

import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from market_agent.agent import AgentError, Answer
from market_agent.config import CAPABLE_MODEL, JUDGE_MODEL, LOCAL_MODEL
from market_agent.context import ToolResult, TurnContext
from market_agent.report import build_report
from market_agent.routing import EscalationAdapter, RouteError, VerifiedClient
from market_agent.runner import new_investigation
from market_agent.schemas import Scope
from market_agent.store import SecretLeak, Store

from .conftest import scripted, tool_result



def _request(text="hello"):
    return {"messages": [{"role": "user", "content": [{"type": "text", "text": text}]}]}


async def test_unreadable_judge_verdict_fails_closed():
    models = {
        LOCAL_MODEL: scripted(LOCAL_MODEL, lambda _m: AIMessage(content="local answer")),
        JUDGE_MODEL: scripted(JUDGE_MODEL, lambda _m: AIMessage(content="not json at all")),
        CAPABLE_MODEL: scripted(CAPABLE_MODEL, lambda _m: AIMessage(content="capable")),
    }
    with pytest.raises(RouteError) as error:
        await EscalationAdapter(models).run(_request())
    assert error.value.code == "judge_verdict_invalid"


async def test_escalation_verdict_selects_the_capable_model():
    models = {
        LOCAL_MODEL: scripted(LOCAL_MODEL, lambda _m: AIMessage(content="local answer")),
        JUDGE_MODEL: scripted(
            JUDGE_MODEL, lambda _m: AIMessage(content=json.dumps({"escalate": True, "reason": "x"}))
        ),
        CAPABLE_MODEL: scripted(CAPABLE_MODEL, lambda _m: AIMessage(content="capable answer")),
    }
    decisions, body = await EscalationAdapter(models).run(_request())
    assert decisions[-1]["selected_model"] == CAPABLE_MODEL


async def test_provider_must_report_the_requested_model():
    model = scripted(LOCAL_MODEL, lambda _m: AIMessage(content="hi"))
    model.respond = lambda _m: AIMessage(content="hi")
    client = VerifiedClient(model)
    original = model._agenerate

    async def wrong_identity(*args, **kwargs):
        result = await original(*args, **kwargs)
        result.generations[0].message.response_metadata = {"model_name": "someone-else"}
        return result

    object.__setattr__(model, "_agenerate", wrong_identity)
    with pytest.raises(RouteError) as error:
        await client.call({"model": LOCAL_MODEL, **_request()})
    assert error.value.code == "identity_mismatch"


def _context(*results):
    context = TurnContext("inv", 1, Scope(status="needs_input"), None, progress=None)
    for name, citation in results:
        context.ledger.results.append(
            ("NVDA", ToolResult.model_validate(tool_result(name, "NVDA", citation)))
        )
    return context


def test_report_fails_closed_when_evidence_is_ignored():
    with pytest.raises(AgentError) as error:
        build_report(Answer(answer="Trust me."), _context(("get_price_context", "cit-aaaaaaaaaaaaaaaa")))
    assert error.value.code == "uncited_answer"


def test_report_strips_citation_ids_from_prose_and_keeps_limitations():
    context = _context(("get_price_context", "cit-aaaaaaaaaaaaaaaa"))
    report = build_report(
        Answer(answer="NVDA fell (cit-aaaaaaaaaaaaaaaa).", citation_ids=["cit-aaaaaaaaaaaaaaaa"]), context
    )
    assert report.answer == "NVDA fell." and report.kind == "research"
    assert report.tools[0].receipt.engine == "cudf"


def test_guide_answers_need_no_citations():
    assert build_report(Answer(answer="Ask about NVDA."), _context()).kind == "guide"


def test_store_encrypts_records_and_refuses_secrets(tmp_path):
    store = Store(tmp_path / "db.sqlite3", b"k" * 32, secrets=("super-secret",))
    investigation = new_investigation(Scope(status="needs_input"))
    store.save(investigation)
    assert store.get(investigation.investigation_id) == investigation
    assert b"needs_input" not in (tmp_path / "db.sqlite3").read_bytes()
    investigation.scope.note = "leaking super-secret"
    with pytest.raises(SecretLeak):
        store.save(investigation)
    assert store.get(uuid4()) is None
