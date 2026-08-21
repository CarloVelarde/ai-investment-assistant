"""Tests for the durable SQLite layout and model mappings."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.models import (
    ClassificationStatus,
    DetectorState,
    Event,
    EventStatus,
    FailureStep,
    MarketBar,
    MarketSignal,
    MarketTimeframe,
    MarketWindow,
    NewsArticle,
    NewsCategory,
    NewsClassification,
    NewsDirection,
    NewsSignal,
    NotificationAttempt,
    ProcessingFailure,
    ResearchReport,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.storage import DATABASE_VERSION, SQLiteStorage

OCCURRED_AT = datetime(2026, 2, 2, 15, 30, tzinfo=UTC)
CREATED_AT = datetime(2026, 2, 2, 16, 0, tzinfo=UTC)
SOURCE_DETAILS = SourceDetails(
    provider="fixture-provider",
    source="offline scenario",
    feed="minute-bars",
    retrieved_at=OCCURRED_AT + timedelta(minutes=1),
)
EVENT = Event(
    event_id="event-1",
    ticker="ACME",
    direction=SignalDirection.DOWN,
    category="GUIDANCE",
    importance=SignalImportance.HIGH,
    market_windows=(MarketWindow.ONE_HOUR,),
    current_update=1,
    status=EventStatus.QUEUED,
    created_at=CREATED_AT,
    updated_at=CREATED_AT,
)
MARKET_SIGNAL = MarketSignal(
    signal_id="market-1",
    ticker="ACME",
    occurred_at=OCCURRED_AT,
    importance=SignalImportance.HIGH,
    source_details=SOURCE_DETAILS,
    direction=SignalDirection.DOWN,
    rule="abrupt-decline",
    window=MarketWindow.ONE_HOUR,
    price_decline_ratio=Decimal("0.06"),
    volume_ratio=Decimal("1.7"),
    baseline_price=Decimal("100"),
    observed_price=Decimal("94"),
)
NEWS_SIGNAL = NewsSignal(
    signal_id="news-1",
    ticker="ACME",
    occurred_at=OCCURRED_AT + timedelta(minutes=5),
    importance=SignalImportance.MODERATE,
    source_details=SOURCE_DETAILS,
    category="GUIDANCE",
    direction=SignalDirection.DOWN,
    headline="Acme lowers guidance",
    matched_phrase="lowers guidance",
)
REPORT = ResearchReport(
    report_id="report-1",
    event_id=EVENT.event_id,
    event_update=1,
    ticker="ACME",
    event_occurred_at=OCCURRED_AT,
    created_at=CREATED_AT + timedelta(minutes=2),
    summary="Offline fake research summary.",
    is_fake=True,
)
ATTEMPT = NotificationAttempt(
    attempt_id="attempt-1",
    event_id=EVENT.event_id,
    event_update=1,
    attempted_at=CREATED_AT + timedelta(minutes=3),
    succeeded=False,
    safe_error="offline notifier unavailable",
)
FAILURE = ProcessingFailure(
    failure_id="failure-1",
    event_id=EVENT.event_id,
    event_update=1,
    step=FailureStep.NOTIFICATION,
    retryable=True,
    occurred_at=CREATED_AT + timedelta(minutes=3),
    description="offline notifier unavailable",
)
CLOSED_EVENT = replace(
    EVENT,
    event_id="event-2",
    episode_open=False,
    closed_at=CREATED_AT + timedelta(hours=2),
)
WATCHLIST_BAR = MarketBar(
    ticker="TSLA",
    timeframe=MarketTimeframe.ONE_MINUTE,
    start_at=OCCURRED_AT,
    end_at=OCCURRED_AT + timedelta(minutes=1),
    open=Decimal("250.00"),
    high=Decimal("251.00"),
    low=Decimal("249.50"),
    close=Decimal("250.25"),
    volume=Decimal("15000"),
    is_complete=True,
    provider="fixture-provider",
    feed="minute-bars",
    retrieved_at=OCCURRED_AT + timedelta(seconds=2),
)
SPY_BAR = MarketBar(
    ticker="SPY",
    timeframe=MarketTimeframe.ONE_DAY,
    start_at=datetime(2026, 2, 2, 14, 30, tzinfo=UTC),
    end_at=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
    open=Decimal("500.00"),
    high=Decimal("502.00"),
    low=Decimal("498.00"),
    close=Decimal("499.50"),
    volume=Decimal("80000000"),
    is_complete=True,
    provider="fixture-provider",
    feed="daily-bars",
    retrieved_at=datetime(2026, 2, 2, 21, 5, tzinfo=UTC),
)
DETECTOR_STATE = DetectorState(
    ticker="TSLA",
    rule="abrupt_move",
    window=MarketWindow.ONE_HOUR,
    direction=SignalDirection.DOWN,
    last_emitted_importance=SignalImportance.HIGH,
    updated_at=OCCURRED_AT,
)
CLEAR_DETECTOR_STATE = replace(
    DETECTOR_STATE,
    rule="multi_day_move",
    window=MarketWindow.FIVE_DAYS,
    last_emitted_importance=None,
)
_V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    direction TEXT,
    category TEXT,
    importance TEXT NOT NULL,
    market_windows TEXT NOT NULL,
    current_update INTEGER NOT NULL CHECK (current_update >= 1),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_notified_at TEXT,
    CHECK (direction IS NOT NULL OR category IS NOT NULL)
);

PRAGMA user_version = 1;
"""


