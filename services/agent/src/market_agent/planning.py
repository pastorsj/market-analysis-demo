"""Code-owned, minimal evidence plans for the seven approved capabilities."""

from __future__ import annotations

import json, re; from collections import Counter; from collections.abc import Mapping; from dataclasses import dataclass; from datetime import date, datetime, timezone; from enum import StrEnum; from types import MappingProxyType; from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .coverage import CoverageCatalog

ToolName = Literal[
    "detect_market_shock", "get_price_context", "search_news", "find_historical_analogues",
    "trace_shock_propagation", "predict_volatility_risk", "project_news_topics",
]
Outcome = Literal["ok", "partial", "no_data"]
_ARGUMENTS = {
    "get_price_context": {"ticker", "as_of"}, "detect_market_shock": {"ticker", "as_of"},
    "search_news": {"ticker", "as_of", "query", "top_k"},
    "find_historical_analogues": {"ticker", "as_of", "top_k"},
    "trace_shock_propagation": {"ticker", "as_of", "max_depth"},
    "predict_volatility_risk": {"ticker", "as_of"},
    "project_news_topics": {"ticker", "as_of", "dimensions", "max_documents"},
}


class Intent(StrEnum):
    PRODUCT_HELP = "product_help"; SAFETY = "safety"; CONVERSATION = "conversation"
    PRICE = "price"; SHOCK = "shock"; CATALYST = "catalyst"; SOURCE = "source"
    ANALOGUE = "analogue"; PROPAGATION = "propagation"; RISK = "risk"; TOPIC = "topic"


class Sufficiency(StrEnum):
    POLICY = "policy"; GROUNDED = "grounded"
GROUP_REGISTRY: Mapping[str, tuple[str, tuple[str, ...] | Literal["targets"]]] = MappingProxyType({
    "ai-exposed semiconductor": ("ai_exposed_semiconductors", ("NVDA", "AMD")), "semiconductor": ("ai_exposed_semiconductors", ("NVDA", "AMD")),
    "financial peer basket": ("financials", ("JPM", "GS", "SCHW")), "financial stocks": ("financials", ("JPM", "GS", "SCHW")),
    "financial firms": ("financials", ("JPM", "GS", "SCHW")), "financial group": ("financials", ("JPM", "GS", "SCHW")), "financial sector": ("financials", ("JPM", "GS", "SCHW")),
    "supported universe": ("supported_universe", "targets"), "supported stocks": ("supported_universe", "targets"), "all five": ("supported_universe", "targets"),
})
COMPANY_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\badvanced\s+micro\s+devices(?:['’]s?)?\b", re.I), "AMD"),
    (re.compile(r"\b(?:charles\s+)?schwab(?:['’]s?)?\b", re.I), "SCHW"),
    (re.compile(r"\bgoldman(?:\s+sachs)?(?:['’]s?)?\b", re.I), "GS"),
    (re.compile(r"\b(?:j\.?\s*p\.?\s*morgan|jpmorgan)(?:\s+chase)?(?:['’]s?)?\b", re.I), "JPM"),
    (re.compile(r"\bnvidia(?:['’]s?)?\b", re.I), "NVDA"),
)
_MONTHS = {name.lower(): number for number, name in enumerate("January February March April May June July August September October November December".split(), 1)}
_WEEKDAYS = frozenset("monday tuesday wednesday thursday friday saturday sunday".split())
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_ENGLISH_DATE = re.compile(rf"\b(?:(?P<weekday>{'|'.join(_WEEKDAYS)})\s*,?\s+)?(?P<month>{'|'.join(_MONTHS)})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s*,\s*(?P<year>20\d{{2}})\b", re.I)
_AMBIGUOUS_DATE = re.compile(rf"\b(?:today|tomorrow|yesterday|(?:last|next)\s+(?:{'|'.join(_WEEKDAYS)})|(?:{'|'.join(_MONTHS)})(?:\s+20\d{{2}})?|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?)\b", re.I)
_NON_TICKERS = frozenset("A ACTUALLY ALL AN ANALYZE ANY COMPARE COMPANY CONTINUE DID DO DOES EVERY EXPLAIN FOR GIVE HOW I IN IS IT MARKET ME MOVE NOW ON PLEASE SHARE SHARES SHOW STOCK TELL THAT THE THIS TO US WHAT WHEN WHERE WHICH WHO WHY WRITE YOU YOUR".split())


class PlannedCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    tool: ToolName; arguments: dict[str, str | int]

    @model_validator(mode="after")
    def validate_call(self):
        if set(self.arguments) != _ARGUMENTS[self.tool]: raise ValueError(f"{self.tool} requires exactly {sorted(_ARGUMENTS[self.tool])}")
        ticker, cutoff = self.arguments["ticker"], self.arguments["as_of"]
        if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker): raise ValueError("ticker must be canonical uppercase")
        try:
            parsed = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("as_of must be canonical UTC") from exc
        canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if parsed.tzinfo else ""
        if cutoff != canonical: raise ValueError("as_of must be canonical UTC")
        for key, bounds in {"top_k": (1, 10), "max_depth": (1, 3), "dimensions": (2, 3), "max_documents": (2, 99)}.items():
            value = self.arguments.get(key)
            if value is not None and (type(value) is not int or not bounds[0] <= value <= bounds[1]): raise ValueError(f"{key} is outside its bound")
        query = self.arguments.get("query")
        if query is not None and (not isinstance(query, str) or not 1 <= len(query) <= 500 or query != " ".join(query.split())): raise ValueError("query must be canonical and bounded")
        return self


class EvidencePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    plan_id: str = Field(pattern=r"^plan-[a-f0-9]{16}$"); intents: tuple[Intent, ...]
    required_calls: tuple[PlannedCall, ...]; optional_calls: tuple[PlannedCall, ...] = ()
    sufficiency: Sufficiency; allowed_outcomes: tuple[Outcome, ...]; deterministic_answer_outcomes: tuple[Outcome, ...]
    explanation: str = Field(min_length=1, max_length=500)
    status: Literal["ready", "needs_scope_resolution"] = "ready"
    action: Literal["execute", "clarify"] = "execute"
    resolved_members: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_plan(self):
        calls = self.required_calls + self.optional_calls
        identities = [(call.tool, json.dumps(call.arguments, sort_keys=True, separators=(",", ":"))) for call in calls]
        if len(identities) != len(set(identities)): raise ValueError("duplicate canonical tool call")
        call_members = {str(call.arguments["ticker"]) for call in calls}
        if len(call_members) > 5 or self.resolved_members and not call_members <= set(self.resolved_members): raise ValueError("call scope and resolved members disagree")
        if not self.allowed_outcomes or not set(self.deterministic_answer_outcomes) <= set(self.allowed_outcomes): raise ValueError("outcome declarations are invalid")
        if not 0 <= len(self.resolved_members) <= 5 or len(set(self.resolved_members)) != len(self.resolved_members): raise ValueError("resolved comparison members must be unique and bounded")
        if any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", item) for item in self.resolved_members): raise ValueError("resolved comparison member is not canonical")
        if (self.status == "ready") != (self.action == "execute") or self.status != "ready" and calls: raise ValueError("scope-resolution plans cannot execute tools")
        if self.status == "ready":
            if calls and not self.resolved_members: raise ValueError("tool plan requires resolved members")
            if "get_price_context" in {call.tool for call in self.required_calls}:
                counts = Counter(str(call.arguments["ticker"]) for call in self.required_calls if call.tool == "get_price_context")
                if counts != Counter({member: 1 for member in self.resolved_members}): raise ValueError("market coverage must include each resolved member exactly once")
        return self


def _normal(question: str) -> str: return " ".join(question.split())


def _open_group(question: str) -> bool:
    """A descriptive sector context is not an unnamed peer-basket request."""
    group = re.search(r"\b(?:group|peers?|basket|universe|stocks|firms)\b", question)
    sector = re.search(r"\bsector\b(?![-\s]+(?:contagion|wide|specific|concerns?|fear)\b)", question)
    compare = re.search(r"\b(?:compare|versus|vs\.?|against|relative\s+to)\b", question)
    return bool(group or sector and compare)


def classify_intents(question: str) -> tuple[Intent, ...]:
    q = _normal(question).lower()
    if any(term in q for term in ("what does this app", "how does this work")): return (Intent.PRODUCT_HELP,)
    if re.fullmatch(r"(?:hello|hi|hey|thanks|thank you)[!. ]*", q): return (Intent.CONVERSATION,)
    return (Intent.PRICE, Intent.SHOCK)


@dataclass(frozen=True)
class ParsedQuestion:
    text: str; lower: str; intents: tuple[Intent, ...]
    explicit_tickers: tuple[str, ...]; covered_tickers: tuple[str, ...]; unknown_tickers: tuple[str, ...]
    date_present: bool; dates: tuple[date, ...]
    group_key: str | None; group_members: tuple[str, ...]
    comparison_requested: bool; comparison_reference: bool; source_fanout: bool
    unresolved_comparators: tuple[str, ...] = ()


