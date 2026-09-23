import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Citation, Investigation, ProgressKind, ProgressPayload, ProgressState, Report, RunStatus, TrajectoryEvent } from "../api/types";
import { validateProgressHistory } from "../api/types";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { Trajectory } from "./Trajectory";
import "./research-panel.css";

export interface ProjectedProgressSpan {
  readonly turnId: string;
  readonly spanId: string;
  readonly parentSpanId: string | null;
  readonly kind: ProgressKind;
  readonly displayName: string;
  readonly state: ProgressState;
  readonly startedAt: string;
  readonly completedAt: string | null;
  readonly startOffsetMs: number;
  readonly wallMs: number;
  readonly computeMs: number | null;
  readonly transportMs: number | null;
  readonly leftPercent: number;
  readonly widthPercent: number;
  readonly overlapGroup: number;
  readonly open: boolean;
  readonly detail: ProgressPayload;
}

export interface ProjectedProgressTurn {
  readonly turnId: string;
  readonly originMs: number;
  readonly envelopeMs: number;
  readonly timeToFirstResearchStepMs: number | null;
  readonly spans: readonly ProjectedProgressSpan[];
}

export interface ProgressProjection { readonly turns: readonly ProjectedProgressTurn[] }

export interface ProjectedGanttSpan {
  readonly span: ProjectedProgressSpan;
  readonly activeStartOffsetMs: number;
  readonly leftPercent: number;
  readonly widthPercent: number;
}

export interface ProjectedGanttTurn {
  readonly turn: ProjectedProgressTurn;
  readonly activeStartOffsetMs: number;
  readonly leftPercent: number;
  readonly widthPercent: number;
  readonly spans: readonly ProjectedGanttSpan[];
}

export interface GanttProjection {
  readonly activeRuntimeMs: number;
  readonly turns: readonly ProjectedGanttTurn[];
}

export function formatDuration(milliseconds: number): string {
  if (milliseconds < 1) return "<1 ms";
  if (milliseconds < 1_000) return `${Math.round(milliseconds)} ms`;
  const seconds = milliseconds / 1_000;
  return `${seconds < 10 ? seconds.toFixed(2) : seconds.toFixed(1)} s`;
}

export function projectProgressSpans(events: readonly TrajectoryEvent[], status: RunStatus, nowMs = Date.now()): ProgressProjection {
  validateProgressHistory(events, status);
  const pairs = new Map<string, { start: ProgressPayload; end: ProgressPayload | null; order: number }>();
  let order = 0;
  for (const event of events) {
    if (event.payload.schema_version !== "progress-span-v1") continue;
    const span = event.payload as unknown as ProgressPayload;
    if (span.state === "started") pairs.set(span.span_id, { start: span, end: null, order: order++ });
    else {
      const pair = pairs.get(span.span_id);
      if (pair) pair.end = span;
    }
  }
  const byTurn = new Map<string, Array<{ start: ProgressPayload; end: ProgressPayload | null; order: number }>>();
  for (const pair of pairs.values()) {
    const rows = byTurn.get(pair.start.turn_id) ?? [];
    rows.push(pair); byTurn.set(pair.start.turn_id, rows);
  }
  return {
    turns: [...byTurn.entries()].map(([turnId, pairsForTurn]) => {
      const originMs = Math.min(...pairsForTurn.map((pair) => Date.parse(pair.start.started_at)));
      const draft = pairsForTurn.sort((left, right) => left.order - right.order).map((pair) => {
        const detail = pair.end ?? pair.start;
        const startedMs = Date.parse(pair.start.started_at);
        const wallMs = pair.end?.elapsed_ms ?? Math.max(0, nowMs - startedMs);
        return {
          turnId,
          spanId: pair.start.span_id,
          parentSpanId: pair.start.parent_span_id,
          kind: pair.start.kind,
          displayName: pair.start.display_name,
          state: detail.state,
          startedAt: pair.start.started_at,
          completedAt: pair.end?.completed_at ?? null,
          startOffsetMs: Math.max(0, startedMs - originMs),
          wallMs,
          computeMs: detail.compute_ms,
          transportMs: detail.transport_ms,
          overlapGroup: 0,
          open: pair.end === null,
          detail,
          order: pair.order,
        };
      });
      const envelopeMs = Math.max(0, ...draft.map((span) => span.startOffsetMs + span.wallMs));
      let group = 0, groupEnd = -1;
      for (const span of [...draft].filter((row) => row.parentSpanId !== null).sort((left, right) => left.startOffsetMs - right.startOffsetMs || left.order - right.order)) {
        if (span.startOffsetMs >= groupEnd) { group += 1; groupEnd = span.startOffsetMs + span.wallMs; }
        else groupEnd = Math.max(groupEnd, span.startOffsetMs + span.wallMs);
        span.overlapGroup = group;
      }
      const denominator = Math.max(1, envelopeMs), childStarts = draft.filter((span) => span.parentSpanId !== null).map((span) => span.startOffsetMs);
      return {
        turnId,
        originMs,
        envelopeMs,
        timeToFirstResearchStepMs: childStarts.length ? Math.min(...childStarts) : null,
        spans: draft.map(({ order: _order, ...span }) => ({ ...span, leftPercent: span.startOffsetMs / denominator * 100, widthPercent: span.wallMs / denominator * 100 })),
      };
    }),
  };
}

