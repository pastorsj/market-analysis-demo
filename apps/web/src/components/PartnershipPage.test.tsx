/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  DRAFT_MODEL,
  EMBED_MODEL,
  LOCAL_MODEL,
  LUNA_MODEL,
  CAPABLE_MODEL,
  type SystemStatus,
} from "../api/types";
import { PartnershipPage } from "./PartnershipPage";

const REVISION = "a".repeat(40);
const status: SystemStatus = {
  schema_version: "system-status-v1",
  service: "agent",
  version: "1.2.0",
  ready: true,
  dependencies: { tools: true, model: true, coverage: true, checkpoint: true, mcp_contract: true, event_catalog: true },
  remote_routing_enabled: true,
  observability: { remote_inference_enabled: true, langsmith_export_enabled: true, project_link: "https://smith.langchain.com/o/test/projects/p/demo" },
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
  limitations: ["reconstructed_later_market_data", "source_coverage_varies_by_ticker_and_cutoff"],
  models: [
    { model_id: LOCAL_MODEL, revision: REVISION, location: "local_model_service", identity_basis: "immutable_revision", roles: ["local_generation", "switchyard_efficient_target"], route_eligible: true, dependency: "model" },
    { model_id: DRAFT_MODEL, revision: REVISION, location: "local_model_service", identity_basis: "immutable_revision", roles: ["speculative_assistant"], route_eligible: false, dependency: "model" },
    { model_id: EMBED_MODEL, revision: REVISION, location: "local_tools_service", identity_basis: "immutable_revision", roles: ["retrieval_embedding"], route_eligible: false, dependency: "tools" },
    { model_id: LUNA_MODEL, revision: null, location: "internal_inference_server", identity_basis: "exact_server_route_id", roles: ["switchyard_classifier"], route_eligible: true, dependency: "remote_routing" },
    { model_id: CAPABLE_MODEL, revision: null, location: "internal_inference_server", identity_basis: "exact_server_route_id", roles: ["switchyard_capable_target", "report_formatter"], route_eligible: true, dependency: "remote_routing" },
  ],
  routes: [{ mode: "switchyard_escalation", enabled: true, disabled_reason: null, model_roles: ["switchyard_classifier", "local_generation", "switchyard_capable_target", "report_formatter"], evidence_tools_unchanged: true }],
  contracts: { max_investigation_turns: 4, max_concurrent_investigations: 1, data: "market-shock-v2-test", skill: "market-agent-skills/deep-agent-1.0.0", prompt: "market-shock-grounded-synthesis/2.0.0", safety: "bounded-equity-research/1.0.0", generation_model: LOCAL_MODEL, embedding_model: EMBED_MODEL, embedding_revision: REVISION },
};

