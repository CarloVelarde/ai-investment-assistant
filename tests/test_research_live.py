"""Live research scheduling, recovery, and console delivery. No network."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr

from investment_assistant.clock import FixedClock
from investment_assistant.config import Settings
from investment_assistant.event_manager import EventManager
from investment_assistant.main import main
from investment_assistant.market_data import MarketSession
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketBar,
    MarketTimeframe,
    ResearchReport,
    Signal,
)
from investment_assistant.reporting import (
    FAKE_RESEARCH_PREFIX,
    create_fake_research_report,
)
from investment_assistant.research_http import Deadline, HttpResult
from investment_assistant.storage import SQLiteStorage
from test_regular_session_lifecycle import (
    AFTER_CLOSE,
    CLOSED_SESSION,
    HandoffMarketData,
    RecordingMarketData,
    _daily,
    _minute,
)
from test_research_foundation import EVENT, NOW, SIGNAL, reserve, seed
from test_research_model import completed, message
from test_research_runner import runner
from test_sec import ScriptedHttp, Timer, response


def _research_calls(
    calls: list[str],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        calls.append(event.event_id)
        return create_fake_research_report(event, signals)

    return research


def _notify_calls(calls: list[str]) -> Callable[[Event, ResearchReport], None]:
    def notify(event: Event, report: ResearchReport) -> None:
        calls.append(event.event_id)

    return notify


def _second_event() -> Event:
    return replace(EVENT, event_id="event-2", ticker="BETA")


def test_live_pass_notifies_saved_reports_and_researches_one_fair_event(
    tmp_path: Path,
) -> None:
    first = EVENT
    second = _second_event()
    third = replace(EVENT, event_id="event-3", ticker="CASA")
    researched: list[str] = []
    notified: list[str] = []
    with SQLiteStorage(tmp_path / "live-pass.sqlite3") as storage:
        seed(storage, first)
        started = storage.mark_researching(first.event_id, 1, updated_at=NOW)
        assert started is not None
        report = create_fake_research_report(
            started, storage.list_signals(first.event_id)
        )
        assert storage.save_report_and_mark_reported(report, updated_at=NOW)
        seed(storage, second)
        seed(storage, third)
        EventManager(storage, clock=FixedClock(NOW)).process_pending(
            researcher=_research_calls(researched),
            notifier=_notify_calls(notified),
            max_research_runs=1,
        )
        assert notified == [first.event_id, second.event_id]
        assert researched == [second.event_id]
        notified_event = storage.get_event(first.event_id)
        researched_event = storage.get_event(second.event_id)
        waiting_event = storage.get_event(third.event_id)
        assert notified_event is not None
        assert researched_event is not None
        assert waiting_event is not None
        assert notified_event.status is EventStatus.NOTIFIED
        assert researched_event.status is EventStatus.NOTIFIED
        assert waiting_event.status is EventStatus.QUEUED


def test_failed_event_does_not_starve_unattempted_work(tmp_path: Path) -> None:
    first = EVENT
    second = _second_event()
    researched: list[str] = []
    with SQLiteStorage(tmp_path / "fair.sqlite3") as storage:
        seed(storage, first)
        seed(storage, second)
        attempt = reserve(storage, first)
        assert attempt is not None
        storage.fail_research_attempt(attempt.attempt_id, finished_at=NOW)
        EventManager(storage, clock=FixedClock(NOW)).process_pending(
            researcher=_research_calls(researched),
            notifier=lambda *_: None,
            max_research_runs=1,
        )
        assert researched == [second.event_id]


def test_interrupted_attempt_waits_five_minutes_then_retries(tmp_path: Path) -> None:
    researched: list[str] = []
    with SQLiteStorage(tmp_path / "interrupted.sqlite3") as storage:
        seed(storage)
        attempt = reserve(storage)
        assert attempt is not None
        assert storage.research_starts_on(NOW) == 1
        EventManager(storage, clock=FixedClock(NOW)).process_pending(
            researcher=_research_calls(researched),
            notifier=lambda *_: None,
            max_research_runs=1,
        )
        assert researched == []
        EventManager(
            storage, clock=FixedClock(NOW + timedelta(minutes=5))
        ).process_pending(
            researcher=_research_calls(researched),
            notifier=lambda *_: None,
            max_research_runs=1,
        )
        assert researched == [EVENT.event_id]
        finished = storage.get_event(EVENT.event_id)
        assert finished is not None
        assert finished.status is EventStatus.NOTIFIED


def test_missing_key_defers_without_report_failure_or_repeat_log(
    tmp_path: Path,
) -> None:
    http = ScriptedHttp([])
    with SQLiteStorage(tmp_path / "missing-key.sqlite3") as storage:
        seed(storage)
        manager = EventManager(storage, clock=FixedClock(NOW))
        manager.process_pending(
            researcher=runner(storage, Timer(), http, key=False),
            notifier=lambda *_: pytest.fail("must not notify"),
            max_research_runs=1,
        )
        deferral = storage.get_research_deferral(EVENT.event_id, 1)
        assert deferral is not None
        assert deferral.reason == "MISSING_KEY"
        assert storage.list_reports(EVENT.event_id) == ()
        assert storage.list_failures(EVENT.event_id) == ()
        assert storage.research_starts_on(NOW) == 0
        manager.process_pending(
            researcher=runner(storage, Timer(), http, key=False),
            notifier=lambda *_: pytest.fail("must not notify"),
            max_research_runs=1,
        )
        assert storage.get_research_deferral(EVENT.event_id, 1) == deferral
        assert storage.list_failures(EVENT.event_id) == ()
    assert not http.calls


def test_exhausted_daily_budget_defers_without_a_provider_call(
    tmp_path: Path,
) -> None:
    http = ScriptedHttp([])
    with SQLiteStorage(tmp_path / "budget.sqlite3") as storage:
        seed(storage)
        for index in range(20):
            other = replace(EVENT, event_id=f"other-{index}", ticker="BETA")
            seed(storage, other)
            assert reserve(storage, other) is not None
        EventManager(storage, clock=FixedClock(NOW)).process_pending(
            researcher=runner(storage, Timer(), http),
            notifier=lambda *_: pytest.fail("must not notify"),
            max_research_runs=1,
        )
        deferred = storage.get_research_deferral(EVENT.event_id, 1)
        assert deferred is not None
        assert deferred.reason == "DAILY_BUDGET"
        assert storage.list_reports(EVENT.event_id) == ()
        assert storage.research_starts_on(NOW) == 20
    assert not http.calls


def test_material_update_is_immediately_eligible_after_a_failed_attempt(
    tmp_path: Path,
) -> None:
    researched: list[int] = []
    with SQLiteStorage(tmp_path / "material.sqlite3") as storage:
        seed(storage)
        attempt = reserve(storage)
        assert attempt is not None
        storage.fail_research_attempt(attempt.attempt_id, finished_at=NOW)
        storage.save_event(replace(EVENT, current_update=2, status=EventStatus.QUEUED))

        def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
            researched.append(event.current_update)
            return create_fake_research_report(event, signals)

        EventManager(storage, clock=FixedClock(NOW)).process_pending(
            researcher=research,
            notifier=lambda *_: None,
            max_research_runs=1,
        )
        assert researched == [2]


def test_stale_result_is_rejected_with_no_report_or_notification(
    tmp_path: Path,
) -> None:
    notified: list[str] = []
    with SQLiteStorage(tmp_path / "stale.sqlite3") as storage:
        seed(storage)

        class UpdatingHttp(ScriptedHttp):
            def request(
                self,
                method: str,
                url: str,
                *,
                headers: Mapping[str, str],
                body: bytes | None,
                deadline: Deadline,
                timeout: float,
            ) -> HttpResult:
                storage.save_event(replace(EVENT, current_update=2))
                return super().request(
                    method,
                    url,
                    headers=headers,
                    body=body,
                    deadline=deadline,
                    timeout=timeout,
                )

        http = UpdatingHttp([response(completed(message()))])
        EventManager(storage, clock=FixedClock(NOW)).process_pending(
            researcher=runner(storage, Timer(), http),
            notifier=_notify_calls(notified),
            max_research_runs=1,
        )
        assert notified == []
        assert storage.list_reports(EVENT.event_id) == ()
        current = storage.get_event(EVENT.event_id)
        assert current is not None
        assert current.current_update == 2


def _live_settings(database_path: Path) -> Settings:
    return Settings(
        alpaca_api_key_id="test-key-id",
        alpaca_api_secret_key=SecretStr("test-secret"),
        openai_api_key=SecretStr(""),
        watchlist="TSLA",
        database_path=database_path,
    )


def _after_close_history() -> tuple[MarketBar, ...]:
    days = (
        datetime(2026, 1, 26).date(),
        datetime(2026, 1, 27).date(),
        datetime(2026, 1, 28).date(),
        datetime(2026, 1, 29).date(),
        datetime(2026, 1, 30).date(),
    )
    history = [*(_daily(day, Decimal("100")) for day in days)]
    history.append(_daily(datetime(2026, 2, 2).date(), Decimal("95")))
    history.extend(
        _minute(index, Decimal("97") if index == 60 else Decimal("100"))
        for index in range(61)
    )
    return tuple(history)


def test_live_mode_without_openai_key_writes_no_fake_report(tmp_path: Path) -> None:
    database_path = tmp_path / "no-key-live.sqlite3"
    provider = RecordingMarketData(history=_after_close_history())
    main(
        settings=_live_settings(database_path),
        provider=provider,
        clock=FixedClock(AFTER_CLOSE),
        loop=False,
        sleeper=lambda _seconds: None,
        notifier=lambda *_: pytest.fail("must not notify"),
    )
    with SQLiteStorage(database_path) as storage:
        events = storage.list_events()
        assert events
        for event in events:
            assert storage.list_reports(event.event_id) == ()
            assert storage.list_notification_attempts(event.event_id) == ()
            deferral = storage.get_research_deferral(
                event.event_id, event.current_update
            )
            assert deferral is not None
            assert deferral.reason == "MISSING_KEY"


def test_startup_research_and_socket_handoff_do_not_duplicate_events(
    tmp_path: Path,
) -> None:
    crossing = _minute(60, Decimal("97"))
    history = [*(_minute(index, Decimal("100")) for index in range(60)), crossing]
    provider = RecordingMarketData(
        history=history,
        session=MarketSession(
            is_open=True,
            timestamp=crossing.end_at,
            next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
            next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
        ),
    )
    database_path = tmp_path / "handoff-compose.sqlite3"
    researched: list[int] = []
    notified: list[int] = []

    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        researched.append(event.current_update)
        return create_fake_research_report(event, signals)

    result = main(
        settings=_live_settings(database_path),
        provider=provider,
        clock=FixedClock(crossing.end_at),
        loop=False,
        sleeper=lambda _seconds: None,
        researcher=research,
        notifier=lambda event, _report: notified.append(event.current_update),
    )

    assert result is not None
    assert provider.open_count == 1
    assert provider.close_count == 0
    assert researched == [1]
    assert notified == [1]
    with SQLiteStorage(database_path) as storage:
        events = storage.list_events()
        assert len(events) == 1
        assert len(storage.list_reports(events[0].event_id)) == 1


def test_post_research_gap_fill_recovers_minutes_without_duplicate_events(
    tmp_path: Path,
) -> None:
    extra = _minute(61, Decimal("97"))
    provider = RecordingMarketData(history=_after_close_history())
    database_path = tmp_path / "gap-fill.sqlite3"
    researched: list[int] = []

    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        provider.add_history(extra)
        researched.append(event.current_update)
        return create_fake_research_report(event, signals)

    main(
        settings=_live_settings(database_path),
        provider=provider,
        clock=FixedClock(AFTER_CLOSE),
        loop=False,
        sleeper=lambda _seconds: None,
        researcher=research,
        notifier=lambda *_: None,
    )

    assert researched == [2]
    with SQLiteStorage(database_path) as storage:
        bars = storage.list_market_bars("TSLA", MarketTimeframe.ONE_MINUTE)
        events = storage.list_events()
        assert extra.bar_id in {bar.bar_id for bar in bars}
        assert len(events) == 1
        assert len(storage.list_reports(events[0].event_id)) == 1
        assert events[0].status is EventStatus.NOTIFIED


def test_research_crossing_regular_close_closes_socket_without_reconnect(
    tmp_path: Path,
) -> None:
    crossing = _minute(60, Decimal("97"))
    provider = HandoffMarketData(
        history=[_minute(index, Decimal("100")) for index in range(60)],
        crossing=crossing,
    )
    closed = MarketSession(
        is_open=False,
        timestamp=datetime(2026, 2, 2, 21, 1, tzinfo=UTC),
        next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
        next_close=datetime(2026, 2, 3, 21, 0, tzinfo=UTC),
    )
    database_path = tmp_path / "open-to-closed.sqlite3"

    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        provider.set_session(closed)
        return create_fake_research_report(event, signals)

    main(
        settings=_live_settings(database_path),
        provider=provider,
        clock=FixedClock(crossing.end_at),
        loop=False,
        sleeper=lambda _seconds: None,
        researcher=research,
        notifier=lambda *_: None,
    )

    assert provider.open_count == 1
    assert provider.close_count == 1
    assert provider.stock_stream_is_open is False


def test_saved_report_retries_delivery_with_fake_label_and_without_research(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "saved-report.sqlite3"
    process_time = datetime(2026, 2, 4, 16, 0, tzinfo=UTC)
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(process_time))
        queued = manager.handle_signal(SIGNAL).event
        assert queued is not None
        researching = storage.mark_researching(
            queued.event_id, 1, updated_at=process_time
        )
        assert researching is not None
        report = create_fake_research_report(
            researching, storage.list_signals(queued.event_id)
        )
        assert storage.save_report_and_mark_reported(report, updated_at=process_time)

    researched: list[str] = []
    delivered: list[str] = []

    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        researched.append(event.event_id)
        raise AssertionError("saved reports must not research again")

    def notify(event: Event, report: ResearchReport) -> None:
        delivered.append(report.summary)
        assert report.is_fake is True
        assert report.summary.startswith(FAKE_RESEARCH_PREFIX)

    main(
        settings=_live_settings(database_path),
        provider=RecordingMarketData(
            history=(_daily(datetime(2026, 2, 2).date(), Decimal("100")),),
            session=CLOSED_SESSION,
        ),
        clock=FixedClock(AFTER_CLOSE),
        loop=False,
        sleeper=lambda _seconds: None,
        researcher=research,
        notifier=notify,
    )

    assert researched == []
    assert delivered == [report.summary]
    with SQLiteStorage(database_path) as storage:
        events = storage.list_events()
        assert len(events) == 1
        assert events[0].status is EventStatus.NOTIFIED
        saved = storage.list_reports(events[0].event_id)
        assert len(saved) == 1
        assert saved[0].is_fake is True
