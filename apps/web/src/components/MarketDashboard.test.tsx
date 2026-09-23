/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getMarketDashboard, type MarketDashboardData } from "../api/dashboard";
import type { SystemStatus } from "../api/types";
import { MarketDashboard } from "./MarketDashboard";

vi.mock("../api/dashboard", async (load) => ({
  ...await load<typeof import("../api/dashboard")>(),
  getMarketDashboard: vi.fn(),
}));

const status = {
  ready: true,
  supported_tickers: ["NVDA", "AMD", "JPM", "GS", "SCHW"],
  companies: [
    { symbol: "NVDA", display_name: "NVIDIA" },
    { symbol: "AMD", display_name: "Advanced Micro Devices" },
    { symbol: "JPM", display_name: "JPMorgan Chase" },
    { symbol: "GS", display_name: "Goldman Sachs" },
    { symbol: "SCHW", display_name: "Charles Schwab" },
  ],
  coverage: {
    first_session: "2022-01-03", last_session: "2026-09-11", session_count: 1177,
    scenario_id: "market-shock-v2-test", scenario_manifest_sha256: "a".repeat(64),
    data_tier: "cc0_reconstruction", vintage_status: "reconstructed_later",
    document_source_kinds: ["company_release", "filing", "primary_source"],
  },
} as unknown as SystemStatus;

const watchlist: MarketDashboardData["watchlist"] = [
  { ticker: "NVDA", outcome: "ok", resolved_session: "2026-09-11", close: 218.29, return_1_session_pct: -0.03, return_5_sessions_pct: -4.34, opening_gap_pct: -0.2, volume_ratio: 0.77, benchmark: "QQQ", benchmark_return_pct: 0.31, market_adjusted_return_pct: -0.34, is_shock: false },
  { ticker: "AMD", outcome: "ok", resolved_session: "2026-09-11", close: 516.13, return_1_session_pct: 2.49, return_5_sessions_pct: 1.72, opening_gap_pct: 0.8, volume_ratio: 1.18, benchmark: "QQQ", benchmark_return_pct: 0.31, market_adjusted_return_pct: 2.18, is_shock: false },
  { ticker: "JPM", outcome: "ok", resolved_session: "2026-09-11", close: 356.23, return_1_session_pct: 0.76, return_5_sessions_pct: 2.04, opening_gap_pct: 0.1, volume_ratio: 0.95, benchmark: "XLF", benchmark_return_pct: 0.42, market_adjusted_return_pct: 0.34, is_shock: false },
  { ticker: "GS", outcome: "ok", resolved_session: "2026-09-11", close: 1029.18, return_1_session_pct: 0.92, return_5_sessions_pct: 3.11, opening_gap_pct: 0.3, volume_ratio: 1.07, benchmark: "XLF", benchmark_return_pct: 0.42, market_adjusted_return_pct: 0.5, is_shock: false },
  { ticker: "SCHW", outcome: "no_data", resolved_session: "2026-09-11", close: null, return_1_session_pct: null, return_5_sessions_pct: null, opening_gap_pct: null, volume_ratio: null, benchmark: "XLF", benchmark_return_pct: null, market_adjusted_return_pct: null, is_shock: null },
];

