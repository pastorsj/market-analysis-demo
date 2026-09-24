"""Build evidence items and their citations from bars, documents, and computations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from .models import Citation, EvidenceItem, stable_id
from .scenario import Scenario, parse_time

_SOURCE_TYPES = {"company_release": "release", "primary_source": "release", "filing": "filing"}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def market_row(scenario: Scenario, row: dict[str, Any], summary: str) -> tuple[EvidenceItem, Citation]:
    """One observed daily bar together with the features derived from its history."""
    observed = parse_time(row["bar_end"])
    values = {key: value for key, value in row.items() if key not in {"source_id", "source_row_id"}}
    evidence_id = stable_id("ev", scenario.manifest_sha256, row["source_id"], row["source_row_id"])
    citation = Citation(
        citation_id=stable_id("cit", row["source_id"], row["source_row_id"], length=16),
        evidence_id=evidence_id,
        title=f"{row['instrument_id']} daily market record for {row['session_date']}",
        url=scenario.source_url(row["source_id"]),
        source_type="market",
        published_at=observed,
        available_at=observed,
        excerpt=summary[:1200],
        content_sha256=_digest(values),
    )
    item = EvidenceItem(
        evidence_id=evidence_id,
        observed_at=observed,
        available_at=observed,
        source_id=row["source_id"],
        values=values,
    )
    return item, citation


def document(
    scenario: Scenario, row: dict[str, Any], score: float | None = None
) -> tuple[EvidenceItem, Citation]:
    published, available = parse_time(row["published_at"]), parse_time(row["available_at"])
    values = {
        "title": row["title"],
        "text": row["text"],
        "source_type": row["source_type"],
        "content_scope": row["content_scope"],
    }
    if score is not None:
        values["similarity"] = round(score, 6)
    evidence_id = stable_id("ev", scenario.manifest_sha256, row["source_id"], row["revision"])
    citation = Citation(
        citation_id=stable_id("cit", row["source_id"], row["revision"], length=16),
        evidence_id=evidence_id,
        title=row["title"],
        url=row["canonical_url"],
        source_type=_SOURCE_TYPES.get(row["source_type"], "news"),
        published_at=published,
        available_at=available,
        excerpt=row["text"][:1200],
        content_sha256=row["content_sha256"],
    )
    item = EvidenceItem(
        evidence_id=evidence_id,
        observed_at=published,
        available_at=available,
        source_id=row["source_id"],
        values=values,
    )
    return item, citation


def corporate_action(scenario: Scenario, row: dict[str, Any]) -> tuple[EvidenceItem, Citation]:
    published = parse_time(row["published_at"])
    evidence_id = stable_id("ev", scenario.manifest_sha256, "corporate-action", row)
    citation = Citation(
        citation_id=stable_id("cit", "corporate-action", row, length=16),
        evidence_id=evidence_id,
        title=f"{row['instrument_id']} {row['action_type']} announcement",
        url=row["attribution_url"],
        source_type="release",
        published_at=published,
        available_at=published,
        excerpt=f"{row['action_type']} factor {row['factor']} effective {row['effective_at']}.",
        content_sha256=row["source_sha256"],
    )
    item = EvidenceItem(
        evidence_id=evidence_id,
        observed_at=published,
        available_at=published,
        source_id="corporate-action",
        values=row,
    )
    return item, citation


def computed(
    scenario: Scenario, tool: str, as_of: datetime, values: dict[str, Any], inputs: list[str]
) -> tuple[EvidenceItem, Citation]:
    """A result computed by a tool (model output, ranking, graph). It has no external URL."""
    payload = {"tool": tool, "input_evidence_ids": inputs, **values}
    digest = _digest(payload)
    evidence_id = stable_id("ev", scenario.manifest_sha256, tool, digest)
    citation = Citation(
        citation_id=stable_id("cit", tool, digest, length=16),
        evidence_id=evidence_id,
        title=f"{tool.replace('_', ' ')} result",
        url=None,
        source_type="model",
        published_at=as_of,
        available_at=as_of,
        excerpt=str(values["summary"])[:1200],
        content_sha256=digest,
    )
    item = EvidenceItem(
        evidence_id=evidence_id,
        observed_at=as_of,
        available_at=as_of,
        source_id=f"computed:{tool}",
        values=payload,
    )
    return item, citation