export function projectActiveGantt(projection: ProgressProjection): GanttProjection {
  const activeRuntimeMs = projection.turns.reduce((total, turn) => total + turn.envelopeMs, 0);
  const denominator = Math.max(1, activeRuntimeMs);
  let cursor = 0;
  const turns = projection.turns.map((turn) => {
    const activeStartOffsetMs = cursor;
    cursor += turn.envelopeMs;
    return {
      turn,
      activeStartOffsetMs,
      leftPercent: activeStartOffsetMs / denominator * 100,
      widthPercent: turn.envelopeMs / denominator * 100,
      spans: turn.spans.map((span) => ({
        span,
        activeStartOffsetMs: activeStartOffsetMs + span.startOffsetMs,
        leftPercent: (activeStartOffsetMs + span.startOffsetMs) / denominator * 100,
        widthPercent: span.wallMs / denominator * 100,
      })),
    };
  });
  return { activeRuntimeMs, turns };
}

type ResearchTab = "activity" | "evidence" | "performance";
type SpanFilter = "all" | "tool" | "model" | "policy" | "planning";

const tabs: readonly ResearchTab[] = ["activity", "evidence", "performance"];
const filters: readonly SpanFilter[] = ["all", "tool", "model", "policy", "planning"];
const title = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const PANEL_MIN_WIDTH = 300;
const PANEL_DEFAULT_WIDTH = 360;
const PANEL_MAX_WIDTH = 720;
const PANEL_WIDTH_STEP = 40;
const DESKTOP_FIXED_COLUMNS_WIDTH = 730;

function availablePanelWidth(): number {
  if (typeof window === "undefined") return PANEL_MAX_WIDTH;
  return Math.max(PANEL_DEFAULT_WIDTH, Math.min(PANEL_MAX_WIDTH, window.innerWidth - DESKTOP_FIXED_COLUMNS_WIDTH));
}

function useDrawerMode(): boolean {
  const query = "(max-width: 1180px)";
  const [drawer, setDrawer] = useState(() => typeof matchMedia === "function" && matchMedia(query).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const media = matchMedia(query), update = () => setDrawer(media.matches); update();
    media.addEventListener("change", update); return () => media.removeEventListener("change", update);
  }, []);
  return drawer;
}

function reportsByTurn(record: Investigation, events: readonly TrajectoryEvent[], turns: readonly ProjectedProgressTurn[]): Map<string, Report> {
  const reports = new Map<string, Report>();
  for (const event of events) {
    const turn = event.payload.turn_id, report = event.payload.report;
    if (typeof turn === "string" && report && typeof report === "object") reports.set(turn, report as Report);
  }
  const latest = turns.at(-1)?.turnId ?? (record.turns.length ? `turn-${String(record.turns.length).padStart(4, "0")}` : null);
  if (latest && record.report) reports.set(latest, record.report);
  return reports;
}

