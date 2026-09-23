import { afterEach, describe, expect, it, vi } from "vitest";
import { decodeShockEventCatalog, getShockEvents, type ShockEventCatalog } from "./events";

export function eventCatalogFixture(): Record<string, any> {
  const gap = { code: "unsupported_missing_news", layer: "licensed_news", detail: "Licensed historical news is not available in this local snapshot." };
  return {
    schema_version: "shock-event-catalog-v1",
    catalog_id: "shock-event-catalog-v1-test",
    catalog_sha256: "a".repeat(64),
    scenario_id: "market-shock-v2-test",
    scenario_manifest_sha256: "b".repeat(64),
    calendar: "XNYS",
    timezone: "America/New_York",
    supported_tickers: ["AMD", "AVGO", "NVDA"],
    categories: [{ category_id: "ai-competition", label: "AI competition", description: "Competitive developments affecting expectations for compute demand.", sort_order: 1 }],
    events: [{
      event_id: "nvda-repricing-2025-01-27",
      category_id: "ai-competition",
      title: "NVIDIA repricing window",
      summary: "A focused semiconductor repricing window suitable for bounded market and evidence research.",
      sort_order: 1,
      event_session: "2025-01-27", source_dates: ["2025-01-27"],
      primary_ticker: "NVDA",
      analysis_tickers: ["NVDA", "AMD", "AVGO"],
      context_instruments: ["QQQ", "SPY"],
      start_session: "2024-12-24",
      end_session: "2025-02-10",
      default_cutoff: "2025-01-27T21:00:00Z",
      questions: [
        { question_id: "measure-repricing", label: "Measure the repricing", capability: "move-measurement", text: "How unusual was the primary company's move relative to recent history and the market?" },
        { question_id: "review-evidence", label: "Review the evidence", capability: "evidence-review", text: "Which cutoff-safe sources support an explanation, and what remains uncertain?" },
        { question_id: "compare-peers", label: "Compare peers", capability: "peer-comparison", text: "How did related companies behave after accounting for the benchmark backdrop?" },
      ],
      source_requirements: {
        market: { required: true, required_fields: ["adjusted_close", "volume"], price_basis: "provider_adjusted" },
        documents: { required_for_ready: true, requirement_id: "company-primary-source", source_kinds: ["company_release", "filing"] },
        licensed_news: { required_for_publication: false, required_for_ready: true, source_kind: "licensed_news_metadata" },
        derived_features: { required_for_ready: true, features: ["event-returns", "relative-returns", "volume-context", "historical-analogues"] },
      },
      limitations: [{ limitation_id: "reconstructed-market-history", detail: "Market history is reconstructed later and is not an archived-at-cutoff feed." }],
      qualification: {
        status: "partial",
        market: { status: "ready", gaps: [] },
        documents: { status: "ready", gaps: [] },
        licensed_news: { status: "partial", gaps: [gap] },
        derived_features: { status: "ready", gaps: [] },
        gaps: [gap],
      },
    }],
    summary: { declared_events: 1, published_events: 1, ready_events: 0, partial_events: 1, excluded_events: 0 },
  };
}

