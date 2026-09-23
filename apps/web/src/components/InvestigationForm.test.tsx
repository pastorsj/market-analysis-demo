/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getSystemStatus } from "../api/client";
import type { ShockEvent } from "../api/events";
import {
  DRAFT_MODEL,
  EMBED_MODEL,
  LOCAL_MODEL,
  LUNA_MODEL,
  CAPABLE_MODEL,
  type InvestigationRequest,
  type SystemStatus,
} from "../api/types";
import { QUESTION_PRESETS } from "../questionPresets";
import { InvestigationForm } from "./InvestigationForm";

vi.mock("../api/client", () => ({ getSystemStatus: vi.fn() }));

const REVISION = "a".repeat(40);
const status = (remote = false): SystemStatus => ({
  schema_version: "system-status-v1",
  service: "agent",
  version: "1.2.0",
  ready: remote,
  dependencies: { tools: true, model: true, coverage: true, checkpoint: true, mcp_contract: true, event_catalog: true },
  remote_routing_enabled: remote,
  observability: { remote_inference_enabled: remote, langsmith_export_enabled: false },
  investigations: "available",
  supported_tickers: ["NVDA", "AMD", "JPM", "GS", "SCHW"],
  companies: [
    { symbol: "NVDA", display_name: "NVIDIA" },
    { symbol: "AMD", display_name: "Advanced Micro Devices" },
    { symbol: "JPM", display_name: "JPMorgan Chase" },
    { symbol: "GS", display_name: "Goldman Sachs" },
    { symbol: "SCHW", display_name: "Charles Schwab" },
  ],
  coverage: {
    scenario_id: "market-shock-v2-test",
    scenario_manifest_sha256: "a".repeat(64),
    data_tier: "cc0_reconstruction",
    vintage_status: "reconstructed_later",
    first_session: "2022-01-03",
    last_session: "2026-09-11",
    session_count: 1177,
    document_source_kinds: ["company_release", "filing", "primary_source"],
  },
  limitations: ["reconstructed_later_market_data", "licensed_news_unavailable", "source_coverage_varies_by_ticker_and_cutoff"],
  models: [
    { model_id: LOCAL_MODEL, revision: REVISION, location: "local_model_service", identity_basis: "immutable_revision", roles: ["local_generation", "switchyard_efficient_target"], route_eligible: true, dependency: "model" },
    { model_id: DRAFT_MODEL, revision: REVISION, location: "local_model_service", identity_basis: "immutable_revision", roles: ["speculative_assistant"], route_eligible: false, dependency: "model" },
    { model_id: EMBED_MODEL, revision: REVISION, location: "local_tools_service", identity_basis: "immutable_revision", roles: ["retrieval_embedding"], route_eligible: false, dependency: "tools" },
    { model_id: LUNA_MODEL, revision: null, location: "internal_inference_server", identity_basis: "exact_server_route_id", roles: ["switchyard_classifier"], route_eligible: true, dependency: "remote_routing" },
    { model_id: CAPABLE_MODEL, revision: null, location: "internal_inference_server", identity_basis: "exact_server_route_id", roles: ["switchyard_capable_target", "report_formatter"], route_eligible: true, dependency: "remote_routing" },
  ],
  routes: [{ mode: "switchyard_escalation", enabled: remote, disabled_reason: remote ? null : "remote_routing_disabled", model_roles: ["switchyard_classifier", "local_generation", "switchyard_capable_target", "report_formatter"], evidence_tools_unchanged: true }],
  contracts: {
    max_investigation_turns: 4,
    max_concurrent_investigations: 1,
    data: "market-shock-v2-test",
    skill: "market-agent-skills/deep-agent-1.0.0",
    prompt: "market-shock-grounded-synthesis/2.0.0",
    safety: "bounded-equity-research/1.0.0",
    generation_model: LOCAL_MODEL,
    embedding_model: EMBED_MODEL,
    embedding_revision: REVISION,
  },
});

