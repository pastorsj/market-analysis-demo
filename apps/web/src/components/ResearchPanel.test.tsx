/** @vitest-environment jsdom */
import { afterEach, describe, expect, it } from "vitest";
import type { ProgressEvent, Span } from "../api/types";
import { byRole, click, investigation, render, type Rendered } from "../test-utils";
import { projectGantt, ResearchPanel } from "./ResearchPanel";

const at = (seconds: number) => new Date(Date.UTC(2024, 5, 28, 20, 0, seconds)).toISOString();
let sequence = 0;
const event = (turn: number, span: Partial<Span> & Pick<Span, "span_id" | "kind" | "name">): ProgressEvent => ({
  sequence: ++sequence, turn, at: at(0),
  span: { parent_id: null, state: "succeeded", started_at: at(0), ended_at: null, detail: {}, ...span },
});

const events = [
  event(1, { span_id: "turn-1", kind: "turn", name: "turn", state: "running", started_at: at(0) }),
  event(1, { span_id: "tool-1", kind: "tool", name: "measure_move", state: "running", started_at: at(2) }),
  event(1, { span_id: "tool-1", kind: "tool", name: "measure_move", started_at: at(2), ended_at: at(4), detail: { ticker: "AAA", engine: "cudf", device: "GB10", duration_ms: 40 } }),
  event(1, { span_id: "turn-1", kind: "turn", name: "turn", started_at: at(0), ended_at: at(10) }),
  event(2, { span_id: "model-2", kind: "model", name: "agent step", state: "running", started_at: at(20) }),
];

let view: Rendered;
afterEach(() => view?.unmount());

describe("projectGantt", () => {
  it("places each turn's latest span states on the turn's own clock", () => {
    const turns = projectGantt(events, Date.parse(at(25)));
    expect(turns.map((item) => [item.turn, item.totalMs])).toEqual([[1, 10_000], [2, 5_000]]);
    const tool = turns[0].bars.find((bar) => bar.span.span_id === "tool-1")!;
    expect([tool.span.state, tool.left, tool.width]).toEqual(["succeeded", 20, 20]);
    expect(turns[1].bars[0].durationMs).toBe(5_000);
  });
});

describe("ResearchPanel", () => {
  it("lists span activity with receipts and switches to the timeline", async () => {
    view = await render(<ResearchPanel investigation={investigation({ events })} />);
    expect(view.container.textContent).toContain("1 running");
    expect(view.container.textContent).toContain("AAA · cudf · GB10 · GPU 40 ms");
    await click(byRole(view.container, "tab", "Timeline"));
    expect(byRole(view.container, "listitem", /measure move, tool, succeeded, starts at 2.00 s, lasts 2.00 s/)).not.toBeNull();
  });
});
