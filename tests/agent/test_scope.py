from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from market_agent.schemas import CreateInvestigation
from market_agent.scope import ScopeError, follow_up, mentioned_date, mentioned_tickers, resolve


def ask(question, coverage, events=None, **fields):
    return resolve(CreateInvestigation(question=question, **fields), coverage, events)


@pytest.mark.parametrize("question", ["Hello", "Thanks!", "Give me a lasagna recipe.", "What can you do?"])
def test_greetings_and_off_topic_questions_ask_for_input(question, coverage):
    scope = ask(question, coverage)
    assert scope.status == "needs_input"
    assert set(scope.missing) == {"ticker", "date"}


@pytest.mark.parametrize(
    "question",
    [
        "How did NVIDIA trade on January 27, 2025?",
        "What drove NVDA lower on 2025-01-27?",
        "Has Nvidia had a day like Jan 27, 2025 before?",
        "What was behind nvidia's plunge on 1/27/2025?",
    ],
)
def test_paraphrases_resolve_to_the_same_scope(question, coverage):
    scope = ask(question, coverage)
    assert (scope.status, scope.ticker, scope.session) == ("resolved", "NVDA", date(2025, 1, 27))
    assert scope.as_of == datetime(2025, 1, 27, 21, tzinfo=UTC)


def test_companies_are_found_by_name_or_symbol_in_order(coverage):
    assert mentioned_tickers("Compare Goldman Sachs with JPM and Charles Schwab", coverage) == [
        "GS",
        "JPM",
        "SCHW",
    ]
    assert mentioned_tickers("versus company-specific explanations", coverage) == []


def test_ui_controls_win_and_extra_names_become_comparison_members(coverage):
    scope = ask("How did it compare with AMD?", coverage, ticker="NVDA", as_of=date(2025, 1, 27))
    assert scope.members == ("NVDA", "AMD")


def test_weekend_dates_resolve_to_the_previous_session(coverage):
    assert ask("NVDA on 2025-01-26", coverage).session == date(2025, 1, 24)


def test_dates_outside_coverage_explain_the_range(coverage):
    scope = ask("NVDA on 2024-06-03", coverage)
    assert scope.status == "needs_input" and "date" in scope.missing
    assert "January 2, 2025" in scope.note


def test_unsupported_ticker_is_reported(coverage):
    scope = ask("How did it move?", coverage, ticker="MSFT", as_of=date(2025, 1, 27))
    assert scope.missing == ("ticker",) and "MSFT" in scope.note


def test_curated_event_binds_members_cutoff_and_event_session(coverage, events):
    scope = ask("Any question text", coverage, events, event_id="nvda-deepseek-2025-01-27")
    assert scope.status == "resolved"
    assert scope.members == ("NVDA", "AMD", "AVGO")
    assert scope.session == date(2025, 1, 27)
    assert scope.as_of == datetime(2025, 1, 27, 21, tzinfo=UTC)


def test_unknown_event_is_an_error(coverage, events):
    with pytest.raises(ScopeError):
        ask("x" * 5, coverage, events, event_id="no-such-event")


def test_follow_up_adds_companies_and_keeps_the_date(coverage):
    first = ask("How did NVDA trade on 2025-01-27?", coverage)
    second = follow_up(first, "How did AMD compare?", coverage)
    assert second.members == ("NVDA", "AMD") and second.session == first.session


def test_follow_up_can_supply_what_was_missing(coverage):
    first = ask("Hello", coverage)
    second = follow_up(first, "NVIDIA on January 27, 2025 please", coverage)
    assert second.status == "resolved" and second.ticker == "NVDA"


def test_invalid_calendar_dates_are_ignored():
    assert mentioned_date("on 2025-02-30") is None


REAL = Path("/srv/market-shock")


@pytest.mark.skipif(not (REAL / "events/current/catalog.json").exists(), reason="prepared data not present")
def test_every_published_event_resolves():
    from market_agent.catalog import Coverage, EventCatalog

    coverage = Coverage.load(REAL / "scenario")
    catalog = EventCatalog.load(REAL / "events/current", coverage)
    for event in catalog.document.events:
        scope = ask(event.questions[0].text, coverage, catalog, event_id=event.event_id)
        assert scope.status == "resolved", event.event_id
        assert scope.session <= scope.as_of.date()
