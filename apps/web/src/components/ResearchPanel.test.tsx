/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LOCAL_MODEL,
  LUNA_MODEL,
  decodeInvestigation,
  decodeProgressPayload,
  decodeTrajectoryEvent,
  validateProgressHistory,
  type Investigation,
  type ProgressPayload,
  type Report,
  type TrajectoryEvent,
} from "../api/types";
import { formatDuration, projectActiveGantt, projectProgressSpans, ResearchPanel } from "./ResearchPanel";
import { Trajectory } from "./Trajectory";

const AT = Date.parse("2025-01-27T17:00:00.000Z");
const iso = (offset: number) => new Date(AT + offset).toISOString();

const basePayload = (overrides: Record<string, unknown> = {}): Record<string, unknown> => ({
  schema_version: "progress-span-v1",
  turn_id: "turn-0001",
  span_id: "span-1111111111111111",
  parent_span_id: null,
  kind: "report",
  display_name: "Investigation",
  state: "started",
  started_at: iso(0),
  completed_at: null,
  elapsed_ms: null,
  plan_id: null,
  tools: [],
  planned_call_count: null,
  tool: null,
  call_sha256: null,
  outcome: null,
  reused: null,
  engine: null,
  device: null,
  compute_ms: null,
  receipt_sha256: null,
  evidence_count: null,
  citation_count: null,
  evidence_preview: [],
  route_mode: null,
  configured_model: null,
  model_assertion: null,
  identity_evidence: null,
  selected_tier: null,
  tokens: null,
  transport_ms: null,
  attempt: null,
  switchyard_trials: [],
  terminal: null,
  ...overrides,
});

const toolPayload = (span: string, started: number, overrides: Record<string, unknown> = {}) => basePayload({
  span_id: span,
  parent_span_id: "span-1111111111111111",
  kind: "tool",
  display_name: "Price context",
  started_at: iso(started),
  plan_id: "plan-00000001",
  tool: "get_price_context",
  call_sha256: span.slice(5).padEnd(64, "a"),
  reused: false,
  ...overrides,
});

const completed = (payload: Record<string, unknown>, elapsed: number, overrides: Record<string, unknown> = {}) => ({
  ...payload,
  state: "completed",
  completed_at: iso(Date.parse(String(payload.started_at)) - AT + elapsed),
  elapsed_ms: elapsed,
  ...(payload.kind === "tool" ? {
    outcome: "ok",
    engine: "cudf",
    device: "cuda:0",
    compute_ms: Math.max(1, elapsed / 2),
    receipt_sha256: "c".repeat(64),
    evidence_count: 1,
    citation_count: 1,
    evidence_preview: [{ evidence_id: `evidence-${String(payload.span_id).slice(-8)}`, title: "NVDA daily bar", source_type: "market" }],
  } : {}),
  ...overrides,
});

const event = (sequence: number, payload: Record<string, unknown>): TrajectoryEvent => decodeTrajectoryEvent({
  sequence,
  event_type: payload.kind === "tool" ? payload.state === "started" ? "tool_started" : "tool_completed" : payload.kind === "model" && payload.state !== "started" ? "routing" : "planning",
  label: payload.display_name === "Investigation" && payload.state === "started" ? "Investigation started" : String(payload.display_name),
  detail: `${String(payload.display_name)} ${String(payload.state)}.`,
  occurred_at: String(payload.completed_at ?? payload.started_at),
  tool: payload.tool,
  payload,
});

