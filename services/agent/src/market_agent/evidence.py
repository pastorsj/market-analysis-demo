"""Bounded execution and exact-scope reuse for validated MCP evidence."""

import asyncio, hashlib, json, math, re
from datetime import datetime; from typing import Any, Literal, Mapping
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import TOOLS
from .coverage import CoverageCatalog
from .planning import EvidencePlan, PlannedCall, ToolName
from .schemas import TOOL_DISPLAY_NAMES
from .security import SecurityRecorder

_HEX = re.compile(r"^[a-f0-9]{64}$")
_CIT = re.compile(r"^cit-[a-f0-9]{12,64}$")
_ROOT = {"schema_version", "tool", "as_of", "outcome", "coverage", "limitations", "evidence", "citations", "receipt", "data", "artifacts", "warnings"}
_COVERAGE = {"dimension", "key", "status", "required", "observed_count", "expected_count"}
_LIMIT = {"code", "message", "affected"}
_EVIDENCE = {"evidence_id", "observed_at", "available_at", "source_id", "values"}
_CITATION = {"citation_id", "evidence_id", "title", "url", "source_type", "published_at", "available_at", "excerpt", "content_sha256", "hindsight"}
_RECEIPT = {"engine", "device", "gpu_executed", "fallback_used", "duration_ms", "artifact_manifest_sha256", "scenario_id", "market_manifest_sha256", "document_manifest_sha256", "market_readiness_sha256", "document_readiness_sha256"}
_ARTIFACT = {"artifact_id", "kind", "title", "data"}
_DIMENSIONS = {"instrument", "market_window", "documents", "analogue_candidates", "graph_paths", "risk_model", "projection_documents"}
_SOURCES = {"market", "news", "filing", "release", "relationship", "model"}
_ENGINES = {"cudf", "cuvs", "cugraph", "xgboost-gpu", "cuml", "deterministic"}