function EvidencePanel({ record, events, projection, onCitation }: { record: Investigation; events: readonly TrajectoryEvent[]; projection: ProgressProjection; onCitation: (citation: Citation) => void }) {
  const reports = reportsByTurn(record, events, projection.turns);
  const content = projection.turns.map((turn) => {
    const report = reports.get(turn.turnId), citations = report?.citations ?? [];
    const byEvidence = new Map<string, Citation[]>();
    for (const citation of citations) byEvidence.set(citation.evidence_id, [...(byEvidence.get(citation.evidence_id) ?? []), citation]);
    const tools = turn.spans.filter((span) => span.kind === "tool" && !span.open);
    const previewIds = new Set(tools.flatMap((span) => span.detail.evidence_preview.map((preview) => preview.evidence_id)));
    if (!tools.length && !citations.length) return null;
    return (
      <section className="evidence-turn" data-turn-id={turn.turnId} key={turn.turnId} aria-labelledby={`evidence-${turn.turnId}`}>
        <header><h3 id={`evidence-${turn.turnId}`}>{title(turn.turnId)}</h3><span>{citations.length} final {citations.length === 1 ? "citation" : "citations"}</span></header>
        {tools.map((span) => (
          <section className="evidence-tool" data-span-id={span.spanId} data-span-kind="tool" key={span.spanId}>
            <h4>{span.displayName}</h4>
            {span.detail.evidence_preview.length ? span.detail.evidence_preview.map((preview) => {
              const matches = byEvidence.get(preview.evidence_id) ?? [], used = matches.length > 0;
              return (
                <article className={`evidence-card ${used ? "used" : "considered"}`} data-turn-id={turn.turnId} data-span-id={span.spanId} data-evidence-id={preview.evidence_id} data-evidence-state={used ? "final-cited" : "considered-not-cited"} key={preview.evidence_id}>
                  <div><span className="evidence-state">{used ? "Final-cited" : "Considered, not cited"}</span><span>{title(preview.source_type)}</span></div>
                  <strong>{preview.title}</strong>
                  <small>{preview.evidence_id}</small>
                  {matches.map((citation) => <button data-citation-id={citation.citation_id} data-evidence-id={citation.evidence_id} key={citation.citation_id} onClick={() => onCitation(citation)}>Inspect evidence</button>)}
                </article>
              );
            }) : <p className="research-empty">No preview was exposed for this completed tool. Counts and receipts remain available in Performance.</p>}
          </section>
        ))}
        {citations.some((citation) => !previewIds.has(citation.evidence_id)) && (
          <section className="evidence-tool report-evidence">
            <h4>Final report citations</h4>
            {citations.filter((citation) => !previewIds.has(citation.evidence_id)).map((citation) => (
              <article className="evidence-card used" data-turn-id={turn.turnId} data-evidence-id={citation.evidence_id} data-evidence-state="final-cited" key={citation.citation_id}>
                <div><span className="evidence-state">Final-cited</span><span>{title(citation.source_type)}</span></div>
                <strong>{citation.title}</strong><small>{citation.evidence_id}</small>
                <button data-citation-id={citation.citation_id} data-evidence-id={citation.evidence_id} onClick={() => onCitation(citation)}>Inspect evidence</button>
              </article>
            ))}
          </section>
        )}
      </section>
    );
  }).filter(Boolean);
  return <div className="research-scroll evidence-view">{content.length ? content : <p className="research-empty">Evidence will appear as tools complete. Final-cited items are reconciled by stable evidence ID.</p>}</div>;
}