const pinnedEvent = {
  event_id: "nvda-deepseek-2025-01-27", category_id: "ai-competition", title: "NVIDIA and the DeepSeek repricing",
  summary: "Market attention on AI compute economics coincided with a focused semiconductor repricing window.", sort_order: 1,
  event_session: "2025-01-27", source_dates: ["2025-01-27"], primary_ticker: "NVDA", analysis_tickers: ["NVDA", "AMD", "AVGO"], context_instruments: ["QQQ", "SPY"],
  start_session: "2024-12-24", end_session: "2025-02-10", default_cutoff: "2025-01-27T21:00:00Z",
  questions: [
    { question_id: "measure-move", label: "Measure the repricing", capability: "move-measurement", text: "How unusual was this move relative to recent history?" },
    { question_id: "find-analogues", label: "Find historical analogues", capability: "historical-analogues", text: "Which historical analogues are most similar to this market shock?" },
  ], source_requirements: {},
  limitations: [{ limitation_id: "reconstructed-market-history", detail: "Market history is reconstructed later and is not an archived-at-cutoff feed." }],
  qualification: {
    status: "partial", market: { status: "ready", gaps: [] }, documents: { status: "ready", gaps: [] },
    licensed_news: { status: "partial", gaps: [{ code: "unsupported_missing_news", layer: "licensed_news", detail: "Licensed historical news is not present in this snapshot." }] },
    derived_features: { status: "ready", gaps: [] }, gaps: [{ code: "unsupported_missing_news", layer: "licensed_news", detail: "Licensed historical news is not present in this snapshot." }],
  },
} as unknown as ShockEvent;