describe("Built Together partnership story", () => {
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
  });

  async function render(currentStatus: SystemStatus | null = status) {
    await act(async () => root.render(
      <PartnershipPage status={currentStatus} />,
    ));
  }

  const button = (name: string) => [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes(name))!;

  it("puts the plain-language story before the product details", async () => {
    await render();
    expect(container.querySelector("h1")?.textContent).toBe("A financial research agent built with LangChain and NVIDIA.");
    expect([...container.querySelectorAll(".story-stages h3")].map((item) => item.textContent)).toEqual([
      "The application prepares the investigation.",
      "The agent runs locally and escalates when needed.",
      "The agent uses approved data and tools.",
      "Each run helps improve the next version.",
    ]);
    expect(container.querySelector(".story-outcomes")?.textContent).toContain("Lower latency improves two feedback loops.");
    expect(container.querySelector(".story-outcomes")?.textContent).toContain("An answer arrives sooner");
    expect(container.querySelector(".story-outcomes")?.textContent).toContain("more experiments and evaluation cycles");
    expect(container.textContent).toContain("Next, not deployed: NeMo Platform Insights + Eval Author");
    expect(container.textContent).not.toMatch(/Compound|Two speed dividends|AI\. Accelerated|Agents\. Orchestrated/i);

    expect([...container.querySelectorAll(".story-stage-copy strong")].map((item) => item.textContent)).toEqual([
      "Deep Agents", "LangGraph", "Nemotron 3.5 Lightning", "Luna", "Switchyard", "Nemotron 3 Ultra",
      "Nemotron 3 Embed", "CUDA-X", "RAPIDS", "OpenShell", "NeMo Relay", "LangSmith", "NeMo Platform",
    ]);

    const marks = [...container.querySelectorAll<HTMLElement>(".technology-mark")];
    const stageFooters = [...container.querySelectorAll<HTMLElement>(".story-stage-footer")];
    expect(stageFooters).toHaveLength(4);
    expect(stageFooters.every((footer) => footer.querySelector(".story-technologies"))).toBe(true);
    expect(marks.map((mark) => mark.querySelector(".technology-name")?.textContent)).toEqual([
      "LangChain", "Deep Agents", "LangGraph",
      "DGX Spark", "Switchyard", "Nemotron 3.5 Lightning", "Nemotron 3 Ultra",
      "CUDA-X / RAPIDS", "OpenShell", "Nemotron 3 Embed",
      "NeMo Relay", "LangSmith", "NeMo Platform",
    ]);
    expect(marks.every((mark) => mark.querySelector("img"))).toBe(true);
    expect(marks.find((mark) => mark.textContent?.includes("LangSmith"))?.dataset.logoOwner).toBe("langchain");
    expect(marks.find((mark) => mark.textContent?.includes("OpenShell"))?.dataset.logoOwner).toBe("nvidia");
    const partnerLockup = container.querySelector<HTMLElement>(".story-partner-lockup")!;
    expect(partnerLockup.getAttribute("aria-label")).toBe("NVIDIA and LangChain");
    expect(partnerLockup.querySelectorAll("img")).toHaveLength(2);
    expect(partnerLockup.textContent).toContain("LangChain");
    expect(container.textContent).not.toContain("Measured in the research view");
    expect(container.textContent).not.toContain("First research step is not token-level time to first response");

    expect(button("Open the research agent")).toBeUndefined();
    expect(button("Back to dashboard")).toBeUndefined();
    expect(button("Run an investigation")).toBeUndefined();
  });

  it("opens source-backed routing details with both model links and restores focus", async () => {
    await render();
    const trigger = button("View routing and model details");
    trigger.focus();
    await act(async () => trigger.click());
    const dialog = container.querySelector<HTMLElement>("[role='dialog']")!;
    expect(dialog.querySelector("h2")?.textContent).toBe("How Switchyard escalates a model turn");
    expect(dialog.textContent).toContain("Lightning produces a local result");
    expect(dialog.textContent).toContain("Luna judges whether that result is sufficient");
    expect(dialog.textContent).toContain("If a provider fails, the turn stops");
    expect(dialog.textContent).toContain("answer-layout step can call Ultra directly");
    expect(dialog.querySelector("pre")?.textContent).toContain("SwitchyardRoutingMiddleware(adapter)");
    expect(dialog.querySelector("pre")?.textContent).toContain('"efficient": [LOCAL_MODEL]');
    expect(dialog.querySelector("pre")?.textContent).toContain('"capable": [CAPABLE_MODEL]');
    const lightning = dialog.querySelector<HTMLAnchorElement>("a[href*='NVIDIA-Nemotron-3.5-Lightning']")!;
    const ultra = dialog.querySelector<HTMLAnchorElement>("a[href*='nemotron-3-ultra-550b-a55b']")!;
    expect(lightning.href).toContain("bee7596271d1495f6992ae224aefde4410e816b8");
    expect(ultra.textContent).toContain("model information");
    expect([...dialog.querySelectorAll("a")].every((link) => link.target === "_blank" && link.rel === "noopener noreferrer")).toBe(true);
    expect(document.activeElement).toBe(dialog.querySelector(".technology-dialog-close"));

    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(container.querySelector("[role='dialog']")).toBeNull();
    expect(document.activeElement).toBe(trigger);

    for (const token of ["LlmClassifierConfig.escalation", "SwitchyardRoutingMiddleware(adapter)"]) expect(dialog.textContent ?? "").toContain(token);
  });

  it("shows the complete checked-in OpenShell base policy without secrets", async () => {
    await render();
    await act(async () => button("View OpenShell policy").click());
    const dialog = container.querySelector<HTMLElement>("[role='dialog']")!;
    const displayed = dialog.querySelector("pre code")?.textContent?.trim();
    expect(displayed).toContain("filesystem_policy:");
    expect(displayed).toContain("- /srv/market-shock/scenario");
    expect(displayed).toContain("- /srv/market-shock/traces");
    expect(dialog.textContent).toContain("endpoint-bound providers");
    expect(dialog.textContent).toContain("not written into this policy or the web application");
    expect(dialog.textContent).not.toMatch(/NVIDIA_INFERENCE_API_KEY|LANGSMITH_API_KEY|sk-[A-Za-z0-9]/);

    const backdrop = container.querySelector<HTMLElement>(".technology-dialog-backdrop")!;
    await act(async () => backdrop.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(container.querySelector("[role='dialog']")).toBeNull();
  });

  it("exposes the real agent, GPU, and trace boundaries through separate dialogs", async () => {
    await render();
    const checks = [
      ["View agent initialization", "How the Deep Agent is created", "create_deep_agent(**agent_kwargs)", "services/agent/src/market_agent/deep_runtime.py"],
      ["View GPU checks", "How GPU execution is verified", '_receipt(graph, "cugraph", bundle)', "services/tools/src/market_tools/server.py"],
      ["View trace configuration", "How one run becomes evidence for the next", "OpenTelemetrySectionConfig", "services/agent/src/market_agent/relay_tracing.py"],
    ] as const;
    for (const [triggerText, title, code, source] of checks) {
      await act(async () => button(triggerText).click());
      const dialog = container.querySelector<HTMLElement>("[role='dialog']")!;
      expect(dialog.querySelector("h2")?.textContent).toBe(title);
      expect(dialog.querySelector("pre")?.textContent).toContain(code);
      expect(dialog.textContent).toContain(source);
      await act(async () => dialog.querySelector<HTMLButtonElement>(".technology-dialog-close")!.click());
    }
    expect(container.textContent).toContain("LangSmith export is enabled");
    expect(container.querySelector(".story-partner-lockup")).not.toBeNull();
  });

  it("does not invent live routing or tracing state while status is unavailable", async () => {
    await render(null);
    expect(container.textContent).toContain("Routing status unavailable");
    expect(container.textContent).toContain("Tracing status unavailable");
    expect(container.textContent).not.toContain("Checking the system · Running on DGX Spark");
    expect(container.textContent).not.toContain("Routing is enabled");
    expect(container.textContent).not.toContain("LangSmith export is enabled");
    await act(async () => button("View trace configuration").click());
    const dialog = container.querySelector<HTMLElement>("[role='dialog']")!;
    expect(dialog.textContent).toContain("live status on this page reports whether LangSmith export is enabled");
    expect(dialog.textContent).not.toContain("LangSmith is connected in this demo");
  });
});