const clone = <T>(value: T): T => structuredClone(value);

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("shock event catalog boundary", () => {
  it("decodes a deterministic partial catalog without hardcoding event identities", () => {
    const decoded = decodeShockEventCatalog(eventCatalogFixture());
    expect(decoded.schema_version).toBe("shock-event-catalog-v1");
    expect(decoded.events[0].analysis_tickers).toEqual(["NVDA", "AMD", "AVGO"]);
    expect(decoded.events[0].qualification.status).toBe("partial");
    expect(decoded.events[0].questions).toHaveLength(3);
  });

  it("accepts qualification gaps projected in canonical sorted order rather than layer order", () => {
    const value = clone(eventCatalogFixture());
    const derived = { code: "derived_feature_unavailable", layer: "derived_features", detail: "Historical analogue features are not available for this qualified event." };
    value.events[0].qualification.derived_features = { status: "partial", gaps: [derived] };
    value.events[0].qualification.gaps = [derived, ...value.events[0].qualification.gaps];
    expect(decodeShockEventCatalog(value).events[0].qualification.gaps).toHaveLength(2);
  });

  it("rejects duplicate gap projections even when their sorted membership otherwise matches", () => {
    const value = clone(eventCatalogFixture());
    value.events[0].qualification.gaps.push(clone(value.events[0].qualification.gaps[0]));
    expect(() => decodeShockEventCatalog(value)).toThrow(/qualification/i);
  });

  it.each([
    ["extra root field", (value: any) => { value.endpoint = "tools"; }],
    ["extra event field", (value: any) => { value.events[0].expected_answer = "hidden"; }],
    ["bad digest", (value: any) => { value.catalog_sha256 = "abc"; }],
    ["invalid calendar", (value: any) => { value.calendar = "UTC"; }],
    ["unknown category", (value: any) => { value.events[0].category_id = "unpublished"; }],
    ["missing source dates", (value: any) => { delete value.events[0].source_dates; }],
    ["empty source dates", (value: any) => { value.events[0].source_dates = []; }],
    ["primary not first", (value: any) => { value.events[0].analysis_tickers = ["AMD", "NVDA", "AVGO"]; }],
    ["context overlap", (value: any) => { value.events[0].context_instruments[0] = "NVDA"; }],
    ["reversed window", (value: any) => { value.events[0].start_session = "2025-02-11"; }],
    ["cutoff beyond window", (value: any) => { value.events[0].default_cutoff = "2025-02-11T21:00:00Z"; }],
    ["too few questions", (value: any) => { value.events[0].questions.length = 2; }],
    ["duplicate question", (value: any) => { value.events[0].questions[1].question_id = value.events[0].questions[0].question_id; }],
    ["wrong source requirement", (value: any) => { value.events[0].source_requirements.licensed_news.required_for_publication = true; }],
    ["ready layer with gap", (value: any) => { value.events[0].qualification.licensed_news.status = "ready"; }],
    ["blocked published market", (value: any) => { value.events[0].qualification.market.status = "blocked"; value.events[0].qualification.market.gaps = [{ code: "market_blocked", layer: "market", detail: "Required market observations are unavailable for this event window." }]; value.events[0].qualification.gaps.unshift(value.events[0].qualification.market.gaps[0]); }],
    ["ticker derivation drift", (value: any) => { value.supported_tickers.pop(); }],
    ["summary drift", (value: any) => { value.summary.ready_events = 1; }],
  ])("rejects %s", (_label, mutate) => {
    const value = clone(eventCatalogFixture());
    mutate(value);
    expect(() => decodeShockEventCatalog(value)).toThrow();
  });

  it.each([
    ["Llama-family content", (value: any) => { value.events[0].summary = "A hidden Llama model generated this event summary without a qualified source."; }],
    ["internal URL", (value: any) => { value.events[0].limitations[0].detail = "The source came from http://tools:8000/internal and cannot be shown."; }],
    ["credential text", (value: any) => { value.categories[0].description = "Authorization: secret-value should never enter a public event catalog."; }],
  ])("rejects unsafe catalog data: %s", (_label, mutate) => {
    const value = clone(eventCatalogFixture());
    mutate(value);
    expect(() => decodeShockEventCatalog(value)).toThrow(/invalid|unsafe/i);
  });
});

describe("getShockEvents", () => {
  it("fetches only the same-origin catalog and decodes its body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(eventCatalogFixture()), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const result: ShockEventCatalog = await getShockEvents();
    expect(result.events).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/shock-events", expect.objectContaining({ headers: { Accept: "application/json" }, signal: expect.any(AbortSignal) }));
  });

  it("surfaces safe detail but suppresses internal transport detail", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Curated events are temporarily unavailable." }), { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "http://tools:8000 bearer secret-value" }), { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getShockEvents()).rejects.toThrow("Curated events are temporarily unavailable.");
    await expect(getShockEvents()).rejects.toThrow("Event catalog unavailable (503)");
  });

  it("honors caller cancellation and the bounded timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_path: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    })));
    const caller = new AbortController();
    const cancelled = getShockEvents(caller.signal);
    const cancelAssertion = expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
    caller.abort();
    await cancelAssertion;
    const timeout = getShockEvents();
    const timeoutAssertion = expect(timeout).rejects.toThrow("Event catalog request timed out.");
    await vi.advanceTimersByTimeAsync(20_000);
    await timeoutAssertion;
  });
});
