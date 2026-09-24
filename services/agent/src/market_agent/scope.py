"""Resolve which companies and which cutoff an investigation is bound to.

Structured input (a curated event, or the UI's ticker and date controls) wins.
Otherwise tickers, company names, and dates are read from the question. Anything
still missing leaves the scope as ``needs_input`` and the agent asks for it.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from .catalog import Coverage, EventCatalog
from .config import COMPANIES
from .schemas import CreateInvestigation, Scope

MAX_MEMBERS = 5
_MONTHS = {
    name: index
    for index, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")
_ENGLISH_DATE = re.compile(
    r"\b("
    + "|".join(sorted(_MONTHS, key=len, reverse=True))
    + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
    re.IGNORECASE,
)


class ScopeError(ValueError):
    """The request names something that does not exist (for example an unknown event)."""


def mentioned_tickers(text: str, coverage: Coverage) -> list[str]:
    """Supported tickers named in ``text`` by symbol or company name, in order of appearance."""
    found: list[tuple[int, str]] = []
    for symbol in coverage.tickers:
        match = re.search(rf"(?<![A-Za-z]){re.escape(symbol)}(?![A-Za-z])", text)
        if match:
            found.append((match.start(), symbol))
        for alias in COMPANIES.get(symbol, ("", ()))[1]:
            match = re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE)
            if match:
                found.append((match.start(), symbol))
    return list(dict.fromkeys(symbol for _, symbol in sorted(found)))


def mentioned_date(text: str) -> date | None:
    """The first calendar date written in ``text`` (ISO, US numeric, or English)."""
    candidates: list[tuple[int, date]] = []
    try:
        for match in _ISO_DATE.finditer(text):
            candidates.append((match.start(), date(int(match[1]), int(match[2]), int(match[3]))))
        for match in _US_DATE.finditer(text):
            candidates.append((match.start(), date(int(match[3]), int(match[1]), int(match[2]))))
        for match in _ENGLISH_DATE.finditer(text):
            candidates.append((match.start(), date(int(match[3]), _MONTHS[match[1].lower()], int(match[2]))))
    except ValueError:
        return None
    return min(candidates)[1] if candidates else None


def _cutoff(value: date | datetime, coverage: Coverage) -> tuple[datetime, date] | str:
    """Evidence cutoff and market session for a requested date or instant, or a problem note."""
    if isinstance(value, datetime):
        instant = value if value.tzinfo else value.replace(tzinfo=UTC)
        eligible = [item for item in coverage.sessions if item.close_at <= instant]
        if not eligible or instant.date() > coverage.last_session:
            return _outside(coverage)
        return instant, eligible[-1].session_date
    session = coverage.session_on_or_before(value)
    if session is None:
        return _outside(coverage)
    return session.close_at, session.session_date


def _outside(coverage: Coverage) -> str:
    return (
        f"That date is outside the prepared data, which covers {coverage.first_session:%B %-d, %Y} "
        f"through {coverage.last_session:%B %-d, %Y}."
    )


def _build(
    tickers: list[str], when: date | datetime | None, coverage: Coverage, event_id: str | None = None
) -> Scope:
    members = tuple(tickers[:MAX_MEMBERS])
    missing = tuple(name for name, value in (("ticker", members), ("date", when)) if not value)
    note = None
    as_of = session = None
    if when is not None:
        resolved = _cutoff(when, coverage)
        if isinstance(resolved, str):
            missing, note = (*missing, "date"), resolved
        else:
            as_of, session = resolved
    return Scope(
        status="needs_input" if missing else "resolved",
        ticker=members[0] if members else None,
        members=members,
        as_of=as_of,
        session=session,
        event_id=event_id,
        missing=missing,
        note=note,
    )


def resolve(request: CreateInvestigation, coverage: Coverage, events: EventCatalog | None) -> Scope:
    if request.event_id:
        event = events.get(request.event_id) if events else None
        if event is None:
            raise ScopeError(f"Unknown event {request.event_id!r}.")
        members = [ticker for ticker in event.analysis_tickers if ticker in coverage.tickers]
        scope = _build(members, event.default_cutoff, coverage, event.event_id)
        session = coverage.session_on_or_before(event.event_session)
        return scope.model_copy(update={"session": session.session_date if session else scope.session})
    named = mentioned_tickers(request.question, coverage)
    if request.ticker and request.ticker not in coverage.tickers:
        return _build([], None, coverage).model_copy(
            update={"missing": ("ticker",), "note": f"{request.ticker} is not in the prepared data."}
        )
    tickers = list(dict.fromkeys([request.ticker, *named] if request.ticker else named))
    return _build(tickers, request.as_of or mentioned_date(request.question), coverage)


def follow_up(scope: Scope, question: str, coverage: Coverage) -> Scope:
    """Carry the scope into a follow-up, adding newly named companies or a new date."""
    tickers = list(dict.fromkeys([*scope.members, *mentioned_tickers(question, coverage)]))
    new_date = mentioned_date(question)
    if scope.event_id and new_date is None and len(tickers) == len(scope.members):
        return scope
    when: date | datetime | None = new_date or scope.as_of
    updated = _build(tickers, when, coverage, scope.event_id if new_date is None else None)
    if scope.event_id and new_date is None:
        updated = updated.model_copy(update={"session": scope.session})
    return updated
