import { useEffect, useState } from "react";
import { InvestigationForm, type InvestigationSeed } from "./components/InvestigationForm";
import { EventExplorer } from "./components/EventExplorer";
import { MarketDashboard } from "./components/MarketDashboard";
import { PartnershipPage } from "./components/PartnershipPage";
import { ReportView } from "./components/Report";
import { ResearchPanel } from "./components/ResearchPanel";
import { useInvestigation } from "./hooks/useInvestigation";
import { useSystemStatus, type ReadinessState } from "./hooks/useSystemStatus";
import { getShockEvents, type ShockEvent } from "./api/events";
import { parseFailure, terminalTurn, type Investigation, type InvestigationRequest, type Scope, type TerminalOutcome, type TerminalTurn } from "./api/types";
import type { InvestigationState } from "./state/investigation";

// Modified NVIDIA eye path adapted from AI-Q's Apache-2.0 Logo component.
// See THIRD_PARTY_NOTICES.md and LICENSES/Apache-2.0.txt.
function NvidiaMark() {
  return (
    <svg className="nvidia-mark" viewBox="0 0 71 47" role="img" aria-label="NVIDIA">
      <path
        fill="currentColor"
        d="M7.255 20.234s6.419-9.476 19.236-10.455V6.34C12.294 7.481 0 19.51 0 19.51s6.963 20.138 26.491 21.982v-3.655C12.161 36.032 7.255 20.234 7.255 20.234zM26.49 30.57v3.346c-10.83-1.931-13.837-13.194-13.837-13.194s5.2-5.764 13.837-6.698v3.672c-4.532-.544-8.09 3.69-8.09 3.69s1.984 7.131 8.09 9.184zM26.49 0v6.341c.417-.033.834-.06 1.253-.074 16.14-.544 26.658 13.242 26.658 13.242s-12.08 14.694-24.663 14.694c-1.153 0-2.234-.107-3.248-.287v3.92a21.24 21.24 0 002.704.176c11.71 0 20.18-5.982 28.38-13.063 1.359 1.089 6.925 3.738 8.07 4.9-7.797 6.529-25.968 11.792-36.27 11.792-.993 0-1.947-.06-2.884-.15V47H71V0H26.491zm0 14.024V9.78c.412-.03.829-.052 1.253-.065 11.607-.365 19.222 9.977 19.222 9.977S38.742 31.12 29.923 31.12c-1.27 0-2.407-.205-3.432-.55V17.697c4.52.546 5.428 2.543 8.145 7.073l6.042-5.096s-4.41-5.787-11.845-5.787c-.81 0-1.583.057-2.342.138z"
      />
    </svg>
  );
}

function GitHubMark() {
  return (
    <svg className="github-mark" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 2C6.477 2 2 6.59 2 12.253c0 4.53 2.865 8.373 6.839 9.73.5.095.682-.222.682-.494 0-.244-.009-.89-.014-1.747-2.782.619-3.369-1.374-3.369-1.374-.455-1.184-1.11-1.499-1.11-1.499-.908-.636.069-.623.069-.623 1.003.073 1.531 1.057 1.531 1.057.892 1.566 2.341 1.114 2.91.852.091-.663.349-1.114.635-1.37-2.221-.259-4.556-1.139-4.556-5.067 0-1.119.39-2.034 1.029-2.752-.103-.259-.446-1.302.098-2.713 0 0 .84-.276 2.75 1.051A9.32 9.32 0 0 1 12 6.984a9.32 9.32 0 0 1 2.504.346c1.909-1.327 2.748-1.051 2.748-1.051.545 1.411.202 2.454.1 2.713.64.718 1.028 1.633 1.028 2.752 0 3.938-2.339 4.805-4.566 5.058.359.317.679.943.679 1.901 0 1.372-.013 2.479-.013 2.816 0 .274.18.594.688.493A10.26 10.26 0 0 0 22 12.253C22 6.59 17.523 2 12 2Z"
      />
    </svg>
  );
}

const readable = (value: string) => value.replaceAll("_", " ");
const routeLabel = (value: InvestigationRequest["route_mode"]) => value === "switchyard_escalation" ? "Switchyard escalation" : readable(value);
const turnId = (ordinal: number) => `turn-${String(ordinal).padStart(4, "0")}`;
const readinessLabel: Record<ReadinessState, string> = {
  loading: "Checking system",
  ready: "Ready",
  degraded: "Degraded",
  unavailable: "Status unavailable",
};
const connectionLabel: Record<InvestigationState["connection"], string> = {
  idle: "Session idle",
  connecting: "Session connecting",
  live: "Live updates",
  reconnecting: "Session reconnecting",
  closed: "Session closed",
};