def _named_comparators(text: str, declared: tuple[str, ...]) -> tuple[str, ...]:
    """Recognize explicit proper names in comparison slots, not arbitrary prose.

    Catalog aliases have already been canonicalized. Unknown names remain names,
    never invented tickers; ordinary prior-close/benchmark language is untouched.
    """
    name = r"\$?[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,4}"
    patterns = (
        rf"(?i:\b(?:with|versus|vs\.?|against|relative to)\s+)({name})",
        rf"(?i:\bhow\s+({name})\s+compared\b)",
        rf"(?i:\bhow\s+did\s+)({name})(?i:\s+compare\b)",
    )
    return tuple(dict.fromkeys(
        match[1] for pattern in patterns for match in re.finditer(pattern, text)
        if match[1].lstrip("$").upper() not in declared
        and match[1].upper() not in _NON_TICKERS
        and match[1].lower().split()[0] not in {"its", "their", "the", "prior", "previous", "benchmark"}
    ))


def _dates(question: str) -> tuple[bool, tuple[date, ...]]:
    matches = [*_ISO_DATE.finditer(question), *_ENGLISH_DATE.finditer(question)]
    try:
        values = [date(*(int(part) for part in match.groups())) if match.re is _ISO_DATE else date(int(match["year"]), _MONTHS[match["month"].lower()], int(match["day"])) for match in matches]
    except ValueError:
        return True, ()
    if any(match.groupdict().get("weekday") and value.strftime("%A").lower() != match["weekday"].lower() for match, value in zip(matches, values)): return True, ()
    return (True, tuple(dict.fromkeys(values))) if matches else (bool(_AMBIGUOUS_DATE.search(question)), ())


def parse_question(question: str, catalog: CoverageCatalog | None = None) -> ParsedQuestion:
    text = _normal(question); lower = text.lower(); intents = classify_intents(text); date_present, dates = _dates(text)
    if catalog is None:
        return ParsedQuestion(text, lower, intents, (), (), (), date_present, dates, None, (), False, False, False)
    declared = tuple(dict.fromkeys((*catalog.targets, *catalog.peers, *catalog.required_benchmarks, *catalog.optional_instruments)))
    covered = declared if catalog.verification == "explicit_test" else tuple(item for item in declared if catalog.fields_for(item))
    canonical = text
    for pattern, symbol in COMPANY_ALIASES:
        if symbol in catalog.targets: canonical = pattern.sub(symbol, canonical)
    occurrences = [(match.start(), symbol) for symbol in declared for match in re.finditer(rf"(?<![A-Z0-9.\-]){re.escape(symbol)}(?![A-Z0-9\-]|\.[A-Z0-9])", canonical, re.I)]
    explicit = tuple(dict.fromkeys(symbol for _, symbol in sorted(occurrences)))
    if not explicit:
        hints = (
            r"\$(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\b", r"\b(?:ticker|symbol)\s+\$?(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\b",
            r"\b(?:happened to|what about|why did|analy[sz]e|focus on|switch to)\s+\$?(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\b", r"^\s*(?:actually[,:]?\s+|now\s+)?\$?(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\b",
        )
        for expression in hints:
            if match := re.search(expression, canonical, re.I):
                candidate = match["ticker"].upper()
                if candidate not in _NON_TICKERS and candidate.lower() not in _MONTHS and candidate.lower() not in _WEEKDAYS and (expression != hints[-1] or match["ticker"].isupper()): explicit = (candidate,); break
    covered_tickers = tuple(item for item in covered if item in explicit)
    candidate_matches = [(match.start(), match.group(0).upper()) for match in re.finditer(r"\b[A-Z][A-Z0-9]*(?:[.\-][A-Z0-9]+)*\b", canonical)]
    candidate_matches += [(match.start(1), match.group(1).upper()) for match in re.finditer(r"\b(?:with|versus|vs\.?|against|relative\s+to)\s+([A-Za-z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)*)", canonical, re.I) if match[1].lower() not in {"its", "their", "the", "prior", "previous", "benchmark"}]
    unknown = tuple(dict.fromkeys(item for _, item in sorted(candidate_matches) if item not in covered and item not in {"AI", "VS", "AND", "THE"}))
    hits = [value for phrase, value in GROUP_REGISTRY.items() if phrase in lower]
    group_key = hits[0][0] if hits and len({item[0] for item in hits}) == 1 else "supported_universe" if hits else None
    mapped = catalog.targets if any(key == "supported_universe" for key, _ in hits) else tuple(item for _, values in hits for item in values if values != "targets")
    group_members = tuple(item for item in dict.fromkeys(mapped) if item in catalog.targets)
    group_words = _open_group(lower)
    comparison = bool(re.search(r"\b(?:compared?|versus|vs\.?|against|relative\s+to)\b", lower)) or len(covered_tickers) > 1 or group_words
    first = explicit[0] if explicit else None
    reference = bool(first and (re.search(r"\b(?:compare\s+(?:that|it)(?:\s+(?:move|reaction|performance|return|price))?|(?:that|it)\s+compare)\s+(?:with|to|against)\b", text, re.I) or re.search(r"\b(?:relative to|against)\s+\$?[A-Z][A-Z0-9.\-]{0,9}\b", text, re.I) or re.match(r"\s*(?:versus|vs\.?)\b", text, re.I)))
    reference = reference or bool(re.search(r"\bhow\s+\S+(?:\s+\S+){0,4}\s+compared\b", text, re.I))
    reference = reference or bool(re.search(r"\bhow\s+did\s+\S+\s+compare\b|\b(?:these|those|both|their)\s+(?:stocks|companies|peers|returns|performance)\b", text, re.I))
    source_terms = r"(?:sources?|evidence|filings?|releases?|news|coverage)"; member_terms = "|".join(re.escape(item) for item in (*covered_tickers, *group_members)); member = rf"(?:{member_terms})(?:['’]s)?"
    named_sources = bool(member_terms and re.search(rf"\bcompare\s+{member}(?:\s*,\s*{member})*\s*(?:,?\s*(?:and|versus|vs\.?|against)\s+{member})+\s+(?:(?:using|on)\s+)?(?:(?:their|both|each(?:['’]s)?)\s+)?{source_terms}\b", canonical, re.I))
    explicit_sources = named_sources or bool(re.search(rf"\b(?:compare|contrast)\s+(?:(?:the|their|these|both|each)\s+)?{source_terms}\b|\b(?:both|each|every|all)\b.{{0,40}}\b{source_terms}\b|\b{source_terms}\s+(?:for|from)\s+(?:both|each|every|all)\b", canonical, re.I))
    source_fanout = explicit_sources
    return ParsedQuestion(text, lower, intents, explicit, covered_tickers, unknown, date_present, dates, group_key, group_members, comparison, reference, source_fanout, _named_comparators(canonical, declared) if comparison else ())


