from datetime import date, datetime; import math; from typing import Annotated, Literal; from uuid import UUID
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator
from .config import CAPABLE_MODEL, LOCAL_MODEL, LUNA_MODEL, SOL_MODEL

MAX_INVESTIGATION_TURNS = 4


ShortText = Annotated[str, Field(min_length=1, max_length=500)]; CitationID = Annotated[str, Field(pattern=r"^cit-[a-f0-9]{12,64}$")]; TickerSymbol = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")]
ScopeStatus = Literal["supported", "partially_supported", "clarification", "refused"]; RouteMode = Literal["local_only", "switchyard_escalation", "frontier_only"]; AnswerMode = Literal["deterministic_policy", "deterministic_evidence", "model_synthesis"]; GroupKey = Literal["supported_universe", "ai_exposed_semiconductors", "financials"]; NoDataReason = Literal["missing_news", "missing_company_release", "non_trading_day", "non_listed_or_delisted_instrument"]
ResearchSkillName = Literal["market-dislocation", "peer-comparison", "historical-analogues", "shock-propagation", "volatility-risk", "narrative-map", "market-research-guide"]
CorrelationID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")]; SecretName = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")]; SecurityState = Literal["verified", "violation", "unknown"]; CAPABILITY_ROUTE = "market-agent-capability"; SWITCHYARD_ROUTE = "switchyard/escalation"


class StrictModel(BaseModel): model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
def _require(condition, message):
    if not condition: raise ValueError(message)


class InvestigationRequest(StrictModel):
    question: Annotated[str, Field(min_length=2, max_length=4000)]; ticker: Annotated[str | None, Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")] = None
    as_of: date | datetime | None = None; route_mode: RouteMode = "switchyard_escalation"; event_id: Annotated[str | None, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)] = None


class TurnRequest(StrictModel):
    question: Annotated[str, Field(min_length=2, max_length=4000)]; route_mode: RouteMode | None = None


class InvestigationScope(StrictModel):
    status: ScopeStatus; action: Literal["answer", "partial_answer", "clarify", "refuse"]; ticker: str | None = None; as_of: datetime | None = None; market_as_of: datetime | None = None; timezone: Literal["America/New_York"] = "America/New_York"; supported_universe: tuple[str, ...]; explanation: ShortText; resolved_tickers: Annotated[tuple[TickerSymbol, ...], Field(max_length=5)] = (); group_key: GroupKey | None = None

    @model_validator(mode="after")
    def explicit_members(self): _require(len(set(self.resolved_tickers)) == len(self.resolved_tickers), "resolved tickers must be unique"); _require(not self.resolved_tickers or not self.ticker or self.resolved_tickers[0] == self.ticker, "primary ticker must be first"); _require(not self.group_key or bool(self.resolved_tickers), "group scope requires resolved tickers"); _require(not self.market_as_of or self.market_as_of.tzinfo is not None and self.as_of is not None and self.market_as_of <= self.as_of, "market cutoff must be aware and no later than evidence cutoff"); return self


class Citation(StrictModel):
    citation_id: str = Field(pattern=r"^cit-[a-f0-9]{12,64}$"); evidence_id: str = Field(min_length=8, max_length=80); title: ShortText; url: AnyHttpUrl; source_type: Literal["market", "news", "filing", "release", "relationship", "model"]
    published_at: datetime; available_at: datetime; excerpt: Annotated[str, Field(min_length=1, max_length=1200)]; content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$"); hindsight: bool = False

    @field_validator("available_at")
    @classmethod
    def availability_is_aware(cls, value: datetime) -> datetime: _require(value.tzinfo is not None, "available_at must be timezone-aware"); return value


class Claim(StrictModel):
    claim_id: str = Field(pattern=r"^claim-[a-z0-9-]{1,40}$"); text: Annotated[str, Field(min_length=1, max_length=3200)]
    kind: Literal["fact", "calculation", "inference", "limitation"]; confidence: Annotated[float, Field(ge=0, le=1)]; citation_ids: Annotated[list[str], Field(max_length=12)] = []