function SpanDetail({ span }: { span: ProjectedProgressSpan }) {
  const detail = span.detail;
  return (
    <div className="span-detail" id={`detail-${span.spanId}`} data-span-detail={span.spanId} role="tooltip">
      <dl>
        <div><dt>{span.open ? "Estimated live elapsed" : "Server wall time"}</dt><dd>{formatDuration(span.wallMs)}</dd></div>
        {span.computeMs !== null && <div><dt>{detail.reused ? "Original cached receipt compute" : "Receipt compute"}</dt><dd>{formatDuration(span.computeMs)}</dd></div>}
        {span.transportMs !== null && <div><dt>Aggregate model transport</dt><dd>{formatDuration(span.transportMs)}</dd></div>}
        {detail.outcome && <div><dt>Outcome</dt><dd>{title(detail.outcome)}</dd></div>}
        {detail.engine && <div><dt>Engine</dt><dd>{detail.engine}</dd></div>}
        {detail.device && <div><dt>Device</dt><dd>{detail.device}</dd></div>}
        {detail.reused !== null && <div><dt>Execution</dt><dd>{detail.reused ? "Reused validated result" : "Executed for this turn"}</dd></div>}
        {detail.evidence_count !== null && <div><dt>Evidence / citations</dt><dd>{detail.evidence_count} / {detail.citation_count ?? 0}</dd></div>}
        {detail.route_mode && <div><dt>Route</dt><dd>{title(detail.route_mode)}</dd></div>}
        {detail.selected_tier && <div><dt>Selected tier</dt><dd className={`tier-value ${detail.selected_tier}`}>{title(detail.selected_tier)}</dd></div>}
        {detail.configured_model && <div><dt>Configured model</dt><dd className="span-model">{detail.configured_model}</dd></div>}
        {detail.model_assertion && <div><dt>Model assertion</dt><dd className="span-model">{detail.model_assertion}</dd></div>}
        {detail.identity_evidence && <div><dt>Identity evidence</dt><dd>{title(detail.identity_evidence)}</dd></div>}
        {detail.tokens && <div><dt>Tokens</dt><dd>{detail.tokens.total} total</dd></div>}
        {detail.planned_call_count !== null && <div><dt>Planned calls</dt><dd>{detail.planned_call_count}</dd></div>}
      </dl>
    </div>
  );
}

