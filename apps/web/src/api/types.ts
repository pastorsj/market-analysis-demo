export type RouteMode = "local_only" | "switchyard_escalation" | "frontier_only";
export type ScopeStatus = "supported" | "partially_supported" | "clarification" | "refused";
export type RunStatus = "created" | "running" | "completed" | "needs_input" | "refused" | "failed" | "cancelled";
export type EventType = "scope" | "planning" | "tool_started" | "tool_completed" | "routing" | "report" | "error" | "cancelled";
export type AnswerMode = "deterministic_policy" | "deterministic_evidence" | "model_synthesis";
export type NoDataReason = "missing_news" | "missing_company_release" | "non_trading_day" | "non_listed_or_delisted_instrument";
export type GroupKey = "supported_universe" | "ai_exposed_semiconductors" | "financials";
export type IdentityEvidence = "direct_provider_verified" | "switchyard_target_asserted" | "unavailable";
export type ClassifierValidity = "classified" | "ambiguous_fallthrough" | "unavailable";
export type SecurityState = "verified" | "unknown" | "violation";
export type FailureStage = "policy_failure" | "tool_failure" | "route_failure" | "synthesis_failure" | "verification_failure" | "workflow_exception" | "security_unknown" | "security_violation";
export type TerminalOutcome = "help" | "refusal" | "clarification" | "no_data" | "partial" | "success" | "cancelled" | FailureStage;
export type AttemptFailure = "route_unavailable" | "transport_error" | "timeout" | "provider_error" | "identity_mismatch" | "response_incomplete" | "finish_length" | "empty_content" | "invalid_json" | "invalid_schema" | "invalid_citation" | "invalid_scope" | "invalid_evidence" | "transport_contract" | "context_length_exceeded";

export const LOCAL_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4";
export const DRAFT_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark";
export const EMBED_MODEL = "nvidia/Nemotron-3-Embed-1B-BF16";
export const LUNA_MODEL = "openai/openai/gpt-5.6-luna";
export const SOL_MODEL = "openai/openai/gpt-5.6-sol";
export const CAPABLE_MODEL = "nvidia/nvidia/nemotron-3-ultra";
export const CAPABILITY_ROUTE = "market-agent-capability";
export const PRIMARY_TICKERS = ["NVDA", "AMD", "JPM", "GS", "SCHW"] as const;

export type PrimaryTicker = typeof PRIMARY_TICKERS[number];
export type ResearchSkillName = "market-dislocation" | "peer-comparison" | "historical-analogues" | "shock-propagation" | "volatility-risk" | "narrative-map" | "market-research-guide";
export type StatusModelRole = "local_generation" | "switchyard_efficient_target" | "speculative_assistant" | "retrieval_embedding" | "switchyard_classifier" | "switchyard_capable_target" | "report_formatter";
export type StatusLimitation = "reconstructed_later_market_data" | "licensed_news_unavailable" | "source_coverage_varies_by_ticker_and_cutoff";
export interface SystemDependencyStatus { readonly tools: boolean; readonly model: boolean; readonly coverage: boolean; readonly checkpoint: boolean; readonly mcp_contract: boolean; readonly event_catalog: boolean }
export interface StatusCompany { readonly symbol: PrimaryTicker; readonly display_name: string }
export interface StatusCoverage { readonly scenario_id: string; readonly scenario_manifest_sha256: string; readonly data_tier: "cc0_reconstruction" | "entitled_local"; readonly vintage_status: "reconstructed_later" | "archived_at_cutoff"; readonly first_session: string; readonly last_session: string; readonly session_count: number; readonly document_source_kinds: readonly ("company_release" | "filing" | "primary_source" | "licensed_news_metadata")[] }
export interface StatusModel { readonly model_id: string; readonly revision: string | null; readonly location: "local_model_service" | "local_tools_service" | "internal_inference_server"; readonly identity_basis: "immutable_revision" | "exact_server_route_id"; readonly roles: readonly StatusModelRole[]; readonly route_eligible: boolean; readonly dependency: "model" | "tools" | "remote_routing" }
export interface StatusRoute { readonly mode: RouteMode; readonly enabled: boolean; readonly disabled_reason: "remote_routing_disabled" | "required_dependencies_unavailable" | null; readonly model_roles: readonly StatusModelRole[]; readonly evidence_tools_unchanged: true }
export interface StatusObservability { readonly remote_inference_enabled: boolean; readonly langsmith_export_enabled: boolean; readonly project_link?: string }
export interface StatusContracts { readonly max_investigation_turns: 4; readonly max_concurrent_investigations: 1; readonly data: string; readonly skill: "market-agent-skills/deep-agent-1.0.0"; readonly prompt: "market-shock-grounded-synthesis/2.0.0"; readonly safety: "bounded-equity-research/1.0.0"; readonly generation_model: typeof LOCAL_MODEL; readonly embedding_model: typeof EMBED_MODEL; readonly embedding_revision: string }
export interface SystemStatus { readonly schema_version: "system-status-v1"; readonly service: "agent"; readonly version: "1.2.0"; readonly ready: boolean; readonly dependencies: SystemDependencyStatus; readonly remote_routing_enabled: boolean; readonly observability: StatusObservability; readonly investigations: "available"; readonly supported_tickers: readonly PrimaryTicker[]; readonly companies: readonly StatusCompany[]; readonly coverage: StatusCoverage; readonly limitations: readonly StatusLimitation[]; readonly models: readonly StatusModel[]; readonly routes: readonly StatusRoute[]; readonly contracts: StatusContracts }

export interface InvestigationRequest { question: string; ticker: string | null; as_of: string | null; route_mode: RouteMode; event_id?: string | null }
export interface Scope { status: ScopeStatus; action: "answer" | "partial_answer" | "clarify" | "refuse"; ticker: string | null; as_of: string | null; market_as_of: string | null; timezone: "America/New_York"; supported_universe: string[]; explanation: string; resolved_tickers: string[]; group_key: GroupKey | null }
export interface Citation { citation_id: string; evidence_id: string; title: string; url: string; source_type: "market" | "news" | "filing" | "release" | "relationship" | "model"; published_at: string; available_at: string; excerpt: string; content_sha256: string; hindsight: boolean }
export interface Claim { claim_id: string; text: string; kind: "fact" | "calculation" | "inference" | "limitation"; confidence: number; citation_ids: string[] }
export interface Receipt { tool: string; engine: "cudf" | "cuvs" | "cugraph" | "xgboost-gpu" | "cuml" | "deterministic"; device: string; gpu_executed: boolean; fallback_used: false; duration_ms: number; artifact_manifest_sha256: string; scenario_id: string | null; market_manifest_sha256: string | null; document_manifest_sha256: string | null; market_readiness_sha256: string | null; document_readiness_sha256: string | null }
export interface TokenCounts { prompt: number; completion: number; total: number }
export type SelectedTier = "judge" | "efficient" | "capable";
export interface ModelAttempt { role: "skill_selection" | "investigation_planning" | "evidence_review" | "answer_synthesis" | "report_formatting" | "agent_reasoning" | "routing_candidate" | "routing_judge"; algorithm: "direct_local" | "direct_frontier" | "switchyard_capability" | "switchyard_escalation"; destination_class: "local_model" | "internal_inference" | "loopback_switchyard"; configured_model: string; model_assertion: string | null; identity_evidence: IdentityEvidence; state: "attempted" | "succeeded" | "failed"; failure_class: AttemptFailure | null; application_call_id: string; application_request_id: string; latency_ms: number; tokens: TokenCounts; validation_status: "not_run" | "valid" | "invalid"; switchyard_trial_id: string | null; selected_tier: SelectedTier | null }
export interface SwitchyardTrialRow { model: string; tier: "classifier" | "weak" | "strong" | ""; prompt_tokens: number; cached_tokens: number; cache_creation_tokens: number; completion_tokens: number; reasoning_tokens: number; total_tokens: number }
export interface SwitchyardTrialBundle { trial_id: string; classifier_row: SwitchyardTrialRow | null; terminal_row: SwitchyardTrialRow | null; classifier_validity: ClassifierValidity; fallback_reason: string | null; prior_target_model: "unavailable"; prior_target_error: "unavailable"; prior_target_row: "unavailable"; provider_identity: "unavailable" }
export interface Routing { requested_mode: RouteMode; effective_mode: RouteMode; configured_model: string; returned_model: string | null; reason: string; remote_attempted: boolean; fallback_used: false; frontier_latched: boolean; latency_ms: number }
export interface Artifact { artifact_id: string; kind: "price_series" | "topic_projection" | "propagation_graph" | "report"; title: string; data: Record<string, unknown> }
export interface Report { schema_version: "1.0"; title: string; summary: string; scope: Scope; no_data_reasons: NoDataReason[]; claims: Claim[]; citations: Citation[]; uncertainty: string[]; receipts: Receipt[]; routing: Routing; answer_mode: AnswerMode; model_attempts: ModelAttempt[]; switchyard_trials: SwitchyardTrialBundle[]; artifacts: Artifact[]; generated_at: string }

export type Boundary = "mcp" | "local_model" | "frontier_model" | "switchyard";
export interface NetworkEgressObservation { schema_version: "security-observation-v1"; observation_id: string; boundary: Boundary; destination_class: "internal_tools" | "local_model" | "internal_inference" | "loopback_switchyard"; decision: "allowed" | "denied" | "unknown"; attempt: "attempted" | "not_attempted" | "unknown"; outcome: "succeeded" | "failed" | "blocked" | "unknown"; call_id: string | null; application_request_id: string | null; switchyard_trial_id: string | null; identity_evidence: Exclude<IdentityEvidence, "unavailable"> | null; switchyard_trial_bundle: SwitchyardTrialBundle | null; unavailable_fields: ("provider_identity" | "prior_target_model" | "prior_target_error" | "prior_target_row")[]; unavailable_provenance: "stock_switchyard_v0.2" | null; limitation: string | null }
export interface ExternalActionObservation { schema_version: "security-observation-v1"; observation_id: string; action_class: "none" | "trade" | "order" | "message" | "file_write" | "arbitrary_url_fetch" | "credential_access" | "other_external"; request_state: "requested" | "not_requested" | "unknown"; decision: "allowed" | "denied" | "unknown"; attempt: "attempted" | "not_attempted" | "unknown"; outcome: "succeeded" | "failed" | "blocked" | "unknown"; limitation: string | null }
export interface TrustBoundaryViolation { schema_version: "security-observation-v1"; boundary: "policy" | Boundary | "persistence" | "sse" | "secret_scan"; code: "unapproved_destination" | "unapproved_tool" | "correlation_mismatch" | "identity_mismatch" | "propagated_headers" | "application_retry" | "unsupported_trial_bundle" | "secret_exposure" | "instrumentation_error" | "unsafe_action"; observation_id: string | null; call_id: string | null; limitation: string | null }
export interface SecretScanObservation { schema_version: "security-observation-v1"; status: "pass" | "fail" | "unknown"; checked_variables: string[]; matched_variables: string[]; limitation: string | null }
export interface SecurityReceipt { schema_version: "security-receipt-v1"; investigation_id: string; turn_id: string; completeness: SecurityState; network_egress: NetworkEgressObservation[]; external_actions: ExternalActionObservation[]; trust_boundary: SecurityState; violations: TrustBoundaryViolation[]; unknown_reasons: ("policy_observation_missing" | "network_observation_unfinished" | "correlation_missing" | "secret_scan_error" | "secret_configuration_error" | "unsupported_trial_bundle")[]; secret_scan: SecretScanObservation; finalized_at: string }

