import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { getSystemStatus } from "../api/client";
import type { ShockEvent, ShockEventQuestion } from "../api/events";
import type { InvestigationRequest, PrimaryTicker, StatusModelRole, SystemStatus } from "../api/types";
import {
  QUESTION_PRESETS,
  availableQuestionPresets,
  findQuestionPreset,
  type GuidedQuestionPreset,
} from "../questionPresets";
import "./event-explorer.css";

type CapabilityTab = "coverage" | "models";
const PREFERRED_ROUTE: InvestigationRequest["route_mode"] = "switchyard_escalation";

export interface InvestigationSeed {
  readonly ticker: PrimaryTicker;
  readonly as_of: string;
  readonly question: string;
  readonly event?: ShockEvent | null;
  readonly eventQuestion?: ShockEventQuestion | null;
}

const roleLabel: Record<StatusModelRole, string> = {
  local_generation: "Local answer synthesis",
  switchyard_efficient_target: "Switchyard efficient target",
  speculative_assistant: "Speculative decoding assistant",
  retrieval_embedding: "Retrieval embeddings",
  switchyard_classifier: "Switchyard classifier",
  switchyard_capable_target: "Switchyard capable target",
  report_formatter: "Report formatter",
};

const limitationLabel = {
  reconstructed_later_market_data: "Historical market data was reconstructed later; it is not an archived-at-cutoff feed.",
  licensed_news_unavailable: "Cutoff-qualified licensed news is not present in this snapshot.",
  source_coverage_varies_by_ticker_and_cutoff: "Company-source coverage varies by ticker and evidence cutoff.",
} as const;

const sourceLabel = (source: SystemStatus["coverage"]["document_source_kinds"][number]) => ({
  company_release: "Company releases",
  filing: "Filings",
  primary_source: "Primary-source documents",
  licensed_news_metadata: "Historical news",
})[source];

const locationLabel = (location: SystemStatus["models"][number]["location"]) =>
  location === "internal_inference_server" ? "Internal inference server" : location === "local_tools_service" ? "Local DGX Spark · tools" : "Local DGX Spark · model";

