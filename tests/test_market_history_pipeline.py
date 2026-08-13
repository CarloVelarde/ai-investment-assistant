"""Tests for the offline bar-history pipeline."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from investment_assistant.clock import SteppingClock
from investment_assistant.event_manager import EventManager
from investment_assistant.fixture_readers import load_market_history_fixture
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketWindow,
    ResearchReport,
    Signal,
    SignalDirection,
)
from investment_assistant.pipeline import process_market_bar, run_market_history
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


def test_abrupt_drop_creates_one_event_and_replays_quietly(tmp_path: Path) -> None:
    database_path = tmp_path / "abrupt.sqlite3"
    research_calls: list[int] = []
    first = run_market_history(
        database_path=database_path,
        fixture_path=HISTORY_DIR / "abrupt_drop.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )
    replay = run_market_history(
        database_path=database_path,
        fixture_path=HISTORY_DIR / "abrupt_drop.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )

    assert first.scenario == "abrupt_drop"
    assert len(first.accepted_signal_ids) == 1
    assert len(first.events) == 1
    assert first.events[0].ticker == "TSLA"
    assert first.events[0].direction is SignalDirection.DOWN
    assert first.events[0].status is EventStatus.NOTIFIED
    assert first.events[0].episode_open is True
    assert first.events[0].market_windows == (MarketWindow.ONE_HOUR,)
    assert replay.accepted_signal_ids == ()
    assert replay.processed_events == ()
    assert research_calls == [1]


def test_continuation_does_not_research_a_second_time(tmp_path: Path) -> None:
    result = run_market_history(
        database_path=tmp_path / "continuation.sqlite3",
        fixture_path=HISTORY_DIR / "continuation.json",
        clock=SteppingClock(START_TIME),
        notifier=lambda *_: None,
    )

    assert len(result.accepted_signal_ids) == 1
    assert result.events[0].current_update == 1
    assert len(result.reports) == 1


def test_escalation_researches_only_the_latest_update(tmp_path: Path) -> None:
    research_calls: list[int] = []
    result = run_market_history(
        database_path=tmp_path / "escalation.sqlite3",
        fixture_path=HISTORY_DIR / "escalation.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )

    assert len(result.accepted_signal_ids) == 2
    assert result.events[0].current_update == 2
    assert result.events[0].status is EventStatus.NOTIFIED
    assert [report.event_update for report in result.reports] == [2]
    assert research_calls == [2]


def test_recovery_closes_the_episode_without_extra_research(tmp_path: Path) -> None:
    research_calls: list[int] = []
    result = run_market_history(
        database_path=tmp_path / "recovery.sqlite3",
        fixture_path=HISTORY_DIR / "recovery.json",
        clock=SteppingClock(START_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=lambda *_: None,
    )

    assert len(result.accepted_signal_ids) == 1
    assert result.closed_event_ids == (result.events[0].event_id,)
    assert result.events[0].episode_open is False
    assert result.events[0].closed_at is not None
    assert result.events[0].status is EventStatus.NOTIFIED
    assert research_calls == [1]


def test_gradual_decline_and_broad_market_use_daily_rules(tmp_path: Path) -> None:
    gradual = run_market_history(
        database_path=tmp_path / "gradual.sqlite3",
        fixture_path=HISTORY_DIR / "gradual_decline.json",
        clock=SteppingClock(START_TIME),
        notifier=lambda *_: None,
    )
    broad = run_market_history(
        database_path=tmp_path / "broad.sqlite3",
        fixture_path=HISTORY_DIR / "broad_market.json",
        clock=SteppingClock(START_TIME),
        notifier=lambda *_: None,
    )

    assert MarketWindow.FIVE_DAYS in gradual.events[0].market_windows
    assert any(
        window is MarketWindow.FIVE_DAYS for window in broad.events[0].market_windows
    )
    assert broad.diagnostics == ()


def test_bar_detection_and_event_acceptance_roll_back_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, bars = load_market_history_fixture(HISTORY_DIR / "abrupt_drop.json")
    database_path = tmp_path / "atomic-bar.sqlite3"
    clock = SteppingClock(START_TIME)

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        for bar in bars[:-1]:
            process_market_bar(
                storage=storage,
                manager=manager,
                bar=bar,
                watchlist=frozenset({"TSLA"}),
                now=bar.end_at,
            )

        def fail_after_detection(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated event failure")

        monkeypatch.setattr(manager, "handle_signal", fail_after_detection)
        with pytest.raises(RuntimeError, match="simulated event failure"):
            process_market_bar(
                storage=storage,
                manager=manager,
                bar=bars[-1],
                watchlist=frozenset({"TSLA"}),
                now=bars[-1].end_at,
            )

        assert storage.get_market_bar(bars[-1].bar_id) is None
        assert storage.list_detector_states("TSLA") == ()
        assert storage.list_events() == ()


def _recording_researcher(
    calls: list[int],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    return research