class DraftClaim(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=3200)]; kind: Literal["fact", "calculation", "inference", "limitation"]; confidence: Annotated[float, Field(ge=0, le=1, description="Inference confidence must be below 1.")]; citation_ids: Annotated[tuple[CitationID, ...], Field(min_length=1, max_length=12, description="Unique IDs from the supporting evidence row.")]; tickers: Annotated[tuple[TickerSymbol, ...], Field(min_length=1, max_length=5, description="Allowed ticker symbols relevant to this claim, uniquely.")]

    @model_validator(mode="after")
    def bounded_references(self): _require(len(set(self.citation_ids)) == len(self.citation_ids) and len(set(self.tickers)) == len(self.tickers) and (self.kind != "inference" or self.confidence < 1), "draft references or confidence are invalid"); return self


class AnswerDraft(StrictModel):
    title: ShortText; summary: Annotated[str, Field(min_length=1, max_length=5000)]
    claims: Annotated[tuple[DraftClaim, ...], Field(min_length=1, max_length=30)]; uncertainty: Annotated[tuple[ShortText, ...], Field(max_length=12)]


class GuideDraft(StrictModel):
    title: ShortText; summary: Annotated[str, Field(min_length=1, max_length=5000)]; suggested_questions: Annotated[tuple[ShortText, ...], Field(max_length=3)] = ()


class TokenCounts(StrictModel):
    prompt: Annotated[int, Field(ge=0)] = 0; completion: Annotated[int, Field(ge=0)] = 0; total: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def consistent(self): _require(self.total >= self.prompt + self.completion, "token total is inconsistent"); return self


ModelRole = Literal["skill_selection", "investigation_planning", "evidence_review", "answer_synthesis", "report_formatting", "agent_reasoning", "routing_candidate", "routing_judge"]
AttemptFailure = Literal["route_unavailable", "transport_error", "timeout", "provider_error", "identity_mismatch", "response_incomplete", "finish_length", "empty_content", "invalid_json", "invalid_schema", "invalid_citation", "invalid_scope", "invalid_evidence", "transport_contract", "context_length_exceeded"]


class ModelAttempt(StrictModel):
    role: ModelRole; algorithm: Literal["direct_local", "direct_frontier", "switchyard_capability", "switchyard_escalation"]; destination_class: Literal["local_model", "internal_inference", "loopback_switchyard"]; configured_model: str; model_assertion: str | None = None
    identity_evidence: Literal["direct_provider_verified", "switchyard_target_asserted", "unavailable"]; state: Literal["attempted", "succeeded", "failed"]; failure_class: AttemptFailure | None = None; application_call_id: CorrelationID; application_request_id: CorrelationID; latency_ms: Annotated[float, Field(ge=0)]; tokens: TokenCounts = Field(default_factory=TokenCounts); validation_status: Literal["not_run", "valid", "invalid"] = "not_run"; switchyard_trial_id: CorrelationID | None = None; selected_tier: Literal["judge", "efficient", "capable"] | None = None

    @model_validator(mode="after")
    def consistent(self):
        legacy = self.algorithm == "switchyard_capability"; escalation = self.algorithm == "switchyard_escalation"; approved = {LOCAL_MODEL, LUNA_MODEL, SOL_MODEL, CAPABLE_MODEL, CAPABILITY_ROUTE}; _require(self.configured_model in approved and self.model_assertion in {None, LOCAL_MODEL, LUNA_MODEL, SOL_MODEL, CAPABLE_MODEL} and not any("llama" in item.lower() for item in (self.configured_model, self.model_assertion or "")), "unapproved model identity"); _require((self.state == "failed") == (self.failure_class is not None) and not (self.validation_status == "valid" and self.state != "succeeded" or self.validation_status == "invalid" and self.state != "failed"), "attempt failure state mismatch"); _require(legacy == (self.destination_class == "loopback_switchyard") == (self.configured_model == CAPABILITY_ROUTE) == (self.switchyard_trial_id is not None), "attempt route contract mismatch"); expected_destination = "local_model" if self.configured_model == LOCAL_MODEL else "internal_inference"; expected_model = {"judge": LUNA_MODEL, "efficient": LOCAL_MODEL, "capable": self.configured_model if self.configured_model in {SOL_MODEL, CAPABLE_MODEL} else CAPABLE_MODEL}.get(self.selected_tier); _require(not (escalation and (self.destination_class != expected_destination or self.switchyard_trial_id is not None or self.selected_tier is None or self.configured_model != expected_model) or not escalation and self.selected_tier is not None), "escalation attempt contract mismatch"); _require(not (self.algorithm == "direct_local" and (self.destination_class, self.configured_model) != ("local_model", LOCAL_MODEL) or self.algorithm == "direct_frontier" and (self.destination_class != "internal_inference" or self.configured_model not in {SOL_MODEL, CAPABLE_MODEL})), "direct attempt contract mismatch"); expected = "switchyard_target_asserted" if legacy else "direct_provider_verified"; _require(not (self.state == "succeeded" and (self.model_assertion is None or self.identity_evidence != expected) or not legacy and self.model_assertion and self.model_assertion != self.configured_model), "successful attempt lacks identity evidence"); return self


