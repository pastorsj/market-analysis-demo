/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ShockEvent } from "../api/types";
import { byLabel, byRole, change, click, render, status, type Rendered } from "../test-utils";
import { InvestigationForm, type InvestigationFormProps } from "./InvestigationForm";

const event: ShockEvent = {
  event_id: "aaa-guidance-cut", category_id: "earnings", title: "AAA guidance cut", summary: "AAA lowered guidance after the close.",
  sort_order: 1, event_session: "2024-06-28", source_dates: ["2024-06-27"], primary_ticker: "AAA", analysis_tickers: ["AAA", "BBB"],
  context_instruments: ["SPY"], start_session: "2024-06-20", end_session: "2024-07-05", default_cutoff: "2024-06-28T20:00:00Z",
  questions: [
    { question_id: "q1", label: "Measure the move", capability: "move-measurement", text: "How large was AAA's move versus SPY?" },
    { question_id: "q2", label: "Compare peers", capability: "peer-comparison", text: "How did BBB react to AAA's guidance cut?" },
  ],
  limitations: [], status: "ready",
};

let view: Rendered;
afterEach(() => view.unmount());
const props = (overrides: Partial<InvestigationFormProps> = {}): InvestigationFormProps => ({
  status: status(), statusError: null, busy: false, seed: null, event: null, onClearEvent: vi.fn(), onSubmit: vi.fn(), ...overrides,
});
const submit = () => byRole(view.container, "button", /Investigate/) as HTMLButtonElement;

describe("InvestigationForm", () => {
  it("shows why research is not ready instead of a spinner", async () => {
    const reason = "Research needs the remote routing endpoint: set REMOTE_ROUTING_ENABLED=true.";
    view = await render(<InvestigationForm {...props({ status: status({ ready: false, reason }) })} />);
    expect(byRole(view.container, "alert")?.textContent).toContain(reason);
    await change(byLabel(view.container, /Ask a market research question/), "What moved AAA?");
    expect(submit().disabled).toBe(true);
  });

  it("lists companies and models from status", async () => {
    view = await render(<InvestigationForm {...props()} />);
    const options = [...(byLabel(view.container, /Company ticker/) as HTMLSelectElement).options].map((option) => option.textContent);
    expect(options).toEqual(["Infer from question", "AAA · Alpha Corp", "BBB · Beta Inc"]);
    expect(view.container.textContent).toContain("local-model");
  });

  it("posts a custom question with ticker and cutoff", async () => {
    const onSubmit = vi.fn();
    view = await render(<InvestigationForm {...props({ onSubmit })} />);
    await change(byLabel(view.container, /Company ticker/), "BBB");
    await change(byLabel(view.container, /Evidence cutoff/), "2024-03-15");
    await change(byLabel(view.container, /Ask a market research question/), "  What moved BBB?  ");
    await click(submit());
    expect(onSubmit).toHaveBeenCalledWith({ question: "What moved BBB?", ticker: "BBB", as_of: "2024-03-15" });
  });

  it("posts a curated event question with only the event id", async () => {
    const onSubmit = vi.fn();
    view = await render(<InvestigationForm {...props({ onSubmit, event })} />);
    const textarea = byLabel(view.container, /Ask about this event/) as HTMLTextAreaElement;
    expect(textarea.value).toBe(event.questions[0].text);
    await click(byRole(view.container, "button", "Compare peers"));
    expect(textarea.value).toBe(event.questions[1].text);
    await click(submit());
    expect(onSubmit).toHaveBeenCalledWith({ question: event.questions[1].text, event_id: "aaa-guidance-cut" });
  });
});
