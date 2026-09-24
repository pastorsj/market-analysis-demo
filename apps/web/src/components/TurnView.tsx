import type { ModelCall, Turn } from "../api/types";
import { formatDuration, words } from "../format";
import { ReportView } from "./Report";

function ModelCalls({ calls }: { calls: readonly ModelCall[] }) {
  const escalated = calls.some((call) => call.tier === "capable");
  return (
    <details className="model-calls">
      <summary>{calls.length} model {calls.length === 1 ? "call" : "calls"}{escalated ? " · escalated to capable tier" : ""}</summary>
      <table>
        <thead><tr><th scope="col">Role</th><th scope="col">Model</th><th scope="col">Tier</th><th scope="col">Latency</th><th scope="col">Result</th></tr></thead>
        <tbody>
          {calls.map((call, index) => (
            <tr key={index} className={call.state}>
              <td>{call.role === "judge" ? "Routing judge" : "Agent"}</td>
              <td className="mono">{call.model}</td>
              <td>{call.tier}</td>
              <td>{formatDuration(call.latency_ms)}</td>
              <td>{call.state === "failed" ? call.failure ?? "failed" : "ok"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

export interface TurnViewProps {
  readonly turn: Turn;
  readonly latest: boolean;
  readonly busy: boolean;
  readonly onCancel: () => void;
  readonly onRetry: () => void;
  readonly onAsk?: (question: string) => void;
}

export function TurnView({ turn, latest, busy, onCancel, onRetry, onAsk }: TurnViewProps) {
  return (
    <section className="conversation-turn" aria-label={`Question ${turn.number}`}>
      <div className="user-question">
        <span>You · Question {turn.number}</span>
        <p>{turn.question}</p>
      </div>
      {(turn.skill || turn.model_calls.length > 0) && (
        <div className="turn-meta">
          {turn.skill && <span className="skill-badge">Skill chosen by the agent: <strong>{words(turn.skill)}</strong></span>}
          {turn.model_calls.length > 0 && <ModelCalls calls={turn.model_calls} />}
        </div>
      )}
      {turn.status === "running" && (
        <div className="working" aria-live="polite">
          <span className="working-spinner" aria-hidden="true" />
          <div><strong>Investigating</strong><p>Reading skills, running tools, and checking each step…</p></div>
          {latest && <button type="button" className="secondary-button danger" disabled={busy} onClick={onCancel}>Cancel</button>}
        </div>
      )}
      {turn.status === "completed" && turn.report && <ReportView report={turn.report} onAsk={latest ? onAsk : undefined} />}
      {(turn.status === "failed" || turn.status === "cancelled") && (
        <div className={`terminal-message ${turn.status === "failed" ? "error" : "cancelled"}`} role={turn.status === "failed" ? "alert" : "status"}>
          <strong>{turn.status === "failed" ? "This question could not be completed" : "This question was cancelled"}</strong>
          {turn.error && <p>{turn.error.message}</p>}
          {turn.error && <details className="terminal-technical"><summary>Technical details</summary><code>{turn.error.code}</code></details>}
          {latest && <button type="button" className="terminal-retry" disabled={busy} onClick={onRetry}>{busy ? "Retrying…" : "Retry"}</button>}
        </div>
      )}
    </section>
  );
}
