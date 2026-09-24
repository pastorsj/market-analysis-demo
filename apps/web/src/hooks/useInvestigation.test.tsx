/** @vitest-environment jsdom */
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProgressEvent } from "../api/types";
import { flush, investigation, jsonResponse, mockFetch, render, turn } from "../test-utils";
import { useInvestigation, type InvestigationFlow } from "./useInvestigation";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static readonly CLOSED = 2;
  readyState = 1;
  closed = false;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>();
  constructor(readonly url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() { this.closed = true; this.readyState = FakeEventSource.CLOSED; }
  emit(type: string, data: unknown) {
    act(() => { for (const listener of this.listeners.get(type) ?? []) listener(new MessageEvent(type, { data: JSON.stringify(data) })); });
  }
}

const progress = (sequence: number, state: "running" | "succeeded" = "running"): ProgressEvent => ({
  sequence,
  turn: 1,
  at: "2024-06-28T20:00:01Z",
  span: { span_id: "tool-1", parent_id: null, kind: "tool", name: "measure_move", state, started_at: "2024-06-28T20:00:01Z", ended_at: null, detail: {} },
});

let flow: InvestigationFlow;
function Probe() { flow = useInvestigation(); return null; }

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  window.history.replaceState(null, "", "/research");
});
afterEach(() => vi.unstubAllGlobals());

describe("useInvestigation", () => {
  it("follows live progress over SSE and fetches the investigation once when done", async () => {
    const completed = investigation({ status: "completed", turns: [turn({ status: "completed" })], events: [progress(1), progress(2, "succeeded")] });
    const fetchMock = mockFetch((url, init) => init?.method === "POST" ? jsonResponse(investigation(), 202) : jsonResponse(completed));
    await render(<Probe />);

    await act(async () => { await flow.start({ question: "What moved AAA?", ticker: "AAA" }); });
    expect(window.location.search).toBe("?investigation=inv-1");
    expect(FakeEventSource.instances).toHaveLength(1);
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("/api/investigations/inv-1/stream");

    source.emit("progress", progress(1));
    source.emit("progress", progress(1));
    source.emit("progress", progress(2, "succeeded"));
    expect(flow.investigation?.events.map((event) => event.sequence)).toEqual([1, 2]);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    source.emit("done", { status: "completed" });
    await flush();
    expect(source.closed).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/investigations/inv-1");
    expect(flow.investigation?.status).toBe("completed");
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("surfaces API error details without an investigation", async () => {
    mockFetch(() => jsonResponse({ detail: "Research needs the remote routing endpoint." }, 503));
    await render(<Probe />);
    await act(async () => { await flow.start({ question: "Why?" }); });
    expect(flow.error).toBe("Research needs the remote routing endpoint.");
    expect(flow.investigation).toBeNull();
  });

  it("posts follow-ups to /turns and reopens the stream", async () => {
    const done = investigation({ status: "completed", turns: [turn({ status: "completed" })] });
    const fetchMock = mockFetch((url) => url.endsWith("/turns")
      ? jsonResponse(investigation({ turns: [turn({ status: "completed" }), turn({ number: 2, question: "And peers?" })] }), 202)
      : jsonResponse(done));
    window.history.replaceState(null, "", "/research?investigation=inv-1");
    await render(<Probe />);
    await flush();
    expect(flow.investigation?.status).toBe("completed");
    expect(FakeEventSource.instances).toHaveLength(0);

    await act(async () => { await flow.followUp("And peers?"); });
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    expect(url).toBe("/api/investigations/inv-1/turns");
    expect(JSON.parse(String(init?.body))).toEqual({ question: "And peers?" });
    expect(FakeEventSource.instances).toHaveLength(1);

    act(() => { FakeEventSource.instances[0].close(); FakeEventSource.instances[0].onerror?.(); });
    expect(flow.error).toMatch(/interrupted/);
  });
});
