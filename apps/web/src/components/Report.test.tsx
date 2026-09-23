/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LOCAL_MODEL, type Report } from "../api/types";
import { ReportView } from "./Report";

const report: Report = {
  schema_version: "1.0",
  title: "NVDA evidence",
  summary: "A bounded answer.",
  scope: {
    status: "supported",
    action: "answer",
    ticker: "NVDA",
    as_of: "2025-01-27T17:00:00.000Z",
    market_as_of: "2025-01-27T17:00:00.000Z",
    timezone: "America/New_York",
    supported_universe: ["NVDA"],
    explanation: "Bounded.",
    resolved_tickers: ["NVDA"],
    group_key: null,
  },
  no_data_reasons: [],
  claims: [{ claim_id: "claim-00000001", text: "NVDA moved.", kind: "inference", confidence: 0.67, citation_ids: [] }],
  citations: [],
  uncertainty: [],
  receipts: [{
    tool: "get_price_context",
    engine: "cudf",
    device: "cuda:0",
    gpu_executed: true,
    fallback_used: false,
    duration_ms: 40.5,
    artifact_manifest_sha256: "a".repeat(64),
    scenario_id: "scenario",
    market_manifest_sha256: "b".repeat(64),
    document_manifest_sha256: null,
    market_readiness_sha256: "c".repeat(64),
    document_readiness_sha256: null,
  }],
  routing: {
    requested_mode: "local_only",
    effective_mode: "local_only",
    configured_model: LOCAL_MODEL,
    returned_model: LOCAL_MODEL,
    reason: "Local route selected.",
    remote_attempted: false,
    fallback_used: false,
    frontier_latched: false,
    latency_ms: 42,
  },
  answer_mode: "model_synthesis",
  model_attempts: [{
    role: "answer_synthesis",
    algorithm: "direct_local",
    destination_class: "local_model",
    configured_model: LOCAL_MODEL,
    model_assertion: LOCAL_MODEL,
    identity_evidence: "direct_provider_verified",
    state: "succeeded",
    failure_class: null,
    application_call_id: "call-00000001",
    application_request_id: "request-00000001",
    latency_ms: 42,
    tokens: { prompt: 10, completion: 5, total: 15 },
    validation_status: "valid",
    switchyard_trial_id: null,
    selected_tier: null,
  }],
  switchyard_trials: [],
  artifacts: [],
  generated_at: "2025-01-27T17:00:01.000Z",
};

