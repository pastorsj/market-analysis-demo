import { afterEach, describe, expect, it, vi } from "vitest";
import { readEvents } from "./client";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("event polling deadline", () => {
  function pendingFetch() {
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    })));
  }
  it("times out a stalled event poll so the watcher can reconnect", async () => {
    vi.useFakeTimers();
    pendingFetch();
    const operation = readEvents("case", 0, new AbortController().signal);
    const result = expect(operation).rejects.toThrow("Reconnect to check your investigation");
    await vi.advanceTimersByTimeAsync(15_000);
    await result;
    expect(vi.getTimerCount()).toBe(0);
  });
  it("preserves explicit cancellation without presenting a timeout", async () => {
    vi.useFakeTimers();
    pendingFetch();
    const controller = new AbortController();
    const operation = readEvents("case", 0, controller.signal);
    const result = expect(operation).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await result;
    expect(vi.getTimerCount()).toBe(0);
  });
});
