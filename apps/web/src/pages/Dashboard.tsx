import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { getMarketDashboard, type DashboardWatchlistRow, type MarketDashboardData } from "../api/dashboard";
import type { PrimaryTicker, SystemStatus } from "../api/types";
import { LiveMarketChart, LiveTickerTape } from "./LiveMarket";
import "./market-dashboard.css";

export interface DashboardResearchSeed {
  readonly ticker: PrimaryTicker;
  readonly asOf: string;
}

export interface MarketDashboardProps {
  readonly status: SystemStatus | null;
  readonly onOpenResearch: (seed?: DashboardResearchSeed) => void;
  readonly onOpenTechnology: () => void;
}

const formatPrice = (value: number | null) => value === null
  ? "Unavailable"
  : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);

const formatPercent = (value: number | null) => value === null
  ? "Unavailable"
  : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;

const formatRatio = (value: number | null) => value === null ? "Unavailable" : `${value.toFixed(2)}×`;
const formatVolume = (value: number) => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
const metricClass = (value: number | null) => value === null ? "neutral" : value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
const sourceLabel = (source: MarketDashboardData["evidence"][number]["source_type"]) => ({
  market: "Market record", news: "Primary source", filing: "Filing", release: "Company release", relationship: "Relationship", model: "Computed evidence",
})[source];

function dateLabel(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
}

function companyName(status: SystemStatus, ticker: PrimaryTicker): string {
  return status.companies.find((company) => company.symbol === ticker)?.display_name ?? ticker;
}