describe("ReportView capture contract", () => {
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

  it("exposes exact answer fields while omitting operational metadata", async () => {
    await act(async () => root.render(<ReportView report={report} />));

    const rendered = container.querySelector<HTMLElement>("[data-report='final']")!;
    expect(rendered.dataset.answerMode).toBe("model_synthesis");
    expect(rendered.dataset.routeMode).toBe("local_only");
    expect(rendered.querySelector("[data-report-title]")?.textContent).toBe(report.title);
    expect(rendered.querySelector("[data-report-summary]")?.textContent).toBe(report.summary);

    const claim = rendered.querySelector<HTMLElement>("[data-claim-id='claim-00000001']")!;
    expect(claim.dataset.claimKind).toBe("inference");
    expect(claim.dataset.claimConfidence).toBe("0.67");

    expect(rendered.querySelector(".answer-provenance-summary, .report-model-attempt, .report-model-absence")).toBeNull();
    expect(rendered.querySelector(".computation-receipt, .receipts-section, .routing-receipt")).toBeNull();
    expect(rendered.textContent).not.toContain("Answer provenance");
    expect(rendered.textContent).not.toContain("Computation receipts");
    expect(rendered.textContent).not.toContain("Routing summary");
  });

  it("orders the reader-facing answer and ends with additional Sources", async () => {
    const ordered: Report = {
      ...report,
      uncertainty: ["The comparison remains sensitive to the event window."],
      artifacts: [{
        artifact_id: "artifact-00000001",
        kind: "report",
        title: "Measured comparison",
        data: { closest: "GS" },
      }],
      citations: [{
        citation_id: "citation-00000001",
        evidence_id: "evidence-00000001",
        title: "Additional market evidence",
        url: "https://example.test/evidence",
        source_type: "market",
        published_at: "2025-01-27T16:00:00.000Z",
        available_at: "2025-01-27T16:01:00.000Z",
        excerpt: "Point-in-time evidence.",
        content_sha256: "d".repeat(64),
        hindsight: false,
      }],
    };
    await act(async () => root.render(<ReportView report={ordered} />));

    const rendered = container.querySelector<HTMLElement>("[data-report='final']")!;
    const sections = Array.from(rendered.children).filter((child): child is HTMLElement => child instanceof HTMLElement && child.tagName === "SECTION");
    expect(sections.map((section) => section.querySelector("h3")?.textContent)).toEqual([
      "Evidence-backed findings",
      "Visual artifacts",
      "What remains uncertain",
      "Sources",
    ]);
    expect(rendered.lastElementChild).toBe(sections.at(-1));
    expect(sections.at(-1)?.classList.contains("sources-inventory")).toBe(true);
  });

  it("renders the bounded report subset without parsing unsupported markup", async () => {
    const emphasized: Report = {
      ...report,
      summary: "## Similarity ranking\n\n1. **GS (2024-11-06)** and _literal underscores_; <em>literal HTML</em>.\n2. AMD remains second.\n\n### Limits of the comparison\n\n- [Link](https://example.test) stays literal.\n- `code`, ![image](bad), and | tables | stay literal.",
      claims: [{ ...report.claims[0], text: "Closest analogue: **GS (2024-11-06)**." }],
    };
    await act(async () => root.render(<ReportView report={emphasized} />));

    const summary = container.querySelector<HTMLElement>("[data-report-summary]")!;
    const claim = container.querySelector<HTMLElement>("[data-claim-id='claim-00000001'] p")!;
    expect(summary.querySelector("h3")?.textContent).toBe("Similarity ranking");
    expect(summary.querySelector("h4")?.textContent).toBe("Limits of the comparison");
    expect(summary.querySelectorAll("ol > li")).toHaveLength(2);
    expect(summary.querySelectorAll("ul > li")).toHaveLength(2);
    expect(summary.querySelector("strong")?.textContent).toBe("GS (2024-11-06)");
    expect(claim.querySelector("strong")?.textContent).toBe("GS (2024-11-06)");
    expect(summary.textContent).toContain("[Link](https://example.test) stays literal.");
    expect(summary.textContent).toContain("`code`, ![image](bad), and | tables | stay literal.");
    expect(summary.innerHTML).not.toContain("**");
    expect(claim.innerHTML).not.toContain("**");
    expect(summary.querySelector("em")).toBeNull();
    expect(summary.querySelector("a, img, script, code, table, blockquote")).toBeNull();
  });

  it("shows citations without duplicating an identical sole claim body", async () => {
    const duplicated: Report = {
      ...report,
      summary: "## Bottom line\n\nThe evidence-grounded answer.",
      claims: [{ ...report.claims[0], text: "## Bottom line\n\nThe evidence-grounded answer." }],
    };
    await act(async () => root.render(<ReportView report={duplicated} />));

    const claim = container.querySelector<HTMLElement>("[data-claim-id='claim-00000001']")!;
    expect(claim.querySelector(".claim-body")).toBeNull();
    expect(claim.querySelector(".claim-evidence-label")?.textContent).toBe("Evidence supporting the answer above");
    expect(container.querySelectorAll(".limited-markdown h3")).toHaveLength(1);
  });

  it("keeps equal claim bodies visible when a report has multiple claims", async () => {
    const multiple: Report = {
      ...report,
      summary: "Shared wording.",
      claims: [
        { ...report.claims[0], text: "Shared wording." },
        { ...report.claims[0], claim_id: "claim-00000002", text: "A distinct finding." },
      ],
    };
    await act(async () => root.render(<ReportView report={multiple} />));

    expect(container.querySelector("[data-claim-id='claim-00000001'] .claim-body")?.textContent).toBe("Shared wording.");
    expect(container.querySelector("[data-claim-id='claim-00000002'] .claim-body")?.textContent).toBe("A distinct finding.");
  });
});