export type ProgressKind = "policy" | "planning" | "tool" | "model" | "verification" | "report";
export type ProgressState = "started" | "completed" | "failed" | "cancelled" | "reused";
export type ProgressTool = "detect_market_shock" | "get_price_context" | "search_news" | "find_historical_analogues" | "trace_shock_propagation" | "predict_volatility_risk" | "project_news_topics";
export interface ProgressEvidencePreview { readonly evidence_id: string; readonly title: string; readonly source_type: Citation["source_type"] }
export interface ProgressPayload {
  readonly schema_version: "progress-span-v1"; readonly turn_id: string; readonly span_id: string; readonly parent_span_id: string | null;
  readonly kind: ProgressKind; readonly display_name: string; readonly state: ProgressState; readonly started_at: string; readonly completed_at: string | null; readonly elapsed_ms: number | null;
  readonly plan_id: string | null; readonly tools: readonly ProgressTool[]; readonly planned_call_count: number | null;
  readonly tool: ProgressTool | null; readonly call_sha256: string | null; readonly outcome: "ok" | "partial" | "no_data" | "failed" | null; readonly reused: boolean | null;
  readonly engine: Receipt["engine"] | null; readonly device: string | null; readonly compute_ms: number | null; readonly receipt_sha256: string | null;
  readonly evidence_count: number | null; readonly citation_count: number | null; readonly evidence_preview: readonly ProgressEvidencePreview[];
  readonly route_mode: RouteMode | null; readonly configured_model: string | null; readonly model_assertion: string | null; readonly identity_evidence: IdentityEvidence | null; readonly selected_tier: SelectedTier | null;
  readonly tokens: TokenCounts | null; readonly transport_ms: number | null; readonly attempt: ModelAttempt | null; readonly switchyard_trials: readonly SwitchyardTrialBundle[];
  readonly terminal: TerminalOutcome | null;
}

export interface TrajectoryEvent { sequence: number; event_type: EventType; label: string; detail: string; occurred_at: string; tool: string | null; payload: Record<string, unknown> }
export interface TerminalTurn {
  turn_id: string;
  terminal: TerminalOutcome;
  report: Report | null;
  security_receipt: SecurityReceipt;
  event: TrajectoryEvent;
}
export interface Investigation { investigation_id: string; created_at: string; updated_at: string; status: RunStatus; request: InvestigationRequest; turns: string[]; scope: Scope | null; active_skill: ResearchSkillName | null; events: TrajectoryEvent[]; security_receipts: SecurityReceipt[]; report: Report | null; error: string | null }
export interface TypedFailure { stage: FailureStage; code: string }

const routeModes = ["local_only", "switchyard_escalation", "frontier_only"] as const;
const statuses = ["created", "running", "completed", "needs_input", "refused", "failed", "cancelled"] as const;
const eventTypes = ["scope", "planning", "tool_started", "tool_completed", "routing", "report", "error", "cancelled"] as const;
const answerModes = ["deterministic_policy", "deterministic_evidence", "model_synthesis"] as const;
const failureStages = ["policy_failure", "tool_failure", "route_failure", "synthesis_failure", "verification_failure", "workflow_exception", "security_unknown", "security_violation"] as const;
const attemptFailures = ["route_unavailable", "transport_error", "timeout", "provider_error", "identity_mismatch", "response_incomplete", "finish_length", "empty_content", "invalid_json", "invalid_schema", "invalid_citation", "invalid_scope", "invalid_evidence", "transport_contract", "context_length_exceeded"] as const;
const correlation = /^[A-Za-z0-9_-]{8,80}$/;
const stableCode = /^[a-z][a-z0-9_]{0,63}$/;
const digestPattern = /^[a-f0-9]{64}$/;
const isoDatePattern = /^\d{4}-\d{2}-\d{2}$/;
const projectLinkPattern = /^https:\/\/smith\.langchain\.com\/o\/[A-Za-z0-9-]+\/projects\/p\/[A-Za-z0-9-]+$/;
const GENERATION_REVISION = "bee7596271d1495f6992ae224aefde4410e816b8";
const DRAFT_REVISION = "8a0177116d138011e63103110f136ec0ca09ebbf";
const EMBED_REVISION = "9e0b24858b1195815ecb1188ffa1b73bcea7b30a";
const STATUS_MODELS = [
  { model_id: LOCAL_MODEL, revision: GENERATION_REVISION, location: "local_model_service", identity_basis: "immutable_revision", roles: ["local_generation", "switchyard_efficient_target"], route_eligible: true, dependency: "model" },
  { model_id: DRAFT_MODEL, revision: DRAFT_REVISION, location: "local_model_service", identity_basis: "immutable_revision", roles: ["speculative_assistant"], route_eligible: false, dependency: "model" },
  { model_id: EMBED_MODEL, revision: EMBED_REVISION, location: "local_tools_service", identity_basis: "immutable_revision", roles: ["retrieval_embedding"], route_eligible: false, dependency: "tools" },
  { model_id: LUNA_MODEL, revision: null, location: "internal_inference_server", identity_basis: "exact_server_route_id", roles: ["switchyard_classifier"], route_eligible: true, dependency: "remote_routing" },
  { model_id: CAPABLE_MODEL, revision: null, location: "internal_inference_server", identity_basis: "exact_server_route_id", roles: ["switchyard_capable_target", "report_formatter"], route_eligible: true, dependency: "remote_routing" },
] as const;
const STATUS_COMPANIES = [
  { symbol: "NVDA", display_name: "NVIDIA" }, { symbol: "AMD", display_name: "Advanced Micro Devices" },
  { symbol: "JPM", display_name: "JPMorgan Chase" }, { symbol: "GS", display_name: "Goldman Sachs" },
  { symbol: "SCHW", display_name: "Charles Schwab" },
] as const;

const recordKeys = ["investigation_id", "created_at", "updated_at", "status", "request", "turns", "scope", "active_skill", "events", "security_receipts", "report", "error"];
const requestKeys = ["question", "ticker", "as_of", "route_mode"];
const scopeKeys = ["status", "action", "ticker", "as_of", "market_as_of", "timezone", "supported_universe", "explanation", "resolved_tickers", "group_key"];
const eventKeys = ["sequence", "event_type", "label", "detail", "occurred_at", "tool", "payload"];
const reportKeys = ["schema_version", "title", "summary", "scope", "no_data_reasons", "claims", "citations", "uncertainty", "receipts", "routing", "answer_mode", "model_attempts", "switchyard_trials", "artifacts", "generated_at"];
const routingKeys = ["requested_mode", "effective_mode", "configured_model", "returned_model", "reason", "remote_attempted", "fallback_used", "frontier_latched", "latency_ms"];
const attemptKeys = ["role", "algorithm", "destination_class", "configured_model", "model_assertion", "identity_evidence", "state", "failure_class", "application_call_id", "application_request_id", "latency_ms", "tokens", "validation_status", "switchyard_trial_id", "selected_tier"];
const trialRowKeys = ["model", "tier", "prompt_tokens", "cached_tokens", "cache_creation_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"];
const trialKeys = ["trial_id", "classifier_row", "terminal_row", "classifier_validity", "fallback_reason", "prior_target_model", "prior_target_error", "prior_target_row", "provider_identity"];
const securityKeys = ["schema_version", "investigation_id", "turn_id", "completeness", "network_egress", "external_actions", "trust_boundary", "violations", "unknown_reasons", "secret_scan", "finalized_at"];
const progressKeys = ["schema_version", "turn_id", "span_id", "parent_span_id", "kind", "display_name", "state", "started_at", "completed_at", "elapsed_ms", "plan_id", "tools", "planned_call_count", "tool", "call_sha256", "outcome", "reused", "engine", "device", "compute_ms", "receipt_sha256", "evidence_count", "citation_count", "evidence_preview", "route_mode", "configured_model", "model_assertion", "identity_evidence", "selected_tier", "tokens", "transport_ms", "attempt", "switchyard_trials", "terminal"];
const progressKinds = ["policy", "planning", "tool", "model", "verification", "report"] as const;
const progressStates = ["started", "completed", "failed", "cancelled", "reused"] as const;
const progressNames = ["Investigation", "Scope policy", "Skill selection", "Investigation plan", "Evidence review", "Evidence plan", "Skill load", "Subagent analysis", "Price context", "Market shock", "News search", "Historical analogues", "Shock propagation", "Volatility risk", "Topic projection", "Agent reasoning", "Routing judge", "Answer synthesis", "Answer formatting", "Report assembly", "Report verification"] as const;
const progressTools = ["detect_market_shock", "get_price_context", "search_news", "find_historical_analogues", "trace_shock_propagation", "predict_volatility_risk", "project_news_topics"] as const;
const progressTerminals = ["help", "refusal", "clarification", "no_data", "partial", "success", "cancelled", "policy_failure", "tool_failure", "route_failure", "synthesis_failure", "verification_failure", "workflow_exception"] as const;
const spanPattern = /^span-[a-f0-9]{16}$/;