function dashboard(selected: "NVDA" | "AMD" = "NVDA"): MarketDashboardData {
  const benchmark = selected === "NVDA" || selected === "AMD" ? "QQQ" : "XLF";
  return {
    schema_version: "market-dashboard-v1",
    mode: "historical_reconstruction",
    requested_as_of: "2026-09-11",
    resolved_session: "2026-09-11",
    cutoff_at: "2026-09-11T20:00:00Z",
    selected_ticker: selected,
    coverage: { first_session: "2022-01-03", last_session: "2026-09-11", session_count: 1177, scenario_id: "market-shock-v2-test", vintage_status: "reconstructed_later" },
    watchlist,
    series: {
      ticker: selected,
      benchmark,
      points: [
        { session_date: "2026-09-09", ticker_close: 227.6, benchmark_close: 603.2, ticker_normalized_return_pct: 0, benchmark_normalized_return_pct: 0, volume: 188_000_000 },
        { session_date: "2026-09-10", ticker_close: 218.36, benchmark_close: 604.4, ticker_normalized_return_pct: -4.06, benchmark_normalized_return_pct: 0.2, volume: 210_000_000 },
        { session_date: "2026-09-11", ticker_close: 218.29, benchmark_close: 606.1, ticker_normalized_return_pct: -4.09, benchmark_normalized_return_pct: 0.48, volume: 172_000_000 },
      ],
    },
    evidence: [{
      citation_id: "cit-0123456789ab", title: `${selected} quarterly filing`, url: "https://example.com/filing",
      source_type: "filing", published_at: "2026-08-27T12:00:00Z", available_at: "2026-08-27T12:05:00Z",
      excerpt: "The company described demand, supply, and material operating risks in its filing.",
    }],
    receipts: [{ operation: "market_series", ticker: selected, engine: "cudf", device: "NVIDIA GB10", gpu_executed: true, fallback_used: false, duration_ms: 4.8 }],
    limitations: [{ code: "reconstructed_later_market_data", message: "Market data was reconstructed after the observed session.", affected: ["market_data"] }],
  };
}

