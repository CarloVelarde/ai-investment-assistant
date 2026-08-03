"""End-to-end tests for the durable offline application flow."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.clock import FixedClock
from investment_assistant.config import get_settings
from investment_assistant.main import main
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketSignal,
    MarketWindow,
    NewsSignal,
    ResearchReport,
    Signal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.pipeline import run_offline_signals, run_pipeline
from investment_assistant.reporting import (
    EVENT_NOTIFICATION_PREFIX,
    FAKE_RESEARCH_PREFIX,
    create_fake_research_report,
)

FIXTURE_DIR = (
    Path(__file__).parents[1]
    / "src"
    / "investment_assistant"
    / "fixtures"
    / "offline_walking_skeleton"
)
SIGNAL_TIME = datetime(2026, 1, 15, 15, 30, tzinfo=UTC)
EVENT_TIME = datetime(2026, 1, 15, 16, 30, tzinfo=UTC)
SOURCE_DETAILS = SourceDetails(
    provider="fixture",
    source="offline scenario",
    feed="offline-demo",
    retrieved_at=SIGNAL_TIME,
)
MARKET_SIGNAL = MarketSignal(
    signal_id="market-acme-down-1",
    ticker="ACME",
    occurred_at=SIGNAL_TIME,
    importance=SignalImportance.MODERATE,
    source_details=SOURCE_DETAILS,
    direction=SignalDirection.DOWN,
    rule="abrupt-decline",
    window=MarketWindow.ONE_HOUR,
    price_decline_ratio=Decimal("0.06"),
    volume_ratio=Decimal("1.7"),
)
NEWS_SIGNAL = NewsSignal(
    signal_id="news-acme-guidance-1",
    ticker="ACME",
    occurred_at=SIGNAL_TIME + timedelta(minutes=5),
    importance=SignalImportance.MODERATE,
    source_details=SOURCE_DETAILS,
    category="GUIDANCE",
    direction=SignalDirection.DOWN,
    headline="Acme lowers guidance",
    matched_phrase="lowers guidance",
)


@pytest.mark.parametrize(
    ("market_fixture", "news_fixture"),
    [
        (FIXTURE_DIR / "triggering_market.json", None),
        (None, FIXTURE_DIR / "triggering_news.json"),
    ],
)
def test_market_or_news_fixture_alone_completes_one_event(
    tmp_path: Path,
    market_fixture: Path | None,
    news_fixture: Path | None,
) -> None:
    notifications: list[int] = []

    result = run_pipeline(
        database_path=tmp_path / "independent.sqlite3",
        tracked_symbol="ACME",
        market_fixture_path=market_fixture,
        news_fixture_path=news_fixture,
        clock=FixedClock(EVENT_TIME),
        notifier=lambda event, _: notifications.append(event.current_update),
    )

    assert len(result.durable.accepted_signal_ids) == 1
    assert len(result.durable.events) == 1
    assert result.durable.events[0].status is EventStatus.NOTIFIED
    assert len(result.durable.reports) == 1
    assert notifications == [1]


def test_first_fixture_run_and_exact_replay_do_work_once(tmp_path: Path) -> None:
    database_path = tmp_path / "fixture-replay.sqlite3"
    research_calls: list[int] = []
    notification_calls: list[int] = []

    def research(
        event: Event,
        signals: tuple[Signal, ...],
    ) -> ResearchReport:
        research_calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    def notify(event: Event, _: ResearchReport) -> None:
        notification_calls.append(event.current_update)

    first = run_pipeline(
        database_path=database_path,
        tracked_symbol="ACME",
        market_fixture_path=FIXTURE_DIR / "triggering_market.json",
        news_fixture_path=FIXTURE_DIR / "triggering_news.json",
        clock=FixedClock(EVENT_TIME),
        researcher=research,
        notifier=notify,
    )
    replay = run_pipeline(
        database_path=database_path,
        tracked_symbol="ACME",
        market_fixture_path=FIXTURE_DIR / "triggering_market.json",
        news_fixture_path=FIXTURE_DIR / "triggering_news.json",
        clock=FixedClock(EVENT_TIME),
        researcher=research,
        notifier=notify,
    )

    assert first.market_record is not None
    assert first.news_record is not None
    assert first.market_signal is not None
    assert first.news_signal is not None
    assert len(first.durable.accepted_signal_ids) == 2
    assert len(first.durable.processed_events) == 1
    assert first.durable.events[0].current_update == 2
    assert first.durable.events[0].status is EventStatus.NOTIFIED
    assert replay.durable.accepted_signal_ids == ()
    assert replay.durable.processed_events == ()
    assert len(replay.durable.events) == 1
    assert len(replay.durable.reports) == 1
    assert len(replay.durable.notification_attempts) == 1
    assert research_calls == [2]
    assert notification_calls == [2]


def test_same_importance_repeat_is_saved_without_more_work(tmp_path: Path) -> None:
    database_path = tmp_path / "same-importance.sqlite3"
    research_calls: list[int] = []
    notification_calls: list[int] = []
    later_signal = replace(
        MARKET_SIGNAL,
        signal_id="market-acme-down-2",
        occurred_at=SIGNAL_TIME + timedelta(minutes=1),
    )
    research = _recording_researcher(research_calls)
    notifier = _recording_notifier(notification_calls)

    run_offline_signals(
        database_path=database_path,
        signals=(MARKET_SIGNAL,),
        clock=FixedClock(EVENT_TIME),
        researcher=research,
        notifier=notifier,
    )
    repeated = run_offline_signals(
        database_path=database_path,
        signals=(later_signal,),
        clock=FixedClock(EVENT_TIME),
        researcher=research,
        notifier=notifier,
    )

    assert repeated.accepted_signal_ids == (later_signal.signal_id,)
    assert repeated.processed_events == ()
    assert repeated.events[0].current_update == 1
    assert repeated.events[0].status is EventStatus.NOTIFIED
    assert len(repeated.reports) == 1
    assert research_calls == [1]
    assert notification_calls == [1]


def test_higher_importance_and_new_news_each_requeue_full_flow(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "important-updates.sqlite3"
    research_calls: list[int] = []
    notification_calls: list[int] = []
    higher_signal = replace(
        MARKET_SIGNAL,
        signal_id="market-acme-down-2",
        occurred_at=SIGNAL_TIME + timedelta(minutes=1),
        importance=SignalImportance.HIGH,
    )
    research = _recording_researcher(research_calls)
    notifier = _recording_notifier(notification_calls)

    run_offline_signals(
        database_path=database_path,
        signals=(MARKET_SIGNAL,),
        clock=FixedClock(EVENT_TIME),
        researcher=research,
        notifier=notifier,
    )
    escalated = run_offline_signals(
        database_path=database_path,
        signals=(higher_signal,),
        clock=FixedClock(EVENT_TIME),
        researcher=research,
        notifier=notifier,
    )
    enriched = run_offline_signals(
        database_path=database_path,
        signals=(NEWS_SIGNAL,),
        clock=FixedClock(EVENT_TIME),
        researcher=research,
        notifier=notifier,
    )

    assert escalated.events[0].current_update == 2
    assert enriched.events[0].current_update == 3
    assert enriched.events[0].status is EventStatus.NOTIFIED
    assert [report.event_update for report in enriched.reports] == [1, 2, 3]
    assert research_calls == [1, 2, 3]
    assert notification_calls == [1, 2, 3]


def test_retryable_failure_recovers_on_later_offline_run(tmp_path: Path) -> None:
    database_path = tmp_path / "offline-recovery.sqlite3"
    research_calls: list[int] = []
    notification_calls: list[int] = []

    def fail_research(
        event: Event,
        _: tuple[Signal, ...],
    ) -> ResearchReport:
        research_calls.append(event.current_update)
        raise RuntimeError("offline research unavailable")

    failed = run_offline_signals(
        database_path=database_path,
        signals=(MARKET_SIGNAL,),
        clock=FixedClock(EVENT_TIME),
        researcher=fail_research,
        notifier=_recording_notifier(notification_calls),
    )
    recovered = run_offline_signals(
        database_path=database_path,
        signals=(),
        clock=FixedClock(EVENT_TIME),
        researcher=_recording_researcher(research_calls),
        notifier=_recording_notifier(notification_calls),
    )

    assert failed.events[0].status is EventStatus.FAILED
    assert len(failed.failures) == 1
    assert recovered.events[0].status is EventStatus.NOTIFIED
    assert len(recovered.reports) == 1
    assert research_calls == [1, 1]
    assert notification_calls == [1]


def test_console_entrypoint_replay_uses_configured_database_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "console.sqlite3"
    get_settings.cache_clear()
    monkeypatch.setenv("INVESTMENT_ASSISTANT_LOG_JSON", "false")
    monkeypatch.setenv("INVESTMENT_ASSISTANT_DATABASE_PATH", str(database_path))
    try:
        main()
        main()
    finally:
        get_settings.cache_clear()

    standard_error = capsys.readouterr().err
    assert "Application started" in standard_error
    assert standard_error.count(EVENT_NOTIFICATION_PREFIX) == 1
    assert standard_error.count(FAKE_RESEARCH_PREFIX) == 1
    assert database_path.exists()


def _recording_researcher(
    calls: list[int],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(
        event: Event,
        signals: tuple[Signal, ...],
    ) -> ResearchReport:
        calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    return research


def _recording_notifier(
    calls: list[int],
) -> Callable[[Event, ResearchReport], None]:
    def notify(event: Event, _: ResearchReport) -> None:
        calls.append(event.current_update)

    return notify