function object(value: unknown, label: string): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) throw Error(`Invalid ${label}`); return value as Record<string, unknown> }
function exact(value: Record<string, unknown>, keys: string[], label: string): void { const actual = Object.keys(value); if (actual.length !== keys.length || actual.some((key) => !keys.includes(key)) || keys.some((key) => !(key in value))) throw Error(`Invalid ${label} fields`) }
function list(value: unknown, label: string): unknown[] { if (!Array.isArray(value)) throw Error(`Invalid ${label}`); return value }
function text(value: unknown, label: string, nullable = false): void { if (!(nullable && value === null) && (typeof value !== "string" || value.length === 0)) throw Error(`Invalid ${label}`) }
function finite(value: unknown, label: string, integer = false): void { if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || integer && !Number.isInteger(value)) throw Error(`Invalid ${label}`) }
function member<T extends string>(value: unknown, allowed: readonly T[], label: string): asserts value is T { if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) throw Error(`Invalid ${label}`) }
function id(value: unknown, label: string): void { if (typeof value !== "string" || !correlation.test(value)) throw Error(`Invalid ${label}`) }
function nullable<T>(value: unknown, validate: (item: unknown) => T): T | null { return value === null ? null : validate(value) }
function canonical(value: unknown): unknown { if (Array.isArray(value)) return value.map(canonical); if (value && typeof value === "object") return Object.fromEntries(Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => [key, canonical(item)])); return value }
function same(left: unknown, right: unknown): boolean { return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right)) }
function timestamp(value: unknown, label: string): number { if (typeof value !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) throw Error(`Invalid ${label}`); return Date.parse(value) }

function rejectUnsafeStatus(value: unknown): void {
  const unsafeFields = new Set(["url", "uri", "endpoint", "key", "token", "secret", "credential", "password", "host"]);
  const visit = (item: unknown, field?: string): void => {
    if (Array.isArray(item)) { item.forEach((child) => visit(child)); return; }
    if (item && typeof item === "object") {
      for (const [key, child] of Object.entries(item as Record<string, unknown>)) {
        if (key.toLowerCase().replaceAll("-", "_").split("_").some((part) => unsafeFields.has(part))) throw Error("Unsafe status field");
        visit(child, key);
      }
      return;
    }
    if (typeof item === "string") {
      const approvedProjectLink = field === "project_link" && projectLinkPattern.test(item);
      if (/llama/i.test(item) || /:\/\//.test(item) && !approvedProjectLink || /bearer\s|api[_-]?key|credential|password|secret/i.test(item)) throw Error("Unsafe status value");
    }
  };
  visit(value);
}

function statusBoolean(value: unknown, label: string): asserts value is boolean { if (typeof value !== "boolean") throw Error(`Invalid ${label}`) }
function statusDate(value: unknown, label: string): asserts value is string {
  if (typeof value !== "string" || !isoDatePattern.test(value) || new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) !== value) throw Error(`Invalid ${label}`);
}

export function decodeSystemStatus(value: unknown): SystemStatus {
  rejectUnsafeStatus(value);
  const item = object(value, "system status");
  exact(item, ["schema_version", "service", "version", "ready", "dependencies", "remote_routing_enabled", "observability", "investigations", "supported_tickers", "companies", "coverage", "limitations", "models", "routes", "contracts"], "system status");
  if (item.schema_version !== "system-status-v1" || item.service !== "agent" || item.version !== "1.2.0" || item.investigations !== "available") throw Error("Invalid system status identity");
  statusBoolean(item.ready, "system readiness"); statusBoolean(item.remote_routing_enabled, "remote routing state");

  const dependencies = object(item.dependencies, "status dependencies"); exact(dependencies, ["tools", "model", "coverage", "checkpoint", "mcp_contract", "event_catalog"], "status dependencies");
  for (const key of ["tools", "model", "coverage", "checkpoint", "mcp_contract", "event_catalog"]) statusBoolean(dependencies[key], `dependency ${key}`);
  if (item.ready !== (Object.values(dependencies).every((state) => state === true) && item.remote_routing_enabled === true)) throw Error("Inconsistent system readiness");

  const observability = object(item.observability, "status observability"), hasProjectLink = Object.hasOwn(observability, "project_link");
  exact(observability, hasProjectLink ? ["remote_inference_enabled", "langsmith_export_enabled", "project_link"] : ["remote_inference_enabled", "langsmith_export_enabled"], "status observability");
  statusBoolean(observability.remote_inference_enabled, "remote inference state"); statusBoolean(observability.langsmith_export_enabled, "LangSmith export state");
  if (observability.remote_inference_enabled !== item.remote_routing_enabled) throw Error("Inconsistent remote inference state");
  if (observability.langsmith_export_enabled) {
    if (!hasProjectLink || typeof observability.project_link !== "string" || !projectLinkPattern.test(observability.project_link)) throw Error("Invalid LangSmith project link");
  } else if (hasProjectLink) throw Error("Unexpected LangSmith project link");

  const tickers = list(item.supported_tickers, "supported tickers");
  if (!same(tickers, PRIMARY_TICKERS) || new Set(tickers).size !== tickers.length) throw Error("Invalid supported tickers");
  const companies = list(item.companies, "status companies");
  for (const company of companies) { const row = object(company, "status company"); exact(row, ["symbol", "display_name"], "status company"); }
  if (!same(companies, STATUS_COMPANIES)) throw Error("Invalid status companies");

  const coverage = object(item.coverage, "status coverage");
  exact(coverage, ["scenario_id", "scenario_manifest_sha256", "data_tier", "vintage_status", "first_session", "last_session", "session_count", "document_source_kinds"], "status coverage");
  text(coverage.scenario_id, "coverage scenario"); if (typeof coverage.scenario_manifest_sha256 !== "string" || !digestPattern.test(coverage.scenario_manifest_sha256)) throw Error("Invalid coverage digest");
  member(coverage.data_tier, ["cc0_reconstruction", "entitled_local"] as const, "data tier"); member(coverage.vintage_status, ["reconstructed_later", "archived_at_cutoff"] as const, "vintage status");
  statusDate(coverage.first_session, "first session"); statusDate(coverage.last_session, "last session"); if (coverage.first_session > coverage.last_session) throw Error("Invalid coverage range"); finite(coverage.session_count, "session count", true); if ((coverage.session_count as number) < 1) throw Error("Invalid session count");
  const sourceKinds = list(coverage.document_source_kinds, "document source kinds"); const allowedSources = ["company_release", "filing", "primary_source", "licensed_news_metadata"] as const;
  sourceKinds.forEach((source) => member(source, allowedSources, "document source kind")); if (!sourceKinds.length || new Set(sourceKinds).size !== sourceKinds.length || !same(sourceKinds, [...sourceKinds].sort())) throw Error("Invalid document source kinds");

  const limitations = list(item.limitations, "status limitations"); const expectedLimitations: StatusLimitation[] = [];
  if (coverage.vintage_status === "reconstructed_later") expectedLimitations.push("reconstructed_later_market_data");
  if (!sourceKinds.includes("licensed_news_metadata")) expectedLimitations.push("licensed_news_unavailable");
  expectedLimitations.push("source_coverage_varies_by_ticker_and_cutoff");
  if (!same(limitations, expectedLimitations)) throw Error("Invalid status limitations");

  const models = list(item.models, "status models"); for (const model of models) { const row = object(model, "status model"); exact(row, ["model_id", "revision", "location", "identity_basis", "roles", "route_eligible", "dependency"], "status model"); }
  if (!same(models, STATUS_MODELS)) throw Error("Invalid status model catalog");

  const routes = list(item.routes, "status routes");
  if (routes.length !== 1) throw Error("Invalid status routes");
  const route = object(routes[0], "status route"); exact(route, ["mode", "enabled", "disabled_reason", "model_roles", "evidence_tools_unchanged"], "status route");
  if (route.mode !== "switchyard_escalation" || !same(route.model_roles, ["switchyard_classifier", "local_generation", "switchyard_capable_target", "report_formatter"]) || route.evidence_tools_unchanged !== true) throw Error("Invalid status route contract");
  statusBoolean(route.enabled, "route availability"); if (route.disabled_reason !== null) member(route.disabled_reason, ["remote_routing_disabled", "required_dependencies_unavailable"] as const, "route disabled reason");
  const common = dependencies.tools && dependencies.model && dependencies.coverage && dependencies.checkpoint && dependencies.mcp_contract && dependencies.event_catalog;
  const enabled = common && item.remote_routing_enabled === true, reason = enabled ? null : !item.remote_routing_enabled ? "remote_routing_disabled" : "required_dependencies_unavailable";
  if (route.enabled !== enabled || route.disabled_reason !== reason) throw Error("Inconsistent route availability");

  const contracts = object(item.contracts, "status contracts"); exact(contracts, ["max_investigation_turns", "max_concurrent_investigations", "data", "skill", "prompt", "safety", "generation_model", "embedding_model", "embedding_revision"], "status contracts");
  if (contracts.max_investigation_turns !== 4 || contracts.max_concurrent_investigations !== 1 || contracts.data !== coverage.scenario_id || contracts.skill !== "market-agent-skills/deep-agent-1.0.0" || contracts.prompt !== "market-shock-grounded-synthesis/2.0.0" || contracts.safety !== "bounded-equity-research/1.0.0" || contracts.generation_model !== LOCAL_MODEL || contracts.embedding_model !== EMBED_MODEL || contracts.embedding_revision !== EMBED_REVISION) throw Error("Invalid status contracts");
  return item as unknown as SystemStatus;
}

function validateRequest(value: unknown): void {
  const item = object(value, "request");
  const hasEvent = Object.hasOwn(item, "event_id");
  exact(item, hasEvent ? [...requestKeys, "event_id"] : requestKeys, "request");
  text(item.question, "question"); text(item.ticker, "ticker", true); text(item.as_of, "cutoff", true); member(item.route_mode, routeModes, "route mode");
  if (hasEvent && item.event_id !== null && (typeof item.event_id !== "string" || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(item.event_id) || item.event_id.length > 96)) throw Error("Invalid event ID");
  if (typeof item.event_id === "string" && (item.ticker === null || item.as_of === null)) throw Error("Event request is missing canonical scope");
}