describe("personal market dashboard", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    vi.mocked(getMarketDashboard).mockReset();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  async function render(props: Partial<Parameters<typeof MarketDashboard>[0]> = {}) {
    const onOpenResearch = vi.fn();
    const onOpenTechnology = vi.fn();
    await act(async () => {
      root.render(<MarketDashboard status={status} onOpenResearch={onOpenResearch} onOpenTechnology={onOpenTechnology} {...props} />);
    });
    await act(async () => { await Promise.resolve(); });
    return { onOpenResearch, onOpenTechnology };
  }

  it("renders real values, truthful freshness, live view, and missing data without dashboard chrome", async () => {
    vi.mocked(getMarketDashboard).mockResolvedValue(dashboard());
    await render();
    expect(container.textContent).toContain("Market now");
    expect(container.textContent).toContain("Cboe via TradingView");
    expect(container.textContent).toContain("Current market context may be delayed");
    expect(container.textContent).not.toContain("Research runtime ready");
    expect(container.textContent).not.toContain("Research known shocks");
    expect(container.textContent).toContain("NVIDIA");
    expect(container.textContent).toContain("Advanced Micro Devices");
    expect(container.textContent).toContain("JPMorgan Chase");
    expect(container.textContent).toContain("Goldman Sachs");
    expect(container.textContent).toContain("Charles Schwab");
    expect(container.textContent).toContain("$218.29");
    expect(container.textContent).toContain("-4.34%");
    expect(container.textContent).toContain("Unavailable");
    expect(container.textContent).toContain("Company evidence");
    expect(container.textContent).toContain("not a licensed live-news feed");
    expect(container.querySelector("script[data-market-widget='chart']")).not.toBeNull();
    expect(container.querySelector("[aria-label='Dashboard evidence cutoff']")).toBeNull();
    expect(container.textContent).toContain("Sep 11, 2026");
  });

  it("switches manually between live market context and cutoff-qualified history", async () => {
    vi.mocked(getMarketDashboard).mockResolvedValue(dashboard());
    await render();

    const viewButtons = [...container.querySelectorAll<HTMLButtonElement>("[aria-label='Market chart view'] button")];
    const live = viewButtons.find((button) => button.textContent?.includes("Market now"))!;
    const historical = viewButtons.find((button) => button.textContent?.includes("Historical research"))!;
    expect(live.getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector("script[data-market-widget='chart']")).not.toBeNull();

    await act(async () => historical.click());
    expect(historical.getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector("script[data-market-widget='chart']")).toBeNull();
    expect(container.querySelector("svg[role='img']")).not.toBeNull();
    expect(container.querySelector("svg desc")?.textContent).toContain("QQQ");
    expect(container.textContent).toContain("Adjusted historical observations, not a live quote");

    await act(async () => live.click());
    expect(live.getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector("script[data-market-widget='chart']")).not.toBeNull();
  });

  it("falls back to history with a human-readable status when the live chart script fails", async () => {
    vi.mocked(getMarketDashboard).mockResolvedValue(dashboard());
    await render();

    const chartScript = container.querySelector<HTMLScriptElement>("script[data-market-widget='chart']")!;
    await act(async () => chartScript.dispatchEvent(new Event("error")));

    expect(container.querySelector("script[data-market-widget='chart']")).toBeNull();
    expect(container.querySelector("svg[role='img']")).not.toBeNull();
    expect([...container.querySelectorAll("[role='status']")]
      .find((node) => node.textContent?.includes("Live market unavailable"))?.textContent)
      .toBe("Live market unavailable — historical snapshot shown.");
    const historical = [...container.querySelectorAll<HTMLButtonElement>("[aria-label='Market chart view'] button")]
      .find((button) => button.textContent?.includes("Historical research"))!;
    expect(historical.getAttribute("aria-pressed")).toBe("true");
  });

  it("changes company through an abortable refetch", async () => {
    vi.mocked(getMarketDashboard).mockResolvedValueOnce(dashboard()).mockResolvedValueOnce(dashboard("AMD"));
    await render();
    const amd = [...container.querySelectorAll<HTMLButtonElement>(".watchlist-rows button")].find((button) => button.textContent?.includes("AMD"))!;
    await act(async () => amd.click());
    await act(async () => { await Promise.resolve(); });
    expect((vi.mocked(getMarketDashboard).mock.calls[0][2] as AbortSignal).aborted).toBe(true);
    expect(getMarketDashboard).toHaveBeenNthCalledWith(2, "AMD", "2026-09-11", expect.any(AbortSignal));
    expect(container.querySelector("#company-title")?.textContent).toContain("Advanced Micro Devices");
  });

  it("keeps scenarios off the dashboard and hands off to Research explicitly", async () => {
    vi.mocked(getMarketDashboard).mockResolvedValueOnce(dashboard());
    const { onOpenResearch, onOpenTechnology } = await render();
    expect(container.querySelector(".event-explorer")).toBeNull();
    expect(container.textContent).not.toContain("Choose a known market shock");
    expect(container.querySelector(".dashboard-research-link")).toBeNull();
    expect(container.textContent).not.toContain("Research known shocks");

    await act(async () => container.querySelector<HTMLButtonElement>(".market-research-handoff button")!.click());
    expect(onOpenResearch).toHaveBeenCalledOnce();
    expect(onOpenResearch).toHaveBeenCalledWith({ ticker: "NVDA", asOf: "2026-09-11" });
    await act(async () => container.querySelector<HTMLButtonElement>(".technology-link")!.click());
    expect(onOpenTechnology).toHaveBeenCalledTimes(1);
  });

  it("surfaces a bounded failure and retries the snapshot", async () => {
    vi.mocked(getMarketDashboard).mockRejectedValueOnce(new Error("Historical dashboard is temporarily unavailable.")).mockResolvedValueOnce(dashboard());
    await render();
    expect(container.querySelector("[role='alert']")?.textContent).toContain("Historical dashboard is temporarily unavailable.");
    await act(async () => container.querySelector<HTMLButtonElement>(".dashboard-error button")!.click());
    await act(async () => { await Promise.resolve(); });
    expect(getMarketDashboard).toHaveBeenCalledTimes(2);
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(container.textContent).toContain("Company evidence");
  });

  it("shows a skeleton without status and an honest empty evidence state", async () => {
    vi.mocked(getMarketDashboard).mockResolvedValue({ ...dashboard(), evidence: [] });
    await render({ status: null });
    expect([...container.querySelectorAll("[role='status']")].some((node) => node.textContent?.includes("Loading verified market context"))).toBe(true);
    expect(getMarketDashboard).not.toHaveBeenCalled();

    await act(async () => root.render(<MarketDashboard status={status} onOpenResearch={() => undefined} onOpenTechnology={() => undefined} />));
    await act(async () => { await Promise.resolve(); });
    expect(container.textContent).not.toContain("Research runtime degraded");
    expect(container.textContent).toContain("No company evidence returned");
  });
});