export type AppView = "dashboard" | "research" | "built-together";

export function resolveAppView(pathname: string, search = ""): AppView {
  if (new URLSearchParams(search).has("investigation")) return "research";
  if (pathname === "/research") return "research";
  if (pathname === "/built-together") return "built-together";
  return "dashboard";
}

export interface PendingTurn { ordinal: number; question: string }

export function projectedPendingTurn(record: Investigation | null, pending: PendingTurn | null, connection: InvestigationState["connection"]): PendingTurn | null {
  return record && pending && pending.ordinal > record.turns.length && ["connecting", "live", "reconnecting"].includes(connection) ? pending : null;
}

function ScopeNotice({ scope }: { scope: Scope | null }) {
  if (!scope || !["clarification", "partially_supported", "refused"].includes(scope.status)) return null;
  return (
    <div className={`scope-message ${scope.status}`}>
      <strong>{readable(scope.status)}</strong>
      <p>{scope.explanation}</p>
    </div>
  );
}

export interface TerminalPresentation {
  readonly title: string;
  readonly explanation: string;
  readonly advice: string;
  readonly technicalCode: string;
}

const routeFailureCopy: Readonly<Record<string, Omit<TerminalPresentation, "technicalCode">>> = {
  context_length_exceeded: {
    title: "This investigation exceeded the model’s context limit",
    explanation: "The accumulated question and evidence were too large for the model to process together. This was not a connection failure; your question is still saved.",
    advice: "Start a new investigation with fewer companies or a narrower question. If it repeats, ask the demo operator to inspect the context size before you try again.",
  },
  invalid_json: {
    title: "The model returned an unusable tool request",
    explanation: "The model replied, but its tool arguments were not valid JSON, so the investigation stopped safely. This was not a connection failure.",
    advice: "Try again once. If it repeats, ask the demo operator to inspect the saved model-response validation error.",
  },
  transport_error: {
    title: "We couldn’t reach the analysis model",
    explanation: "The connection to the model service failed before it returned an answer. Your question is still saved.",
    advice: "Try again. If it happens again, wait for the system status to show Ready or ask the demo operator to check the model service.",
  },
  transport_contract: {
    title: "We couldn’t complete this investigation",
    explanation: "The investigation stopped before it produced an answer the application could safely use. Your question is still saved.",
    advice: "Try again. If the same message returns, ask the demo operator to inspect the saved investigation and its agent steps.",
  },
  timeout: {
    title: "The analysis model took too long to respond",
    explanation: "The model did not finish within the allowed time. Your question is still saved and can be retried.",
    advice: "Try again. If timeouts continue, wait for the system status to show Ready before retrying.",
  },
  provider_error: {
    title: "The analysis model reported a temporary error",
    explanation: "The model service could not complete this request. The investigation stopped without presenting an incomplete answer.",
    advice: "Try again. If it repeats, ask the demo operator to check the model service.",
  },
  identity_mismatch: {
    title: "We stopped before showing an unverified answer",
    explanation: "The responding model did not match the approved model identity, so the investigation failed closed.",
    advice: "Try again once. If this repeats, ask the demo operator to verify model routing before relying on a result.",
  },
  route_unavailable: {
    title: "No approved analysis model is available right now",
    explanation: "The investigation could not find an approved model route. Your question and collected evidence remain saved.",
    advice: "Wait for the system status to show Ready, then try again. If it persists, ask the demo operator to check model routing.",
  },
};

const failureCodeCopy: Readonly<Record<string, Omit<TerminalPresentation, "technicalCode">>> = {
  "synthesis_failure:invalid_evidence": {
    title: "We stopped an answer that went beyond the evidence",
    explanation: "The research completed, but the draft included a detail that was not supported by the retained cutoff-qualified sources, so no answer was shown.",
    advice: "Try again, or narrow the question to the specific company, date, or evidence comparison you want. If it repeats, ask the demo operator to inspect the cited evidence.",
  },
  "synthesis_failure:agent_turn_limit": {
    title: "The agent ran out of research steps",
    explanation: "The agent may have gathered evidence, but it reached its step limit before it could complete a supported answer. Your question is still saved.",
    advice: "Try again. If it repeats, make the question narrower or ask the demo operator to inspect the unfinished research steps.",
  },
};