function validateScope(value: unknown): void {
  const item = object(value, "scope"); exact(item, scopeKeys, "scope");
  member(item.status, ["supported", "partially_supported", "clarification", "refused"] as const, "scope status"); member(item.action, ["answer", "partial_answer", "clarify", "refuse"] as const, "scope action");
  text(item.ticker, "scope ticker", true); text(item.as_of, "scope cutoff", true); text(item.market_as_of, "market cutoff", true); if (item.timezone !== "America/New_York") throw Error("Invalid scope timezone");
  const universe = list(item.supported_universe, "supported universe"), resolved = list(item.resolved_tickers, "resolved tickers");
  if (universe.some((entry) => typeof entry !== "string") || resolved.length > 5 || resolved.some((entry) => typeof entry !== "string") || new Set(resolved).size !== resolved.length) throw Error("Invalid scope tickers");
  text(item.explanation, "scope explanation"); if (item.group_key !== null) member(item.group_key, ["supported_universe", "ai_exposed_semiconductors", "financials"] as const, "group key");
  if (resolved.length && item.ticker !== resolved[0] || item.group_key !== null && !resolved.length || item.market_as_of !== null && (item.as_of === null || timestamp(item.market_as_of, "market cutoff") > timestamp(item.as_of, "scope cutoff"))) throw Error("Invalid resolved scope");
}

function validateTokenCounts(value: unknown): void {
  const item = object(value, "token counts"); exact(item, ["prompt", "completion", "total"], "token counts"); finite(item.prompt, "prompt tokens", true); finite(item.completion, "completion tokens", true); finite(item.total, "total tokens", true);
  if ((item.total as number) < (item.prompt as number) + (item.completion as number)) throw Error("Invalid token total");
}

function validateAttempt(value: unknown): ModelAttempt {
  const item = object(value, "model attempt"); exact(item, attemptKeys, "model attempt"); member(item.role, ["skill_selection", "investigation_planning", "evidence_review", "answer_synthesis", "report_formatting", "agent_reasoning", "routing_candidate", "routing_judge"] as const, "model role");
  member(item.algorithm, ["direct_local", "direct_frontier", "switchyard_capability", "switchyard_escalation"] as const, "model algorithm"); member(item.destination_class, ["local_model", "internal_inference", "loopback_switchyard"] as const, "model destination");
  text(item.configured_model, "configured model"); text(item.model_assertion, "model assertion", true); member(item.identity_evidence, ["direct_provider_verified", "switchyard_target_asserted", "unavailable"] as const, "identity evidence"); member(item.state, ["attempted", "succeeded", "failed"] as const, "attempt state");
  if (item.failure_class !== null) member(item.failure_class, attemptFailures, "attempt failure"); id(item.application_call_id, "application call ID"); id(item.application_request_id, "application request ID"); finite(item.latency_ms, "attempt latency"); validateTokenCounts(item.tokens); member(item.validation_status, ["not_run", "valid", "invalid"] as const, "validation status"); if (item.switchyard_trial_id !== null) id(item.switchyard_trial_id, "Switchyard trial ID");
  if (item.selected_tier !== null) member(item.selected_tier, ["judge", "efficient", "capable"] as const, "selected tier");
  const legacyRouted = item.algorithm === "switchyard_capability", escalation = item.algorithm === "switchyard_escalation", approved = [LOCAL_MODEL, LUNA_MODEL, CAPABLE_MODEL, SOL_MODEL, CAPABILITY_ROUTE];
  if (!approved.includes(item.configured_model as string) || item.model_assertion !== null && ![LOCAL_MODEL, LUNA_MODEL, CAPABLE_MODEL, SOL_MODEL].includes(item.model_assertion as string)) throw Error("Unapproved model identity");
  if ((item.state === "failed") !== (item.failure_class !== null) || item.validation_status === "valid" && item.state !== "succeeded" || item.validation_status === "invalid" && item.state !== "failed") throw Error("Inconsistent model attempt");
  if (legacyRouted !== (item.destination_class === "loopback_switchyard") || legacyRouted !== (item.configured_model === CAPABILITY_ROUTE) || legacyRouted !== (item.switchyard_trial_id !== null)) throw Error("Inconsistent capability attempt");
  const expectedDestination = item.configured_model === LOCAL_MODEL ? "local_model" : "internal_inference";
  // Historical Sol receipts remain readable; current selection comes only from STATUS_MODELS.
  const tierModels: Record<SelectedTier, readonly string[]> = { judge: [LUNA_MODEL], efficient: [LOCAL_MODEL], capable: [CAPABLE_MODEL, SOL_MODEL] };
  if (escalation && (item.destination_class !== expectedDestination || item.switchyard_trial_id !== null || item.selected_tier === null || !tierModels[item.selected_tier as SelectedTier].includes(item.configured_model as string)) || !escalation && item.selected_tier !== null) throw Error("Inconsistent escalation attempt");
  if (item.algorithm === "direct_local" && (item.destination_class !== "local_model" || item.configured_model !== LOCAL_MODEL) || item.algorithm === "direct_frontier" && (item.destination_class !== "internal_inference" || ![CAPABLE_MODEL, SOL_MODEL].includes(item.configured_model as string))) throw Error("Inconsistent direct attempt");
  const expectedIdentity = legacyRouted ? "switchyard_target_asserted" : "direct_provider_verified", unavailable = item.model_assertion === null && item.identity_evidence === "unavailable", verified = item.model_assertion !== null && item.identity_evidence === expectedIdentity;
  if (item.state === "succeeded" && !verified || item.state === "failed" && !unavailable && !verified || item.state === "attempted" && !unavailable || !legacyRouted && item.model_assertion !== null && item.model_assertion !== item.configured_model) throw Error("Unverified model identity");
  return item as unknown as ModelAttempt;
}

function validateTrialRow(value: unknown): SwitchyardTrialRow {
  const item = object(value, "trial row"); exact(item, trialRowKeys, "trial row"); member(item.model, [LOCAL_MODEL, LUNA_MODEL, SOL_MODEL] as const, "trial model"); member(item.tier, ["classifier", "weak", "strong", ""] as const, "trial tier");
  for (const key of trialRowKeys.slice(2)) finite(item[key], key, true); if ((item.total_tokens as number) < (item.prompt_tokens as number) + (item.completion_tokens as number)) throw Error("Invalid trial token total"); return item as unknown as SwitchyardTrialRow;
}

function validateTrial(value: unknown): SwitchyardTrialBundle {
  const item = object(value, "Switchyard trial bundle"); exact(item, trialKeys, "Switchyard trial bundle"); id(item.trial_id, "trial ID");
  const classifier = nullable(item.classifier_row, validateTrialRow), terminalRow = nullable(item.terminal_row, validateTrialRow); member(item.classifier_validity, ["classified", "ambiguous_fallthrough", "unavailable"] as const, "classifier validity"); text(item.fallback_reason, "fallback reason", true);
  for (const key of ["prior_target_model", "prior_target_error", "prior_target_row", "provider_identity"]) if (item[key] !== "unavailable") throw Error("Invented prior target detail");
  if (classifier && (classifier.tier !== "classifier" || classifier.model !== LUNA_MODEL) || terminalRow && (terminalRow.tier === "classifier" || ![LOCAL_MODEL, SOL_MODEL].includes(terminalRow.model))) throw Error("Invalid trial roles");
  const expected = terminalRow === null ? "unavailable" : terminalRow.tier === "" ? "ambiguous_fallthrough" : "classified";
  if (item.classifier_validity !== expected || item.fallback_reason !== null && terminalRow === null) throw Error("Inconsistent trial bundle"); return item as unknown as SwitchyardTrialBundle;
}

function validateRouting(value: unknown): Routing {
  const item = object(value, "compatibility routing projection"); exact(item, routingKeys, "compatibility routing projection"); member(item.requested_mode, routeModes, "requested route"); member(item.effective_mode, routeModes, "effective route");
  text(item.configured_model, "routing model"); text(item.returned_model, "returned model", true); text(item.reason, "routing reason");
  if (typeof item.remote_attempted !== "boolean" || item.fallback_used !== false || typeof item.frontier_latched !== "boolean") throw Error("Invalid routing state"); finite(item.latency_ms, "routing latency");
  return item as unknown as Routing;
}

function validateReceipt(value: unknown): void {
  const keys = ["tool", "engine", "device", "gpu_executed", "fallback_used", "duration_ms", "artifact_manifest_sha256", "scenario_id", "market_manifest_sha256", "document_manifest_sha256", "market_readiness_sha256", "document_readiness_sha256"];
  const item = object(value, "computation receipt"); exact(item, keys, "computation receipt"); text(item.tool, "receipt tool"); member(item.engine, ["cudf", "cuvs", "cugraph", "xgboost-gpu", "cuml", "deterministic"] as const, "receipt engine"); text(item.device, "receipt device");
  if (typeof item.gpu_executed !== "boolean" || item.fallback_used !== false) throw Error("Invalid computation receipt"); finite(item.duration_ms, "receipt duration"); for (const key of keys.slice(6)) text(item[key], key, key !== "artifact_manifest_sha256");
}

function validateCitation(value: unknown): void {
  const keys = ["citation_id", "evidence_id", "title", "url", "source_type", "published_at", "available_at", "excerpt", "content_sha256", "hindsight"], item = object(value, "citation"); exact(item, keys, "citation");
  for (const key of ["citation_id", "evidence_id", "title", "url", "published_at", "available_at", "excerpt", "content_sha256"]) text(item[key], `citation ${key}`); member(item.source_type, ["market", "news", "filing", "release", "relationship", "model"] as const, "citation source"); if (typeof item.hindsight !== "boolean") throw Error("Invalid citation hindsight");
}

function validateClaim(value: unknown): void {
  const item = object(value, "claim"); exact(item, ["claim_id", "text", "kind", "confidence", "citation_ids"], "claim"); text(item.claim_id, "claim ID"); text(item.text, "claim text"); member(item.kind, ["fact", "calculation", "inference", "limitation"] as const, "claim kind"); finite(item.confidence, "claim confidence");
  if ((item.confidence as number) > 1 || list(item.citation_ids, "claim citations").some((entry) => typeof entry !== "string")) throw Error("Invalid claim");
}

