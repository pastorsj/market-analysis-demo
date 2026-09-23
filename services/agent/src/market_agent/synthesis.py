"""Accepted tool evidence and report payloads for the Deep Agent.

There is no secondary one-shot writer or deterministic claim menu.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from .evidence import EvidenceRun
from .planning import EvidencePlan
from .schemas import (
    AccelerationReceipt,
    AnswerDraft,
    Artifact,
    Citation,
    InvestigationScope,
    ModelAttempt,
    SwitchyardTrialBundle,
)


@dataclass(frozen=True)
class SynthesisResult:
    draft: AnswerDraft
    attempt: ModelAttempt
    trial_bundle: SwitchyardTrialBundle | None = None


def evidence_reference_tickers(run: EvidenceRun) -> tuple[str, ...]:
    allowed: set[str] = set()
    for record in run.records:
        if (row := record.result) and row.get("outcome") in {"ok", "partial"}:
            sources = {
                item.get("evidence_id"): item.get("source_type")
                for item in row.get("citations", [])
                if isinstance(item, Mapping)
            }
            coverage = [
                item
                for item in row.get("coverage", [])
                if isinstance(item, Mapping) and item.get("dimension") == "instrument"
            ]
            available = {
                item.get("key") for item in coverage if item.get("status") != "missing"
            } - {
                item.get("key") for item in coverage if item.get("status") == "missing"
            }
            evidence = [
                (item.get("evidence_id"), item.get("values"))
                for item in row.get("evidence", [])
                if isinstance(item, Mapping) and isinstance(item.get("values"), Mapping)
            ]
            allowed |= {
                values["ticker"]
                for evidence_id, values in evidence
                if sources.get(evidence_id) == "market"
                and values.get("ticker") in available
                and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", values["ticker"])
            }
            if record.identity.tool == "find_historical_analogues":
                declared = {
                    (item.get("evidence_id"), item.get("ticker"))
                    for item in row.get("data", {}).get("analogues", [])
                    if isinstance(item, Mapping)
                }
                allowed |= {
                    values["analogue_ticker"]
                    for evidence_id, values in evidence
                    if sources.get(evidence_id) == "model"
                    and isinstance(values.get("analogue_ticker"), str)
                    and (evidence_id, values["analogue_ticker"]) in declared
                    and re.fullmatch(
                        r"[A-Z][A-Z0-9.\-]{0,9}", values["analogue_ticker"]
                    )
                }
            if record.identity.tool == "trace_shock_propagation":
                paths = row.get("data", {}).get("paths", [])
                declared = {
                    (item.get("from"), item.get("to"), item.get("relation"))
                    for item in paths
                    if isinstance(item, Mapping)
                }
                edges = {
                    (values.get("from"), values.get("to"))
                    for evidence_id, values in evidence
                    if sources.get(evidence_id) == "relationship"
                    and (values.get("from"), values.get("to"), values.get("relation"))
                    in declared
                    and all(
                        isinstance(values.get(key), str)
                        and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", values[key])
                        for key in ("from", "to")
                    )
                }
                depth = record.identity.arguments.get("max_depth")
                start = record.identity.arguments.get("ticker")
                valid_depth = type(depth) is int and 1 <= depth <= 3
                one = (
                    {target for source, target in edges if source == start}
                    if valid_depth
                    else set()
                )
                two = (
                    {target for source, target in edges if source in one}
                    if valid_depth and depth >= 2
                    else set()
                )
                three = (
                    {target for source, target in edges if source in two}
                    if valid_depth and depth == 3
                    else set()
                )
                reachable = {start} | one | two | three
                allowed |= {
                    value for edge in edges for value in edge if value in reachable
                }
    return tuple(sorted(allowed))


def accepted_evidence(
    scope: InvestigationScope,
    plan: EvidencePlan,
    run: EvidenceRun,
    *,
    normalize_scope: bool = False,
):
    members = plan.resolved_members or (
        (scope.ticker,) if normalize_scope and scope.ticker else ()
    )
    if (
        plan.status != "ready"
        or run.plan_id != plan.plan_id
        or run.status not in {"completed", "partial"}
    ):
        raise ValueError("evidence_integrity")
    if (
        not members
        or scope.ticker != members[0]
        or scope.as_of is None
        or (
            scope.resolved_tickers not in {(), members}
            if normalize_scope
            else scope.resolved_tickers != members
        )
    ):
        raise ValueError("scope_integrity")
    calls = {
        (
            item.tool,
            json.dumps(item.arguments, sort_keys=True, separators=(",", ":")),
        ): item
        for item in plan.required_calls + plan.optional_calls
    }
    cutoff = scope.as_of.astimezone(timezone.utc); market_cutoff = (scope.market_as_of or cutoff).astimezone(timezone.utc)
    document_tools = {"search_news", "project_news_topics"}
    call_cutoffs = {key: datetime.fromisoformat(str(item.arguments["as_of"]).replace("Z", "+00:00")) for key, item in calls.items()}
    market_cutoffs = {value for key, value in call_cutoffs.items() if key[0] not in document_tools}
    if len(run.records) != len(calls) or market_cutoffs not in (set(), {market_cutoff}) or any(
        item.arguments["ticker"] not in members or call_cutoffs[key] > cutoff or item.tool in document_tools and call_cutoffs[key] != cutoff
        for key, item in calls.items()
    ):
        raise ValueError("scope_integrity")
    rows, citations, artifacts, receipts = [], {}, {}, []
    for record in run.records:
        key = (
            record.identity.tool,
            json.dumps(
                record.identity.arguments, sort_keys=True, separators=(",", ":")
            ),
        )
        ticker = record.identity.arguments["ticker"]
        if (
            key not in calls
            or record.identity.primary_ticker != members[0]
            or record.identity.comparison_ticker
            != (None if ticker == members[0] else ticker)
        ):
            raise ValueError("evidence_integrity")
        if record.result is None:
            continue
        row = record.result
        receipt = row.get("receipt", {})
        expected = {
            "artifact_manifest_sha256": record.identity.scenario_manifest_sha256,
            "scenario_id": record.identity.scenario_id,
            "market_manifest_sha256": record.identity.market_manifest_sha256,
            "document_manifest_sha256": record.identity.document_manifest_sha256,
            "market_readiness_sha256": record.identity.market_readiness_sha256,
            "document_readiness_sha256": record.identity.document_readiness_sha256,
        }
        evidence_ids = {item.get("evidence_id") for item in row.get("evidence", [])}
        if (
            row.get("tool") != record.identity.tool
            or row.get("as_of") != record.identity.effective_cutoff
            or record.identity.effective_cutoff != record.identity.arguments["as_of"]
            or any(receipt.get(name) != value for name, value in expected.items())
            or any(
                item.get("evidence_id") not in evidence_ids
                for item in row.get("citations", [])
            )
        ):
            raise ValueError("evidence_integrity")
        for name, identity, values in (
            ("citation", "citation_id", citations),
            ("artifact", "artifact_id", artifacts),
        ):
            for item in row[f"{name}s"]:
                item_id = item[identity]
                if item_id in values and values[item_id] != item:
                    raise ValueError(f"{name}_integrity")
                values[item_id] = item
        rows.append(row)
        receipts.append({"tool": row["tool"], **row["receipt"]})
    if not rows:
        raise ValueError("evidence_integrity")
    return (
        scope.model_copy(update={"resolved_tickers": members}),
        rows,
        [Citation.model_validate(item) for item in citations.values()],
        [AccelerationReceipt.model_validate(item) for item in receipts],
        [Artifact.model_validate(item) for item in artifacts.values()],
    )