describe("safe progress-span decoding", () => {
  it("accepts the exact closed contract and rejects unknown, secret-shaped, or impossible detail", () => {
    expect(decodeProgressPayload(basePayload()).kind).toBe("report");
    expect(() => decodeProgressPayload({ ...basePayload(), raw_prompt: "hidden reasoning" })).toThrow(/fields|progress/i);
    expect(() => decodeProgressPayload(basePayload({ kind: "reasoning" }))).toThrow(/kind/i);
    expect(() => decodeProgressPayload(toolPayload("span-2222222222222222", 10, { device: "https://internal.invalid/v1" }))).toThrow(/unsafe|sensitive/i);
    expect(() => decodeProgressPayload(completed(basePayload(), -1))).toThrow(/elapsed|timing/i);
    expect(() => decodeProgressPayload(completed(basePayload(), 10, { completed_at: iso(-1) }))).toThrow(/precedes|timing/i);
    expect(() => decodeProgressPayload(completed(basePayload(), 10, { completed_at: iso(500) }))).toThrow(/reconcile/i);
    const tool = toolPayload("span-2222222222222222", 10);
    expect(() => decodeProgressPayload(completed(tool, 10, { compute_ms: 250 }))).toThrow(/compute.*wall/i);
  });

  it("accepts actual tool, model, and planning start shapes while keeping terminal-only detail off starts", () => {
    const tool = toolPayload("span-2222222222222222", 10);
    expect(decodeProgressPayload(tool).state).toBe("started");
    const model = basePayload({ span_id: "span-3333333333333333", parent_span_id: "span-1111111111111111", kind: "model", display_name: "Answer synthesis", route_mode: "local_only" });
    expect(decodeProgressPayload(model).route_mode).toBe("local_only");
    const planning = basePayload({ span_id: "span-4444444444444444", parent_span_id: "span-1111111111111111", kind: "planning", display_name: "Evidence plan" });
    const planned = completed(planning, 10, { plan_id: "plan-00000001", tools: ["get_price_context"], planned_call_count: 1 });
    expect(validateProgressHistory([event(1, basePayload()), event(2, planning), event(3, planned)], "running").at(-1)?.plan_id).toBe("plan-00000001");
    for (const display_name of ["Skill load", "Subagent analysis"] as const) expect(decodeProgressPayload(basePayload({ kind: "planning", display_name })).display_name).toBe(display_name);
    expect(() => decodeProgressPayload({ ...tool, compute_ms: 1 })).toThrow(/started.*terminal/i);
  });

  it("accepts an in-process escalation span with an actual target model and selected tier", () => {
    const start = basePayload({ span_id: "span-3333333333333333", parent_span_id: "span-1111111111111111", kind: "model", display_name: "Routing judge", route_mode: "switchyard_escalation", configured_model: LUNA_MODEL, selected_tier: "judge" });
    const attempt = { role: "routing_judge", algorithm: "switchyard_escalation", destination_class: "internal_inference", configured_model: LUNA_MODEL, model_assertion: LUNA_MODEL, identity_evidence: "direct_provider_verified", state: "succeeded", failure_class: null, application_call_id: "call-00000001", application_request_id: "request-00000001", latency_ms: 20, tokens: { prompt: 10, completion: 5, total: 15 }, validation_status: "valid", switchyard_trial_id: null, selected_tier: "judge" };
    const done = completed(start, 30, { model_assertion: LUNA_MODEL, identity_evidence: "direct_provider_verified", tokens: attempt.tokens, transport_ms: 20, attempt });
    const decoded = decodeProgressPayload(done);
    expect(decoded.selected_tier).toBe("judge");
    expect(decoded.configured_model).toBe(LUNA_MODEL);
    expect(decoded.switchyard_trials).toEqual([]);
  });

  it("accepts an actual reused tool payload whose original receipt compute exceeds reuse wall time", () => {
    const start = toolPayload("span-2222222222222222", 10, { reused: true });
    const reused = completed(start, 2, { state: "reused", compute_ms: 420 });
    const decoded = decodeProgressPayload(reused);
    expect(decoded.state).toBe("reused");
    expect(decoded.elapsed_ms).toBe(2);
    expect(decoded.compute_ms).toBe(420);
  });

  it("rejects transport time beyond the model wall span and event timestamps detached from their boundary", () => {
    const model = basePayload({ span_id: "span-3333333333333333", parent_span_id: "span-1111111111111111", kind: "model", display_name: "Answer synthesis", route_mode: "local_only" });
    const attempt = { role: "answer_synthesis", algorithm: "direct_local", destination_class: "local_model", configured_model: LOCAL_MODEL, model_assertion: LOCAL_MODEL, identity_evidence: "direct_provider_verified", state: "succeeded", failure_class: null, application_call_id: "call-00000001", application_request_id: "request-00000001", latency_ms: 250, tokens: { prompt: 10, completion: 5, total: 15 }, validation_status: "valid", switchyard_trial_id: null, selected_tier: null };
    expect(() => decodeProgressPayload(completed(model, 10, { configured_model: LOCAL_MODEL, model_assertion: LOCAL_MODEL, identity_evidence: "direct_provider_verified", tokens: attempt.tokens, transport_ms: 250, attempt }))).toThrow(/transport.*wall/i);
    const root = basePayload();
    expect(() => decodeTrajectoryEvent({ sequence: 1, event_type: "planning", label: "Investigation started", detail: "Investigation started.", occurred_at: iso(500), tool: null, payload: root })).toThrow(/projection/i);
  });

  it("rejects orphans and duplicates, allows an open span only while running, and closes clean terminals", () => {
    const root = basePayload(), rootDone = completed(root, 100, { terminal: "success" });
    expect(() => validateProgressHistory([event(1, rootDone)], "completed")).toThrow(/orphan/i);
    expect(() => validateProgressHistory([event(1, root), event(2, root)], "running")).toThrow(/duplicate/i);
    expect(validateProgressHistory([event(1, root)], "running")).toHaveLength(1);
    expect(() => validateProgressHistory([event(1, root)], "completed")).toThrow(/open/i);
    expect(validateProgressHistory([event(1, root), event(2, rootDone)], "completed")).toHaveLength(2);
  });

  it("requires a completion to preserve its started identity", () => {
    const root = basePayload();
    expect(() => validateProgressHistory([
      event(1, root),
      event(2, completed(root, 10, { display_name: "Report assembly" })),
    ], "completed")).toThrow(/match/i);
  });

  it("rejects a parent completion while a child is open or whose claimed end exceeds the parent envelope", () => {
    const root = basePayload(), tool = toolPayload("span-2222222222222222", 10);
    expect(() => validateProgressHistory([event(1, root), event(2, tool), event(3, completed(root, 50, { terminal: "success" }))], "running")).toThrow(/open child/i);
    expect(() => validateProgressHistory([
      event(1, root), event(2, tool), event(3, completed(tool, 80)), event(4, completed(root, 50, { terminal: "success" })),
    ], "completed")).toThrow(/parent|envelope/i);
  });

  it("integrates paired progress into running and terminal investigation decoding", () => {
    const root = basePayload(), cancelled = completed(root, 50, { state: "cancelled", terminal: "cancelled" });
    const common = { investigation_id: "11111111-1111-4111-8111-111111111111", created_at: iso(0), updated_at: iso(50), request: { question: "What happened?", ticker: "NVDA", as_of: "2025-01-27", route_mode: "local_only" }, turns: ["What happened?"], scope: null, active_skill: null, report: null, error: null };
    expect(decodeInvestigation({ ...common, status: "running", events: [event(1, root)], security_receipts: [] }).status).toBe("running");
    const security = { schema_version: "security-receipt-v1", investigation_id: common.investigation_id, turn_id: "turn-0001", completeness: "verified", network_egress: [], external_actions: [{ schema_version: "security-observation-v1", observation_id: "action-00000001", action_class: "none", request_state: "not_requested", decision: "allowed", attempt: "not_attempted", outcome: "succeeded", limitation: null }], trust_boundary: "verified", violations: [], unknown_reasons: [], secret_scan: { schema_version: "security-observation-v1", status: "pass", checked_variables: [], matched_variables: [], limitation: null }, finalized_at: iso(50) };
    const terminal = { sequence: 3, event_type: "cancelled", label: "Investigation cancelled", detail: "No further tool calls will be started.", occurred_at: iso(50), tool: null, payload: { turn_id: "turn-0001", terminal: "cancelled", security_receipt: security } };
    expect(decodeInvestigation({ ...common, status: "cancelled", events: [event(1, root), event(2, cancelled), terminal], security_receipts: [security] }).status).toBe("cancelled");
  });
});

