import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationHistory, projectedPendingTurn } from "../App";
import { ArtifactView } from "../components/ArtifactView";
import { InvestigationForm } from "../components/InvestigationForm";
import { ReportView } from "../components/Report";
import { Trajectory } from "../components/Trajectory";
import { initialState, reducer } from "../state/investigation";
import { createInvestigation, getInvestigation, readEvents } from "./client";
import {
  CAPABILITY_ROUTE,
  LOCAL_MODEL,
  LUNA_MODEL,
  SOL_MODEL,
  decodeInvestigation,
  parseFailure,
  type Artifact,
  type Investigation,
  type ModelAttempt,
  type Report,
  type SecurityReceipt,
  type SwitchyardTrialBundle,
  type SwitchyardTrialRow,
  type TrajectoryEvent,
} from "./types";

const investigationId = "11111111-1111-4111-8111-111111111111";
const timestamp = "2025-01-27T17:00:00-05:00";
const inertProjectionKey = "frontier_\u006catched";

const event = (sequence: number, event_type: TrajectoryEvent["event_type"] = "planning", payload: Record<string, unknown> = {}): TrajectoryEvent => ({ sequence, event_type, label: `Step ${sequence}`, detail: "", occurred_at: timestamp, tool: null, payload });

const request = (route_mode: "local_only" | "switchyard_escalation" | "frontier_only" = "local_only") => ({
  question: "Why did NVDA move on 2025-01-27?", ticker: "NVDA", as_of: "2025-01-27", route_mode,
});

const scope = (): Report["scope"] => ({
  status: "supported", action: "answer", ticker: "NVDA", as_of: "2025-01-27T16:00:00-05:00",
  market_as_of: "2025-01-27T16:00:00-05:00",
  timezone: "America/New_York", supported_universe: ["NVDA", "AMD", "JPM", "GS", "SCHW"],
  explanation: "Verified point-in-time scope.", resolved_tickers: ["NVDA"], group_key: null,
});

const compatibilityRouting = (route_mode: "local_only" | "switchyard_escalation" | "frontier_only" = "local_only", model = "deterministic", returned_model: string | null = null, latency_ms = 0) => ({
  requested_mode: route_mode, effective_mode: route_mode, configured_model: model, returned_model,
  reason: returned_model ? "direct provider identity verified" : "deterministic policy or evidence path",
  remote_attempted: model !== "deterministic" && route_mode !== "local_only", fallback_used: false, [inertProjectionKey]: false, latency_ms,
});

const baseReport = (): Report => ({
  schema_version: "1.0", title: "NVDA evidence", summary: "A bounded answer.", scope: scope(), no_data_reasons: [],
  claims: [], citations: [], uncertainty: [], receipts: [], routing: compatibilityRouting() as Report["routing"],
  answer_mode: "deterministic_evidence", model_attempts: [], switchyard_trials: [], artifacts: [], generated_at: timestamp,
});

const citation = (citation_id: string, title: string): Report["citations"][number] => ({
  citation_id, evidence_id: `evidence-${citation_id}`, title, url: `https://example.test/${citation_id}`,
  source_type: "news", published_at: timestamp, available_at: timestamp, excerpt: `${title} excerpt.`,
  content_sha256: "a".repeat(64), hindsight: false,
});

const trialRow = (model: string, tier: SwitchyardTrialRow["tier"]): SwitchyardTrialRow => ({
  model, tier, prompt_tokens: 10, cached_tokens: 0, cache_creation_tokens: 0,
  completion_tokens: 4, reasoning_tokens: 0, total_tokens: 14,
});

const classifiedTrial = (): SwitchyardTrialBundle => ({
  trial_id: "trial-00000001", classifier_row: trialRow(LUNA_MODEL, "classifier"), terminal_row: trialRow(SOL_MODEL, "strong"),
  classifier_validity: "classified", fallback_reason: null, prior_target_model: "unavailable",
  prior_target_error: "unavailable", prior_target_row: "unavailable", provider_identity: "unavailable",
});

const ambiguousTrial = (): SwitchyardTrialBundle => ({
  ...classifiedTrial(), trial_id: "trial-00000002", terminal_row: trialRow(LOCAL_MODEL, ""),
  classifier_validity: "ambiguous_fallthrough", fallback_reason: null,
});

const fallthroughTrial = (): SwitchyardTrialBundle => ({
  ...ambiguousTrial(), trial_id: "trial-00000003",
  fallback_reason: "Switchyard selected a terminal target after an ambiguous classifier result.",
});

const unavailableTrial = (): SwitchyardTrialBundle => ({
  ...classifiedTrial(), trial_id: "trial-00000004", terminal_row: null,
  classifier_validity: "unavailable", fallback_reason: null,
});

const directAttempt = (role: ModelAttempt["role"] = "answer_synthesis", sequence = 1): ModelAttempt => ({
  role, algorithm: "direct_local", destination_class: "local_model",
  configured_model: LOCAL_MODEL, model_assertion: LOCAL_MODEL, identity_evidence: "direct_provider_verified",
  state: "succeeded", failure_class: null, application_call_id: `call-${String(sequence).padStart(8, "0")}`,
  application_request_id: `request-${String(sequence).padStart(8, "0")}`, latency_ms: 7 + sequence,
  tokens: { prompt: 10, completion: 4, total: 14 }, validation_status: "valid", switchyard_trial_id: null, selected_tier: null,
});

const routedAttempt = (trial: SwitchyardTrialBundle = classifiedTrial(), role: ModelAttempt["role"] = "answer_synthesis", sequence = 20): ModelAttempt => ({
  role, algorithm: "switchyard_capability", destination_class: "loopback_switchyard",
  configured_model: CAPABILITY_ROUTE, model_assertion: trial.terminal_row?.model ?? null,
  identity_evidence: "switchyard_target_asserted", state: "succeeded", failure_class: null,
  application_call_id: `call-${String(sequence).padStart(8, "0")}`, application_request_id: `request-${String(sequence).padStart(8, "0")}`, latency_ms: 12 + sequence,
  tokens: { prompt: 12, completion: 5, total: 17 }, validation_status: "valid", switchyard_trial_id: trial.trial_id, selected_tier: null,
});

const escalationAttempt = (tier: "judge" | "efficient" | "capable", sequence: number): ModelAttempt => {
  const model = tier === "judge" ? LUNA_MODEL : tier === "efficient" ? LOCAL_MODEL : SOL_MODEL;
  return {
    role: tier === "judge" ? "routing_judge" : "agent_reasoning", algorithm: "switchyard_escalation",
    destination_class: tier === "efficient" ? "local_model" : "internal_inference", configured_model: model,
    model_assertion: model, identity_evidence: "direct_provider_verified", state: "succeeded", failure_class: null,
    application_call_id: `call-escalation-${sequence}`, application_request_id: `request-escalation-${sequence}`,
    latency_ms: 20 + sequence, tokens: { prompt: 15, completion: 6, total: 21 }, validation_status: "valid",
    switchyard_trial_id: null, selected_tier: tier,
  };
};

