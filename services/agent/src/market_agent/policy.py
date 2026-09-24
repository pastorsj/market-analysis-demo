"""Ordered deterministic policy over verified coverage and session facts."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from .coverage import CoverageCatalog, MarketSession
from .planning import Intent, ParsedQuestion, parse_question, resolve_comparison_scope, resolve_group_scope
from .schemas import InvestigationRequest, InvestigationScope, RouteMode, TurnRequest
from .security import SecurityRecorder

_NY = ZoneInfo("America/New_York")
_UNSAFE = re.compile(
    "|".join(
        (
            r"^\s*(?:please\s+)?(?:buy(?![- ]side\b)|sell(?![- ]side\b)|short(?![- ](?:interest|squeeze|term|selling|volume)\b))\b",
            r"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:buy(?![- ]side\b)|sell(?![- ]side\b)|short(?![- ](?:interest|squeeze|term|selling|volume)\b))\b",
            r"\bshould\s+i\s+(?:buy|sell|short)\b",
            r"\b(?:tell|advise)\s+me\b.{0,60}\b(?:how much|whether|how)\b.{0,40}\b(?:buy|sell|short|trade)\b",
            r"\b(?:buy|sell|short)\b.{0,40}\b(?:for me|my account|my shares?)\b",
            r"\b(?:guarantee|risk[- ]free)\b",
            r"\b(?:print|show|reveal|list|give)\b.{0,80}\b(?:api key|credentials?|\.env|environment variables?|system prompt|hidden routing|private chain of thought)\b",
            r"\b(?:confidential(?:ly)?|inside information)\b.{0,80}\b(?:trade|buy|sell|short)\b",
            r"\bnon-public\b.{0,80}\b(?:document|trading advantage)\b",
            r"\bexact\s+(?:closing\s+)?price\b.{0,50}\b(?:tomorrow|next\s+(?:day|week|month|year))\b",
            r"\b(?:run|execute)\b.{0,30}\bshell command\b",
            r"\buploads? every file\b",
            r"\b(?:169\.254\.169\.254|metadata\.google|file://)\b",
            r"\b(?:execute|place|submit)\b.{0,30}\b(?:order|trade)\b",
        )
    ),
    re.I,
)
_ACTIONS = tuple(
    (name, re.compile(pattern, re.I))
    for name, pattern in (
        ("order", r"\b(?:execute|place|submit|cancel)\b.{0,30}\border\b"),
        (
            "trade",
            r"^\s*(?:please\s+)?(?:buy(?![- ]side\b)|sell(?![- ]side\b)|short(?![- ](?:interest|squeeze|term|selling|volume)\b))\b|\b(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:buy(?![- ]side\b)|sell(?![- ]side\b)|short(?![- ](?:interest|squeeze|term|selling|volume)\b))\b|\b(?:execute|place|submit|make|perform)\b.{0,30}\btrade\b|\b(?:buy|sell|short)\b.{0,40}\b(?:for me|my account|my shares?)\b",
        ),
        (
            "message",
            r"\b(?:send|post|email|message|contact)\b.{0,50}\b(?:message|email|broker|person|user)\b",
        ),
        ("file_write", r"\b(?:write|create|save|delete|modify)\b.{0,40}\bfile\b"),
        (
            "arbitrary_url_fetch",
            r"\b(?:fetch|open|browse|download|request|curl)\b.{0,80}\b(?:https?://|www\.)",
        ),
        (
            "credential_access",
            r"\b(?:print|show|reveal|list|give|read)\b.{0,80}\b(?:api key|credentials?|\.env|environment variables?)\b",
        ),
        (
            "other_external",
            r"\b(?:run|execute)\b.{0,30}\b(?:shell command|script|program)\b|\bupload\b.{0,40}\bfile\b",
        ),
    )
)
_PRODUCT = (
    "this app",
    "this application",
    "application cover",
    "application use a frontier",
    "what do you mean by a market shock",
    "which stocks can i ask",
    "live information or a saved snapshot",
    "what sources do you use",
    "inspect the evidence behind an answer",
    "as-of time in a report",
    "how should i phrase a question",
    "two supported stocks",
    "what is a historical analogue",
    "main limitations of the analysis",
    "communicate confidence",
    "what happens if i",
    "continue an investigation",
    "parts of an investigation run locally",
    "how does this work",
)
_UNRELATED = re.compile(
    r"\b(?:recipe|lasagna|weather|rain|basketball|chest pain|diagnose|hospital)\b|\b(?:write|compose) (?:a )?(?:sonnet|poem|story)\b|\bwho was president\b",
    re.I,
)
_PERSONAL_TRAVEL = re.compile(
    r"\b(?:plan|book|organize|arrange)\s+(?:(?:me|us)\s+)?"
    r"(?:(?:a|an|my|our|the|weekend|summer)\s+){0,3}"
    r"(?:vacation|trip|holiday|itinerary|travel)\b",
    re.I,
)
_OVERBROAD = re.compile(
    r"\b(?:every|all)\b.{0,40}\b(?:u\.?s\.?\s+)?(?:stocks?|tickers?|companies|market)\b", re.I
)
_MARKET_WORDS = "stock market price return volume ticker share shock filing release volatility happened earnings trading analyst drawdown".split()
_MONTH_DAY = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*,?\s*20\d{2}\b)",
    re.I,
)
_COVERAGE_CAPABILITY = re.compile(
    r"\b(?:fully supported|supported primary target|context[- ](?:only|level))\b"
    r"|\b(?:full|primary|validated|verified)\s+(?:document\s+)?coverage\b"
    r"|\bcoverage\s+(?:do|does|can)\s+.{0,40}\b(?:have|offer|provide)\b"
    r"|\b(?:dataset|tools?|application|demo)\b.{0,60}\b(?:support|cover|capabilities)\b",
    re.I,
)


class PolicyKind(StrEnum):
    REFUSAL = "refusal"
    PRODUCT_HELP = "product_help"
    CONVERSATION = "conversation"
    CLARIFICATION = "clarification"
    PARTIAL_SUPPORT = "partial_support"
    NO_DATA = "no_data"
    SUPPORTED = "supported"


class PolicyReason(StrEnum):
    SAFETY = "safety"
    PRODUCT = "product"
    OUT_OF_SCOPE = "out_of_scope"
    MISSING_TICKER = "missing_ticker"
    MISSING_DATE = "missing_date"
    OVERBROAD = "overbroad"
    SCOPE_CONFLICT = "scope_conflict"
    CONTEXT_ONLY = "context_only"
    UNSUPPORTED_TICKER = "unsupported_ticker"
    OUTSIDE_COVERAGE = "outside_coverage"
    NON_TRADING_DATE = "non_trading_date"
    KNOWN_EVIDENCE_GAP = "known_evidence_gap"
    COVERED = "covered"


@dataclass(frozen=True)
class PolicyDecision:
    kind: PolicyKind
    reason: PolicyReason
    intents: tuple[Intent, ...]
    scope: InvestigationScope
    request: InvestigationRequest
    terminal: bool
    requested_day: date | None = None
    effective_session_day: date | None = None


def _request_day(value: date | datetime) -> date:
    if isinstance(value, datetime):
        cutoff = value.replace(tzinfo=_NY) if value.tzinfo is None else value
        return cutoff.astimezone(_NY).date()
    return value


def _contextual_date(parsed: ParsedQuestion, cutoff: date | datetime | None) -> ParsedQuestion:
    """Complete a matching month/day from explicit UI or established turn context.

    Never erase a full date, invalid date, relative date, or competing month/day.
    A bare month/day only restates the selected date; it cannot select a new one.
    """
    if cutoff is None or parsed.dates or not parsed.date_present:
        return parsed
    selected = _request_day(cutoff)
    matches = tuple(_MONTH_DAY.finditer(parsed.text))
    if not matches or any(
        match[1].lower() != selected.strftime("%B").lower() or int(match[2]) != selected.day
        for match in matches
    ):
        return parsed
    remainder = _MONTH_DAY.sub("", parsed.text)
    if parse_question(remainder).date_present:
        return parsed
    return replace(parsed, dates=(selected,))


def _scope(
    catalog: CoverageCatalog,
    *,
    status: str,
    action: str,
    explanation: str,
    ticker: str | None = None,
    as_of: datetime | None = None,
    members: tuple[str, ...] = (),
    group_key: str | None = None,
) -> InvestigationScope:
    return InvestigationScope(
        status=status,
        action=action,
        ticker=ticker,
        as_of=as_of,
        market_as_of=as_of,
        supported_universe=catalog.targets,
        explanation=explanation,
        resolved_tickers=members,
        group_key=group_key,
    )


def _product_help(question: str, catalog: CoverageCatalog) -> str:
    """Supply authoritative facts to the model-driven research-guide skill."""
    first, last = (
        (catalog.sessions[0].session_date.isoformat(), catalog.sessions[-1].session_date.isoformat())
        if catalog.sessions
        else ("unavailable", "unavailable")
    )
    sources = ", ".join(item.replace("_", " ") for item in catalog.document_source_kinds) or "none"
    return f"Answer the product question using this verified boundary: primary tickers {', '.join(catalog.targets)}; completed-session coverage {first} through {last}; historical daily snapshot; later historical reconstruction filtered by the selected cutoff; document classes {sources}; no live feed, personalized advice, trade execution, guarantees, unrestricted browsing, or hidden-system access."


def _decision(
    kind: PolicyKind,
    reason: PolicyReason,
    request: InvestigationRequest,
    catalog: CoverageCatalog,
    parsed: ParsedQuestion,
    explanation: str,
    *,
    status: str,
    action: str,
    ticker: str | None = None,
    session: MarketSession | None = None,
    requested_day: date | None = None,
    terminal: bool = True,
    members: tuple[str, ...] = (),
    group_key: str | None = None,
) -> PolicyDecision:
    cutoff = session.close_at.astimezone(_NY) if session else None
    policy_intent = {
        PolicyKind.REFUSAL: Intent.SAFETY,
        PolicyKind.PRODUCT_HELP: Intent.PRODUCT_HELP,
        PolicyKind.CONVERSATION: Intent.CONVERSATION,
    }.get(kind)
    intents = (policy_intent,) if policy_intent else parsed.intents
    return PolicyDecision(
        kind,
        reason,
        intents,
        _scope(
            catalog,
            status=status,
            action=action,
            ticker=ticker,
            as_of=cutoff,
            explanation=explanation,
            members=members,
            group_key=group_key,
        ),
        request,
        terminal,
        requested_day,
        session.session_date if session else None,
    )


def _unsupported(
    request: InvestigationRequest,
    catalog: CoverageCatalog,
    parsed: ParsedQuestion,
    ticker: str,
    context: set[str],
) -> PolicyDecision:
    reason = PolicyReason.CONTEXT_ONLY if ticker in context else PolicyReason.UNSUPPORTED_TICKER
    label = (
        "is comparison context, not a supported primary target."
        if reason == PolicyReason.CONTEXT_ONLY
        else "is not in validated coverage."
    )
    return _decision(
        PolicyKind.PARTIAL_SUPPORT,
        reason,
        request,
        catalog,
        parsed,
        f"{ticker} {label} Choose {', '.join(catalog.targets)}.",
        status="partially_supported",
        action="partial_answer",
        ticker=ticker,
    )


def _coverage_help(
    request: InvestigationRequest,
    catalog: CoverageCatalog,
    parsed: ParsedQuestion,
    ticker: str | None,
    session: MarketSession | None = None,
    requested_day: date | None = None,
) -> PolicyDecision:
    context = tuple(
        dict.fromkeys((*catalog.peers, *catalog.required_benchmarks, *catalog.optional_instruments))
    )
    explanation = (
        f"Verified primary targets: {', '.join(catalog.targets)}. "
        f"Declared context instruments: {', '.join(context) or 'none'}. "
        "Neither role proves full document coverage. Do not assume context coverage from the question; "
        "unlisted companies have no validated coverage. Explain these capabilities, not the previous "
        "market move. Do not invent company-specific sources or metrics."
    )
    return _decision(
        PolicyKind.PRODUCT_HELP,
        PolicyReason.PRODUCT,
        request,
        catalog,
        parsed,
        explanation,
        status="supported",
        action="answer",
        ticker=ticker,
        session=session,
        requested_day=requested_day,
        members=(ticker,) if ticker in catalog.targets else (),
    )


def resolve_policy(
    request: InvestigationRequest,
    catalog: CoverageCatalog,
    *,
    recorder: SecurityRecorder | None = None,
    _parsed: ParsedQuestion | None = None,
    _comparison_members: tuple[str, ...] = (),
) -> PolicyDecision:
    """Resolve one request; callers must inject a verified or explicit-test catalog."""
    if not isinstance(catalog, CoverageCatalog):
        raise TypeError("catalog must be a CoverageCatalog")
    question = request.question.strip()
    parsed = _parsed or parse_question(question, catalog)
    parsed = _contextual_date(parsed, request.as_of)
    lowered = parsed.lower
    if (
        parsed.comparison_reference
        and len(_comparison_members) > 1
        and not parsed.group_key
        and set(parsed.explicit_tickers) <= set(_comparison_members)
    ):
        valid, _ = resolve_comparison_scope(parsed, request.ticker, catalog, _comparison_members)
        if valid == "ready":
            parsed = replace(parsed, covered_tickers=_comparison_members)
    action = next((name for name, pattern in _ACTIONS if pattern.search(question)), None)
    if recorder:
        recorder.record_policy(
            **(
                {
                    "action_class": action,
                    "request_state": "requested",
                    "decision": "denied",
                    "attempt": "not_attempted",
                    "outcome": "blocked",
                }
                if action
                else {}
            )
        )
    if action or _UNSAFE.search(question):
        return _decision(
            PolicyKind.REFUSAL,
            PolicyReason.SAFETY,
            request,
            catalog,
            parsed,
            "I can analyze public market evidence, but cannot assist with that request.",
            status="refused",
            action="refuse",
        )
    if any(term in lowered for term in _PRODUCT):
        return _decision(
            PolicyKind.PRODUCT_HELP,
            PolicyReason.PRODUCT,
            request,
            catalog,
            parsed,
            _product_help(question, catalog),
            status="supported",
            action="answer",
        )
    if (
        re.fullmatch(r"(?:hello|hi|hey|thanks|thank you)(?: there)?[!. ]*", lowered)
        or _UNRELATED.search(question)
        or _PERSONAL_TRAVEL.search(question)
    ):
        return _decision(
            PolicyKind.CONVERSATION,
            PolicyReason.OUT_OF_SCOPE,
            request,
            catalog,
            parsed,
            "I can help with a supported stock and cutoff-bounded market question. "
            + _product_help(question, catalog),
            status="partially_supported",
            action="partial_answer",
        )
    explicit_tickers = parsed.explicit_tickers
    explicit_ticker = explicit_tickers[0] if explicit_tickers else None
    date_present, explicit_dates = parsed.date_present, parsed.dates
    parsed_date = explicit_dates[0] if len(explicit_dates) == 1 else None
    context = set(catalog.peers + catalog.required_benchmarks + catalog.optional_instruments)
    if (
        request.ticker
        and explicit_ticker
        and request.ticker not in explicit_tickers
        and explicit_ticker not in catalog.targets
        and not parsed.comparison_reference
    ):
        return _unsupported(request, catalog, parsed, explicit_ticker, context)
    if (
        request.ticker
        and explicit_ticker
        and request.ticker not in explicit_tickers
        and not parsed.comparison_reference
    ):
        named = ", ".join(explicit_tickers)
        explanation = (
            f"The ticker control selects {request.ticker}, but the question explicitly names {named}. "
            "Update the control or the question so they match."
        )
        return _decision(
            PolicyKind.CLARIFICATION,
            PolicyReason.SCOPE_CONFLICT,
            request,
            catalog,
            parsed,
            explanation,
            status="clarification",
            action="clarify",
            ticker=request.ticker,
            terminal=False,
        )
    if request.as_of is not None and date_present:
        if parsed_date is None:
            detail = (
                f" ({', '.join(item.isoformat() for item in explicit_dates)})"
                if len(explicit_dates) > 1
                else ""
            )
            return _decision(
                PolicyKind.CLARIFICATION,
                PolicyReason.MISSING_DATE,
                request,
                catalog,
                parsed,
                f"The question contains an invalid or ambiguous date{detail}. Use one explicit YYYY-MM-DD date that matches the evidence-cutoff control.",
                status="clarification",
                action="clarify",
                ticker=request.ticker,
                terminal=False,
            )
        selected_day = _request_day(request.as_of)
        if selected_day != parsed_date:
            explanation = (
                f"The evidence-cutoff control selects {selected_day.isoformat()}, but the question explicitly asks for {parsed_date.isoformat()}. "
                "Update the control or the question so they match."
            )
            return _decision(
                PolicyKind.CLARIFICATION,
                PolicyReason.SCOPE_CONFLICT,
                request,
                catalog,
                parsed,
                explanation,
                status="clarification",
                action="clarify",
                ticker=request.ticker or explicit_ticker,
                terminal=False,
            )
    ticker = request.ticker or explicit_ticker
    group_key, group_members = resolve_group_scope(parsed, catalog, ticker)
    if group_members:
        ticker = group_members[0]
    requested = request.as_of if request.as_of is not None else parsed_date
    capability = bool(_COVERAGE_CAPABILITY.search(question))
    if capability and (ticker is None or requested is None):
        return _coverage_help(request, catalog, parsed, ticker)
    if ticker is None and not any(term in lowered for term in _MARKET_WORDS):
        return _decision(
            PolicyKind.CONVERSATION,
            PolicyReason.OUT_OF_SCOPE,
            request,
            catalog,
            parsed,
            "I can help with a supported stock and cutoff-bounded market question.",
            status="partially_supported",
            action="partial_answer",
        )
    if _OVERBROAD.search(question) and group_key is None:
        return _decision(
            PolicyKind.CLARIFICATION,
            PolicyReason.OVERBROAD,
            request,
            catalog,
            parsed,
            f"Choose one supported primary ticker: {', '.join(catalog.targets)}.",
            status="clarification",
            action="clarify",
            terminal=False,
        )
    if ticker is None:
        return _decision(
            PolicyKind.CLARIFICATION,
            PolicyReason.MISSING_TICKER,
            request,
            catalog,
            parsed,
            "Which supported ticker should I investigate?",
            status="clarification",
            action="clarify",
            terminal=False,
        )
    if ticker not in catalog.targets:
        return _unsupported(request, catalog, parsed, ticker, context)
    if requested is None:
        explanation = (
            "That date is invalid or ambiguous; please provide YYYY-MM-DD."
            if date_present
            else "What point-in-time date should I use? Please provide YYYY-MM-DD."
        )
        return _decision(
            PolicyKind.CLARIFICATION,
            PolicyReason.MISSING_DATE,
            request,
            catalog,
            parsed,
            explanation,
            status="clarification",
            action="clarify",
            ticker=ticker,
            terminal=False,
            members=group_members,
            group_key=group_key,
        )
    if isinstance(requested, datetime):
        cutoff = requested.replace(tzinfo=_NY) if requested.tzinfo is None else requested
        requested_day = cutoff.astimezone(_NY).date()
    else:
        cutoff = requested
        requested_day = requested
    if (
        not catalog.sessions
        or requested_day < catalog.sessions[0].session_date
        or requested_day > catalog.sessions[-1].session_date
    ):
        return _decision(
            PolicyKind.PARTIAL_SUPPORT,
            PolicyReason.OUTSIDE_COVERAGE,
            request,
            catalog,
            parsed,
            "The requested date is outside validated scenario coverage.",
            status="partially_supported",
            action="partial_answer",
            ticker=ticker,
            requested_day=requested_day,
            members=group_members,
            group_key=group_key,
        )
    session = catalog.resolve_completed_session(cutoff)
    if session is None:
        return _decision(
            PolicyKind.PARTIAL_SUPPORT,
            PolicyReason.OUTSIDE_COVERAGE,
            request,
            catalog,
            parsed,
            "No completed validated market session exists at that cutoff.",
            status="partially_supported",
            action="partial_answer",
            ticker=ticker,
            requested_day=requested_day,
            members=group_members,
            group_key=group_key,
        )
    if not any(item.session_date == requested_day for item in catalog.sessions):
        requested_label = f"{requested_day.strftime('%A')}, {requested_day.isoformat()}"
        effective_label = f"{session.session_date.strftime('%A')}, {session.session_date.isoformat()}"
        explanation = (
            f"The requested day {requested_label} is not an XNYS trading session; the effective prior completed "
            f"session is {effective_label}. No {requested_day.strftime('%A')} market bar is assumed."
        )
        reason = PolicyReason.NON_TRADING_DATE
    elif session.session_date != requested_day:
        explanation = f"The requested cutoff on {requested_day.isoformat()} resolves to prior completed XNYS session {session.session_date.isoformat()}."
        reason = PolicyReason.COVERED
    else:
        explanation = f"Ticker and requested day {requested_day.isoformat()} resolve to that completed session in the verified coverage catalog."
        reason = PolicyReason.COVERED
    if capability:
        return _coverage_help(request, catalog, parsed, ticker, session, requested_day)
    if parsed.unresolved_comparators:
        names = ", ".join(parsed.unresolved_comparators)
        explanation = (
            f"The requested comparator {names} is not resolved in the verified coverage catalog. "
            f"Do not substitute {ticker} for that company or claim its data is available. "
            "Explain that this comparison cannot be grounded with the current tools; "
            f"available primary companies are {', '.join(catalog.targets)}."
        )
        ticker_hint = all(
            re.fullmatch(r"\$?[A-Z][A-Z0-9.-]{0,9}", name) for name in parsed.unresolved_comparators
        )
        kind = PolicyKind.CLARIFICATION if ticker_hint else PolicyKind.PARTIAL_SUPPORT
        return _decision(
            kind,
            PolicyReason.UNSUPPORTED_TICKER,
            request,
            catalog,
            parsed,
            explanation,
            status="clarification" if ticker_hint else "partially_supported",
            action="clarify" if ticker_hint else "partial_answer",
            ticker=ticker,
            session=session,
            requested_day=requested_day,
            members=(ticker,),
            terminal=not ticker_hint,
        )
    comparison_status, comparison_members = resolve_comparison_scope(parsed, ticker, catalog)
    if comparison_status != "ready":
        return _decision(
            PolicyKind.CLARIFICATION,
            PolicyReason.OVERBROAD,
            request,
            catalog,
            parsed,
            "Explicit comparison scope is required.",
            status="clarification",
            action="clarify",
            ticker=ticker,
            session=session,
            requested_day=requested_day,
            terminal=False,
            members=(ticker,),
        )
    return _decision(
        PolicyKind.SUPPORTED,
        reason,
        request,
        catalog,
        parsed,
        explanation,
        status="supported",
        action="answer",
        ticker=ticker,
        session=session,
        requested_day=requested_day,
        members=group_members or comparison_members,
        group_key=group_key,
    )


def resolve_follow_up(
    current: TurnRequest,
    prior_scope: InvestigationScope,
    prior_route_mode: RouteMode,
    catalog: CoverageCatalog,
    *,
    recorder: SecurityRecorder | None = None,
) -> PolicyDecision:
    """Parse the current turn first, then inherit only omitted primary scope fields."""
    parsed = _contextual_date(parse_question(current.question, catalog), prior_scope.as_of)
    explicit_ticker = parsed.explicit_tickers[0] if parsed.explicit_tickers else None
    comparison_only = parsed.comparison_reference
    ticker = prior_scope.ticker if explicit_ticker is None or comparison_only else explicit_ticker
    explicit_date = parsed.dates[0] if len(parsed.dates) == 1 else None
    as_of = explicit_date if parsed.date_present else prior_scope.as_of
    request = InvestigationRequest(
        question=current.question,
        ticker=ticker,
        as_of=as_of,
        route_mode=current.route_mode or prior_route_mode,
    )
    decision = resolve_policy(
        request, catalog, recorder=recorder, _parsed=parsed, _comparison_members=prior_scope.resolved_tickers
    )
    current_group, _ = resolve_group_scope(parsed, catalog, ticker)
    reset = bool(
        explicit_ticker
        and re.search(
            r"\b(?:focus|switch|analy[sz]e|investigate)\s+(?:only\s+|just\s+)?(?:on\s+|to\s+)?", parsed.lower
        )
    )
    if (
        not reset
        and current_group is None
        and (
            comparison_only
            and explicit_ticker
            or (
                explicit_ticker is None
                or explicit_ticker == prior_scope.ticker
                and not parsed.comparison_requested
            )
            and prior_scope.resolved_tickers
        )
    ):
        inherited = prior_scope.resolved_tickers or ((prior_scope.ticker,) if prior_scope.ticker else ())
        members = tuple(dict.fromkeys((*inherited, *(parsed.covered_tickers if comparison_only else ()))))
        if len(members) > 5:
            scope = decision.scope.model_copy(
                update={
                    "status": "clarification",
                    "action": "clarify",
                    "explanation": "Choose no more than five explicit comparison instruments.",
                    "resolved_tickers": (),
                    "group_key": None,
                }
            )
            return replace(
                decision,
                kind=PolicyKind.CLARIFICATION,
                reason=PolicyReason.OVERBROAD,
                scope=scope,
                terminal=False,
            )
        scope = decision.scope.model_copy(
            update={"resolved_tickers": members, "group_key": prior_scope.group_key}
        )
        decision = replace(decision, scope=scope)
    return decision