class SwitchyardTrialRow(StrictModel):
    model: str; tier: Literal["classifier", "weak", "strong", ""]; prompt_tokens: Annotated[int, Field(ge=0)] = 0; cached_tokens: Annotated[int, Field(ge=0)] = 0; cache_creation_tokens: Annotated[int, Field(ge=0)] = 0; completion_tokens: Annotated[int, Field(ge=0)] = 0; reasoning_tokens: Annotated[int, Field(ge=0)] = 0; total_tokens: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def approved(self): _require(self.model in {LOCAL_MODEL, LUNA_MODEL, SOL_MODEL} and "llama" not in self.model.lower(), "unapproved trial model"); _require(self.total_tokens >= self.prompt_tokens + self.completion_tokens, "trial token total is inconsistent"); return self


class SwitchyardTrialBundle(StrictModel):
    trial_id: CorrelationID; classifier_row: SwitchyardTrialRow | None = None; terminal_row: SwitchyardTrialRow | None = None; classifier_validity: Literal["classified", "ambiguous_fallthrough", "unavailable"]; fallback_reason: Annotated[str | None, Field(max_length=500)] = None; prior_target_model: Literal["unavailable"] = "unavailable"; prior_target_error: Literal["unavailable"] = "unavailable"; prior_target_row: Literal["unavailable"] = "unavailable"; provider_identity: Literal["unavailable"] = "unavailable"

    @model_validator(mode="after")
    def sanctioned_shape(self): validity = "unavailable" if self.terminal_row is None else "ambiguous_fallthrough" if self.terminal_row.tier == "" else "classified"; _require(not (self.classifier_row and (self.classifier_row.tier != "classifier" or self.classifier_row.model != LUNA_MODEL) or self.terminal_row and (self.terminal_row.tier == "classifier" or self.terminal_row.model not in {LOCAL_MODEL, SOL_MODEL})), "invalid trial row"); _require(self.classifier_validity == validity and not (self.fallback_reason and self.terminal_row is None), "invalid trial bundle"); return self


class AccelerationReceipt(StrictModel):
    tool: str; engine: Literal["cudf", "cuvs", "cugraph", "xgboost-gpu", "cuml", "deterministic"]; device: str; gpu_executed: bool; fallback_used: bool; duration_ms: Annotated[float, Field(ge=0)]; artifact_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$"); scenario_id: str | None = None
    market_manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$"); document_manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$"); market_readiness_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$"); document_readiness_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class RoutingReceipt(StrictModel):
    requested_mode: RouteMode; effective_mode: RouteMode; configured_model: str; returned_model: str | None = None; reason: ShortText
    remote_attempted: bool = False; fallback_used: bool = False; frontier_latched: bool = False; latency_ms: Annotated[float, Field(ge=0)] = 0