const stageFailureCopy: Readonly<Record<string, Omit<TerminalPresentation, "technicalCode">>> = {
  policy_failure: {
    title: "This request could not pass the research policy checks",
    explanation: "The investigation stopped before using research tools or generating an answer.",
    advice: "Review the supported scope and try a market-research question about one of the listed companies and dates.",
  },
  tool_failure: {
    title: "An evidence tool could not complete the research",
    explanation: "The investigation stopped because required evidence could not be collected safely.",
    advice: "Try again. If it repeats, ask the demo operator to check the evidence services.",
  },
  synthesis_failure: {
    title: "We couldn’t assemble a supported answer",
    explanation: "The research ran, but the resulting answer did not meet the application’s evidence requirements.",
    advice: "Try again, or make the question more specific about the company, date, or comparison you want.",
  },
  verification_failure: {
    title: "The answer did not pass final verification",
    explanation: "The investigation stopped rather than show an answer that could not be fully checked against its evidence.",
    advice: "Try again. If it repeats, narrow the question or ask the demo operator to inspect the verification step.",
  },
  workflow_exception: {
    title: "The investigation stopped unexpectedly",
    explanation: "An internal workflow step failed before a complete answer was available. Your question is still saved.",
    advice: "Try again. If it repeats, ask the demo operator to inspect this investigation.",
  },
  security_unknown: {
    title: "We couldn’t verify the investigation’s security record",
    explanation: "The investigation stopped because its required security telemetry was incomplete.",
    advice: "Try again. If it repeats, ask the demo operator to check tracing and security telemetry.",
  },
  security_violation: {
    title: "The investigation was stopped by a security safeguard",
    explanation: "A protected boundary rejected part of the request, so no answer was presented.",
    advice: "Do not retry unchanged. Ask the demo operator to review the technical details for this investigation.",
  },
};

export function terminalPresentation(terminal: TerminalOutcome, detail: string): TerminalPresentation {
  let failure = null;
  try { failure = parseFailure(detail || null); } catch { /* Preserve a safe generic message for malformed legacy detail. */ }
  const technicalCode = failure ? `${failure.stage}:${failure.code}` : detail || terminal;
  const copy = failureCodeCopy[technicalCode] ?? (failure?.stage === "route_failure" ? routeFailureCopy[failure.code] : failure ? stageFailureCopy[failure.stage] : null);
  if (copy) return { ...copy, technicalCode };
  if (terminal === "route_failure") return {
    title: "The analysis model could not complete this request",
    explanation: "The approved model route stopped before a complete answer was available. Your question is still saved.",
    advice: "Try again. If the same message returns, ask the demo operator to inspect this investigation.",
    technicalCode,
  };
  return {
    title: "The investigation could not complete",
    explanation: "The workflow stopped before a complete, verified answer was available.",
    advice: "Try again. If the problem repeats, ask the demo operator to inspect this investigation.",
    technicalCode,
  };
}

export function TerminalMessage({ terminal, onRetry, retrying = false }: { terminal: TerminalTurn; onRetry?: () => void; retrying?: boolean }) {
  const cancelled = terminal.event.event_type === "cancelled";
  if (cancelled) return (
    <div className="terminal-message cancelled" data-terminal-outcome={terminal.terminal}>
      <strong>Investigation cancelled</strong>
      <p>The investigation was stopped before it produced a complete answer.</p>
    </div>
  );
  const presentation = terminalPresentation(terminal.terminal, terminal.event.detail);
  return (
    <div className={`terminal-message ${terminal.event.event_type}`} data-terminal-outcome={terminal.terminal} role="alert">
      <strong>{presentation.title}</strong>
      <p>{presentation.explanation}</p>
      <div className="terminal-recovery">
        <span>What to do</span>
        <p>{presentation.advice}</p>
        {onRetry && <button className="terminal-retry" type="button" disabled={retrying} onClick={onRetry}>{retrying ? "Trying again…" : "Try again"}</button>}
      </div>
      <details className="terminal-technical">
        <summary>Technical details for support</summary>
        <code>{presentation.technicalCode}</code>
      </details>
    </div>
  );
}