def resolve_group_scope(question: str | ParsedQuestion, catalog: CoverageCatalog, primary: str | None = None) -> tuple[str | None, tuple[str, ...]]:
    parsed = question if isinstance(question, ParsedQuestion) else parse_question(question, catalog)
    if parsed.group_key is None: return None, ()
    members = parsed.group_members
    if primary in catalog.targets and primary not in members: members = (primary, *members)
    if primary in members: members = (primary, *(item for item in members if item != primary))
    return (parsed.group_key, members) if members and len(members) <= 5 else (None, ())


def resolve_comparison_scope(question: str | ParsedQuestion, ticker: str | None, catalog: CoverageCatalog | None, resolved: tuple[str, ...] | None = None) -> tuple[str, tuple[str, ...]]:
    primary = (ticker or "").strip().upper()
    if catalog is None: return "ready", (primary,) if primary else ()
    if not isinstance(catalog, CoverageCatalog): raise TypeError("catalog must be a verified CoverageCatalog")
    declared = tuple(dict.fromkeys((*catalog.targets, *catalog.peers, *catalog.required_benchmarks, *catalog.optional_instruments)))
    covered = declared if catalog.verification == "explicit_test" else tuple(item for item in declared if catalog.fields_for(item))
    parsed = question if isinstance(question, ParsedQuestion) else parse_question(question, catalog)
    group_key, mapped = resolve_group_scope(parsed, catalog, primary); explicit = parsed.covered_tickers
    if resolved is not None:
        members = tuple(item for item in resolved if item in covered)
        return ("ready", members) if members and len(members) == len(resolved) <= 5 and members[0] == primary else ("needs_scope_resolution", ())
    open_group = _open_group(parsed.lower)
    if (parsed.comparison_requested and parsed.unknown_tickers) or (open_group and group_key is None and len(explicit) < 2): return "needs_scope_resolution", ()
    members = tuple(item for item in dict.fromkeys((*explicit, *mapped)) if item in covered)
    if parsed.comparison_requested and primary and primary in covered and not mapped and primary not in members: members = (primary, *members)
    if parsed.comparison_requested and primary in members: members = (primary, *(item for item in members if item != primary))
    if not parsed.comparison_requested: members = (primary,) if primary in covered else ()
    return ("needs_scope_resolution", ()) if not members or len(members) > 5 else ("ready", members)
