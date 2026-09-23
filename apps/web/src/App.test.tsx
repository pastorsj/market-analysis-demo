// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { PinnedEventSummary, TerminalMessage, resolveAppView, terminalPresentation } from "./App";
import * as eventApi from "./api/events";
import type { ShockEvent, ShockEventCatalog } from "./api/events";
import type { Investigation, SystemStatus, TerminalOutcome, TerminalTurn } from "./api/types";
import * as investigationHook from "./hooks/useInvestigation";
import * as statusHook from "./hooks/useSystemStatus";

const terminalTurn = (detail: string, terminal: TerminalOutcome = "route_failure"): TerminalTurn => ({
  turn_id: "turn-0001",
  terminal,
  report: null,
  security_receipt: {} as TerminalTurn["security_receipt"],
  event: {
    sequence: 1,
    event_type: "error",
    label: "Investigation stopped",
    detail,
    occurred_at: "2025-01-27T17:00:00-05:00",
    tool: null,
    payload: {},
  },
});

const PROJECT_LINK = "https://smith.langchain.com/o/test-org/projects/p/test-project";

const visitorStatus = (projectLink?: string) => ({
  observability: projectLink
    ? { remote_inference_enabled: true, langsmith_export_enabled: true, project_link: projectLink }
    : { remote_inference_enabled: true, langsmith_export_enabled: false },
  contracts: { max_investigation_turns: 4, max_concurrent_investigations: 1 },
} as unknown as SystemStatus);

const visitorRecord = (turns: string[], status: Investigation["status"] = "completed"): Investigation => ({
  investigation_id: "case-visitor-boundary",
  created_at: "2025-01-27T21:05:00Z",
  updated_at: "2025-01-27T21:06:00Z",
  status,
  request: {
    question: turns[0] ?? "How unusual was NVIDIA's move?",
    ticker: "NVDA",
    as_of: "2025-01-27T21:00:00Z",
    route_mode: "switchyard_escalation",
  },
  turns,
  scope: null,
  active_skill: null,
  events: [],
  security_receipts: [],
  report: null,
  error: null,
});

describe("application view routing", () => {
  it.each([
    ["/", "", "dashboard"],
    ["/research", "", "research"],
    ["/built-together", "", "built-together"],
    ["/legacy", "?investigation=case-12345678", "research"],
    ["/unknown", "", "dashboard"],
  ] as const)("resolves %s%s to %s", (pathname, search, expected) => {
    expect(resolveAppView(pathname, search)).toBe(expected);
  });
});

describe("human-readable investigation failures", () => {
  it.each([
    ["context_length_exceeded", "This investigation exceeded the model’s context limit"],
    ["invalid_json", "The model returned an unusable tool request"],
    ["transport_error", "The analysis route was interrupted"],
    ["transport_contract", "We couldn’t complete this investigation"],
    ["timeout", "The analysis model took too long to respond"],
    ["provider_error", "The analysis model reported a temporary error"],
    ["identity_mismatch", "We stopped before showing an unverified answer"],
    ["route_unavailable", "No approved analysis model is available right now"],
  ])("explains route_failure:%s without using the code as the headline", (code, title) => {
    const presentation = terminalPresentation("route_failure", `route_failure:${code}`);
    expect(presentation.title).toBe(title);
    expect(presentation.explanation.length).toBeGreaterThan(20);
    expect(presentation.advice).toMatch(/try again|wait/i);
    expect(presentation.technicalCode).toBe(`route_failure:${code}`);
  });

  it("explains the agent research-step limit as an unfinished synthesis", () => {
    const presentation = terminalPresentation("synthesis_failure", "synthesis_failure:agent_turn_limit");
    expect(presentation.title).toBe("The agent ran out of research steps");
    expect(presentation.explanation).toContain("gathered evidence");
    expect(presentation.advice).toContain("Try again");
    expect(presentation.technicalCode).toBe("synthesis_failure:agent_turn_limit");
  });

  it("explains an unsupported draft detail as a retained-evidence failure", () => {
    const presentation = terminalPresentation("synthesis_failure", "synthesis_failure:invalid_evidence");
    expect(presentation.title).toContain("beyond the evidence");
    expect(presentation.explanation).toContain("not supported by the retained cutoff-qualified sources");
    expect(presentation.advice).toContain("specific company, date, or evidence comparison");
    expect(presentation.technicalCode).toBe("synthesis_failure:invalid_evidence");
  });

  describe("recovery action", () => {
    let container: HTMLDivElement;
    let root: Root;

    beforeEach(() => {
      (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
      container = document.createElement("div");
      document.body.append(container);
      root = createRoot(container);
    });

    afterEach(() => {
      act(() => root.unmount());
      container.remove();
    });

    it("offers retry while keeping the typed code in collapsed support details", () => {
      const retry = vi.fn();
      act(() => root.render(<TerminalMessage terminal={terminalTurn("route_failure:transport_error")} onRetry={retry} />));

      expect(container.querySelector("[role=alert]")?.textContent).toContain("The analysis route was interrupted");
      expect(container.querySelector(".terminal-technical")?.hasAttribute("open")).toBe(false);
      expect(container.querySelector(".terminal-technical code")?.textContent).toBe("route_failure:transport_error");
      const button = container.querySelector<HTMLButtonElement>(".terminal-retry");
      expect(button?.textContent).toBe("Try again");
      act(() => button?.click());
      expect(retry).toHaveBeenCalledOnce();
    });

    it("disables the retry action while a retry is already running", () => {
      act(() => root.render(<TerminalMessage terminal={terminalTurn("synthesis_failure:agent_turn_limit", "synthesis_failure")} onRetry={() => undefined} retrying />));
      const button = container.querySelector<HTMLButtonElement>(".terminal-retry");
      expect(button?.disabled).toBe(true);
      expect(button?.textContent).toBe("Trying again…");
    });
  });
});

describe("active event context", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("preserves the title, companies, window, and event identity during an active investigation", () => {
    const event = {
      event_id: "nvda-deepseek-2025-01-27", title: "NVIDIA and the DeepSeek repricing",
      analysis_tickers: ["NVDA", "AMD", "AVGO"], start_session: "2024-12-24", end_session: "2025-02-10",
    } as unknown as ShockEvent;
    act(() => root.render(<PinnedEventSummary eventId={event.event_id} event={event} />));
    expect(container.textContent).toContain("NVIDIA and the DeepSeek repricing");
    expect(container.textContent).toContain("NVDA, AMD, AVGO");
    expect(container.textContent).toContain("2024-12-24–2025-02-10");
    expect(container.textContent).toContain("nvda-deepseek-2025-01-27");
  });

  it("keeps the server-verified event ID visible after a direct URL reload", () => {
    act(() => root.render(<PinnedEventSummary eventId="nvda-deepseek-2025-01-27" event={null} />));
    expect(container.textContent).toContain("Pinned event context");
    expect(container.textContent).toContain("server still owns and verifies");
    expect(container.textContent).toContain("nvda-deepseek-2025-01-27");
  });
});