const formatterAttempt = (failed = false): ModelAttempt => ({
  role: "report_formatting", algorithm: "direct_frontier", destination_class: "internal_inference",
  configured_model: SOL_MODEL, model_assertion: failed ? null : SOL_MODEL,
  identity_evidence: failed ? "unavailable" : "direct_provider_verified",
  state: failed ? "failed" : "succeeded", failure_class: failed ? "transport_error" : null,
  application_call_id: failed ? "call-formatter-failed" : "call-formatter-success",
  application_request_id: failed ? "request-formatter-failed" : "request-formatter-success",
  latency_ms: 31, tokens: { prompt: 20, completion: failed ? 0 : 5, total: failed ? 20 : 25 },
  validation_status: failed ? "invalid" : "valid", switchyard_trial_id: null, selected_tier: null,
});

const directReport = (): Report => {
  const attempts = [directAttempt("skill_selection", 11), directAttempt("investigation_planning", 12), directAttempt("evidence_review", 13), directAttempt("answer_synthesis", 14)], attempt = attempts.at(-1)!;
  return { ...baseReport(), answer_mode: "model_synthesis", model_attempts: attempts, routing: compatibilityRouting("local_only", LOCAL_MODEL, LOCAL_MODEL, attempt.latency_ms) as Report["routing"] };
};

const routedReport = (trial: SwitchyardTrialBundle = classifiedTrial()): Report => {
  const attempts = [directAttempt("skill_selection", 11), directAttempt("investigation_planning", 12), directAttempt("evidence_review", 13), routedAttempt(trial, "answer_synthesis", 20)], attempt = attempts.at(-1)!;
  return { ...baseReport(), answer_mode: "model_synthesis", model_attempts: attempts, switchyard_trials: [trial], routing: compatibilityRouting("switchyard_escalation", CAPABILITY_ROUTE, null, attempt.latency_ms) as Report["routing"] };
};

const fullyRoutedReport = (): Report => {
  const trials = ["trial-stage-01", "trial-stage-02", "trial-stage-03", "trial-stage-04"].map((trial_id) => ({ ...classifiedTrial(), trial_id }));
  const roles: ModelAttempt["role"][] = ["skill_selection", "investigation_planning", "evidence_review", "answer_synthesis"];
  const attempts = roles.map((role, index) => routedAttempt(trials[index], role, 31 + index)), attempt = attempts.at(-1)!;
  return { ...baseReport(), answer_mode: "model_synthesis", model_attempts: attempts, switchyard_trials: trials, routing: compatibilityRouting("switchyard_escalation", CAPABILITY_ROUTE, null, attempt.latency_ms) as Report["routing"] };
};

const escalationReport = (): Report => {
  const attempts = [escalationAttempt("judge", 1), escalationAttempt("efficient", 2)], final = attempts.at(-1)!;
  return {
    ...baseReport(), answer_mode: "model_synthesis", model_attempts: attempts, switchyard_trials: [],
    routing: { ...compatibilityRouting("switchyard_escalation", "switchyard/escalation", final.model_assertion, final.latency_ms), remote_attempted: true } as Report["routing"],
  };
};

const escalationReportWithTransportRetry = () => {
  const report = escalationReport(), success = escalationAttempt("capable", 3);
  const failed: ModelAttempt = {
    ...success,
    model_assertion: null,
    identity_evidence: "unavailable",
    state: "failed",
    failure_class: "transport_error",
    application_call_id: "call-escalation-3-first",
    latency_ms: 5,
    tokens: { prompt: 0, completion: 0, total: 0 },
    validation_status: "invalid",
  };
  report.model_attempts.push(failed, success);
  Object.assign(report.routing, {
    returned_model: success.model_assertion,
    remote_attempted: true,
    [inertProjectionKey]: true,
    latency_ms: success.latency_ms,
  });
  return { report, failed, success };
};

const action = (): SecurityReceipt["external_actions"][number] => ({
  schema_version: "security-observation-v1", observation_id: "action-00000001", action_class: "none",
  request_state: "not_requested", decision: "allowed", attempt: "not_attempted", outcome: "succeeded", limitation: null,
});

const verifiedSecurity = (turn_id = "turn-0001"): SecurityReceipt => ({
  schema_version: "security-receipt-v1", investigation_id: investigationId, turn_id,
  completeness: "verified", network_egress: [], external_actions: [action()], trust_boundary: "verified",
  violations: [], unknown_reasons: [],
  secret_scan: { schema_version: "security-observation-v1", status: "pass", checked_variables: ["NVIDIA_INFERENCE_API_KEY"], matched_variables: [], limitation: null },
  finalized_at: timestamp,
});

const networkForAttempt = (attempt: ModelAttempt, trial: SwitchyardTrialBundle | null = null) => {
  const routed = attempt.algorithm === "switchyard_capability", fallback = trial?.fallback_reason !== null && trial?.fallback_reason !== undefined;
  const boundary = routed ? "switchyard" : attempt.destination_class === "local_model" ? "local_model" : "frontier_model";
  return {
    schema_version: "security-observation-v1", observation_id: `network-${attempt.application_call_id.slice(5)}`,
    boundary, destination_class: attempt.destination_class,
    decision: "allowed", attempt: "attempted", outcome: attempt.state === "failed" && (attempt.validation_status !== "invalid" || attempt.identity_evidence === "unavailable") ? "failed" : "succeeded",
    call_id: attempt.application_call_id, application_request_id: attempt.application_request_id,
    switchyard_trial_id: routed ? attempt.switchyard_trial_id : null,
    identity_evidence: attempt.identity_evidence === "unavailable" ? null : attempt.identity_evidence,
    switchyard_trial_bundle: routed ? trial : null,
    unavailable_fields: routed ? ["provider_identity", ...(fallback ? ["prior_target_model", "prior_target_error", "prior_target_row"] : [])] : [],
    unavailable_provenance: routed ? "stock_switchyard_v0.2" : null,
    limitation: routed ? "Provider identity and unavailable target details are not exposed by stock Switchyard v0.2." : null,
  };
};

const securityForAttempt = (attempt: ModelAttempt, trial: SwitchyardTrialBundle | null = null): SecurityReceipt => ({
  ...verifiedSecurity(), network_egress: [networkForAttempt(attempt, trial)] as SecurityReceipt["network_egress"],
});

const securityForReport = (report: Report): SecurityReceipt => ({
  ...verifiedSecurity(),
  network_egress: report.model_attempts.map((attempt) => networkForAttempt(attempt, report.switchyard_trials.find((trial) => trial.trial_id === attempt.switchyard_trial_id) ?? null)) as SecurityReceipt["network_egress"],
});

const terminalRecord = (report: Report = baseReport(), receipt: SecurityReceipt = verifiedSecurity()) => {
  const events = [event(1, "scope")];
  for (const attempt of report.model_attempts) events.push(event(events.length + 1, "routing", { attempt, switchyard_trials: report.switchyard_trials.filter((trial) => trial.trial_id === attempt.switchyard_trial_id) }));
  events.push(event(events.length + 1, "report", { turn_id: receipt.turn_id, terminal: "success", report, security_receipt: receipt }));
  return {
    investigation_id: investigationId, created_at: timestamp, updated_at: timestamp, status: "completed",
    request: request(report.routing.requested_mode), turns: [request().question], scope: report.scope,
    active_skill: "market-dislocation", events, security_receipts: [receipt], report, error: null,
  };
};