function logicalAttempts(attempts: ModelAttempt[]): ModelAttempt[] {
  if (new Set(attempts.map((attempt) => attempt.application_call_id)).size !== attempts.length) throw Error("Model answer contains an invalid or duplicate attempt");
  const requests = new Map<string, number[]>();
  attempts.forEach((attempt, index) => requests.set(attempt.application_request_id, [...(requests.get(attempt.application_request_id) ?? []), index]));
  const retried = new Set<number>();
  for (const indexes of requests.values()) {
    if (indexes.length === 1) continue;
    if (indexes.length !== 2 || indexes[1] !== indexes[0] + 1) throw Error("Invalid model transport retry");
    const first = attempts[indexes[0]], second = attempts[indexes[1]];
    const sameTarget = first.role === second.role && first.algorithm === second.algorithm && first.destination_class === second.destination_class && first.configured_model === second.configured_model && first.application_request_id === second.application_request_id && first.switchyard_trial_id === second.switchyard_trial_id && first.selected_tier === second.selected_tier;
    const failedTransport = first.algorithm === "switchyard_escalation" && first.destination_class === "internal_inference" && first.state === "failed" && first.failure_class === "transport_error" && first.model_assertion === null && first.identity_evidence === "unavailable" && first.validation_status === "invalid" && first.tokens.prompt === 0 && first.tokens.completion === 0 && first.tokens.total === 0;
    const succeeded = second.state === "succeeded" && second.failure_class === null && second.model_assertion === second.configured_model && second.identity_evidence === "direct_provider_verified" && second.validation_status === "valid";
    const transportFailedAgain = second.state === "failed" && second.failure_class === "transport_error" && second.model_assertion === null && second.identity_evidence === "unavailable" && second.validation_status === "invalid" && second.tokens.prompt === 0 && second.tokens.completion === 0 && second.tokens.total === 0;
    if (!sameTarget || !failedTransport || !(succeeded || transportFailedAgain) || first.application_call_id === second.application_call_id) throw Error("Invalid model transport retry");
    retried.add(indexes[0]);
  }
  return attempts.filter((_attempt, index) => !retried.has(index));
}

function validateReport(value: unknown): Report {
  const item = object(value, "report"); exact(item, reportKeys, "report"); if (item.schema_version !== "1.0") throw Error("Invalid report version"); text(item.title, "report title"); text(item.summary, "report summary"); validateScope(item.scope); const reasons = list(item.no_data_reasons, "no-data reasons"), order = ["missing_news", "missing_company_release", "non_trading_day", "non_listed_or_delisted_instrument"] as const; if (reasons.length > order.length || reasons.some((reason) => typeof reason !== "string" || !order.includes(reason as NoDataReason)) || new Set(reasons).size !== reasons.length || reasons.some((reason, index) => order.indexOf(reason as NoDataReason) <= order.indexOf(reasons[index - 1] as NoDataReason))) throw Error("Invalid no-data reasons");
  list(item.claims, "claims").forEach(validateClaim); list(item.citations, "citations").forEach(validateCitation); if (list(item.uncertainty, "uncertainty").some((entry) => typeof entry !== "string")) throw Error("Invalid uncertainty"); list(item.receipts, "receipts").forEach(validateReceipt); const routing = validateRouting(item.routing); member(item.answer_mode, answerModes, "answer mode");
  const attempts = list(item.model_attempts, "model attempts").map(validateAttempt), trials = list(item.switchyard_trials, "Switchyard trials").map(validateTrial), artifacts = list(item.artifacts, "artifacts");
  for (const raw of artifacts) { const artifact = object(raw, "artifact"); exact(artifact, ["artifact_id", "kind", "title", "data"], "artifact"); text(artifact.artifact_id, "artifact ID"); member(artifact.kind, ["price_series", "topic_projection", "propagation_graph", "report"] as const, "artifact kind"); text(artifact.title, "artifact title"); object(artifact.data, "artifact data"); }
  text(item.generated_at, "report timestamp");
  if (item.answer_mode === "model_synthesis") {
    const logical = logicalAttempts(attempts), formatters = logical.filter((attempt) => attempt.role === "report_formatting"), core = logical.filter((attempt) => attempt.role !== "report_formatting");
    const formatter = formatters[0] ?? null;
    const formatterTerminal = formatter && (formatter.state === "succeeded" && formatter.validation_status === "valid" || formatter.state === "failed" && formatter.validation_status === "invalid");
    if (formatters.length > 1 || formatter && (logical.at(-1) !== formatter || formatter.algorithm !== "direct_frontier" || !formatterTerminal)) throw Error("Invalid report formatting attempt");
    const escalation = core.some((attempt) => attempt.algorithm === "switchyard_escalation");
    const finalAttempt = escalation ? [...core].reverse().find((attempt) => attempt.role === "agent_reasoning") : core.at(-1);
    if (!finalAttempt) throw Error("Invalid deep-agent model attempt sequence");
    if (core.some((attempt) => attempt.state !== "succeeded" || attempt.validation_status !== "valid")) throw Error("Model answer contains an invalid or duplicate attempt");
    if (escalation) {
      if (core.some((attempt) => attempt.algorithm !== "switchyard_escalation") || trials.length || routing.requested_mode !== "switchyard_escalation" || routing.effective_mode !== "switchyard_escalation" || routing.configured_model !== "switchyard/escalation" || routing.returned_model !== finalAttempt.model_assertion || routing.remote_attempted !== core.some((attempt) => attempt.destination_class === "internal_inference") || routing.frontier_latched !== (finalAttempt.selected_tier === "capable") || routing.latency_ms !== finalAttempt.latency_ms) throw Error("Escalation route projection disagrees with model attempts");
    } else {
      const roles = core.map((attempt) => attempt.role), routed = core.filter((attempt) => attempt.algorithm === "switchyard_capability");
      if (roles[0] !== "skill_selection" || roles[1] !== "investigation_planning" || roles.at(-1) !== "answer_synthesis" || roles.slice(2, -1).some((role) => role !== "evidence_review")) throw Error("Invalid deep-agent model attempt sequence");
      if (trials.length !== routed.length || routed.some((attempt, index) => trials[index].trial_id !== attempt.switchyard_trial_id) || new Set(trials.map((trial) => trial.trial_id)).size !== trials.length) throw Error("Model answer trial mismatch");
      const direct = finalAttempt.identity_evidence === "direct_provider_verified", expectedReturned = direct ? finalAttempt.model_assertion : null;
      if (routing.requested_mode !== routing.effective_mode || routing.configured_model !== finalAttempt.configured_model || routing.returned_model !== expectedReturned || routing.remote_attempted !== (finalAttempt.destination_class !== "local_model") || routing.frontier_latched || routing.latency_ms !== finalAttempt.latency_ms) throw Error("Compatibility route projection disagrees with final synthesis attempt");
    }
  } else {
    if (attempts.length || trials.length) throw Error("Deterministic answer contains model routing");
    if (routing.requested_mode !== routing.effective_mode || routing.configured_model !== "deterministic" || routing.returned_model !== null || routing.remote_attempted || routing.frontier_latched || routing.latency_ms !== 0) throw Error("Compatibility route projection disagrees with deterministic answer");
  }
  return item as unknown as Report;
}

function validateSecretScan(value: unknown): SecretScanObservation {
  const item = object(value, "secret scan"); exact(item, ["schema_version", "status", "checked_variables", "matched_variables", "limitation"], "secret scan"); if (item.schema_version !== "security-observation-v1") throw Error("Invalid secret scan version"); member(item.status, ["pass", "fail", "unknown"] as const, "secret scan status"); text(item.limitation, "secret scan limitation", true);
  const checked = list(item.checked_variables, "checked variables"), matched = list(item.matched_variables, "matched variables"); if (checked.some((entry) => typeof entry !== "string") || matched.some((entry) => typeof entry !== "string") || new Set(checked).size !== checked.length || new Set(matched).size !== matched.length || matched.some((entry) => !checked.includes(entry))) throw Error("Invalid secret scan variables");
  if (item.status === "pass" && matched.length || item.status === "fail" && !matched.length || item.status === "unknown" && (matched.length || item.limitation === null)) throw Error("Inconsistent secret scan"); return item as unknown as SecretScanObservation;
}

function validateNetwork(value: unknown): NetworkEgressObservation {
  const keys = ["schema_version", "observation_id", "boundary", "destination_class", "decision", "attempt", "outcome", "call_id", "application_request_id", "switchyard_trial_id", "identity_evidence", "switchyard_trial_bundle", "unavailable_fields", "unavailable_provenance", "limitation"], item = object(value, "network observation"); exact(item, keys, "network observation");
  if (item.schema_version !== "security-observation-v1") throw Error("Invalid network observation version"); member(item.boundary, ["mcp", "local_model", "frontier_model", "switchyard"] as const, "network boundary"); member(item.destination_class, ["internal_tools", "local_model", "internal_inference", "loopback_switchyard"] as const, "destination class"); member(item.decision, ["allowed", "denied", "unknown"] as const, "network decision"); member(item.attempt, ["attempted", "not_attempted", "unknown"] as const, "network attempt"); member(item.outcome, ["succeeded", "failed", "blocked", "unknown"] as const, "network outcome"); id(item.observation_id, "network observation ID");
  for (const key of ["call_id", "application_request_id", "switchyard_trial_id"]) if (item[key] !== null) id(item[key], key); if (item.identity_evidence !== null) member(item.identity_evidence, ["direct_provider_verified", "switchyard_target_asserted"] as const, "network identity");
  const bundle = nullable(item.switchyard_trial_bundle, validateTrial), unavailable = list(item.unavailable_fields, "unavailable fields"), allowedUnavailable = ["provider_identity", "prior_target_model", "prior_target_error", "prior_target_row"];
  if (unavailable.some((entry) => typeof entry !== "string" || !allowedUnavailable.includes(entry)) || new Set(unavailable).size !== unavailable.length) throw Error("Invalid unavailable fields"); if (item.unavailable_provenance !== null && item.unavailable_provenance !== "stock_switchyard_v0.2") throw Error("Invalid unavailable provenance"); text(item.limitation, "network limitation", true);
  const destinations = { mcp: "internal_tools", local_model: "local_model", frontier_model: "internal_inference", switchyard: "loopback_switchyard" } as const;
  if (item.destination_class !== destinations[item.boundary as Boundary]) throw Error("Boundary destination mismatch");
  if (item.attempt === "attempted" && (item.decision !== "allowed" || !item.call_id || !["succeeded", "failed"].includes(item.outcome as string)) || item.attempt === "not_attempted" && (item.call_id !== null || item.identity_evidence !== null || item.decision === "unknown" || item.outcome !== "blocked" || item.limitation === null) || item.attempt === "unknown" && (item.call_id !== null || item.identity_evidence !== null || item.outcome !== "unknown" || item.limitation === null)) throw Error("Inconsistent network observation");
  const routed = item.boundary === "switchyard", modelBoundary = item.boundary !== "mcp";
  const trialData = [item.switchyard_trial_id, bundle], correlationData = [item.application_request_id, ...trialData];
  if (item.attempt === "attempted"
    ? modelBoundary !== (item.application_request_id !== null)
      || routed && (trialData.some((entry) => entry === null) || bundle?.trial_id !== item.switchyard_trial_id)
      || !routed && trialData.some((entry) => entry !== null)
    : correlationData.some((entry) => entry !== null)) throw Error("Invalid Switchyard correlation");
  const expectedIdentity = routed ? "switchyard_target_asserted" : item.boundary === "mcp" ? null : "direct_provider_verified"; if (item.identity_evidence !== null && item.identity_evidence !== expectedIdentity || item.outcome === "succeeded" && item.identity_evidence !== expectedIdentity) throw Error("Network identity mismatch");
  const fallback = bundle?.fallback_reason, expectedUnavailable = routed && item.attempt === "attempted" ? ["provider_identity", ...(fallback ? ["prior_target_model", "prior_target_error", "prior_target_row"] : [])] : [];
  if (unavailable.length !== expectedUnavailable.length || expectedUnavailable.some((entry) => !unavailable.includes(entry)) || expectedUnavailable.length && (item.unavailable_provenance !== "stock_switchyard_v0.2" || item.limitation === null)) throw Error("Unavailable route detail lacks provenance"); return item as unknown as NetworkEgressObservation;
}