describe("pure progress projection", () => {
  it("uses the span envelope for total wall time while retaining overlapping child durations", () => {
    const root = basePayload();
    const first = toolPayload("span-2222222222222222", 10);
    const second = toolPayload("span-3333333333333333", 20, { display_name: "Market shock", tool: "detect_market_shock", call_sha256: "b".repeat(64) });
    const events = [event(1, root), event(2, first), event(3, second), event(4, completed(second, 70)), event(5, completed(first, 80)), event(6, completed(root, 100, { terminal: "success" }))];
    const projection = projectProgressSpans(events, "completed", AT + 1_000);
    const turn = projection.turns[0];
    expect(turn.envelopeMs).toBe(100);
    expect(turn.spans.filter((span) => span.kind === "tool").reduce((sum, span) => sum + span.wallMs, 0)).toBe(150);
    expect(turn.spans.filter((span) => span.kind === "tool").map((span) => span.overlapGroup)).toEqual([1, 1]);
    expect(turn.spans.find((span) => span.spanId === "span-2222222222222222")?.computeMs).toBe(40);
  });

  it("ticks an open span from the supplied clock and stops at server completion", () => {
    const root = basePayload(), tool = toolPayload("span-2222222222222222", 10);
    const live = projectProgressSpans([event(1, root), event(2, tool)], "running", AT + 90);
    expect(live.turns[0].spans.find((span) => span.kind === "tool")?.wallMs).toBe(80);
    const done = projectProgressSpans([
      event(1, root), event(2, tool), event(3, completed(tool, 50)), event(4, completed(root, 70, { terminal: "success" })),
    ], "completed", AT + 9_000);
    expect(done.turns[0].spans.find((span) => span.kind === "tool")?.wallMs).toBe(50);
  });

  it("keeps sequential work in distinct overlap groups and formats durations consistently", () => {
    const root = basePayload();
    const first = toolPayload("span-2222222222222222", 5);
    const second = toolPayload("span-3333333333333333", 35, { display_name: "Market shock", tool: "detect_market_shock", call_sha256: "b".repeat(64) });
    const projection = projectProgressSpans([
      event(1, root), event(2, first), event(3, completed(first, 20)), event(4, second), event(5, completed(second, 20)), event(6, completed(root, 60, { terminal: "success" })),
    ], "completed", AT + 60);
    expect(projection.turns[0].spans.filter((span) => span.kind === "tool").map((span) => span.overlapGroup)).toEqual([1, 2]);
    expect(formatDuration(.5)).toBe("<1 ms");
    expect(formatDuration(125)).toBe("125 ms");
    expect(formatDuration(1_250)).toBe("1.25 s");
  });

  it("concatenates turn envelopes on one active-runtime axis without including user idle time", () => {
    const root = basePayload(), tool = toolPayload("span-2222222222222222", 10);
    const first = projectProgressSpans([
      event(1, root), event(2, tool), event(3, completed(tool, 80)), event(4, completed(root, 100, { terminal: "success" })),
    ], "completed", AT + 100).turns[0];
    const second = { ...first, turnId: "turn-0002", envelopeMs: 50, spans: first.spans.map((span) => ({ ...span, turnId: "turn-0002", wallMs: span.parentSpanId === null ? 50 : 20 })) };
    const gantt = projectActiveGantt({ turns: [first, second] });
    expect(gantt.activeRuntimeMs).toBe(150);
    expect(gantt.turns[1].activeStartOffsetMs).toBe(100);
    expect(gantt.turns[1].leftPercent).toBeCloseTo(66.67, 1);
    expect(gantt.turns[1].spans[0].activeStartOffsetMs).toBe(100);
  });
});