describe("restored investigation event context", () => {
  let container: HTMLDivElement;
  let root: Root;

  const event = {
    event_id: "nvda-deepseek-2025-01-27",
    title: "NVIDIA and the DeepSeek repricing",
    analysis_tickers: ["NVDA", "AMD", "AVGO"],
    start_session: "2024-12-24",
    end_session: "2025-02-10",
  } as unknown as ShockEvent;
  const catalog = { events: [event] } as unknown as ShockEventCatalog;
  const record: Investigation = {
    investigation_id: "case-12345678",
    created_at: "2025-01-27T21:05:00Z",
    updated_at: "2025-01-27T21:06:00Z",
    status: "completed",
    request: {
      question: "Which historical analogues are most similar?",
      ticker: "NVDA",
      as_of: "2025-01-27T21:00:00Z",
      route_mode: "switchyard_escalation",
      event_id: event.event_id,
    },
    turns: ["Which historical analogues are most similar?"],
    scope: null,
    active_skill: "historical-analogues",
    events: [],
    security_receipts: [],
    report: null,
    error: null,
  };

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    window.history.replaceState({}, "", "/research?investigation=case-12345678");
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    vi.spyOn(investigationHook, "useInvestigation").mockReturnValue({
      state: { record, events: [], connection: "closed", busy: false, error: null },
      create: vi.fn(), turn: vi.fn(), cancel: vi.fn(), retry: vi.fn(), reconnect: vi.fn(), reload: vi.fn(), reset: vi.fn(),
    });
    vi.spyOn(statusHook, "useSystemStatus").mockReturnValue({ state: "ready", status: null, detail: "Ready", refresh: vi.fn() });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    window.history.replaceState({}, "", "/");
  });

  it("hydrates a direct investigation URL with its full catalog event context", async () => {
    const lookup = vi.spyOn(eventApi, "getShockEvents").mockResolvedValue(catalog);
    await act(async () => {
      root.render(<App />);
      await Promise.resolve();
    });

    expect(lookup).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(container.querySelector("[aria-label='Pinned event context']")?.textContent).toContain("NVIDIA and the DeepSeek repricing");
    expect(container.textContent).toContain("NVDA, AMD, AVGO");
    expect(container.textContent).toContain("2024-12-24–2025-02-10");
    expect(container.textContent).toContain(event.event_id);
  });

  it.each([
    ["catalog mismatch", () => Promise.resolve({ events: [] } as unknown as ShockEventCatalog)],
    ["catalog failure", () => Promise.reject(new Error("Catalog unavailable"))],
  ])("keeps the safe event-ID fallback without blocking on %s", async (_label, result) => {
    vi.spyOn(eventApi, "getShockEvents").mockImplementation(result);
    await act(async () => {
      root.render(<App />);
      await Promise.resolve();
    });

    const summary = container.querySelector("[aria-label='Pinned event context']");
    expect(summary?.textContent).toContain("Pinned event context");
    expect(summary?.textContent).toContain("server still owns and verifies");
    expect(summary?.textContent).toContain(event.event_id);
    expect(container.textContent).toContain(record.request.question);
  });
});

