/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, mockFetch } from "../test-utils";
import { api, getJson, postJson } from "./client";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("api client", () => {
  it("returns JSON objects and sends JSON bodies", async () => {
    const fetchMock = mockFetch(() => jsonResponse({ investigation_id: "x" }, 202));
    await expect(postJson("/api/investigations", { question: "Why?" })).resolves.toEqual({ investigation_id: "x" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/investigations");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ question: "Why?" });
  });

  it("builds the dashboard query string", async () => {
    const fetchMock = mockFetch(() => jsonResponse({}));
    await api.dashboard("AAA", "2024-06-28");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/dashboard?ticker=AAA&as_of=2024-06-28");
  });

  it("surfaces FastAPI string details", async () => {
    mockFetch(() => jsonResponse({ detail: "An investigation holds up to 4 questions." }, 409));
    await expect(getJson("/api/x")).rejects.toMatchObject({ message: "An investigation holds up to 4 questions.", status: 409 });
  });

  it("joins FastAPI validation errors", async () => {
    mockFetch(() => jsonResponse({ detail: [{ msg: "String should have at least 2 characters" }, { msg: "Field required" }] }, 422));
    await expect(getJson("/api/x")).rejects.toThrow("String should have at least 2 characters; Field required");
  });

  it("falls back to the HTTP status when the error body is not JSON", async () => {
    mockFetch(() => Promise.resolve(new Response("upstream down", { status: 502 })));
    await expect(getJson("/api/x")).rejects.toThrow("Request failed (502).");
  });

  it("rejects responses that are not JSON objects", async () => {
    mockFetch(() => jsonResponse([1, 2]));
    await expect(getJson("/api/x")).rejects.toThrow("unexpected response");
  });

  it("explains unreachable servers and timeouts", async () => {
    mockFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(getJson("/api/x")).rejects.toThrow("could not be reached");

    vi.useFakeTimers();
    mockFetch((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const pending = getJson("/api/x");
    const assertion = expect(pending).rejects.toThrow("took too long");
    await vi.advanceTimersByTimeAsync(20_000);
    await assertion;
  });
});