def legacy_routing_projection(attempt: ModelAttempt, requested_mode: RouteMode) -> RoutingReceipt:
    """Read-only bridge for consumers awaiting the authoritative attempt contract."""
    direct = attempt.identity_evidence == "direct_provider_verified" and attempt.validation_status == "valid"; escalation = attempt.algorithm == "switchyard_escalation"
    return RoutingReceipt(requested_mode=requested_mode, effective_mode="switchyard_escalation" if escalation else requested_mode, configured_model=SWITCHYARD_ROUTE if escalation else attempt.configured_model, returned_model=attempt.model_assertion if direct else None, reason=f"Switchyard escalation selected the {attempt.selected_tier} target." if escalation and direct else "direct provider identity verified" if direct else "capability target assertion; provider identity unavailable" if attempt.state == "succeeded" else "model attempt failed", remote_attempted=attempt.destination_class != "local_model", fallback_used=False, frontier_latched=attempt.selected_tier == "capable", latency_ms=attempt.latency_ms)


class Artifact(StrictModel):
    artifact_id: str; kind: Literal["price_series", "topic_projection", "propagation_graph", "report"]
    title: ShortText; data: dict[str, object] = Field(default_factory=dict)


class FinalReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"; title: ShortText; summary: Annotated[str, Field(min_length=1, max_length=5000)]; scope: InvestigationScope; no_data_reasons: Annotated[list[NoDataReason], Field(max_length=4)] = []

    @field_validator("no_data_reasons")
    @classmethod
    def ordered_reasons(cls, value): order = ("missing_news", "missing_company_release", "non_trading_day", "non_listed_or_delisted_instrument"); _require(value == sorted(set(value), key=order.index), "no-data reasons must be unique and ordered"); return value
    claims: Annotated[list[Claim], Field(max_length=30)]; citations: Annotated[list[Citation], Field(max_length=50)]; uncertainty: Annotated[list[str], Field(max_length=12)] = []
    receipts: Annotated[list[AccelerationReceipt], Field(max_length=20)] = []; routing: RoutingReceipt; answer_mode: AnswerMode | None = None
    model_attempts: Annotated[tuple[ModelAttempt, ...], Field(max_length=48)] = (); switchyard_trials: Annotated[tuple[SwitchyardTrialBundle, ...], Field(max_length=10)] = ()
    artifacts: Annotated[list[Artifact], Field(max_length=10)] = []; generated_at: datetime


class SecurityContract(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)
    @field_validator("limitation", check_fields=False)
    @classmethod
    def safe_detail(cls, value): return value if not value or not any(term in value.lower() for term in ("://", "authorization", "bearer ", "token=", "key=", "secret=")) else (_ for _ in ()).throw(ValueError("sensitive detail is forbidden"))