// Compile-time assurance that the fixture is the public decoded type, not an unchecked UI shape.
const _typedFixture: ProgressPayload = decodeProgressPayload(basePayload());
void _typedFixture;

const report = (): Report => ({
  schema_version: "1.0",
  title: "NVDA evidence",
  summary: "A bounded answer.",
  scope: { status: "supported", action: "answer", ticker: "NVDA", as_of: iso(0), market_as_of: iso(0), timezone: "America/New_York", supported_universe: ["NVDA"], explanation: "Bounded.", resolved_tickers: ["NVDA"], group_key: null },
  no_data_reasons: [],
  claims: [{ claim_id: "claim-00000001", text: "NVDA moved.", kind: "fact", confidence: .9, citation_ids: ["citation-00000001"] }],
  citations: [{ citation_id: "citation-00000001", evidence_id: "evidence-22222222", title: "NVDA daily bar", url: "https://example.test/nvda", source_type: "market", published_at: iso(0), available_at: iso(0), excerpt: "NVDA OHLC evidence.", content_sha256: "d".repeat(64), hindsight: false }],
  uncertainty: [],
  receipts: [{ tool: "get_price_context", engine: "cudf", device: "cuda:0", gpu_executed: true, fallback_used: false, duration_ms: 40, artifact_manifest_sha256: "e".repeat(64), scenario_id: "scenario", market_manifest_sha256: "f".repeat(64), document_manifest_sha256: null, market_readiness_sha256: "a".repeat(64), document_readiness_sha256: null }],
  routing: { requested_mode: "local_only", effective_mode: "local_only", configured_model: "deterministic", returned_model: null, reason: "deterministic", remote_attempted: false, fallback_used: false, frontier_latched: false, latency_ms: 0 } as Report["routing"],
  answer_mode: "deterministic_evidence",
  model_attempts: [], switchyard_trials: [], artifacts: [], generated_at: iso(100),
});

