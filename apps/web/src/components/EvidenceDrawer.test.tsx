/** @vitest-environment jsdom */

import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Citation } from "../api/types";
import { EvidenceDrawer } from "./EvidenceDrawer";

const citation: Citation = {
  citation_id: "citation-00000001",
  evidence_id: "evidence-00000001",
  title: "NVDA daily bar",
  url: "https://example.test/nvda",
  source_type: "market",
  published_at: "2025-01-27T17:00:00.000Z",
  available_at: "2025-01-27T17:00:00.000Z",
  excerpt: "Point-in-time market evidence.",
  content_sha256: "d".repeat(64),
  hindsight: false,
};

function TimerHarness({ onClose }: { onClose: (tick: number) => void }) {
  const [selected, setSelected] = useState<Citation | null>(null), [tick, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((value) => value + 1), 250);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <>
      <button data-action="inspect" onClick={() => setSelected(citation)}>Inspect citation</button>
      <output data-tick={tick}>{tick}</output>
      <EvidenceDrawer citation={selected} onClose={() => { onClose(tick); setSelected(null); }} />
    </>
  );
}

describe("EvidenceDrawer modal focus", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  async function open(onClose = vi.fn()) {
    await act(async () => root.render(<TimerHarness onClose={onClose} />));
    const trigger = container.querySelector<HTMLButtonElement>("[data-action='inspect']")!;
    trigger.focus();
    await act(async () => trigger.click());
    return { onClose, trigger };
  }

  it("exposes canonical evidence metadata separately from visible localized fields", async () => {
    await open();
    const drawer = container.querySelector<HTMLElement>("[data-evidence-drawer]")!;
    expect(drawer.getAttribute("data-citation-id")).toBe(citation.citation_id);
    expect(drawer.getAttribute("data-evidence-id")).toBe(citation.evidence_id);
    expect(drawer.getAttribute("data-source-type")).toBe(citation.source_type);
    expect(drawer.getAttribute("data-temporal-label")).toBe("Reconstructed · cutoff-filtered");
    expect(drawer.getAttribute("data-published-at")).toBe(citation.published_at);
    expect(drawer.getAttribute("data-available-at")).toBe(citation.available_at);
    expect(drawer.getAttribute("data-content-digest")).toBe(citation.content_sha256);
    expect(drawer.querySelector("[data-evidence-title]")?.textContent).toBe(citation.title);
    expect(drawer.querySelector("[data-evidence-excerpt]")?.textContent).toBe(citation.excerpt);
    expect(drawer.querySelector("[data-evidence-published-text]")?.textContent).not.toBe("");
    expect(drawer.querySelector("[data-evidence-available-text]")?.textContent).not.toBe("");
  });

  it("does not refocus Close during 250 ms parent renders and calls the latest close handler", async () => {
    vi.useFakeTimers();
    const first = vi.fn(), latest = vi.fn();
    const { trigger } = await open(first);
    const source = container.querySelector<HTMLAnchorElement>("a[href]")!;
    expect(container.querySelector<HTMLButtonElement>("[aria-label='Close evidence']")).toBe(document.activeElement);
    source.focus();

    await act(async () => {
      vi.advanceTimersByTime(750);
      root.render(<TimerHarness onClose={latest} />);
    });
    expect(container.querySelector("output")?.getAttribute("data-tick")).toBe("3");
    expect(document.activeElement).toBe(source);

    await act(async () => source.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(first).not.toHaveBeenCalled();
    expect(latest).toHaveBeenCalledWith(3);
    expect(container.querySelector("[role='dialog']")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("wraps Tab focus in both directions and redirects escaped focus into the modal", async () => {
    const { trigger } = await open();
    const close = container.querySelector<HTMLButtonElement>("[aria-label='Close evidence']")!;
    const source = container.querySelector<HTMLAnchorElement>("a[href]")!;

    expect(document.activeElement).toBe(close);
    await act(async () => close.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true })));
    expect(document.activeElement).toBe(source);
    await act(async () => source.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })));
    expect(document.activeElement).toBe(close);

    trigger.focus();
    expect(document.activeElement).toBe(close);
  });

  it("restores the invoking citation button after button and scrim dismissal", async () => {
    let result = await open();
    await act(async () => container.querySelector<HTMLButtonElement>("[aria-label='Close evidence']")!.click());
    expect(document.activeElement).toBe(result.trigger);

    result = await open();
    const scrim = container.querySelector<HTMLDivElement>(".scrim")!;
    await act(async () => scrim.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(container.querySelector("[role='dialog']")).toBeNull();
    expect(document.activeElement).toBe(result.trigger);
  });
});