const multiTurnRecord = (): Investigation => {
  const questions = ["What moved NVDA on the selected date?", "How did its benchmark compare?"];
  const receipts = [verifiedSecurity("turn-0001"), verifiedSecurity("turn-0002")];
  const reports = [
    { ...baseReport(), title: "First-turn answer", summary: "The first bounded answer." },
    { ...baseReport(), title: "Second-turn answer", summary: "The follow-up bounded answer." },
  ];
  const value = {
    ...terminalRecord(reports[1], receipts[1]),
    request: { ...request(), question: questions[1] },
    turns: questions,
    events: [
      event(1, "scope"),
      event(2, "report", { turn_id: "turn-0001", terminal: "success", report: reports[0], security_receipt: receipts[0] }),
      event(3, "scope"),
      event(4, "report", { turn_id: "turn-0002", terminal: "success", report: reports[1], security_receipt: receipts[1] }),
    ],
    security_receipts: receipts,
  };
  return decodeInvestigation(value);
};

const typedFailureRecord = (error = "route_failure:transport_contract", receipt: SecurityReceipt = verifiedSecurity()) => {
  const failure = event(2, "error", { turn_id: receipt.turn_id, terminal: error.split(":")[0], security_receipt: receipt }); failure.detail = error;
  return {
    investigation_id: investigationId, created_at: timestamp, updated_at: timestamp, status: "failed",
    request: request("switchyard_escalation"), turns: [request().question], scope: scope(),
    active_skill: null, events: [event(1, "scope"), failure], security_receipts: [receipt], report: null, error,
  };
};

const unknownSecurity = (): SecurityReceipt => ({
  ...verifiedSecurity(), completeness: "unknown", trust_boundary: "unknown", unknown_reasons: ["secret_scan_error"],
  secret_scan: { schema_version: "security-observation-v1", status: "unknown", checked_variables: [], matched_variables: [], limitation: "Secret scan instrumentation was unavailable." },
});

const violatedSecurity = (): SecurityReceipt => ({
  ...verifiedSecurity(), completeness: "violation", trust_boundary: "violation",
  violations: [{ schema_version: "security-observation-v1", boundary: "secret_scan", code: "secret_exposure", observation_id: null, call_id: null, limitation: "A configured secret matched the outbound payload." }],
  secret_scan: { schema_version: "security-observation-v1", status: "fail", checked_variables: ["NVIDIA_INFERENCE_API_KEY"], matched_variables: ["NVIDIA_INFERENCE_API_KEY"], limitation: null },
});

afterEach(() => vi.unstubAllGlobals());