function validateAction(value: unknown): ExternalActionObservation {
  const item = object(value, "external action"); exact(item, ["schema_version", "observation_id", "action_class", "request_state", "decision", "attempt", "outcome", "limitation"], "external action"); if (item.schema_version !== "security-observation-v1") throw Error("Invalid action version"); id(item.observation_id, "action observation ID");
  member(item.action_class, ["none", "trade", "order", "message", "file_write", "arbitrary_url_fetch", "credential_access", "other_external"] as const, "action class"); member(item.request_state, ["requested", "not_requested", "unknown"] as const, "action request state"); member(item.decision, ["allowed", "denied", "unknown"] as const, "action decision"); member(item.attempt, ["attempted", "not_attempted", "unknown"] as const, "action attempt"); member(item.outcome, ["succeeded", "failed", "blocked", "unknown"] as const, "action outcome"); text(item.limitation, "action limitation", true);
  if (item.request_state === "unknown" && (item.decision !== "unknown" || item.attempt !== "unknown" || item.outcome !== "unknown" || item.limitation === null) || item.request_state === "not_requested" && (item.action_class !== "none" || item.decision !== "allowed" || item.attempt !== "not_attempted" || item.outcome !== "succeeded") || item.request_state === "requested" && item.action_class === "none") throw Error("Inconsistent external action"); return item as unknown as ExternalActionObservation;
}

function validateViolation(value: unknown): TrustBoundaryViolation {
  const item = object(value, "trust boundary violation"); exact(item, ["schema_version", "boundary", "code", "observation_id", "call_id", "limitation"], "trust boundary violation"); if (item.schema_version !== "security-observation-v1") throw Error("Invalid violation version"); member(item.boundary, ["policy", "mcp", "local_model", "frontier_model", "switchyard", "persistence", "sse", "secret_scan"] as const, "violation boundary"); member(item.code, ["unapproved_destination", "unapproved_tool", "correlation_mismatch", "identity_mismatch", "propagated_headers", "application_retry", "unsupported_trial_bundle", "secret_exposure", "instrumentation_error", "unsafe_action"] as const, "violation code");
  for (const key of ["observation_id", "call_id"]) if (item[key] !== null) id(item[key], key); text(item.limitation, "violation limitation", true); return item as unknown as TrustBoundaryViolation;
}

function validateSecurity(value: unknown): SecurityReceipt {
  const item = object(value, "security receipt"); exact(item, securityKeys, "security receipt"); if (item.schema_version !== "security-receipt-v1") throw Error("Invalid security receipt version"); id(item.investigation_id, "security investigation ID"); id(item.turn_id, "security turn ID"); member(item.completeness, ["verified", "unknown", "violation"] as const, "security completeness"); member(item.trust_boundary, ["verified", "unknown", "violation"] as const, "trust boundary state");
  const network = list(item.network_egress, "network egress").map(validateNetwork), actions = list(item.external_actions, "external actions").map(validateAction), violations = list(item.violations, "violations").map(validateViolation); if (!actions.length) throw Error("Missing external action disposition");
  const unknownReasons = list(item.unknown_reasons, "unknown reasons"), allowedReasons = ["policy_observation_missing", "network_observation_unfinished", "correlation_missing", "secret_scan_error", "secret_configuration_error", "unsupported_trial_bundle"];
  if (unknownReasons.some((entry) => typeof entry !== "string" || !allowedReasons.includes(entry)) || new Set(unknownReasons).size !== unknownReasons.length) throw Error("Invalid security unknown reasons");
  const scan = validateSecretScan(item.secret_scan); text(item.finalized_at, "security timestamp");
  const unsafe = actions.some((entry) => entry.request_state === "requested" && (entry.decision !== "denied" || entry.attempt !== "not_attempted" || entry.outcome !== "blocked")), hasViolation = violations.length > 0 || scan.status === "fail" || unsafe;
  const hasUnknown = unknownReasons.length > 0 || scan.status === "unknown" || network.some((entry) => [entry.decision, entry.attempt, entry.outcome].includes("unknown")) || actions.some((entry) => entry.request_state === "unknown" || [entry.decision, entry.attempt, entry.outcome].includes("unknown"));
  const expected = hasViolation ? "violation" : hasUnknown ? "unknown" : "verified", observationIds = [...network, ...actions].map((entry) => entry.observation_id);
  if (item.completeness !== expected || item.trust_boundary !== expected || new Set(observationIds).size !== observationIds.length) throw Error("Inconsistent security receipt"); return item as unknown as SecurityReceipt;
}

function rejectUnsafeProgress(value: unknown): void {
  const visit = (item: unknown): void => {
    if (Array.isArray(item)) { item.forEach(visit); return; }
    if (item && typeof item === "object") { Object.values(item as Record<string, unknown>).forEach(visit); return; }
    if (typeof item === "string" && /:\/\/|authorization|bearer\s|api[_-]?key|(?:credential|token|key|secret)=/i.test(item)) throw Error("Unsafe progress detail");
  };
  visit(value);
}

function optionalText(value: unknown, label: string, maximum = 240): void {
  if (value !== null && (typeof value !== "string" || value.length < 1 || value.length > maximum)) throw Error(`Invalid ${label}`);
}