def test_setup_is_repeatable_and_models_survive_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "durable-state.sqlite3"

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        storage.initialize()
        assert storage.database_version == DATABASE_VERSION

        storage.save_event(EVENT)
        storage.save_event(CLOSED_EVENT)
        storage.save_signal(
            MARKET_SIGNAL,
            event_id=EVENT.event_id,
            affected_update=1,
        )
        storage.save_signal(
            NEWS_SIGNAL,
            event_id=EVENT.event_id,
            affected_update=1,
        )
        storage.save_report(REPORT)
        storage.save_notification_attempt(ATTEMPT)
        storage.save_failure(FAILURE)
        storage.save_market_bar(WATCHLIST_BAR)
        storage.save_market_bar(SPY_BAR)
        storage.save_detector_state(DETECTOR_STATE)
        storage.save_detector_state(CLEAR_DETECTOR_STATE)

    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()

        assert reopened.database_version == DATABASE_VERSION
        assert reopened.get_event(EVENT.event_id) == EVENT
        assert reopened.get_event(CLOSED_EVENT.event_id) == CLOSED_EVENT
        assert reopened.get_signal(MARKET_SIGNAL.signal_id) == MARKET_SIGNAL
        assert reopened.get_signal(NEWS_SIGNAL.signal_id) == NEWS_SIGNAL
        assert reopened.list_signals(EVENT.event_id) == (
            MARKET_SIGNAL,
            NEWS_SIGNAL,
        )
        assert reopened.get_report(REPORT.report_id) == REPORT
        assert reopened.list_notification_attempts(EVENT.event_id) == (ATTEMPT,)
        assert reopened.list_failures(EVENT.event_id) == (FAILURE,)
        assert reopened.get_market_bar(WATCHLIST_BAR.bar_id) == WATCHLIST_BAR
        assert reopened.get_market_bar(SPY_BAR.bar_id) == SPY_BAR
        assert (
            reopened.get_detector_state(
                DETECTOR_STATE.ticker,
                DETECTOR_STATE.rule,
                DETECTOR_STATE.window,
                DETECTOR_STATE.direction,
            )
            == DETECTOR_STATE
        )
        assert (
            reopened.get_detector_state(
                CLEAR_DETECTOR_STATE.ticker,
                CLEAR_DETECTOR_STATE.rule,
                CLEAR_DETECTOR_STATE.window,
                CLEAR_DETECTOR_STATE.direction,
            )
            == CLEAR_DETECTOR_STATE
        )


def test_source_details_reload_as_an_internal_model(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "source-details.sqlite3") as storage:
        storage.initialize()
        storage.save_event(EVENT)
        storage.save_signal(
            MARKET_SIGNAL,
            event_id=EVENT.event_id,
            affected_update=1,
        )

        reloaded = storage.get_signal(MARKET_SIGNAL.signal_id)

    assert isinstance(reloaded, MarketSignal)
    assert isinstance(reloaded.source_details, SourceDetails)
    assert reloaded.source_details == SOURCE_DETAILS


