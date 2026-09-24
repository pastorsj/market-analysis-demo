import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SystemStatus } from "../api/types";

const READY_REFRESH_MS = 15_000;
const NOT_READY_REFRESH_MS = 5_000;

export interface SystemReadiness {
  /** Last successful /api/status response (kept while a later refresh fails). */
  readonly status: SystemStatus | null;
  /** Why the status could not be fetched, if the latest attempt failed. */
  readonly error: string | null;
}

export function useSystemStatus(): SystemReadiness {
  const [value, setValue] = useState<SystemReadiness>({ status: null, error: null });

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = async () => {
      let delay = NOT_READY_REFRESH_MS;
      try {
        const status = await api.status(controller.signal);
        setValue({ status, error: null });
        if (status.ready) delay = READY_REFRESH_MS;
      } catch (error) {
        if (controller.signal.aborted) return;
        setValue((current) => ({ status: current.status, error: error instanceof Error ? error.message : "Status is unavailable." }));
      }
      if (!controller.signal.aborted) timer = setTimeout(() => void check(), delay);
    };
    void check();
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);

  return value;
}