class EvidenceValidationError(ValueError):
    """Stable boundary error; upstream exception details are never persisted."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceIdentity(_Strict):
    scenario_id: str; primary_ticker: str; comparison_ticker: str | None = None
    scenario_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    market_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    market_readiness_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_readiness_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_cutoff: str; effective_session: str; tool: ToolName; arguments: dict[str, str | int]

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EvidenceLimitation(_Strict):
    code: Literal["required_tool_failed", "optional_tool_failed"]; tool: ToolName
    call_sha256: str = Field(pattern=r"^[a-f0-9]{64}$"); message: str = Field(min_length=1, max_length=160)


class EvidenceRecord(_Strict):
    cache_key: str = Field(pattern=r"^[a-f0-9]{64}$"); identity: EvidenceIdentity; required: bool
    status: Literal["tool_completed", "tool_failed"]
    transitions: tuple[Literal["tool_started", "tool_completed", "tool_failed"], ...]
    result: dict[str, Any] | None = None; failure_code: Literal["tool_execution_failed", "invalid_tool_result"] | None = None; reused: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.cache_key != self.identity.digest(): raise ValueError("cache identity digest mismatch")
        completed = self.status == "tool_completed"
        if completed != (self.result is not None) or completed == (self.failure_code is not None): raise ValueError("record outcome mismatch")
        expected = ("tool_completed",) if self.reused else ("tool_started", "tool_completed" if completed else "tool_failed")
        if self.transitions != expected or self.reused and not completed: raise ValueError("record transitions mismatch")
        return self


class EvidenceRun(_Strict):
    schema_version: Literal["1.0"] = "1.0"; plan_id: str
    status: Literal["completed", "partial", "required_failed", "needs_scope_resolution"]
    records: tuple[EvidenceRecord, ...] = (); limitations: tuple[EvidenceLimitation, ...] = ()

    @model_validator(mode="after")
    def consistent(self):
        if len({row.cache_key for row in self.records}) != len(self.records): raise ValueError("duplicate evidence record")
        if self.status == "required_failed" and not any(row.required and row.status == "tool_failed" for row in self.records): raise ValueError("required failure missing")
        if self.status == "needs_scope_resolution" and (self.records or self.limitations): raise ValueError("scope request executed evidence")
        return self


def _exact(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys: raise EvidenceValidationError(code)
    return value


def _utc(value: Any, code: str):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError as exc: raise EvidenceValidationError(code) from exc
    if parsed is None or parsed.tzinfo is None: raise EvidenceValidationError(code)
    return parsed


def _bounded_list(value: Any, maximum: int, code: str) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum: raise EvidenceValidationError(code)
    return value


def _safe_citation_url(value: Any) -> bool:
    if not isinstance(value, str): return False
    parsed = urlsplit(value)
    try: query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError: return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.fragment and all(name == "datasetVersionNumber" and re.fullmatch(r"\d{1,6}", item) for name, item in query)


def _validate_result(value: Any, call: PlannedCall, plan: EvidencePlan, catalog: CoverageCatalog) -> dict[str, Any]:
    row = _exact(value, _ROOT, "result_shape"); cutoff = _utc(call.arguments["as_of"], "planned_cutoff")
    if row["schema_version"] != "2.0" or row["tool"] != call.tool or row["as_of"] != call.arguments["as_of"] or row["outcome"] not in plan.allowed_outcomes: raise EvidenceValidationError("result_identity")
    coverage = _bounded_list(row["coverage"], 32, "coverage_shape"); limitations = _bounded_list(row["limitations"], 20, "limitation_shape")
    if not coverage: raise EvidenceValidationError("coverage_empty")
    for item in coverage:
        item = _exact(item, _COVERAGE, "coverage_shape"); observed, expected = item["observed_count"], item["expected_count"]
        if item["dimension"] not in _DIMENSIONS or not isinstance(item["key"], str) or not re.fullmatch(r"[A-Za-z0-9_.:/@+\-]{1,160}", item["key"]) or item["status"] not in {"available", "partial", "missing"} or type(item["required"]) is not bool or type(observed) is not int or not 0 <= observed <= 1_000_000 or expected is not None and (type(expected) is not int or not 1 <= expected <= 1_000_000) or item["status"] == "missing" and observed or item["status"] == "available" and not observed: raise EvidenceValidationError("coverage_value")
    for item in limitations:
        item = _exact(item, _LIMIT, "limitation_shape"); affected = item["affected"]
        if not isinstance(item["code"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item["code"]) or not isinstance(item["message"], str) or not 1 <= len(item["message"]) <= 500 or not isinstance(affected, list) or len(affected) > 20 or any(not isinstance(x, str) or not 1 <= len(x) <= 160 for x in affected): raise EvidenceValidationError("limitation_value")
    unavailable = [item for item in coverage if item["status"] != "available"]
    if row["outcome"] == "ok" and (unavailable or limitations) or row["outcome"] == "partial" and (not unavailable or not limitations or not any(item["status"] != "missing" for item in coverage)) or row["outcome"] == "no_data" and (not limitations or not any(item["required"] and item["status"] == "missing" for item in coverage)): raise EvidenceValidationError("outcome_contract")
    evidence = _bounded_list(row["evidence"], 100, "evidence_shape"); citations = _bounded_list(row["citations"], 100, "citation_shape")
    for item in evidence:
        item = _exact(item, _EVIDENCE, "evidence_shape")
        if not all(isinstance(item[key], str) and item[key] for key in ("evidence_id", "source_id")) or not isinstance(item["values"], dict) or _utc(item["available_at"], "evidence_time") > cutoff or _utc(item["observed_at"], "evidence_time") > cutoff: raise EvidenceValidationError("evidence_value")
    for item in citations:
        item = _exact(item, _CITATION, "citation_shape")
        if not isinstance(item["citation_id"], str) or not _CIT.fullmatch(item["citation_id"]) or not isinstance(item["evidence_id"], str) or not all(isinstance(item[key], str) and 1 <= len(item[key]) <= limit for key, limit in (("title", 500), ("excerpt", 1200))) or not _safe_citation_url(item["url"]) or item["source_type"] not in _SOURCES or _utc(item["available_at"], "citation_time") > cutoff or _utc(item["published_at"], "citation_time") > cutoff or not isinstance(item["content_sha256"], str) or not _HEX.fullmatch(item["content_sha256"]) or item["hindsight"] is not False: raise EvidenceValidationError("citation_value")
    evidence_ids, cited_ids, citation_ids = [x["evidence_id"] for x in evidence], [x["evidence_id"] for x in citations], [x["citation_id"] for x in citations]
    if len(evidence_ids) != len(set(evidence_ids)) or len(cited_ids) != len(set(cited_ids)) or len(citation_ids) != len(set(citation_ids)) or set(evidence_ids) != set(cited_ids): raise EvidenceValidationError("citation_identity")
    receipt = _exact(row["receipt"], _RECEIPT, "receipt_shape"); expected = {"artifact_manifest_sha256": catalog.scenario_manifest_sha256, "scenario_id": catalog.scenario_id, "market_manifest_sha256": catalog.market_manifest_sha256, "document_manifest_sha256": catalog.document_manifest_sha256, "market_readiness_sha256": catalog.market_readiness_sha256, "document_readiness_sha256": catalog.document_readiness_sha256}
    duration = receipt["duration_ms"]
    if any(receipt[key] != expected[key] for key in expected) or receipt["engine"] not in _ENGINES or not isinstance(receipt["device"], str) or not receipt["device"] or type(receipt["gpu_executed"]) is not bool or receipt["gpu_executed"] != (receipt["engine"] != "deterministic") or receipt["fallback_used"] is not False or type(duration) not in {int, float} or not math.isfinite(duration) or duration < 0: raise EvidenceValidationError("receipt_value")
    artifacts = _bounded_list(row["artifacts"], 10, "artifact_shape"); warnings = _bounded_list(row["warnings"], 20, "warning_shape")
    for item in artifacts:
        item = _exact(item, _ARTIFACT, "artifact_shape")
        if not isinstance(item["artifact_id"], str) or not item["artifact_id"] or item["kind"] not in {"price_series", "topic_projection", "propagation_graph", "report"} or not isinstance(item["title"], str) or not 1 <= len(item["title"]) <= 500 or not isinstance(item["data"], dict): raise EvidenceValidationError("artifact_value")
    if row["outcome"] == "no_data" and artifacts or not isinstance(row["data"], dict) or not isinstance(row["data"].get("summary"), str) or not row["data"]["summary"] or any(not isinstance(item, str) for item in warnings): raise EvidenceValidationError("result_value")
    try: return json.loads(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False))
    except (TypeError, ValueError) as exc: raise EvidenceValidationError("result_not_json") from exc


class EvidenceExecutor:
    def __init__(self, tools: Mapping[str, Any], catalog: CoverageCatalog):
        if set(tools) != set(TOOLS) or any(getattr(value, "name", name) != name for name, value in tools.items()): raise EvidenceValidationError("executor requires the exact seven-tool map")
        if not isinstance(catalog, CoverageCatalog): raise EvidenceValidationError("executor requires verified coverage")
        self.tools, self.catalog = dict(tools), catalog

    def _identity(self, plan: EvidencePlan, call: PlannedCall) -> EvidenceIdentity:
        cutoff = _utc(call.arguments["as_of"], "planned_cutoff"); session = self.catalog.resolve_completed_session(cutoff)
        known = {*self.catalog.targets, *self.catalog.peers, *self.catalog.required_benchmarks, *self.catalog.optional_instruments}
        if call.arguments["ticker"] not in known or self.catalog.verification != "explicit_test" and not self.catalog.fields_for(str(call.arguments["ticker"])) or session is None or cutoff > self.catalog.sessions[-1].close_at: raise EvidenceValidationError("call scope is outside verified coverage")
        primary = plan.resolved_members[0] if plan.resolved_members else str((plan.required_calls + plan.optional_calls)[0].arguments["ticker"])
        ticker = str(call.arguments["ticker"])
        return EvidenceIdentity(scenario_id=self.catalog.scenario_id, scenario_manifest_sha256=self.catalog.scenario_manifest_sha256, market_manifest_sha256=self.catalog.market_manifest_sha256, document_manifest_sha256=self.catalog.document_manifest_sha256, market_readiness_sha256=self.catalog.market_readiness_sha256, document_readiness_sha256=self.catalog.document_readiness_sha256, primary_ticker=primary, comparison_ticker=None if ticker == primary else ticker, effective_cutoff=call.arguments["as_of"], effective_session=session.session_date.isoformat(), tool=call.tool, arguments=call.arguments)

    async def execute(self, plan: EvidencePlan, *, prior: EvidenceRun | Mapping[str, Any] | None = None, recorder: SecurityRecorder | None = None, progress=None) -> EvidenceRun:
        try: plan = EvidencePlan.model_validate_json(plan.model_dump_json())
        except Exception as exc:
            if recorder: recorder.violation("mcp", "unapproved_tool")
            raise EvidenceValidationError("unapproved tool or arguments") from exc
        if plan.status != "ready": return EvidenceRun(plan_id=plan.plan_id, status="needs_scope_resolution")
        try: old = EvidenceRun.model_validate(prior.model_dump(mode="json") if isinstance(prior, EvidenceRun) else prior) if prior else None
        except Exception: old = None
        cached = {} if old is None else {row.cache_key: row for row in old.records if row.status == "tool_completed" and row.result is not None}
        async def invoke(call: PlannedCall, required: bool, identity: EvidenceIdentity) -> EvidenceRecord:
            key = identity.digest(); span_key = f"{key}:{plan.plan_id}"; previous = cached.get(key)
            if previous is not None:
                try: result = _validate_result(previous.result, call, plan, self.catalog)
                except Exception:
                    if recorder: recorder.violation("persistence", "correlation_mismatch")
                else:
                    if progress:
                        await progress(key=span_key, kind="tool", display_name=TOOL_DISPLAY_NAMES[call.tool], state="started", tool=call.tool, call_sha256=key, reused=True, plan_id=plan.plan_id)
                        await progress(key=span_key, kind="tool", display_name=TOOL_DISPLAY_NAMES[call.tool], state="reused", **_progress_result(result))
                    return EvidenceRecord(cache_key=key, identity=identity, required=required, status="tool_completed", transitions=("tool_completed",), result=result, reused=True)
            if progress: await progress(key=span_key, kind="tool", display_name=TOOL_DISPLAY_NAMES[call.tool], state="started", tool=call.tool, call_sha256=key, reused=False, plan_id=plan.plan_id)
            observation = recorder.expect_network("mcp", "internal_tools") if recorder else None
            failure_code: Literal["tool_execution_failed", "invalid_tool_result"] = "tool_execution_failed"
            try:
                result = await self.tools[call.tool].ainvoke(call.arguments)
                failure_code = "invalid_tool_result"
                accepted = _validate_result(result, call, plan, self.catalog)
            except asyncio.CancelledError:
                if recorder: recorder.finish_network(observation, attempt="attempted", outcome="failed", call_id=key)
                if progress: await progress(key=span_key, kind="tool", display_name=TOOL_DISPLAY_NAMES[call.tool], state="cancelled", outcome="failed")
                raise
            except Exception:
                if recorder: recorder.finish_network(observation, attempt="attempted", outcome="failed", call_id=key)
                if recorder and failure_code == "invalid_tool_result": recorder.violation("mcp", "correlation_mismatch", observation_id=observation, call_id=key)
                if progress: await progress(key=span_key, kind="tool", display_name=TOOL_DISPLAY_NAMES[call.tool], state="failed", outcome="failed")
                return EvidenceRecord(cache_key=key, identity=identity, required=required, status="tool_failed", transitions=("tool_started", "tool_failed"), failure_code=failure_code)
            if recorder: recorder.finish_network(observation, attempt="attempted", outcome="succeeded", call_id=key)
            if progress: await progress(key=span_key, kind="tool", display_name=TOOL_DISPLAY_NAMES[call.tool], state="completed", **_progress_result(accepted))
            return EvidenceRecord(cache_key=key, identity=identity, required=required, status="tool_completed", transitions=("tool_started", "tool_completed"), result=accepted)
        pairs = tuple((item, True) for item in plan.required_calls) + tuple((item, False) for item in plan.optional_calls)
        try: identities = tuple(self._identity(plan, item) for item, _ in pairs)
        except EvidenceValidationError:
            if recorder: recorder.violation("mcp", "unapproved_tool")
            raise
        records = tuple(await asyncio.gather(*(invoke(item, required, identity) for (item, required), identity in zip(pairs, identities, strict=True))))
        failures = [row for row in records if row.status == "tool_failed"]
        limitations = tuple(EvidenceLimitation(code="required_tool_failed" if row.required else "optional_tool_failed", tool=row.identity.tool, call_sha256=row.cache_key, message="Required evidence execution failed." if row.required else "Optional evidence enrichment failed.") for row in failures)
        required_failed = any(row.required for row in failures); incomplete = any(row.result and row.result["outcome"] != "ok" for row in records)
        status = "required_failed" if required_failed else "partial" if failures or incomplete else "completed"
        return EvidenceRun(plan_id=plan.plan_id, status=status, records=records, limitations=limitations)


def _progress_result(result: dict[str, Any]) -> dict[str, Any]:
    receipt, citations = result["receipt"], result["citations"]
    preview = tuple({"evidence_id": item["evidence_id"], "title": item["title"], "source_type": item["source_type"]} for item in citations[:3])
    digest = hashlib.sha256(json.dumps({"tool": result["tool"], **receipt}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"outcome": result["outcome"], "engine": receipt["engine"], "device": receipt["device"], "compute_ms": receipt["duration_ms"], "receipt_sha256": digest, "evidence_count": len(result["evidence"]), "citation_count": len(citations), "evidence_preview": preview}
