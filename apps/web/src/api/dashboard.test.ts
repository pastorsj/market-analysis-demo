import { afterEach, describe, expect, it, vi } from "vitest";
import { decodeMarketDashboard, getMarketDashboard, type MarketDashboardData } from "./dashboard";

const rows = [
  ["NVDA", "QQQ", 218.29, -0.03, -4.34, 0.77, false],
  ["AMD", "QQQ", 516.13, 2.49, 1.72, 1.18, false],
  ["JPM", "XLF", 356.23, 0.76, 2.04, 0.95, false],
  ["GS", "XLF", 1029.18, 0.92, 3.11, 1.07, false],
  ["SCHW", "XLF", 107.25, -0.07, 0.44, 0.82, false],
] as const;

export const dashboardFixture = (): Record<string, any> => ({
  schema_version: "market-dashboard-v1",
  mode: "historical_reconstruction",
  requested_as_of: "2026-09-11",
  resolved_session: "2026-09-11",
  cutoff_at: "2026-09-11T20:00:00Z",
  selected_ticker: "NVDA",
  coverage: {
    first_session: "2022-01-03",
    last_session: "2026-09-11",
    session_count: 1177,
    scenario_id: "market-shock-v2-test",
    vintage_status: "reconstructed_later",
  },
  watchlist: rows.map(([ticker, benchmark, close, one, five, volume, shock]) => ({
    ticker,
    outcome: "ok",
    resolved_session: "2026-09-11",
    close,
    return_1_session_pct: one,
    return_5_sessions_pct: five,
    opening_gap_pct: one / 2,
    volume_ratio: volume,
    benchmark,
    benchmark_return_pct: 0.31,
    market_adjusted_return_pct: one - 0.31,
    is_shock: shock,
  })),
  series: {
    ticker: "NVDA",
    benchmark: "QQQ",
    points: [
      { session_date: "2026-09-09", ticker_close: 227.6, benchmark_close: 603.2, ticker_normalized_return_pct: 0, benchmark_normalized_return_pct: 0, volume: 188_000_000 },
      { session_date: "2026-09-10", ticker_close: 218.36, benchmark_close: 604.4, ticker_normalized_return_pct: -4.06, benchmark_normalized_return_pct: 0.2, volume: 210_000_000 },
      { session_date: "2026-09-11", ticker_close: 218.29, benchmark_close: 606.1, ticker_normalized_return_pct: -4.09, benchmark_normalized_return_pct: 0.48, volume: 172_000_000 },
    ],
  },
  evidence: [{
    citation_id: "cit-0123456789ab",
    title: "NVIDIA quarterly filing",
    url: "https://investor.nvidia.com/financial-info/sec-filings/default.aspx",
    source_type: "filing",
    published_at: "2026-08-27T12:00:00Z",
    available_at: "2026-08-27T12:05:00Z",
    excerpt: "NVIDIA described demand, supply, and material operating risks in its filing.",
  }],
  receipts: [
    ...rows.map(([ticker]) => ({ operation: "get_price_context", ticker, engine: "cudf", device: "NVIDIA GB10", gpu_executed: true, fallback_used: false, duration_ms: 3.2 })),
    { operation: "market_series", ticker: "NVDA", engine: "cudf", device: "NVIDIA GB10", gpu_executed: true, fallback_used: false, duration_ms: 4.8 },
    { operation: "search_news", ticker: "NVDA", engine: "cuvs", device: "NVIDIA GB10", gpu_executed: true, fallback_used: false, duration_ms: 9.4 },
  ],
  limitations: [{ code: "reconstructed_later_market_data", message: "Market data was reconstructed after the observed session.", affected: ["market_data"] }],
});

