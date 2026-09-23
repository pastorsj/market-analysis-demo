/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveMarketChart, LiveTickerTape, MARKET_PULSE_SYMBOLS } from "./LiveMarket";

function widgetScript(container: HTMLElement, kind: "chart" | "tape") {
  const script = container.querySelector<HTMLScriptElement>(`script[data-market-widget="${kind}"]`);
  expect(script, `${kind} widget script`).not.toBeNull();
  return script!;
}

function widgetConfig(script: HTMLScriptElement): Record<string, unknown> {
  return JSON.parse(script.textContent ?? "") as Record<string, unknown>;
}

describe("TradingView market widgets", () => {
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
    vi.restoreAllMocks();
  });

  it("mounts the exact TradingView chart symbol and constrained display configuration", async () => {
    const unavailable = vi.fn();
    await act(async () => root.render(<LiveMarketChart ticker="NVDA" onUnavailable={unavailable} />));

    let script = widgetScript(container, "chart");
    expect(script.src).toBe("https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js");
    expect(widgetConfig(script)).toEqual({
      autosize: true,
      symbol: "NASDAQ:NVDA",
      interval: "5",
      timezone: "America/New_York",
      theme: "light",
      style: "1",
      locale: "en",
      allow_symbol_change: false,
      calendar: false,
      support_host: "https://www.tradingview.com",
    });
    expect(container.querySelector("[aria-label='NVDA interactive current market chart']")).not.toBeNull();

    await act(async () => root.render(<LiveMarketChart ticker="GS" onUnavailable={unavailable} />));
    script = widgetScript(container, "chart");
    expect(widgetConfig(script).symbol).toBe("NYSE:GS");
    expect(container.querySelector("[aria-label='GS interactive current market chart']")).not.toBeNull();
    expect(unavailable).not.toHaveBeenCalled();
  });

  it("mounts the complete market pulse with a light adaptive configuration", async () => {
    await act(async () => root.render(<LiveTickerTape />));

    const script = widgetScript(container, "tape");
    expect(script.src).toBe("https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js");
    expect(widgetConfig(script)).toEqual({
      symbols: MARKET_PULSE_SYMBOLS,
      showSymbolLogo: true,
      colorTheme: "light",
      isTransparent: true,
      displayMode: "adaptive",
      locale: "en",
    });
    expect(container.querySelector("[aria-label='Current US market ticker tape']")).not.toBeNull();
    expect(container.textContent).toContain("Cboe via TradingView");
  });
});
