/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { InvestigationSeed } from "./components/InvestigationForm";

vi.mock("./hooks/useInvestigation", () => ({
  useInvestigation: () => ({
    state: { record: null, events: [], connection: "closed", busy: false, error: null },
    create: vi.fn(), turn: vi.fn(), cancel: vi.fn(), retry: vi.fn(), reconnect: vi.fn(), reload: vi.fn(), reset: vi.fn(),
  }),
}));

vi.mock("./hooks/useSystemStatus", () => ({
  useSystemStatus: () => ({ state: "ready", status: null, detail: "Ready", refresh: vi.fn() }),
}));

vi.mock("./components/MarketDashboard", () => ({
  MarketDashboard: ({ onOpenResearch }: { onOpenResearch: (seed: { ticker: "GS"; asOf: string }) => void }) => (
    <button type="button" data-testid="dashboard-handoff" onClick={() => onOpenResearch({ ticker: "GS", asOf: "2025-04-07" })}>
      Research GS
    </button>
  ),
}));

vi.mock("./components/InvestigationForm", () => ({
  InvestigationForm: ({ initialSeed }: { initialSeed: InvestigationSeed | null }) => (
    <section
      data-testid="research-seed"
      data-ticker={initialSeed?.ticker ?? ""}
      data-cutoff={initialSeed?.as_of ?? ""}
      data-question={initialSeed?.question ?? "missing"}
      data-event={initialSeed?.event === null ? "none" : "unexpected"}
    />
  ),
}));

vi.mock("./components/EventExplorer", () => ({ EventExplorer: () => null }));
vi.mock("./components/PartnershipPage", () => ({ PartnershipPage: () => null }));

import App from "./App";

describe("dashboard to Research handoff", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    window.history.replaceState({}, "", "/");
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    window.history.replaceState({}, "", "/");
    vi.restoreAllMocks();
  });

  it("preserves the exact dashboard ticker and resolved cutoff as the Research seed", async () => {
    await act(async () => root.render(<App />));
    await act(async () => container.querySelector<HTMLButtonElement>("[data-testid='dashboard-handoff']")!.click());

    const seed = container.querySelector<HTMLElement>("[data-testid='research-seed']")!;
    expect(window.location.pathname).toBe("/research");
    expect({
      ticker: seed.dataset.ticker,
      as_of: seed.dataset.cutoff,
      question: seed.dataset.question,
      event: seed.dataset.event,
    }).toEqual({ ticker: "GS", as_of: "2025-04-07", question: "", event: "none" });
  });
});
