import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { CreateInvestigation, ShockEvent, SystemStatus } from "../api/types";
import { words } from "../format";
import "./event-explorer.css";

export interface InvestigationSeed { readonly ticker: string; readonly asOf: string }
type CapabilityTab = "coverage" | "models";

export interface InvestigationFormProps {
  readonly status: SystemStatus | null;
  readonly statusError: string | null;
  readonly busy: boolean;
  readonly seed: InvestigationSeed | null;
  readonly event: ShockEvent | null;
  readonly onClearEvent: () => void;
  readonly onSubmit: (body: CreateInvestigation) => void;
  readonly scenarioPicker?: ReactNode;
}

export function RuntimeState({ status, error }: { status: SystemStatus | null; error: string | null }) {
  if (!status && error) return <div className="runtime-state" role="alert"><strong>Status unavailable</strong><p>{error}</p></div>;
  if (!status) return <div className="runtime-state"><p role="status"><span className="status-dot loading" />Checking the research runtime…</p></div>;
  if (!status.ready) return <div className="runtime-state" role="alert"><strong>Research is not available yet</strong><p>{status.reason ?? "The agent is not ready."}</p></div>;
  return <div className="runtime-state"><p role="status"><span className="status-dot" />Research runtime ready</p></div>;
}

export function InvestigationForm({ status, statusError, busy, seed, event, onClearEvent, onSubmit, scenarioPicker }: InvestigationFormProps) {
  const [question, setQuestion] = useState("");
  const [ticker, setTicker] = useState(seed?.ticker ?? "");
  const [date, setDate] = useState(seed?.asOf ?? "");
  const [tab, setTab] = useState<CapabilityTab>("coverage");
  const tabRefs = useRef<Record<CapabilityTab, HTMLButtonElement | null>>({ coverage: null, models: null });
  const questionRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!seed) return;
    setTicker(seed.ticker);
    setDate(seed.asOf);
    questionRef.current?.focus();
  }, [seed]);

  useEffect(() => {
    setQuestion(event?.questions[0]?.text ?? "");
  }, [event]);

  const canSubmit = Boolean(status?.ready && !busy && question.trim().length >= 2);
  const tabKeyDown = (keyEvent: KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(keyEvent.key)) return;
    keyEvent.preventDefault();
    const next = keyEvent.key === "ArrowLeft" || keyEvent.key === "Home" ? "coverage" : "models";
    setTab(next);
    tabRefs.current[next]?.focus();
  };

  return (
    <form
      className="start-layout"
      onSubmit={(submitEvent) => {
        submitEvent.preventDefault();
        if (!canSubmit) return;
        const text = question.trim();
        onSubmit(event ? { question: text, event_id: event.event_id } : { question: text, ...(ticker ? { ticker } : {}), ...(date ? { as_of: date } : {}) });
      }}
    >
      <aside className="scope-panel">
        <div className="panel-heading"><h2>Investigation</h2></div>
        <RuntimeState status={status} error={statusError} />
        {event ? (
          <section className="event-pinned-scope" aria-labelledby="pinned-event-title">
            <span>Pinned event · {event.status === "ready" ? "research ready" : "partial evidence"}</span>
            <h3 id="pinned-event-title">{event.title}</h3>
            <p>{event.summary}</p>
            <dl className="event-pinned-facts">
              <div><dt>Companies</dt><dd>{event.analysis_tickers.join(", ")}</dd></div>
              <div><dt>Market context</dt><dd>{event.context_instruments.join(", ")}</dd></div>
              <div><dt>Analysis window</dt><dd>{event.start_session}–{event.end_session}</dd></div>
              <div><dt>Evidence cutoff</dt><dd>{event.default_cutoff}</dd></div>
            </dl>
            {event.limitations.length > 0 && (
              <details className="event-pinned-limits">
                <summary>Limitations ({event.limitations.length})</summary>
                <ul>{event.limitations.map((item) => <li key={item.limitation_id}>{item.detail}</li>)}</ul>
              </details>
            )}
            <button type="button" onClick={() => { setTicker(event.primary_ticker); setDate(event.default_cutoff.slice(0, 10)); onClearEvent(); }}>Use custom scope</button>
          </section>
        ) : (
          <div className="scope-fields">
            <label>
              <span>Company ticker</span>
              <select value={ticker} disabled={!status} onChange={(change) => setTicker(change.target.value)}>
                <option value="">Infer from question</option>
                {status?.companies.map((company) => <option value={company.symbol} key={company.symbol}>{company.symbol} · {company.name}</option>)}
              </select>
            </label>
            <label>
              <span>Evidence cutoff</span>
              <input type="date" value={date} min={status?.coverage.first_session} max={status?.coverage.last_session} disabled={!status} onChange={(change) => setDate(change.target.value)} />
              {status && <small>{status.coverage.first_session} through {status.coverage.last_session}. Non-trading days resolve to the prior completed session.</small>}
            </label>
          </div>
        )}
      </aside>

      <section className={`prompt-stage${scenarioPicker ? " with-scenarios" : ""}`}>
        <div className="welcome">
          <div className="welcome-mark" aria-hidden="true" />
          <h1>{event ? event.title : "What moved the market?"}</h1>
          <p>{event ? "Pick one of the curated questions or write your own; the event's companies and cutoff are pinned." : "Choose a known shock below, or pick a company and cutoff and ask your own market research question."}</p>
        </div>
        {scenarioPicker}
        <div className="question-composer">
          <div className="composer-heading">
            <label htmlFor="question">{event ? "Ask about this event" : "Ask a market research question"}</label>
          </div>
          {event && (
            <div className="event-questions" role="group" aria-label="Curated event questions">
              {event.questions.map((item) => (
                <button type="button" key={item.question_id} aria-pressed={question === item.text} title={words(item.capability)} onClick={() => { setQuestion(item.text); questionRef.current?.focus(); }}>{item.label}</button>
              ))}
            </div>
          )}
          <textarea
            id="question"
            ref={questionRef}
            rows={3}
            value={question}
            placeholder={event ? "Ask about the measured move, evidence, peers, analogues, or what remains uncertain…" : `For example: What happened to ${ticker || "a company"}${date ? ` on ${date}` : " on a specific date"}?`}
            onChange={(change) => setQuestion(change.target.value)}
            minLength={2}
            maxLength={2000}
            required
          />
          <div className="composer-footer">
            <span>{event ? `${event.primary_ticker} · ${event.event_session} · event pinned` : `${ticker || "Infer company"} · ${date || "Infer date"}`}</span>
            <button className="send-button" disabled={!canSubmit}>
              {busy ? <><span className="button-spinner" aria-hidden="true" /><span>Starting…</span></> : <><span>Investigate</span><span aria-hidden="true">→</span></>}
            </button>
          </div>
        </div>
        <p className="disclaimer">Research assistance from a bounded historical snapshot—not investment advice or live market data.</p>
      </section>

      <aside className="capabilities-panel">
        <div className="panel-heading"><h2>Research details</h2></div>
        <div className="panel-tabs" role="tablist" aria-label="Research overview">
          {(["coverage", "models"] as const).map((name) => (
            <button type="button" role="tab" id={`${name}-tab`} aria-controls={`${name}-panel`} aria-selected={tab === name} tabIndex={tab === name ? 0 : -1} disabled={!status} ref={(node) => { tabRefs.current[name] = node; }} onClick={() => setTab(name)} onKeyDown={tabKeyDown} key={name}>
              {name === "coverage" ? "Coverage" : "Models"}
            </button>
          ))}
        </div>
        {!status && <div className="capabilities-content capability-empty"><p>Research details appear once the runtime status loads.</p></div>}
        {status && (
          <div className="capabilities-content" role="tabpanel" id="coverage-panel" aria-labelledby="coverage-tab" hidden={tab !== "coverage"}>
            <dl className="coverage-facts">
              <div><dt>Companies</dt><dd>{status.companies.map((company) => company.symbol).join(", ")}</dd></div>
              <div><dt>Sessions</dt><dd>{status.coverage.first_session}–{status.coverage.last_session}</dd></div>
              <div><dt>Scenario</dt><dd className="mono">{status.coverage.scenario_id}</dd></div>
              <div><dt>Questions per investigation</dt><dd>{status.max_turns}</dd></div>
            </dl>
          </div>
        )}
        {status && (
          <div className="capabilities-content" role="tabpanel" id="models-panel" aria-labelledby="models-tab" hidden={tab !== "models"}>
            <ul className="model-list">
              {status.models.map((model) => (
                <li key={model.id}><span>{model.where === "local" ? "Local · DGX Spark" : "Remote endpoint"}</span><strong>{model.id}</strong><small>{model.role}</small></li>
              ))}
            </ul>
          </div>
        )}
        <div className="capabilities-footer">Evidence is restricted to the selected point in time.</div>
      </aside>
    </form>
  );
}