function PriceChart({ data }: { data: MarketDashboardData }) {
  type ChartRange = "1M" | "3M" | "MAX";
  const allPoints = data.series.points;
  const [range, setRange] = useState<ChartRange>("3M");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const explorerRef = useRef<HTMLDivElement>(null);
  const descriptionId = useId();
  const instructionsId = useId();
  const rangeOptions: readonly { value: ChartRange; label: string; sessions: number }[] = [
    { value: "1M", label: "1M", sessions: 21 },
    { value: "3M", label: "3M", sessions: 63 },
    ...(allPoints.length > 63 ? [{ value: "MAX" as const, label: "Max", sessions: allPoints.length }] : []),
  ];
  const sessions = rangeOptions.find((option) => option.value === range)?.sessions ?? allPoints.length;
  const points = allPoints.slice(-sessions);
  const first = points[0], last = points.at(-1)!;
  const tickerReturn = (index: number) => (points[index].ticker_close / first.ticker_close - 1) * 100;
  const benchmarkReturn = (index: number) => (points[index].benchmark_close / first.benchmark_close - 1) * 100;
  const width = 600, height = 360, left = 42, right = 16, top = 18, bottom = 34, volumeHeight = 52, volumeGap = 18;
  const plotWidth = width - left - right, volumeBottom = height - bottom, volumeTop = volumeBottom - volumeHeight;
  const priceBottom = volumeTop - volumeGap, plotHeight = priceBottom - top;
  const values = points.flatMap((_, index) => [tickerReturn(index), benchmarkReturn(index)]);
  const rawMin = Math.min(...values, 0), rawMax = Math.max(...values, 0);
  const padding = Math.max((rawMax - rawMin) * 0.12, 0.5);
  const minimum = rawMin - padding, maximum = rawMax + padding, span = maximum - minimum;
  const x = (index: number) => left + (points.length === 1 ? plotWidth / 2 : index / (points.length - 1) * plotWidth);
  const y = (value: number) => top + (maximum - value) / span * plotHeight;
  const path = (series: "ticker" | "benchmark") => points
    .map((_, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(series === "ticker" ? tickerReturn(index) : benchmarkReturn(index)).toFixed(1)}`)
    .join(" ");
  const maximumVolume = Math.max(...points.map((point) => point.volume), 1);
  const barWidth = Math.max(1.5, Math.min(11, plotWidth / points.length * .68));
  const activePoint = activeIndex === null ? null : points[activeIndex];
  const activeTickerReturn = activeIndex === null ? null : tickerReturn(activeIndex);
  const activeBenchmarkReturn = activeIndex === null ? null : benchmarkReturn(activeIndex);

  const selectPointFromPointer = (event: PointerEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const chartX = (event.clientX - bounds.left) / bounds.width * width;
    const ratio = Math.max(0, Math.min(1, (chartX - left) / plotWidth));
    setActiveIndex(Math.round(ratio * (points.length - 1)));
  };

  const moveSelection = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = activeIndex ?? points.length - 1;
    let next = current;
    if (event.key === "ArrowLeft") next = Math.max(0, current - 1);
    else if (event.key === "ArrowRight") next = Math.min(points.length - 1, current + 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = points.length - 1;
    else return;
    event.preventDefault();
    setActiveIndex(next);
  };

  return (
    <figure className="market-chart-card" aria-labelledby="market-chart-title">
      <div className="dashboard-section-heading">
        <div>
          <span className="dashboard-kicker">{points.length}-session window</span>
          <h2 id="market-chart-title">Relative performance</h2>
        </div>
        <div className="chart-heading-tools">
          <div className="chart-range" role="group" aria-label="Historical chart range">
            {rangeOptions.map((option) => (
              <button
                type="button"
                key={option.value}
                aria-pressed={range === option.value}
                onClick={() => { setRange(option.value); setActiveIndex(null); }}
              >{option.label}</button>
            ))}
          </div>
          <div className="chart-legend" aria-label="Chart legend">
            <span><i className="ticker-line" />{data.series.ticker}</span>
            <span><i className="benchmark-line" />{data.series.benchmark}</span>
          </div>
        </div>
      </div>
      <div
        className="market-chart-explorer"
        ref={explorerRef}
        role="group"
        aria-roledescription="interactive historical chart"
        aria-label={`${data.series.ticker} and ${data.series.benchmark} performance`}
        aria-describedby={instructionsId}
        tabIndex={0}
        onFocus={() => setActiveIndex((current) => current ?? points.length - 1)}
        onBlur={() => setActiveIndex(null)}
        onKeyDown={moveSelection}
        onPointerLeave={() => { if (document.activeElement !== explorerRef.current) setActiveIndex(null); }}
      >
        <svg className="market-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={`market-chart-title ${descriptionId}`} onPointerDown={selectPointFromPointer} onPointerMove={selectPointFromPointer}>
          <desc id={descriptionId}>
            Normalized return for {data.series.ticker} and {data.series.benchmark} from {first.session_date} through {last.session_date}.
            {data.series.ticker} ends at {formatPercent(tickerReturn(points.length - 1))} and {data.series.benchmark} at {formatPercent(benchmarkReturn(points.length - 1))}.
          </desc>
          <line className="chart-grid-zero" x1={left} x2={width - right} y1={y(0)} y2={y(0)} />
          <text className="chart-axis-label" x={left - 8} y={top + 4} textAnchor="end">{maximum.toFixed(1)}%</text>
          <text className="chart-axis-label" x={left - 8} y={priceBottom} textAnchor="end">{minimum.toFixed(1)}%</text>
          <text className="chart-axis-label chart-volume-label" x={left - 8} y={volumeTop + 8} textAnchor="end">Vol</text>
          <line className="chart-volume-baseline" x1={left} x2={width - right} y1={volumeBottom} y2={volumeBottom} />
          {points.map((point, index) => {
            const barHeight = point.volume / maximumVolume * volumeHeight;
            return <rect key={point.session_date} className={`chart-volume-bar${activeIndex === index ? " active" : ""}`} x={x(index) - barWidth / 2} y={volumeBottom - barHeight} width={barWidth} height={barHeight} />;
          })}
          <text className="chart-axis-label" x={left} y={height - 8}>{first.session_date}</text>
          <text className="chart-axis-label" x={width - right} y={height - 8} textAnchor="end">{last.session_date}</text>
          <path className="chart-path benchmark" d={path("benchmark")} />
          <path className="chart-path ticker" d={path("ticker")} />
          <circle className="chart-end benchmark" cx={x(points.length - 1)} cy={y(benchmarkReturn(points.length - 1))} r="4"><title>{data.series.benchmark}: {formatPercent(benchmarkReturn(points.length - 1))}</title></circle>
          <circle className="chart-end ticker" cx={x(points.length - 1)} cy={y(tickerReturn(points.length - 1))} r="4"><title>{data.series.ticker}: {formatPercent(tickerReturn(points.length - 1))}</title></circle>
          {activePoint && activeIndex !== null && activeTickerReturn !== null && activeBenchmarkReturn !== null && (
            <g className="chart-active-point" aria-hidden="true">
              <line x1={x(activeIndex)} x2={x(activeIndex)} y1={top} y2={volumeBottom} />
              <circle className="benchmark" cx={x(activeIndex)} cy={y(activeBenchmarkReturn)} r="4" />
              <circle className="ticker" cx={x(activeIndex)} cy={y(activeTickerReturn)} r="4.5" />
            </g>
          )}
        </svg>
        {activePoint && activeIndex !== null && activeTickerReturn !== null && activeBenchmarkReturn !== null && (
          <div
            className={`chart-tooltip ${activeIndex > (points.length - 1) / 2 ? "before" : "after"}`}
            style={{ left: `${x(activeIndex) / width * 100}%` }}
            aria-hidden="true"
          >
            <strong>{dateLabel(activePoint.session_date)}</strong>
            <span><i className="ticker-dot" />{data.series.ticker}<b>{formatPrice(activePoint.ticker_close)} · {formatPercent(activeTickerReturn)}</b></span>
            <span><i className="benchmark-dot" />{data.series.benchmark}<b>{formatPrice(activePoint.benchmark_close)} · {formatPercent(activeBenchmarkReturn)}</b></span>
            <span className="chart-tooltip-volume">Volume<b>{formatVolume(activePoint.volume)} shares</b></span>
          </div>
        )}
        <span className="sr-only" id={instructionsId}>Hover over the chart, or focus it and use the left and right arrow keys, to inspect each session.</span>
        <span className="sr-only" aria-live="polite">{activePoint && activeTickerReturn !== null && activeBenchmarkReturn !== null ? `${dateLabel(activePoint.session_date)}. ${data.series.ticker} ${formatPrice(activePoint.ticker_close)}, ${formatPercent(activeTickerReturn)}. ${data.series.benchmark} ${formatPrice(activePoint.benchmark_close)}, ${formatPercent(activeBenchmarkReturn)}. Volume ${activePoint.volume.toLocaleString()} shares.` : ""}</span>
      </div>
      <figcaption>Rebased to the first visible session. Bars show daily share volume. Hover or focus the chart for details. Adjusted historical observations, not a live quote.</figcaption>
    </figure>
  );
}

function MoveRanking({ rows, selected }: { rows: readonly DashboardWatchlistRow[]; selected: PrimaryTicker }) {
  const ranked = [...rows].sort((left, right) => Math.abs(right.market_adjusted_return_pct ?? 0) - Math.abs(left.market_adjusted_return_pct ?? 0));
  const maximum = Math.max(...ranked.map((row) => Math.abs(row.market_adjusted_return_pct ?? 0)), 0.01);
  return (
    <section className="move-ranking" aria-labelledby="move-ranking-title">
      <div className="dashboard-section-heading compact">
        <div><span className="dashboard-kicker">Benchmark adjusted</span><h2 id="move-ranking-title">Comparable moves</h2></div>
        <span className="ranking-note">1 session</span>
      </div>
      <ol>
        {ranked.map((row) => {
          const value = row.market_adjusted_return_pct;
          return (
            <li key={row.ticker} className={row.ticker === selected ? "selected" : undefined}>
              <strong>{row.ticker}<span className="sr-only">{row.ticker === selected ? " selected" : ""}</span></strong>
              <span className="ranking-track" aria-hidden="true"><i className={metricClass(value)} style={{ width: `${value === null ? 0 : Math.max(4, Math.abs(value) / maximum * 100)}%` }} /></span>
              <span className={metricClass(value)}>{formatPercent(value)}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function DashboardSkeleton() {
  return (
    <div className="dashboard-loading" role="status" aria-live="polite">
      <span className="dashboard-spinner" aria-hidden="true" />
      <div><strong>Loading verified market context</strong><p>Reading the local historical store and cutoff-qualified evidence.</p></div>
    </div>
  );
}

export function MarketDashboard({ status, onOpenResearch, onOpenTechnology }: MarketDashboardProps) {
  const initialTicker = status?.supported_tickers.includes("NVDA") ? "NVDA" : status?.supported_tickers[0] ?? "NVDA";
  const [ticker, setTicker] = useState<PrimaryTicker>(initialTicker);
  const [asOf, setAsOf] = useState(status?.coverage.last_session ?? "");
  const [data, setData] = useState<MarketDashboardData | null>(null);
  const [loading, setLoading] = useState(Boolean(status));
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [marketView, setMarketView] = useState<"live" | "historical">("live");
  const [liveFallback, setLiveFallback] = useState(false);
  const [liveAttempt, setLiveAttempt] = useState(0);

  useEffect(() => {
    if (!status) return;
    setTicker((current) => status.supported_tickers.includes(current) ? current : status.supported_tickers[0]);
    setAsOf((current) => current >= status.coverage.first_session && current <= status.coverage.last_session ? current : status.coverage.last_session);
  }, [status]);

  useEffect(() => {
    if (!status || !asOf || !status.supported_tickers.includes(ticker)) {
      setLoading(false);
      setData(null);
      return;
    }
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setError(null);
    void getMarketDashboard(ticker, asOf, controller.signal).then((value) => {
      if (active) setData(value);
    }).catch((reason: unknown) => {
      if (!active || reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "Historical dashboard is unavailable.");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; controller.abort(); };
  }, [asOf, attempt, status, ticker]);

  const contentTicker = data?.selected_ticker ?? ticker;
  const selected = data?.watchlist.find((row) => row.ticker === contentTicker) ?? null;
  const name = status ? companyName(status, contentTicker) : contentTicker;
  return (
    <main className="market-dashboard">
      <LiveTickerTape />
      <section className="dashboard-hero" aria-labelledby="dashboard-title">
        <div className="dashboard-hero-copy">
          <span className="dashboard-kicker"><i />Personal market desk</span>
          <h1 id="dashboard-title">Your market research desk.</h1>
          <p>Watch the market now, inspect verified history, then ask the research agent what moved and why.</p>
        </div>
      </section>

      {!status && <DashboardSkeleton />}
      {status && loading && !data && <DashboardSkeleton />}
      {status && error && !loading && !data && (
        <section className="dashboard-error" role="alert">
          <div><span>Snapshot unavailable</span><h2>We could not load this historical view.</h2><p>{error}</p></div>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry snapshot</button>
        </section>
      )}

      {status && data && selected && (
        <>
          {loading && <div className="dashboard-refreshing" role="status"><span className="dashboard-spinner" aria-hidden="true" />Updating verified history for {ticker}…</div>}
          {error && !loading && <div className="dashboard-refresh-warning" role="status"><strong>Could not refresh this snapshot.</strong><span>{error}</span><button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry</button></div>}
          <section className="snapshot-bar" aria-label="Snapshot provenance">
            <div><span>Selected session</span><strong>{dateLabel(data.resolved_session)}</strong></div>
            {data.requested_as_of !== data.resolved_session && <div><span>Requested cutoff</span><strong>{dateLabel(data.requested_as_of)}</strong></div>}
            <div><span>Dataset</span><strong>{data.coverage.session_count.toLocaleString()} sessions</strong></div>
            <div><span>Vintage</span><strong>{data.coverage.vintage_status === "reconstructed_later" ? "Reconstructed later" : "Archived at cutoff"}</strong></div>
          </section>

          <div className="dashboard-grid">
            <div className="dashboard-main-column">
              <div className="market-view-shell">
                <div className="market-view-toolbar">
                  <div>
                    <span className="dashboard-kicker">Explore the market</span>
                    <strong>{marketView === "live" ? "Current market activity" : "Cutoff-qualified history"}</strong>
                    {liveFallback && marketView === "historical" && <small role="status">Live market unavailable — historical snapshot shown.</small>}
                  </div>
                  <label className="market-mobile-ticker">
                    <span>Company</span>
                    <select aria-label="Market chart company" value={ticker} onChange={(event) => setTicker(event.target.value as PrimaryTicker)}>
                      {status.companies.filter((company) => status.supported_tickers.includes(company.symbol)).map((company) => <option value={company.symbol} key={company.symbol}>{company.symbol} · {company.display_name}</option>)}
                    </select>
                  </label>
                  <div className="market-view-toggle" role="group" aria-label="Market chart view">
                    <button
                      type="button"
                      aria-pressed={marketView === "live"}
                      onClick={() => { setLiveFallback(false); setLiveAttempt((value) => value + 1); setMarketView("live"); }}
                    >
                      <i aria-hidden="true" />Market now
                    </button>
                    <button type="button" aria-pressed={marketView === "historical"} onClick={() => setMarketView("historical")}>Historical research</button>
                  </div>
                </div>
                {marketView === "live" ? (
                  <LiveMarketChart
                    key={`${ticker}-${liveAttempt}`}
                    ticker={ticker}
                    onUnavailable={() => { setLiveFallback(true); setMarketView("historical"); }}
                  />
                ) : <PriceChart data={data} />}
                <div className="market-research-handoff">
                  <span>{marketView === "live" ? "Want the evidence behind a move?" : `Take this ${contentTicker} cutoff into the agent.`}</span>
                  <button type="button" disabled={loading} onClick={() => onOpenResearch({ ticker: contentTicker, asOf: data.resolved_session })}>Research {contentTicker} <span aria-hidden="true">→</span></button>
                </div>
              </div>
              <section className="company-overview" aria-labelledby="company-title">
                <div className="company-overview-heading">
                  <div><span className="dashboard-kicker">Historical snapshot · {selected.benchmark} benchmark · {selected.outcome.replace("_", " ")}</span><h2 id="company-title">{name} <span>{contentTicker}</span></h2></div>
                  <div className="company-price"><strong>{formatPrice(selected.close)}</strong><span className={metricClass(selected.return_1_session_pct)}>{formatPercent(selected.return_1_session_pct)} on session</span><small>At {dateLabel(data.resolved_session)} cutoff</small></div>
                </div>
                <dl className="market-metrics">
                  <div><dt>1 session</dt><dd className={metricClass(selected.return_1_session_pct)}>{formatPercent(selected.return_1_session_pct)}</dd></div>
                  <div><dt>5 sessions</dt><dd className={metricClass(selected.return_5_sessions_pct)}>{formatPercent(selected.return_5_sessions_pct)}</dd></div>
                  <div><dt>vs {selected.benchmark}</dt><dd className={metricClass(selected.market_adjusted_return_pct)}>{formatPercent(selected.market_adjusted_return_pct)}</dd></div>
                  <div><dt>Volume / 20d median</dt><dd>{formatRatio(selected.volume_ratio)}</dd></div>
                </dl>
                <div className={`shock-state ${selected.is_shock ? "detected" : "clear"}`}><i />{selected.is_shock === null ? "Shock classification unavailable" : selected.is_shock ? "Unusual move detected" : "No qualified shock at this cutoff"}</div>
              </section>
              <MoveRanking rows={data.watchlist} selected={contentTicker} />
            </div>

            <aside className="watchlist-panel" aria-labelledby="watchlist-title">
              <div className="dashboard-section-heading compact"><div><span className="dashboard-kicker">Five companies</span><h2 id="watchlist-title">Watchlist</h2></div></div>
              <div className="watchlist-rows">
                {data.watchlist.map((row) => (
                  <button
                    type="button"
                    key={row.ticker}
                    aria-pressed={row.ticker === ticker}
                    onClick={() => setTicker(row.ticker)}
                  >
                    <span className="watchlist-identity"><strong>{row.ticker}</strong><small>{companyName(status, row.ticker)}</small></span>
                    <span className="watchlist-price">{formatPrice(row.close)}<small className={metricClass(row.return_1_session_pct)}>{formatPercent(row.return_1_session_pct)} · 1d</small></span>
                  </button>
                ))}
              </div>
              <button className="technology-link" type="button" onClick={onOpenTechnology}><span>Built on DGX Spark</span><strong>See NVIDIA × LangChain →</strong></button>
            </aside>

            <aside className="evidence-panel" aria-labelledby="evidence-title">
              <div className="dashboard-section-heading compact">
                <div><span className="dashboard-kicker">Available by cutoff</span><h2 id="evidence-title">Company evidence</h2></div>
                <span className="evidence-count">{data.evidence.length}</span>
              </div>
              {data.evidence.length ? <div className="evidence-list">{data.evidence.map((item) => (
                <article key={item.citation_id}>
                  <div><span>{sourceLabel(item.source_type)}</span><time dateTime={item.available_at}>{dateLabel(item.available_at.slice(0, 10))}</time></div>
                  <h3><a href={item.url} target="_blank" rel="noreferrer">{item.title}<span className="sr-only"> (opens in a new tab)</span></a></h3>
                  <p>{item.excerpt}</p>
                </article>
              ))}</div> : <div className="evidence-empty"><strong>No company evidence returned</strong><p>The price snapshot remains available, but this cutoff has no eligible filing, release, or primary-source match.</p></div>}
              <p className="evidence-boundary">This is cutoff-qualified company evidence, not a licensed live-news feed.</p>
            </aside>
          </div>

          <footer className="dashboard-provenance">
            <div><strong>Verified local computation</strong><span>{data.receipts.length} receipt{data.receipts.length === 1 ? "" : "s"} · no silent fallback</span></div>
            {data.limitations.length > 0 && <details><summary>Snapshot limitations ({data.limitations.length})</summary><ul>{data.limitations.map((limitation) => <li key={limitation.code}>{limitation.message}</li>)}</ul></details>}
          </footer>
        </>
      )}
    </main>
  );
}