describe("corrected investigation contract", () => {
  it("accepts a persisted research skill and rejects an unknown skill", () => {
    const value = terminalRecord();
    expect(decodeInvestigation(value).active_skill).toBe("market-dislocation");
    value.active_skill = "unknown-skill";
    expect(() => decodeInvestigation(value)).toThrow(/active skill/i);
  });

  it("accepts a persisted event-bound request and rejects event identity without canonical scope", () => {
    const value = terminalRecord();
    Object.assign(value.request, {
      ticker: "NVDA",
      as_of: "2025-01-27T21:00:00Z",
      event_id: "nvda-deepseek-2025-01-27",
    });
    expect(decodeInvestigation(value).request.event_id).toBe("nvda-deepseek-2025-01-27");
    (value.request as unknown as { ticker: string | null }).ticker = null;
    expect(() => decodeInvestigation(value)).toThrow(/canonical scope/i);
  });

  it("requires the explicit market cutoff and rejects one after the evidence cutoff", () => {
    const missing = terminalRecord();
    delete (missing.scope as unknown as Record<string, unknown>).market_as_of;
    expect(() => decodeInvestigation(missing)).toThrow(/scope fields/i);

    const late = terminalRecord();
    late.scope.market_as_of = "2025-01-27T16:00:01-05:00";
    expect(() => decodeInvestigation(late)).toThrow(/resolved scope/i);
  });

  it.each(["nvidia/nvidia/nemotron-3-ultra", SOL_MODEL])("accepts capable receipts for current or historical model %s", (model) => {
    const report = escalationReport();
    const attempt = { ...escalationAttempt("capable", 3), configured_model: model, model_assertion: model };
    report.model_attempts.push(attempt);
    Object.assign(report.routing, { returned_model: model, frontier_latched: true, latency_ms: attempt.latency_ms });
    expect(decodeInvestigation(terminalRecord(report, securityForReport(report))).report?.model_attempts.at(-1)?.configured_model).toBe(model);
  });

  it.each(["nvidia/nvidia/nemotron-3-ultra", SOL_MODEL])("accepts current or historical formatting receipt %s", (model) => {
    const report = escalationReport();
    report.model_attempts.push({ ...formatterAttempt(), configured_model: model, model_assertion: model });
    expect(decodeInvestigation(terminalRecord(report, securityForReport(report))).report?.model_attempts.at(-1)?.configured_model).toBe(model);
  });

  it.each(["nvidia/nvidia/nemotron-3-ultra", "unapproved/model"])("rejects wrong judge model %s", (model) => {
    const report = escalationReport();
    Object.assign(report.model_attempts[0], { configured_model: model, model_assertion: model });
    expect(() => decodeInvestigation(terminalRecord(report, securityForReport(report)))).toThrow();
  });

  it("rejects an unknown capable identity even with matching assertion", () => {
    const report = escalationReport(), model = "unapproved/model";
    const attempt = { ...escalationAttempt("capable", 3), configured_model: model, model_assertion: model };
    report.model_attempts.push(attempt);
    Object.assign(report.routing, { returned_model: model, frontier_latched: true, latency_ms: attempt.latency_ms });
    expect(() => decodeInvestigation(terminalRecord(report, securityForReport(report)))).toThrow(/Unapproved model/);
  });

  it("accepts real in-process escalation attempts with actual target models and no trial bundle", () => {
    const report = escalationReport(), decoded = decodeInvestigation(terminalRecord(report, securityForReport(report)));
    expect(decoded.report?.model_attempts.map((attempt) => [attempt.selected_tier, attempt.configured_model])).toEqual([
      ["judge", LUNA_MODEL], ["efficient", LOCAL_MODEL],
    ]);
    expect(decoded.report?.switchyard_trials).toEqual([]);
    expect(decoded.report?.routing.configured_model).toBe("switchyard/escalation");
  });

  it("accepts one adjacent same-target remote transport retry for a logical request", () => {
    const { report, failed, success } = escalationReportWithTransportRetry();
    const decoded = decodeInvestigation(terminalRecord(report, securityForReport(report)));

    expect(decoded.report?.model_attempts.slice(-2).map((attempt) => attempt.application_call_id)).toEqual([
      failed.application_call_id,
      success.application_call_id,
    ]);
    expect(decoded.report?.model_attempts.slice(-2).map((attempt) => attempt.application_request_id)).toEqual([
      success.application_request_id,
      success.application_request_id,
    ]);
  });

  it.each(["wrong_failure", "nonzero_tokens", "wrong_target", "different_request", "same_call", "nonadjacent", "third_attempt", "second_failure", "local_retry", "direct_retry"])("rejects invalid model transport retry shape: %s", (caseName) => {
    const { report, failed, success } = escalationReportWithTransportRetry();
    if (caseName === "wrong_failure") failed.failure_class = "timeout";
    if (caseName === "nonzero_tokens") failed.tokens = { prompt: 1, completion: 0, total: 1 };
    if (caseName === "wrong_target") success.role = "routing_judge";
    if (caseName === "different_request") success.application_request_id = "request-escalation-other";
    if (caseName === "same_call") failed.application_call_id = success.application_call_id;
    if (caseName === "nonadjacent") report.model_attempts.splice(-1, 0, escalationAttempt("judge", 4));
    if (caseName === "third_attempt") report.model_attempts.push({ ...success, application_call_id: "call-escalation-third" });
    if (caseName === "second_failure") Object.assign(success, { model_assertion: null, identity_evidence: "unavailable", state: "failed", failure_class: "transport_error", tokens: { prompt: 0, completion: 0, total: 0 }, validation_status: "invalid" });
    if (caseName === "local_retry") {
      const local = escalationAttempt("efficient", 5), localFailure: ModelAttempt = { ...local, model_assertion: null, identity_evidence: "unavailable", state: "failed", failure_class: "transport_error", application_call_id: "call-local-first", tokens: { prompt: 0, completion: 0, total: 0 }, validation_status: "invalid" };
      local.application_request_id = localFailure.application_request_id;
      report.model_attempts.splice(-2, 2, localFailure, local);
    }
    if (caseName === "direct_retry") {
      const direct: ModelAttempt = { ...success, role: "answer_synthesis", algorithm: "direct_frontier", selected_tier: null };
      const directFailure: ModelAttempt = { ...failed, role: "answer_synthesis", algorithm: "direct_frontier", selected_tier: null };
      report.model_attempts.splice(-2, 2, directFailure, direct);
    }

    expect(() => decodeInvestigation(terminalRecord(report, securityForReport(report)))).toThrow(/transport retry|duplicate attempt|model answer|security receipt/i);
  });

  it.each([false, true])("accepts a terminal cosmetic formatter attempt (failed=%s) without changing routing", (failed) => {
    const report = escalationReport(), before = { ...report.routing };
    report.model_attempts = [...report.model_attempts, formatterAttempt(failed)];
    const decoded = decodeInvestigation(terminalRecord(report, securityForReport(report)));

    expect(decoded.report?.model_attempts.at(-1)?.role).toBe("report_formatting");
    expect(decoded.report?.model_attempts.at(-1)?.state).toBe(failed ? "failed" : "succeeded");
    expect(decoded.report?.routing).toEqual(before);
  });

  it("rejects a non-terminal cosmetic formatter attempt", () => {
    const report = escalationReport();
    report.model_attempts = [...report.model_attempts, {
      ...formatterAttempt(false), state: "attempted", failure_class: null,
      model_assertion: null, identity_evidence: "unavailable", validation_status: "not_run",
    } as ModelAttempt];
    expect(() => decodeInvestigation(terminalRecord(report, securityForReport(report)))).toThrow(/identity mismatch|report formatting attempt/i);
  });

  it("rejects an escalation tier paired with the wrong approved model", () => {
    const report = escalationReport();
    report.model_attempts[0] = {
      ...report.model_attempts[0], configured_model: SOL_MODEL, model_assertion: SOL_MODEL,
    };
    expect(() => decodeInvestigation(terminalRecord(report, securityForReport(report)))).toThrow(/escalation attempt/i);
  });

  it("accepts direct provider-verified identity and no capability trial", () => {
    const report = directReport(), receipt = securityForReport(report);
    const decoded = decodeInvestigation(terminalRecord(report, receipt));
    expect(decoded.report?.answer_mode).toBe("model_synthesis");
    expect(decoded.report?.model_attempts[0].identity_evidence).toBe("direct_provider_verified");
    expect(decoded.report?.switchyard_trials).toEqual([]);
  });

  it("accepts and correlates every stage in a routed deep-agent sequence", () => {
    const report = fullyRoutedReport(), decoded = decodeInvestigation(terminalRecord(report, securityForReport(report)));
    expect(decoded.report?.model_attempts.map((attempt) => attempt.role)).toEqual(["skill_selection", "investigation_planning", "evidence_review", "answer_synthesis"]);
    expect(decoded.report?.switchyard_trials.map((trial) => trial.trial_id)).toEqual(report.model_attempts.map((attempt) => attempt.switchyard_trial_id));
    expect(decoded.security_receipts[0].network_egress.map((observation) => observation.call_id)).toEqual(report.model_attempts.map((attempt) => attempt.application_call_id));
    expect(decoded.report?.routing.latency_ms).toBe(report.model_attempts.at(-1)?.latency_ms);
  });

  it("rejects malformed deep-agent stage order and per-stage correlation gaps", () => {
    const ordered = fullyRoutedReport(), wrongRole = terminalRecord(ordered, securityForReport(ordered));
    wrongRole.report!.model_attempts[1].role = "evidence_review";
    expect(() => decodeInvestigation(wrongRole)).toThrow(/deep-agent model attempt sequence/i);

    const missingEventReport = fullyRoutedReport(), missingEvent = terminalRecord(missingEventReport, securityForReport(missingEventReport));
    missingEvent.events.splice(2, 1); missingEvent.events.forEach((item, index) => { item.sequence = index + 1; });
    expect(() => decodeInvestigation(missingEvent)).toThrow(/historical model attempt correlation/i);

    const missingSecurityReport = fullyRoutedReport(), missingSecurity = securityForReport(missingSecurityReport); missingSecurity.network_egress.splice(1, 1);
    expect(() => decodeInvestigation(terminalRecord(missingSecurityReport, missingSecurity))).toThrow(/model security correlation/i);
  });

  it("keeps a hard refusal model-free", () => {
    const refusalScope: Report["scope"] = { ...scope(), status: "refused", action: "refuse", ticker: null, resolved_tickers: [] };
    const report = { ...baseReport(), scope: refusalScope, answer_mode: "deterministic_policy" as const, title: "Request refused" }, value = terminalRecord(report);
    value.status = "refused"; value.events.at(-1)!.payload.terminal = "refusal";
    const decoded = decodeInvestigation(value);
    expect(decoded.report?.model_attempts).toEqual([]);
    expect(decoded.security_receipts[0].network_egress).toEqual([]);
  });

  it("requires closed ordered no-data reasons exactly on a readable no-data terminal", () => {
    const report = { ...baseReport(), no_data_reasons: ["missing_news" as const, "missing_company_release" as const] }, value = terminalRecord(report); value.events[1].payload.terminal = "no_data";
    expect(decodeInvestigation(value).report?.no_data_reasons).toEqual(["missing_news", "missing_company_release"]);
    expect(() => decodeInvestigation({ ...value, report: { ...report, no_data_reasons: [] }, events: [value.events[0], { ...value.events[1], payload: { ...value.events[1].payload, report: { ...report, no_data_reasons: [] } } }] })).toThrow(/no-data reason/i);
    expect(() => decodeInvestigation(terminalRecord(report))).toThrow(/no-data reason/i);
    expect(() => decodeInvestigation(terminalRecord({ ...baseReport(), no_data_reasons: ["missing_company_release", "missing_news"] }))).toThrow(/no-data reasons/i);
  });

  it("accepts a routed target assertion with classified variable trial rows", () => {
    const trial = classifiedTrial(), report = routedReport(trial), receipt = securityForReport(report);
    const decoded = decodeInvestigation(terminalRecord(report, receipt));
    expect(decoded.report?.model_attempts.at(-1)?.identity_evidence).toBe("switchyard_target_asserted");
    expect(decoded.report?.switchyard_trials[0].classifier_row?.model).toBe(LUNA_MODEL);
    expect(decoded.report?.switchyard_trials[0].terminal_row?.tier).toBe("strong");
    expect(decoded.report?.routing.returned_model).toBeNull();
  });

  it("accepts official ambiguous target fallthrough while prior-target details stay unavailable", () => {
    const trial = fallthroughTrial(), report = routedReport(trial), receipt = securityForReport(report);
    const decoded = decodeInvestigation(terminalRecord(report, receipt));
    expect(decoded.report?.switchyard_trials[0].classifier_validity).toBe("ambiguous_fallthrough");
    expect(decoded.report?.switchyard_trials[0].prior_target_error).toBe("unavailable");
    expect(decoded.security_receipts[0].network_egress.at(-1)?.unavailable_fields).toEqual(["provider_identity", "prior_target_model", "prior_target_error", "prior_target_row"]);
  });

  it("accepts an ambiguous selection without inventing an official fallthrough reason", () => {
    const trial = ambiguousTrial(), report = routedReport(trial), receipt = securityForReport(report);
    const decoded = decodeInvestigation(terminalRecord(report, receipt));
    expect(decoded.report?.switchyard_trials[0].fallback_reason).toBeNull();
    expect(decoded.security_receipts[0].network_egress.at(-1)?.unavailable_fields).toEqual(["provider_identity"]);
  });

  it("accepts an unavailable terminal trial on a typed provider failure", () => {
    const trial = unavailableTrial();
    const attempt = { ...routedAttempt(trial), model_assertion: null, identity_evidence: "unavailable", state: "failed", failure_class: "provider_error", validation_status: "not_run" } as ModelAttempt;
    const receipt = securityForAttempt(attempt, trial), record = typedFailureRecord("route_failure:provider_error", receipt);
    record.events.splice(1, 0, event(2, "routing", { attempt, switchyard_trials: [trial] })); record.events[2].sequence = 3;
    expect(((decodeInvestigation(record).events[1].payload.switchyard_trials as SwitchyardTrialBundle[])[0]).classifier_validity).toBe("unavailable");
  });

  it("accepts a failed terminal attempt and its trial only as a typed failed record", () => {
    const trial = classifiedTrial();
    const attempt = { ...routedAttempt(trial), state: "failed", failure_class: "transport_contract", validation_status: "invalid" } as ModelAttempt;
    const receipt = securityForAttempt(attempt, trial), record = typedFailureRecord("route_failure:transport_contract", receipt);
    record.events.splice(1, 0, event(2, "routing", { attempt, switchyard_trials: [trial] }));
    record.events[2].sequence = 3;
    const decoded = decodeInvestigation(record);
    expect(decoded.status).toBe("failed");
    expect((decoded.events[1].payload.attempt as ModelAttempt).state).toBe("failed");
    expect(parseFailure(decoded.error)).toEqual({ stage: "route_failure", code: "transport_contract" });
  });

  it("accepts and renders a typed terminal after both sanctioned transport attempts fail", () => {
    const { failed, success } = escalationReportWithTransportRetry();
    const retryFailed: ModelAttempt = {
      ...failed,
      application_call_id: success.application_call_id,
      latency_ms: 8,
    };
    const receipt: SecurityReceipt = {
      ...verifiedSecurity(),
      network_egress: [networkForAttempt(failed), networkForAttempt(retryFailed)] as SecurityReceipt["network_egress"],
    };
    const record = typedFailureRecord("route_failure:transport_error", receipt);
    record.events.splice(1, 0,
      event(2, "routing", { attempt: failed, switchyard_trials: [] }),
      event(3, "routing", { attempt: retryFailed, switchyard_trials: [] }),
    );
    record.events[3].sequence = 4;

    const decoded = decodeInvestigation(record), html = renderToStaticMarkup(createElement(ConversationHistory, { record: decoded, active: false }));
    expect(decoded.status).toBe("failed");
    expect(parseFailure(decoded.error)).toEqual({ stage: "route_failure", code: "transport_error" });
    expect(html).toContain('data-terminal-outcome="route_failure"');
    expect(html).toContain("route_failure:transport_error");
  });

  it("renders a synthesis validation failure after successful model transport", () => {
    const attempt = { ...directAttempt(), state: "failed", failure_class: "invalid_evidence", validation_status: "invalid" } as ModelAttempt;
    const receipt = securityForAttempt(attempt), record = typedFailureRecord("synthesis_failure:invalid_evidence", receipt);
    record.request = request("local_only");
    record.events.splice(1, 0, event(2, "routing", { attempt, switchyard_trials: [] })); record.events[2].sequence = 3;
    const decoded = decodeInvestigation(record), html = renderToStaticMarkup(createElement(ConversationHistory, { record: decoded, active: false }));
    expect(decoded.security_receipts[0].network_egress[0].outcome).toBe("succeeded");
    expect(html).toContain('data-terminal-outcome="synthesis_failure"');
    expect(html).toContain("synthesis_failure:invalid_evidence");
  });

  it("also accepts an explicit unavailable identity on a failed model event", () => {
    const trial = classifiedTrial();
    const attempt = { ...routedAttempt(trial), model_assertion: null, identity_evidence: "unavailable", state: "failed", failure_class: "transport_contract", validation_status: "invalid" } as ModelAttempt;
    const receipt = securityForAttempt(attempt, trial), record = typedFailureRecord("route_failure:transport_contract", receipt);
    record.events.splice(1, 0, event(2, "routing", { attempt, switchyard_trials: [trial] })); record.events[2].sequence = 3;
    expect(decodeInvestigation(record).events[1].payload.attempt).toEqual(attempt);
  });

  it("rejects mismatched failure identity pairs and asserted in-progress attempts", () => {
    const trial = classifiedTrial(), mutations = [
      { state: "failed" as const, failure_class: "transport_contract" as const, validation_status: "invalid" as const, model_assertion: SOL_MODEL, identity_evidence: "unavailable" as const },
      { state: "failed" as const, failure_class: "transport_contract" as const, validation_status: "invalid" as const, model_assertion: null, identity_evidence: "switchyard_target_asserted" as const },
      { state: "attempted" as const, failure_class: null, validation_status: "not_run" as const, model_assertion: SOL_MODEL, identity_evidence: "switchyard_target_asserted" as const },
    ];
    for (const mutation of mutations) {
      const attempt = { ...routedAttempt(trial), ...mutation }, receipt = securityForAttempt(attempt as ModelAttempt, trial), record = typedFailureRecord("route_failure:transport_contract", receipt);
      record.events.splice(1, 0, event(2, "routing", { attempt, switchyard_trials: [trial] })); record.events[2].sequence = 3;
      expect(() => decodeInvestigation(record)).toThrow(/model identity/i);
    }
  });

  it("rejects duplicate, orphaned, and mismatched model security observations", () => {
    const trial = classifiedTrial(), attempt = { ...routedAttempt(trial), state: "failed", failure_class: "transport_contract", validation_status: "invalid" } as ModelAttempt;
    const build = () => { const receipt = securityForAttempt(attempt, trial), record = typedFailureRecord("route_failure:transport_contract", receipt); record.events.splice(1, 0, event(2, "routing", { attempt, switchyard_trials: [trial] })); record.events[2].sequence = 3; return { receipt, record }; };
    const duplicate = build(); duplicate.receipt.network_egress.push({ ...duplicate.receipt.network_egress[0], observation_id: "network-duplicate-01" }); expect(() => decodeInvestigation(duplicate.record)).toThrow(/model security correlation/i);
    const mismatch = build(); mismatch.receipt.network_egress[0].application_request_id = "request-mismatch-01"; expect(() => decodeInvestigation(mismatch.record)).toThrow(/model security correlation/i);
    const orphanReceipt = verifiedSecurity(); orphanReceipt.network_egress = [networkForAttempt(directAttempt())] as SecurityReceipt["network_egress"]; expect(() => decodeInvestigation(terminalRecord(baseReport(), orphanReceipt))).toThrow(/orphan model security/i);
  });

  it.each(["created", "running", "completed", "needs_input", "refused", "failed", "cancelled"] as const)("accepts the %s public status with its required terminal shape", (status) => {
    if (status === "created" || status === "running") {
      const value = terminalRecord();
      Object.assign(value, { status, report: null, security_receipts: [], events: [event(1, "scope")] });
      expect(decodeInvestigation(value).status).toBe(status);
      return;
    }
    if (status === "failed") {
      expect(decodeInvestigation(typedFailureRecord()).status).toBe(status);
      return;
    }
    if (status === "cancelled") {
      const receipt = verifiedSecurity(), value = typedFailureRecord("route_failure:transport_contract", receipt);
      Object.assign(value, { status, error: null, events: [event(1, "scope"), event(2, "cancelled", { turn_id: receipt.turn_id, terminal: "cancelled", security_receipt: receipt })] });
      expect(decodeInvestigation(value).status).toBe(status);
      return;
    }
    const value = terminalRecord();
    value.status = status;
    value.events[1].payload.terminal = status === "needs_input" ? "clarification" : status === "refused" ? "refusal" : "success";
    expect(decodeInvestigation(value).status).toBe(status);
  });

  it("rejects missing, unknown, or violated security on a clean terminal", () => {
    const missing = terminalRecord(); missing.security_receipts = []; delete missing.events[1].payload.security_receipt;
    expect(() => decodeInvestigation(missing)).toThrow(/security receipt/i);
    for (const receipt of [unknownSecurity(), violatedSecurity()]) {
      const value = terminalRecord(baseReport(), receipt);
      expect(() => decodeInvestigation(value)).toThrow(/security/i);
    }
  });

  it("rejects a terminal event bound to a different turn receipt", () => {
    const value = terminalRecord();
    value.events[1].payload.turn_id = "turn-0002";
    expect(() => decodeInvestigation(value)).toThrow(/mismatched/i);

    const historical = multiTurnRecord();
    historical.security_receipts[0] = verifiedSecurity("turn-0099");
    expect(() => decodeInvestigation(historical)).toThrow(/historical security receipt correlation/i);

    const wrongEventType = multiTurnRecord();
    wrongEventType.events[1].event_type = "error";
    expect(() => decodeInvestigation(wrongEventType)).toThrow(/event contract/i);
  });

  it("accepts explicit unknown and violation receipts only on typed failed records", () => {
    expect(decodeInvestigation(typedFailureRecord("security_unknown:security_receipt_unknown", unknownSecurity())).security_receipts[0].completeness).toBe("unknown");
    expect(decodeInvestigation(typedFailureRecord("security_violation:secret_exposure", violatedSecurity())).security_receipts[0].completeness).toBe("violation");

    const attempt = { ...routedAttempt(), model_assertion: null, identity_evidence: "unavailable", state: "failed", failure_class: "transport_contract", validation_status: "not_run" } as ModelAttempt;
    const observation = {
      ...networkForAttempt(attempt), decision: "allowed", attempt: "not_attempted", outcome: "blocked",
      call_id: null, application_request_id: null, switchyard_trial_id: null, identity_evidence: null,
      switchyard_trial_bundle: null, unavailable_fields: [], unavailable_provenance: null,
      limitation: "Switchyard audit correlation is unavailable.",
    } as SecurityReceipt["network_egress"][number];
    const receipt = {
      ...verifiedSecurity(), completeness: "violation", trust_boundary: "violation", network_egress: [observation],
      violations: [{ schema_version: "security-observation-v1", boundary: "switchyard", code: "correlation_mismatch", observation_id: observation.observation_id, call_id: null, limitation: "Switchyard preflight correlation failed." }],
    } as SecurityReceipt;
    const record = typedFailureRecord("security_violation:correlation_mismatch", receipt);
    record.events.splice(1, 0, event(2, "routing", { attempt, switchyard_trials: [] })); record.events[2].sequence = 3;
    const decoded = decodeInvestigation(record);
    expect(decoded.security_receipts[0].network_egress[0]).toMatchObject({ decision: "allowed", attempt: "not_attempted", outcome: "blocked" });
    expect(decoded.security_receipts[0].completeness).toBe("violation");
  });

  it("binds typed failure detail and terminal stage to the exact security state", () => {
    const detailMismatch = typedFailureRecord(); detailMismatch.events.at(-1)!.detail = "route_failure:other";
    expect(() => decodeInvestigation(detailMismatch)).toThrow(/terminal status mapping/i);

    const nonSecurity = typedFailureRecord("route_failure:transport_contract", unknownSecurity());
    expect(() => decodeInvestigation(nonSecurity)).toThrow(/security state/i);

    const falselyVerified = typedFailureRecord("security_unknown:secret_scan_error", verifiedSecurity());
    expect(() => decodeInvestigation(falselyVerified)).toThrow(/security state/i);
  });

  it("rejects stale singular routing and active compatibility state", () => {
    const staleReport = directReport(), stale = terminalRecord(staleReport, securityForReport(staleReport));
    delete (stale.report as unknown as Record<string, unknown>).model_attempts;
    expect(() => decodeInvestigation(stale)).toThrow();

    const invented = terminalRecord();
    (invented.report!.routing as unknown as Record<string, unknown>).selected_by = "trajectory";
    expect(() => decodeInvestigation(invented)).toThrow(/routing projection/i);

    const active = terminalRecord();
    (active.report!.routing as unknown as Record<string, unknown>)[inertProjectionKey] = true;
    expect(() => decodeInvestigation(active)).toThrow(/route projection/i);

    const mismatchReport = directReport(), mismatched = terminalRecord(mismatchReport, securityForReport(mismatchReport));
    mismatched.report!.routing.latency_ms += 1;
    expect(() => decodeInvestigation(mismatched)).toThrow(/projection disagrees/i);
  });

  it("rejects incomplete, inconsistent, or invented trial and security fields", () => {
    const inconsistentTrial = classifiedTrial(); inconsistentTrial.classifier_validity = "unavailable";
    const inconsistentReport = routedReport(inconsistentTrial), routed = terminalRecord(inconsistentReport, securityForReport(inconsistentReport));
    expect(() => decodeInvestigation(routed)).toThrow(/trial bundle/i);

    const inventedPrior = ambiguousTrial(); (inventedPrior as unknown as Record<string, unknown>).prior_target_model = SOL_MODEL;
    const inventedReport = routedReport(inventedPrior);
    expect(() => decodeInvestigation(terminalRecord(inventedReport, securityForReport(inventedReport)))).toThrow(/prior target/i);

    const unknownReceipt = terminalRecord();
    (unknownReceipt.security_receipts[0] as unknown as Record<string, unknown>).raw_endpoint = "not allowed";
    expect(() => decodeInvestigation(unknownReceipt)).toThrow(/security receipt fields/i);

    const directModelReport = directReport(), direct = directModelReport.model_attempts[0], wrongCorrelation = securityForReport(directModelReport);
    wrongCorrelation.network_egress[0].call_id = "call-99999999";
    expect(() => decodeInvestigation(terminalRecord(directModelReport, wrongCorrelation))).toThrow(/model security correlation/i);

    const unknownBlocked = verifiedSecurity();
    unknownBlocked.completeness = "unknown"; unknownBlocked.trust_boundary = "unknown"; unknownBlocked.unknown_reasons = ["network_observation_unfinished"];
    unknownBlocked.network_egress = [{ ...networkForAttempt(direct), decision: "unknown", attempt: "not_attempted", outcome: "blocked", call_id: null, identity_evidence: null, limitation: "The call was not attempted." }] as SecurityReceipt["network_egress"];
    expect(() => decodeInvestigation(terminalRecord(baseReport(), unknownBlocked))).toThrow(/network observation/i);
  });

  it("rejects terminal history beyond turns, incomplete terminal history, and duplicate routing within one turn", () => {
    const extraTerminal = multiTurnRecord();
    extraTerminal.turns = [extraTerminal.turns[0]];
    expect(() => decodeInvestigation(extraTerminal)).toThrow(/terminal turn history/i);

    const incompleteTerminal = terminalRecord();
    incompleteTerminal.turns.push("A follow-up without its terminal event.");
    expect(() => decodeInvestigation(incompleteTerminal)).toThrow(/terminal turn history/i);

    const duplicateReport = directReport(), duplicateRouting = terminalRecord(duplicateReport, securityForReport(duplicateReport));
    duplicateRouting.events.splice(2, 0, { ...duplicateRouting.events[1], sequence: 3 });
    duplicateRouting.events.forEach((item, index) => { item.sequence = index + 1; });
    expect(() => decodeInvestigation(duplicateRouting)).toThrow(/duplicate model attempt/i);
  });

  it("rejects unknown top-level, nested, event, and typed-failure values", () => {
    for (const mutate of [
      (value: ReturnType<typeof terminalRecord>) => Object.assign(value, { surprise: true }),
      (value: ReturnType<typeof terminalRecord>) => Object.assign(value.request, { surprise: true }),
      (value: ReturnType<typeof terminalRecord>) => Object.assign(value.events[0], { surprise: true }),
      (value: ReturnType<typeof terminalRecord>) => { value.events[0].event_type = "invented" as TrajectoryEvent["event_type"]; },
      (value: ReturnType<typeof terminalRecord>) => { value.events[1].sequence = 4; },
    ]) {
      const value = terminalRecord(); mutate(value); expect(() => decodeInvestigation(value)).toThrow();
    }
    expect(() => decodeInvestigation({ status: "running", events: [] })).toThrow();
    expect(() => parseFailure("timeout")).toThrow(/typed failure/i);
  });
});

