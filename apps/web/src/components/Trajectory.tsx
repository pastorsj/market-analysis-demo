import { isTerminalEvent, type ExternalActionObservation, type ModelAttempt, type SecurityReceipt, type TerminalOutcome, type TrajectoryEvent } from "../api/types";

const names: Record<string, string> = {
  tool_started: "Running analysis",
  tool_completed: "Evidence received",
  planning: "Planning",
  routing: "Model attempt",
  scope: "Scope checked",
  report: "Answer prepared",
  error: "Error",
  cancelled: "Cancelled",
};

const words = (value: string) => value.replaceAll("_", " ");
const record = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;

function attemptDetail(attempt: ModelAttempt) {
  const identity = attempt.identity_evidence === "direct_provider_verified"
    ? `Direct provider identity verified: ${attempt.model_assertion}.`
    : attempt.identity_evidence === "switchyard_target_asserted"
      ? `Switchyard target asserted: ${attempt.model_assertion}. Provider identity unavailable.`
      : "Model identity evidence unavailable.";
  const failure = attempt.failure_class ? ` Typed failure: ${words(attempt.failure_class)}.` : "";
  const tier = attempt.selected_tier ? ` ${words(attempt.selected_tier)} tier.` : "";
  return `${identity}${tier} ${attempt.latency_ms.toFixed(0)} ms. Call ${attempt.application_call_id}; request ${attempt.application_request_id}.${failure}`;
}

function securityDetail(receipt: SecurityReceipt | null) {
  if (!receipt) return "Security telemetry missing — this result is not verified.";
  if (receipt.completeness === "verified") return "Security verified for this turn.";
  if (receipt.completeness === "unknown") return `Security telemetry unknown${receipt.unknown_reasons.length ? `: ${receipt.unknown_reasons.map(words).join(", ")}` : ""}.`;
  const detail = receipt.violations.map((violation) => words(violation.code)).join(", ");
  return `Security violation recorded${detail ? `: ${detail}` : ""}.`;
}

function externalActionDetail(action: ExternalActionObservation) {
  return `External action: ${action.action_class} · ${action.request_state} · ${action.decision} · ${action.attempt} · ${action.outcome}`;
}

function EventDetails({ event }: { event: TrajectoryEvent }) {
  const payload = record(event.payload);
  const attempt = record(payload?.attempt) as unknown as ModelAttempt | null;
  const receipt = record(payload?.security_receipt) as unknown as SecurityReceipt | null;
  const terminal = typeof payload?.terminal === "string" ? payload.terminal as TerminalOutcome : null;
  const isTerminal = isTerminalEvent(event);
  const failedTerminal = isTerminal && event.event_type === "error";

  return (
    <>
      {failedTerminal
        ? <p>The investigation stopped before a complete answer was available. See the answer pane for recovery guidance.</p>
        : event.detail && <p>{event.detail}</p>}
      {attempt && (
        <p
          className="model-attempt"
          data-application-call-id={attempt.application_call_id}
          data-application-request-id={attempt.application_request_id}
          data-switchyard-trial-id={attempt.switchyard_trial_id ?? ""}
        >
          {attemptDetail(attempt)}
        </p>
      )}
      {isTerminal && terminal && terminal !== "success" && !failedTerminal && <p>Terminal outcome: {words(terminal)}.</p>}
      {isTerminal && <p>{securityDetail(receipt)}</p>}
      {isTerminal && receipt?.external_actions.map((action) => (
        <p className="external-action-receipt" data-action-observation-id={action.observation_id} key={action.observation_id}>{externalActionDetail(action)}</p>
      ))}
    </>
  );
}

export function Trajectory({ events }: { events: TrajectoryEvent[] }) {
  return (
    <section className="trajectory" aria-label="Investigation trajectory">
      <div className="trajectory-heading">
        <h2>Investigation trail</h2>
        <span>{events.length} {events.length === 1 ? "step" : "steps"}</span>
      </div>
      {events.length === 0 ? (
        <p className="muted">Activity will appear here.</p>
      ) : (
        <ol>
          {events.map((event) => {
            const turnId = typeof event.payload.turn_id === "string" ? event.payload.turn_id : undefined;
            return (
              <li key={event.sequence} className={event.event_type} data-event-sequence={event.sequence} data-event-type={event.event_type} data-turn-id={turnId}>
                <span className="event-marker" aria-hidden="true" />
                <div>
                  <small>{names[event.event_type] ?? event.event_type}{event.tool ? ` · ${event.tool.replaceAll("_", " ")}` : ""}</small>
                  <strong>{event.label}</strong>
                  <EventDetails event={event} />
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
