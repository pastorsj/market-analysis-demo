/** @vitest-environment jsdom */

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getShockEvents, type ShockEvent, type ShockEventCatalog } from "../api/events";
import { EventExplorer } from "./EventExplorer";

vi.mock("../api/events", async (load) => ({
  ...await load<typeof import("../api/events")>(),
  getShockEvents: vi.fn(),
}));

const newsGap = {
  code: "unsupported_missing_news",
  layer: "licensed_news" as const,
  detail: "Licensed historical news is not present in the current local data tier.",
};

function shockEvent(overrides: Partial<ShockEvent> = {}): ShockEvent {
  return {
    event_id: "nvda-repricing-2025-01-27",
    category_id: "ai-competition",
    title: "NVIDIA repricing window",
    summary: "A focused semiconductor repricing window for measured, point-in-time market research.",
    sort_order: 1,
    event_session: "2025-01-27", source_dates: ["2025-01-27"],
    primary_ticker: "NVDA",
    analysis_tickers: ["NVDA", "AMD", "AVGO"],
    context_instruments: ["QQQ", "SPY"],
    start_session: "2024-12-24",
    end_session: "2025-02-10",
    default_cutoff: "2025-01-27T21:00:00Z",
    questions: [
      { question_id: "measure-repricing", label: "Measure the repricing", capability: "move-measurement", text: "How unusual was NVIDIA's move relative to recent history and the market?" },
      { question_id: "review-evidence", label: "Review the evidence", capability: "evidence-review", text: "Which cutoff-safe sources support an explanation, and what remains uncertain?" },
      { question_id: "compare-peers", label: "Compare peers", capability: "peer-comparison", text: "How did related companies behave after accounting for the benchmark backdrop?" },
    ],
    source_requirements: {
      market: { required: true, required_fields: ["adjusted_close", "volume"], price_basis: "provider_adjusted" },
      documents: { required_for_ready: true, requirement_id: "company-primary-source", source_kinds: ["company_release"] },
      licensed_news: { required_for_publication: false, required_for_ready: true, source_kind: "licensed_news_metadata" },
      derived_features: { required_for_ready: true, features: ["event-returns", "relative-returns", "volume-context", "historical-analogues"] },
    },
    limitations: [{ limitation_id: "reconstructed-market-history", detail: "Market history is reconstructed later and is not an archived-at-cutoff feed." }],
    qualification: {
      status: "partial",
      market: { status: "ready", gaps: [] },
      documents: { status: "ready", gaps: [] },
      licensed_news: { status: "partial", gaps: [newsGap] },
      derived_features: { status: "ready", gaps: [] },
      gaps: [newsGap],
    },
    ...overrides,
  };
}

function catalog(): ShockEventCatalog {
  const second = shockEvent({
    event_id: "jpm-results-2025-01-15",
    category_id: "earnings-guidance",
    title: "JPMorgan earnings repricing",
    summary: "A bank earnings window with qualified market measurements and explicit evidence limits.",
    sort_order: 2,
    event_session: "2025-01-15", source_dates: ["2025-01-15"],
    primary_ticker: "JPM",
    analysis_tickers: ["JPM", "GS"],
    context_instruments: ["XLF", "SPY"],
    start_session: "2024-12-10",
    end_session: "2025-01-29",
    default_cutoff: "2025-01-15T21:00:00Z",
  });
  return {
    schema_version: "shock-event-catalog-v1",
    catalog_id: "shock-event-catalog-v1-test",
    catalog_sha256: "a".repeat(64),
    scenario_id: "market-shock-v2-test",
    scenario_manifest_sha256: "b".repeat(64),
    calendar: "XNYS",
    timezone: "America/New_York",
    supported_tickers: ["AMD", "AVGO", "GS", "JPM", "NVDA"],
    categories: [
      { category_id: "ai-competition", label: "AI competition", description: "Competitive developments affecting expectations for compute demand.", sort_order: 1 },
      { category_id: "earnings-guidance", label: "Earnings and guidance", description: "Company results and outlook updates associated with repricing windows.", sort_order: 2 },
    ],
    events: [shockEvent(), second],
    summary: { declared_events: 2, published_events: 2, ready_events: 0, partial_events: 2, excluded_events: 0 },
  };
}

