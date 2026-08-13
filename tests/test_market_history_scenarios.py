"""End-to-end scenario tests for Milestone 3 acceptance criteria AC-06–AC-10."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from investment_assistant.clock import SteppingClock
from investment_assistant.market_metrics import (
    RULE_MULTI_DAY_MOVE,
    RULE_RELATIVE_TO_SPY,
)
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketWindow,
    NewsSignal,
    ResearchReport,
    Signal,
    SignalDirection,
)
from investment_assistant.pipeline import run_market_history
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

HISTORY_DIR = (
    Path(__file__).parents[1]
    / "src"
    / "investment_assistant"
    / "fixtures"
    / "market_history"
)
START_TIME = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)


def test_ac06_abrupt_and_gradual_create_open_market_only_events(
    tmp_path: Path,
) -> None:
    research_calls: list[int] = []
    abrupt_db = tmp_path / "ac06-abrupt.sqlite3"
    gradual_db = tmp_path / "ac06-gradual.sqlite3"
    abrupt = run_market_history(
        database_path=abrupt_db,
        fixture_path=HISTORY_DIR / "abrupt_drop.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )
    gradual = run_market_history(
        database_path=gradual_db,
        fixture_path=HISTORY_DIR / "gradual_decline.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )

    for result, database_path in ((abrupt, abrupt_db), (gradual, gradual_db)):
        assert len(result.events) == 1
        event = result.events[0]
        assert event.episode_open is True
        assert event.status is EventStatus.NOTIFIED
        assert event.direction is SignalDirection.DOWN
        assert result.reports
        assert not _news_signals(database_path)

    assert abrupt.events[0].market_windows == (MarketWindow.ONE_HOUR,)
    assert MarketWindow.FIVE_DAYS in gradual.events[0].market_windows
    assert research_calls == [1, 1]


def test_ac05_and_ac07_replay_and_escalation_use_one_latest_research(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "ac07-escalation.sqlite3"
    research_calls: list[int] = []
    first = run_market_history(
        database_path=database_path,
        fixture_path=HISTORY_DIR / "escalation.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )
    replay = run_market_history(
        database_path=database_path,
        fixture_path=HISTORY_DIR / "escalation.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )

    assert len(first.accepted_signal_ids) == 2
    assert first.events[0].current_update == 2
    assert [report.event_update for report in first.reports] == [2]
    assert research_calls == [2]
    assert replay.accepted_signal_ids == ()
    assert replay.processed_events == ()
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        states = storage.list_detector_states("TSLA", SignalDirection.DOWN)
    assert states
    assert any(state.last_emitted_importance is not None for state in states)


def test_ac08_recovery_clears_stress_and_closes_without_new_research(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "ac08-recovery.sqlite3"
    research_calls: list[int] = []
    result = run_market_history(
        database_path=database_path,
        fixture_path=HISTORY_DIR / "recovery.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )
    replay = run_market_history(
        database_path=database_path,
        fixture_path=HISTORY_DIR / "recovery.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )

    assert len(result.events) == 1
    assert result.events[0].episode_open is False
    assert result.events[0].closed_at is not None
    assert result.closed_event_ids == (result.events[0].event_id,)
    assert research_calls == [1]
    assert replay.accepted_signal_ids == ()
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        states = storage.list_detector_states("TSLA", SignalDirection.DOWN)
    assert states
    assert all(state.last_emitted_importance is None for state in states)


def test_ac09_later_drop_after_close_starts_a_new_event(tmp_path: Path) -> None:
    database_path = tmp_path / "ac09-new-event.sqlite3"
    research_calls: list[int] = []
    recovered = run_market_history(
        database_path=database_path,
        fixture_path=HISTORY_DIR / "recovery.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )
    later = run_market_history(
        database_path=database_path,
        fixture_path=HISTORY_DIR / "fresh_drop.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )

    assert len(recovered.events) == 1
    assert recovered.events[0].episode_open is False
    assert len(later.events) == 2
    first, second = later.events
    assert first.event_id == recovered.events[0].event_id
    assert first.episode_open is False
    assert second.event_id != first.event_id
    assert second.episode_open is True
    assert second.status is EventStatus.NOTIFIED
    assert later.accepted_signal_ids
    assert research_calls == [1, 1]


def test_ac10_relative_to_spy_distinguishes_company_and_market_moves(
    tmp_path: Path,
) -> None:
    company = run_market_history(
        database_path=tmp_path / "ac10-company.sqlite3",
        fixture_path=HISTORY_DIR / "broad_market.json",
        clock=SteppingClock(START_TIME),
        notifier=lambda *_: None,
    )
    market = run_market_history(
        database_path=tmp_path / "ac10-market.sqlite3",
        fixture_path=HISTORY_DIR / "market_wide.json",
        clock=SteppingClock(START_TIME),
        notifier=lambda *_: None,
    )

    company_rules = {
        signal.rule for signal in _market_signals(tmp_path / "ac10-company.sqlite3")
    }
    market_rules = {
        signal.rule for signal in _market_signals(tmp_path / "ac10-market.sqlite3")
    }

    assert RULE_MULTI_DAY_MOVE in company_rules
    assert RULE_RELATIVE_TO_SPY in company_rules
    assert RULE_MULTI_DAY_MOVE in market_rules
    assert RULE_RELATIVE_TO_SPY not in market_rules
    assert company.diagnostics == ()
    assert market.diagnostics == ()
    assert company.events[0].episode_open is True
    assert market.events[0].episode_open is True


def _market_signals(database_path: Path) -> tuple[Signal, ...]:
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        return tuple(
            signal
            for event in storage.list_events()
            for signal in storage.list_signals(event.event_id)
        )


def _news_signals(database_path: Path) -> tuple[NewsSignal, ...]:
    return tuple(
        signal
        for signal in _market_signals(database_path)
        if isinstance(signal, NewsSignal)
    )


def _recording_researcher(
    calls: list[int],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    return research