const change = (element: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement, value: string) => {
  Object.getOwnPropertyDescriptor(element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value")?.set?.call(element, value);
  element.dispatchEvent(new Event("change", { bubbles: true }));
  element.dispatchEvent(new Event("input", { bubbles: true }));
};

describe("guided investigation launcher", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.resetAllMocks();
  });

  async function render(props: Partial<Parameters<typeof InvestigationForm>[0]> = {}) {
    await act(async () => {
      root.render(<InvestigationForm busy={false} onSubmit={() => undefined} {...props} />);
    });
  }

  it("loads live coverage and keeps all six guided presets visible across ticker changes", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(true));
    await render();
    const ticker = container.querySelector<HTMLSelectElement>("[aria-label='Ticker']")!;
    const date = container.querySelector<HTMLInputElement>("[aria-label='Evidence cutoff']")!;
    const questions = container.querySelector<HTMLSelectElement>("[aria-label='Guided question']")!;
    expect([...ticker.options].map((item) => item.value)).toEqual(["", "NVDA", "AMD", "JPM", "GS", "SCHW"]);
    expect(date.min).toBe("2022-01-03");
    expect(date.max).toBe("2026-09-11");
    expect([...questions.options].filter((item) => !item.hidden).map((item) => item.value)).toEqual([
      "nvda-market-dislocation-2025-01-27",
      "nvda-historical-analogues-2025-01-27",
      "schw-shock-propagation-2023-03-13",
      "nvda-volatility-risk-2025-01-27",
      "jpm-narrative-map-2025-01-15",
      "jpm-peer-comparison-2025-01-15",
    ]);
    expect([...questions.options].map((item) => item.text)).toContain(
      "SCHW · 2023-03-13 · Shock propagation · Relationship paths",
    );
    expect(questions.closest(".composer-heading")).not.toBeNull();
    expect(container.querySelector(".scope-fields")?.contains(questions)).toBe(false);
    expect(questions.value).toBe("");
    expect(questions.textContent).not.toContain("Custom question");
    expect(container.textContent).not.toContain("No guided questions are published yet");

    await act(async () => change(ticker, "AMD"));
    expect([...questions.options].filter((item) => !item.hidden).map((item) => item.value)).toEqual([
      "nvda-market-dislocation-2025-01-27",
      "nvda-historical-analogues-2025-01-27",
      "schw-shock-propagation-2023-03-13",
      "nvda-volatility-risk-2025-01-27",
      "jpm-narrative-map-2025-01-15",
      "jpm-peer-comparison-2025-01-15",
    ]);
    expect(container.textContent).not.toContain("No guided questions are available");
  });

  it("explains that remote-off is a prepared state rather than a failed local model", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(false));
    await render();
    const message = container.querySelector(".runtime-state .degraded")?.textContent;
    expect(message).toContain("prepared locally");
    expect(message).toContain("approved internal inference route");
    expect(message).not.toContain("local route recovers");
  });

  it("applies a cross-ticker preset atomically and returns to manual entry when its text is edited", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(true));
    const onSubmit = vi.fn<(value: InvestigationRequest) => void>();
    await render({ onSubmit });
    const preset = QUESTION_PRESETS.find((item) => item.ticker === "SCHW")!;
    const questions = container.querySelector<HTMLSelectElement>("[aria-label='Guided question']")!;
    await act(async () => change(questions, preset.presetId));
    expect(questions.value).toBe(preset.presetId);
    expect(container.querySelector<HTMLSelectElement>("[aria-label='Ticker']")!.value).toBe("SCHW");
    expect(container.querySelector<HTMLInputElement>("[aria-label='Evidence cutoff']")!.value).toBe("2023-03-13");
    const textarea = container.querySelector<HTMLTextAreaElement>("[aria-label='Investigation question']")!;
    expect(textarea.value).toBe(preset.question);
    await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(onSubmit).toHaveBeenCalledWith({
      question: preset.question,
      ticker: "SCHW",
      as_of: "2023-03-13",
      route_mode: "switchyard_escalation",
    });
    await act(async () => change(textarea, `${preset.question} Please be concise.`));
    expect(questions.value).toBe("");
    expect(questions.selectedOptions[0]?.text).toBe("Choose an example");
    expect(textarea.value).toBe(`${preset.question} Please be concise.`);
  });

  it("applies an editable dashboard seed without submitting it", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(true));
    const onSubmit = vi.fn<(value: InvestigationRequest) => void>();
    await render({
      onSubmit,
      initialSeed: {
        ticker: "JPM",
        as_of: "2025-01-15",
        question: "How did JPM compare with its financial peers on this date?",
      },
    });
    expect(container.querySelector<HTMLSelectElement>("[aria-label='Ticker']")!.value).toBe("JPM");
    expect(container.querySelector<HTMLInputElement>("[aria-label='Evidence cutoff']")!.value).toBe("2025-01-15");
    const question = container.querySelector<HTMLTextAreaElement>("[aria-label='Investigation question']")!;
    expect(question.value).toContain("financial peers");
    expect(onSubmit).not.toHaveBeenCalled();
    await act(async () => change(question, `${question.value} Focus on relative return.`));
    expect(question.value).toContain("Focus on relative return");
  });

  it("keeps a dashboard event pinned while editing and sends its canonical server-bound scope", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(true));
    const onSubmit = vi.fn<(value: InvestigationRequest) => void>();
    await render({
      onSubmit,
      initialSeed: {
        ticker: "NVDA",
        as_of: pinnedEvent.default_cutoff,
        question: "Which historical analogues are most similar to this market shock?",
        event: pinnedEvent,
        eventQuestion: { question_id: "find-analogues", label: "Find historical analogues", capability: "historical-analogues", text: "Which historical analogues are most similar to this market shock?" },
      },
    });
    expect(container.textContent).toContain("Pinned event context · partial evidence");
    expect(container.textContent).toContain("NVDA, AMD, AVGO · NVDA primary");
    expect(container.textContent).toContain("Market session2025-01-27");
    expect(container.textContent).toContain("2024-12-24–2025-02-10");
    expect(container.querySelector("[aria-label='Ticker']")).toBeNull();
    expect(container.querySelector("[aria-label='Evidence cutoff']")).toBeNull();
    expect(container.querySelector("[aria-label='Guided question']")).toBeNull();
    const eventQuestions = container.querySelector<HTMLSelectElement>("[aria-label='Event question']")!;
    expect([...eventQuestions.options].map((item) => item.text)).toEqual([
      "Choose a question",
      "Measure the repricing · move measurement",
      "Find historical analogues · historical analogues",
    ]);
    expect(eventQuestions.value).toBe("find-analogues");
    const scenarioAction = container.querySelector<HTMLButtonElement>("[aria-label='Investigate scenario']")!;
    expect(scenarioAction.textContent).toContain("Investigate scenario");
    expect(scenarioAction.disabled).toBe(false);
    expect(container.querySelector(".event-mode-note")).toBeNull();
    expect(container.querySelector(".scope-examples")).toBeNull();
    expect(onSubmit).not.toHaveBeenCalled();

    const textarea = container.querySelector<HTMLTextAreaElement>("[aria-label='Investigation question']")!;
    await act(async () => change(textarea, `${textarea.value} Focus on measured features.`));
    expect(container.querySelector("#pinned-event-title")?.textContent).toContain("DeepSeek repricing");
    expect(eventQuestions.value).toBe("");
    await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(onSubmit).toHaveBeenCalledWith({
      question: "Which historical analogues are most similar to this market shock? Focus on measured features.",
      ticker: "NVDA",
      as_of: "2025-01-27T21:00:00Z",
      route_mode: "switchyard_escalation",
      event_id: "nvda-deepseek-2025-01-27",
    });

    await act(async () => [...container.querySelectorAll<HTMLButtonElement>(".event-pinned-scope button")].find((button) => button.textContent === "Use custom scope")!.click());
    expect(container.querySelector("#pinned-event-title")).toBeNull();
    expect(container.querySelector<HTMLSelectElement>("[aria-label='Ticker']")!.value).toBe("NVDA");
    expect(container.querySelector<HTMLInputElement>("[aria-label='Evidence cutoff']")!.value).toBe("2025-01-27");
    onSubmit.mockClear();
    await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(onSubmit).toHaveBeenCalledWith({
      question: "Which historical analogues are most similar to this market shock? Focus on measured features.",
      ticker: "NVDA",
      as_of: "2025-01-27",
      route_mode: "switchyard_escalation",
    });
  });

  it("reacts to a scenario selected after the Research form is already mounted", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(true));
    const onSubmit = vi.fn<(value: InvestigationRequest) => void>();
    await render({ onSubmit });
    expect(container.querySelector("#pinned-event-title")).toBeNull();

    await act(async () => root.render(<InvestigationForm
      busy={false}
      onSubmit={onSubmit}
      initialSeed={{ ticker: "NVDA", as_of: pinnedEvent.default_cutoff, question: pinnedEvent.questions[0].text, event: pinnedEvent, eventQuestion: pinnedEvent.questions[0] }}
    />));
    expect(container.querySelector("#pinned-event-title")?.textContent).toContain("DeepSeek repricing");
    expect(container.querySelector<HTMLSelectElement>("[aria-label='Event question']")?.options).toHaveLength(3);
    expect(container.querySelector<HTMLSelectElement>("[aria-label='Event question']")?.value).toBe("measure-move");
    expect(container.querySelector<HTMLTextAreaElement>("[aria-label='Investigation question']")?.value).toBe("How unusual was this move relative to recent history?");
    expect(container.querySelector<HTMLButtonElement>("[aria-label='Investigate scenario']")?.disabled).toBe(false);

    const measure = container.querySelector<HTMLSelectElement>("[aria-label='Event question']")!;
    await act(async () => change(measure, "measure-move"));
    expect(container.querySelector<HTMLTextAreaElement>("[aria-label='Investigation question']")?.value).toBe("How unusual was this move relative to recent history?");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("places scenario choices inside the center pane between the welcome and question composer", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(true));
    await render({ scenarioPicker: <section data-testid="scenario-picker">Scenario choices</section> });
    const stage = container.querySelector(".prompt-stage")!;
    expect(stage.classList.contains("with-scenarios")).toBe(true);
    expect([...stage.children].map((child) => child.getAttribute("data-testid") ?? child.className)).toEqual([
      "welcome",
      "scenario-picker",
      "question-composer",
      "disclaimer",
    ]);
  });

  it("makes Coverage and Models keyboard tabs backed by runtime truth", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status());
    await render();
    const coverage = container.querySelector<HTMLButtonElement>("[role='tab'][data-tab='coverage']")!;
    expect(coverage.getAttribute("aria-selected")).toBe("true");
    expect(container.textContent).toContain("2022-01-03");
    await act(async () => coverage.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    const models = container.querySelector<HTMLButtonElement>("[role='tab'][data-tab='models']")!;
    expect(models.getAttribute("aria-selected")).toBe("true");
    expect(container.textContent).toContain(LOCAL_MODEL);
    expect(container.textContent).toContain(EMBED_MODEL);
    expect(container.textContent).toContain(LUNA_MODEL);
    expect(container.textContent).toContain(CAPABLE_MODEL);
    expect(container.textContent).toContain("No Llama-family models or silent substitutes");
  });

  it("uses the one Switchyard route when the internal inference boundary is available", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(true));
    const onSubmit = vi.fn<(value: InvestigationRequest) => void>();
    await render({ onSubmit });
    expect(container.querySelector("[aria-label='Model route']")).toBeNull();
    expect(container.textContent).not.toContain("Analysis route");
    const textarea = container.querySelector<HTMLTextAreaElement>("[aria-label='Investigation question']")!;
    await act(async () => change(textarea, "What happened to NVDA on 2026-09-11?"));
    await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(onSubmit.mock.calls[0][0].route_mode).toBe("switchyard_escalation");
  });

  it("submits only a live-supported custom scope", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(true));
    const onSubmit = vi.fn<(value: InvestigationRequest) => void>();
    await render({ onSubmit });
    const textarea = container.querySelector<HTMLTextAreaElement>("[aria-label='Investigation question']")!;
    await act(async () => change(textarea, "What happened to NVDA on 2026-09-11?"));
    await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(onSubmit).toHaveBeenCalledWith({
      question: "What happened to NVDA on 2026-09-11?",
      ticker: "NVDA",
      as_of: "2026-09-11",
      route_mode: "switchyard_escalation",
    });
  });

  it("preserves an explicitly unspecified ticker and cutoff for question-driven policy", async () => {
    vi.mocked(getSystemStatus).mockResolvedValue(status(true));
    const onSubmit = vi.fn<(value: InvestigationRequest) => void>();
    await render({ onSubmit });
    const ticker = container.querySelector<HTMLSelectElement>("[aria-label='Ticker']")!;
    const date = container.querySelector<HTMLInputElement>("[aria-label='Evidence cutoff']")!;
    const textarea = container.querySelector<HTMLTextAreaElement>("[aria-label='Investigation question']")!;
    await act(async () => change(ticker, ""));
    await act(async () => change(date, ""));
    await act(async () => change(textarea, "What does this application do?"));
    await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(onSubmit).toHaveBeenCalledWith({
      question: "What does this application do?",
      ticker: null,
      as_of: null,
      route_mode: "switchyard_escalation",
    });
  });

  it("surfaces status failure and retries without enabling an invented fallback", async () => {
    vi.mocked(getSystemStatus).mockRejectedValueOnce(new Error("Status unavailable")).mockResolvedValueOnce(status());
    await render();
    expect(container.querySelector("[role='alert']")?.textContent).toContain("Status unavailable");
    expect(container.querySelector<HTMLButtonElement>("[aria-label='Investigate']")!.disabled).toBe(true);
    await act(async () => container.querySelector<HTMLButtonElement>("[data-action='retry-status']")!.click());
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(container.querySelector<HTMLSelectElement>("[aria-label='Ticker']")!.options).toHaveLength(6);
  });
});
