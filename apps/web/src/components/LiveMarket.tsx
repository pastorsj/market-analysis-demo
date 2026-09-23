import { useEffect, useRef, useState } from "react";
import type { PrimaryTicker } from "../api/types";
import "./live-market.css";

export type LiveWidgetState = "loading" | "ready" | "unavailable";

const TRADING_VIEW_SCRIPTS = {
  chart: "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js",
  tape: "https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js",
} as const;

export const TRADING_VIEW_SYMBOLS: Readonly<Record<PrimaryTicker, string>> = {
  NVDA: "NASDAQ:NVDA",
  AMD: "NASDAQ:AMD",
  JPM: "NYSE:JPM",
  GS: "NYSE:GS",
  SCHW: "NYSE:SCHW",
};

export const MARKET_PULSE_SYMBOLS = [
  { proName: "NASDAQ:NVDA", title: "NVIDIA" },
  { proName: "NASDAQ:AMD", title: "AMD" },
  { proName: "NYSE:JPM", title: "JPMorgan" },
  { proName: "NYSE:GS", title: "Goldman Sachs" },
  { proName: "NYSE:SCHW", title: "Charles Schwab" },
  { proName: "AMEX:SPY", title: "S&P 500" },
  { proName: "NASDAQ:QQQ", title: "Nasdaq 100" },
  { proName: "AMEX:XLF", title: "Financials" },
] as const;

interface TradingViewEmbedProps {
  readonly className: string;
  readonly config: Readonly<Record<string, unknown>>;
  readonly label: string;
  readonly script: keyof typeof TRADING_VIEW_SCRIPTS;
  readonly timeoutMs?: number;
  readonly onStateChange?: (state: LiveWidgetState) => void;
}

export function TradingViewEmbed({ className, config, label, script, timeoutMs = 12_000, onStateChange }: TradingViewEmbedProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const stateCallbackRef = useRef(onStateChange);
  const [state, setState] = useState<LiveWidgetState>("loading");
  const configJson = JSON.stringify(config);

  useEffect(() => {
    stateCallbackRef.current = onStateChange;
  }, [onStateChange]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let frame: HTMLIFrameElement | null = null;
    let settled = false;
    let watchdog = 0;
    const update = (next: LiveWidgetState) => {
      if (next !== "loading" && settled) return;
      if (next !== "loading") settled = true;
      setState(next);
      stateCallbackRef.current?.(next);
    };
    const unavailable = () => update("unavailable");
    const connected = () => {
      if (watchdog) window.clearTimeout(watchdog);
      update("ready");
    };

    host.replaceChildren();
    update("loading");

    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      unavailable();
      return () => host.replaceChildren();
    }

    const widget = document.createElement("div");
    widget.className = "tradingview-widget-container__widget";
    const loader = document.createElement("script");
    loader.async = true;
    loader.type = "text/javascript";
    loader.src = TRADING_VIEW_SCRIPTS[script];
    loader.textContent = configJson;
    loader.dataset.marketWidget = script;
    loader.addEventListener("error", unavailable, { once: true });

    const observer = new MutationObserver(() => {
      const nextFrame = host.querySelector("iframe");
      if (!nextFrame || nextFrame === frame) return;
      frame = nextFrame;
      frame.title = label;
      frame.addEventListener("load", connected, { once: true });
    });
    observer.observe(host, { childList: true, subtree: true });
    watchdog = window.setTimeout(unavailable, timeoutMs);
    host.append(widget, loader);

    return () => {
      settled = true;
      observer.disconnect();
      if (watchdog) window.clearTimeout(watchdog);
      frame?.removeEventListener("load", connected);
      loader.removeEventListener("error", unavailable);
      host.replaceChildren();
    };
  }, [configJson, label, script, timeoutMs]);

  return (
    <div className={`${className} is-${state}`} data-live-widget-state={state}>
      <div ref={hostRef} className="tradingview-widget-container" aria-label={label} />
      {state === "loading" && <div className="live-widget-loading" role="status"><i aria-hidden="true" />Connecting to market data…</div>}
    </div>
  );
}

export function LiveTickerTape() {
  const [state, setState] = useState<LiveWidgetState>("loading");
  return (
    <section className="market-pulse" aria-label="Market now ticker tape">
      <div className="market-pulse-label">
        <span><i aria-hidden="true" />Market now</span>
        <small>Cboe via TradingView</small>
      </div>
      {state === "unavailable" ? (
        <div className="market-pulse-unavailable" role="status">Market pulse unavailable <span>· Historical research remains ready</span></div>
      ) : (
        <TradingViewEmbed
          className="market-pulse-widget"
          script="tape"
          label="Current US market ticker tape"
          onStateChange={setState}
          config={{
            symbols: MARKET_PULSE_SYMBOLS,
            showSymbolLogo: true,
            colorTheme: "light",
            isTransparent: true,
            displayMode: "adaptive",
            locale: "en",
          }}
        />
      )}
    </section>
  );
}

export function LiveMarketChart({ ticker, onUnavailable }: { readonly ticker: PrimaryTicker; readonly onUnavailable: () => void }) {
  const symbol = TRADING_VIEW_SYMBOLS[ticker];
  return (
    <figure className="live-market-card" aria-labelledby="live-market-title">
      <div className="live-market-heading">
        <div>
          <span className="dashboard-kicker">5-minute view · display only</span>
          <h2 id="live-market-title">{ticker} market now</h2>
        </div>
        <span className="live-source"><i aria-hidden="true" />Cboe via TradingView</span>
      </div>
      <TradingViewEmbed
        className="live-chart-widget"
        script="chart"
        label={`${ticker} interactive current market chart`}
        onStateChange={(state) => { if (state === "unavailable") onUnavailable(); }}
        config={{
          autosize: true,
          symbol,
          interval: "5",
          timezone: "America/New_York",
          theme: "light",
          style: "1",
          locale: "en",
          allow_symbol_change: false,
          calendar: false,
          support_host: "https://www.tradingview.com",
        }}
      />
      <figcaption>Current market context may be delayed. It is not cutoff-qualified research evidence and is never cited by the agent.</figcaption>
    </figure>
  );
}