export function InvestigationForm({ busy, onSubmit, presets = QUESTION_PRESETS, initialSeed, onUseCustomScope, scenarioPicker }: {
  busy: boolean;
  onSubmit: (value: InvestigationRequest) => void;
  presets?: readonly GuidedQuestionPreset[];
  initialSeed?: InvestigationSeed | null;
  onUseCustomScope?: (seed: InvestigationSeed) => void;
  scenarioPicker?: ReactNode;
}) {
  const [question, setQuestion] = useState(initialSeed?.question ?? "");
  const [ticker, setTicker] = useState<PrimaryTicker | "">(initialSeed?.ticker ?? "NVDA");
  const [date, setDate] = useState(initialSeed?.event?.default_cutoff.slice(0, 10) ?? initialSeed?.as_of.slice(0, 10) ?? "");
  const [eventContext, setEventContext] = useState<ShockEvent | null>(initialSeed?.event ?? null);
  const [eventQuestion, setEventQuestion] = useState<ShockEventQuestion | null>(initialSeed?.eventQuestion ?? null);
  const [selectedPreset, setSelectedPreset] = useState("");
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusAttempt, setStatusAttempt] = useState(0);
  const [tab, setTab] = useState<CapabilityTab>("coverage");
  const tabRefs = useRef<Record<CapabilityTab, HTMLButtonElement | null>>({ coverage: null, models: null });
  const questionRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!initialSeed) return;
    setQuestion(initialSeed.question);
    setTicker(initialSeed.ticker);
    setDate(initialSeed.event?.default_cutoff.slice(0, 10) ?? initialSeed.as_of.slice(0, 10));
    setEventContext(initialSeed.event ?? null);
    setEventQuestion(initialSeed.eventQuestion ?? null);
    setSelectedPreset("");
  }, [initialSeed]);

  useEffect(() => {
    let active = true;
    setStatusLoading(true);
    setStatusError(null);
    void getSystemStatus().then((value) => {
      if (!active) return;
      setStatus(value);
      setTicker((current) => current && value.supported_tickers.includes(current) ? current : value.supported_tickers[0]);
      setDate((current) => current >= value.coverage.first_session && current <= value.coverage.last_session ? current : value.coverage.last_session);
    }).catch((error: unknown) => {
      if (!active) return;
      setStatus(null);
      setStatusError(error instanceof Error ? error.message : "Runtime status is unavailable.");
    }).finally(() => {
      if (active) setStatusLoading(false);
    });
    return () => { active = false; };
  }, [statusAttempt]);

  useEffect(() => {
    if (status && initialSeed) queueMicrotask(() => questionRef.current?.focus());
  }, [initialSeed, status]);

  const visiblePresets = status ? availableQuestionPresets(status, presets) : [];
  const launchRoute = status?.routes.find((route) => route.mode === PREFERRED_ROUTE && route.enabled);
  const inCoverage = Boolean(status && date >= status.coverage.first_session && date <= status.coverage.last_session);
  const eventPrimary = eventContext && status?.supported_tickers.find((candidate) => candidate === eventContext.primary_ticker);
  const eventCutoff = eventContext?.default_cutoff.slice(0, 10) ?? "";
  const eventScopeValid = Boolean(eventContext && eventPrimary && status && eventCutoff >= status.coverage.first_session && eventCutoff <= status.coverage.last_session);
  const submitLabel = eventContext ? "Investigate scenario" : "Investigate";
  const canSubmit = Boolean(
    !busy && status && launchRoute?.enabled
    && (eventContext ? eventScopeValid : (!ticker || status.supported_tickers.includes(ticker)) && (!date || inCoverage))
    && question.trim().length >= 2,
  );

  const leavePreset = (clear: boolean) => {
    if (!selectedPreset) return;
    setSelectedPreset("");
    if (clear) setQuestion("");
  };

  const chooseTab = (next: CapabilityTab, focus = false) => {
    setTab(next);
    if (focus) queueMicrotask(() => tabRefs.current[next]?.focus());
  };

  const tabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    chooseTab(event.key === "ArrowLeft" || event.key === "Home" ? "coverage" : "models", true);
  };

  return (
    <form
      className="start-layout"
      onSubmit={(event) => {
        event.preventDefault();
        if (!canSubmit) return;
        if (eventContext) {
          onSubmit({ question: question.trim(), ticker: eventPrimary!, as_of: eventContext.default_cutoff, route_mode: launchRoute!.mode, event_id: eventContext.event_id });
          return;
        }
        onSubmit({ question: question.trim(), ticker: ticker || null, as_of: date || null, route_mode: launchRoute!.mode });
      }}
    >
      <aside className="scope-panel">
        <div className="panel-heading"><h2>Investigation</h2></div>
        <div className="runtime-state" aria-live="polite">
          {statusLoading && <p role="status"><span className="status-dot loading" />Loading verified runtime coverage…</p>}
          {statusError && (
            <div role="alert">
              <strong>Runtime status unavailable</strong>
              <p>{statusError}</p>
              <button type="button" data-action="retry-status" onClick={() => setStatusAttempt((value) => value + 1)}>Retry status</button>
            </div>
          )}
          {status && !status.ready && (
            <p className="degraded">
              <span className="status-dot warning" />
              {status.remote_routing_enabled
                ? "Runtime is degraded. New investigations are unavailable until the required service recovers."
                : "Runtime is prepared locally, but research requires the approved internal inference route to be enabled."}
            </p>
          )}
          {status?.ready && <p><span className="status-dot" />Verified runtime ready</p>}
        </div>
        {eventContext ? (
          <section className="event-pinned-scope" aria-labelledby="pinned-event-title">
            <span>Pinned event context · {eventContext.qualification.status === "ready" ? "research ready" : "partial evidence"}</span>
            <h3 id="pinned-event-title">{eventContext.title}</h3>
            <p>{eventContext.summary}</p>
            <dl className="event-pinned-facts">
              <div><dt>Companies</dt><dd>{eventContext.analysis_tickers.join(", ")} · {eventContext.primary_ticker} primary</dd></div>
              <div><dt>Market context</dt><dd>{eventContext.context_instruments.join(", ")}</dd></div>
              <div><dt>Market session</dt><dd>{eventContext.event_session}</dd></div>
              <div><dt>Analysis window</dt><dd>{eventContext.start_session}–{eventContext.end_session}</dd></div>
              <div><dt>Evidence cutoff</dt><dd>{eventContext.default_cutoff}</dd></div>
            </dl>
            {!eventScopeValid && status && <p className="event-pinned-error" role="alert">This event is outside the current runtime coverage and cannot be started.</p>}
            <details className="event-pinned-limits">
              <summary>Coverage and limitations ({eventContext.limitations.length + eventContext.qualification.gaps.length})</summary>
              <ul>
                {eventContext.limitations.map((item) => <li key={item.limitation_id}>{item.detail}</li>)}
                {eventContext.qualification.gaps.map((item) => <li key={`${item.layer}:${item.code}`}>{item.detail}</li>)}
              </ul>
            </details>
            <button
              type="button"
              onClick={() => {
                const primary = status?.supported_tickers.find((candidate) => candidate === eventContext.primary_ticker);
                setEventContext(null);
                setEventQuestion(null);
                setSelectedPreset("");
                if (primary) setTicker(primary);
                if (status && eventCutoff >= status.coverage.first_session && eventCutoff <= status.coverage.last_session) setDate(eventCutoff);
                if (primary) onUseCustomScope?.({ ticker: primary, as_of: eventCutoff, question, event: null, eventQuestion: null });
              }}
            >Use custom scope</button>
          </section>
        ) : <div className="scope-fields">
          <label>
            <span>Company ticker</span>
            <select
              aria-label="Ticker"
              value={ticker}
              disabled={!status}
              onChange={(event) => {
                leavePreset(true);
                setTicker(event.target.value as PrimaryTicker | "");
              }}
            >
              {!status && <option value="NVDA">Runtime status required</option>}
              {status && <option value="">Infer from question</option>}
              {status?.companies.map((company) => <option value={company.symbol} key={company.symbol}>{company.symbol} · {company.display_name}</option>)}
            </select>
          </label>
          <label>
            <span>Evidence cutoff</span>
            <input
              aria-label="Evidence cutoff"
              type="date"
              value={date}
              min={status?.coverage.first_session}
              max={status?.coverage.last_session}
              disabled={!status}
              onChange={(event) => {
                leavePreset(true);
                setDate(event.target.value);
              }}
            />
            {status && <small>{status.coverage.first_session} through {status.coverage.last_session}. Non-trading days resolve to the prior completed session.</small>}
          </label>
        </div>}
      </aside>

      <section className={`prompt-stage${scenarioPicker ? " with-scenarios" : ""}`}>
        <div className="welcome">
          <div className="welcome-mark" aria-hidden="true" />
          <h1>{eventContext ? eventContext.title : "What moved the market?"}</h1>
          <p>{eventContext ? "Review and edit your question with the event's companies, window, cutoff, coverage, and limitations pinned." : scenarioPicker ? "Choose a known shock below, or use the manual scope and ask your own market research question." : "Choose a supported company and historical cutoff, then ask about price, peers, primary evidence, or what remains uncertain."}</p>
        </div>
        {scenarioPicker}
        <div className="question-composer">
          <div className="composer-heading">
            <label htmlFor="question">{eventContext ? "Ask anything about this event" : "Ask a market research question"}</label>
            {eventContext ? <select
              className="composer-preset"
              aria-label="Event question"
              value={eventQuestion?.question_id ?? ""}
              disabled={!status}
              onChange={(event) => {
                const suggestion = eventContext.questions.find((item) => item.question_id === event.target.value);
                if (!suggestion) return;
                setEventQuestion(suggestion);
                setQuestion(suggestion.text);
                queueMicrotask(() => questionRef.current?.focus());
              }}
            >
              <option value="" disabled hidden>Choose a question</option>
              {eventContext.questions.map((suggestion) => (
                <option value={suggestion.question_id} key={suggestion.question_id}>
                  {suggestion.label} · {suggestion.capability.replaceAll("-", " ")}
                </option>
              ))}
            </select> : <select
              className="composer-preset"
              aria-label="Guided question"
              value={selectedPreset}
              disabled={!status}
              onChange={(event) => {
                const value = event.target.value;
                if (!status) return;
                const preset = findQuestionPreset(value, status, presets);
                if (!preset) return;
                setSelectedPreset(preset.presetId);
                setTicker(preset.ticker);
                setDate(preset.date);
                setQuestion(preset.question);
              }}
            >
              <option value="" disabled hidden>Choose an example</option>
              {visiblePresets.map((preset) => (
                <option value={preset.presetId} key={preset.presetId}>
                  {preset.ticker} · {preset.date} · {preset.label} · {preset.capability}
                </option>
              ))}
            </select>}
          </div>
          {status && !eventContext && visiblePresets.length === 0 && (
            <p className="preset-note" aria-live="polite">No guided questions are available for this runtime snapshot. Custom questions remain available.</p>
          )}
          <textarea
            id="question"
            ref={questionRef}
            aria-label="Investigation question"
            rows={3}
            value={question}
            placeholder={status ? eventContext ? "Ask about the measured move, evidence, peers, analogues, or remaining uncertainty…" : `For example: What happened to ${ticker || "NVDA"}${date ? ` on ${date}` : " on a specific date"}?` : "Verified runtime status is required before starting."}
            onChange={(event) => {
              setQuestion(event.target.value);
              if (selectedPreset) setSelectedPreset("");
              if (eventQuestion) setEventQuestion(null);
            }}
            minLength={2}
            maxLength={4000}
            disabled={!status}
            required
          />
          <div className="composer-footer">
            <span>{status ? eventContext ? `${eventContext.primary_ticker} · ${eventContext.event_session} · event pinned` : `${ticker || "Infer company"} · ${date || "Infer date"}` : "Waiting for runtime status"}</span>
            <button className="send-button" disabled={!canSubmit} aria-label={busy ? "Starting investigation" : submitLabel}>
              {busy ? <><span className="button-spinner" /><span>Starting…</span></> : <><span>{submitLabel}</span><span aria-hidden="true">→</span></>}
            </button>
          </div>
        </div>
        <p className="disclaimer">Research assistance from a bounded historical snapshot—not investment advice or live market data.</p>
      </section>

      <aside className="capabilities-panel">
        <div className="panel-heading"><h2>Research details</h2></div>
        <div className="panel-tabs" role="tablist" aria-label="Research overview">
          {(["coverage", "models"] as const).map((name) => (
            <button
              type="button"
              role="tab"
              data-tab={name}
              id={`${name}-tab`}
              aria-controls={`${name}-panel`}
              aria-selected={tab === name}
              tabIndex={tab === name ? 0 : -1}
              disabled={!status}
              ref={(node) => { tabRefs.current[name] = node; }}
              onClick={() => chooseTab(name)}
              onKeyDown={tabKeyDown}
              key={name}
            >
              {name === "coverage" ? "Coverage" : "Models"}
            </button>
          ))}
        </div>
        {!status && (
          <div className="capabilities-content capability-empty">
            <p>{statusLoading ? "Loading verified research details…" : "Research details are unavailable until status can be verified."}</p>
          </div>
        )}
        {status && (
          <div className="capabilities-content" role="tabpanel" id="coverage-panel" aria-labelledby="coverage-tab" hidden={tab !== "coverage"}>
            <p className="section-label">Verified snapshot</p>
            <dl className="coverage-facts">
              <div><dt>Primary companies</dt><dd>{status.supported_tickers.join(", ")}</dd></div>
              <div><dt>Sessions</dt><dd>{status.coverage.first_session}–{status.coverage.last_session}<small>{status.coverage.session_count.toLocaleString()} validated XNYS sessions</small></dd></div>
              <div><dt>Data vintage</dt><dd>{status.coverage.vintage_status === "reconstructed_later" ? "Historical reconstruction" : "Archived at cutoff"}</dd></div>
              <div><dt>Document classes</dt><dd>{status.coverage.document_source_kinds.map(sourceLabel).join(", ")}</dd></div>
            </dl>
            <p className="section-label limitations-label">Known boundaries</p>
            <ul className="boundary-list">{status.limitations.map((item) => <li key={item}>{limitationLabel[item]}</li>)}</ul>
          </div>
        )}
        {status && (
          <div className="capabilities-content" role="tabpanel" id="models-panel" aria-labelledby="models-tab" hidden={tab !== "models"}>
            <p className="section-label">Approved model boundary</p>
            <ul className="model-list">
              {status.models.map((model) => (
                <li key={model.model_id}>
                  <span>{locationLabel(model.location)}</span>
                  <strong>{model.model_id}</strong>
                  <small>{model.roles.map((role) => roleLabel[role]).join(" · ")}</small>
                </li>
              ))}
            </ul>
            <p className="no-llama"><strong>No Llama-family models or silent substitutes.</strong> The DSpark model assists speculative decoding; it is not a route or fallback.</p>
          </div>
        )}
        <div className="capabilities-footer">Evidence is restricted to the selected point in time.</div>
      </aside>
    </form>
  );
}
