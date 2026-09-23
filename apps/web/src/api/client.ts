import {
  decodeInvestigation, decodeSystemStatus, decodeTrajectoryEvent,
  type Investigation, type InvestigationRequest, type RouteMode,
  type SystemStatus, type TrajectoryEvent,
} from "./types";

const BASE = "/api/investigations";
const REQUEST_TIMEOUT_MS = 15_000;
const INVESTIGATION_TIMEOUT_MS = 240_000;

// Both cancellation and a deadline apply, including while consuming a response body.
async function withDeadline<T>(
  action: (signal: AbortSignal) => Promise<T>,
  timeoutMs = REQUEST_TIMEOUT_MS,
  external?: AbortSignal | null,
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  external?.addEventListener("abort", cancel, { once: true });
  if (external?.aborted) cancel();
  try {
    return await action(controller.signal);
  } catch (error) {
    if (timedOut && !external?.aborted) {
      throw new Error("The server took too long to respond. Reconnect to check your investigation.");
    }
    throw error;
  } finally {
    clearTimeout(timer);
    external?.removeEventListener("abort", cancel);
  }
}

async function decodedRequest<T>(
  path: string, decode: (value: unknown) => T, init?: RequestInit,
  timeoutMs = REQUEST_TIMEOUT_MS,
): Promise<T> {
  return withDeadline(async (signal) => {
    const response = await fetch(path, {
      ...init, headers: { "content-type": "application/json", ...init?.headers }, signal,
    });
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body?.detail === "string") message = body.detail;
      } catch { /* Preserve the HTTP status when no JSON error is available. */ }
      throw new Error(message);
    }
    return decode(await response.json());
  }, timeoutMs, init?.signal);
}

const request = (path: string, init?: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Investigation> =>
  decodedRequest(path, decodeInvestigation, init, timeoutMs);
const investigationPath = (id: string) => `${BASE}/${encodeURIComponent(id)}`;

export const getSystemStatus = (): Promise<SystemStatus> => decodedRequest("/api/status", decodeSystemStatus);
export const createInvestigation = (body: InvestigationRequest) => request(BASE, { method: "POST", body: JSON.stringify(body) });
export const getInvestigation = (id: string) => request(investigationPath(id));
export const runInvestigation = (id: string) => request(`${investigationPath(id)}/run`, { method: "POST" }, INVESTIGATION_TIMEOUT_MS);
export const continueInvestigation = (id: string, question: string, route_mode?: RouteMode) =>
  request(`${investigationPath(id)}/turn`, { method: "POST", body: JSON.stringify({ question, route_mode }) }, INVESTIGATION_TIMEOUT_MS);
export const cancelInvestigation = (id: string) => request(`${investigationPath(id)}/cancel`, { method: "POST" });
export const retryInvestigation = (id: string) => request(`${investigationPath(id)}/retry`, { method: "POST" }, INVESTIGATION_TIMEOUT_MS);

export function readEvents(id: string, after: number, signal: AbortSignal): Promise<TrajectoryEvent[]> {
  return withDeadline(async (boundedSignal) => {
    const response = await fetch(`${investigationPath(id)}/events?after=${after}`, {
      headers: { Accept: "text/event-stream" }, signal: boundedSignal,
    });
    if (!response.ok) throw new Error(`Event stream failed (${response.status})`);
    const text = await response.text();
    return text.split("\n\n").flatMap((block) => {
      const line = block.split("\n").find((item) => item.startsWith("data: "));
      return line ? [decodeTrajectoryEvent(JSON.parse(line.slice(6)))] : [];
    });
  }, REQUEST_TIMEOUT_MS, signal);
}