export function decodeProgressPayload(value: unknown): ProgressPayload {
  rejectUnsafeProgress(value);
  const item = object(value, "progress payload"); exact(item, progressKeys, "progress payload");
  if (item.schema_version !== "progress-span-v1" || typeof item.turn_id !== "string" || !/^turn-\d{4}$/.test(item.turn_id) || typeof item.span_id !== "string" || !spanPattern.test(item.span_id) || item.parent_span_id !== null && (typeof item.parent_span_id !== "string" || !spanPattern.test(item.parent_span_id)) || item.parent_span_id === item.span_id) throw Error("Invalid progress identity");
  member(item.kind, progressKinds, "progress kind"); member(item.display_name, progressNames, "progress display name"); member(item.state, progressStates, "progress state");
  const started = timestamp(item.started_at, "progress start"), terminalState = item.state !== "started";
  const completedAt = item.completed_at === null ? null : timestamp(item.completed_at, "progress completion");
  if (terminalState !== (completedAt !== null) || terminalState !== (item.elapsed_ms !== null)) throw Error("Invalid progress terminal timing");
  if (item.elapsed_ms !== null) finite(item.elapsed_ms, "progress elapsed");
  if (completedAt !== null && completedAt < started) throw Error("Progress completion precedes start");
  if (completedAt !== null && item.elapsed_ms !== null) {
    const elapsed = item.elapsed_ms as number, tolerance = Math.max(100, elapsed * .05);
    if (Math.abs(completedAt - started - elapsed) > tolerance) throw Error("Progress wall timestamps do not reconcile with server elapsed time");
  }

  optionalText(item.plan_id, "progress plan ID", 80); if (typeof item.plan_id === "string" && item.plan_id.length < 8) throw Error("Invalid progress plan ID");
  const tools = list(item.tools, "progress tools"); tools.forEach((tool) => member(tool, progressTools, "progress tool")); if (tools.length > 7 || new Set(tools).size !== tools.length) throw Error("Invalid progress tools");
  if (item.planned_call_count !== null) { finite(item.planned_call_count, "planned call count", true); if ((item.planned_call_count as number) > 36 || (item.planned_call_count as number) < tools.length) throw Error("Invalid planned call count"); }
  if (item.tool !== null) member(item.tool, progressTools, "progress tool");
  for (const key of ["call_sha256", "receipt_sha256"]) if (item[key] !== null && (typeof item[key] !== "string" || !digestPattern.test(item[key] as string))) throw Error(`Invalid ${key}`);
  if (item.outcome !== null) member(item.outcome, ["ok", "partial", "no_data", "failed"] as const, "tool outcome");
  if (item.reused !== null && typeof item.reused !== "boolean") throw Error("Invalid tool reuse state");
  if (item.engine !== null) member(item.engine, ["cudf", "cuvs", "cugraph", "xgboost-gpu", "cuml", "deterministic"] as const, "tool engine"); optionalText(item.device, "tool device", 120);
  if (item.compute_ms !== null) finite(item.compute_ms, "tool compute time");
  for (const key of ["evidence_count", "citation_count"]) if (item[key] !== null) { finite(item[key], key, true); if ((item[key] as number) > 100) throw Error(`Invalid ${key}`); }
  const previews = list(item.evidence_preview, "evidence preview"); if (previews.length > 3) throw Error("Invalid evidence preview");
  previews.forEach((raw) => { const preview = object(raw, "evidence preview"); exact(preview, ["evidence_id", "title", "source_type"], "evidence preview"); text(preview.evidence_id, "evidence ID"); text(preview.title, "evidence title"); if ((preview.evidence_id as string).length < 8 || (preview.evidence_id as string).length > 80 || (preview.title as string).length > 240) throw Error("Invalid evidence preview"); member(preview.source_type, ["market", "news", "filing", "release", "relationship", "model"] as const, "evidence source"); });

  if (item.route_mode !== null) member(item.route_mode, routeModes, "progress route"); optionalText(item.configured_model, "configured model", 160); optionalText(item.model_assertion, "model assertion", 160); if (item.identity_evidence !== null) member(item.identity_evidence, ["direct_provider_verified", "switchyard_target_asserted", "unavailable"] as const, "identity evidence"); if (item.selected_tier !== null) member(item.selected_tier, ["judge", "efficient", "capable"] as const, "selected tier");
  if (item.tokens !== null) validateTokenCounts(item.tokens); if (item.transport_ms !== null) finite(item.transport_ms, "model transport time");
  const attempt = item.attempt === null ? null : validateAttempt(item.attempt), trials = list(item.switchyard_trials, "progress Switchyard trials").map(validateTrial); if (trials.length > 1) throw Error("Invalid progress Switchyard trials");
  if (item.terminal !== null) member(item.terminal, progressTerminals, "progress terminal");

  const toolFields = [item.tool, item.call_sha256, item.outcome, item.reused, item.engine, item.device, item.compute_ms, item.receipt_sha256, item.evidence_count, item.citation_count];
  if (item.kind === "tool") {
    if (item.tool === null || item.call_sha256 === null || item.reused === null || terminalState && item.outcome === null || item.state === "reused" && item.reused !== true || ["completed", "reused"].includes(item.state as string) && item.receipt_sha256 === null) throw Error("Incomplete tool progress span");
  } else if (toolFields.some((entry) => entry !== null) || previews.length) throw Error("Tool detail outside tool progress span");
  if (item.kind === "model") {
    if (item.route_mode === null || ["completed", "reused"].includes(item.state as string) && attempt === null) throw Error("Incomplete model progress span");
    if (attempt && (item.configured_model !== attempt.configured_model || item.model_assertion !== attempt.model_assertion || item.identity_evidence !== attempt.identity_evidence || item.selected_tier !== attempt.selected_tier || !same(item.tokens, attempt.tokens) || item.transport_ms !== attempt.latency_ms || (item.state === "completed") !== (attempt.state === "succeeded") || (item.state === "failed") !== (attempt.state === "failed"))) throw Error("Model progress span does not match attempt");
    const roleNames: Record<ModelAttempt["role"], string> = { skill_selection: "Skill selection", investigation_planning: "Investigation plan", evidence_review: "Evidence review", answer_synthesis: "Answer synthesis", report_formatting: "Answer formatting", agent_reasoning: "Agent reasoning", routing_candidate: "Agent reasoning", routing_judge: "Routing judge" };
    if (attempt && item.display_name !== roleNames[attempt.role]) throw Error("Model progress span does not match attempt role");
  } else if ([item.route_mode, item.configured_model, item.model_assertion, item.identity_evidence, item.selected_tier, item.tokens, item.transport_ms, item.attempt].some((entry) => entry !== null) || trials.length) throw Error("Model detail outside model progress span");
  if (!["planning", "tool"].includes(item.kind as string) && item.plan_id !== null || item.kind !== "planning" && (tools.length || item.planned_call_count !== null) || item.kind !== "report" && item.terminal !== null || item.state === "reused" && item.kind !== "tool") throw Error("Progress detail outside span kind");
  if (!terminalState && (item.outcome !== null || item.engine !== null || item.device !== null || item.compute_ms !== null || item.receipt_sha256 !== null || item.evidence_count !== null || item.citation_count !== null || previews.length || item.model_assertion !== null || item.identity_evidence !== null || item.tokens !== null || item.transport_ms !== null || item.attempt !== null || trials.length || item.kind !== "model" && (item.configured_model !== null || item.selected_tier !== null) || item.kind === "planning" && (item.plan_id !== null || tools.length || item.planned_call_count !== null) || item.terminal !== null)) throw Error("Started progress span contains terminal detail");
  if (item.elapsed_ms !== null) {
    const tolerance = Math.max(100, (item.elapsed_ms as number) * .05);
    // Reuse wall time measures this cache lookup; compute_ms remains the original
    // validated receipt's compute duration and therefore may truthfully be longer.
    if (item.state !== "reused" && item.compute_ms !== null && (item.compute_ms as number) > (item.elapsed_ms as number) + tolerance) throw Error("Tool compute time exceeds span wall time");
    if (item.transport_ms !== null && (item.transport_ms as number) > (item.elapsed_ms as number) + tolerance) throw Error("Model transport time exceeds span wall time");
  }
  return item as unknown as ProgressPayload;
}

export function validateProgressHistory(events: readonly TrajectoryEvent[], status: RunStatus): ProgressPayload[] {
  const opened = new Map<string, ProgressPayload>(), closed = new Map<string, ProgressPayload>(), children = new Map<string, Set<string>>(), decoded: ProgressPayload[] = [];
  for (const event of events) {
    if (event.payload.schema_version !== "progress-span-v1") continue;
    const span = decodeProgressPayload(event.payload); decoded.push(span);
    if (span.state === "started") {
      if (opened.has(span.span_id) || closed.has(span.span_id)) throw Error("Duplicate progress start");
      if (span.parent_span_id !== null) {
        const parent = opened.get(span.parent_span_id); if (!parent) throw Error("Progress parent is not open");
        if (Date.parse(span.started_at) < Date.parse(parent.started_at)) throw Error("Progress child starts before parent");
        const members = children.get(parent.span_id) ?? new Set<string>(); members.add(span.span_id); children.set(parent.span_id, members);
      }
      opened.set(span.span_id, span); continue;
    }
    const start = opened.get(span.span_id);
    if (!start || closed.has(span.span_id)) throw Error("Orphan or duplicate progress completion");
    if (span.parent_span_id !== null && !opened.has(span.parent_span_id)) throw Error("Progress parent closed before child");
    if ([...(children.get(span.span_id) ?? [])].some((child) => opened.has(child))) throw Error("Progress parent completed with an open child");
    if (span.turn_id !== start.turn_id || span.parent_span_id !== start.parent_span_id || span.kind !== start.kind || span.display_name !== start.display_name || span.started_at !== start.started_at || span.tool !== start.tool || span.call_sha256 !== start.call_sha256 || span.kind === "tool" && span.plan_id !== start.plan_id || span.route_mode !== start.route_mode || start.configured_model !== null && span.configured_model !== start.configured_model || start.selected_tier !== null && span.selected_tier !== start.selected_tier) throw Error("Progress completion does not match start");
    for (const childId of children.get(span.span_id) ?? []) {
      const child = closed.get(childId); if (!child || child.completed_at === null || span.completed_at === null || Date.parse(child.completed_at) > Date.parse(span.completed_at)) throw Error("Progress child ends after parent");
      const childEnd = Date.parse(child.started_at) - Date.parse(span.started_at) + (child.elapsed_ms ?? 0), tolerance = Math.max(100, (span.elapsed_ms ?? 0) * .05);
      if (childEnd > (span.elapsed_ms ?? 0) + tolerance) throw Error("Progress child wall time exceeds parent envelope");
    }
    opened.delete(span.span_id); closed.set(span.span_id, span);
  }
  if (status !== "running" && opened.size) throw Error("Non-running investigation has open progress spans");
  return decoded;
}

export function isTerminalEvent(event: TrajectoryEvent): boolean {
  return event.event_type === "report" || event.event_type === "cancelled" || event.event_type === "error" && typeof event.payload.terminal === "string";
}

export function terminalTurn(event: TrajectoryEvent): TerminalTurn | null {
  if (!isTerminalEvent(event)) return null;
  const turn_id = event.payload.turn_id, terminal = event.payload.terminal;
  if (typeof turn_id !== "string" || !/^turn-\d{4}$/.test(turn_id) || typeof terminal !== "string" || !event.payload.security_receipt) throw Error("Incomplete terminal turn");
  member(terminal, ["help", "refusal", "clarification", "no_data", "partial", "success", "cancelled", ...failureStages] as const, "terminal outcome");
  const security_receipt = validateSecurity(event.payload.security_receipt);
  if (security_receipt.turn_id !== turn_id) throw Error("Terminal turn receipt mismatch");
  return {
    turn_id,
    terminal,
    report: event.payload.report === undefined ? null : validateReport(event.payload.report),
    security_receipt,
    event,
  };
}

function validateTurnModelSecurity(attempts: ModelAttempt[], trials: SwitchyardTrialBundle[], receipt: SecurityReceipt): void {
  const modelObservations = receipt.network_egress.filter((observation) => observation.boundary !== "mcp");
  if (!attempts.length) {
    if (modelObservations.length) throw Error("Orphan model security observation");
    return;
  }
  if (modelObservations.length !== attempts.length || new Set(trials.map((trial) => trial.trial_id)).size !== trials.length || trials.some((trial) => !attempts.some((attempt) => attempt.switchyard_trial_id === trial.trial_id))) throw Error("Model security correlation mismatch");
  const used = new Set<number>();
  for (const attempt of attempts) {
    const routed = attempt.algorithm === "switchyard_capability", expectedBoundary = routed ? "switchyard" : attempt.destination_class === "local_model" ? "local_model" : "frontier_model";
    let matches = modelObservations.map((observation, index) => ({ observation, index })).filter(({ observation, index }) => !used.has(index) && observation.call_id === attempt.application_call_id);
    if (!matches.length && attempt.state === "failed") matches = modelObservations.map((observation, index) => ({ observation, index })).filter(({ observation, index }) => !used.has(index) && observation.boundary === expectedBoundary && observation.attempt === "not_attempted");
    if (matches.length !== 1 || matches[0].observation.boundary !== expectedBoundary || attempt.state === "attempted") throw Error("Model security correlation mismatch");
    used.add(matches[0].index); const observation = matches[0].observation;
    if (observation.attempt === "not_attempted") {
      if (attempt.state !== "failed" || observation.call_id !== null || observation.outcome !== "blocked" || observation.application_request_id !== null || observation.switchyard_trial_id !== null || observation.switchyard_trial_bundle !== null || observation.identity_evidence !== null) throw Error("Model security correlation mismatch");
      continue;
    }
    const expectedIdentity = attempt.identity_evidence === "unavailable" ? null : attempt.identity_evidence, validatedProviderResponse = attempt.validation_status === "invalid" && attempt.identity_evidence !== "unavailable", expectedOutcome = attempt.state === "succeeded" || validatedProviderResponse ? "succeeded" : "failed", expectedTrial = routed ? trials.find((trial) => trial.trial_id === attempt.switchyard_trial_id) ?? null : null;
    if (observation.attempt !== "attempted" || observation.outcome !== expectedOutcome || observation.call_id !== attempt.application_call_id || observation.identity_evidence !== expectedIdentity || observation.application_request_id !== attempt.application_request_id || observation.switchyard_trial_id !== attempt.switchyard_trial_id || !same(observation.switchyard_trial_bundle, expectedTrial)) throw Error("Model security correlation mismatch");
  }
}