class NetworkEgressObservation(SecurityContract):
    schema_version: Literal["security-observation-v1"] = "security-observation-v1"; observation_id: CorrelationID; boundary: Literal["mcp", "local_model", "frontier_model", "switchyard"]; destination_class: Literal["internal_tools", "local_model", "internal_inference", "loopback_switchyard"]; decision: Literal["allowed", "denied", "unknown"]; attempt: Literal["attempted", "not_attempted", "unknown"]; outcome: Literal["succeeded", "failed", "blocked", "unknown"]
    call_id: CorrelationID | None = None; application_request_id: CorrelationID | None = None; switchyard_trial_id: CorrelationID | None = None; identity_evidence: Literal["direct_provider_verified", "switchyard_target_asserted"] | None = None; switchyard_trial_bundle: SwitchyardTrialBundle | None = None; unavailable_fields: tuple[Literal["provider_identity", "prior_target_model", "prior_target_error", "prior_target_row"], ...] = (); unavailable_provenance: Literal["stock_switchyard_v0.2"] | None = None; limitation: ShortText | None = None

    @model_validator(mode="after")
    def correlated(self):
        destination = {"mcp": "internal_tools", "local_model": "local_model", "frontier_model": "internal_inference", "switchyard": "loopback_switchyard"}[self.boundary]
        routed = self.boundary == "switchyard"
        physical = self.boundary != "mcp"
        trial_data = (self.switchyard_trial_id, self.switchyard_trial_bundle)
        identity = "switchyard_target_asserted" if routed else None if not physical else "direct_provider_verified"
        fallback = self.switchyard_trial_bundle.fallback_reason if self.switchyard_trial_bundle else None
        expected = ({"provider_identity"} | ({"prior_target_model", "prior_target_error", "prior_target_row"} if fallback else set())) if routed and self.attempt == "attempted" else set()
        _require(self.destination_class == destination, "boundary destination mismatch")
        _require(not (self.attempt == "attempted" and (not self.call_id or self.decision != "allowed" or self.outcome not in {"succeeded", "failed"}) or self.attempt == "not_attempted" and (self.call_id or self.identity_evidence or self.decision == "unknown" or self.outcome != "blocked" or not self.limitation) or self.attempt == "unknown" and (self.call_id or self.identity_evidence or self.outcome != "unknown" or not self.limitation)), "attempt correlation incomplete")
        _require(not (self.attempt == "attempted" and (not physical and self.application_request_id is not None or routed and (self.application_request_id is None or not all(trial_data) or self.switchyard_trial_bundle.trial_id != self.switchyard_trial_id) or not routed and any(trial_data)) or self.attempt != "attempted" and (self.application_request_id is not None or any(trial_data))), "model correlation incomplete")
        _require(self.identity_evidence in {None, identity} and not (self.outcome == "succeeded" and self.identity_evidence != identity), "identity evidence mismatch")
        _require(not (fallback and any(term in fallback.lower() for term in ("://", "authorization", "bearer ", "token=", "key=", "secret="))) and len(set(self.unavailable_fields)) == len(self.unavailable_fields) and set(self.unavailable_fields) == expected and not (expected and (self.unavailable_provenance != "stock_switchyard_v0.2" or not self.limitation)), "unavailable detail lacks provenance")
        return self


class ExternalActionObservation(SecurityContract):
    schema_version: Literal["security-observation-v1"] = "security-observation-v1"; observation_id: CorrelationID; action_class: Literal["none", "trade", "order", "message", "file_write", "arbitrary_url_fetch", "credential_access", "other_external"]; request_state: Literal["requested", "not_requested", "unknown"]; decision: Literal["allowed", "denied", "unknown"]; attempt: Literal["attempted", "not_attempted", "unknown"]; outcome: Literal["succeeded", "failed", "blocked", "unknown"]; limitation: ShortText | None = None

    @model_validator(mode="after")
    def observed(self): _require(not (self.request_state == "unknown" and ((self.decision, self.attempt, self.outcome) != ("unknown", "unknown", "unknown") or not self.limitation) or self.request_state == "not_requested" and (self.action_class != "none" or (self.decision, self.attempt, self.outcome) != ("allowed", "not_attempted", "succeeded")) or self.request_state == "requested" and self.action_class == "none"), "action observation mismatch"); return self


class TrustBoundaryViolation(SecurityContract):
    schema_version: Literal["security-observation-v1"] = "security-observation-v1"; boundary: Literal["policy", "mcp", "local_model", "frontier_model", "switchyard", "persistence", "sse", "secret_scan"]; code: Literal["unapproved_destination", "unapproved_tool", "correlation_mismatch", "identity_mismatch", "propagated_headers", "application_retry", "unsupported_trial_bundle", "secret_exposure", "instrumentation_error", "unsafe_action"]; observation_id: CorrelationID | None = None; call_id: CorrelationID | None = None; limitation: ShortText | None = None


