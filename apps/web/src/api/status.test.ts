import { afterEach, describe, expect, it, vi } from "vitest";
import { getSystemStatus } from "./client";
import {
  DRAFT_MODEL,
  LOCAL_MODEL,
  LUNA_MODEL,
  SOL_MODEL,
  decodeSystemStatus,
  type SystemStatus,
} from "./types";

const GENERATION_REVISION = "bee7596271d1495f6992ae224aefde4410e816b8";
const DRAFT_REVISION = "8a0177116d138011e63103110f136ec0ca09ebbf";
const EMBED_MODEL = "nvidia/Nemotron-3-Embed-1B-BF16";
const EMBED_REVISION = "9e0b24858b1195815ecb1188ffa1b73bcea7b30a";
const PROJECT_LINK = "https://smith.langchain.com/o/11111111-1111-1111-1111-111111111111/projects/p/22222222-2222-2222-2222-222222222222";

const localStatus = (): Record<string, unknown> => ({
  schema_version: "system-status-v1",
  service: "agent",
  version: "1.2.0",
  ready: false,
  dependencies: { tools: true, model: true, coverage: true, checkpoint: true, mcp_contract: true, event_catalog: true },
  remote_routing_enabled: false,
  observability: { remote_inference_enabled: false, langsmith_export_enabled: false },
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
    scenario_id: "market-shock-v2-test", scenario_manifest_sha256: "a".repeat(64),
    data_tier: "cc0_reconstruction", vintage_status: "reconstructed_later",
    first_session: "2022-01-03", last_session: "2026-09-11", session_count: 1177,
    document_source_kinds: ["company_release", "filing", "primary_source"],
  },
  limitations: ["reconstructed_later_market_data", "licensed_news_unavailable", "source_coverage_varies_by_ticker_and_cutoff"],
  models: [
    { model_id: LOCAL_MODEL, revision: GENERATION_REVISION, location: "local_model_service", identity_basis: "immutable_revision", roles: ["local_generation", "switchyard_efficient_target"], route_eligible: true, dependency: "model" },
    { model_id: DRAFT_MODEL, revision: DRAFT_REVISION, location: "local_model_service", identity_basis: "immutable_revision", roles: ["speculative_assistant"], route_eligible: false, dependency: "model" },
    { model_id: EMBED_MODEL, revision: EMBED_REVISION, location: "local_tools_service", identity_basis: "immutable_revision", roles: ["retrieval_embedding"], route_eligible: false, dependency: "tools" },
    { model_id: LUNA_MODEL, revision: null, location: "internal_inference_server", identity_basis: "exact_server_route_id", roles: ["switchyard_classifier"], route_eligible: true, dependency: "remote_routing" },
    { model_id: "nvidia/nvidia/nemotron-3-ultra", revision: null, location: "internal_inference_server", identity_basis: "exact_server_route_id", roles: ["switchyard_capable_target", "report_formatter"], route_eligible: true, dependency: "remote_routing" },
  ],
  routes: [{ mode: "switchyard_escalation", enabled: false, disabled_reason: "remote_routing_disabled", model_roles: ["switchyard_classifier", "local_generation", "switchyard_capable_target", "report_formatter"], evidence_tools_unchanged: true }],
  contracts: {
    max_investigation_turns: 4, max_concurrent_investigations: 1,
    data: "market-shock-v2-test", skill: "market-agent-skills/deep-agent-1.0.0",
    prompt: "market-shock-grounded-synthesis/2.0.0", safety: "bounded-equity-research/1.0.0",
    generation_model: LOCAL_MODEL, embedding_model: EMBED_MODEL, embedding_revision: EMBED_REVISION,
  },
});

const clone = <T>(value: T): T => structuredClone(value);

