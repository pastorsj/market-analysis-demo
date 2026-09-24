import { useCallback, useState } from "react";
import type { ShockEvent, SystemStatus } from "../api/types";
import { EventExplorer } from "../components/EventExplorer";
import { InvestigationForm, type InvestigationSeed } from "../components/InvestigationForm";
import { ResearchPanel } from "../components/ResearchPanel";
import { TurnView } from "../components/TurnView";
import type { InvestigationFlow } from "../hooks/useInvestigation";

export interface ResearchProps {
  readonly status: SystemStatus | null;
  readonly statusError: string | null;
  readonly flow: InvestigationFlow;
  readonly seed: InvestigationSeed | null;
}

function FollowUp({ flow, maxTurns }: { flow: InvestigationFlow; maxTurns: number }) {
  const [question, setQuestion] = useState("");
  const investigation = flow.investigation!;
  const remaining = Math.max(0, maxTurns - investigation.turns.length);
  if (investigation.status === "running") return null;
  if (remaining === 0) {
    return (
      <section className="turn-limit" role="status" aria-label="Question limit">
        <div><strong>Question limit reached</strong><p>An investigation holds up to {maxTurns} questions. Start a new one to keep researching.</p></div>
        <button type="button" onClick={flow.reset}>Start new investigation</button>
      </section>
    );
  }
  return (
    <form className="follow-up" onSubmit={(event) => {
      event.preventDefault();
      if (question.trim().length < 2) return;
      void flow.followUp(question.trim());
      setQuestion("");
    }}>
      <div className="follow-up-heading">
        <label htmlFor="follow-up">Continue this investigation</label>
        <span>{remaining} of {maxTurns} questions left</span>
      </div>
      <div className="follow-up-composer">
        <input id="follow-up" value={question} disabled={flow.busy} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a follow-up question…" />
        <button disabled={flow.busy || question.trim().length < 2} aria-label="Send follow-up">↑</button>
      </div>
    </form>
  );
}

export function Research({ status, statusError, flow, seed }: ResearchProps) {
  const [event, setEvent] = useState<ShockEvent | null>(null);
  const clearEvent = useCallback(() => setEvent(null), []);
  const investigation = flow.investigation;

  if (!investigation) {
    return (
      <main className="empty-state">
        <InvestigationForm
          status={status}
          statusError={statusError}
          busy={flow.busy}
          seed={seed}
          event={event}
          onClearEvent={clearEvent}
          onSubmit={(body) => void flow.start(body)}
          scenarioPicker={(
            <EventExplorer
              selectedEvent={event}
              availableTickers={status?.companies.map((company) => company.symbol) ?? []}
              coverage={status?.coverage ?? null}
              onSelectEvent={setEvent}
            />
          )}
        />
      </main>
    );
  }

  const scope = investigation.scope;
  const running = investigation.status === "running";
  return (
    <main className="investigation-view">
      <aside className="case-panel">
        <div className="panel-heading"><h2>Investigation</h2></div>
        <div className="case-summary">
          <span className={`case-status ${investigation.status}`}>{investigation.status}</span>
          <dl>
            <div><dt>Company</dt><dd>{scope.ticker ?? "Not resolved"}</dd></div>
            {scope.members.length > 1 && <div><dt>Companies in scope</dt><dd>{scope.members.join(", ")}</dd></div>}
            <div><dt>Evidence cutoff</dt><dd>{scope.as_of ? new Date(scope.as_of).toLocaleString() : "Not resolved"}</dd></div>
            {scope.session && <div><dt>Market session</dt><dd>{scope.session}</dd></div>}
            {scope.event_id && <div><dt>Curated event</dt><dd className="mono">{scope.event_id}</dd></div>}
          </dl>
          {scope.status === "needs_input" && (
            <div className="scope-message" role="status">
              <strong>Needs {scope.missing.join(" and ") || "more detail"}</strong>
              {scope.note && <p>{scope.note}</p>}
            </div>
          )}
        </div>
        <div className="panel-note">
          <strong>Cutoff-bounded reconstruction</strong>
          <p>Sources and observations are filtered by time; this historical snapshot was captured later.</p>
        </div>
        {!running && <div className="panel-note"><button type="button" className="secondary-button" onClick={flow.reset}>Start new investigation</button></div>}
      </aside>

      <section className="conversation-pane">
        {flow.error && (
          <div className="banner" role="alert">
            <span>{flow.error}</span>
            <button type="button" disabled={flow.busy} onClick={() => void flow.reload()}>Reload</button>
          </div>
        )}
        <div className="conversation-scroll">
          <div className="conversation-lane">
            {investigation.turns.map((turn, index) => (
              <TurnView
                key={turn.number}
                turn={turn}
                latest={index === investigation.turns.length - 1}
                busy={flow.busy}
                onCancel={() => void flow.cancel()}
                onRetry={() => void flow.retry()}
                onAsk={investigation.turns.length < (status?.max_turns ?? 0) ? (question) => void flow.followUp(question) : undefined}
              />
            ))}
          </div>
        </div>
        {status && <FollowUp flow={flow} maxTurns={status.max_turns} />}
      </section>

      <ResearchPanel investigation={investigation} />
    </main>
  );
}