describe("client and reducer boundaries", () => {
  it("deduplicates replayed SSE events", () => {
    const one = reducer(initialState, { type: "event", event: event(1) });
    expect(reducer(one, { type: "event", event: event(1) }).events).toHaveLength(1);
  });

  it("detects a sequence gap and requests reconciliation", () => {
    const value = reducer(initialState, { type: "event", event: event(2) });
    expect(value.connection).toBe("reconnecting"); expect(value.error).toContain("gap");
  });

  it("URL-encodes record IDs and resumes events after the last sequence", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => terminalRecord() })
      .mockResolvedValueOnce({ ok: true, text: async () => "" });
    vi.stubGlobal("fetch", fetch);
    await getInvestigation("a/b"); await readEvents("a/b", 7, new AbortController().signal);
    expect(fetch.mock.calls[0][0]).toBe("/api/investigations/a%2Fb");
    expect(fetch.mock.calls[1][0]).toBe("/api/investigations/a%2Fb/events?after=7");
  });

  it("sends the exact event identity and canonical scope when creating event research", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => terminalRecord() });
    vi.stubGlobal("fetch", fetch);
    await createInvestigation({
      event_id: "nvda-deepseek-2025-01-27",
      ticker: "NVDA",
      as_of: "2025-01-27T21:00:00Z",
      question: "Which historical analogues are most similar to this event?",
      route_mode: "switchyard_escalation",
    });
    expect(fetch.mock.calls[0][0]).toBe("/api/investigations");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      event_id: "nvda-deepseek-2025-01-27",
      ticker: "NVDA",
      as_of: "2025-01-27T21:00:00Z",
      question: "Which historical analogues are most similar to this event?",
      route_mode: "switchyard_escalation",
    });
  });

  it("strictly decodes incremental event-stream records", async () => {
    const invalid = { ...event(1), event_type: "invented" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, text: async () => `data: ${JSON.stringify(invalid)}\n\n` }));
    await expect(readEvents(investigationId, 0, new AbortController().signal)).rejects.toThrow(/event type/i);
  });

  it("decodes every streamed model-stage event in order", async () => {
    const report = fullyRoutedReport(), events = terminalRecord(report, securityForReport(report)).events.filter((item) => item.event_type === "routing");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, text: async () => events.map((item) => `data: ${JSON.stringify(item)}\n\n`).join("") }));
    const decoded = await readEvents(investigationId, 0, new AbortController().signal);
    expect(decoded.map((item) => (item.payload.attempt as ModelAttempt).role)).toEqual(["skill_selection", "investigation_planning", "evidence_review", "answer_synthesis"]);
  });
});