export function decodeTrajectoryEvent(value: unknown): TrajectoryEvent {
  const item = object(value, "trajectory event"); exact(item, eventKeys, "trajectory event"); finite(item.sequence, "event sequence", true); if ((item.sequence as number) < 1) throw Error("Invalid event sequence"); member(item.event_type, eventTypes, "event type"); text(item.label, "event label"); if (typeof item.detail !== "string") throw Error("Invalid event detail"); text(item.occurred_at, "event timestamp"); text(item.tool, "event tool", true);
  const payload = object(item.payload, "event payload");
  if (payload.schema_version === "progress-span-v1") {
    const span = decodeProgressPayload(payload), expected = span.kind === "tool" ? span.state === "started" ? "tool_started" : "tool_completed" : span.kind === "model" && span.state !== "started" ? "routing" : "planning";
    rejectUnsafeProgress([item.label, item.detail]); const occurred = timestamp(item.occurred_at, "progress event timestamp"), boundary = Date.parse(span.completed_at ?? span.started_at);
    const phrase = span.state === "started" ? "started" : span.state === "reused" ? "reused" : span.state === "cancelled" ? "cancelled" : span.state === "failed" ? "failed" : "completed";
    const expectedLabel = span.display_name === "Investigation" && span.state === "started" ? "Investigation started" : span.display_name;
    if (item.event_type !== expected || item.tool !== span.tool || item.label !== expectedLabel || item.detail !== `${span.display_name} ${phrase}.` || occurred < boundary || occurred - boundary > 100) throw Error("Progress event projection mismatch");
    return { ...item, payload: span } as unknown as TrajectoryEvent;
  }
  const attempt = payload.attempt === undefined ? null : validateAttempt(payload.attempt), trials = payload.switchyard_trials === undefined ? null : list(payload.switchyard_trials, "event trials").map(validateTrial), receipt = payload.security_receipt === undefined ? null : validateSecurity(payload.security_receipt);
  if (attempt && trials) { const routed = attempt.algorithm === "switchyard_capability"; if (!routed && trials.length || trials.length > 1 || trials.length === 1 && trials[0].trial_id !== attempt.switchyard_trial_id) throw Error("Event trial correlation mismatch"); }
  if (!attempt && trials) throw Error("Event trials lack a model attempt"); if (payload.report !== undefined) validateReport(payload.report); if (payload.terminal !== undefined) member(payload.terminal, ["help", "refusal", "clarification", "no_data", "partial", "success", "cancelled", ...failureStages] as const, "terminal outcome");
  const terminalEvent = isTerminalEvent(item as unknown as TrajectoryEvent);
  if (terminalEvent && (!receipt || typeof payload.turn_id !== "string" || !/^turn-\d{4}$/.test(payload.turn_id) || receipt.turn_id !== payload.turn_id)) throw Error("Terminal security receipt is missing or mismatched");
  const readable = ["help", "refusal", "clarification", "no_data", "partial", "success"];
  if (item.event_type === "routing" && (!attempt || trials === null) || item.event_type === "report" && (payload.report === undefined || !readable.includes(String(payload.terminal))) || item.event_type === "cancelled" && (payload.terminal !== "cancelled" || payload.report !== undefined) || item.event_type === "error" && terminalEvent && (!failureStages.includes(payload.terminal as FailureStage) || payload.report !== undefined) || item.event_type !== "report" && payload.report !== undefined || !terminalEvent && receipt !== null) throw Error("Incomplete event contract");
  return item as unknown as TrajectoryEvent;
}

export function parseFailure(error: string | null): TypedFailure | null {
  if (error === null) return null; const separator = error.indexOf(":"); if (separator < 1) throw Error("Invalid typed failure"); const stage = error.slice(0, separator), code = error.slice(separator + 1); member(stage, failureStages, "failure stage"); if (!stableCode.test(code)) throw Error("Invalid failure code"); return { stage, code };
}

export function decodeInvestigation(value: unknown): Investigation {
  const item = object(value, "investigation response"); exact(item, recordKeys, "investigation"); text(item.investigation_id, "investigation ID"); text(item.created_at, "created timestamp"); text(item.updated_at, "updated timestamp"); member(item.status, statuses, "run status"); validateRequest(item.request);
  const turns = list(item.turns, "turns"); if (turns.some((entry) => typeof entry !== "string")) throw Error("Invalid turns"); if (item.scope !== null) validateScope(item.scope);
  if (item.active_skill !== null) member(item.active_skill, ["market-dislocation", "peer-comparison", "historical-analogues", "shock-propagation", "volatility-risk", "narrative-map", "market-research-guide"] as const, "active skill");
  const events = list(item.events, "events").map(decodeTrajectoryEvent); if (events.some((event, index) => event.sequence !== index + 1)) throw Error("Non-monotonic trajectory"); validateProgressHistory(events, item.status as RunStatus);
  const receipts = list(item.security_receipts, "security receipts").map(validateSecurity); if (receipts.some((receipt) => receipt.investigation_id !== item.investigation_id) || new Set(receipts.map((receipt) => receipt.turn_id)).size !== receipts.length) throw Error("Security receipt correlation mismatch");
  const terminalTurns = events.flatMap((event) => { const turn = terminalTurn(event); return turn ? [turn] : []; });
  if (terminalTurns.length !== receipts.length || terminalTurns.some((turn, index) => turn.turn_id !== `turn-${String(index + 1).padStart(4, "0")}` || !same(turn.security_receipt, receipts[index]))) throw Error("Historical security receipt correlation mismatch");
  if (terminalTurns.length > turns.length || terminal(item.status as RunStatus) && terminalTurns.length !== turns.length) throw Error("Terminal turn history mismatch");
  for (const turn of terminalTurns) {
    const securityTerminal = turn.security_receipt.completeness === "verified" ? null : `security_${turn.security_receipt.completeness}`;
    if (securityTerminal === null ? ["security_unknown", "security_violation"].includes(turn.terminal) : turn.terminal !== securityTerminal) throw Error("Terminal security state mismatch");
    if (turn.report && ((turn.terminal === "no_data") !== Boolean(turn.report.no_data_reasons.length))) throw Error("Terminal no-data reason mismatch");
  }
  let turnAttempts: ModelAttempt[] = [], turnTrials: SwitchyardTrialBundle[] = [], terminalIndex = 0;
  for (const event of events) {
    if (event.event_type === "routing") { const attempt = event.payload.attempt as unknown as ModelAttempt; if (turnAttempts.some((item) => item.application_call_id === attempt.application_call_id)) throw Error("Duplicate model attempt in turn"); turnAttempts.push(attempt); turnTrials.push(...event.payload.switchyard_trials as SwitchyardTrialBundle[]); }
    if (event.event_type === "report") { const eventReport = event.payload.report as unknown as Report; if (!same(eventReport.model_attempts, turnAttempts) || !same(eventReport.switchyard_trials, turnTrials)) throw Error("Historical model attempt correlation mismatch"); }
    if (isTerminalEvent(event)) { logicalAttempts(turnAttempts); validateTurnModelSecurity(turnAttempts, turnTrials, receipts[terminalIndex]); turnAttempts = []; turnTrials = []; terminalIndex++; }
  }
  const decodedReport = item.report === null ? null : validateReport(item.report); text(item.error, "investigation error", true); const failure = parseFailure(item.error as string | null);
  const decodedScope = item.scope as Scope | null, request = item.request as unknown as InvestigationRequest;
  if (decodedReport && (decodedScope && !same(decodedScope, decodedReport.scope) || decodedReport.routing.requested_mode !== request.route_mode)) throw Error("Report correlation mismatch");
  if ((item.status === "failed") !== (failure !== null) || item.status !== "failed" && item.error !== null) throw Error("Inconsistent typed failure");
  const isTerminal = terminal(item.status as RunStatus); if (["completed", "needs_input", "refused"].includes(item.status as string) && decodedReport === null) throw Error("Clean terminal lacks a report");
  if (isTerminal) {
    const last = receipts.at(-1), terminalEvent = events.at(-1), eventReceipt = terminalEvent?.payload.security_receipt;
    if (!last || !eventReceipt || !same(last, eventReceipt)) throw Error("Terminal security receipt is missing");
    if (["completed", "needs_input", "refused"].includes(item.status as string) && last.completeness !== "verified") throw Error("Unverified security cannot be a clean terminal");
    const terminalOutcome = terminalEvent?.payload.terminal;
    if (item.status === "completed" && (terminalEvent?.event_type !== "report" || !["help", "no_data", "partial", "success"].includes(terminalOutcome as string)) || item.status === "needs_input" && (terminalEvent?.event_type !== "report" || terminalOutcome !== "clarification") || item.status === "refused" && (terminalEvent?.event_type !== "report" || terminalOutcome !== "refusal") || item.status === "failed" && (terminalEvent?.event_type !== "error" || terminalOutcome !== failure?.stage || terminalEvent.detail !== item.error) || item.status === "cancelled" && (terminalEvent?.event_type !== "cancelled" || terminalOutcome !== "cancelled")) throw Error("Terminal status mapping mismatch");
    if (decodedReport && terminalEvent?.event_type === "report" && !same(decodedReport, terminalEvent.payload.report)) throw Error("Terminal report correlation mismatch");
  }
  return item as unknown as Investigation;
}

export const terminal = (status: RunStatus) => ["completed", "needs_input", "refused", "failed", "cancelled"].includes(status);
