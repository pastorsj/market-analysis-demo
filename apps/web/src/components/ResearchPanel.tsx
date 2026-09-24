import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { Investigation, ProgressEvent, Span } from "../api/types";
import { formatDuration, words } from "../format";
import { latestSpans, spanSummary, Trajectory } from "./Trajectory";
import "./research-panel.css";

export interface GanttBar { readonly span: Span; readonly startMs: number; readonly durationMs: number; readonly left: number; readonly width: number }
export interface GanttTurn { readonly turn: number; readonly totalMs: number; readonly bars: readonly GanttBar[] }

/** Lay each turn's spans on one clock: offsets and widths as percentages of the turn's wall time. */
export function projectGantt(events: readonly ProgressEvent[], nowMs = Date.now()): GanttTurn[] {
  const turns = new Map<number, Span[]>();
  for (const { turn, span } of latestSpans(events)) turns.set(turn, [...(turns.get(turn) ?? []), span]);
  return [...turns.entries()].map(([turn, spans]) => {
    const origin = Math.min(...spans.map((span) => Date.parse(span.started_at)));
    const timed = spans.map((span) => {
      const start = Date.parse(span.started_at) - origin;
      const end = (span.ended_at ? Date.parse(span.ended_at) : span.state === "running" ? nowMs : Date.parse(span.started_at)) - origin;
      return { span, startMs: start, durationMs: Math.max(0, end - start) };
    });
    const totalMs = Math.max(1, ...timed.map((bar) => bar.startMs + bar.durationMs));
    return { turn, totalMs, bars: timed.map((bar) => ({ ...bar, left: bar.startMs / totalMs * 100, width: bar.durationMs / totalMs * 100 })) };
  });
}

type Filter = "all" | Span["kind"];
const FILTERS: readonly Filter[] = ["all", "tool", "model", "skill"];

function Timeline({ events }: { events: readonly ProgressEvent[] }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [inspected, setInspected] = useState<GanttBar | null>(null);
  const [clock, setClock] = useState(Date.now());
  const running = events.some((event) => event.span.state === "running");
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setClock(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [running]);
  const turns = useMemo(() => projectGantt(events, clock), [clock, events]);
  if (!turns.length) return <div className="research-scroll"><p className="research-empty">Timing appears when the investigation starts.</p></div>;
  return (
    <div className="research-scroll performance-view">
      <section className="performance-gantt" aria-labelledby="gantt-heading" onMouseLeave={() => setInspected(null)}>
        <header className="gantt-heading"><div><span>Progress</span><h3 id="gantt-heading">Execution timeline</h3></div></header>
        <div className="span-filters" role="group" aria-label="Filter timeline steps">
          {FILTERS.map((item) => <button type="button" aria-pressed={filter === item} key={item} onClick={() => setFilter(item)}>{item === "all" ? "All steps" : `${words(item)}s`}</button>)}
        </div>
        {turns.map(({ turn, totalMs, bars }) => {
          const visible = bars.filter((bar) => filter === "all" || bar.span.kind === filter);
          return (
            <section className="performance-turn" key={turn} aria-label={`Turn ${turn} timeline`}>
              <header className="performance-summary"><div><span>Turn {turn}</span><strong>Total wall time {formatDuration(totalMs)}</strong></div></header>
              <ul className="gantt-rows">
                {visible.length ? visible.map((bar) => (
                  <li
                    className={`gantt-row ${bar.span.state}${inspected?.span.span_id === bar.span.span_id ? " inspected" : ""}`}
                    data-kind={bar.span.kind}
                    tabIndex={0}
                    key={bar.span.span_id}
                    aria-label={`${words(bar.span.name)}, ${bar.span.kind}, ${bar.span.state}, starts at ${formatDuration(bar.startMs)}, lasts ${formatDuration(bar.durationMs)}`}
                    onMouseEnter={() => setInspected(bar)}
                    onFocus={() => setInspected(bar)}
                  >
                    <span className="gantt-label"><span className="span-state" aria-hidden="true" /><span><strong>{words(bar.span.name)}</strong><small>{bar.span.state} · {formatDuration(bar.durationMs)}</small></span></span>
                    <span className="gantt-track" aria-hidden="true"><span className="gantt-bar" style={{ left: `${bar.left}%`, width: `${Math.min(Math.max(0.8, bar.width), Math.max(0.2, 100 - bar.left))}%` }} /></span>
                  </li>
                )) : <li className="research-empty gantt-empty">No {filter} steps in this turn.</li>}
              </ul>
            </section>
          );
        })}
        <div className="gantt-inspector" aria-live="off">
          {inspected ? <><header><span>Step detail</span><strong>{words(inspected.span.name)}</strong></header><p>{spanSummary(inspected.span) || "No additional detail."}</p></> : <p>Hover or focus a step to inspect its tool, model, or GPU receipt.</p>}
        </div>
      </section>
    </div>
  );
}

