/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Report } from "../api/types";
import { byRole, click, render, type Rendered } from "../test-utils";
import { ReportView } from "./Report";

const report: Report = {
  kind: "research",
  answer: "## What happened\nAAA fell **8%** after guidance.\n\n- Volume tripled\n- Peers were flat\n\n<script>alert(1)</script>",
  citations: [{ citation_id: "c1", title: "AAA 8-K", url: null, source_type: "filing", published_at: "2024-06-27T20:00:00Z", available_at: "2024-06-27T21:00:00Z", excerpt: "Guidance lowered." }],
  uncertainty: ["Whether the guidance cut was fully priced in."],
  suggested_questions: ["How did peers react?"],
  limitations: ["Prices are reconstructed later."],
  tools: [{ tool: "measure_move", ticker: "AAA", outcome: "ok", summary: "AAA fell 8.1%.", receipt: { engine: "cudf", device: "NVIDIA GB10", duration_ms: 12.4 }, limitations: [] }],
  artifacts: [
    { artifact_id: "a1", kind: "analogue_table", title: "Measured analogues for AAA", data: { target_session: "2024-06-28", method: "Standardized distance.", rows: [{ ticker: "AAA", session_date: "2023-02-01", return_1d_pct: -7.5, benchmark_relative_return_pp: -6.9, volume_ratio: 2.8, distance: 0.41 }] } },
    { artifact_id: "a2", kind: "comovement_graph", title: "AAA co-movement network", data: { target: "AAA", session_date: "2024-06-28", nodes: [{ ticker: "BBB", hops: 1, correlation_with_target: 0.82, session_return_pct: -3.1 }, { ticker: "CCC", hops: 2, correlation_with_target: null, session_return_pct: 0.4 }], edges: [] } },
    { artifact_id: "a3", kind: "topic_projection", title: "AAA document map", data: { dimensions: 2, points: [{ evidence_id: "e1", title: "8-K", coordinates: [0, 1] }, { evidence_id: "e2", title: "Release", coordinates: [1, 0] }] } },
  ],
};

let view: Rendered;
afterEach(() => view.unmount());

describe("ReportView", () => {
  it("renders limited Markdown as structure and leaves HTML inert", async () => {
    view = await render(<ReportView report={report} />);
    expect(byRole(view.container, "heading", "What happened")).not.toBeNull();
    expect([...view.container.querySelectorAll(".summary strong")].map((item) => item.textContent)).toEqual(["8%"]);
    expect([...view.container.querySelectorAll(".summary li")].map((item) => item.textContent)).toEqual(["Volume tripled", "Peers were flat"]);
    expect(view.container.querySelector("script")).toBeNull();
    expect(view.container.textContent).toContain("<script>alert(1)</script>");
  });

  it("shows uncertainty, limitations, tool summaries, and GPU receipts", async () => {
    view = await render(<ReportView report={report} />);
    expect(view.container.textContent).toContain("Whether the guidance cut was fully priced in.");
    expect(view.container.textContent).toContain("Prices are reconstructed later.");
    expect(view.container.textContent).toContain("AAA fell 8.1%.");
    expect(view.container.querySelector("[aria-label='GPU receipt']")?.textContent).toBe("cudf on NVIDIA GB10 · 12 ms");
  });

  it("renders each artifact kind as measured tool output", async () => {
    view = await render(<ReportView report={report} />);
    const tables = [...view.container.querySelectorAll("table")];
    expect(tables[0].querySelector("caption")?.textContent).toMatch(/^Measured tool output/);
    expect(tables[0].textContent).toContain("2023-02-01");
    expect(tables[0].textContent).toContain("-7.50%");
    const rows = [...tables[1].querySelectorAll("tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent));
    expect(rows).toEqual([["BBB", "1", "0.82", "-3.10%"], ["CCC", "2", "n/a", "+0.40%"]]);
    expect(byRole(view.container, "img", "AAA document map: 2 documents")?.querySelectorAll("circle")).toHaveLength(2);
  });

  it("opens cited sources and offers suggested follow-ups", async () => {
    const onAsk = vi.fn();
    view = await render(<ReportView report={report} onAsk={onAsk} />);
    await click(byRole(view.container, "button", "[1] AAA 8-K"));
    const dialog = byRole(document.body, "dialog");
    expect(dialog?.textContent).toContain("Guidance lowered.");
    expect(dialog?.querySelector("a")).toBeNull();
    await click(byRole(dialog!, "button", "Close evidence"));
    await click(byRole(view.container, "button", "How did peers react?"));
    expect(onAsk).toHaveBeenCalledWith("How did peers react?");
  });
});
