/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Dashboard as DashboardData } from "../api/types";
import { byLabel, byRole, change, click, flush, jsonResponse, mockFetch, render, status, type Rendered } from "../test-utils";
import { Dashboard } from "./Dashboard";

const payload = (ticker: string, asOf: string): DashboardData => ({
  requested_as_of: asOf, resolved_session: asOf, cutoff_at: `${asOf}T20:00:00Z`, selected_ticker: ticker,
  coverage: { scenario_id: "scenario-1", first_session: "2024-01-02", last_session: "2024-06-28" },
  watchlist: ["AAA", "BBB"].map((symbol) => ({
    ticker: symbol, available: true, close: 100, return_1d_pct: symbol === "AAA" ? -8 : 1, return_5d_pct: 2, opening_gap_pct: -1,
    volume_ratio: 3, benchmark: "SPY", benchmark_return_1d_pct: 0.2, benchmark_relative_return_pp: symbol === "AAA" ? -8.2 : 0.8, is_shock: symbol === "AAA",
  })),
  series: { ticker, benchmark: "SPY", points: [
    { session_date: "2024-06-27", ticker_close: 100, benchmark_close: 500, ticker_return_pct: 0, benchmark_return_pct: 0, volume: 1000 },
    { session_date: asOf, ticker_close: 92, benchmark_close: 501, ticker_return_pct: -8, benchmark_return_pct: 0.2, volume: 3000 },
  ] },
  documents: [{ title: "AAA 8-K", url: "https://example.test/8k", source_type: "filing", content_scope: "metadata", available_at: "2024-06-27T21:00:00Z", excerpt: "Guidance." }],
  receipt: { engine: "cudf", device: "NVIDIA GB10", duration_ms: 4 },
  limitations: ["Prices are reconstructed later."],
});

let view: Rendered;
afterEach(() => { view.unmount(); vi.unstubAllGlobals(); });

describe("Dashboard", () => {
  it("fetches only when the ticker or date changes", async () => {
    const fetchMock = mockFetch((url) => {
      const query = new URL(url, "http://local").searchParams;
      return jsonResponse(payload(query.get("ticker")!, query.get("as_of")!));
    });
    const first = status();
    const props = { onOpenResearch: vi.fn(), onOpenTechnology: vi.fn() };
    view = await render(<Dashboard companies={first.companies} coverage={first.coverage} {...props} />);
    await flush();
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/dashboard?ticker=AAA&as_of=2024-06-28"]);
    expect(view.container.textContent).toContain("cudf on NVIDIA GB10");
    expect(view.container.textContent).toContain("Unusual move detected");

    const polled = status();
    await view.rerender(<Dashboard companies={polled.companies} coverage={polled.coverage} {...props} />);
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await click(byRole(view.container, "button", /^BBB/));
    await flush();
    await change(byLabel(view.container, /As of/), "2024-03-15");
    await flush();
    expect(fetchMock.mock.calls.map(([url]) => url).slice(1)).toEqual([
      "/api/dashboard?ticker=BBB&as_of=2024-06-28",
      "/api/dashboard?ticker=BBB&as_of=2024-03-15",
    ]);

    await click(byRole(view.container, "button", /Research BBB/));
    expect(props.onOpenResearch).toHaveBeenCalledWith({ ticker: "BBB", asOf: "2024-03-15" });
  });

  it("shows the server's error and retries on request", async () => {
    let calls = 0;
    mockFetch(() => (calls += 1) === 1 ? jsonResponse({ detail: "The tools service returned no dashboard." }, 502) : jsonResponse(payload("AAA", "2024-06-28")));
    const current = status();
    view = await render(<Dashboard companies={current.companies} coverage={current.coverage} onOpenResearch={vi.fn()} onOpenTechnology={vi.fn()} />);
    await flush();
    expect(byRole(view.container, "alert")?.textContent).toContain("The tools service returned no dashboard.");
    await click(byRole(view.container, "button", "Retry snapshot"));
    await flush();
    expect(byRole(view.container, "alert")).toBeNull();
    expect(view.container.textContent).toContain("Alpha Corp");
  });
});