class SecretScanObservation(SecurityContract):
    schema_version: Literal["security-observation-v1"] = "security-observation-v1"; status: Literal["pass", "fail", "unknown"]; checked_variables: Annotated[tuple[SecretName, ...], Field(max_length=32)] = (); matched_variables: Annotated[tuple[SecretName, ...], Field(max_length=32)] = (); limitation: ShortText | None = None

    @model_validator(mode="after")
    def consistent(self): _require(not (len(set(self.checked_variables)) != len(self.checked_variables) or len(set(self.matched_variables)) != len(self.matched_variables) or not set(self.matched_variables) <= set(self.checked_variables) or self.status == "pass" and self.matched_variables or self.status == "fail" and not self.matched_variables or self.status == "unknown" and (self.matched_variables or not self.limitation)), "secret scan status mismatch"); return self


class SecurityReceipt(SecurityContract):
    schema_version: Literal["security-receipt-v1"] = "security-receipt-v1"; investigation_id: CorrelationID; turn_id: CorrelationID; completeness: SecurityState; network_egress: Annotated[tuple[NetworkEgressObservation, ...], Field(max_length=80)]; external_actions: Annotated[tuple[ExternalActionObservation, ...], Field(min_length=1, max_length=20)]
    trust_boundary: SecurityState; violations: Annotated[tuple[TrustBoundaryViolation, ...], Field(max_length=40)]; unknown_reasons: Annotated[tuple[Literal["policy_observation_missing", "network_observation_unfinished", "correlation_missing", "secret_scan_error", "secret_configuration_error", "unsupported_trial_bundle"], ...], Field(max_length=20)] = (); secret_scan: SecretScanObservation; finalized_at: datetime

    @model_validator(mode="after")
    def complete(self): unsafe = any(item.request_state == "requested" and (item.decision, item.attempt, item.outcome) != ("denied", "not_attempted", "blocked") for item in self.external_actions); violation = bool(self.violations) or self.secret_scan.status == "fail" or unsafe; unknown = bool(self.unknown_reasons) or self.secret_scan.status == "unknown" or any("unknown" in {item.decision, item.attempt, item.outcome} for item in (*self.network_egress, *self.external_actions)) or any(item.request_state == "unknown" for item in self.external_actions); expected = "violation" if violation else "unknown" if unknown else "verified"; _require(self.completeness == expected and self.trust_boundary == expected and len({item.observation_id for item in (*self.network_egress, *self.external_actions)}) == len(self.network_egress) + len(self.external_actions) and self.finalized_at.tzinfo is not None, "security completeness mismatch"); return self


TOOL_DISPLAY_NAMES = {
    "get_price_context": "Price context", "detect_market_shock": "Market shock",
    "search_news": "News search", "find_historical_analogues": "Historical analogues",
    "trace_shock_propagation": "Shock propagation", "predict_volatility_risk": "Volatility risk", "project_news_topics": "Topic projection",
}
WORKFLOW_TERMINALS = ("help", "refusal", "clarification", "no_data", "partial", "success", "policy_failure", "tool_failure", "route_failure", "synthesis_failure", "verification_failure")


class ProgressEvidencePreview(StrictModel):
    evidence_id: Annotated[str, Field(min_length=8, max_length=80)]
    title: ShortText
    source_type: Literal["market", "news", "filing", "release", "relationship", "model"]