export function ConversationHistory({ record, active, pending = null, onRetry, retrying = false }: {
  record: Investigation;
  active: boolean;
  pending?: PendingTurn | null;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  const terminals = new Map<string, TerminalTurn>();
  for (const event of record.events) {
    const terminal = terminalTurn(event);
    if (terminal) terminals.set(terminal.turn_id, terminal);
  }
  const rows = record.turns.map((question, index) => ({ ordinal: index + 1, question }));
  if (pending && pending.ordinal > rows.length) rows.push(pending);
  else if (rows.length === 0) rows.push({ ordinal: 1, question: record.request.question });

  return (
    <>
      {rows.map((row, index) => {
        const id = turnId(row.ordinal), terminal = terminals.get(id) ?? null, latest = index === rows.length - 1;
        const scope = terminal?.report?.scope ?? (latest ? record.scope : null);
        return (
          <section className="conversation-turn" data-turn-id={id} data-terminal-outcome={terminal?.terminal ?? ""} key={id} aria-label={`Investigation turn ${row.ordinal}`}>
            <div className="user-question">
              <span>You · Turn {row.ordinal}</span>
              <p>{row.question}</p>
            </div>
            <ScopeNotice scope={scope} />
            {terminal?.report ? (
              <ReportView report={terminal.report} />
            ) : terminal ? (
              <TerminalMessage terminal={terminal} onRetry={latest && record.status === "failed" ? onRetry : undefined} retrying={retrying} />
            ) : latest ? (
              <div className="working" aria-live="polite">
                <span className="working-spinner" aria-hidden="true" />
                <div>
                  <strong>{active ? "Investigating" : "Investigation paused"}</strong>
                  <p>{active ? "Reviewing sources and calculations…" : record.error ?? "Ready for the next step."}</p>
                </div>
              </div>
            ) : null}
          </section>
        );
      })}
    </>
  );
}

export function PinnedEventSummary({ eventId, event }: { eventId: string; event: ShockEvent | null }) {
  return (
    <section className="case-event-context" aria-label="Pinned event context">
      <span>Known shock event</span>
      <strong>{event?.title ?? "Pinned event context"}</strong>
      {event ? (
        <dl className="case-event-facts">
          <div><dt>Companies</dt><dd>{event.analysis_tickers.join(", ")}</dd></div>
          <div><dt>Window</dt><dd>{event.start_session}–{event.end_session}</dd></div>
        </dl>
      ) : <p>Full catalog details are not loaded in this browser session. The server still owns and verifies the canonical event scope.</p>}
      <code>{eventId}</code>
    </section>
  );
}

export default function App() {
  const flow = useInvestigation();
  const readiness = useSystemStatus();
  const [view, setView] = useState<AppView>(() => resolveAppView(window.location.pathname, window.location.search));
  const [researchSeed, setResearchSeed] = useState<InvestigationSeed | null>(null);
  const [restoredEvent, setRestoredEvent] = useState<ShockEvent | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [pendingTurn, setPendingTurn] = useState<PendingTurn | null>(null);
  const record = flow.state.record;
  const watchActive = ["connecting", "live", "reconnecting"].includes(flow.state.connection);
  const visiblePendingTurn = projectedPendingTurn(record, pendingTurn, flow.state.connection);
  const pending = visiblePendingTurn !== null;
  const active = watchActive || record?.status === "running" || record?.status === "created" || pending;
  const visibleStatus = record && (watchActive || pending) ? "running" : record?.status;
  const maxTurns = readiness.status?.contracts.max_investigation_turns ?? 4;
  const submittedTurns = (record?.turns.length ?? 0) + (visiblePendingTurn ? 1 : 0);
  const remainingTurns = Math.max(0, maxTurns - submittedTurns);
  const langSmithProjectLink = readiness.status?.observability.project_link;
  const eventId = record?.request.event_id ?? null;
  const seededEvent = eventId && researchSeed?.event?.event_id === eventId ? researchSeed.event : null;
  const activeEvent = seededEvent ?? (eventId && restoredEvent?.event_id === eventId ? restoredEvent : null);

  useEffect(() => {
    if (pendingTurn && (!record || pendingTurn.ordinal <= record.turns.length || !watchActive)) setPendingTurn(null);
  }, [pendingTurn, record, watchActive]);

  useEffect(() => {
    if (!eventId || seededEvent) {
      setRestoredEvent(null);
      return;
    }
    const controller = new AbortController();
    setRestoredEvent(null);
    void getShockEvents(controller.signal)
      .then((catalog) => {
        if (!controller.signal.aborted) setRestoredEvent(catalog.events.find((event) => event.event_id === eventId) ?? null);
      })
      .catch(() => {
        // Catalog context is supplemental: the restored investigation and server-verified event ID remain usable.
      });
    return () => controller.abort();
  }, [eventId, seededEvent]);

  useEffect(() => {
    const onHistory = () => setView(resolveAppView(window.location.pathname, window.location.search));
    window.addEventListener("popstate", onHistory);
    if (!["/", "/research", "/built-together"].includes(window.location.pathname) && !new URLSearchParams(window.location.search).has("investigation")) {
      window.history.replaceState(window.history.state, "", "/");
    }
    return () => window.removeEventListener("popstate", onHistory);
  }, []);

  const navigate = (next: AppView) => {
    const target = next === "dashboard"
      ? "/"
      : next === "built-together"
        ? "/built-together"
        : record
          ? `/research?investigation=${encodeURIComponent(record.investigation_id)}`
          : "/research";
    window.history.pushState({ view: next }, "", target);
    setView(next);
  };

  const selectResearchEvent = (event: ShockEvent | null) => {
    if (!event) {
      setResearchSeed((current) => current ? { ...current, event: null, eventQuestion: null } : null);
      return;
    }
    const primary = readiness.status?.supported_tickers.find((candidate) => candidate === event.primary_ticker);
    if (!primary) return;
    const suggestedQuestion = event.questions[0] ?? null;
    setResearchSeed({
      ticker: primary,
      as_of: event.default_cutoff,
      question: suggestedQuestion?.text ?? "",
      event,
      eventQuestion: suggestedQuestion,
    });
  };

  const reset = () => {
    setFollowUp("");
    setPendingTurn(null);
    setResearchSeed(null);
    flow.reset();
  };

  const researchHref = record ? `/research?investigation=${encodeURIComponent(record.investigation_id)}` : "/research";

  return (
    <div className={`app-shell${view === "research" && !record ? " research-start-shell" : ""}`}>
      <header className="app-bar">
        <a className="brand" href="/" aria-label="Market Shock home" onClick={(event) => { event.preventDefault(); navigate("dashboard"); }}>
          <NvidiaMark />
          <strong>Market Shock</strong>
        </a>
        <nav className="app-navigation" aria-label="Primary navigation">
          <a href="/" aria-current={view === "dashboard" ? "page" : undefined} onClick={(event) => { event.preventDefault(); navigate("dashboard"); }}>Dashboard</a>
          <a href={researchHref} data-has-investigation={record ? "true" : "false"} aria-current={view === "research" ? "page" : undefined} onClick={(event) => { event.preventDefault(); navigate("research"); }}>Research</a>
          <a href="/built-together" aria-current={view === "built-together" ? "page" : undefined} onClick={(event) => { event.preventDefault(); navigate("built-together"); }}>Built Together</a>
        </nav>
        {record && view === "research" && <span className="session-title">{record.request.question}</span>}
        <div className="app-actions">
          {record && view === "research" && !active && <button className="new-investigation" data-action="reset-investigation" type="button" onClick={reset}>Start new investigation</button>}
          {record && view === "research" && (
            <div className="connection" data-session-connection={flow.state.connection} aria-live="polite">
              <span className={["reconnecting", "closed"].includes(flow.state.connection) ? "connection-dot warning" : "connection-dot"} />
              {connectionLabel[flow.state.connection]}
            </div>
          )}
          <div
            className="connection"
            data-system-status={readiness.state}
            aria-label={`System ${readiness.state}: ${readiness.detail}`}
            title={readiness.detail}
            aria-live="polite"
          >
            <span className={readiness.state === "ready" ? "connection-dot" : "connection-dot warning"} />
            {readinessLabel[readiness.state]}{readiness.state === "degraded" ? ` · ${readiness.detail}` : ""}
          </div>
          {langSmithProjectLink && (
            <a
              className="langsmith-link"
              href={langSmithProjectLink}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Open LangSmith traces in a new tab"
            >
              LangSmith <span aria-hidden="true">↗</span>
            </a>
          )}
          <a
            className="repository-link"
            href="https://github.com/pastorsj/market-analysis-demo"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Open the Market Shock demo source code on GitHub in a new tab"
          >
            <GitHubMark />
            <span>GitHub</span>
            <span className="external-link-arrow" aria-hidden="true">↗</span>
          </a>
        </div>
      </header>

      {view === "research" && (
        <aside className="research-disclosure" aria-label="Research use and privacy notice">
          <p><strong>Historical research demo — not investment advice.</strong> Do not enter personal or confidential information. Prompts, evidence, and outputs may be sent to approved internal inference and, when configured, LangSmith tracing.</p>
        </aside>
      )}

      {view === "research" && flow.state.error && (
        <aside className="banner" role="alert">
          <span>{flow.state.error}</span>
          <button disabled={flow.state.busy} onClick={() => flow.reconnect()}>Reconnect investigation</button>
        </aside>
      )}

      {view === "dashboard" ? (
        <MarketDashboard
          status={readiness.status}
          onOpenResearch={(seed) => {
            if (seed) {
              setResearchSeed({ ticker: seed.ticker, as_of: seed.asOf, question: "", event: null, eventQuestion: null });
              flow.reset();
              window.history.pushState({ view: "research" }, "", "/research");
              setView("research");
              return;
            }
            navigate("research");
          }}
          onOpenTechnology={() => navigate("built-together")}
        />
      ) : view === "built-together" ? (
        <PartnershipPage
          status={readiness.status}
        />
      ) : !record ? (
        <main className="empty-state">
          <InvestigationForm
            busy={flow.state.busy}
            initialSeed={researchSeed}
            onUseCustomScope={setResearchSeed}
            scenarioPicker={(
              <EventExplorer
                compact
                selectedEvent={researchSeed?.event ?? null}
                availableTickers={readiness.status?.supported_tickers ?? []}
                coverage={readiness.status?.coverage ?? null}
                onSelectEvent={selectResearchEvent}
              />
            )}
            onSubmit={(request: InvestigationRequest) => {
              void flow.create(request);
            }}
          />
        </main>
      ) : (
        <main className="investigation-view">
          <aside className="case-panel">
            <div className="panel-heading"><h2>Investigation</h2></div>
            <div className="case-summary">
              <span className={`case-status ${visibleStatus}`}>{readable(visibleStatus ?? record.status)}</span>
              {record.request.event_id && <PinnedEventSummary eventId={record.request.event_id} event={activeEvent} />}
              <dl>
                <div><dt>Company</dt><dd data-scope-company="">{record.scope?.ticker ?? record.request.ticker ?? "Pending"}</dd></div>
                <div><dt>Evidence cutoff</dt><dd data-scope-cutoff="">{record.scope?.as_of ? new Date(record.scope.as_of).toLocaleDateString() : record.request.as_of ?? "Pending"}</dd></div>
                <div><dt>Route</dt><dd data-scope-route={record.request.route_mode}>{routeLabel(record.request.route_mode)}</dd></div>
                <div><dt>Case</dt><dd className="mono" data-scope-case="">{record.investigation_id.slice(0, 8)}</dd></div>
              </dl>
            </div>
            <div className="panel-note">
              <strong>Cutoff-bounded reconstruction</strong>
              <p>Sources and observations are filtered by time; this historical snapshot was captured later.</p>
            </div>
          </aside>

          <section className="conversation-pane">
            <div className="conversation-scroll">
              <div className="conversation-lane">
                <ConversationHistory
                  record={record}
                  active={active}
                  pending={visiblePendingTurn}
                  onRetry={remainingTurns > 0 ? () => void flow.retry() : undefined}
                  retrying={flow.state.busy}
                />
              </div>
            </div>

            {!active && record.status !== "refused" && remainingTurns > 0 && (
              <form
                className="follow-up"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (followUp.trim().length > 1) {
                    const question = followUp.trim();
                    setPendingTurn({ ordinal: record.turns.length + 1, question });
                    void flow.turn(question);
                    setFollowUp("");
                  }
                }}
              >
                <div className="follow-up-heading">
                  <label htmlFor="follow-up">Continue this investigation</label>
                  <span role="status">{remainingTurns} {remainingTurns === 1 ? "turn" : "turns"} remaining · {maxTurns} max</span>
                </div>
                <div className="follow-up-composer">
                  <input id="follow-up" value={followUp} onChange={(event) => setFollowUp(event.target.value)} placeholder="Ask a follow-up question…" />
                  <button disabled={followUp.trim().length < 2} aria-label="Send">↑</button>
                </div>
              </form>
            )}
            {!active && record.status !== "refused" && remainingTurns === 0 && (
              <section className="turn-limit" aria-label="Investigation turn limit" role="status">
                <div>
                  <strong>Turn limit reached</strong>
                  <p>All {maxTurns} submitted turns have been used. Start a new investigation to continue researching.</p>
                </div>
                <button type="button" onClick={reset}>Start new investigation</button>
              </section>
            )}
          </section>

          <ResearchPanel
            record={record}
            events={flow.state.events}
            active={active}
            busy={flow.state.busy}
            onCancel={() => void flow.cancel()}
            onRetry={remainingTurns > 0 ? () => void flow.retry() : undefined}
          />
        </main>
      )}
    </div>
  );
}
