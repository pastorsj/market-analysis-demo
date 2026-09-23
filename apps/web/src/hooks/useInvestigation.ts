import { useCallback, useEffect, useReducer, useRef } from "react";
import * as api from "../api/client";
import {
  terminal,
  terminalTurn,
  type Investigation,
  type InvestigationRequest,
  type RouteMode,
} from "../api/types";
import { initialState, reducer } from "../state/investigation";

const pause = (milliseconds: number, signal: AbortSignal) => new Promise<void>((resolve) => {
  if (signal.aborted) { resolve(); return; }
  const timer = window.setTimeout(done, milliseconds);
  function done() {
    window.clearTimeout(timer);
    signal.removeEventListener("abort", done);
    resolve();
  }
  signal.addEventListener("abort", done, { once: true });
});

const message = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;

function setInvestigationQuery(identifier: string | null) {
  const url = new URL(window.location.href);
  if (identifier) url.searchParams.set("investigation", identifier);
  else url.searchParams.delete("investigation");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

export function useInvestigation() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const abort = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const recordRef = useRef<Investigation | null>(null);
  recordRef.current = state.record;

  const stopWatch = useCallback(() => {
    generation.current += 1;
    abort.current?.abort();
    abort.current = null;
  }, []);

  const watch = useCallback((identifier: string, work?: Promise<Investigation>, initialSequence = 0) => {
    stopWatch();
    const controller = new AbortController();
    const version = generation.current;
    abort.current = controller;
    const current = () => !controller.signal.aborted && generation.current === version && abort.current === controller;
    let operationSettled = work === undefined;
    let operationRecord: Investigation | null = null;
    let operationError: unknown = null;
    let after = initialSequence;
    let failures = 0;
    let latest: Investigation | null = null;

    if (work) {
      void work.then((record) => { operationRecord = record; operationSettled = true; })
        .catch((error: unknown) => { operationError = error; operationSettled = true; });
    }

    dispatch({ type: "connection", value: "connecting" });
    void (async () => {
      while (current()) {
        try {
          const events = await api.readEvents(identifier, after, controller.signal);
          if (!current()) return;
          for (const event of events) {
            after = Math.max(after, event.sequence);
            dispatch({ type: "event", event });
          }
          const fetched = await api.getInvestigation(identifier);
          if (!current()) return;
          latest = operationRecord ?? fetched;
          after = Math.max(after, latest.events.at(-1)?.sequence ?? 0);
          dispatch({ type: "hydrate", record: latest });
          const newTerminal = latest.events.some((event) =>
            event.sequence > initialSequence && terminalTurn(event) !== null,
          );
          if (work === undefined || operationSettled || !terminal(latest.status) || newTerminal) {
            dispatch({ type: "busy", value: false });
          }
          failures = 0;
          if (terminal(latest.status) && (work === undefined || operationSettled || newTerminal)) break;
          if (operationError && operationSettled && latest.status === "created") break;
          dispatch({ type: "connection", value: "live" });
          await pause(500, controller.signal);
        } catch (error) {
          if (!current()) return;
          failures += 1;
          dispatch({ type: "error", message: `Live updates interrupted: ${message(error, "connection unavailable")}` });
          dispatch({ type: "connection", value: "reconnecting" });
          await pause(Math.min(500 * 2 ** (failures - 1), 4_000), controller.signal);
        }
      }
      if (!current()) return;
      if (operationError && (!latest || !terminal(latest.status) || latest.events.length <= initialSequence)) {
        dispatch({ type: "error", message: message(operationError, "Investigation action failed") });
      }
      abort.current = null;
      dispatch({ type: "busy", value: false });
      dispatch({ type: "connection", value: "closed" });
    })();
  }, [stopWatch]);

  const restore = useCallback(async (identifier: string) => {
    stopWatch();
    const version = generation.current;
    if (recordRef.current?.investigation_id !== identifier) dispatch({ type: "reset" });
    dispatch({ type: "busy", value: true });
    dispatch({ type: "connection", value: "connecting" });
    try {
      const record = await api.getInvestigation(identifier);
      if (generation.current !== version) return;
      dispatch({ type: "hydrate", record });
      dispatch({ type: "busy", value: false });
      if (terminal(record.status)) dispatch({ type: "connection", value: "closed" });
      else watch(
        identifier,
        record.status === "created" ? api.runInvestigation(identifier) : undefined,
        record.events.at(-1)?.sequence ?? 0,
      );
    } catch (error) {
      if (generation.current !== version) return;
      dispatch({ type: "error", message: message(error, "Unable to restore investigation") });
      dispatch({ type: "connection", value: "closed" });
    }
  }, [stopWatch, watch]);

  useEffect(() => {
    const identifier = new URLSearchParams(window.location.search).get("investigation");
    if (identifier) void restore(identifier);
    const onHistory = () => {
      const next = new URLSearchParams(window.location.search).get("investigation");
      if (next && next !== recordRef.current?.investigation_id) void restore(next);
      else if (!next && window.location.pathname === "/research") {
        stopWatch();
        dispatch({ type: "reset" });
      }
    };
    window.addEventListener("popstate", onHistory);
    return () => {
      window.removeEventListener("popstate", onHistory);
      stopWatch();
    };
  }, [restore, stopWatch]);

  const create = useCallback(async (body: InvestigationRequest) => {
    stopWatch();
    const version = generation.current;
    dispatch({ type: "reset" });
    dispatch({ type: "busy", value: true });
    try {
      const record = await api.createInvestigation(body);
      if (generation.current !== version) return;
      setInvestigationQuery(record.investigation_id);
      dispatch({ type: "hydrate", record });
      dispatch({ type: "busy", value: false });
      watch(
        record.investigation_id,
        api.runInvestigation(record.investigation_id),
        record.events.at(-1)?.sequence ?? 0,
      );
    } catch (error) {
      if (generation.current !== version) return;
      dispatch({ type: "error", message: message(error, "Unable to start investigation") });
      dispatch({ type: "connection", value: "closed" });
    }
  }, [stopWatch, watch]);

  const cancel = useCallback(async () => {
    const record = recordRef.current;
    if (!record) return;
    stopWatch();
    const version = generation.current;
    dispatch({ type: "busy", value: true });
    try {
      const cancelled = await api.cancelInvestigation(record.investigation_id);
      if (generation.current !== version) return;
      dispatch({ type: "hydrate", record: cancelled });
      dispatch({ type: "connection", value: "closed" });
    } catch (error) {
      if (generation.current !== version) return;
      dispatch({ type: "error", message: message(error, "Unable to cancel investigation") });
      dispatch({ type: "connection", value: "closed" });
    } finally {
      if (generation.current === version) dispatch({ type: "busy", value: false });
    }
  }, [stopWatch]);

  const retry = useCallback(() => {
    const record = recordRef.current;
    if (!record || !["failed", "cancelled"].includes(record.status)) return;
    dispatch({ type: "busy", value: true });
    watch(
      record.investigation_id,
      api.retryInvestigation(record.investigation_id),
      record.events.at(-1)?.sequence ?? 0,
    );
  }, [watch]);

  const turn = useCallback((question: string, mode?: RouteMode) => {
    const record = recordRef.current;
    if (!record || !terminal(record.status)) return;
    dispatch({ type: "busy", value: true });
    watch(
      record.investigation_id,
      api.continueInvestigation(record.investigation_id, question, mode),
      record.events.at(-1)?.sequence ?? 0,
    );
  }, [watch]);

  const reconnect = useCallback(() => {
    const identifier = recordRef.current?.investigation_id
      ?? new URLSearchParams(window.location.search).get("investigation");
    if (identifier) void restore(identifier);
  }, [restore]);

  const reset = useCallback(() => {
    stopWatch();
    setInvestigationQuery(null);
    dispatch({ type: "reset" });
  }, [stopWatch]);

  return { state, create, turn, cancel, retry, reconnect, reload: reconnect, reset };
}