const clone = <T>(value: T): T => structuredClone(value);

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("market dashboard boundary", () => {
  it("decodes an exact historical reconstruction with negative and positive metrics", () => {
    const decoded = decodeMarketDashboard(dashboardFixture());
    expect(decoded.schema_version).toBe("market-dashboard-v1");
    expect(decoded.watchlist.map((row) => row.ticker)).toEqual(["NVDA", "AMD", "JPM", "GS", "SCHW"]);
    expect(decoded.watchlist[0].return_5_sessions_pct).toBe(-4.34);
    expect(decoded.series.points).toHaveLength(3);
    expect(decoded.evidence[0].source_type).toBe("filing");
  });

  it("accepts a truthful no-data row without inventing a zero", () => {
    const fixture = dashboardFixture();
    fixture.watchlist[4] = {
      ...fixture.watchlist[4], outcome: "no_data", close: null, return_1_session_pct: null,
      return_5_sessions_pct: null, opening_gap_pct: null, volume_ratio: null,
      benchmark_return_pct: null, market_adjusted_return_pct: null, is_shock: null,
    };
    const decoded = decodeMarketDashboard(fixture);
    expect(decoded.watchlist[4].close).toBeNull();
  });

  it.each([
    ["extra root field", (value: any) => { value.endpoint = "tools"; }],
    ["extra nested field", (value: any) => { value.series.points[0].low = 1; }],
    ["canonical ticker drift", (value: any) => { [value.watchlist[0], value.watchlist[1]] = [value.watchlist[1], value.watchlist[0]]; }],
    ["selection drift", (value: any) => { value.selected_ticker = "AMD"; }],
    ["invalid date", (value: any) => { value.requested_as_of = "2026-02-30"; }],
    ["future resolved session", (value: any) => { value.resolved_session = "2026-09-12"; }],
    ["cutoff mismatch", (value: any) => { value.cutoff_at = "2026-09-10T20:00:00Z"; }],
    ["future evidence", (value: any) => { value.evidence[0].available_at = "2026-09-12T12:00:00Z"; }],
    ["out-of-order series", (value: any) => { value.series.points.reverse(); }],
    ["non-finite metric", (value: any) => { value.watchlist[0].close = Number.NaN; }],
    ["invented no-data metric", (value: any) => { value.watchlist[0].outcome = "no_data"; }],
    ["receipt fallback", (value: any) => { value.receipts[0].fallback_used = true; }],
    ["receipt GPU drift", (value: any) => { value.receipts[0].gpu_executed = false; }],
    ["missing selected series receipt", (value: any) => { value.receipts = value.receipts.filter((item: any) => item.operation !== "market_series"); }],
  ])("rejects %s", (_label, mutate) => {
    const fixture = clone(dashboardFixture());
    mutate(fixture);
    expect(() => decodeMarketDashboard(fixture)).toThrow();
  });

  it.each([
    ["Llama-family content", (value: any) => { value.limitations[0].message = "Uses a hidden Llama model"; }],
    ["internal URL in text", (value: any) => { value.coverage.scenario_id = "http://tools:8000/mcp"; }],
    ["private citation host", (value: any) => { value.evidence[0].url = "https://192.168.1.2/source"; }],
    ["credential query", (value: any) => { value.evidence[0].url = "https://example.com/source?token=secret"; }],
    ["credential-shaped extra field", (value: any) => { value.receipts[0].api_key = "secret"; }],
    ["credential-shaped text", (value: any) => { value.limitations[0].message = "Bearer abcdef-secret-value"; }],
  ])("rejects unsafe data: %s", (_label, mutate) => {
    const fixture = clone(dashboardFixture());
    mutate(fixture);
    expect(() => decodeMarketDashboard(fixture)).toThrow(/invalid|unsafe/i);
  });
});

describe("getMarketDashboard", () => {
  it("fetches only the encoded same-origin endpoint and decodes its body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(dashboardFixture()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const result: MarketDashboardData = await getMarketDashboard("NVDA", "2026-09-11");
    expect(result.selected_ticker).toBe("NVDA");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/dashboard?ticker=NVDA&as_of=2026-09-11",
      expect.objectContaining({ headers: { Accept: "application/json" }, signal: expect.any(AbortSignal) }),
    );
  });

  it("surfaces safe bounded detail and suppresses endpoint-shaped detail", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Historical dashboard is temporarily unavailable." }), { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "http://tools:8000/mcp failed" }), { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getMarketDashboard("NVDA", "2026-09-11")).rejects.toThrow("Historical dashboard is temporarily unavailable.");
    await expect(getMarketDashboard("NVDA", "2026-09-11")).rejects.toThrow("Dashboard unavailable (503)");
  });

  it("propagates caller cancellation and enforces a bounded timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_path: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    })));
    const caller = new AbortController();
    const cancelled = getMarketDashboard("NVDA", "2026-09-11", caller.signal);
    const cancelledAssertion = expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
    caller.abort();
    await cancelledAssertion;

    const timedOut = getMarketDashboard("NVDA", "2026-09-11");
    const timeoutAssertion = expect(timedOut).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(20_000);
    await timeoutAssertion;
  });
});
