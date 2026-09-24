import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { vi } from "vitest";
import type { Investigation, SystemStatus, Turn } from "./api/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

export interface Rendered { container: HTMLElement; root: Root; rerender: (node: ReactNode) => Promise<void>; unmount: () => void }

export async function render(node: ReactNode): Promise<Rendered> {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(node));
  return {
    container,
    root,
    rerender: async (next) => { await act(async () => root.render(next)); },
    unmount: () => { act(() => root.unmount()); container.remove(); },
  };
}

export const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

export async function click(element: Element | null | undefined) {
  if (!element) throw new Error("element not found");
  await act(async () => { (element as HTMLElement).click(); });
}

/** Set a React-controlled input/select/textarea value and fire the matching event. */
export async function change(element: Element | null | undefined, value: string) {
  if (!element) throw new Error("element not found");
  const prototype = Object.getPrototypeOf(element) as object;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
  await act(async () => {
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  });
}

export const byRole = (container: ParentNode, role: string, name?: RegExp | string) =>
  [...container.querySelectorAll<HTMLElement>(`[role="${role}"], ${implicit[role] ?? "_none_"}`)].find((element) => {
    if (name === undefined) return true;
    const label = element.getAttribute("aria-label") ?? element.textContent ?? "";
    return typeof name === "string" ? label.trim() === name : name.test(label);
  }) ?? null;

const implicit: Record<string, string> = { button: "button", listitem: "li", table: "table", heading: "h1,h2,h3,h4", link: "a[href]", combobox: "select", textbox: "textarea,input:not([type])" };

export const byLabel = (container: ParentNode, text: RegExp) =>
  [...container.querySelectorAll("label")].find((label) => text.test(label.textContent ?? ""))
    ?.querySelector<HTMLElement>("input, select, textarea")
  ?? [...container.querySelectorAll("label")].filter((label) => text.test(label.textContent ?? "")).map((label) => document.getElementById(label.htmlFor)).find(Boolean) ?? null;

export function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

export function mockFetch(handler: (url: string, init?: RequestInit) => Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const status = (overrides: Partial<SystemStatus> = {}): SystemStatus => ({
  ready: true,
  reason: null,
  remote_routing_enabled: true,
  dependencies: { tools: true, model: true, events: true },
  companies: [{ symbol: "AAA", name: "Alpha Corp" }, { symbol: "BBB", name: "Beta Inc" }],
  coverage: { scenario_id: "scenario-1", first_session: "2024-01-02", last_session: "2024-06-28" },
  models: [{ id: "local-model", role: "agent reasoning", where: "local" }],
  max_turns: 3,
  langsmith_project_url: null,
  ...overrides,
});

export const turn = (overrides: Partial<Turn> = {}): Turn => ({
  number: 1,
  question: "What moved AAA?",
  status: "running",
  started_at: "2024-06-28T20:00:00Z",
  ended_at: null,
  skill: null,
  report: null,
  error: null,
  model_calls: [],
  ...overrides,
});

export const investigation = (overrides: Partial<Investigation> = {}): Investigation => ({
  investigation_id: "inv-1",
  created_at: "2024-06-28T20:00:00Z",
  updated_at: "2024-06-28T20:00:00Z",
  status: "running",
  scope: { status: "resolved", ticker: "AAA", members: ["AAA"], as_of: "2024-06-28T20:00:00Z", session: "2024-06-28", event_id: null, missing: [], note: null },
  turns: [turn()],
  events: [],
  ...overrides,
});