describe("event-first explorer", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    vi.mocked(getShockEvents).mockReset();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  async function render(initialSelected: ShockEvent | null = null) {
    const selections: Array<ShockEvent | null> = [];
    function Harness() {
      const [selected, setSelected] = useState<ShockEvent | null>(initialSelected);
      return <EventExplorer
        selectedEvent={selected}
        availableTickers={["NVDA", "AMD", "JPM", "GS", "SCHW"]}
        coverage={{ first_session: "2022-01-03", last_session: "2026-09-11" }}
        onSelectEvent={(event) => { selections.push(event); setSelected(event); }}
      />;
    }
    await act(async () => root.render(<Harness />));
    await act(async () => { await Promise.resolve(); });
    return { selections };
  }

  it("renders catalog-driven categories and filters event cards", async () => {
    vi.mocked(getShockEvents).mockResolvedValue(catalog());
    await render();
    expect(container.textContent).toContain("Choose a known market shock");
    expect(container.textContent).toContain("2Published events");
    expect(container.textContent).toContain("NVIDIA repricing window");
    expect(container.textContent).toContain("JPMorgan earnings repricing");
    const earnings = [...container.querySelectorAll<HTMLButtonElement>(".event-category-filters button")].find((button) => button.textContent?.includes("Earnings and guidance"))!;
    await act(async () => earnings.click());
    expect(container.textContent).not.toContain("NVIDIA repricing window");
    expect(container.textContent).toContain("JPMorgan earnings repricing");
    expect(earnings.getAttribute("aria-pressed")).toBe("true");
  });

  it("selects one scenario at a time for the Research form", async () => {
    vi.mocked(getShockEvents).mockResolvedValue(catalog());
    const { selections } = await render();
    const card = [...container.querySelectorAll<HTMLButtonElement>(".event-card-grid button")].find((button) => button.textContent?.includes("NVIDIA repricing window"))!;
    expect(card.textContent).toContain("Set up scenario →");
    await act(async () => card.click());
    expect(selections.at(-1)?.event_id).toBe("nvda-repricing-2025-01-27");
    expect(card.getAttribute("aria-pressed")).toBe("true");
    expect(card.textContent).toContain("Ready to investigate ✓");

    const jpm = [...container.querySelectorAll<HTMLButtonElement>(".event-card-grid button")].find((button) => button.textContent?.includes("JPMorgan earnings repricing"))!;
    await act(async () => jpm.click());
    expect(selections.at(-1)?.event_id).toBe("jpm-results-2025-01-15");
    expect(card.getAttribute("aria-pressed")).toBe("false");
    expect(jpm.getAttribute("aria-pressed")).toBe("true");
  });

  it("clears a selected scenario when a refreshed catalog no longer contains it", async () => {
    const value = catalog();
    vi.mocked(getShockEvents).mockResolvedValue({ ...value, events: [value.events[1]], summary: { ...value.summary, published_events: 1 } });
    const { selections } = await render(shockEvent());
    expect(selections.at(-1)).toBeNull();
  });

  it("keeps free-text research available when the catalog fails and retries it", async () => {
    vi.mocked(getShockEvents).mockRejectedValueOnce(new Error("Catalog contract unavailable.")).mockResolvedValueOnce(catalog());
    await render();
    expect(container.querySelector("[role='alert']")?.textContent).toContain("Catalog contract unavailable.");
    expect(container.querySelector("[role='alert']")?.textContent).toContain("Free-text research remains available below.");
    await act(async () => container.querySelector<HTMLButtonElement>(".event-catalog-error button")!.click());
    await act(async () => { await Promise.resolve(); });
    expect(getShockEvents).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("NVIDIA repricing window");
  });

  it("disables events whose primary ticker is outside runtime coverage", async () => {
    const value = catalog();
    vi.mocked(getShockEvents).mockResolvedValue({ ...value, events: [shockEvent({ primary_ticker: "AVGO", analysis_tickers: ["AVGO", "NVDA", "AMD"] })] });
    await render();
    const card = container.querySelector<HTMLButtonElement>(".event-card-grid button")!;
    expect(card.disabled).toBe(true);
    expect(card.textContent).toContain("Unavailable in this runtime");
  });
});
