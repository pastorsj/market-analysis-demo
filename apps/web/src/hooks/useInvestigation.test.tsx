/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api/client";
import type { Investigation } from "../api/types";
import { useInvestigation } from "./useInvestigation";

vi.mock("../api/client", () => ({
  getInvestigation: vi.fn(), readEvents: vi.fn(), runInvestigation: vi.fn(),
  createInvestigation: vi.fn(), cancelInvestigation: vi.fn(), retryInvestigation: vi.fn(),
  continueInvestigation: vi.fn(),
}));

const record = (id: string) => ({ investigation_id: id, status: "completed", events: [], turns: [] }) as unknown as Investigation;

describe("investigation history recovery", () => {
  let root: Root;
  let container: HTMLDivElement;
  let current: ReturnType<typeof useInvestigation>;
  function Harness() { current = useInvestigation(); return <span>{current.state.record?.investigation_id ?? "empty"}</span>; }
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.resetAllMocks();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    window.history.replaceState(null, "", "/research?investigation=first");
    vi.mocked(api.getInvestigation).mockImplementation(async (id) => record(id));
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
  async function navigate(path: string) {
    await act(async () => {
      window.history.pushState(null, "", path);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
  }
  it("restores the investigation identified by browser history, then clears a blank Research route", async () => {
    await act(async () => root.render(<Harness />));
    expect(container.textContent).toBe("first");
    await navigate("/research?investigation=second");
    expect(container.textContent).toBe("second");
    expect(api.getInvestigation).toHaveBeenLastCalledWith("second");
    await navigate("/research");
    expect(container.textContent).toBe("empty");
    expect(current!.state.busy).toBe(false);
  });
  it("preserves a completed investigation while visiting the dashboard", async () => {
    await act(async () => root.render(<Harness />));
    await navigate("/");
    expect(container.textContent).toBe("first");
    expect(api.getInvestigation).toHaveBeenCalledTimes(1);
  });
  it("does not show the previous answer when restoration fails", async () => {
    await act(async () => root.render(<Harness />));
    vi.mocked(api.getInvestigation).mockRejectedValueOnce(new Error("Investigation not found"));
    await navigate("/research?investigation=missing");
    expect(container.textContent).toBe("empty");
    expect(current!.state.error).toBe("Investigation not found");
    expect(current!.state.busy).toBe(false);
  });
  it("ignores a late restore response after a reset", async () => {
    let finish!: (value: Investigation) => void;
    vi.mocked(api.getInvestigation).mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await act(async () => root.render(<Harness />));
    await act(async () => current!.reset());
    await act(async () => finish(record("first")));
    expect(container.textContent).toBe("empty");
    expect(current!.state.busy).toBe(false);
  });
});
