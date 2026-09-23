import { useCallback, useEffect, useState } from "react";
import { getSystemStatus } from "../api/client";
import type { SystemDependencyStatus, SystemStatus } from "../api/types";

export type ReadinessState = "loading" | "ready" | "degraded" | "unavailable";

export interface SystemReadiness {
  readonly state: ReadinessState;
  readonly status: SystemStatus | null;
  readonly detail: string;
  readonly refresh: () => void;
}

const HEALTHY_REFRESH_MS = 15_000;
const DEGRADED_REFRESH_MS = 5_000;
const MAX_BACKOFF_MS = 10_000;
const dependencyLabels: Record<keyof SystemDependencyStatus, string> = {
  tools: "tools",
  model: "local model",
  coverage: "coverage",
  checkpoint: "state store",
  mcp_contract: "tool contract",
  event_catalog: "event catalog",
};

export function missingDependencies(status: SystemStatus): string[] {
  return (Object.entries(status.dependencies) as Array<[keyof SystemDependencyStatus, boolean]>)
    .filter(([, available]) => !available)
    .map(([dependency]) => dependencyLabels[dependency]);
}

export function degradedDetail(status: SystemStatus): string {
  const missing = missingDependencies(status);
  if (missing.length > 0) return `Unavailable: ${missing.join(", ")}`;
  if (!status.remote_routing_enabled) {
    return "Prepared locally; the approved internal inference route is disabled";
  }
  return "Runtime readiness could not be verified";
}

export function useSystemStatus(): SystemReadiness {
  const [refreshKey, setRefreshKey] = useState(0);
  const [value, setValue] = useState<Omit<SystemReadiness, "refresh">>({
    state: "loading",
    status: null,
    detail: "Checking runtime dependencies",
  });

  const refresh = useCallback(() => setRefreshKey((current) => current + 1), []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;

    const schedule = (delay: number) => {
      if (active) timer = setTimeout(() => void check(), delay);
    };
    const check = async () => {
      try {
        const status = await getSystemStatus();
        if (!active) return;
        failures = 0;
        setValue(status.ready
          ? { state: "ready", status, detail: "All runtime dependencies verified" }
          : { state: "degraded", status, detail: degradedDetail(status) });
        schedule(status.ready ? HEALTHY_REFRESH_MS : DEGRADED_REFRESH_MS);
      } catch (error) {
        if (!active) return;
        failures += 1;
        setValue({
          state: "unavailable",
          status: null,
          detail: error instanceof Error ? error.message : "Runtime status is unavailable",
        });
        schedule(Math.min(1_000 * 2 ** (failures - 1), MAX_BACKOFF_MS));
      }
    };

    setValue((current) => refreshKey === 0 ? current : {
      state: "loading",
      status: null,
      detail: "Refreshing runtime dependencies",
    });
    void check();
    return () => {
      active = false;
      if (timer !== null) clearTimeout(timer);
    };
  }, [refreshKey]);

  return { ...value, refresh };
}
