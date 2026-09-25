"""Small SQLite persistence boundary for durable event and market state.

Write paths
-----------
Real application code (especially the event manager) must use the *safe
lifecycle* methods. Those methods check version and status, and they update
event progress in one database transaction with the related row.

Safe lifecycle writers (use these in app code):

- ``record_signal`` — attach a signal to an event and save both together
- ``mark_researching`` — claim the research step for the current update
- ``save_report_and_mark_reported`` — save the report and mark ready to notify
- ``save_notification_result`` — save a notify attempt and final/failed status
- ``save_failure_and_mark_failed`` — save a failure and mark the step failed

Free-form writers (``save_event``, ``save_signal``, ``save_report``,
``save_notification_attempt``, ``save_failure``, ``save_market_bar``,
``save_detector_state``) insert or replace rows without enforcing
lifecycle rules. Prefer them only in tests (to seed a specific state)
or inside this module. Do not use them from production pipeline code to
advance an event through research or notification.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Literal
from uuid import uuid4

from investment_assistant.market_data import is_regular_session_minute
from investment_assistant.model_budget import (
    CLASSIFIER_ALLOWANCE,
    POLICY_VERSION,
    RESEARCH_ALLOWANCE,
    RESEARCH_REQUEST_ALLOWANCE,
    SEARCH_FEE,
    token_charge,
)
from investment_assistant.models import (
    ClassificationStatus,
    DetectorState,
    Event,
    EventStatus,
    EvidencePacket,
    EvidenceSnapshot,
    FailureStep,
    LiveReportDetails,
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
    ResearchAttempt,
    ResearchDeferral,
    ResearchReport,
    ResearchToolResult,
    ResearchUsage,
    Signal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
    _as_utc,
)

DATABASE_VERSION = 7

_MARKET_HISTORY_TABLES = """
CREATE TABLE IF NOT EXISTS market_bars (
    bar_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    open_price TEXT NOT NULL,
    high_price TEXT NOT NULL,
    low_price TEXT NOT NULL,
    close_price TEXT NOT NULL,
    volume TEXT NOT NULL,
    is_complete INTEGER NOT NULL CHECK (is_complete IN (0, 1)),
    provider TEXT NOT NULL,
    feed TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE (ticker, timeframe, start_at)
);

CREATE INDEX IF NOT EXISTS market_bars_ticker_timeframe_start_idx
    ON market_bars(ticker, timeframe, start_at);

CREATE TABLE IF NOT EXISTS detector_state (
    ticker TEXT NOT NULL,
    rule TEXT NOT NULL,
    window TEXT NOT NULL,
    direction TEXT NOT NULL,
    last_emitted_importance TEXT,
    updated_at TEXT NOT NULL,
    last_evaluated_at TEXT,
    PRIMARY KEY (ticker, rule, window, direction)
);
"""

_NEWS_TABLES = """
CREATE TABLE IF NOT EXISTS news_articles (
    article_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_article_id TEXT NOT NULL,
    symbols TEXT NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    UNIQUE (provider, provider_article_id)
);

CREATE INDEX IF NOT EXISTS news_articles_canonical_url_idx
    ON news_articles(canonical_url);
CREATE INDEX IF NOT EXISTS news_articles_created_at_idx
    ON news_articles(created_at);
CREATE INDEX IF NOT EXISTS news_articles_updated_at_idx
    ON news_articles(updated_at);

CREATE TABLE IF NOT EXISTS news_classifications (
    article_id TEXT NOT NULL REFERENCES news_articles(article_id),
    ticker TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    relevant INTEGER CHECK (relevant IN (0, 1)),
    category TEXT,
    significant INTEGER CHECK (significant IN (0, 1)),
    direction TEXT,
    importance TEXT,
    confidence TEXT,
    rationale TEXT,
    status TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    safe_error TEXT,
    PRIMARY KEY (article_id, ticker, prompt_version, model_version)
);

CREATE INDEX IF NOT EXISTS news_classifications_attempted_idx
    ON news_classifications(attempted_at);
CREATE INDEX IF NOT EXISTS news_classifications_status_idx
    ON news_classifications(status);

CREATE TABLE IF NOT EXISTS news_retrieval_state (
    provider TEXT PRIMARY KEY,
    high_water_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classifier_budget (
    utc_day TEXT PRIMARY KEY,
    call_count INTEGER NOT NULL CHECK (call_count >= 0)
);
"""

_CORE_TABLES = """
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
    episode_open INTEGER NOT NULL DEFAULT 1 CHECK (episode_open IN (0, 1)),
    closed_at TEXT,
    CHECK (direction IS NOT NULL OR category IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    affected_update INTEGER NOT NULL CHECK (affected_update >= 1),
    signal_type TEXT NOT NULL CHECK (signal_type IN ('MARKET', 'NEWS')),
    ticker TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    importance TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_feed TEXT,
    retrieved_at TEXT NOT NULL,
    direction TEXT,
    market_rule TEXT,
    market_window TEXT,
    price_decline_ratio TEXT,
    volume_ratio TEXT,
    baseline_price TEXT,
    observed_price TEXT,
    comparison_return_ratio TEXT,
    news_category TEXT,
    headline TEXT,
    matched_phrase TEXT,
    article_id TEXT,
    classification_prompt_version TEXT,
    classification_model_version TEXT
);

CREATE INDEX IF NOT EXISTS signals_event_id_idx ON signals(event_id);
CREATE INDEX IF NOT EXISTS events_ticker_direction_idx
    ON events(ticker, direction);
CREATE INDEX IF NOT EXISTS events_ticker_category_idx
    ON events(ticker, category);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    event_update INTEGER NOT NULL CHECK (event_update >= 1),
    ticker TEXT NOT NULL,
    event_occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    is_fake INTEGER NOT NULL CHECK (is_fake IN (0, 1)),
    details TEXT,
    UNIQUE (event_id, event_update)
);