def test_version_1_database_migrates_and_keeps_events(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-v1.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(_V1_SCHEMA)
        connection.execute(
            """
            INSERT INTO events (
                event_id, ticker, direction, category, importance,
                market_windows, current_update, status, created_at,
                updated_at, last_notified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                EVENT.event_id,
                EVENT.ticker,
                EVENT.direction.value if EVENT.direction is not None else None,
                EVENT.category,
                EVENT.importance.value,
                '["ONE_HOUR"]',
                EVENT.current_update,
                EVENT.status.value,
                EVENT.created_at.isoformat(),
                EVENT.updated_at.isoformat(),
                None,
            ),
        )

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        storage.initialize()

        assert storage.database_version == DATABASE_VERSION
        migrated = storage.get_event(EVENT.event_id)
        assert migrated == EVENT
        assert migrated is not None
        assert migrated.episode_open is True
        assert migrated.closed_at is None

        storage.save_market_bar(WATCHLIST_BAR)
        storage.save_detector_state(DETECTOR_STATE)
        assert storage.get_market_bar(WATCHLIST_BAR.bar_id) == WATCHLIST_BAR
        assert (
            storage.get_detector_state(
                DETECTOR_STATE.ticker,
                DETECTOR_STATE.rule,
                DETECTOR_STATE.window,
                DETECTOR_STATE.direction,
            )
            == DETECTOR_STATE
        )


def test_version_2_database_migrates_explainable_signal_fields(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-v2.sqlite3"
    legacy_signal = replace(
        MARKET_SIGNAL,
        baseline_price=None,
        observed_price=None,
    )
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        storage.save_event(EVENT)
        storage.save_signal(legacy_signal, event_id=EVENT.event_id, affected_update=1)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 2")
        for table, column in (
            ("detector_state", "last_evaluated_at"),
            ("signals", "baseline_price"),
            ("signals", "observed_price"),
            ("signals", "comparison_return_ratio"),
        ):
            connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

    with SQLiteStorage(database_path) as migrated:
        migrated.initialize()
        signal = migrated.get_signal(legacy_signal.signal_id)

        assert migrated.database_version == DATABASE_VERSION
        assert signal == legacy_signal


def test_duplicate_bar_ingest_does_not_multiply_rows(tmp_path: Path) -> None:
    updated = replace(
        WATCHLIST_BAR,
        close=Decimal("248.00"),
        low=Decimal("247.50"),
        retrieved_at=OCCURRED_AT + timedelta(seconds=30),
    )

    with SQLiteStorage(tmp_path / "bars.sqlite3") as storage:
        storage.initialize()
        storage.save_market_bar(WATCHLIST_BAR)
        storage.save_market_bar(WATCHLIST_BAR)
        storage.save_market_bar(updated)
        storage.save_market_bar(SPY_BAR)

        loaded = storage.list_market_bars()
        tsla_minutes = storage.list_market_bars(
            "tsla",
            MarketTimeframe.ONE_MINUTE,
            complete_only=True,
        )

    assert len(loaded) == 2
    assert {bar.bar_id for bar in loaded} == {WATCHLIST_BAR.bar_id, SPY_BAR.bar_id}
    assert tsla_minutes == (updated,)
    assert updated.bar_id == WATCHLIST_BAR.bar_id


def test_bounded_as_of_bar_history_excludes_newer_bars(tmp_path: Path) -> None:
    older = replace(
        WATCHLIST_BAR,
        start_at=WATCHLIST_BAR.start_at - timedelta(minutes=1),
        end_at=WATCHLIST_BAR.end_at - timedelta(minutes=1),
    )
    newer = replace(
        WATCHLIST_BAR,
        start_at=WATCHLIST_BAR.start_at + timedelta(minutes=1),
        end_at=WATCHLIST_BAR.end_at + timedelta(minutes=1),
    )

    with SQLiteStorage(tmp_path / "bounded-bars.sqlite3") as storage:
        storage.initialize()
        for bar in (older, WATCHLIST_BAR, newer):
            storage.save_market_bar(bar)

        loaded = storage.list_market_bars(
            "TSLA",
            MarketTimeframe.ONE_MINUTE,
            complete_only=True,
            through_start_at=OCCURRED_AT,
            limit=1,
        )

    assert loaded == (WATCHLIST_BAR,)


def test_stale_or_incomplete_update_cannot_regress_completed_bar(
    tmp_path: Path,
) -> None:
    stale = replace(
        WATCHLIST_BAR,
        close=Decimal("245"),
        low=Decimal("244"),
        retrieved_at=WATCHLIST_BAR.retrieved_at - timedelta(seconds=1),
    )
    later_incomplete = replace(
        WATCHLIST_BAR,
        is_complete=False,
        retrieved_at=WATCHLIST_BAR.retrieved_at + timedelta(seconds=1),
    )

    with SQLiteStorage(tmp_path / "bar-regression.sqlite3") as storage:
        storage.initialize()
        storage.save_market_bar(WATCHLIST_BAR)
        storage.save_market_bar(stale)
        storage.save_market_bar(later_incomplete)

        assert storage.get_market_bar(WATCHLIST_BAR.bar_id) == WATCHLIST_BAR


NEWS_ARTICLE = NewsArticle(
    provider="alpaca",
    provider_article_id="24843171",
    symbols=("TSLA", "AMD"),
    headline="Tesla reports record deliveries",
    summary="Vehicle deliveries rose.",
    content="Tesla said quarterly deliveries increased.",
    url="https://www.benzinga.com/news/tesla-deliveries",
    canonical_url="https://www.benzinga.com/news/tesla-deliveries",
    source="benzinga",
    created_at=OCCURRED_AT,
    updated_at=OCCURRED_AT + timedelta(seconds=1),
    retrieved_at=OCCURRED_AT + timedelta(minutes=1),
    content_fingerprint="d" * 64,
)
NEWS_CLASSIFICATION = NewsClassification(
    article_id=NEWS_ARTICLE.article_id,
    ticker="TSLA",
    prompt_version="news-classifier-v1",
    model_version="gpt-5.4-nano-2026-03-17",
    status=ClassificationStatus.SUCCEEDED,
    attempted_at=OCCURRED_AT + timedelta(minutes=2),
    relevant=True,
    category=NewsCategory.EARNINGS,
    significant=True,
    direction=NewsDirection.UP,
    importance=SignalImportance.HIGH,
    confidence=0.91,
    rationale="Deliveries beat with a raised outlook.",
)


def test_news_article_and_classification_survive_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "news-state.sqlite3"
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        storage.save_news_article(NEWS_ARTICLE)
        storage.save_news_classification(NEWS_CLASSIFICATION)
        storage.save_news_high_water(
            "alpaca",
            NEWS_ARTICLE.created_at,
            updated_at=NEWS_ARTICLE.retrieved_at,
        )
        storage.record_classifier_call(OCCURRED_AT.date().isoformat())

    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()
        assert reopened.get_news_article(NEWS_ARTICLE.article_id) == NEWS_ARTICLE
        assert (
            reopened.get_news_classification(
                NEWS_ARTICLE.article_id,
                "TSLA",
                prompt_version="news-classifier-v1",
                model_version="gpt-5.4-nano-2026-03-17",
            )
            == NEWS_CLASSIFICATION
        )
        assert reopened.get_news_high_water("alpaca") == NEWS_ARTICLE.created_at
        assert reopened.classifier_call_count(OCCURRED_AT.date().isoformat()) == 1


def test_successful_classification_is_not_overwritten(tmp_path: Path) -> None:
    failed = replace(
        NEWS_CLASSIFICATION,
        status=ClassificationStatus.FAILED,
        relevant=None,
        category=None,
        significant=None,
        direction=None,
        importance=None,
        confidence=None,
        rationale=None,
        safe_error="classifier request failed",
    )
    with SQLiteStorage(tmp_path / "no-overwrite.sqlite3") as storage:
        storage.initialize()
        storage.save_news_article(NEWS_ARTICLE)
        assert storage.save_news_classification(NEWS_CLASSIFICATION) is True
        assert storage.save_news_classification(failed) is False
        loaded = storage.get_news_classification(
            NEWS_ARTICLE.article_id,
            "TSLA",
            prompt_version="news-classifier-v1",
            model_version="gpt-5.4-nano-2026-03-17",
        )
        assert loaded == NEWS_CLASSIFICATION


def test_version_3_database_migrates_news_tables_and_keeps_events(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-v3.sqlite3"
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        storage.save_event(EVENT)
        storage.save_signal(MARKET_SIGNAL, event_id=EVENT.event_id, affected_update=1)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 3")
        for table in (
            "news_articles",
            "news_classifications",
            "news_retrieval_state",
            "classifier_budget",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")

    with SQLiteStorage(database_path) as migrated:
        migrated.initialize()
        migrated.save_news_article(NEWS_ARTICLE)

        assert migrated.database_version == DATABASE_VERSION
        assert migrated.get_event(EVENT.event_id) == EVENT
        assert migrated.get_signal(MARKET_SIGNAL.signal_id) == MARKET_SIGNAL
        assert migrated.get_news_article(NEWS_ARTICLE.article_id) == NEWS_ARTICLE