describe("truthful presentation", () => {
  it("does not expose compatibility route choices in the fixed-route launcher", () => {
    const html = renderToStaticMarkup(createElement(InvestigationForm, { busy: false, onSubmit: () => undefined }));
    expect(html).not.toContain("Per-request capability routing");
    expect(html).not.toContain("Model route");
    expect(html).not.toContain("Governed");
  });

  it("keeps operational model identity out of the reader-facing report", () => {
    const html = renderToStaticMarkup(createElement(ReportView, { report: routedReport(fallthroughTrial()) }));
    expect(html).toContain('data-answer-mode="model_synthesis"');
    expect(html).toContain('data-route-mode="switchyard_escalation"');
    expect(html).not.toContain("Model synthesis");
    expect(html).not.toContain("Switchyard target asserted");
    expect(html).not.toContain("Provider identity unavailable");
    expect(html).not.toContain("Switchyard-managed fallthrough");
    expect(html).not.toContain("Prior target model and error unavailable");
    expect(html).not.toContain("No application retry");
    expect(html).not.toContain('class="answer-provenance-summary"');
    expect(html).not.toContain('class="report-model-attempt"');
    expect(html).not.toContain('class="report-switchyard-trial"');
  });

  it("renders every question with its own historical terminal report", () => {
    const html = renderToStaticMarkup(createElement(ConversationHistory, { record: multiTurnRecord(), active: false }));
    const first = html.indexOf('data-turn-id="turn-0001"'), second = html.indexOf('data-turn-id="turn-0002"');
    expect(first).toBeGreaterThan(-1); expect(second).toBeGreaterThan(first);
    expect(html.slice(first, second)).toContain("What moved NVDA on the selected date?");
    expect(html.slice(first, second)).toContain("First-turn answer");
    expect(html.slice(second)).toContain("How did its benchmark compare?");
    expect(html.slice(second)).toContain("Second-turn answer");
    expect((html.match(/data-turn-id=/g) ?? [])).toHaveLength(2);
  });

  it("keeps unclaimed citation inventory visible and machine-addressable", () => {
    const linked = citation("citation-00000001", "Linked source"), additional = citation("citation-00000002", "Additional source");
    const report = { ...baseReport(), claims: [{ claim_id: "claim-00000001", text: "Bounded finding.", kind: "fact" as const, confidence: 0.9, citation_ids: [linked.citation_id] }], citations: [linked, additional] };
    const html = renderToStaticMarkup(createElement(ReportView, { report }));
    expect(html).toContain('data-citation-id="citation-00000001"');
    expect(html).toContain('aria-label="Additional report sources"');
    expect(html).toContain('data-citation-id="citation-00000002"');
    expect(html).toContain("Additional evidence retained in the report inventory.");
  });

  it("projects a pending follow-up only while its watch is active", () => {
    const record = multiTurnRecord(), question = "What remains uncertain?", pending = { ordinal: 3, question };
    const projected = renderToStaticMarkup(createElement(ConversationHistory, { record, active: true, pending: projectedPendingTurn(record, pending, "connecting") }));
    expect((projected.match(/data-turn-id=/g) ?? [])).toHaveLength(3);
    expect((projected.match(new RegExp(question.replace("?", "\\?"), "g")) ?? [])).toHaveLength(1);
    const persisted = { ...record, status: "running", request: { ...record.request, question }, turns: [...record.turns, question], report: null } as Investigation;
    const hydrated = renderToStaticMarkup(createElement(ConversationHistory, { record: persisted, active: true, pending: projectedPendingTurn(persisted, pending, "live") }));
    expect((hydrated.match(/data-turn-id=/g) ?? [])).toHaveLength(3);
    expect((hydrated.match(new RegExp(question.replace("?", "\\?"), "g")) ?? [])).toHaveLength(1);
    const failed = renderToStaticMarkup(createElement(ConversationHistory, { record, active: false, pending: projectedPendingTurn(record, pending, "closed") }));
    expect((failed.match(/data-turn-id=/g) ?? [])).toHaveLength(2);
    expect(failed).not.toContain(question);
  });

  it("retains an exact inspectable projection beside a topic visualization", () => {
    const artifact: Artifact = { artifact_id: "artifact-topic-01", kind: "topic_projection", title: "Evidence topics", data: { dimensions: 3, points: [{ evidence_id: "evidence-01", coordinates: [1, 2, 9] }] } };
    const html = renderToStaticMarkup(createElement(ArtifactView, { artifact }));
    expect(html).toContain('data-artifact-id="artifact-topic-01"');
    expect(html).toContain('data-artifact-kind="topic_projection"');
    expect(html).toContain("Inspect exact artifact data");
    const encoded = renderToStaticMarkup(createElement("pre", null, JSON.stringify(artifact.data, null, 2))).slice(5, -6);
    expect(html).toContain(encoded);
    expect(html).toContain("evidence-01");
  });

  it("renders typed failures and verified, unknown, violated, and absent security distinctly", () => {
    const receipts = [verifiedSecurity(), unknownSecurity(), violatedSecurity()];
    const events = receipts.map((receipt, index) => event(index + 1, index === 0 ? "report" : "error", { turn_id: receipt.turn_id, terminal: index === 0 ? "success" : index === 1 ? "security_unknown" : "security_violation", security_receipt: receipt }));
    events.push(event(4, "error", { turn_id: "turn-0004", terminal: "route_failure" }));
    events.push(event(5, "error", { turn_id: "turn-0005", tool: "market_snapshot", outcome: "failed" }));
    events.push(event(6, "error", { turn_id: "turn-0006", terminal: null, tool: "market_snapshot", outcome: "failed" }));
    const html = renderToStaticMarkup(createElement(Trajectory, { events }));
    expect(html).toContain("Security verified"); expect(html).toContain("Security telemetry unknown");
    expect(html).toContain("Security violation recorded"); expect(html).toContain("Security telemetry missing");
    expect((html.match(/Security telemetry missing/g) ?? [])).toHaveLength(1);
    expect(html).toContain("See the answer pane for recovery guidance");
    expect(html).not.toContain("route failure");
  });

  it("renders exact failed-attempt provenance without obsolete trial internals", () => {
    const trial = classifiedTrial();
    const attempt = { ...routedAttempt(trial), state: "failed", failure_class: "transport_contract", validation_status: "invalid" } as ModelAttempt;
    const html = renderToStaticMarkup(createElement(Trajectory, { events: [event(1, "routing", { attempt, switchyard_trials: [trial] })] }));
    expect(html).toContain('data-event-sequence="1"');
    expect(html).toContain('data-event-type="routing"');
    expect(html).toContain(`data-application-call-id="${attempt.application_call_id}"`);
    expect(html).toContain(`data-application-request-id="${attempt.application_request_id}"`);
    expect((html.match(new RegExp(`data-switchyard-trial-id="${trial.trial_id}"`, "g")) ?? [])).toHaveLength(1);
    expect(html).toContain(`Switchyard target asserted: ${attempt.model_assertion}. Provider identity unavailable.`);
    expect(html).toContain(`Call ${attempt.application_call_id}; request ${attempt.application_request_id}. Typed failure: transport contract.`);
    expect(html).not.toContain(`Trial ${trial.trial_id}.`);
  });

  it("renders the bounded external-action disposition without its limitation", () => {
    const receipt = verifiedSecurity();
    receipt.external_actions = [{
      schema_version: "security-observation-v1", observation_id: "action-credential-01", action_class: "credential_access",
      request_state: "requested", decision: "denied", attempt: "not_attempted", outcome: "blocked", limitation: "Sensitive policy detail that must remain hidden.",
    }];
    const html = renderToStaticMarkup(createElement(Trajectory, { events: [event(1, "report", { turn_id: receipt.turn_id, terminal: "refusal", security_receipt: receipt })] }));
    expect(html).toContain("External action: credential_access · requested · denied · not_attempted · blocked");
    expect(html).not.toContain("Sensitive policy detail");
  });
});
