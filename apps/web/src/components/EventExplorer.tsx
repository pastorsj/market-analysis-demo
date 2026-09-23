import { useEffect, useMemo, useState } from "react";
import { getShockEvents, type ShockEvent, type ShockEventCatalog } from "../api/events";
import "./event-explorer.css";

export interface EventExplorerProps {
  readonly selectedEvent: ShockEvent | null;
  readonly availableTickers: readonly string[];
  readonly coverage: { readonly first_session: string; readonly last_session: string } | null;
  readonly onSelectEvent: (event: ShockEvent | null) => void;
  readonly compact?: boolean;
}

function readableDate(value: string): string {
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
}

export function EventExplorer({ selectedEvent, availableTickers, coverage, onSelectEvent, compact = false }: EventExplorerProps) {
  const [catalog, setCatalog] = useState<ShockEventCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [category, setCategory] = useState("all");

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setError(null);
    void getShockEvents(controller.signal).then((value) => {
      if (!active) return;
      setCatalog(value);
    }).catch((reason: unknown) => {
      if (!active || reason instanceof DOMException && reason.name === "AbortError") return;
      setCatalog(null);
      setError(reason instanceof Error ? reason.message : "Curated market shocks are unavailable.");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; controller.abort(); };
  }, [attempt]);

  useEffect(() => {
    if (!selectedEvent || loading) return;
    if (!catalog || !catalog.events.some((event) => event.event_id === selectedEvent.event_id)) onSelectEvent(null);
  }, [catalog, loading, onSelectEvent, selectedEvent]);

  const visibleEvents = useMemo(
    () => catalog?.events.filter((event) => category === "all" || event.category_id === category) ?? [],
    [catalog, category],
  );
  const eventAvailable = (event: ShockEvent) => availableTickers.includes(event.primary_ticker)
    && Boolean(coverage && event.default_cutoff.slice(0, 10) >= coverage.first_session && event.default_cutoff.slice(0, 10) <= coverage.last_session);

  return (
    <section className="event-explorer research-event-picker" aria-labelledby="event-explorer-title">
      {compact ? (
        <div className="event-picker-compact-heading">
          <div><span className="dashboard-kicker">Curated research contexts</span><h2 id="event-explorer-title">Known shock scenarios</h2></div>
          {catalog && <span>{catalog.summary.published_events} events</span>}
        </div>
      ) : <div className="event-explorer-heading">
        <div>
          <span className="dashboard-kicker">Curated research contexts</span>
          <h2 id="event-explorer-title">Choose a known market shock</h2>
          <p>Selecting a scenario populates its companies, analysis window, evidence cutoff, and suggested questions below. You can always write your own question.</p>
        </div>
        {catalog && <div className="event-count"><strong>{catalog.summary.published_events}</strong><span>Published events</span></div>}
      </div>}

      {loading && <div className="event-catalog-state" role="status"><span className="dashboard-spinner" /><div><strong>Loading qualified events</strong><p>Reading the scenario-bound local catalog.</p></div></div>}
      {error && !loading && (
        <div className="event-catalog-error" role="alert">
          <div><strong>Known events are temporarily unavailable.</strong><p>{error} Free-text research remains available below.</p></div>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry events</button>
        </div>
      )}

      {catalog && !loading && (
        <>
          <div className="event-category-filters" role="group" aria-label="Filter known market shocks">
            <button type="button" aria-pressed={category === "all"} onClick={() => setCategory("all")}>All events <span>{catalog.events.length}</span></button>
            {catalog.categories.map((item) => (
              <button type="button" key={item.category_id} title={item.description} aria-pressed={category === item.category_id} onClick={() => setCategory(item.category_id)}>
                {item.label} <span>{catalog.events.filter((event) => event.category_id === item.category_id).length}</span>
              </button>
            ))}
          </div>
          <div className="event-card-grid" aria-live="polite">
            {visibleEvents.map((event) => {
              const available = eventAvailable(event);
              return (
                <button
                  type="button"
                  data-event-id={event.event_id}
                  className={selectedEvent?.event_id === event.event_id ? "selected" : undefined}
                  aria-pressed={selectedEvent?.event_id === event.event_id}
                  disabled={!available}
                  title={available ? undefined : "This event is outside the current research runtime coverage."}
                  onClick={() => onSelectEvent(event)}
                  key={event.event_id}
                >
                  <span className="event-card-top"><small>{readableDate(event.event_session)}</small><i className={event.qualification.status}>{event.qualification.status}</i></span>
                  <strong>{event.title}</strong>
                  <span className="event-card-tickers">{event.analysis_tickers.join(" · ")}</span>
                  <p>{event.summary}</p>
                  <span className="event-card-action">{available ? selectedEvent?.event_id === event.event_id ? "Ready to investigate ✓" : "Set up scenario →" : "Unavailable in this runtime"}</span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