function PerformancePanel({ projection }: { projection: ProgressProjection }) {
  const [filter, setFilter] = useState<SpanFilter>("all"), [inspected, setInspected] = useState<ProjectedProgressSpan | null>(null);
  const gantt = useMemo(() => projectActiveGantt(projection), [projection]);
  const stepCount = projection.turns.reduce((total, turn) => total + turn.spans.length, 0);
  return (
    <div className="research-scroll performance-view">
      {!projection.turns.length && <p className="research-empty">Performance timing will appear when the investigation starts.</p>}
      {projection.turns.length > 0 && (
        <section
          className="performance-gantt"
          aria-labelledby="performance-gantt-heading"
          data-active-runtime-ms={gantt.activeRuntimeMs}
          onMouseLeave={() => setInspected(null)}
          onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setInspected(null); }}
        >
          <header className="gantt-heading">
            <div><span>End-to-end execution</span><h3 id="performance-gantt-heading">Execution Gantt</h3></div>
            <div><strong>{formatDuration(gantt.activeRuntimeMs)}</strong><small>active agent time</small></div>
          </header>
          <p className="envelope-note">One shared clock for the full investigation. Parallel work overlaps; pauses between user turns are excluded.</p>
          <div className="span-filters" aria-label="Performance span filters">
            {filters.map((item) => <button aria-pressed={filter === item} data-span-filter={item} key={item} onClick={() => { setFilter(item); setInspected(null); }}>{item === "all" ? "All steps" : `${title(item)}s`}</button>)}
          </div>
          <div className="gantt-legend" aria-label="Execution type legend">
            <span data-legend-kind="tool">Tool</span><span data-legend-kind="model">Model</span><span data-legend-kind="policy">Policy / plan</span><span data-legend-kind="report">Report / verify</span>
          </div>
          <div className="gantt-axis" aria-hidden="true">
            <span>Elapsed runtime</span>
            <div><i>0</i><i>{formatDuration(gantt.activeRuntimeMs / 2)}</i><i>{formatDuration(gantt.activeRuntimeMs)}</i></div>
          </div>
          <div className="gantt-plot" role="list" aria-label={`${stepCount} execution steps on one active-runtime timeline`}>
            {gantt.turns.map(({ turn, activeStartOffsetMs, spans }) => {
              const visible = spans.filter(({ span }) => filter === "all" || span.kind === filter);
              return (
                <section className="performance-turn" data-turn-id={turn.turnId} data-turn-envelope-ms={turn.envelopeMs} data-time-to-first-research-step-ms={turn.timeToFirstResearchStepMs ?? ""} data-turn-active-start-ms={activeStartOffsetMs} key={turn.turnId}>
                  <header className="performance-summary">
                    <div><span>{title(turn.turnId)}</span><strong>Total wall time {formatDuration(turn.envelopeMs)}</strong></div>
                    <small>{turn.timeToFirstResearchStepMs === null ? "Waiting for first research step" : `First research step ${formatDuration(turn.timeToFirstResearchStepMs)}`}</small>
                  </header>
                  <div className="gantt-rows">
                    {visible.length ? visible.map(({ span, activeStartOffsetMs: spanActiveStart, leftPercent, widthPercent }) => {
                      const visualWidth = Math.min(Math.max(.8, widthPercent), Math.max(.2, 100 - leftPercent));
                      const isInspected = inspected?.spanId === span.spanId;
                      return (
                        <div className="waterfall-entry gantt-entry" data-overlap-group={span.overlapGroup} key={span.spanId}>
                          <div
                            className={`waterfall-row gantt-row ${span.state} ${isInspected ? "inspected" : ""}`}
                            role="listitem"
                            tabIndex={0}
                            aria-label={`${span.displayName}, ${title(span.kind)}, ${title(span.state)}, starts at ${formatDuration(spanActiveStart)}, duration ${formatDuration(span.wallMs)}`}
                            aria-describedby={isInspected ? `detail-${span.spanId}` : undefined}
                            data-turn-id={span.turnId}
                            data-span-id={span.spanId}
                            data-span-kind={span.kind}
                            data-span-state={span.state}
                            data-span-duration-ms={span.wallMs}
                            data-span-compute-ms={span.computeMs ?? ""}
                            data-span-transport-ms={span.transportMs ?? ""}
                            data-selected-tier={span.detail.selected_tier ?? ""}
                            data-configured-model={span.detail.configured_model ?? ""}
                            data-model-assertion={span.detail.model_assertion ?? ""}
                            data-span-reused={span.detail.reused === null ? "" : String(span.detail.reused)}
                            data-parent-span-id={span.parentSpanId ?? ""}
                            data-span-started-at={span.startedAt}
                            data-span-completed-at={span.completedAt ?? ""}
                            data-span-start-offset-ms={span.startOffsetMs}
                            data-span-active-start-ms={spanActiveStart}
                            onMouseEnter={() => setInspected(span)}
                            onFocus={() => setInspected(span)}
                          >
                            <span className="gantt-label"><span className="span-state" aria-hidden="true" /><span><strong>{span.displayName}</strong><small>{title(span.state)} · {formatDuration(span.wallMs)}</small></span></span>
                            <span className="gantt-track" aria-hidden="true"><span className="gantt-bar" style={{ left: `${leftPercent}%`, width: `${visualWidth}%` }} /></span>
                          </div>
                        </div>
                      );
                    }) : <p className="research-empty gantt-empty">No {filter} spans were recorded for this turn.</p>}
                  </div>
                </section>
              );
            })}
          </div>
          <div className={`gantt-inspector ${inspected ? "active" : ""}`} data-gantt-inspector aria-live="off">
            {inspected ? <><header><span>Step detail</span><strong>{inspected.displayName}</strong></header><SpanDetail span={inspected} /></> : <p>Hover a bar or focus a row to inspect its timing, model, and receipt details.</p>}
          </div>
        </section>
      )}
    </div>
  );
}

export interface ResearchPanelProps {
  readonly record: Investigation;
  readonly events: readonly TrajectoryEvent[];
  readonly active?: boolean;
  readonly busy: boolean;
  readonly onCancel: () => void;
  readonly onRetry?: () => void;
}

