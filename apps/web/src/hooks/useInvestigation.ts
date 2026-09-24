import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamInvestigation } from "../api/client";
import type { CreateInvestigation, Investigation, ProgressEvent } from "../api/types";

const message = (error: unknown) => error instanceof Error ? error.message : "The request failed.";

function setQuery(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("investigation", id);
  else url.searchParams.delete("investigation");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

function appendEvent(record: Investigation, event: ProgressEvent): Investigation {
  const last = record.events.at(-1)?.sequence ?? 0;
  return event.sequence > last ? { ...record, events: [...record.events, event] } : record;
}

export interface InvestigationFlow {
  readonly investigation: Investigation | null;
  readonly busy: boolean;
  readonly error: string | null;
  readonly start: (body: CreateInvestigation) => Promise<void>;
  readonly followUp: (question: string) => Promise<void>;
  readonly retry: () => Promise<void>;
  readonly cancel: () => Promise<void>;
  readonly reload: () => Promise<void>;
  readonly reset: () => void;
}

/** One investigation: REST actions plus live progress over SSE while a turn is running. */
export function useInvestigation(): InvestigationFlow {
  const [investigation, setInvestigation] = useState<Investigation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [streamAttempt, setStreamAttempt] = useState(0);
  const current = useRef<Investigation | null>(null);
  current.current = investigation;

  const run = useCallback(async (action: () => Promise<Investigation>) => {
    setBusy(true);
    setError(null);
    try {
      const record = await action();
      setInvestigation(record);
      setQuery(record.investigation_id);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }, []);

  const withId = useCallback((action: (id: string) => Promise<Investigation>) => async () => {
    const id = current.current?.investigation_id;
    if (id) await run(() => action(id));
  }, [run]);

  const reload = useCallback(async () => {
    const id = current.current?.investigation_id;
    if (!id) return;
    await run(() => api.get(id));
    setStreamAttempt((value) => value + 1);
  }, [run]);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("investigation");
    if (id) void run(() => api.get(id));
  }, [run]);

  const id = investigation?.investigation_id ?? null;
  const running = investigation?.status === "running";
  useEffect(() => {
    if (!id || !running) return;
    return streamInvestigation(id, {
      onProgress: (event) => setInvestigation((record) => record?.investigation_id === id ? appendEvent(record, event) : record),
      onDone: () => {
        void api.get(id)
          .then((record) => setInvestigation((existing) => existing?.investigation_id === id ? record : existing))
          .catch((reason: unknown) => setError(message(reason)));
      },
      onError: () => setError("Live progress was interrupted. Reload to check the investigation."),
    });
  }, [id, running, streamAttempt]);

  return {
    investigation,
    busy,
    error,
    start: (body) => run(() => api.create(body)),
    followUp: (question) => withId((key) => api.followUp(key, question))(),
    retry: withId(api.retry),
    cancel: withId(api.cancel),
    reload,
    reset: () => { setInvestigation(null); setError(null); setQuery(null); },
  };
}
