import { useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Dashboard } from "../api/types";
import { dateLabel, formatPercent } from "../format";

export const formatPrice = (value: number | null) => value === null
  ? "n/a"
  : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
const formatVolume = (value: number) => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);

export function PriceChart({ data }: { data: Dashboard }) {
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
