"""Runtime-safe reader for the prepared, scenario-bound shock-event catalog."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, UTC
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .coverage import CoverageCatalog
from .event_schema import (
    EventCatalogError as EventCatalogError,
    _need as _need,
    Event as Event,
    PreparedCatalog as PreparedCatalog,
    ArtifactManifest as ArtifactManifest,
)
from .planning import parse_question
from .policy import PolicyDecision, PolicyKind, PolicyReason, resolve_policy
from .schemas import InvestigationRequest


_UNSAFE = re.compile(
    r"(?:\bllama(?:\b|[-_/])|(?:https?|grpc)://|\bbearer\s+|\b(?:api[_ -]?key|authorization|password)\b|\b(?:secret|token)\s*=)",
    re.I,
)
_GENERIC_OVERBROAD = re.compile(r"\b(?:all|every)\b.{0,40}\b(?:stocks?|tickers?|companies|market)\b", re.I)
_MARKET_DATE_QUESTION = re.compile(r"\b(?:trade|trading|move|return|volume|price|close|open)\b", re.I)
_SOURCE_DATE_QUESTION = re.compile(
    r"\b(?:evidence|filing|news|release|report(?:ed)?|results|source|statement)\b", re.I
)
_GENERIC_EVENT_SCOPE = re.compile(
    r"\b(?:event|shock)\b.{0,60}\bpeers?\b|"
    r"\bpeers?\b.{0,60}\b(?:event|shock)\b|"
    r"\b(?:the|these|its)\s+peers?\b",
    re.I,
)
_FOCUS_MENTION = re.compile(
    r"(?:\b(?:how did|what happened to|what about|analy[sz]e|focus on|switch to)\s+|"
    r"^\s*(?:please\s+)?(?:compare|discuss)\s+)"
    r"(?:(?:the|this|these)\s+)?\$?([A-Za-z][A-Za-z0-9.\-]{0,20})",
    re.I,
)
_NON_TICKER_REFERENCES = frozenset(
    {
        "BROADER",
        "FINANCIAL",
        "HISTORY",
        "ITS",
        "MARKET",
        "RECENT",
        "SECTOR",
        "THE",
        "THEIR",
        "VOLATILITY",
        "VOLUME",
    }
)
_SCOPE_REFERENCES = frozenset({"EVENT", "MOVE", "PEER", "PEERS", "RESULTS", "SHOCK"})
_COMPANY_ALIASES = {
    "ADVANCED": "AMD",
    "AMD": "AMD",
    "BROADCOM": "AVGO",
    "CHIPMAKER": "AVGO",
    "CHARLES": "SCHW",
    "GOLDMAN": "GS",
    "JP": "JPM",
    "JPMORGAN": "JPM",
    "NVIDIA": "NVDA",
    "SCHWAB": "SCHW",
    "APPLE": "AAPL",
    "MICROSOFT": "MSFT",
    "TESLA": "TSLA",
}


class EventRequestError(ValueError):
    """Safe user-facing error for unknown or conflicting event scope."""


def _read(path: Path, *, maximum: int, code: str) -> tuple[dict[str, Any], bytes]:
    try:
        _need(path.is_file() and not path.is_symlink(), code)
        size = path.stat().st_size
        _need(0 < size <= maximum, code)
        body = path.read_bytes()
        value = json.loads(body)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventCatalogError(code) from exc
    _need(isinstance(value, dict), code)
    return value, body


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _safe_public(value: object) -> None:
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    _need(not _UNSAFE.search(wire), "event_public_safety")


@dataclass(frozen=True)
class ShockEventCatalog:
    document: PreparedCatalog
    prepared_sha256: str

    @classmethod
    def load(cls, root: Path, coverage: CoverageCatalog) -> ShockEventCatalog:
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise EventCatalogError("event_catalog_unavailable") from exc
        _need(resolved.is_dir() and resolved.name.startswith("shock-events-"), "event_catalog_root")
        manifest_value, _ = _read(resolved / "manifest.json", maximum=256 * 1024, code="event_manifest")
        catalog_value, catalog_body = _read(
            resolved / "catalog.json", maximum=8 * 1024 * 1024, code="event_catalog"
        )
        try:
            manifest = ArtifactManifest.model_validate(manifest_value)
            document = PreparedCatalog.model_validate(catalog_value)
        except (ValueError, TypeError) as exc:
            raise EventCatalogError("event_catalog_contract") from exc
        prepared_sha = _digest(catalog_body)
        record = manifest.artifacts[0]
        _need(manifest.artifact_id == resolved.name == document.artifact_id, "event_artifact_identity")
        _need(
            record.sha256 == prepared_sha
            and record.bytes == len(catalog_body)
            and record.records == len(document.events),
            "event_artifact_digest",
        )
        _need(
            manifest.binding == document.binding and manifest.summary == document.summary,
            "event_artifact_projection",
        )
        expected_id = (
            "shock-events-"
            + _digest(
                _canonical(
                    {
                        "catalog_sha256": manifest.catalog_sha256,
                        "schema_sha256": manifest.schema_sha256,
                        "binding": manifest.binding.model_dump(mode="json"),
                    }
                )
            )[:16]
        )
        _need(expected_id == document.artifact_id, "event_artifact_identity")
        _need(document.binding.scenario_id == coverage.scenario_id, "event_scenario_binding")
        _need(
            document.binding.scenario_manifest_sha256 == coverage.scenario_manifest_sha256,
            "event_scenario_binding",
        )
        _need(document.binding.market_snapshot_id == coverage.market_snapshot_id, "event_scenario_binding")
        _need(
            document.binding.market_manifest_sha256 == coverage.market_manifest_sha256,
            "event_scenario_binding",
        )
        _need(
            document.binding.document_snapshot_id == coverage.document_snapshot_id, "event_scenario_binding"
        )
        _need(
            document.binding.document_manifest_sha256 == coverage.document_manifest_sha256,
            "event_scenario_binding",
        )
        public = cls(document, prepared_sha).public_payload()
        _safe_public(public)
        return cls(document, prepared_sha)

    def get(self, event_id: str) -> Event | None:
        return next((item for item in self.document.events if item.event_id == event_id), None)

    def public_payload(self) -> dict[str, Any]:
        binding = self.document.binding
        tickers = sorted({ticker for event in self.document.events for ticker in event.analysis_tickers})
        return {
            "schema_version": "shock-event-catalog-v1",
            "catalog_id": self.document.artifact_id,
            "catalog_sha256": self.prepared_sha256,
            "scenario_id": binding.scenario_id,
            "scenario_manifest_sha256": binding.scenario_manifest_sha256,
            "calendar": self.document.calendar,
            "timezone": self.document.timezone,
            "supported_tickers": tickers,
            "categories": [item.model_dump(mode="json") for item in self.document.categories],
            "events": [item.model_dump(mode="json") for item in self.document.events],
            "summary": self.document.summary.model_dump(mode="json"),
        }


def _same_cutoff(value: date | datetime, cutoff: datetime) -> bool:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value == cutoff.replace(tzinfo=None)
        return value.astimezone(UTC) == cutoff.astimezone(UTC)
    return value == cutoff.date()


def bind_event_request(
    request: InvestigationRequest, events: ShockEventCatalog | None
) -> tuple[InvestigationRequest, Event]:
    if not request.event_id:
        raise EventRequestError("A known event ID is required.")
    if events is None:
        raise EventRequestError("Known shock events are temporarily unavailable. Use custom scope instead.")
    event = events.get(request.event_id)
    if event is None:
        raise EventRequestError(
            "That known shock event is unavailable. Choose a listed event or use custom scope."
        )
    if request.ticker is not None and request.ticker != event.primary_ticker:
        raise EventRequestError(
            f"This event uses {event.primary_ticker} as its primary ticker. Clear the conflicting ticker or use custom scope."
        )
    if request.as_of is not None and not _same_cutoff(request.as_of, event.default_cutoff):
        raise EventRequestError(
            f"This event uses the evidence cutoff {event.default_cutoff.isoformat().replace('+00:00', 'Z')}. Clear the conflicting cutoff or use custom scope."
        )
    canonical = request.model_copy(
        update={
            "ticker": event.primary_ticker,
            "as_of": event.default_cutoff,
            "event_id": event.event_id,
        }
    )
    return canonical, event


def resolve_bound_policy(
    request: InvestigationRequest,
    coverage: CoverageCatalog,
    events: ShockEventCatalog | None,
    *,
    recorder=None,
) -> PolicyDecision:
    """Resolve normal requests unchanged and event requests to immutable event scope."""
    if request.event_id is None:
        return resolve_policy(request, coverage, recorder=recorder)
    canonical, event = bind_event_request(request, events)
    decision = resolve_policy(canonical, coverage, recorder=recorder)
    parsed = parse_question(canonical.question, coverage)
    named_allowed = set(event.analysis_tickers) | set(event.context_instruments)
    # Event selection pins market measurement to event_session. Other dates may
    # identify source material inside the window, but a market-date phrase must
    # still name the measured session so it cannot silently relabel the bar.
    clauses = re.split(r"\b(?:and|while)\b|[,;?]", canonical.question, flags=re.I)
    market_dates = {
        day
        for clause in clauses
        if _MARKET_DATE_QUESTION.search(clause)
        or re.search(r"\bcompare\b", clause, re.I)
        and not _SOURCE_DATE_QUESTION.search(clause)
        for day in parse_question(clause, coverage).dates
    }
    allowed_dates = {event.event_session, event.default_cutoff.date(), *event.source_dates}
    parsed_dates_compatible = (
        not parsed.dates
        or len(parsed.dates) <= 2
        and set(parsed.dates) <= allowed_dates
        and market_dates <= {event.event_session}
    )
    foreign_ticker_mentions = tuple(
        ticker
        for ticker in parsed.unknown_tickers
        if ticker not in _NON_TICKER_REFERENCES
        and re.search(
            rf"(?:\$\s*{re.escape(ticker)}\b|\b(?:how did|what happened to|what about|"
            rf"analy[sz]e|focus on|switch to|ticker|symbol|with|versus|vs\.?|against|"
            rf"relative to)\s+\$?{re.escape(ticker)}\b|"
            rf"^\s*\$?{re.escape(ticker)}\b)",
            canonical.question,
            re.I,
        )
    )
    focus_mentions = tuple(match.group(1).upper() for match in _FOCUS_MENTION.finditer(canonical.question))
    foreign_focus_mentions = tuple(
        _COMPANY_ALIASES.get(token, token)
        for token in focus_mentions
        if token not in _SCOPE_REFERENCES and _COMPANY_ALIASES.get(token, token) not in named_allowed
    )
    foreign_ticker_mentions = tuple(dict.fromkeys((*foreign_ticker_mentions, *foreign_focus_mentions)))
    material_unknown_tickers = tuple(
        ticker for ticker in parsed.unknown_tickers if ticker not in _NON_TICKER_REFERENCES
    )
    text_within_event = (
        not foreign_ticker_mentions
        and set(parsed.explicit_tickers) <= named_allowed
        and parsed_dates_compatible
    )
    event_resolved_reasons = {
        PolicyReason.SCOPE_CONFLICT,
        PolicyReason.MISSING_DATE,
        PolicyReason.MISSING_TICKER,
    }
    related_analysis_ticker = (
        decision.reason == PolicyReason.CONTEXT_ONLY
        and bool(parsed.explicit_tickers)
        and set(parsed.explicit_tickers) <= set(event.analysis_tickers)
    )
    authored_question = " ".join(canonical.question.casefold().split()) in {
        " ".join(question.text.casefold().split()) for question in event.questions
    }
    bounded_event_scope = (
        decision.reason == PolicyReason.OVERBROAD
        and not _GENERIC_OVERBROAD.search(canonical.question)
        and not material_unknown_tickers
        and (authored_question or bool(_GENERIC_EVENT_SCOPE.search(canonical.question)))
    )
    if (
        parsed.dates
        and not parsed_dates_compatible
        and decision.kind
        not in {
            PolicyKind.REFUSAL,
            PolicyKind.PRODUCT_HELP,
            PolicyKind.CONVERSATION,
        }
    ):
        scope = decision.scope.model_copy(
            update={
                "status": "clarification",
                "action": "clarify",
                "ticker": event.primary_ticker,
                "as_of": None,
                "market_as_of": None,
                "resolved_tickers": (),
                "group_key": None,
                "explanation": (
                    f"This event measures the market on {event.event_session.isoformat()} and uses "
                    f"{event.default_cutoff.date().isoformat()} only as the evidence cutoff. "
                    "Clarify the date or use custom research scope."
                ),
            }
        )
        decision = replace(
            decision,
            kind=PolicyKind.CLARIFICATION,
            reason=PolicyReason.SCOPE_CONFLICT,
            scope=scope,
            terminal=False,
        )
    if foreign_ticker_mentions and decision.kind == PolicyKind.SUPPORTED:
        ticker = foreign_ticker_mentions[0]
        scope = decision.scope.model_copy(
            update={
                "status": "partially_supported",
                "action": "partial_answer",
                "ticker": ticker,
                "as_of": None,
                "market_as_of": None,
                "resolved_tickers": (),
                "group_key": None,
                "explanation": (
                    f"{ticker} is outside this curated event's analysis and context scope. "
                    "Choose a listed event company or use custom research scope."
                ),
            }
        )
        decision = replace(
            decision,
            kind=PolicyKind.PARTIAL_SUPPORT,
            reason=PolicyReason.UNSUPPORTED_TICKER,
            scope=scope,
        )
    if text_within_event and (
        decision.kind == PolicyKind.CLARIFICATION
        and (decision.reason in event_resolved_reasons or bounded_event_scope)
        or decision.kind == PolicyKind.PARTIAL_SUPPORT
        and related_analysis_ticker
    ):
        decision = replace(decision, kind=PolicyKind.SUPPORTED, reason=PolicyReason.COVERED, terminal=True)
    limited_layers = tuple(
        name.replace("_", " ")
        for name in ("market", "documents", "licensed_news", "derived_features")
        if getattr(event.qualification, name).status != "ready"
    )
    qualification = f" Catalog qualification: {event.qualification.status}"
    if limited_layers:
        qualification += f"; limited layers: {', '.join(limited_layers)}"
    qualification += "."
    label = (
        f"Curated event {event.title} fixes primary {event.primary_ticker}; analysis tickers "
        f"{', '.join(event.analysis_tickers)}; market window {event.start_session.isoformat()} through "
        f"{event.end_session.isoformat()}; market measurement session {event.event_session.isoformat()}; evidence cutoff "
        f"{event.default_cutoff.astimezone(UTC).isoformat().replace('+00:00', 'Z')}."
        f"{qualification}"
    )
    supported = decision.kind == PolicyKind.SUPPORTED
    if not supported:
        return replace(decision, request=canonical)
    market_as_of = min(
        datetime.combine(event.event_session, time(23, 59, 59), UTC),
        event.default_cutoff,
    )
    scope = decision.scope.model_copy(
        update={
            "status": "supported",
            "action": "answer",
            "ticker": event.primary_ticker,
            "as_of": event.default_cutoff,
            "market_as_of": market_as_of,
            "resolved_tickers": event.analysis_tickers,
            "group_key": None,
            "explanation": label,
        }
    )
    return replace(
        decision,
        request=canonical,
        scope=scope,
        requested_day=event.event_session,
        effective_session_day=event.event_session,
    )


__all__ = [
    "EventCatalogError",
    "EventRequestError",
    "ShockEventCatalog",
    "bind_event_request",
    "resolve_bound_policy",
]
