// Mirrors services/agent/src/market_agent/schemas.py, the /api/status payload in app.py,
// EventCatalog.public() in catalog.py, and build_dashboard() in the tools service.
// The server owns every invariant; these are plain shapes, not validators.

// Status -----------------------------------------------------------------------

export interface Company { symbol: string; name: string }
export interface Coverage { scenario_id: string; first_session: string; last_session: string }
export interface StatusModel { id: string; role: string; where: "local" | "remote" }

export interface SystemStatus {
  ready: boolean;
  reason: string | null;
  remote_routing_enabled: boolean;
  dependencies: Record<string, boolean>;
  companies: Company[];
  coverage: Coverage;
  models: StatusModel[];
  max_turns: number;
  langsmith_project_url: string | null;
}

// Curated shock events --------------------------------------------------------

export interface EventCategory { category_id: string; label: string; description: string; sort_order: number }
export interface EventQuestion { question_id: string; label: string; capability: string; text: string }

export interface ShockEvent {
  event_id: string;
  category_id: string;
  title: string;
  summary: string;
  sort_order: number;
  event_session: string;
  source_dates: string[];
  primary_ticker: string;
  analysis_tickers: string[];
  context_instruments: string[];
  start_session: string;
  end_session: string;
  default_cutoff: string;
  questions: EventQuestion[];
  limitations: { limitation_id: string; detail: string }[];
  status: "ready" | "partial";
}

export interface ShockEventCatalog { categories: EventCategory[]; events: ShockEvent[] }

// Dashboard ---------------------------------------------------------------------

export interface WatchRow {
  ticker: string;
  available: boolean;
  close: number | null;
  return_1d_pct: number | null;
  return_5d_pct: number | null;
  opening_gap_pct: number | null;
  volume_ratio: number | null;
  benchmark: string;
  benchmark_return_1d_pct: number | null;
  benchmark_relative_return_pp: number | null;
  is_shock: boolean | null;
}

export interface SeriesPoint {
  session_date: string;
  ticker_close: number;
  benchmark_close: number;
  ticker_return_pct: number;
  benchmark_return_pct: number;
  volume: number;
}

export interface DashboardDocument {
  title: string;
  url: string | null;
  source_type: string;
  content_scope: string;
  available_at: string;
  excerpt: string;
}

export interface Receipt { engine: string; device: string; duration_ms: number }

export interface Dashboard {
  requested_as_of: string;
  resolved_session: string;
  cutoff_at: string;
  selected_ticker: string;
  coverage: Coverage;
  watchlist: WatchRow[];
  series: { ticker: string; benchmark: string; points: SeriesPoint[] };
  documents: DashboardDocument[];
  receipt: Receipt;
  limitations: string[];
}

// Investigations ------------------------------------------------------------------

export interface CreateInvestigation {
  question: string;
  ticker?: string;
  as_of?: string;
  event_id?: string;
}

export interface Scope {
  status: "resolved" | "needs_input";
  ticker: string | null;
  members: string[];
  as_of: string | null;
  session: string | null;
  event_id: string | null;
  missing: ("ticker" | "date")[];
  note: string | null;
}

export interface Citation {
  citation_id: string;
  title: string;
  url: string | null;
  source_type: "market" | "news" | "filing" | "release" | "relationship" | "model";
  published_at: string;
  available_at: string;
  excerpt: string;
}

export interface ToolSummary {
  tool: string;
  ticker: string;
  outcome: "ok" | "partial" | "no_data" | "failed";
  summary: string;
  receipt: Receipt | null;
  limitations: string[];
}

export interface Artifact {
  artifact_id: string;
  kind: "analogue_table" | "comovement_graph" | "topic_projection";
  title: string;
  data: Record<string, unknown>;
}

export interface Report {
  kind: "research" | "guide";
  answer: string;
  citations: Citation[];
  uncertainty: string[];
  suggested_questions: string[];
  limitations: string[];
  tools: ToolSummary[];
  artifacts: Artifact[];
}

export interface ModelCall {
  role: "judge" | "agent";
  model: string;
  tier: "judge" | "efficient" | "capable";
  state: "succeeded" | "failed";
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  failure: string | null;
}

export type SpanState = "running" | "succeeded" | "failed" | "cancelled";

export interface Span {
  span_id: string;
  parent_id: string | null;
  kind: "turn" | "model" | "tool" | "skill";
  name: string;
  state: SpanState;
  started_at: string;
  ended_at: string | null;
  detail: Record<string, unknown>;
}

export interface ProgressEvent { sequence: number; turn: number; at: string; span: Span }

export type TurnStatus = "running" | "completed" | "failed" | "cancelled";

export interface Turn {
  number: number;
  question: string;
  status: TurnStatus;
  started_at: string;
  ended_at: string | null;
  skill: string | null;
  report: Report | null;
  error: { code: string; message: string } | null;
  model_calls: ModelCall[];
}

export interface Investigation {
  investigation_id: string;
  created_at: string;
  updated_at: string;
  status: TurnStatus;
  scope: Scope;
  turns: Turn[];
  events: ProgressEvent[];
}
