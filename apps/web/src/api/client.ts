import type {
  CreateInvestigation, Dashboard, Investigation, ProgressEvent, ShockEventCatalog, SystemStatus, TurnStatus,
} from "./types";

const TIMEOUT_MS = 20_000;

export class ApiError extends Error {
  constructor(message: string, readonly status: number | null = null) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI puts a string in `detail`, or a list of validation errors with `msg`. */
function detailMessage(body: unknown): string | null {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => (item as { msg?: unknown })?.msg).filter((msg) => typeof msg === "string");
    if (messages.length) return messages.join("; ");
  }
  return null;
}

async function request<T>(path: string, init: RequestInit = {}, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, TIMEOUT_MS);
  const forward = () => controller.abort();
  signal?.addEventListener("abort", forward, { once: true });
  try {
    const response = await fetch(path, { ...init, headers: { Accept: "application/json", ...init.headers }, signal: controller.signal });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(detailMessage(body) ?? `Request failed (${response.status}).`, response.status);
    if (body === null || typeof body !== "object" || Array.isArray(body)) {
      throw new ApiError("The server returned an unexpected response.", response.status);
    }
    return body as T;
  } catch (error) {
    if (timedOut) throw new ApiError("The server took too long to respond. Try again.");
    if (error instanceof ApiError || signal?.aborted) throw error;
    throw new ApiError("The server could not be reached. Check the connection and try again.");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forward);
  }
}

export const getJson = <T>(path: string, signal?: AbortSignal) => request<T>(path, {}, signal);
export const postJson = <T>(path: string, body?: unknown) => request<T>(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

const investigation = (id: string) => `/api/investigations/${encodeURIComponent(id)}`;

export const api = {
  status: (signal?: AbortSignal) => getJson<SystemStatus>("/api/status", signal),
  shockEvents: (signal?: AbortSignal) => getJson<ShockEventCatalog>("/api/shock-events", signal),
  dashboard: (ticker: string, asOf: string, signal?: AbortSignal) =>
    getJson<Dashboard>(`/api/dashboard?${new URLSearchParams({ ticker, as_of: asOf })}`, signal),
  create: (body: CreateInvestigation) => postJson<Investigation>("/api/investigations", body),
  get: (id: string) => getJson<Investigation>(investigation(id)),
  followUp: (id: string, question: string) => postJson<Investigation>(`${investigation(id)}/turns`, { question }),
  retry: (id: string) => postJson<Investigation>(`${investigation(id)}/retry`),
  cancel: (id: string) => postJson<Investigation>(`${investigation(id)}/cancel`),
};

export interface StreamHandlers {
  onProgress: (event: ProgressEvent) => void;
  onDone: (status: TurnStatus) => void;
  onError: () => void;
}

/** Follow an investigation's server-sent progress. The browser resumes with Last-Event-ID on reconnect. */
export function streamInvestigation(id: string, handlers: StreamHandlers): () => void {
  const source = new EventSource(`${investigation(id)}/stream`);
  source.addEventListener("progress", (message) => {
    handlers.onProgress(JSON.parse((message as MessageEvent<string>).data) as ProgressEvent);
  });
  source.addEventListener("done", (message) => {
    source.close();
    handlers.onDone((JSON.parse((message as MessageEvent<string>).data) as { status: TurnStatus }).status);
  });
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) handlers.onError();
  };
  return () => source.close();
}
