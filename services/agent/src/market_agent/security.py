"""Fail-closed, per-turn security observation recorder."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, UTC
from uuid import uuid4

from pydantic import BaseModel, SecretStr

from .schemas import (
    ExternalActionObservation,
    NetworkEgressObservation,
    SecretScanObservation,
    SecurityReceipt,
    TrustBoundaryViolation,
)

_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")
_SENSITIVE = re.compile(r"(?i)(?:://|authorization|bearer\s|(?:token|key|secret)=)")
BOUNDARY_DESTINATIONS = {
    "mcp": "internal_tools",
    "local_model": "local_model",
    "frontier_model": "internal_inference",
    "switchyard": "loopback_switchyard",
}


def _identifier(value: object, name: str) -> str:
    rendered = str(value)
    return (
        rendered
        if _ID.fullmatch(rendered)
        else (_ for _ in ()).throw(ValueError(f"stable {name} is required"))
    )


def _safe(value: str | None) -> str | None:
    return (
        value
        if not value or not _SENSITIVE.search(value)
        else (_ for _ in ()).throw(ValueError("sensitive detail is forbidden"))
    )


class SecurityRecorder:
    def __init__(
        self, investigation_id: object, turn_id: object, secrets_by_name: Mapping[str, object] | None = None
    ):
        self.investigation_id = _identifier(investigation_id, "investigation_id")
        self.turn_id = _identifier(turn_id, "turn_id")
        self._network: dict[str, NetworkEgressObservation] = {}
        self._violations: list[TrustBoundaryViolation] = []
        self._unknown_reasons: set[str] = set()
        self._receipt = None
        self._actions = [
            ExternalActionObservation(
                observation_id=self._new("action"),
                action_class="none",
                request_state="unknown",
                decision="unknown",
                attempt="unknown",
                outcome="unknown",
                limitation="Policy action disposition was not observed.",
            )
        ]
        self._secrets: dict[str, str] = {}
        self._secret_error = False
        try:
            for name, value in dict(secrets_by_name or {}).items():
                if not _ENV.fullmatch(name):
                    raise ValueError("invalid secret variable name")
                raw = value.get_secret_value() if isinstance(value, SecretStr) else value
                if raw is not None and not isinstance(raw, str):
                    raise TypeError("secret value must be text")
                if raw:
                    self._secrets[name] = raw
        except Exception:
            self._secret_error = True
            self._secrets = {}

    @staticmethod
    def _new(prefix: str) -> str:
        return f"{prefix}-{uuid4().hex}"

    def _open(self) -> None:
        return (
            None
            if self._receipt is None
            else (_ for _ in ()).throw(RuntimeError("security recorder is finalized"))
        )

    def record_policy(
        self,
        *,
        action_class: str = "none",
        request_state: str = "not_requested",
        decision: str = "allowed",
        attempt: str = "not_attempted",
        outcome: str = "succeeded",
        limitation: str | None = None,
    ) -> None:
        self._open()
        pending = self._actions[0].request_state == "unknown"
        key = self._actions[0].observation_id if pending else self._new("action")
        try:
            item = ExternalActionObservation(
                observation_id=key,
                action_class=action_class,
                request_state=request_state,
                decision=decision,
                attempt=attempt,
                outcome=outcome,
                limitation=_safe(limitation),
            )
        except (TypeError, ValueError):
            self._violations.append(TrustBoundaryViolation(boundary="policy", code="instrumentation_error"))
            return
        self._actions[0] = item if pending else self._actions[0]
        self._actions.append(item) if not pending else None

    def expect_network(
        self,
        boundary: str,
        destination_class: str,
        *,
        decision: str = "allowed",
        observation_id: str | None = None,
    ) -> str:
        self._open()
        key = _identifier(observation_id or self._new("network"), "observation_id")
        if boundary not in BOUNDARY_DESTINATIONS or BOUNDARY_DESTINATIONS[boundary] != destination_class:
            safe_boundary = boundary if boundary in BOUNDARY_DESTINATIONS else "policy"
            self._violations.append(
                TrustBoundaryViolation(
                    boundary=safe_boundary, code="unapproved_destination", observation_id=key
                )
            )
            return key
        if key in self._network:
            raise ValueError("duplicate network observation")
        self._network[key] = NetworkEgressObservation(
            observation_id=key,
            boundary=boundary,
            destination_class=destination_class,
            decision=decision,
            attempt="unknown",
            outcome="unknown",
            limitation="Network outcome was not observed.",
        )
        return key

    def finish_network(
        self,
        observation_id: str,
        *,
        attempt: str,
        outcome: str,
        call_id: str | None = None,
        application_request_id: str | None = None,
        switchyard_trial_id: str | None = None,
        identity_evidence: str | None = None,
        switchyard_trial_bundle: object | None = None,
        limitation: str | None = None,
    ) -> None:
        self._open()
        current = self._network.get(observation_id)
        if current is None:
            self._unknown_reasons.add("correlation_missing")
            return
        if current.attempt != "unknown":
            self._violations.append(
                TrustBoundaryViolation(
                    boundary=current.boundary,
                    code="application_retry",
                    observation_id=observation_id,
                    call_id=current.call_id,
                )
            )
            return
        values = current.model_dump()
        values.update(
            attempt=attempt,
            outcome=outcome,
            call_id=call_id,
            application_request_id=application_request_id,
            switchyard_trial_id=switchyard_trial_id,
            identity_evidence=identity_evidence,
            switchyard_trial_bundle=switchyard_trial_bundle,
            limitation=_safe(limitation),
        )
        if current.boundary == "switchyard" and attempt == "attempted":
            bundle = switchyard_trial_bundle
            fallback = getattr(bundle, "fallback_reason", None) if bundle is not None else None
            if isinstance(bundle, dict):
                fallback = bundle.get("fallback_reason")
            values.update(
                unavailable_fields=(
                    "provider_identity",
                    "prior_target_model",
                    "prior_target_error",
                    "prior_target_row",
                )
                if fallback
                else ("provider_identity",),
                unavailable_provenance="stock_switchyard_v0.2",
                limitation="Stock Switchyard v0.2 does not expose provider or failed-target detail."
                if fallback
                else "Stock Switchyard v0.2 does not expose provider identity.",
            )
        try:
            self._network[observation_id] = NetworkEgressObservation(**values)
        except (TypeError, ValueError):
            self._unknown_reasons.add(
                "unsupported_trial_bundle" if current.boundary == "switchyard" else "correlation_missing"
            )

    def violation(
        self,
        boundary: str,
        code: str,
        *,
        observation_id: str | None = None,
        call_id: str | None = None,
        limitation: str | None = None,
    ) -> None:
        self._open()
        try:
            item = TrustBoundaryViolation(
                boundary=boundary,
                code=code,
                observation_id=observation_id,
                call_id=call_id,
                limitation=_safe(limitation),
            )
        except (TypeError, ValueError):
            item = TrustBoundaryViolation(boundary="policy", code="instrumentation_error")
        self._violations.append(item)

    def reconcile(self, state: Mapping[str, object]) -> None:
        self._open()
        evidence = state.get("evidence") or {}
        records = evidence.get("records", []) if isinstance(evidence, Mapping) else []
        attempts = state.get("model_attempts") or []
        destinations = {
            "local_model": "local_model",
            "internal_inference": "frontier_model",
            "loopback_switchyard": "switchyard",
        }
        expected = ["mcp" for row in records if isinstance(row, Mapping) and not row.get("reused")] + [
            destinations.get(row.get("destination_class")) for row in attempts if isinstance(row, Mapping)
        ]
        observed = [row.boundary for row in self._network.values()]
        if sorted(item for item in expected if item) != sorted(observed):
            self._unknown_reasons.add("correlation_missing")
        tool_ids = [
            row.get("cache_key") for row in records if isinstance(row, Mapping) and not row.get("reused")
        ]
        mcp_ids = [row.call_id for row in self._network.values() if row.boundary == "mcp"]
        # Parallel Deep Agent tool branches may begin in a different order from
        # the deterministic evidence-record merge. Correlate the exact keyed
        # multiset; ordering is not part of the trust-boundary contract.
        if sorted(str(item) for item in tool_ids if item) != sorted(str(item) for item in mcp_ids if item):
            self.violation(
                "mcp", "correlation_mismatch", call_id=next((str(item) for item in mcp_ids if item), None)
            )
        matched_observations: set[str] = set()
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                continue
            rows = [
                row for row in self._network.values() if row.call_id == attempt.get("application_call_id")
            ]
            boundary = destinations.get(attempt.get("destination_class"))
            precontact = False
            if not rows and attempt.get("role") == "report_formatting" and attempt.get("state") == "failed":
                # Cosmetic presentation may reject immutable source blocks
                # before provider contact. Such observations intentionally
                # have no call ID, so correlate the one unused blocked row at
                # the expected physical boundary.
                rows = [
                    row
                    for row in self._network.values()
                    if row.observation_id not in matched_observations
                    and row.boundary == boundary
                    and row.attempt == "not_attempted"
                ]
                precontact = len(rows) == 1
            if len(rows) != 1:
                self._unknown_reasons.add("correlation_missing")
                continue
            row = rows[0]
            matched_observations.add(row.observation_id)
            expected_identity = (
                None
                if attempt.get("identity_evidence") == "unavailable"
                else attempt.get("identity_evidence")
            )
            if precontact:
                if (
                    row.outcome != "blocked"
                    or row.identity_evidence is not None
                    or expected_identity is not None
                ):
                    self.violation(
                        row.boundary,
                        "correlation_mismatch",
                        observation_id=row.observation_id,
                        call_id=row.call_id,
                    )
                continue
            formatter_plan_failure = (
                attempt.get("role") == "report_formatting"
                and attempt.get("state") == "failed"
                and attempt.get("failure_class") in {"invalid_schema", "finish_length"}
                and expected_identity == "direct_provider_verified"
            )
            expected_outcome = (
                "succeeded"
                if formatter_plan_failure
                else {"succeeded": "succeeded", "failed": "failed"}.get(attempt.get("state"))
            )
            if (
                row.boundary != boundary
                or row.attempt != "attempted"
                or row.application_request_id != attempt.get("application_request_id")
                or row.switchyard_trial_id != attempt.get("switchyard_trial_id")
                or row.outcome != expected_outcome
            ):
                self.violation(
                    row.boundary,
                    "correlation_mismatch",
                    observation_id=row.observation_id,
                    call_id=row.call_id,
                )
            if row.identity_evidence != expected_identity:
                self.violation(
                    row.boundary, "identity_mismatch", observation_id=row.observation_id, call_id=row.call_id
                )

    @staticmethod
    def _serialize(value: object) -> str:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return (
            value
            if isinstance(value, str)
            else json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )

    def finalize(self, *canonical_payloads: object) -> SecurityReceipt:
        if self._receipt is not None:
            return self._receipt
        checked = tuple(sorted(self._secrets))
        matched: tuple[str, ...] = ()
        limitation = None
        try:
            if self._secret_error:
                raise ValueError("secret configuration unavailable")
            wire = "\n".join(
                self._serialize(value)
                for value in (*canonical_payloads, *self._network.values(), *self._actions, *self._violations)
            )
            matched = tuple(
                name
                for name in checked
                if self._secrets[name] in wire
                or json.dumps(self._secrets[name], ensure_ascii=False)[1:-1] in wire
            )
            status = "fail" if matched else "pass"
        except Exception:
            status = "unknown"
            limitation = "Canonical secret scan did not complete."
        scan = SecretScanObservation(
            status=status, checked_variables=checked, matched_variables=matched, limitation=limitation
        )
        if status == "fail":
            self._violations.append(TrustBoundaryViolation(boundary="secret_scan", code="secret_exposure"))
        self._unknown_reasons.update(
            code
            for flag, code in (
                (
                    status == "unknown",
                    "secret_configuration_error" if self._secret_error else "secret_scan_error",
                ),
                (
                    any(
                        item.attempt == "unknown" or item.outcome == "unknown"
                        for item in self._network.values()
                    ),
                    "network_observation_unfinished",
                ),
                (
                    any(item.request_state == "unknown" for item in self._actions),
                    "policy_observation_missing",
                ),
            )
            if flag
        )
        unsafe = any(
            item.request_state == "requested"
            and (item.decision, item.attempt, item.outcome) != ("denied", "not_attempted", "blocked")
            for item in self._actions
        )
        state = (
            "violation"
            if self._violations or status == "fail" or unsafe
            else "unknown"
            if self._unknown_reasons
            else "verified"
        )
        self._receipt = SecurityReceipt(
            investigation_id=self.investigation_id,
            turn_id=self.turn_id,
            completeness=state,
            network_egress=tuple(self._network.values()),
            external_actions=tuple(self._actions),
            trust_boundary=state,
            violations=tuple(self._violations),
            unknown_reasons=tuple(sorted(self._unknown_reasons)),
            secret_scan=scan,
            finalized_at=datetime.now(UTC),
        )
        self._secrets.clear()
        return self._receipt