class ProgressPayload(StrictModel):
    """Closed, display-safe operational span; never a reasoning or request payload."""

    schema_version: Literal["progress-span-v1"] = "progress-span-v1"
    turn_id: Annotated[str, Field(pattern=r"^turn-\d{4}$")]
    span_id: Annotated[str, Field(pattern=r"^span-[a-f0-9]{16}$")]
    parent_span_id: Annotated[str | None, Field(pattern=r"^span-[a-f0-9]{16}$")] = None
    kind: Literal["policy", "planning", "tool", "model", "verification", "report"]; display_name: Literal["Investigation", "Scope policy", "Skill selection", "Investigation plan", "Evidence review", "Evidence plan", "Skill load", "Subagent analysis", *tuple(TOOL_DISPLAY_NAMES.values()), "Agent reasoning", "Routing judge", "Answer synthesis", "Answer formatting", "Report assembly", "Report verification"]; state: Literal["started", "completed", "failed", "cancelled", "reused"]
    started_at: datetime; completed_at: datetime | None = None
    elapsed_ms: Annotated[float | None, Field(ge=0)] = None
    plan_id: Annotated[str | None, Field(min_length=8, max_length=80)] = None
    tools: Annotated[tuple[Literal[*TOOL_DISPLAY_NAMES], ...], Field(max_length=7)] = ()
    planned_call_count: Annotated[int | None, Field(ge=0, le=36)] = None
    tool: Literal[*TOOL_DISPLAY_NAMES] | None = None
    call_sha256: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    outcome: Literal["ok", "partial", "no_data", "failed"] | None = None
    reused: bool | None = None
    engine: Literal["cudf", "cuvs", "cugraph", "xgboost-gpu", "cuml", "deterministic"] | None = None
    device: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    compute_ms: Annotated[float | None, Field(ge=0)] = None
    receipt_sha256: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    evidence_count: Annotated[int | None, Field(ge=0, le=100)] = None
    citation_count: Annotated[int | None, Field(ge=0, le=100)] = None
    evidence_preview: Annotated[tuple[ProgressEvidencePreview, ...], Field(max_length=3)] = ()
    route_mode: RouteMode | None = None
    configured_model: str | None = None; model_assertion: str | None = None
    identity_evidence: Literal["direct_provider_verified", "switchyard_target_asserted", "unavailable"] | None = None
    selected_tier: Literal["judge", "efficient", "capable"] | None = None
    tokens: TokenCounts | None = None; transport_ms: Annotated[float | None, Field(ge=0)] = None
    attempt: ModelAttempt | None = None
    switchyard_trials: Annotated[tuple[SwitchyardTrialBundle, ...], Field(max_length=1)] = ()
    terminal: Literal[*WORKFLOW_TERMINALS, "cancelled", "workflow_exception"] | None = None

    @model_validator(mode="after")
    def safe_and_consistent(self):
        terminal = self.state != "started"
        aware = self.started_at.tzinfo is not None and (self.completed_at is None or self.completed_at.tzinfo is not None)
        finite = all(value is None or math.isfinite(value) for value in (self.elapsed_ms, self.compute_ms, self.transport_ms))
        _require(aware and finite and terminal == (self.completed_at is not None) == (self.elapsed_ms is not None) and (self.completed_at is None or self.completed_at >= self.started_at), "progress timing is invalid")
        _require(self.parent_span_id != self.span_id, "progress span cannot parent itself")
        if self.kind == "tool":
            valid = self.tool is not None and self.call_sha256 is not None and self.reused is not None and (not terminal or self.outcome is not None) and (self.state != "reused" or self.reused is True) and (self.state not in {"completed", "reused"} or self.receipt_sha256 is not None)
            _require(valid, "tool span identity or outcome is incomplete")
        else: _require(all(value is None for value in (self.tool, self.call_sha256, self.outcome, self.reused, self.engine, self.device, self.compute_ms, self.receipt_sha256, self.evidence_count, self.citation_count)) and not self.evidence_preview, "tool detail is outside a tool span")
        if self.kind == "model":
            _require(self.route_mode is not None and (self.state not in {"completed", "reused"} or self.attempt is not None), "model route or attempt is missing")
            if self.attempt:
                _require(self.configured_model == self.attempt.configured_model and self.model_assertion == self.attempt.model_assertion and self.identity_evidence == self.attempt.identity_evidence and self.selected_tier == self.attempt.selected_tier and self.tokens == self.attempt.tokens and self.transport_ms == self.attempt.latency_ms, "model span does not match attempt")
                _require((self.state == "completed") == (self.attempt.state == "succeeded") and (self.state == "failed") == (self.attempt.state == "failed"), "model span state does not match attempt")
        else: _require(all(value is None for value in (self.route_mode, self.configured_model, self.model_assertion, self.identity_evidence, self.selected_tier, self.tokens, self.transport_ms, self.attempt)) and not self.switchyard_trials, "model detail is outside a model span")
        valid_plan = (self.kind in {"planning", "tool"} or self.plan_id is None) and (self.kind == "planning" or not self.tools and self.planned_call_count is None) and (self.kind != "planning" or self.planned_call_count is None or self.planned_call_count >= len(self.tools))
        _require(valid_plan, "progress plan detail is inconsistent")
        _require(self.kind == "report" or self.terminal is None, "terminal detail is outside a report span")
        wire = self.model_dump_json().lower()
        _require(not any(marker in wire for marker in ("://", "authorization", "bearer ", "api_key", "credential=", "secret=")), "sensitive progress detail is forbidden")
        return self


