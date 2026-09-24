import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Company, Coverage, Dashboard as DashboardData, WatchRow } from "../api/types";
import { formatPrice, PriceChart } from "../components/PriceChart";
import { dateLabel, formatDuration, formatPercent, words } from "../format";
import "../components/market-dashboard.css";

export interface DashboardProps {
  readonly companies: readonly Company[];
  readonly coverage: Coverage | null;
  readonly onOpenResearch: (seed: { ticker: string; asOf: string }) => void;
  readonly onOpenTechnology: () => void;
}

const formatRatio = (value: number | null) => value === null ? "n/a" : `${value.toFixed(2)}×`;
const metricClass = (value: number | null) => value === null ? "neutral" : value > 0 ? "positive" : value < 0 ? "negative" : "neutral";

function MoveRanking({ rows, selected }: { rows: readonly WatchRow[]; selected: string }) {
  const ranked = [...rows].sort((left, right) => Math.abs(right.benchmark_relative_return_pp ?? 0) - Math.abs(left.benchmark_relative_return_pp ?? 0));
  const maximum = Math.max(...ranked.map((row) => Math.abs(row.benchmark_relative_return_pp ?? 0)), 0.01);
  return (
    <section className="move-ranking" aria-labelledby="move-ranking-title">
      <div className="dashboard-section-heading compact">
        <div><span className="dashboard-kicker">Benchmark adjusted</span><h2 id="move-ranking-title">Comparable moves</h2></div>
        <span className="ranking-note">1 session, pp</span>
      </div>
      <ol>
        {ranked.map((row) => {
          const value = row.benchmark_relative_return_pp;
          return (
            <li key={row.ticker} className={row.ticker === selected ? "selected" : undefined}>
              <strong>{row.ticker}<span className="sr-only">{row.ticker === selected ? " selected" : ""}</span></strong>
              <span className="ranking-track" aria-hidden="true"><i className={metricClass(value)} style={{ width: `${value === null ? 0 : Math.max(4, Math.abs(value) / maximum * 100)}%` }} /></span>
              <span className={metricClass(value)}>{value === null ? "n/a" : `${value > 0 ? "+" : ""}${value.toFixed(2)} pp`}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Loading() {
  return (
    <div className="dashboard-loading" role="status" aria-live="polite">
      <span className="dashboard-spinner" aria-hidden="true" />
      <div><strong>Loading market context</strong><p>Reading the local historical store and cutoff-qualified documents.</p></div>
    </div>
  );
}

export function Dashboard({ companies, coverage, onOpenResearch, onOpenTechnology }: DashboardProps) {
  const [ticker, setTicker] = useState("");
  const [asOf, setAsOf] = useState("");
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // Pick defaults once status arrives; later status polls must not refetch.
  const defaultTicker = companies[0]?.symbol ?? "";
  const lastSession = coverage?.last_session ?? "";
  useEffect(() => { if (!ticker && defaultTicker) setTicker(defaultTicker); }, [defaultTicker, ticker]);
  useEffect(() => { if (!asOf && lastSession) setAsOf(lastSession); }, [asOf, lastSession]);

  useEffect(() => {
    if (!ticker || !asOf) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api.dashboard(ticker, asOf, controller.signal)
      .then(setData)
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "The dashboard is unavailable."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [ticker, asOf, attempt]);

  const selected = data?.watchlist.find((row) => row.ticker === data.selected_ticker) ?? null;
  const name = (symbol: string) => companies.find((company) => company.symbol === symbol)?.name ?? symbol;

  return (
    <main className="market-dashboard">
      <section className="dashboard-hero" aria-labelledby="dashboard-title">
        <div className="dashboard-hero-copy">
          <span className="dashboard-kicker"><i />Point-in-time market desk</span>
          <h1 id="dashboard-title">Your market research desk.</h1>
          <p>Inspect verified history at any cutoff, then ask the research agent what moved and why.</p>
        </div>
        <div className="dashboard-controls">
          <label><span>Company</span>
            <select value={ticker} disabled={!companies.length} onChange={(event) => setTicker(event.target.value)}>
              {(data?.watchlist.map((row) => row.ticker) ?? companies.map((company) => company.symbol)).map((symbol) => <option value={symbol} key={symbol}>{symbol} · {name(symbol)}</option>)}
            </select>
          </label>
          <label><span>As of</span>
            <input type="date" value={asOf} min={coverage?.first_session} max={coverage?.last_session} disabled={!coverage} onChange={(event) => { if (event.target.value) setAsOf(event.target.value); }} />
          </label>
        </div>
      </section>

      {(!data && !error) && <Loading />}
      {error && (
        <section className="dashboard-error" role="alert">
          <div><span>Snapshot unavailable</span><h2>We could not load this historical view.</h2><p>{error}</p></div>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry snapshot</button>
        </section>
      )}

      {data && selected && (
        <>
          {loading && <div className="dashboard-refreshing" role="status"><span className="dashboard-spinner" aria-hidden="true" />Updating history for {ticker}…</div>}
          <section className="snapshot-bar" aria-label="Snapshot provenance">
            <div><span>Selected session</span><strong>{dateLabel(data.resolved_session)}</strong></div>
            {data.requested_as_of !== data.resolved_session && <div><span>Requested cutoff</span><strong>{dateLabel(data.requested_as_of)}</strong></div>}
            <div><span>Scenario</span><strong>{data.coverage.scenario_id}</strong></div>
            <div><span>GPU receipt</span><strong>{data.receipt.engine} on {data.receipt.device} · {formatDuration(data.receipt.duration_ms)}</strong></div>
          </section>

          <div className="dashboard-grid">
            <div className="dashboard-main-column">
              <div className="market-view-shell">
                {data.series.points.length > 0 ? <PriceChart data={data} /> : <p className="evidence-empty">No price series is available for this cutoff.</p>}
                <div className="market-research-handoff">
                  <span>Take this {data.selected_ticker} cutoff into the agent.</span>
                  <button type="button" onClick={() => onOpenResearch({ ticker: data.selected_ticker, asOf: data.resolved_session })}>Research {data.selected_ticker} <span aria-hidden="true">→</span></button>
                </div>
              </div>
              <section className="company-overview" aria-labelledby="company-title">
                <div className="company-overview-heading">
                  <div><span className="dashboard-kicker">Historical snapshot · {selected.benchmark} benchmark</span><h2 id="company-title">{name(selected.ticker)} <span>{selected.ticker}</span></h2></div>
                  <div className="company-price"><strong>{formatPrice(selected.close)}</strong><span className={metricClass(selected.return_1d_pct)}>{formatPercent(selected.return_1d_pct)} on session</span><small>At {dateLabel(data.resolved_session)} close</small></div>
                </div>
                <dl className="market-metrics">
                  <div><dt>1 session</dt><dd className={metricClass(selected.return_1d_pct)}>{formatPercent(selected.return_1d_pct)}</dd></div>
                  <div><dt>5 sessions</dt><dd className={metricClass(selected.return_5d_pct)}>{formatPercent(selected.return_5d_pct)}</dd></div>
                  <div><dt>Opening gap</dt><dd className={metricClass(selected.opening_gap_pct)}>{formatPercent(selected.opening_gap_pct)}</dd></div>
                  <div><dt>Volume / 20d median</dt><dd>{formatRatio(selected.volume_ratio)}</dd></div>
                </dl>
                <div className={`shock-state ${selected.is_shock ? "detected" : "clear"}`}><i />{selected.is_shock === null ? "Shock classification unavailable" : selected.is_shock ? "Unusual move detected" : "No unusual move at this cutoff"}</div>
              </section>
              <MoveRanking rows={data.watchlist} selected={data.selected_ticker} />
            </div>

            <aside className="watchlist-panel" aria-labelledby="watchlist-title">
              <div className="dashboard-section-heading compact"><div><span className="dashboard-kicker">Research universe</span><h2 id="watchlist-title">Watchlist</h2></div></div>
              <div className="watchlist-rows">
                {data.watchlist.map((row) => (
                  <button type="button" key={row.ticker} aria-pressed={row.ticker === ticker} onClick={() => setTicker(row.ticker)}>
                    <span className="watchlist-identity"><strong>{row.ticker}</strong><small>{name(row.ticker)}</small></span>
                    <span className="watchlist-price">{formatPrice(row.close)}<small className={metricClass(row.return_1d_pct)}>{formatPercent(row.return_1d_pct)} · 1d</small></span>
                  </button>
                ))}
              </div>
              <button className="technology-link" type="button" onClick={onOpenTechnology}><span>Built on DGX Spark</span><strong>See NVIDIA × LangChain →</strong></button>
            </aside>

            <aside className="evidence-panel" aria-labelledby="evidence-title">
              <div className="dashboard-section-heading compact">
                <div><span className="dashboard-kicker">Available by cutoff</span><h2 id="evidence-title">Company documents</h2></div>
                <span className="evidence-count">{data.documents.length}</span>
              </div>
              {data.documents.length ? <div className="evidence-list">{data.documents.map((item) => (
                <article key={`${item.title}-${item.available_at}`}>
                  <div><span>{words(item.source_type)}</span><time dateTime={item.available_at}>{dateLabel(item.available_at)}</time></div>
                  <h3>{item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.title}<span className="sr-only"> (opens in a new tab)</span></a> : item.title}</h3>
                  <p>{item.excerpt}</p>
                </article>
              ))}</div> : <div className="evidence-empty"><strong>No company documents</strong><p>No filing, release, or news item was available by this cutoff.</p></div>}
            </aside>
          </div>

          {data.limitations.length > 0 && (
            <footer className="dashboard-provenance">
              <details><summary>Snapshot limitations ({data.limitations.length})</summary><ul>{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>
            </footer>
          )}
        </>
      )}
    </main>
  );
}