const investigation = (events: TrajectoryEvent[], status: Investigation["status"] = "completed"): Investigation => ({
  investigation_id: "11111111-1111-4111-8111-111111111111",
  created_at: iso(0), updated_at: iso(100), status,
  request: { question: "RAW PROMPT CANARY", ticker: "NVDA", as_of: "2025-01-27", route_mode: "local_only" },
  turns: ["RAW PROMPT CANARY"], scope: report().scope, active_skill: "market-dislocation", events: [...events], security_receipts: [], report: status === "completed" ? report() : null, error: null,
});

const closedEvents = () => {
  const root = basePayload(), tool = toolPayload("span-2222222222222222", 10);
  return [event(1, root), event(2, tool), event(3, completed(tool, 80)), event(4, completed(root, 100, { terminal: "success" }))];
};

const click = (element: Element) => act(async () => element.dispatchEvent(new MouseEvent("click", { bubbles: true })));
const hover = (element: Element) => act(async () => element.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));

describe("research workspace", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  });

  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  async function render(status: Investigation["status"] = "completed", events = closedEvents()) {
    await act(async () => root.render(<ResearchPanel record={investigation(events, status)} events={events} busy={false} onCancel={() => undefined} onRetry={() => undefined} />));
  }

  it("exposes three keyboard tabs with distinct Activity, Evidence, and Performance content", async () => {
    await render();
    const tabs = [...container.querySelectorAll<HTMLButtonElement>("[role='tab']")];
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Activity", "Evidence", "Performance"]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector("[aria-label='Investigation trajectory']")).not.toBeNull();
    expect(container.querySelector("[data-event-sequence='1']")?.getAttribute("data-turn-id")).toBe("turn-0001");
    await click(tabs[1]);
    expect(container.querySelector("[role='tabpanel']:not([hidden])")?.textContent).toContain("Final-cited");
    expect(container.querySelector("[data-evidence-id='evidence-22222222'][data-evidence-state='final-cited']")).not.toBeNull();
    expect(container.querySelector("[data-citation-id='citation-00000001']")).not.toBeNull();
    await act(async () => tabs[1].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(tabs[2].getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector("[role='tabpanel']:not([hidden])")?.textContent).toContain("Execution Gantt");
  });

  it("renders one shared-axis Gantt and exposes safe receipt detail on hover", async () => {
    await render();
    await click(container.querySelector("[data-research-tab='performance']")!);
    expect(container.querySelectorAll(".performance-gantt")).toHaveLength(1);
    expect(container.querySelector("[data-active-runtime-ms='100']")).not.toBeNull();
    expect(container.querySelector(".gantt-axis")?.textContent).toContain("50 ms");
    expect(container.querySelector("[data-turn-envelope-ms='100']")?.textContent).toContain("100 ms");
    expect(container.querySelector("[data-time-to-first-research-step-ms='10']")?.textContent).toContain("First research step 10 ms");
    const tool = container.querySelector<HTMLElement>("[data-span-kind='tool']")!;
    expect(tool.dataset.spanDurationMs).toBe("80");
    expect(tool.dataset.spanReused).toBe("false");
    expect(tool.dataset.parentSpanId).toBe("span-1111111111111111");
    expect(tool.dataset.spanStartedAt).toBe(iso(10));
    expect(tool.dataset.spanCompletedAt).toBe(iso(90));
    expect(tool.dataset.spanStartOffsetMs).toBe("10");
    expect(tool.dataset.spanActiveStartMs).toBe("10");
    expect(tool.closest("[data-overlap-group]")?.getAttribute("data-overlap-group")).toBe("1");
    expect(container.querySelector<HTMLElement>("[data-span-id='span-1111111111111111']")?.dataset.spanReused).toBe("");
    expect(tool.getAttribute("aria-expanded")).toBeNull();
    expect(container.querySelector("[data-span-detail]")).toBeNull();
    await hover(tool);
    const detail = container.querySelector("[data-span-detail='span-2222222222222222']")?.textContent;
    expect(detail).toContain("Server wall time"); expect(detail).toContain("80 ms");
    expect(detail).toContain("Receipt compute"); expect(detail).toContain("40 ms");
    await click(container.querySelector("[data-span-filter='model']")!);
    expect(container.querySelector("[data-span-kind='tool']")).toBeNull();
    expect(container.textContent).not.toContain("150 ms overall");
  });

  it("shows the routed tier and actual model in Gantt hover detail", async () => {
    const rootStart = basePayload();
    const modelStart = basePayload({ span_id: "span-3333333333333333", parent_span_id: "span-1111111111111111", kind: "model", display_name: "Agent reasoning", route_mode: "switchyard_escalation", configured_model: LOCAL_MODEL, selected_tier: "efficient", started_at: iso(10) });
    const attempt = { role: "agent_reasoning", algorithm: "switchyard_escalation", destination_class: "local_model", configured_model: LOCAL_MODEL, model_assertion: LOCAL_MODEL, identity_evidence: "direct_provider_verified", state: "succeeded", failure_class: null, application_call_id: "call-00000001", application_request_id: "request-00000001", latency_ms: 40, tokens: { prompt: 10, completion: 5, total: 15 }, validation_status: "valid", switchyard_trial_id: null, selected_tier: "efficient" };
    const modelDone = completed(modelStart, 50, { model_assertion: LOCAL_MODEL, identity_evidence: "direct_provider_verified", tokens: attempt.tokens, transport_ms: 40, attempt });
    const rootDone = completed(rootStart, 70, { terminal: "success" });
    const events = [event(1, rootStart), event(2, modelStart), event(3, modelDone), event(4, rootDone)];
    await render("completed", events);
    await click(container.querySelector("[data-research-tab='performance']")!);
    const row = container.querySelector<HTMLElement>("[data-span-kind='model']")!;
    expect(row.dataset.selectedTier).toBe("efficient");
    expect(row.dataset.configuredModel).toBe(LOCAL_MODEL);
    await hover(row);
    const detail = container.querySelector("[data-span-detail='span-3333333333333333']")?.textContent;
    expect(detail).toContain("Selected tier"); expect(detail).toContain("Efficient"); expect(detail).toContain(LOCAL_MODEL);
  });

  it("distinguishes estimated live elapsed and original cached receipt compute from terminal server wall time", async () => {
    const rootStart = basePayload(), liveTool = toolPayload("span-2222222222222222", 10);
    await render("running", [event(1, rootStart), event(2, liveTool)]);
    await click(container.querySelector("[data-research-tab='performance']")!);
    const liveRow = container.querySelector<HTMLElement>("[data-span-id='span-2222222222222222']")!;
    await hover(liveRow);
    const liveDetail = container.querySelector("[data-span-detail='span-2222222222222222']")?.textContent;
    expect(liveDetail).toContain("Estimated live elapsed");
    expect(liveDetail).not.toContain("Server wall time");

    const reusedStart = toolPayload("span-3333333333333333", 10, { reused: true, call_sha256: "b".repeat(64) });
    const reusedDone = completed(reusedStart, 2, { state: "reused", compute_ms: 420 });
    const rootDone = completed(rootStart, 20, { terminal: "success" });
    const reusedEvents = [event(1, rootStart), event(2, reusedStart), event(3, reusedDone), event(4, rootDone)];
    await act(async () => root.render(<ResearchPanel record={investigation(reusedEvents)} events={reusedEvents} busy={false} onCancel={() => undefined} onRetry={() => undefined} />));
    await click(container.querySelector("[data-research-tab='performance']")!);
    const reusedRow = container.querySelector<HTMLElement>("[data-span-id='span-3333333333333333']")!;
    expect(reusedRow.dataset.spanReused).toBe("true");
    await hover(reusedRow);
    const reusedDetail = container.querySelector("[data-span-detail='span-3333333333333333']")?.textContent;
    expect(reusedDetail).toContain("Server wall time"); expect(reusedDetail).toContain("2 ms");
    expect(reusedDetail).toContain("Original cached receipt compute"); expect(reusedDetail).toContain("420 ms");
  });

  it("never binds the request prompt or a hidden report object into the analysis DOM", async () => {
    await render();
    expect(container.innerHTML).not.toContain("RAW PROMPT CANARY");
    expect(container.querySelector("script[type='application/json'], [data-report-json], [data-prompt]")).toBeNull();
  });

  it("keeps typed failure codes out of the visible activity trail", async () => {
    const failure: TrajectoryEvent = {
      sequence: 1,
      event_type: "error",
      label: "Investigation failed",
      detail: "route_failure:transport_contract",
      occurred_at: iso(100),
      tool: null,
      payload: { turn_id: "turn-0001", terminal: "route_failure" },
    };
    await act(async () => root.render(<Trajectory events={[failure]} />));
    expect(container.textContent).toContain("See the answer pane for recovery guidance");
    expect(container.textContent).not.toContain("route_failure:transport_contract");
    expect(container.textContent).not.toContain("Terminal outcome: route failure");
  });

  it("does not expose a route failure carried by a non-terminal progress row", async () => {
    const progressFailure: TrajectoryEvent = {
      sequence: 1,
      event_type: "planning",
      label: "Investigation",
      detail: "Investigation failed.",
      occurred_at: iso(100),
      tool: null,
      payload: { turn_id: "turn-0001", terminal: "route_failure" },
    };
    await act(async () => root.render(<Trajectory events={[progressFailure]} />));
    expect(container.textContent).toContain("Investigation failed.");
    expect(container.textContent).not.toContain("Terminal outcome: route failure");
    expect(container.textContent).not.toContain("route_failure");
  });

  it("restores focus to the invoking research citation when its evidence modal closes", async () => {
    await render();
    await click(container.querySelector("[data-research-tab='evidence']")!);
    const citation = container.querySelector<HTMLButtonElement>("[data-citation-id='citation-00000001']")!;
    citation.focus();
    await click(citation);
    expect(container.querySelector("[role='dialog']")).not.toBeNull();
    expect(container.querySelector<HTMLButtonElement>("[aria-label='Close evidence']")).toBe(document.activeElement);
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    expect(container.querySelector("[role='dialog']")).toBeNull();
    expect(document.activeElement).toBe(citation);
  });

  it("labels live and failed spans with text-backed states", async () => {
    const rootStart = basePayload(), toolStart = toolPayload("span-2222222222222222", 10);
    await render("running", [event(1, rootStart), event(2, toolStart)]);
    expect(container.querySelector("[role='status']")?.textContent).toContain("2 running");
    await click(container.querySelector("[data-research-tab='performance']")!);
    expect(container.querySelector("[data-span-state='started']")?.textContent).toContain("Started");

    const toolFailed = completed(toolStart, 20, { state: "failed", outcome: "failed", engine: null, device: null, compute_ms: null, receipt_sha256: null, evidence_count: null, citation_count: null, evidence_preview: [] });
    const rootFailed = completed(rootStart, 40, { state: "failed", terminal: "tool_failure" });
    await act(async () => root.render(<ResearchPanel record={investigation([event(1, rootStart), event(2, toolStart), event(3, toolFailed), event(4, rootFailed)], "failed")} events={[event(1, rootStart), event(2, toolStart), event(3, toolFailed), event(4, rootFailed)]} busy={false} onCancel={() => undefined} onRetry={() => undefined} />));
    await click(container.querySelector("[data-research-tab='performance']")!);
    expect(container.querySelector("[data-span-state='failed']")?.textContent).toContain("Failed");
  });

  it("opens as a labelled drawer, closes on Escape, and restores trigger focus", async () => {
    vi.mocked(matchMedia).mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() } as unknown as MediaQueryList);
    await render();
    const trigger = container.querySelector<HTMLButtonElement>("[data-action='show-analysis']")!;
    trigger.focus(); await click(trigger);
    expect(container.querySelector("[role='dialog']")?.getAttribute("aria-modal")).toBe("true");
    expect(container.querySelector("[role='separator']")).toBeNull();
    expect(container.querySelector<HTMLButtonElement>("[data-action='close-analysis']")).toBe(document.activeElement);
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    expect(trigger).toBe(document.activeElement);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("supports bounded keyboard resizing on desktop", async () => {
    vi.stubGlobal("innerWidth", 1_440);
    await render();
    const panel = container.querySelector<HTMLElement>("#research-workspace")!;
    const handle = container.querySelector<HTMLElement>("[role='separator']")!;
    expect(handle.getAttribute("aria-label")).toBe("Resize Research details panel");
    expect(handle.getAttribute("aria-valuemin")).toBe("300");
    expect(handle.getAttribute("aria-valuemax")).toBe("710");
    expect(handle.getAttribute("aria-valuenow")).toBe("360");
    expect(panel.style.width).toBe("360px");

    await act(async () => handle.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true, cancelable: true })));
    expect(panel.style.width).toBe("400px");
    expect(handle.getAttribute("aria-valuetext")).toBe("400 pixels wide");
    await act(async () => handle.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true, cancelable: true })));
    expect(panel.style.width).toBe("710px");
    await act(async () => handle.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true, cancelable: true })));
    expect(panel.style.width).toBe("300px");

    Object.assign(handle, { setPointerCapture: vi.fn(), hasPointerCapture: vi.fn(() => true), releasePointerCapture: vi.fn() });
    await act(async () => handle.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 1_000 })));
    await act(async () => handle.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 900 })));
    expect(panel.style.width).toBe("400px");
    expect(panel.classList.contains("resizing")).toBe(true);
    await act(async () => handle.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 900 })));
    expect(panel.classList.contains("resizing")).toBe(false);
  });

  it("keeps the active research view inside a dedicated flex scroll region", async () => {
    await render();
    const activity = container.querySelector("#research-panel-activity");
    expect(activity?.classList.contains("research-tab-panel")).toBe(true);
    expect(activity?.querySelector(".trajectory > ol")).not.toBeNull();
    await click(container.querySelector("[data-research-tab='evidence']")!);
    expect(container.querySelector("#research-panel-evidence > .research-scroll")).not.toBeNull();
    await click(container.querySelector("[data-research-tab='performance']")!);
    expect(container.querySelector("#research-panel-performance > .research-scroll")).not.toBeNull();
  });

  it("closes evidence without also dismissing the responsive research drawer", async () => {
    vi.mocked(matchMedia).mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() } as unknown as MediaQueryList);
    await render();
    const analysisTrigger = container.querySelector<HTMLButtonElement>("[data-action='show-analysis']")!;
    await click(analysisTrigger);
    await click(container.querySelector("[data-research-tab='evidence']")!);
    const citation = container.querySelector<HTMLButtonElement>("[data-citation-id='citation-00000001']")!;
    citation.focus();
    await click(citation);
    expect(container.querySelectorAll("[role='dialog']")).toHaveLength(2);

    const evidenceClose = container.querySelector<HTMLButtonElement>("[aria-label='Close evidence']")!;
    await act(async () => evidenceClose.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
    expect(container.querySelectorAll("[role='dialog']")).toHaveLength(1);
    expect(analysisTrigger.getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(citation);
  });
});