describe("visitor research boundary", () => {
  let container: HTMLDivElement;
  let root: Root;
  let reset: ReturnType<typeof vi.fn>;

  function renderVisitor(turns: string[], projectLink?: string, status: Investigation["status"] = "completed") {
    const record = visitorRecord(turns, status);
    reset = vi.fn();
    vi.spyOn(investigationHook, "useInvestigation").mockReturnValue({
      state: { record, events: [], connection: "closed", busy: false, error: null },
      create: vi.fn(), turn: vi.fn(), cancel: vi.fn(), retry: vi.fn(), reconnect: vi.fn(), reload: vi.fn(), reset,
    });
    vi.spyOn(statusHook, "useSystemStatus").mockReturnValue({
      state: "ready", status: visitorStatus(projectLink), detail: "Ready", refresh: vi.fn(),
    });
    act(() => root.render(<App />));
  }

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    window.history.replaceState({}, "", "/research?investigation=case-visitor-boundary");
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.restoreAllMocks();
    container.remove();
    window.history.replaceState({}, "", "/");
  });

  it("shows concise scope and processing guidance only on the research view", () => {
    renderVisitor(["How unusual was NVIDIA's move?"]);
    const notice = container.querySelector<HTMLElement>("[aria-label='Research use and privacy notice']");
    expect(notice?.textContent).toContain("Historical research demo — not investment advice");
    expect(notice?.textContent).toContain("Do not enter personal or confidential information");
    expect(notice?.textContent).toContain("approved internal inference");
    expect(notice?.textContent).toContain("LangSmith tracing");
  });

  it("renders only the status-provided LangSmith link with safe new-tab attributes", () => {
    renderVisitor(["How unusual was NVIDIA's move?"], PROJECT_LINK);
    const link = container.querySelector<HTMLAnchorElement>("[aria-label='Open LangSmith traces in a new tab']");
    expect(link?.href).toBe(PROJECT_LINK);
    expect(link?.target).toBe("_blank");
    expect(link?.rel).toBe("noopener noreferrer");
    act(() => link?.focus());
    expect(document.activeElement).toBe(link);
  });

  it("omits the LangSmith link when tracing has no configured presentation link", () => {
    renderVisitor(["How unusual was NVIDIA's move?"]);
    expect(container.querySelector("[aria-label='Open LangSmith traces in a new tab']")).toBeNull();
  });

  it("links to the public source repository from the header in a safe new tab", () => {
    renderVisitor(["How unusual was NVIDIA's move?"]);
    const link = container.querySelector<HTMLAnchorElement>("[aria-label='Open the Market Shock demo source code on GitHub in a new tab']");
    expect(link?.href).toBe("https://github.com/pastorsj/market-analysis-demo");
    expect(link?.target).toBe("_blank");
    expect(link?.rel).toBe("noopener noreferrer");
    expect(link?.textContent).toContain("GitHub");
    expect(link?.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("uses the runtime four-turn contract to show remaining capacity", () => {
    renderVisitor(["Initial question"]);
    const input = container.querySelector<HTMLInputElement>("#follow-up");
    expect(input).not.toBeNull();
    expect(container.querySelector(".follow-up [role='status']")?.textContent).toBe("3 turns remaining · 4 max");
    act(() => input?.focus());
    expect(document.activeElement).toBe(input);
    expect(container.querySelector("[data-action='reset-investigation']")?.textContent).toBe("Start new investigation");
    expect(container.textContent).not.toMatch(/delete|erase/i);
  });

  it("does not orphan the sole admitted run by offering reset while research is active", () => {
    renderVisitor(["Initial question"], undefined, "running");
    expect(container.querySelector("[data-action='reset-investigation']")).toBeNull();
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Cancel investigation")).toBe(true);
  });

  it("removes follow-up controls at exhaustion and guides a keyboard-operable new investigation", () => {
    renderVisitor(["Turn one", "Turn two", "Turn three", "Turn four"], undefined, "failed");
    expect(container.querySelector("#follow-up")).toBeNull();
    expect(container.textContent).not.toContain("Retry investigation");
    const limit = container.querySelector<HTMLElement>("[aria-label='Investigation turn limit']");
    expect(limit?.textContent).toContain("All 4 submitted turns have been used");
    const start = limit?.querySelector<HTMLButtonElement>("button");
    expect(start?.textContent).toBe("Start new investigation");
    act(() => start?.focus());
    expect(document.activeElement).toBe(start);
    act(() => start?.click());
    expect(reset).toHaveBeenCalledOnce();
  });
});