ProgressPayload.model_rebuild()


def progress_event_type(span: ProgressPayload) -> str:
    return "tool_started" if span.kind == "tool" and span.state == "started" else "tool_completed" if span.kind == "tool" else "routing" if span.kind == "model" and span.state != "started" else "planning"


class TrajectoryEvent(StrictModel):
    sequence: Annotated[int, Field(ge=1)]; event_type: Literal["scope", "planning", "tool_started", "tool_completed", "routing", "report", "error", "cancelled"]; label: ShortText; detail: Annotated[str, Field(max_length=1200)] = ""; occurred_at: datetime; tool: str | None = None; payload: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def safe_progress(self):
        if self.payload.get("schema_version") != "progress-span-v1": return self
        span = ProgressPayload.model_validate(self.payload)
        _require(self.event_type == progress_event_type(span) and self.tool == span.tool, "progress event projection mismatch")
        return self


def active_progress(events) -> dict[str, ProgressPayload]:
    opened: dict[str, ProgressPayload] = {}; seen: set[str] = set()
    for event in events:
        if event.payload.get("schema_version") != "progress-span-v1": continue
        span = ProgressPayload.model_validate(event.payload)
        if span.state == "started":
            _require(span.span_id not in seen, "duplicate progress start")
            _require(span.parent_span_id is None or span.parent_span_id in opened, "progress parent is not open")
            opened[span.span_id] = span; seen.add(span.span_id); continue
        start = opened.pop(span.span_id, None)
        _require(start is not None, "orphan or duplicate progress completion")
        _require((span.turn_id, span.parent_span_id, span.kind, span.display_name, span.started_at) == (start.turn_id, start.parent_span_id, start.kind, start.display_name, start.started_at), "progress completion does not match start")
    return opened


class InvestigationRecord(StrictModel):
    investigation_id: UUID; created_at: datetime; updated_at: datetime; status: Literal["created", "running", "completed", "needs_input", "refused", "failed", "cancelled"]; request: InvestigationRequest; turns: Annotated[list[str], Field(max_length=30)] = []; scope: InvestigationScope | None = None
    active_skill: ResearchSkillName | None = None
    events: Annotated[list[TrajectoryEvent], Field(max_length=300)] = []; security_receipts: Annotated[list[SecurityReceipt], Field(max_length=30)] = []; report: FinalReport | None = None; error: str | None = None

    @model_validator(mode="after")
    def unique_security_turns(self):
        valid = len({item.turn_id for item in self.security_receipts}) == len(self.security_receipts) and [item.sequence for item in self.events] == list(range(1, len(self.events) + 1))
        _require(valid, "duplicate security receipt turn or noncontiguous event sequence")
        opened = active_progress(self.events)
        _require(self.status in {"created", "running"} or not opened, "terminal investigation has open progress spans")
        return self
