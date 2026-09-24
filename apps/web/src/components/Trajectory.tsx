import type { ProgressEvent, Span } from "../api/types";
import { formatDuration, words } from "../format";

export interface SpanRow {
  readonly turn: number;
  readonly span: Span;
}

/** Latest state of every span, in the order spans first appeared. */
export function latestSpans(events: readonly ProgressEvent[]): SpanRow[] {
  const byId = new Map<string, SpanRow>();
  for (const event of events) byId.set(event.span.span_id, { turn: event.turn, span: event.span });
  return [...byId.values()];
}

/** A short, human line for the span's detail payload (tool receipts, model tier, skill path). */
export function spanSummary(span: Span): string {
  const detail = span.detail;
  const parts: string[] = [];
  const add = (value: unknown, format: (item: never) => string = String) => {
    if (value !== undefined && value !== null && value !== "") parts.push(format(value as never));
  };
  add(detail.ticker);
  add(detail.outcome, words);
  add(detail.path);
  add(detail.model);
  add(detail.tier, (tier: string) => `${tier} tier`);
  add(detail.engine);
  add(detail.device);
  add(detail.duration_ms, (ms: number) => `GPU ${formatDuration(ms)}`);
  add(detail.latency_ms, (ms: number) => formatDuration(ms));
  add(detail.error ?? detail.failure);
  return parts.join(" · ");
}

export function Trajectory({ events }: { events: readonly ProgressEvent[] }) {
  const rows = latestSpans(events);
  return (
    <section className="trajectory" aria-label="Investigation trail">
      <div className="trajectory-heading">
        <h2>Investigation trail</h2>
        <span>{rows.length} {rows.length === 1 ? "step" : "steps"}</span>
      </div>
      {rows.length === 0 ? <p className="muted">Activity will appear here.</p> : (
        <ol>
          {rows.map(({ turn, span }) => (
            <li key={span.span_id} className={span.state}>
              <span className="event-marker" aria-hidden="true" />
              <div>
                <small>Turn {turn} · {span.kind} · {span.state}</small>
                <strong>{words(span.name)}</strong>
                {spanSummary(span) && <p>{spanSummary(span)}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