const remoteStatus = (): Record<string, unknown> => {
  const value = localStatus();
  value.remote_routing_enabled = true;
  value.observability = { remote_inference_enabled: true, langsmith_export_enabled: false };
  value.ready = true;
  value.routes = (value.routes as Record<string, unknown>[]).map((route) => ({ ...route, enabled: true, disabled_reason: null }));
  return value;
};

const newsEnabledStatus = (): Record<string, unknown> => {
  const value = remoteStatus();
  (value.coverage as Record<string, unknown>).document_source_kinds = ["company_release", "filing", "licensed_news_metadata", "primary_source"];
  value.limitations = ["reconstructed_later_market_data", "source_coverage_varies_by_ticker_and_cutoff"];
  return value;
};

const tracedStatus = (): Record<string, unknown> => {
  const value = remoteStatus();
  value.observability = {
    remote_inference_enabled: true,
    langsmith_export_enabled: true,
    project_link: PROJECT_LINK,
  };
  return value;
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("system status contract", () => {
  it("decodes an exact disabled escalation capability response", () => {
    const decoded = decodeSystemStatus(localStatus());
    expect(decoded.supported_tickers).toEqual(["NVDA", "AMD", "JPM", "GS", "SCHW"]);
    expect(decoded.models.map((row) => row.model_id)).toEqual([LOCAL_MODEL, DRAFT_MODEL, EMBED_MODEL, LUNA_MODEL, "nvidia/nvidia/nemotron-3-ultra"]);
    expect(decoded.routes.map((row) => [row.mode, row.enabled])).toEqual([["switchyard_escalation", false]]);
    expect(decoded.contracts.max_investigation_turns).toBe(4);
    expect(decoded.contracts.max_concurrent_investigations).toBe(1);
    expect(decoded.observability).toEqual({ remote_inference_enabled: false, langsmith_export_enabled: false });
  });

  it("decodes only an approved LangSmith project presentation link", () => {
    const decoded = decodeSystemStatus(tracedStatus());

    expect(decoded.observability).toEqual({
      remote_inference_enabled: true,
      langsmith_export_enabled: true,
      project_link: PROJECT_LINK,
    });
  });

  it("decodes remote-enabled and dependency-degraded responses without inventing readiness", () => {
    expect(decodeSystemStatus(remoteStatus()).routes.every((route) => route.enabled)).toBe(true);

    const degraded = remoteStatus();
    degraded.ready = false;
    degraded.dependencies = { ...(degraded.dependencies as object), model: false };
    degraded.routes = [{ mode: "switchyard_escalation", enabled: false, disabled_reason: "required_dependencies_unavailable", model_roles: ["switchyard_classifier", "local_generation", "switchyard_capable_target", "report_formatter"], evidence_tools_unchanged: true }];
    const decoded = decodeSystemStatus(degraded);
    expect(decoded.ready).toBe(false);
    expect(decoded.dependencies.model).toBe(false);
    expect(decoded.routes[0].enabled).toBe(false);
  });

  it("decodes the runtime news metadata source kind and keeps Research available", () => {
    const decoded = decodeSystemStatus(newsEnabledStatus());

    expect(decoded.ready).toBe(true);
    expect(decoded.investigations).toBe("available");
    expect(decoded.coverage.document_source_kinds).toContain("licensed_news_metadata");
    expect(decoded.limitations).not.toContain("licensed_news_unavailable");
  });

  it.each([
    ["missing root field", (value: any) => { delete value.ready; }],
    ["extra root field", (value: any) => { value.extra = true; }],
    ["invalid date", (value: any) => { value.coverage.first_session = "2022-02-30"; }],
    ["reversed dates", (value: any) => { value.coverage.first_session = "2027-01-01"; }],
    ["fractional count", (value: any) => { value.coverage.session_count = 1.5; }],
    ["duplicate ticker", (value: any) => { value.supported_tickers[4] = "NVDA"; }],
    ["unknown ticker", (value: any) => { value.companies[4].symbol = "MSFT"; }],
    ["legacy news source alias", (value: any) => { value.coverage.document_source_kinds.splice(2, 0, "licensed_news"); }],
    ["unknown source kind", (value: any) => { value.coverage.document_source_kinds.push("social_media"); }],
    ["unknown route", (value: any) => { value.routes[0].mode = "automatic"; }],
    ["role drift", (value: any) => { value.models[0].roles = ["frontier_answer"]; }],
    ["location drift", (value: any) => { value.models[2].location = "internal_inference_server"; }],
    ["route availability drift", (value: any) => { value.routes[0].enabled = true; value.routes[0].disabled_reason = null; }],
    ["limitation drift", (value: any) => { value.limitations = []; }],
    ["contract drift", (value: any) => { value.contracts.generation_model = SOL_MODEL; }],
    ["turn limit drift", (value: any) => { value.contracts.max_investigation_turns = 5; }],
    ["concurrency limit drift", (value: any) => { value.contracts.max_concurrent_investigations = 2; }],
    ["remote inference inconsistency", (value: any) => { value.observability.remote_inference_enabled = true; }],
    ["export without project link", (value: any) => { value.observability.langsmith_export_enabled = true; }],
    ["link while export disabled", (value: any) => { value.observability.project_link = PROJECT_LINK; }],
    ["retired capable selection", (value: any) => { value.models[4].model_id = SOL_MODEL; }],
  ])("rejects %s", (_label, mutate) => {
    const value = clone(localStatus());
    mutate(value);
    expect(() => decodeSystemStatus(value)).toThrow();
  });

  it.each([
    ["Llama-family value", (value: any) => { value.models[0].model_id = "vendor/llama-hidden"; }],
    ["URL-shaped value", (value: any) => { value.coverage.scenario_id = "https://internal.invalid"; }],
    ["credential-shaped field", (value: any) => { value.models[0].api_key = "do-not-render"; }],
    ["endpoint-shaped field", (value: any) => { value.routes[0].endpoint = "internal"; }],
    ["wrong project host", (value: any) => { value.observability = { remote_inference_enabled: true, langsmith_export_enabled: true, project_link: "https://evil.example/o/a/projects/p/b" }; value.remote_routing_enabled = true; value.ready = true; value.routes[0].enabled = true; value.routes[0].disabled_reason = null; }],
    ["project URL query", (value: any) => { value.observability = { remote_inference_enabled: true, langsmith_export_enabled: true, project_link: `${PROJECT_LINK}?token=secret` }; value.remote_routing_enabled = true; value.ready = true; value.routes[0].enabled = true; value.routes[0].disabled_reason = null; }],
  ])("rejects unsafe status data: %s", (_label, mutate) => {
    const value = clone(localStatus());
    mutate(value);
    expect(() => decodeSystemStatus(value)).toThrow(/status|unsafe|invalid/i);
  });
});

describe("getSystemStatus", () => {
  it("fetches same-origin news-enabled status and keeps Research available", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(newsEnabledStatus()), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const result: SystemStatus = await getSystemStatus();
    expect(result.schema_version).toBe("system-status-v1");
    expect(result.investigations).toBe("available");
    expect(result.coverage.document_source_kinds).toContain("licensed_news_metadata");
    expect(fetchMock).toHaveBeenCalledWith("/api/status", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("surfaces bounded HTTP detail and rejects malformed success bodies", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Status unavailable" }), { status: 503 })).mockResolvedValueOnce(new Response(JSON.stringify({ ready: true }), { status: 200 })));
    await expect(getSystemStatus()).rejects.toThrow("Status unavailable");
    await expect(getSystemStatus()).rejects.toThrow(/status|fields|invalid/i);
  });

  it("aborts a status request after the bounded timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_path: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    })));
    const pending = getSystemStatus();
    const rejection = expect(pending).rejects.toThrow("The server took too long to respond.");
    await vi.advanceTimersByTimeAsync(15_000);
    await rejection;
  });
});
