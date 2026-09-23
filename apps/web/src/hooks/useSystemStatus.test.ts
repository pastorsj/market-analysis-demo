import { describe, expect, it } from "vitest";
import type { SystemStatus } from "../api/types";
import { degradedDetail } from "./useSystemStatus";

function status(remote: boolean, unavailable: Partial<SystemStatus["dependencies"]> = {}): SystemStatus {
  return {
    remote_routing_enabled: remote,
    dependencies: {
      tools: true,
      model: true,
      coverage: true,
      checkpoint: true,
      mcp_contract: true,
      event_catalog: true,
      ...unavailable,
    },
  } as SystemStatus;
}

describe("degraded runtime guidance", () => {
  it("names the configured remote boundary when local dependencies are healthy", () => {
    expect(degradedDetail(status(false))).toBe(
      "Prepared locally; the approved internal inference route is disabled",
    );
  });

  it("lists an unavailable runtime dependency without blaming routing", () => {
    expect(degradedDetail(status(true, { model: false }))).toBe("Unavailable: local model");
    expect(degradedDetail(status(true, { event_catalog: false }))).toBe("Unavailable: event catalog");
  });
});
