/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { byRole, click, render, turn, type Rendered } from "../test-utils";
import { TurnView } from "./TurnView";

let view: Rendered;
afterEach(() => view.unmount());
const handlers = () => ({ onCancel: vi.fn(), onRetry: vi.fn() });

describe("TurnView", () => {
  it("offers Cancel while the latest turn is running", async () => {
    const actions = handlers();
    view = await render(<TurnView turn={turn()} latest busy={false} {...actions} />);
    await click(byRole(view.container, "button", "Cancel"));
    expect(actions.onCancel).toHaveBeenCalled();
  });

  it("shows the typed error with a Retry button for a failed turn", async () => {
    const actions = handlers();
    view = await render(<TurnView turn={turn({ status: "failed", error: { code: "model_timeout", message: "The model took too long." } })} latest busy={false} {...actions} />);
    const alert = byRole(view.container, "alert");
    expect(alert?.textContent).toContain("The model took too long.");
    expect(alert?.querySelector("code")?.textContent).toBe("model_timeout");
    await click(byRole(view.container, "button", "Retry"));
    expect(actions.onRetry).toHaveBeenCalled();
  });

  it("does not offer Retry on earlier turns", async () => {
    view = await render(<TurnView turn={turn({ status: "cancelled" })} latest={false} busy={false} {...handlers()} />);
    expect(byRole(view.container, "button", "Retry")).toBeNull();
    expect(view.container.textContent).toContain("cancelled");
  });

  it("shows the agent's chosen skill and each model call", async () => {
    view = await render(<TurnView turn={turn({
      status: "completed",
      skill: "historical-analogues",
      model_calls: [
        { role: "judge", model: "judge-model", tier: "judge", state: "succeeded", latency_ms: 310, prompt_tokens: 0, completion_tokens: 0, failure: null },
        { role: "agent", model: "ultra-model", tier: "capable", state: "succeeded", latency_ms: 2400, prompt_tokens: 0, completion_tokens: 0, failure: null },
      ],
    })} latest busy={false} {...handlers()} />);
    expect(view.container.textContent).toContain("Skill chosen by the agent: historical analogues");
    expect(view.container.querySelector("summary")?.textContent).toBe("2 model calls · escalated to capable tier");
    const rows = [...view.container.querySelectorAll("tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent));
    expect(rows).toEqual([["Routing judge", "judge-model", "judge", "310 ms", "ok"], ["Agent", "ultra-model", "capable", "2.40 s", "ok"]]);
  });
});