type Tab = "activity" | "timeline";
const TABS: readonly Tab[] = ["activity", "timeline"];

function useDrawerMode(): boolean {
  const query = "(max-width: 1180px)";
  const [drawer, setDrawer] = useState(() => typeof matchMedia === "function" && matchMedia(query).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const media = matchMedia(query), update = () => setDrawer(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return drawer;
}

export function ResearchPanel({ investigation }: { investigation: Investigation }) {
  const [tab, setTab] = useState<Tab>("activity");
  const [open, setOpen] = useState(false);
  const drawerMode = useDrawerMode();
  const trigger = useRef<HTMLButtonElement>(null), close = useRef<HTMLButtonElement>(null), tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  const events = investigation.events;
  const runningCount = latestSpans(events).filter((row) => row.span.state === "running").length;

  useEffect(() => {
    if (!drawerMode || !open) return;
    close.current?.focus();
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", escape);
    const opener = trigger.current;
    return () => { window.removeEventListener("keydown", escape); opener?.focus(); };
  }, [drawerMode, open]);

  const tabKey = (event: KeyboardEvent, current: Tab) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const index = TABS.indexOf(current);
    const next = event.key === "Home" ? TABS[0] : event.key === "End" ? TABS[TABS.length - 1] : TABS[(index + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
    setTab(next);
    tabRefs.current[next]?.focus();
  };

  return (
    <>
      <button ref={trigger} type="button" className="show-analysis" aria-controls="research-workspace" aria-expanded={open} onClick={() => setOpen(true)}>Show progress <span aria-hidden="true">↗</span></button>
      {drawerMode && open && <div className="research-scrim" aria-hidden="true" onMouseDown={() => setOpen(false)} />}
      <aside
        id="research-workspace"
        className={`research-panel research-workspace${open ? " open" : ""}`}
        role={drawerMode ? "dialog" : "region"}
        aria-modal={drawerMode && open ? "true" : undefined}
        aria-hidden={drawerMode && !open ? "true" : undefined}
        aria-labelledby="research-heading"
      >
        <div className="panel-heading research-heading">
          <div><h2 id="research-heading">Research progress</h2><span role="status" aria-live="polite">{runningCount ? `${runningCount} running` : `${latestSpans(events).length} recorded steps`}</span></div>
          <button ref={close} type="button" className="close-analysis" aria-label="Close progress" onClick={() => setOpen(false)}>×</button>
        </div>
        <div className="research-tabs" role="tablist" aria-label="Progress view">
          {TABS.map((item) => (
            <button type="button" id={`research-tab-${item}`} role="tab" aria-selected={tab === item} aria-controls={`research-panel-${item}`} tabIndex={tab === item ? 0 : -1} key={item} ref={(node) => { tabRefs.current[item] = node; }} onClick={() => setTab(item)} onKeyDown={(event) => tabKey(event, item)}>
              {item === "activity" ? "Activity" : "Timeline"}
            </button>
          ))}
        </div>
        <div className="research-content">
          <div id={`research-panel-${tab}`} role="tabpanel" aria-labelledby={`research-tab-${tab}`}>
            {tab === "activity" ? <Trajectory events={events} /> : <Timeline events={events} />}
          </div>
        </div>
      </aside>
    </>
  );
}
