"""Invocation-scoped evidence collection and bounded agent tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any

from langchain_core.tools import BaseTool, tool

from .evidence import EvidenceExecutor, EvidenceLimitation, EvidenceRecord, EvidenceRun
from .planning import EvidencePlan, Intent, PlannedCall, Sufficiency
from .policy import PolicyDecision, PolicyKind
from .security import SecurityRecorder


def _plan(calls: tuple[PlannedCall, ...], members: tuple[str, ...]) -> EvidencePlan:
    names = {item.tool for item in calls}
    intents: list[Intent] = []
    for name, intent in (
        ("find_historical_analogues", Intent.ANALOGUE),
        ("trace_shock_propagation", Intent.PROPAGATION),
        ("predict_volatility_risk", Intent.RISK),
        ("project_news_topics", Intent.TOPIC),
        ("search_news", Intent.CATALYST),
        ("detect_market_shock", Intent.SHOCK),
        ("get_price_context", Intent.PRICE),
    ):
        if name in names:
            intents.append(intent)
    material = [item.model_dump(mode="json") for item in calls]
    plan_id = (
        "plan-"
        + hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
    )
    return EvidencePlan(
        plan_id=plan_id,
        intents=tuple(dict.fromkeys(intents)) or (Intent.PRICE,),
        required_calls=(),
        optional_calls=calls,
        sufficiency=Sufficiency.GROUNDED,
        allowed_outcomes=("ok", "partial", "no_data"),
        deterministic_answer_outcomes=("no_data",),
        explanation="Deep Agent selected bounded evidence tools.",
        resolved_members=members,
    )


def _compact_value(value: Any, *, depth: int = 0) -> Any:
    """Bound trusted tool context without altering the retained evidence record."""
    if depth >= 4:
        return "[nested data omitted]"
    if isinstance(value, str):
        return value if len(value) <= 800 else value[:797] + "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _compact_value(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
        }
    if isinstance(value, (list, tuple)):
        return [_compact_value(item, depth=depth + 1) for item in value[:12]]
    return str(value)[:800]


def _agent_evidence_view(result: Mapping[str, Any]) -> dict[str, Any]:
    """Give the model citation-ready facts while keeping artifacts out of context."""
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    presented_data = dict(data)
    if result.get("tool") == "find_historical_analogues":
        contract = data.get("feature_contract")
        ranked = (
            contract.get("ranked_features") if isinstance(contract, Mapping) else None
        )
        if isinstance(ranked, list):
            specs = {
                str(item.get("name")): item
                for item in ranked
                if isinstance(item, Mapping)
            }
            absolute = specs.get("absolute_return_pct")
            volume = specs.get("volume_ratio")
            if absolute and volume:
                presented_data["feature_contract_summary"] = (
                    "Squared Euclidean distance scales absolute-return magnitude by "
                    f"{float(absolute['scale_divisor']):g} percentage points and volume "
                    f"ratio by {float(volume['scale_divisor']):g}×; each scaled feature "
                    f"is capped at {float(absolute['scaled_cap']):g}. These divisors are "
                    "explicitly returned, not omitted."
                )
        direction = data.get("direction_summary")
        target = data.get("target_features")
        if isinstance(direction, Mapping) and isinstance(target, Mapping):
            analogues = data.get("analogues")
            candidate_directions = (
                {
                    str(item.get("features", {}).get("signed_direction"))
                    for item in analogues
                    if isinstance(item, Mapping)
                    and isinstance(item.get("features"), Mapping)
                    and item.get("features", {}).get("signed_direction")
                }
                if isinstance(analogues, list)
                else set()
            )
            candidates = (
                next(iter(candidate_directions))
                if len(candidate_directions) == 1
                else "mixed"
            )
            presented_data["direction_contract_summary"] = (
                f"Target direction is {target.get('signed_direction')}; candidate "
                f"direction is {candidates}; {direction.get('match_count')} of "
                f"{direction.get('candidate_count')} match and "
                f"{direction.get('mismatch_count')} of {direction.get('candidate_count')} "
                "differ. Treat these structured counts as authoritative."
            )
    evidence = {
        str(item.get("evidence_id")): item.get("values", {})
        for item in result.get("evidence", ())
        if isinstance(item, Mapping) and item.get("evidence_id")
    }
    citations = [
        item for item in result.get("citations", ()) if isinstance(item, Mapping)
    ]
    if result.get("tool") == "find_historical_analogues":
        target = data.get("target_features")
        target_id = target.get("evidence_id") if isinstance(target, Mapping) else None
        citations = [
            citation
            for _, citation in sorted(
                enumerate(citations),
                key=lambda item: (
                    (
                        0
                        if item[1].get("source_type") == "model"
                        else 1 if item[1].get("evidence_id") == target_id else 2
                    ),
                    item[0],
                ),
            )
        ]
    sources = []
    for citation in citations[:8]:
        sources.append(
            {
                "citation_id": citation.get("citation_id"),
                "title": citation.get("title"),
                "source_type": citation.get("source_type"),
                "published_at": citation.get("published_at"),
                "excerpt": _compact_value(citation.get("excerpt")),
                "values": _compact_value(
                    evidence.get(str(citation.get("evidence_id")), {})
                ),
            }
        )
    references = []
    for source, citation in zip(sources, citations[:8], strict=True):
        original = evidence.get(str(citation.get("evidence_id")), {})
        if isinstance(original, Mapping) and source["values"] == original and source["citation_id"]:
            references.append((original, source["citation_id"]))
        if isinstance(source["values"], dict):
            for key, displayed in (("text", source["excerpt"]), ("title", source["title"])):
                if key in original and original[key] == displayed:
                    source["values"].pop(key, None)
    if result.get("tool") == "search_news" and isinstance(data.get("matches"), list):
        presented_data["matches"] = [
            next(({"citation_id": citation_id} for original, citation_id in references
                  if match == original and _compact_value(match, depth=2) == match), match)
            for match in data["matches"]
        ]
    if "summary" in presented_data and presented_data["summary"] == data.get("summary"):
        presented_data.pop("summary")
    return {
        "outcome": result.get("outcome"),
        "tool": result.get("tool"),
        "as_of": result.get("as_of"),
        "summary": data.get("summary"),
        "data": _compact_value(presented_data),
        "sources": sources,
        "warnings": _compact_value(result.get("warnings", ())[:5]),
        "limitations": _compact_value(result.get("limitations", ())[:5]),
    }


@dataclass
class EvidenceCollector:
    executor: EvidenceExecutor
    decision: PolicyDecision
    recorder: SecurityRecorder
    progress: Any
    selected_skill: str
    required_tools: tuple[str, ...] = ()
    calls: list[PlannedCall] = field(default_factory=list)
    records: dict[str, EvidenceRecord] = field(default_factory=dict)
    limitations: list[EvidenceLimitation] = field(default_factory=list)
    inflight: dict[str, asyncio.Task[EvidenceRecord]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def members(self) -> tuple[str, ...]:
        scope = self.decision.scope
        return scope.resolved_tickers or ((scope.ticker,) if scope.ticker else ())

    async def call(
        self, name: str, ticker: str, **arguments: str | int
    ) -> dict[str, Any]:
        if (
            self.decision.kind != PolicyKind.SUPPORTED
            or self.decision.scope.as_of is None
        ):
            return {
                "outcome": "blocked",
                "message": "Evidence tools require a resolved supported ticker and cutoff.",
            }
        ticker = ticker.strip().upper()
        if ticker not in self.members:
            if self.selected_skill == "historical-analogues":
                return {
                    "outcome": "blocked",
                    "message": (
                        "That analogue candidate is outside the immutable investigation "
                        "scope. Do not retry it or try another candidate ticker. Candidate "
                        "dates and measured features are already in the historical-analogue "
                        "result; use that evidence and call submit_answer now."
                    ),
                }
            return {
                "outcome": "blocked",
                "message": (
                    "Ticker is outside the immutable investigation scope. "
                    f"Allowed members: {', '.join(self.members)}. "
                    "Do not retry this ticker or explore other out-of-scope tickers, "
                    "including through a different tool. This authorization denial is "
                    "not evidence that no market data exists. Use accepted in-scope "
                    "evidence; finish only any still-needed allowed-member evidence "
                    "calls, then call submit_answer and disclose the unavailable "
                    "comparison coverage. Do not invent results for excluded members."
                ),
            }
        cutoff = (
            self.decision.scope.as_of.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        if self.decision.request.event_id and name not in {"search_news", "project_news_topics"}:
            cutoff = self.decision.scope.market_as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            call = PlannedCall(
                tool=name, arguments={"ticker": ticker, "as_of": cutoff, **arguments}
            )
        except Exception:
            self.recorder.violation("mcp", "unapproved_tool")
            return {
                "outcome": "blocked",
                "message": "Tool arguments failed the bounded contract.",
            }
        key = hashlib.sha256(call.model_dump_json().encode()).hexdigest()
        async with self.lock:
            if key in self.records:
                cached = self.records[key].result
                return (
                    _agent_evidence_view(cached)
                    if cached
                    else {"outcome": "failed", "message": "Evidence execution failed."}
                )
            task = self.inflight.get(key)
            if task is None:
                if len(self.calls) >= 16:
                    return {
                        "outcome": "blocked",
                        "message": "The bounded evidence-call limit was reached.",
                    }
                self.calls.append(call)
                task = asyncio.create_task(self._execute(call))
                self.inflight[key] = task
        record = await task
        async with self.lock:
            self.records[key] = record
            self.inflight.pop(key, None)
        return (
            _agent_evidence_view(record.result)
            if record.result
            else {"outcome": "failed", "message": "Evidence execution failed."}
        )

    async def _execute(self, call: PlannedCall) -> EvidenceRecord:
        run = await self.executor.execute(
            _plan((call,), self.members), recorder=self.recorder, progress=self.progress
        )
        self.limitations.extend(run.limitations)
        return run.records[0]

    def finish(self) -> tuple[EvidencePlan | None, EvidenceRun | None]:
        if not self.calls:
            return None, None
        unique = {item.model_dump_json(): item for item in self.calls}
        calls = tuple(unique.values())
        plan = _plan(calls, self.members)
        ordered = tuple(
            self.records[hashlib.sha256(item.model_dump_json().encode()).hexdigest()]
            for item in calls
        )
        limitations = {item.model_dump_json(): item for item in self.limitations}
        incomplete = any(
            item.status == "tool_failed"
            or item.result
            and item.result["outcome"] != "ok"
            for item in ordered
        )
        return plan, EvidenceRun(
            plan_id=plan.plan_id,
            status="partial" if incomplete else "completed",
            records=ordered,
            limitations=tuple(limitations.values()),
        )


def _tools(collector: EvidenceCollector) -> list[BaseTool]:
    @tool
    async def get_price_context(ticker: str) -> dict[str, Any]:
        """Get cutoff-filtered OHLCV, return, and benchmark context for one in-scope ticker."""
        return await collector.call("get_price_context", ticker)

    @tool
    async def detect_market_shock(ticker: str) -> dict[str, Any]:
        """Measure whether the in-scope ticker move was an unusual market shock."""
        return await collector.call("detect_market_shock", ticker)

    @tool
    async def search_news(ticker: str, query: str, top_k: int = 5) -> dict[str, Any]:
        """Search cutoff-qualified stored news, filings, and releases for an in-scope ticker."""
        bounded_news = getattr(collector, "selected_skill", None) in {
            "market-dislocation", "peer-comparison", "shock-propagation",
        }
        normalized_query = (
            collector.decision.request.question
            if bounded_news
            else " ".join(query.split())[:500] or collector.decision.request.question
        )
        bounded_top_k = 5 if bounded_news else max(1, min(top_k, 10))
        return await collector.call(
            "search_news", ticker, query=normalized_query, top_k=bounded_top_k
        )

    @tool
    async def find_historical_analogues(ticker: str, top_k: int = 5) -> dict[str, Any]:
        """Find similar historical market episodes for an in-scope ticker."""
        return await collector.call(
            "find_historical_analogues", ticker, top_k=5
        )

    @tool
    async def trace_shock_propagation(
        ticker: str, max_depth: int = 2
    ) -> dict[str, Any]:
        """Trace bounded relationship paths through the validated market graph."""
        return await collector.call(
            "trace_shock_propagation", ticker, max_depth=2
        )

    @tool
    async def predict_volatility_risk(ticker: str) -> dict[str, Any]:
        """Estimate validated post-shock volatility risk for an in-scope ticker."""
        return await collector.call("predict_volatility_risk", ticker)

    @tool
    async def project_news_topics(
        ticker: str, dimensions: int = 2, max_documents: int = 24
    ) -> dict[str, Any]:
        """Project up to 24 cutoff-qualified stored documents into a bounded topic view."""
        bounded_documents = max(2, min(max_documents, 24))
        return await collector.call(
            "project_news_topics",
            ticker,
            dimensions=max(2, min(dimensions, 3)),
            max_documents=bounded_documents,
        )

    return [
        get_price_context,
        detect_market_shock,
        search_news,
        find_historical_analogues,
        trace_shock_propagation,
        predict_volatility_risk,
        project_news_topics,
    ]