CREATE TABLE IF NOT EXISTS notification_attempts (
    attempt_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    event_update INTEGER NOT NULL CHECK (event_update >= 1),
    attempted_at TEXT NOT NULL,
    succeeded INTEGER NOT NULL CHECK (succeeded IN (0, 1)),
    safe_error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS one_successful_notification_per_update_idx
    ON notification_attempts(event_id, event_update)
    WHERE succeeded = 1;

CREATE TABLE IF NOT EXISTS failures (
    failure_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    event_update INTEGER NOT NULL CHECK (event_update >= 1),
    step TEXT NOT NULL,
    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
    occurred_at TEXT NOT NULL,
    description TEXT NOT NULL
);
"""


_RESEARCH_TABLES = """
CREATE TABLE IF NOT EXISTS research_attempts (
    attempt_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    event_update INTEGER NOT NULL CHECK(event_update >= 1),
    started_at TEXT NOT NULL,
    status TEXT NOT NULL,
    retry_not_before TEXT NOT NULL,
    details TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS research_attempts_start_idx ON research_attempts(started_at);
CREATE INDEX IF NOT EXISTS research_attempts_update_idx
    ON research_attempts(event_id, event_update, started_at);
CREATE TABLE IF NOT EXISTS research_deferrals (
    event_id TEXT NOT NULL REFERENCES events(event_id),
    event_update INTEGER NOT NULL CHECK(event_update >= 1),
    details TEXT NOT NULL,
    PRIMARY KEY(event_id, event_update)
);
CREATE INDEX IF NOT EXISTS research_signal_time_idx ON signals(event_id, occurred_at, signal_id);
"""

_DELIVERY_TABLES = """
CREATE TABLE IF NOT EXISTS notification_deliveries (
    delivery_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    event_update INTEGER NOT NULL CHECK (event_update >= 1),
    destination TEXT NOT NULL CHECK (destination IN ('console', 'discord')),
    destination_fingerprint TEXT,
    state TEXT NOT NULL CHECK (state IN (
        'READY', 'CLAIMED', 'ACKNOWLEDGED', 'SUCCEEDED', 'RETRY_WAIT',
        'PERMANENT_FAILURE', 'UNCERTAIN', 'SUPERSEDED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    next_attempt_at TEXT,
    uncertain_since TEXT,
    resend_not_before TEXT,
    uncertain_resend_used INTEGER NOT NULL DEFAULT 0 CHECK (uncertain_resend_used IN (0, 1)),
    ordinary_count INTEGER NOT NULL DEFAULT 0 CHECK (ordinary_count BETWEEN 0 AND 5),
    active_attempt_id TEXT,
    message_id TEXT,
    completed_at TEXT,
    safe_reason TEXT,
    manual_authorized INTEGER NOT NULL DEFAULT 0 CHECK (manual_authorized IN (0, 1)),
    CHECK ((destination = 'console' AND destination_fingerprint IS NULL)
        OR (destination = 'discord' AND destination_fingerprint IS NOT NULL)),
    CHECK ((state = 'CLAIMED') = (active_attempt_id IS NOT NULL)),
    CHECK (state NOT IN ('ACKNOWLEDGED', 'SUCCEEDED')
        OR destination = 'console' OR message_id IS NOT NULL),
    CHECK ((state = 'SUCCEEDED') = (completed_at IS NOT NULL)),
    CHECK (state != 'UNCERTAIN' OR (uncertain_since IS NOT NULL
        AND resend_not_before IS NOT NULL)),
    UNIQUE (event_id, event_update)
);
CREATE INDEX IF NOT EXISTS notification_deliveries_state_idx
    ON notification_deliveries(state, next_attempt_at, created_at);
CREATE TABLE IF NOT EXISTS notification_submissions (
    attempt_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES notification_deliveries(delivery_id),
    destination_fingerprint TEXT,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    resend INTEGER NOT NULL CHECK (resend IN (0, 1)),
    claimed_at TEXT NOT NULL,
    completed_at TEXT,
    outcome TEXT CHECK (outcome IN (
        'ACKNOWLEDGED', 'DEFINITE_RETRY', 'PERMANENT', 'UNCERTAIN')),
    message_id TEXT,
    safe_reason TEXT,
    CHECK ((outcome IS NULL) = (completed_at IS NULL)),
    UNIQUE (delivery_id, attempt_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_notification_claim_idx
    ON notification_submissions(delivery_id) WHERE outcome IS NULL;
CREATE TABLE IF NOT EXISTS notification_destination_state (
    fingerprint TEXT PRIMARY KEY,
    wait_until TEXT,
    disabled_reason TEXT
);
CREATE TABLE IF NOT EXISTS notification_global_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    wait_until TEXT
);
CREATE TABLE IF NOT EXISTS model_budget_migration_hold (
    utc_day TEXT PRIMARY KEY,
    reason TEXT NOT NULL
);
"""

_BUDGET_TABLES = """
CREATE TABLE IF NOT EXISTS model_budget_runs (
    owner_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('research', 'classifier')),
    utc_day TEXT NOT NULL,
    model_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    reserved_microdollars INTEGER NOT NULL CHECK (reserved_microdollars >= 0),
    charged_microdollars INTEGER NOT NULL CHECK (charged_microdollars >= 0),
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'FINISHED')),
    overrun INTEGER NOT NULL DEFAULT 0 CHECK (overrun IN (0, 1))
);
CREATE INDEX IF NOT EXISTS model_budget_runs_day_idx ON model_budget_runs(utc_day);
CREATE TABLE IF NOT EXISTS model_budget_requests (
    owner_id TEXT NOT NULL REFERENCES model_budget_runs(owner_id),
    request_number INTEGER NOT NULL CHECK (request_number >= 1),
    reserved_microdollars INTEGER NOT NULL,
    charged_microdollars INTEGER NOT NULL,
    search_slots INTEGER NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    search_calls INTEGER,
    state TEXT NOT NULL CHECK (state IN ('STARTED', 'SETTLED')),
    PRIMARY KEY(owner_id, request_number)
);
"""

_SCHEMA = (
    _CORE_TABLES
    + _MARKET_HISTORY_TABLES
    + _NEWS_TABLES
    + _RESEARCH_TABLES
    + _DELIVERY_TABLES
    + _BUDGET_TABLES
    + f"\nPRAGMA user_version = {DATABASE_VERSION};\n"
)

_MIGRATE_V1_TO_V2 = (
    """
ALTER TABLE events ADD COLUMN episode_open INTEGER NOT NULL DEFAULT 1
    CHECK (episode_open IN (0, 1));
ALTER TABLE events ADD COLUMN closed_at TEXT;
"""
    + _MARKET_HISTORY_TABLES
    + "\nPRAGMA user_version = 2;\n"
)


class SQLiteStorage:
    """Persist and reconstruct internal models using one SQLite connection."""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.create_function(
            "is_regular_session_minute",
            1,
            _sqlite_is_regular_session_minute,
            deterministic=True,
        )
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._transaction_depth = 0

    def __enter__(self) -> SQLiteStorage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def initialize(self, *, now: datetime | None = None) -> None:
        """Create or upgrade the layout, or validate the current version."""

        version = self.database_version
        if version not in (0, 1, 2, 3, 4, 5, 6, DATABASE_VERSION):
            raise ValueError(f"unsupported SQLite database version: {version}")
        if version == 0:
            self._run_script_atomically(_SCHEMA)
            return
        if version == 1:
            self._run_script_atomically(_MIGRATE_V1_TO_V2)
            version = 2
        if version == 2:
            self._migrate_v2_to_v3()
            version = 3
        if version == 3:
            self._migrate_v3_to_v4()
            version = 4
        if version == 4:
            self._migrate_v4_to_v5()
            version = 5
        if version == 5:
            self._migrate_v5_to_v6(now or datetime.now(UTC))
            version = 6
        if version == 6:
            self._migrate_v6_to_v7(now or datetime.now(UTC))
        self._run_script_atomically(_SCHEMA)
        self._reconcile_research_attempts()
        self._recover_model_budget_runs()

    def _migrate_v6_to_v7(self, now: datetime) -> None:
        """Remove the automatic-attempt bound from operator submissions."""

        with self.transaction():
            self._connection.execute(
                "ALTER TABLE notification_submissions RENAME TO old_notification_submissions"
            )
            self._connection.execute(
                """CREATE TABLE notification_submissions (
                attempt_id TEXT PRIMARY KEY,
                delivery_id TEXT NOT NULL REFERENCES notification_deliveries(delivery_id),
                destination_fingerprint TEXT,
                attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
                resend INTEGER NOT NULL CHECK (resend IN (0, 1)),
                claimed_at TEXT NOT NULL, completed_at TEXT,
                outcome TEXT CHECK (outcome IN ('ACKNOWLEDGED', 'DEFINITE_RETRY', 'PERMANENT', 'UNCERTAIN')),
                message_id TEXT, safe_reason TEXT,
                CHECK ((outcome IS NULL) = (completed_at IS NULL)),
                UNIQUE (delivery_id, attempt_number))"""
            )
            self._connection.execute(
                """INSERT INTO notification_submissions
                (attempt_id, delivery_id, destination_fingerprint, attempt_number,
                 resend, claimed_at, completed_at, outcome, message_id, safe_reason)
                SELECT s.attempt_id, s.delivery_id, d.destination_fingerprint,
                       s.attempt_number, s.resend, s.claimed_at, s.completed_at,
                       s.outcome, s.message_id, s.safe_reason
                FROM old_notification_submissions s JOIN notification_deliveries d
                ON d.delivery_id = s.delivery_id"""
            )
            self._connection.execute("DROP TABLE old_notification_submissions")
            self._connection.execute(
                "CREATE UNIQUE INDEX one_open_notification_claim_idx ON notification_submissions(delivery_id) WHERE outcome IS NULL"
            )
            for statement in _BUDGET_TABLES.split(";"):
                if statement.strip():
                    self._connection.execute(statement)
            day = _as_utc(now, "now").date().isoformat()
            legacy = self._connection.execute(
                """SELECT 1 FROM research_attempts WHERE substr(started_at, 1, 10) = ?
                UNION SELECT 1 FROM classifier_budget WHERE utc_day = ? AND call_count > 0""",
                (day, day),
            ).fetchone()
            if legacy:
                self._connection.execute(
                    "INSERT OR IGNORE INTO model_budget_migration_hold VALUES (?, 'LEGACY_SPEND_UNKNOWN')",
                    (day,),
                )
            self._connection.execute("PRAGMA user_version = 7")

    def _recover_model_budget_runs(self) -> None:
        """On startup, charge submitted requests and release unused run slots."""

        with self.transaction():
            rows = self._connection.execute(
                "SELECT owner_id FROM model_budget_runs WHERE state = 'ACTIVE'"
            ).fetchall()
            for row in rows:
                self.finish_model_run(str(row["owner_id"]))

    def _reconcile_research_attempts(self) -> None:
        """A saved report is authoritative if older bookkeeping was interrupted."""
        with self.transaction():
            rows = self._connection.execute(
                """SELECT a.details AS attempt, r.details AS report, r.created_at
                FROM research_attempts a JOIN reports r
                ON r.event_id = a.event_id AND r.event_update = a.event_update
                WHERE a.status = 'STARTED' AND r.details IS NOT NULL"""
            ).fetchall()
            for row in rows:
                attempt = ResearchAttempt.model_validate_json(row["attempt"])
                details = LiveReportDetails.model_validate_json(row["report"])
                if details.attempt_id != attempt.attempt_id:
                    continue
                data = attempt.model_dump()
                data.update(
                    status="SUCCEEDED", finished_at=_datetime(row["created_at"])
                )
                self._write_research_attempt(ResearchAttempt.model_validate(data))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group nested storage writes into one atomic commit."""

        if self._transaction_depth > 0:
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
            return

        self._connection.execute("BEGIN")
        self._transaction_depth = 1
        try:
            yield
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()
        finally:
            self._transaction_depth = 0

    def _run_script_atomically(self, script: str) -> None:
        try:
            self._connection.executescript(f"BEGIN IMMEDIATE;\n{script}\nCOMMIT;")
        except BaseException:
            self._connection.rollback()
            raise

    def _migrate_v2_to_v3(self) -> None:
        columns = (
            ("detector_state", "last_evaluated_at", "TEXT"),
            ("signals", "baseline_price", "TEXT"),
            ("signals", "observed_price", "TEXT"),
            ("signals", "comparison_return_ratio", "TEXT"),
        )
        with self.transaction():
            for table, column, definition in columns:
                if not self._table_exists(table) or column in self._column_names(table):
                    continue
                self._connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
            self._connection.execute("PRAGMA user_version = 3")

    def _migrate_v3_to_v4(self) -> None:
        self._run_script_atomically(_NEWS_TABLES + "\nPRAGMA user_version = 4;\n")

    def _migrate_v4_to_v5(self) -> None:
        statements = []
        for table, column in (
            ("reports", "details"),
            ("signals", "article_id"),
            ("signals", "classification_prompt_version"),
            ("signals", "classification_model_version"),
        ):
            if self._table_exists(table) and column not in self._column_names(table):
                statements.append(f"ALTER TABLE {table} ADD COLUMN {column} TEXT;")
        self._run_script_atomically(
            "\n".join(statements)
            + _CORE_TABLES
            + _RESEARCH_TABLES
            + "\nPRAGMA user_version = 5;"
        )

    def _migrate_v5_to_v6(self, now: datetime) -> None:
        """Upgrade delivery history without replaying old console output."""

        day = _as_utc(now, "now").date().isoformat()
        with self.transaction():
            for statement in _DELIVERY_TABLES.split(";"):
                if statement.strip():
                    self._connection.execute(statement)
            self._connection.execute(
                """INSERT OR IGNORE INTO notification_deliveries
                (delivery_id, event_id, event_update, destination, state,
                 created_at, updated_at, completed_at)
                SELECT 'delivery:' || event_id || ':' || event_update,
                       event_id, event_update, 'console', 'SUCCEEDED',
                       attempted_at, attempted_at, attempted_at
                FROM notification_attempts WHERE succeeded = 1"""
            )
            legacy_research = self._connection.execute(
                "SELECT 1 FROM research_attempts WHERE substr(started_at, 1, 10) = ? LIMIT 1",
                (day,),
            ).fetchone()
            legacy_classification = self._connection.execute(
                "SELECT 1 FROM classifier_budget WHERE utc_day = ? AND call_count > 0",
                (day,),
            ).fetchone()
            if legacy_research or legacy_classification:
                self._connection.execute(
                    """INSERT OR IGNORE INTO model_budget_migration_hold
                    (utc_day, reason) VALUES (?, 'LEGACY_SPEND_UNKNOWN')""",
                    (day,),
                )
            self._connection.execute("PRAGMA user_version = 6")

    def _table_exists(self, table: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def _column_names(self, table: str) -> frozenset[str]:
        rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        return frozenset(str(row["name"]) for row in rows)

    def close(self) -> None:
        """Close the owned database connection."""

        self._connection.close()

    @property
    def database_version(self) -> int:
        """Return the initialized SQLite layout version."""

        row = self._connection.execute("PRAGMA user_version").fetchone()
        if row is None:
            raise RuntimeError("could not read SQLite database version")
        return int(row[0])

    def has_signal(self, signal_id: str) -> bool:
        """Return whether a signal ID has already been accepted."""

        row = self._connection.execute(
            "SELECT 1 FROM signals WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        return row is not None

    def save_event(self, event: Event) -> None:
        """Insert or replace the evolving values of one event."""

        with self.transaction():
            self._write_event(event)

    def get_event(self, event_id: str) -> Event | None:
        """Reload an event by ID."""

        row = self._connection.execute(
            "SELECT * FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return None if row is None else _event_from_row(row)

    def list_events(self) -> tuple[Event, ...]:
        """Reload all events in stable creation order."""

        rows = self._connection.execute(
            "SELECT * FROM events ORDER BY created_at, event_id"
        ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def mark_researching(
        self,
        event_id: str,
        event_update: int,
        *,
        updated_at: datetime,
    ) -> Event | None:
        """Persist research start for the current event update."""

        with self.transaction():
            cursor = self._connection.execute(
                """
                UPDATE events
                SET status = ?, updated_at = ?
                WHERE event_id = ? AND current_update = ?
                  AND status IN (?, ?, ?)
                """,
                (
                    EventStatus.RESEARCHING.value,
                    _timestamp(updated_at),
                    event_id,
                    event_update,
                    EventStatus.QUEUED.value,
                    EventStatus.RESEARCHING.value,
                    EventStatus.FAILED.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_event(event_id)

    def find_direction_events(
        self,
        ticker: str,
        direction: SignalDirection,
        *,
        with_market_signal: bool | None = None,
    ) -> tuple[Event, ...]:
        """Find open directional candidates for deterministic grouping."""

        condition = ""
        if with_market_signal is True:
            condition = (
                " AND EXISTS (SELECT 1 FROM signals s "
                "WHERE s.event_id = events.event_id AND s.signal_type = 'MARKET')"
            )
        elif with_market_signal is False:
            condition = (
                " AND NOT EXISTS (SELECT 1 FROM signals s "
                "WHERE s.event_id = events.event_id AND s.signal_type = 'MARKET')"
            )
        rows = self._connection.execute(
            "SELECT * FROM events WHERE ticker = ? AND direction = ?"
            " AND episode_open = 1"
            f"{condition} ORDER BY created_at, event_id",
            (ticker, direction.value),
        ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def close_episode(self, event_id: str, *, closed_at: datetime) -> Event | None:
        """Mark an open episode closed without changing research status."""

        with self.transaction():
            cursor = self._connection.execute(
                """
                UPDATE events
                SET episode_open = 0, closed_at = ?, updated_at = ?
                WHERE event_id = ? AND episode_open = 1
                """,
                (_timestamp(closed_at), _timestamp(closed_at), event_id),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_event(event_id)

    def find_category_events(self, ticker: str, category: str) -> tuple[Event, ...]:
        """Find open category candidates when news has no clear market match."""

        rows = self._connection.execute(
            "SELECT * FROM events "
            "WHERE ticker = ? AND category = ? AND episode_open = 1 "
            "ORDER BY created_at, event_id",
            (ticker, category),
        ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def record_signal(self, signal: Signal, event: Event) -> bool:
        """Atomically save a new signal and its new or updated event."""

        with self.transaction():
            duplicate = self._connection.execute(
                "SELECT 1 FROM signals WHERE signal_id = ?",
                (signal.signal_id,),
            ).fetchone()
            if duplicate is not None:
                return False
            self._write_event(event)
            self._write_signal(signal, event.event_id, event.current_update)
        return True

    def save_signal(
        self,
        signal: Signal,
        *,
        event_id: str,
        affected_update: int,
    ) -> None:
        """Save a signal linked to an already persisted event."""

        with self.transaction():
            self._write_signal(signal, event_id, affected_update)

    def get_signal(self, signal_id: str) -> Signal | None:
        """Reload a market or news signal by ID."""

        row = self._connection.execute(
            "SELECT * FROM signals WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        return None if row is None else _signal_from_row(row)

    def list_signals(self, event_id: str) -> tuple[Signal, ...]:
        """Reload the signals grouped into an event."""

        rows = self._connection.execute(
            "SELECT * FROM signals WHERE event_id = ? ORDER BY occurred_at, signal_id",
            (event_id,),
        ).fetchall()
        return tuple(_signal_from_row(row) for row in rows)

    def get_research_attempt(self, attempt_id: str) -> ResearchAttempt | None:
        row = self._connection.execute(
            "SELECT details FROM research_attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        return None if row is None else ResearchAttempt.model_validate_json(row[0])

    def research_starts_on(self, at: datetime) -> int:
        day = at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        row = self._connection.execute(
            "SELECT COUNT(*) FROM research_attempts WHERE started_at >= ? AND started_at < ?",
            (_timestamp(day), _timestamp(day + timedelta(days=1))),
        ).fetchone()
        return int(row[0])

    def get_research_deferral(
        self, event_id: str, event_update: int
    ) -> ResearchDeferral | None:
        row = self._connection.execute(
            "SELECT details FROM research_deferrals WHERE event_id = ? AND event_update = ?",
            (event_id, event_update),
        ).fetchone()
        return None if row is None else ResearchDeferral.model_validate_json(row[0])

    def _defer_research(self, deferral: ResearchDeferral) -> None:
        self._connection.execute(
            """INSERT INTO research_deferrals(event_id, event_update, details)
            VALUES (?, ?, ?) ON CONFLICT(event_id, event_update) DO UPDATE
            SET details = excluded.details""",
            (deferral.event_id, deferral.event_update, deferral.model_dump_json()),
        )

    def model_budget_used(self, utc_day: str) -> int:
        row = self._connection.execute(
            """SELECT COALESCE(SUM(charged_microdollars + reserved_microdollars), 0)
            FROM model_budget_runs WHERE utc_day = ?""",
            (utc_day,),
        ).fetchone()
        return int(row[0])

    def model_budget_totals(self, utc_day: str) -> tuple[int, int]:
        """Return charged and still reserved microdollars for one UTC day."""

        row = self._connection.execute(
            """SELECT COALESCE(SUM(charged_microdollars), 0),
            COALESCE(SUM(reserved_microdollars), 0)
            FROM model_budget_runs WHERE utc_day = ?""",
            (utc_day,),
        ).fetchone()
        return int(row[0]), int(row[1])

    def notification_backlog(
        self, now: datetime
    ) -> tuple[int, int, int, int, float | None]:
        """Count only current reported updates, including ones not discovered yet."""

        rows = self._connection.execute(
            """SELECT r.created_at, d.state, d.uncertain_resend_used
            FROM events e JOIN reports r
              ON r.event_id = e.event_id AND r.event_update = e.current_update
            LEFT JOIN notification_deliveries d
              ON d.event_id = e.event_id AND d.event_update = e.current_update
            WHERE e.status IN ('REPORTED', 'FAILED')
              AND (d.state IS NULL OR d.state != 'SUCCEEDED')"""
        ).fetchall()
        uncertain = sum(row["state"] == "UNCERTAIN" for row in rows)
        awaiting_resend = sum(
            row["state"] == "UNCERTAIN" and not row["uncertain_resend_used"]
            for row in rows
        )
        failed = sum(
            row["state"] == "PERMANENT_FAILURE"
            or (row["state"] == "UNCERTAIN" and row["uncertain_resend_used"])
            for row in rows
        )
        age = (
            max(
                0.0,
                (
                    now - min(_datetime(row["created_at"]) for row in rows)
                ).total_seconds(),
            )
            if rows
            else None
        )
        return len(rows), uncertain, awaiting_resend, failed, age

    def model_budget_overrun(self, utc_day: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM model_budget_runs WHERE utc_day = ? AND overrun = 1 LIMIT 1",
                (utc_day,),
            ).fetchone()
            is not None
        )

    def _reserve_model_run(
        self,
        owner_id: str,
        kind: Literal["research", "classifier"],
        model_version: str,
        now: datetime,
        allowance: int,
        budget_microdollars: int,
    ) -> bool:
        day = _as_utc(now, "now").date().isoformat()
        if (
            self.model_budget_migration_hold_on(now)
            or self.model_budget_overrun(day)
            or self.model_budget_used(day) + allowance > budget_microdollars
        ):
            return False
        try:
            token_charge(model_version, 0, 0)
        except ValueError:
            return False
        self._connection.execute(
            """INSERT INTO model_budget_runs
            (owner_id, kind, utc_day, model_version, policy_version,
             reserved_microdollars, charged_microdollars, state)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'ACTIVE')""",
            (owner_id, kind, day, model_version, POLICY_VERSION, allowance),
        )
        return True

    def reserve_classifier_call(
        self,
        *,
        now: datetime,
        model_version: str,
        daily_calls: int,
        budget_microdollars: int,
    ) -> str | None:
        """Count and reserve one classifier request in a single write transaction."""

        day = _as_utc(now, "now").date().isoformat()
        with self.transaction():
            self._connection.execute(
                "UPDATE classifier_budget SET call_count = call_count WHERE 0"
            )
            if self.classifier_call_count(day) >= daily_calls:
                return None
            owner_id = f"classifier:{uuid4()}"
            if not self._reserve_model_run(
                owner_id,
                "classifier",
                model_version,
                now,
                CLASSIFIER_ALLOWANCE,
                budget_microdollars,
            ):
                return None
            self._connection.execute(
                """INSERT INTO model_budget_requests
                (owner_id, request_number, reserved_microdollars, charged_microdollars,
                 search_slots, state) VALUES (?, 1, ?, ?, 0, 'STARTED')""",
                (owner_id, CLASSIFIER_ALLOWANCE, CLASSIFIER_ALLOWANCE),
            )
            self.record_classifier_call(day)
            return owner_id

    def start_research_request(self, owner_id: str, *, search_slots: int) -> int:
        """Record a model request and its granted search allowance before I/O."""

        with self.transaction():
            run = self._connection.execute(
                "SELECT * FROM model_budget_runs WHERE owner_id = ? AND kind = 'research' AND state = 'ACTIVE'",
                (owner_id,),
            ).fetchone()
            if run is None or not 0 <= search_slots <= 3:
                raise ValueError("research budget run unavailable")
            used = self._connection.execute(
                """SELECT count(*), COALESCE(sum(COALESCE(search_calls, search_slots)), 0)
                FROM model_budget_requests WHERE owner_id = ?""",
                (owner_id,),
            ).fetchone()
            if int(used[0]) >= 5 or int(used[1]) + search_slots > 3:
                raise ValueError("research request allowance exhausted")
            number = int(used[0]) + 1
            allowance = RESEARCH_REQUEST_ALLOWANCE + search_slots * SEARCH_FEE
            self._connection.execute(
                """INSERT INTO model_budget_requests
                (owner_id, request_number, reserved_microdollars, charged_microdollars,
                 search_slots, state) VALUES (?, ?, ?, ?, ?, 'STARTED')""",
                (owner_id, number, allowance, allowance, search_slots),
            )
            return number

    def settle_model_request(
        self,
        owner_id: str,
        number: int,
        *,
        usage: tuple[int, int] | None,
        search_calls: int | None = None,
    ) -> None:
        """Settle once; missing usage/search counts keep their full allocation."""

        with self.transaction():
            request = self._connection.execute(
                "SELECT * FROM model_budget_requests WHERE owner_id = ? AND request_number = ?",
                (owner_id, number),
            ).fetchone()
            run = self._connection.execute(
                "SELECT * FROM model_budget_runs WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()
            if request is None or run is None or request["state"] == "SETTLED":
                return
            known_search = search_calls is not None and search_calls >= 0
            charge = int(request["reserved_microdollars"])
            if usage is not None:
                charge = token_charge(str(run["model_version"]), *usage)
                if run["kind"] == "research":
                    charge += (
                        int(search_calls)
                        if known_search and search_calls is not None
                        else int(request["search_slots"])
                    ) * SEARCH_FEE
            self._connection.execute(
                """UPDATE model_budget_requests SET charged_microdollars = ?,
                input_tokens = ?, output_tokens = ?, search_calls = ?, state = 'SETTLED'
                WHERE owner_id = ? AND request_number = ?""",
                (
                    charge,
                    usage[0] if usage else None,
                    usage[1] if usage else None,
                    search_calls if known_search else None,
                    owner_id,
                    number,
                ),
            )
            if charge > int(request["reserved_microdollars"]) or (
                search_calls is not None and search_calls > int(request["search_slots"])
            ):
                self._connection.execute(
                    "UPDATE model_budget_runs SET overrun = 1 WHERE owner_id = ?",
                    (owner_id,),
                )

    def finish_model_run(self, owner_id: str) -> None:
        with self.transaction():
            run = self._connection.execute(
                "SELECT state, reserved_microdollars FROM model_budget_runs WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()
            if run is None or run["state"] == "FINISHED":
                return
            charged = int(
                self._connection.execute(
                    "SELECT COALESCE(sum(charged_microdollars), 0) FROM model_budget_requests WHERE owner_id = ?",
                    (owner_id,),
                ).fetchone()[0]
            )
            self._connection.execute(
                """UPDATE model_budget_runs SET state = 'FINISHED', reserved_microdollars = 0,
                charged_microdollars = ?, overrun = CASE WHEN ? > reserved_microdollars
                THEN 1 ELSE overrun END WHERE owner_id = ?""",
                (charged, charged, owner_id),
            )

    def reserve_research_attempt(
        self,
        event_id: str,
        event_update: int,
        *,
        now: datetime,
        model_version: str,
        prompt_version: str,
        has_api_key: bool,
        daily_starts: int = 20,
        budget_microdollars: int | None = None,
    ) -> ResearchAttempt | None:
        """Reserve a durable start before I/O; deferrals never consume a start."""
        now = _as_utc(now, "now")
        with self.transaction():
            # Acquire SQLite's write lock before reading the shared daily ledger.
            self._connection.execute(
                "UPDATE research_attempts SET status = status WHERE 0"
            )
            event = self.get_event(event_id)
            if (
                event is None
                or event.current_update != event_update
                or event.status
                not in (EventStatus.QUEUED, EventStatus.RESEARCHING, EventStatus.FAILED)
                or self.get_report_for_update(event_id, event_update) is not None
            ):
                return None
            reason: (
                Literal[
                    "MISSING_KEY",
                    "DAILY_BUDGET",
                    "MODEL_BUDGET",
                    "UNKNOWN_PRICING",
                    "MIGRATION_HOLD",
                    "ESTIMATION_OVERRUN",
                ]
                | None
            ) = None
            day = now.date().isoformat()
            if not has_api_key:
                reason = "MISSING_KEY"
            elif self.model_budget_migration_hold_on(now):
                reason = "MIGRATION_HOLD"
            elif self.research_starts_on(now) >= daily_starts:
                reason = "DAILY_BUDGET"
            elif budget_microdollars is not None:
                try:
                    token_charge(model_version, 0, 0)
                except ValueError:
                    reason = "UNKNOWN_PRICING"
                if reason is None and self.model_budget_overrun(day):
                    reason = "ESTIMATION_OVERRUN"
                if (
                    reason is None
                    and self.model_budget_used(day) + RESEARCH_ALLOWANCE
                    > budget_microdollars
                ):
                    reason = "MODEL_BUDGET"
            if reason is not None:
                retry = (
                    None
                    if not has_api_key
                    else now.replace(hour=0, minute=0, second=0, microsecond=0)
                    + timedelta(days=1)
                )
                old = self.get_research_deferral(event_id, event_update)
                if old is None or old.reason != reason or old.retry_not_before != retry:
                    self._defer_research(
                        ResearchDeferral(
                            event_id=event_id,
                            event_update=event_update,
                            reason=reason,
                            deferred_at=now,
                            retry_not_before=retry,
                        )
                    )
                return None
            row = self._connection.execute(
                """SELECT retry_not_before FROM research_attempts
                WHERE event_id = ? AND event_update = ?
                ORDER BY started_at DESC, attempt_id DESC LIMIT 1""",
                (event_id, event_update),
            ).fetchone()
            if row is not None and _datetime(row[0]) > now:
                return None
            attempt = ResearchAttempt(
                attempt_id=f"research:{uuid4()}",
                event_id=event_id,
                event_update=event_update,
                started_at=now,
                retry_not_before=now + timedelta(minutes=5),
                model_version=model_version,
                prompt_version=prompt_version,
            )
            if budget_microdollars is not None and not self._reserve_model_run(
                attempt.attempt_id,
                "research",
                model_version,
                now,
                RESEARCH_ALLOWANCE,
                budget_microdollars,
            ):
                raise RuntimeError("model budget admission changed during reservation")
            self._connection.execute(
                """INSERT INTO research_attempts
                (attempt_id, event_id, event_update, started_at, status, retry_not_before, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    attempt.attempt_id,
                    event_id,
                    event_update,
                    _timestamp(now),
                    attempt.status,
                    _timestamp(attempt.retry_not_before),
                    attempt.model_dump_json(),
                ),
            )
            self._connection.execute(
                "DELETE FROM research_deferrals WHERE event_id = ? AND event_update = ?",
                (event_id, event_update),
            )
            self.mark_researching(event_id, event_update, updated_at=now)
            return attempt

    def next_research_event(self, *, now: datetime) -> Event | None:
        """Choose unattempted work first, then the least recently attempted update."""
        row = self._connection.execute(
            """SELECT e.* FROM events e
            LEFT JOIN research_attempts a ON a.attempt_id = (
                SELECT attempt_id FROM research_attempts
                WHERE event_id = e.event_id AND event_update = e.current_update
                ORDER BY started_at DESC, attempt_id DESC LIMIT 1)
            WHERE e.status IN ('QUEUED', 'RESEARCHING', 'FAILED')
              AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.event_id = e.event_id
                              AND r.event_update = e.current_update)
              AND (a.retry_not_before IS NULL OR a.retry_not_before <= ?)
            ORDER BY a.started_at IS NOT NULL, a.started_at, e.created_at, e.event_id LIMIT 1""",
            (_timestamp(_as_utc(now, "now")),),
        ).fetchone()
        return None if row is None else _event_from_row(row)

    def _write_research_attempt(self, attempt: ResearchAttempt) -> None:
        self._connection.execute(
            """UPDATE research_attempts SET status = ?, retry_not_before = ?, details = ?
            WHERE attempt_id = ?""",
            (
                attempt.status,
                _timestamp(attempt.retry_not_before),
                attempt.model_dump_json(),
                attempt.attempt_id,
            ),
        )

    def save_research_evidence(
        self,
        attempt_id: str,
        *,
        packet: EvidencePacket,
        evidence: tuple[EvidenceSnapshot, ...] = (),
        usage: ResearchUsage | None = None,
        tool_results: tuple[ResearchToolResult, ...] = (),
    ) -> None:
        """Store only normalized bounded input, never provider dumps or reasoning."""
        with self.transaction():
            attempt = self.get_research_attempt(attempt_id)
            if attempt is None or attempt.status != "STARTED":
                raise ValueError("research attempt is not active")
            data = attempt.model_dump()
            data.update(
                packet=packet,
                evidence=evidence,
                usage=usage or attempt.usage,
                tool_results=tool_results,
            )
            self._write_research_attempt(ResearchAttempt.model_validate(data))

    def fail_research_attempt(self, attempt_id: str, *, finished_at: datetime) -> None:
        """Finish with application-owned safe text and five-minute retry spacing."""
        with self.transaction():
            attempt = self.get_research_attempt(attempt_id)
            if attempt is None or attempt.status != "STARTED":
                raise ValueError("research attempt is not active")
            data = attempt.model_dump()
            data.update(
                status="FAILED",
                finished_at=finished_at,
                retry_not_before=finished_at + timedelta(minutes=5),
                safe_error="Research failed; the current update may be retried.",
            )
            self._write_research_attempt(ResearchAttempt.model_validate(data))
            self._connection.execute(
                """UPDATE events SET status = ?, updated_at = ?
                WHERE event_id = ? AND current_update = ? AND status = 'RESEARCHING'
                AND NOT EXISTS (SELECT 1 FROM reports WHERE event_id = ? AND event_update = ?)""",
                (
                    EventStatus.FAILED.value,
                    _timestamp(finished_at),
                    attempt.event_id,
                    attempt.event_update,
                    attempt.event_id,
                    attempt.event_update,
                ),
            )

    def read_research_signals(
        self, event: Event, *, as_of: datetime
    ) -> tuple[tuple[Signal, ...], int]:
        """Bound reads while retaining evidence establishing severity and windows."""
        where = "event_id = ? AND affected_update <= ? AND occurred_at <= ? AND retrieved_at <= ?"
        args = (
            event.event_id,
            event.current_update,
            _timestamp(as_of),
            _timestamp(as_of),
        )
        total = int(
            self._connection.execute(
                f"SELECT COUNT(*) FROM signals WHERE {where}", args
            ).fetchone()[0]
        )
        rows = self._connection.execute(
            f"SELECT * FROM signals WHERE {where} ORDER BY occurred_at DESC, signal_id DESC LIMIT 50",
            args,
        ).fetchall()
        mandatory: dict[str, Signal] = {}
        for condition, value in [
            ("importance", event.importance.value),
            *(("market_window", w.value) for w in event.market_windows),
        ]:
            row = self._connection.execute(
                f"SELECT * FROM signals WHERE {where} AND {condition} = ? ORDER BY occurred_at, signal_id LIMIT 1",
                (*args, value),
            ).fetchone()
            if row is not None:
                signal = _signal_from_row(row)
                mandatory[signal.signal_id] = signal
        selected = dict(mandatory)
        for row in rows:
            if len(selected) >= 50:
                break
            signal = _signal_from_row(row)
            selected[signal.signal_id] = signal
        return tuple(
            sorted(selected.values(), key=lambda s: (s.occurred_at, s.signal_id))
        ), total

    def read_research_bars(
        self,
        ticker: str,
        timeframe: MarketTimeframe,
        *,
        as_of: datetime,
        end_at: datetime,
    ) -> tuple[MarketBar, ...]:
        rows = self._connection.execute(
            """SELECT * FROM market_bars WHERE ticker = ? AND timeframe = ?
            AND is_complete = 1 AND end_at <= ? AND retrieved_at <= ?
            AND (timeframe != '1Min' OR is_regular_session_minute(start_at) = 1)
            ORDER BY end_at DESC, bar_id DESC LIMIT ?""",
            (
                ticker,
                timeframe.value,
                _timestamp(min(as_of, end_at)),
                _timestamp(as_of),
                25 if timeframe == MarketTimeframe.ONE_DAY else 60,
            ),
        ).fetchall()
        return tuple(_market_bar_from_row(row) for row in reversed(rows))

    def previous_research_report(
        self, event: Event, *, as_of: datetime
    ) -> ResearchReport | None:
        row = self._connection.execute(
            """SELECT * FROM reports WHERE event_id = ? AND event_update < ? AND created_at <= ?
            ORDER BY event_update DESC LIMIT 1""",
            (event.event_id, event.current_update, _timestamp(as_of)),
        ).fetchone()
        return None if row is None else _report_from_row(row)

    def save_report(self, report: ResearchReport) -> None:
        """Persist one report for a specific event update."""

        if not report.is_fake:
            raise ValueError("live reports require the atomic lifecycle save")
        with self.transaction():
            self._write_report(report)

    def save_report_and_mark_reported(
        self,
        report: ResearchReport,
        *,
        updated_at: datetime,
    ) -> bool:
        """Atomically save a current report and mark it ready for delivery."""

        with self.transaction():
            current = self._connection.execute(
                """
                SELECT 1 FROM events
                WHERE event_id = ? AND current_update = ? AND status = ?
                """,
                (
                    report.event_id,
                    report.event_update,
                    EventStatus.RESEARCHING.value,
                ),
            ).fetchone()
            if current is None:
                return False
            if report.details is not None:
                self._complete_research_attempt(report, finished_at=updated_at)
            self._write_report(report)
            self._connection.execute(
                """
                UPDATE events
                SET status = ?, updated_at = ?
                WHERE event_id = ? AND current_update = ?
                """,
                (
                    EventStatus.REPORTED.value,
                    _timestamp(updated_at),
                    report.event_id,
                    report.event_update,
                ),
            )
        return True

    def _complete_research_attempt(
        self, report: ResearchReport, *, finished_at: datetime
    ) -> None:
        from investment_assistant.research import create_live_report

        assert report.details is not None
        details = report.details
        attempt = self.get_research_attempt(details.attempt_id)
        if attempt is None or attempt.status != "STARTED" or attempt.packet is None:
            raise ValueError("live report requires an active attempt with evidence")
        expected = create_live_report(
            attempt.packet,
            details.analysis,
            attempt_id=attempt.attempt_id,
            created_at=report.created_at,
            model_version=attempt.model_version,
            prompt_version=attempt.prompt_version,
            usage=attempt.usage,
            evidence=attempt.evidence,
        )
        if expected != report:
            raise ValueError("report does not match persisted research evidence")
        data = attempt.model_dump()
        data.update(status="SUCCEEDED", finished_at=finished_at)
        self._write_research_attempt(ResearchAttempt.model_validate(data))

    def get_report(self, report_id: str) -> ResearchReport | None:
        """Reload a report by ID."""

        row = self._connection.execute(
            "SELECT * FROM reports WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        return None if row is None else _report_from_row(row)

    def get_report_for_update(
        self,
        event_id: str,
        event_update: int,
    ) -> ResearchReport | None:
        """Reload the report for one event update."""

        row = self._connection.execute(
            "SELECT * FROM reports WHERE event_id = ? AND event_update = ?",
            (event_id, event_update),
        ).fetchone()
        return None if row is None else _report_from_row(row)

    def list_reports(self, event_id: str) -> tuple[ResearchReport, ...]:
        """Reload all reports for an event in update order."""

        rows = self._connection.execute(
            "SELECT * FROM reports WHERE event_id = ? ORDER BY event_update",
            (event_id,),
        ).fetchall()
        return tuple(_report_from_row(row) for row in rows)

    def save_notification_attempt(self, attempt: NotificationAttempt) -> None:
        """Persist one notification attempt."""

        with self.transaction():
            self._write_notification_attempt(attempt)

    def save_notification_result(
        self,
        attempt: NotificationAttempt,
        *,
        failure: ProcessingFailure | None,
        updated_at: datetime,
    ) -> bool:
        """Atomically save an attempt and its resulting event state."""

        if attempt.succeeded == (failure is not None):
            raise ValueError("failed attempts require one failure record")
        if failure is not None and (
            failure.event_id != attempt.event_id
            or failure.event_update != attempt.event_update
            or failure.step is not FailureStep.NOTIFICATION
        ):
            raise ValueError("notification failure must match its attempt")
        with self.transaction():
            current = self._connection.execute(
                """
                SELECT 1 FROM events
                WHERE event_id = ? AND current_update = ?
                  AND status IN (?, ?)
                  AND EXISTS (
                      SELECT 1 FROM reports
                      WHERE reports.event_id = events.event_id
                        AND reports.event_update = events.current_update
                  )
                """,
                (
                    attempt.event_id,
                    attempt.event_update,
                    EventStatus.REPORTED.value,
                    EventStatus.FAILED.value,
                ),
            ).fetchone()
            if current is None:
                return False
            self._write_notification_attempt(attempt)
            if failure is None:
                self._connection.execute(
                    """INSERT OR IGNORE INTO notification_deliveries
                    (delivery_id, event_id, event_update, destination, state,
                     created_at, updated_at, completed_at)
                    VALUES (?, ?, ?, 'console', 'SUCCEEDED', ?, ?, ?)""",
                    (
                        f"delivery:{attempt.event_id}:{attempt.event_update}",
                        attempt.event_id,
                        attempt.event_update,
                        _timestamp(attempt.attempted_at),
                        _timestamp(updated_at),
                        _timestamp(updated_at),
                    ),
                )
                self._connection.execute(
                    """
                    UPDATE events
                    SET status = ?, updated_at = ?, last_notified_at = ?
                    WHERE event_id = ? AND current_update = ?
                    """,
                    (
                        EventStatus.NOTIFIED.value,
                        _timestamp(updated_at),
                        _timestamp(updated_at),
                        attempt.event_id,
                        attempt.event_update,
                    ),
                )
            else:
                self._write_failure(failure)
                self._connection.execute(
                    """
                    UPDATE events
                    SET status = ?, updated_at = ?
                    WHERE event_id = ? AND current_update = ?
                    """,
                    (
                        EventStatus.FAILED.value,
                        _timestamp(updated_at),
                        attempt.event_id,
                        attempt.event_update,
                    ),
                )
        return True

    def list_notification_attempts(
        self,
        event_id: str,
    ) -> tuple[NotificationAttempt, ...]:
        """Reload notification attempts for an event."""

        rows = self._connection.execute(
            "SELECT * FROM notification_attempts WHERE event_id = ? "
            "ORDER BY attempted_at, rowid",
            (event_id,),
        ).fetchall()
        return tuple(
            NotificationAttempt(
                attempt_id=str(row["attempt_id"]),
                event_id=str(row["event_id"]),
                event_update=int(row["event_update"]),
                attempted_at=_datetime(row["attempted_at"]),
                succeeded=bool(row["succeeded"]),
                safe_error=_optional_text(row["safe_error"]),
            )
            for row in rows
        )

    def has_external_delivery(self, event_id: str, event_update: int) -> bool:
        """Keep prior external work out of the console fallback path."""

        row = self._connection.execute(
            """SELECT 1 FROM notification_deliveries
            WHERE event_id = ? AND event_update = ? AND destination = 'discord'
              AND state != 'SUPERSEDED'""",
            (event_id, event_update),
        ).fetchone()
        return row is not None

    def save_failure(self, failure: ProcessingFailure) -> None:
        """Persist one safe processing failure."""

        with self.transaction():
            self._write_failure(failure)

    def save_failure_and_mark_failed(
        self,
        failure: ProcessingFailure,
        *,
        updated_at: datetime,
    ) -> bool:
        """Atomically save a current processing failure and failed state."""

        with self.transaction():
            current = self._connection.execute(
                "SELECT 1 FROM events WHERE event_id = ? AND current_update = ?",
                (failure.event_id, failure.event_update),
            ).fetchone()
            if current is None:
                return False
            self._write_failure(failure)
            self._connection.execute(
                """
                UPDATE events
                SET status = ?, updated_at = ?
                WHERE event_id = ? AND current_update = ?
                """,
                (
                    EventStatus.FAILED.value,
                    _timestamp(updated_at),
                    failure.event_id,
                    failure.event_update,
                ),
            )
        return True

    def list_failures(self, event_id: str) -> tuple[ProcessingFailure, ...]:
        """Reload failures for an event."""

        rows = self._connection.execute(
            "SELECT * FROM failures WHERE event_id = ? ORDER BY occurred_at, rowid",
            (event_id,),
        ).fetchall()
        return tuple(_failure_from_row(row) for row in rows)

    def get_latest_failure(
        self,
        event_id: str,
        event_update: int,
    ) -> ProcessingFailure | None:
        """Reload the newest failure for one event update."""

        row = self._connection.execute(
            """
            SELECT * FROM failures
            WHERE event_id = ? AND event_update = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (event_id, event_update),
        ).fetchone()
        return None if row is None else _failure_from_row(row)

    def save_market_bar(self, bar: MarketBar) -> None:
        """Insert or replace one market bar by its stable identity."""

        with self.transaction():
            self._write_market_bar(bar)

    def get_market_bar(self, bar_id: str) -> MarketBar | None:
        """Reload one market bar by stable identity."""

        row = self._connection.execute(
            "SELECT * FROM market_bars WHERE bar_id = ?",
            (bar_id,),
        ).fetchone()
        return None if row is None else _market_bar_from_row(row)

    def list_market_bars(
        self,
        ticker: str | None = None,
        timeframe: MarketTimeframe | None = None,
        *,
        complete_only: bool = False,
        regular_session_only: bool = False,
        start_at_or_after: datetime | None = None,
        through_start_at: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[MarketBar, ...]:
        """Reload a bounded, optionally as-of history in start time order."""

        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")

        conditions: list[str] = []
        params: list[object] = []
        if ticker is not None:
            conditions.append("ticker = ?")
            params.append(ticker.strip().upper())
        if timeframe is not None:
            conditions.append("timeframe = ?")
            params.append(timeframe.value)
        if complete_only:
            conditions.append("is_complete = 1")
        if regular_session_only:
            conditions.append("is_regular_session_minute(start_at) = 1")
        if start_at_or_after is not None:
            conditions.append("start_at >= ?")
            params.append(_timestamp(start_at_or_after))
        if through_start_at is not None:
            conditions.append("start_at <= ?")
            params.append(_timestamp(through_start_at))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        order = "DESC" if limit is not None else "ASC"
        limit_sql = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            params.append(limit)
        rows = self._connection.execute(
            f"SELECT * FROM market_bars{where} "
            f"ORDER BY start_at {order}, bar_id {order}{limit_sql}",
            params,
        ).fetchall()
        if limit is not None:
            rows.reverse()
        return tuple(_market_bar_from_row(row) for row in rows)

    def save_detector_state(self, state: DetectorState) -> None:
        """Insert or replace detector baseline state for one key."""

        with self.transaction():
            self._write_detector_state(state)

    def get_detector_state(
        self,
        ticker: str,
        rule: str,
        window: MarketWindow,
        direction: SignalDirection,
    ) -> DetectorState | None:
        """Reload detector state for one ticker, rule, window, and direction."""

        row = self._connection.execute(
            """
            SELECT * FROM detector_state
            WHERE ticker = ? AND rule = ? AND window = ? AND direction = ?
            """,
            (ticker.strip().upper(), rule, window.value, direction.value),
        ).fetchone()
        return None if row is None else _detector_state_from_row(row)

    def list_detector_states(
        self,
        ticker: str,
        direction: SignalDirection | None = None,
    ) -> tuple[DetectorState, ...]:
        """Reload detector keys for a ticker, optionally one direction."""

        if direction is None:
            rows = self._connection.execute(
                """
                SELECT * FROM detector_state
                WHERE ticker = ?
                ORDER BY rule, window, direction
                """,
                (ticker.strip().upper(),),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                SELECT * FROM detector_state
                WHERE ticker = ? AND direction = ?
                ORDER BY rule, window
                """,
                (ticker.strip().upper(), direction.value),
            ).fetchall()
        return tuple(_detector_state_from_row(row) for row in rows)

    def save_news_article(self, article: NewsArticle) -> None:
        """Insert or revise one normalized news article."""

        with self.transaction():
            self._write_news_article(article)

    def get_news_article(self, article_id: str) -> NewsArticle | None:
        """Reload one article by stable identity."""

        row = self._connection.execute(
            "SELECT * FROM news_articles WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        return None if row is None else _news_article_from_row(row)

    def canonical_url_is_processed(
        self,
        canonical_url: str,
        ticker: str,
        *,
        excluding_article_id: str,
        prompt_version: str,
        model_version: str,
    ) -> bool:
        """Return True when another article with this URL was already decided."""

        row = self._connection.execute(
            """
            SELECT 1 FROM news_articles a
            INNER JOIN news_classifications c
                ON c.article_id = a.article_id
            WHERE a.canonical_url = ?
              AND a.article_id != ?
              AND c.ticker = ?
              AND c.prompt_version = ?
              AND c.model_version = ?
              AND c.status IN (?, ?, ?)
            LIMIT 1
            """,
            (
                canonical_url,
                excluding_article_id,
                ticker.strip().upper(),
                prompt_version,
                model_version,
                ClassificationStatus.SUCCEEDED.value,
                ClassificationStatus.FILTERED.value,
                ClassificationStatus.FAILED.value,
            ),
        ).fetchone()
        return row is not None

    def list_news_articles_since(self, start: datetime) -> tuple[NewsArticle, ...]:
        """Reload articles created or updated at or after ``start``, oldest first."""

        bound = _timestamp(start)
        rows = self._connection.execute(
            """
            SELECT * FROM news_articles
            WHERE created_at >= ? OR updated_at >= ?
            ORDER BY created_at, article_id
            """,
            (bound, bound),
        ).fetchall()
        return tuple(_news_article_from_row(row) for row in rows)

    def save_news_classification(self, classification: NewsClassification) -> bool:
        """Save a classification unless a successful one already exists."""

        with self.transaction():
            existing = self._connection.execute(
                """
                SELECT status FROM news_classifications
                WHERE article_id = ? AND ticker = ?
                  AND prompt_version = ? AND model_version = ?
                """,
                (
                    classification.article_id,
                    classification.ticker,
                    classification.prompt_version,
                    classification.model_version,
                ),
            ).fetchone()
            if (
                existing is not None
                and str(existing["status"]) == ClassificationStatus.SUCCEEDED.value
            ):
                return False
            self._write_news_classification(classification)
        return True

    def get_news_classification(
        self,
        article_id: str,
        ticker: str,
        *,
        prompt_version: str,
        model_version: str,
    ) -> NewsClassification | None:
        """Reload one article/ticker/prompt/model classification."""

        row = self._connection.execute(
            """
            SELECT * FROM news_classifications
            WHERE article_id = ? AND ticker = ?
              AND prompt_version = ? AND model_version = ?
            """,
            (article_id, ticker.strip().upper(), prompt_version, model_version),
        ).fetchone()
        return None if row is None else _news_classification_from_row(row)

    def list_news_classifications(
        self,
        article_id: str,
    ) -> tuple[NewsClassification, ...]:
        """Reload classifications for one article."""

        rows = self._connection.execute(
            """
            SELECT * FROM news_classifications
            WHERE article_id = ?
            ORDER BY ticker, attempted_at
            """,
            (article_id,),
        ).fetchall()
        return tuple(_news_classification_from_row(row) for row in rows)

    def get_news_high_water(self, provider: str) -> datetime | None:
        """Return the durable news retrieval high-water mark."""

        row = self._connection.execute(
            "SELECT high_water_at FROM news_retrieval_state WHERE provider = ?",
            (provider,),
        ).fetchone()
        return None if row is None else _datetime(row["high_water_at"])

    def save_news_high_water(
        self,
        provider: str,
        high_water_at: datetime,
        *,
        updated_at: datetime,
    ) -> None:
        """Persist the retrieval high-water mark after accepted articles."""

        with self.transaction():
            self._connection.execute(
                """
                INSERT INTO news_retrieval_state (
                    provider, high_water_at, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    high_water_at = excluded.high_water_at,
                    updated_at = excluded.updated_at
                """,
                (provider, _timestamp(high_water_at), _timestamp(updated_at)),
            )

    def classifier_call_count(self, utc_day: str) -> int:
        """Return how many classifier calls have been recorded for a UTC day."""

        row = self._connection.execute(
            "SELECT call_count FROM classifier_budget WHERE utc_day = ?",
            (utc_day,),
        ).fetchone()
        return 0 if row is None else int(row["call_count"])

    def model_budget_migration_hold_on(self, now: datetime) -> bool:
        """Legacy model calls have unknown spend for their UTC migration day."""

        day = _as_utc(now, "now").date().isoformat()
        return (
            self._connection.execute(
                "SELECT 1 FROM model_budget_migration_hold WHERE utc_day = ?",
                (day,),
            ).fetchone()
            is not None
        )

    def record_classifier_call(self, utc_day: str) -> int:
        """Increment the UTC-day classifier counter and return the new count."""

        with self.transaction():
            self._connection.execute(
                """
                INSERT INTO classifier_budget (utc_day, call_count)
                VALUES (?, 1)
                ON CONFLICT(utc_day) DO UPDATE SET
                    call_count = call_count + 1
                """,
                (utc_day,),
            )
            row = self._connection.execute(
                "SELECT call_count FROM classifier_budget WHERE utc_day = ?",
                (utc_day,),
            ).fetchone()
        if row is None:
            raise RuntimeError("classifier budget row missing after increment")
        return int(row["call_count"])

    def _write_event(self, event: Event) -> None:
        self._connection.execute(
            """
            INSERT INTO events (
                event_id, ticker, direction, category, importance,
                market_windows, current_update, status, created_at,
                updated_at, last_notified_at, episode_open, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                ticker = excluded.ticker,
                direction = excluded.direction,
                category = excluded.category,
                importance = excluded.importance,
                market_windows = excluded.market_windows,
                current_update = excluded.current_update,
                status = excluded.status,
                updated_at = excluded.updated_at,
                last_notified_at = excluded.last_notified_at,
                episode_open = excluded.episode_open,
                closed_at = excluded.closed_at
            """,
            (
                event.event_id,
                event.ticker,
                None if event.direction is None else event.direction.value,
                event.category,
                event.importance.value,
                json.dumps([window.value for window in event.market_windows]),
                event.current_update,
                event.status.value,
                _timestamp(event.created_at),
                _timestamp(event.updated_at),
                (
                    None
                    if event.last_notified_at is None
                    else _timestamp(event.last_notified_at)
                ),
                int(event.episode_open),
                None if event.closed_at is None else _timestamp(event.closed_at),
            ),
        )

    def _write_signal(
        self,
        signal: Signal,
        event_id: str,
        affected_update: int,
    ) -> None:
        common_values = (
            signal.signal_id,
            event_id,
            affected_update,
            signal.ticker,
            _timestamp(signal.occurred_at),
            signal.importance.value,
            signal.source_details.provider,
            signal.source_details.source,
            signal.source_details.feed,
            _timestamp(signal.source_details.retrieved_at),
        )
        if isinstance(signal, MarketSignal):
            specific_values: tuple[object, ...] = (
                "MARKET",
                signal.direction.value,
                signal.rule,
                signal.window.value,
                str(signal.price_decline_ratio),
                str(signal.volume_ratio),
                (None if signal.baseline_price is None else str(signal.baseline_price)),
                (None if signal.observed_price is None else str(signal.observed_price)),
                (
                    None
                    if signal.comparison_return_ratio is None
                    else str(signal.comparison_return_ratio)
                ),
                None,
                None,
                None,
            )
        else:
            specific_values = (
                "NEWS",
                None if signal.direction is None else signal.direction.value,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                signal.category,
                signal.headline,
                signal.matched_phrase,
            )
        self._connection.execute(
            """
            INSERT INTO signals (
                signal_id, event_id, affected_update, ticker, occurred_at,
                importance, source_provider, source_name, source_feed,
                retrieved_at, signal_type, direction, market_rule,
                market_window, price_decline_ratio, volume_ratio,
                baseline_price, observed_price, comparison_return_ratio,
                news_category, headline, matched_phrase, article_id,
                classification_prompt_version, classification_model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *common_values,
                *specific_values,
                signal.article_id if isinstance(signal, NewsSignal) else None,
                signal.classification_prompt_version
                if isinstance(signal, NewsSignal)
                else None,
                signal.classification_model_version
                if isinstance(signal, NewsSignal)
                else None,
            ),
        )

    def _write_report(self, report: ResearchReport) -> None:
        self._connection.execute(
            """
            INSERT INTO reports (
                report_id, event_id, event_update, ticker,
                event_occurred_at, created_at, summary, is_fake, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.report_id,
                report.event_id,
                report.event_update,
                report.ticker,
                _timestamp(report.event_occurred_at),
                _timestamp(report.created_at),
                report.summary,
                int(report.is_fake),
                None if report.details is None else report.details.model_dump_json(),
            ),
        )

    def _write_notification_attempt(self, attempt: NotificationAttempt) -> None:
        self._connection.execute(
            """
            INSERT INTO notification_attempts (
                attempt_id, event_id, event_update, attempted_at,
                succeeded, safe_error
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.attempt_id,
                attempt.event_id,
                attempt.event_update,
                _timestamp(attempt.attempted_at),
                int(attempt.succeeded),
                attempt.safe_error,
            ),
        )

    def _write_market_bar(self, bar: MarketBar) -> None:
        self._connection.execute(
            """
            INSERT INTO market_bars (
                bar_id, ticker, timeframe, start_at, end_at, open_price,
                high_price, low_price, close_price, volume, is_complete,
                provider, feed, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bar_id) DO UPDATE SET
                end_at = excluded.end_at,
                open_price = excluded.open_price,
                high_price = excluded.high_price,
                low_price = excluded.low_price,
                close_price = excluded.close_price,
                volume = excluded.volume,
                is_complete = excluded.is_complete,
                provider = excluded.provider,
                feed = excluded.feed,
                retrieved_at = excluded.retrieved_at
            WHERE excluded.retrieved_at >= market_bars.retrieved_at
              AND (market_bars.is_complete = 0 OR excluded.is_complete = 1)
            """,
            (
                bar.bar_id,
                bar.ticker,
                bar.timeframe.value,
                _timestamp(bar.start_at),
                _timestamp(bar.end_at),
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
                int(bar.is_complete),
                bar.provider,
                bar.feed,
                _timestamp(bar.retrieved_at),
            ),
        )

    def _write_detector_state(self, state: DetectorState) -> None:
        self._connection.execute(
            """
            INSERT INTO detector_state (
                ticker, rule, window, direction, last_emitted_importance,
                updated_at, last_evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, rule, window, direction) DO UPDATE SET
                last_emitted_importance = excluded.last_emitted_importance,
                updated_at = excluded.updated_at,
                last_evaluated_at = excluded.last_evaluated_at
            """,
            (
                state.ticker,
                state.rule,
                state.window.value,
                state.direction.value,
                (
                    None
                    if state.last_emitted_importance is None
                    else state.last_emitted_importance.value
                ),
                _timestamp(state.updated_at),
                (
                    None
                    if state.last_evaluated_at is None
                    else _timestamp(state.last_evaluated_at)
                ),
            ),
        )

    def _write_news_article(self, article: NewsArticle) -> None:
        self._connection.execute(
            """
            INSERT INTO news_articles (
                article_id, provider, provider_article_id, symbols, headline,
                summary, content, url, canonical_url, source, created_at,
                updated_at, retrieved_at, content_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                symbols = excluded.symbols,
                headline = excluded.headline,
                summary = excluded.summary,
                content = excluded.content,
                url = excluded.url,
                canonical_url = excluded.canonical_url,
                source = excluded.source,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                retrieved_at = excluded.retrieved_at,
                content_fingerprint = excluded.content_fingerprint
            WHERE excluded.retrieved_at >= news_articles.retrieved_at
            """,
            (
                article.article_id,
                article.provider,
                article.provider_article_id,
                json.dumps(list(article.symbols)),
                article.headline,
                article.summary,
                article.content,
                article.url,
                article.canonical_url,
                article.source,
                _timestamp(article.created_at),
                _timestamp(article.updated_at),
                _timestamp(article.retrieved_at),
                article.content_fingerprint,
            ),
        )

    def _write_news_classification(self, classification: NewsClassification) -> None:
        self._connection.execute(
            """
            INSERT INTO news_classifications (
                article_id, ticker, prompt_version, model_version, relevant,
                category, significant, direction, importance, confidence,
                rationale, status, attempted_at, safe_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id, ticker, prompt_version, model_version)
            DO UPDATE SET
                relevant = excluded.relevant,
                category = excluded.category,
                significant = excluded.significant,
                direction = excluded.direction,
                importance = excluded.importance,
                confidence = excluded.confidence,
                rationale = excluded.rationale,
                status = excluded.status,
                attempted_at = excluded.attempted_at,
                safe_error = excluded.safe_error
            WHERE news_classifications.status != ?
            """,
            (
                classification.article_id,
                classification.ticker,
                classification.prompt_version,
                classification.model_version,
                (
                    None
                    if classification.relevant is None
                    else int(classification.relevant)
                ),
                (
                    None
                    if classification.category is None
                    else classification.category.value
                ),
                (
                    None
                    if classification.significant is None
                    else int(classification.significant)
                ),
                (
                    None
                    if classification.direction is None
                    else classification.direction.value
                ),
                (
                    None
                    if classification.importance is None
                    else classification.importance.value
                ),
                (
                    None
                    if classification.confidence is None
                    else str(classification.confidence)
                ),
                classification.rationale,
                classification.status.value,
                _timestamp(classification.attempted_at),
                classification.safe_error,
                ClassificationStatus.SUCCEEDED.value,
            ),
        )

    def _write_failure(self, failure: ProcessingFailure) -> None:
        self._connection.execute(
            """
            INSERT INTO failures (
                failure_id, event_id, event_update, step, retryable,
                occurred_at, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                failure.failure_id,
                failure.event_id,
                failure.event_update,
                failure.step.value,
                int(failure.retryable),
                _timestamp(failure.occurred_at),
                failure.description,
            ),
        )


def _event_from_row(row: sqlite3.Row) -> Event:
    windows = _market_windows(row["market_windows"])
    direction = _optional_text(row["direction"])
    return Event(
        event_id=str(row["event_id"]),
        ticker=str(row["ticker"]),
        direction=None if direction is None else SignalDirection(direction),
        category=_optional_text(row["category"]),
        importance=SignalImportance(str(row["importance"])),
        market_windows=windows,
        current_update=int(row["current_update"]),
        status=EventStatus(str(row["status"])),
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
        last_notified_at=_optional_datetime(row["last_notified_at"]),
        episode_open=bool(row["episode_open"]),
        closed_at=_optional_datetime(row["closed_at"]),
    )


def _report_from_row(row: sqlite3.Row) -> ResearchReport:
    return ResearchReport(
        report_id=str(row["report_id"]),
        event_id=str(row["event_id"]),
        event_update=int(row["event_update"]),
        ticker=str(row["ticker"]),
        event_occurred_at=_datetime(row["event_occurred_at"]),
        created_at=_datetime(row["created_at"]),
        summary=str(row["summary"]),
        is_fake=bool(row["is_fake"]),
        details=None
        if row["details"] is None
        else LiveReportDetails.model_validate_json(row["details"]),
    )


def _market_bar_from_row(row: sqlite3.Row) -> MarketBar:
    return MarketBar(
        ticker=str(row["ticker"]),
        timeframe=MarketTimeframe(str(row["timeframe"])),
        start_at=_datetime(row["start_at"]),
        end_at=_datetime(row["end_at"]),
        open=Decimal(str(row["open_price"])),
        high=Decimal(str(row["high_price"])),
        low=Decimal(str(row["low_price"])),
        close=Decimal(str(row["close_price"])),
        volume=Decimal(str(row["volume"])),
        is_complete=bool(row["is_complete"]),
        provider=str(row["provider"]),
        feed=str(row["feed"]),
        retrieved_at=_datetime(row["retrieved_at"]),
    )


def _detector_state_from_row(row: sqlite3.Row) -> DetectorState:
    importance = _optional_text(row["last_emitted_importance"])
    return DetectorState(
        ticker=str(row["ticker"]),
        rule=str(row["rule"]),
        window=MarketWindow(str(row["window"])),
        direction=SignalDirection(str(row["direction"])),
        last_emitted_importance=(
            None if importance is None else SignalImportance(importance)
        ),
        updated_at=_datetime(row["updated_at"]),
        last_evaluated_at=_optional_datetime(row["last_evaluated_at"]),
    )


def _news_article_from_row(row: sqlite3.Row) -> NewsArticle:
    return NewsArticle(
        provider=str(row["provider"]),
        provider_article_id=str(row["provider_article_id"]),
        symbols=_news_symbols(row["symbols"]),
        headline=str(row["headline"]),
        summary=str(row["summary"]),
        content=str(row["content"]),
        url=str(row["url"]),
        canonical_url=str(row["canonical_url"]),
        source=str(row["source"]),
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
        retrieved_at=_datetime(row["retrieved_at"]),
        content_fingerprint=str(row["content_fingerprint"]),
    )


def _news_classification_from_row(row: sqlite3.Row) -> NewsClassification:
    category = _optional_text(row["category"])
    direction = _optional_text(row["direction"])
    importance = _optional_text(row["importance"])
    confidence = _optional_text(row["confidence"])
    relevant = row["relevant"]
    significant = row["significant"]
    return NewsClassification(
        article_id=str(row["article_id"]),
        ticker=str(row["ticker"]),
        prompt_version=str(row["prompt_version"]),
        model_version=str(row["model_version"]),
        status=ClassificationStatus(str(row["status"])),
        attempted_at=_datetime(row["attempted_at"]),
        relevant=None if relevant is None else bool(relevant),
        category=None if category is None else NewsCategory(category),
        significant=None if significant is None else bool(significant),
        direction=None if direction is None else NewsDirection(direction),
        importance=None if importance is None else SignalImportance(importance),
        confidence=None if confidence is None else float(confidence),
        rationale=_optional_text(row["rationale"]),
        safe_error=_optional_text(row["safe_error"]),
    )


def _news_symbols(value: object) -> tuple[str, ...]:
    decoded: object = json.loads(str(value))
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        raise ValueError("invalid stored news symbols")
    return tuple(decoded)


def _failure_from_row(row: sqlite3.Row) -> ProcessingFailure:
    return ProcessingFailure(
        failure_id=str(row["failure_id"]),
        event_id=str(row["event_id"]),
        event_update=int(row["event_update"]),
        step=FailureStep(str(row["step"])),
        retryable=bool(row["retryable"]),
        occurred_at=_datetime(row["occurred_at"]),
        description=str(row["description"]),
    )


def _signal_from_row(row: sqlite3.Row) -> Signal:
    source_details = SourceDetails(
        provider=str(row["source_provider"]),
        source=str(row["source_name"]),
        feed=_optional_text(row["source_feed"]),
        retrieved_at=_datetime(row["retrieved_at"]),
    )
    direction = _optional_text(row["direction"])
    if row["signal_type"] == "MARKET":
        return MarketSignal(
            signal_id=str(row["signal_id"]),
            ticker=str(row["ticker"]),
            occurred_at=_datetime(row["occurred_at"]),
            importance=SignalImportance(str(row["importance"])),
            source_details=source_details,
            direction=SignalDirection(_required(direction, "direction")),
            rule=_required(_optional_text(row["market_rule"]), "market_rule"),
            window=MarketWindow(
                _required(_optional_text(row["market_window"]), "market_window")
            ),
            price_decline_ratio=Decimal(
                _required(
                    _optional_text(row["price_decline_ratio"]),
                    "price_decline_ratio",
                )
            ),
            volume_ratio=Decimal(
                _required(_optional_text(row["volume_ratio"]), "volume_ratio")
            ),
            baseline_price=_optional_decimal(row["baseline_price"]),
            observed_price=_optional_decimal(row["observed_price"]),
            comparison_return_ratio=_optional_decimal(row["comparison_return_ratio"]),
        )
    if row["signal_type"] == "NEWS":
        return NewsSignal(
            signal_id=str(row["signal_id"]),
            ticker=str(row["ticker"]),
            occurred_at=_datetime(row["occurred_at"]),
            importance=SignalImportance(str(row["importance"])),
            source_details=source_details,
            category=_required(
                _optional_text(row["news_category"]),
                "news_category",
            ),
            direction=None if direction is None else SignalDirection(direction),
            headline=_required(_optional_text(row["headline"]), "headline"),
            article_id=_optional_text(row["article_id"]),
            classification_prompt_version=_optional_text(
                row["classification_prompt_version"]
            ),
            classification_model_version=_optional_text(
                row["classification_model_version"]
            ),
            matched_phrase=_required(
                _optional_text(row["matched_phrase"]),
                "matched_phrase",
            ),
        )
    raise ValueError(f"unsupported signal type: {row['signal_type']}")


def _market_windows(value: object) -> tuple[MarketWindow, ...]:
    decoded: object = json.loads(str(value))
    if not isinstance(decoded, list) or not all(
        isinstance(item, str) for item in decoded
    ):
        raise ValueError("invalid stored market_windows")
    return tuple(MarketWindow(item) for item in decoded)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _sqlite_is_regular_session_minute(value: object) -> int:
    return int(is_regular_session_minute(_datetime(value)))


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _required(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValueError(f"stored {field_name} must not be null")
    return value