export function ResearchPanel({ record, events, active = false, busy, onCancel, onRetry }: ResearchPanelProps) {
  const [tab, setTab] = useState<ResearchTab>("activity"), [drawerOpen, setDrawerOpen] = useState(false), [selected, setSelected] = useState<Citation | null>(null), [clock, setClock] = useState(Date.now()), [panelWidth, setPanelWidth] = useState(PANEL_DEFAULT_WIDTH), [resizing, setResizing] = useState(false);
  const drawerMode = useDrawerMode(), trigger = useRef<HTMLButtonElement>(null), close = useRef<HTMLButtonElement>(null), panel = useRef<HTMLElement>(null), restore = useRef(false);
  const resizeSession = useRef<{ readonly pointerId: number; readonly startX: number; readonly startWidth: number } | null>(null);
  const projectionStatus: RunStatus = active ? "running" : record.status;
  const projection = useMemo(() => projectProgressSpans(events, projectionStatus, clock), [clock, events, projectionStatus]);
  const hasOpen = projection.turns.some((turn) => turn.spans.some((span) => span.open));
  const closeEvidence = useCallback(() => setSelected(null), []);

  useEffect(() => {
    if (!hasOpen) return;
    const timer = window.setInterval(() => setClock(Date.now()), 250); return () => window.clearInterval(timer);
  }, [hasOpen]);
  useEffect(() => {
    if (!drawerMode && drawerOpen) setDrawerOpen(false);
  }, [drawerMode, drawerOpen]);
  useEffect(() => {
    if (drawerMode && drawerOpen) { restore.current = true; close.current?.focus(); }
    else if (restore.current) { restore.current = false; if (drawerMode) trigger.current?.focus(); }
  }, [drawerMode, drawerOpen]);
  useEffect(() => {
    if (!drawerOpen) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setDrawerOpen(false); };
    window.addEventListener("keydown", escape); return () => window.removeEventListener("keydown", escape);
  }, [drawerOpen]);
  useEffect(() => {
    const clampToViewport = () => setPanelWidth((current) => Math.min(current, availablePanelWidth()));
    window.addEventListener("resize", clampToViewport); return () => window.removeEventListener("resize", clampToViewport);
  }, []);

  const setClampedPanelWidth = (width: number) => setPanelWidth(Math.min(availablePanelWidth(), Math.max(PANEL_MIN_WIDTH, Math.round(width))));
  const resizeKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    let next: number | null = null;
    if (event.key === "ArrowLeft") next = panelWidth + PANEL_WIDTH_STEP;
    if (event.key === "ArrowRight") next = panelWidth - PANEL_WIDTH_STEP;
    if (event.key === "Home") next = PANEL_MIN_WIDTH;
    if (event.key === "End") next = availablePanelWidth();
    if (next !== null) { event.preventDefault(); setClampedPanelWidth(next); }
  };
  const beginResize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (drawerMode || event.button !== 0) return;
    event.preventDefault();
    resizeSession.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: panelWidth };
    event.currentTarget.setPointerCapture(event.pointerId);
    setResizing(true);
  };
  const continueResize = (event: React.PointerEvent<HTMLDivElement>) => {
    const session = resizeSession.current;
    if (!session || session.pointerId !== event.pointerId) return;
    setClampedPanelWidth(session.startWidth + session.startX - event.clientX);
  };
  const endResize = (event: React.PointerEvent<HTMLDivElement>) => {
    const session = resizeSession.current;
    if (!session || session.pointerId !== event.pointerId) return;
    resizeSession.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setResizing(false);
  };

  const chooseTab = (next: ResearchTab) => {
    setTab(next);
    requestAnimationFrame(() => panel.current?.querySelector<HTMLButtonElement>(`[data-research-tab='${next}']`)?.focus());
  };
  const tabKey = (event: React.KeyboardEvent, current: ResearchTab) => {
    const index = tabs.indexOf(current); let next: ResearchTab | null = null;
    if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
    if (event.key === "ArrowLeft") next = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === "Home") next = tabs[0]; if (event.key === "End") next = tabs.at(-1)!;
    if (next) { event.preventDefault(); chooseTab(next); }
  };
  const trapFocus = (event: React.KeyboardEvent) => {
    if (!drawerMode || !drawerOpen || event.key !== "Tab") return;
    const focusable = [...(panel.current?.querySelectorAll<HTMLElement>("button:not(:disabled), a[href], input:not(:disabled), [tabindex='0']") ?? [])].filter((item) => item.offsetParent !== null || item === document.activeElement);
    if (!focusable.length) return; const first = focusable[0], last = focusable.at(-1)!;
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  const progressCount = projection.turns.reduce((sum, turn) => sum + turn.spans.length, 0), runningCount = projection.turns.reduce((sum, turn) => sum + turn.spans.filter((span) => span.open).length, 0);
  return (
    <>
      <button ref={trigger} className="show-analysis" data-action="show-analysis" aria-controls="research-workspace" aria-expanded={drawerOpen} onClick={() => setDrawerOpen(true)}>Show analysis <span aria-hidden="true">↗</span></button>
      {drawerMode && drawerOpen && <div className="research-scrim" aria-hidden="true" onMouseDown={(event) => { if (event.target === event.currentTarget) setDrawerOpen(false); }} />}
      <aside
        ref={panel}
        id="research-workspace"
        className={`research-panel research-workspace ${drawerOpen ? "open" : ""} ${resizing ? "resizing" : ""}`}
        style={drawerMode ? undefined : { width: `${panelWidth}px` }}
        role={drawerMode ? "dialog" : "region"}
        aria-modal={drawerMode && drawerOpen ? "true" : undefined}
        aria-hidden={drawerMode && !drawerOpen ? "true" : undefined}
        aria-labelledby="research-heading"
        onKeyDown={trapFocus}
      >
        {!drawerMode && (
          <div
            className="research-resize-handle"
            role="separator"
            aria-label="Resize Research details panel"
            aria-controls="research-workspace"
            aria-orientation="vertical"
            aria-valuemin={PANEL_MIN_WIDTH}
            aria-valuemax={availablePanelWidth()}
            aria-valuenow={panelWidth}
            aria-valuetext={`${panelWidth} pixels wide`}
            tabIndex={0}
            title="Drag to resize. Use Left and Right arrow keys for keyboard resizing."
            onKeyDown={resizeKey}
            onPointerDown={beginResize}
            onPointerMove={continueResize}
            onPointerUp={endResize}
            onPointerCancel={endResize}
          />
        )}
        <div className="panel-heading research-heading">
          <div><h2 id="research-heading">Research details</h2><span role="status" aria-live="polite">{runningCount ? `${runningCount} running` : `${progressCount} recorded steps`}</span></div>
          <button ref={close} className="close-analysis" data-action="close-analysis" aria-label="Close analysis" onClick={() => setDrawerOpen(false)}>×</button>
        </div>
        <div className="research-tabs" role="tablist" aria-label="Research detail view">
          {tabs.map((item) => <button id={`research-tab-${item}`} role="tab" aria-selected={tab === item} aria-controls={`research-panel-${item}`} tabIndex={tab === item ? 0 : -1} data-research-tab={item} key={item} onClick={() => setTab(item)} onKeyDown={(event) => tabKey(event, item)}>{title(item)}</button>)}
        </div>
        <div className="research-content">
          <div className="research-tab-panel" id="research-panel-activity" role="tabpanel" aria-labelledby="research-tab-activity" hidden={tab !== "activity"}>
            {tab === "activity" && <Trajectory events={[...events]} />}
          </div>
          <div className="research-tab-panel" id="research-panel-evidence" role="tabpanel" aria-labelledby="research-tab-evidence" hidden={tab !== "evidence"}>
            {tab === "evidence" && <EvidencePanel record={record} events={events} projection={projection} onCitation={setSelected} />}
          </div>
          <div className="research-tab-panel" id="research-panel-performance" role="tabpanel" aria-labelledby="research-tab-performance" hidden={tab !== "performance"}>
            {tab === "performance" && <PerformancePanel projection={projection} />}
          </div>
        </div>
        <div className="research-actions">
          {(["created", "running"].includes(record.status) || active) && <button className="secondary-button danger" disabled={busy} onClick={onCancel}>Cancel investigation</button>}
          {["failed", "cancelled"].includes(record.status) && onRetry && <button className="secondary-button" disabled={busy} onClick={onRetry}>Retry investigation</button>}
        </div>
      </aside>
      <EvidenceDrawer citation={selected} onClose={closeEvidence} />
    </>
  );
}
