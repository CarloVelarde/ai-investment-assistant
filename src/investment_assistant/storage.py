"""Small SQLite persistence boundary for durable event state.

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
``save_notification_attempt``, ``save_failure``) insert or replace rows
without enforcing lifecycle rules. Prefer them only in tests (to seed a
specific state) or inside this module. Do not use them from production
pipeline code to advance an event through research or notification.
"""

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType

from investment_assistant.models import (
    Event,
    EventStatus,
    FailureStep,
    MarketSignal,
    MarketWindow,
    NewsSignal,
    NotificationAttempt,
    ProcessingFailure,
    ResearchReport,
    Signal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)

DATABASE_VERSION = 1

_SCHEMA = """
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
    news_category TEXT,
    headline TEXT,
    matched_phrase TEXT
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

PRAGMA user_version = 1;
"""


class SQLiteStorage:
    """Persist and reconstruct internal models using one SQLite connection."""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

    def __enter__(self) -> SQLiteStorage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def initialize(self) -> None:
        """Create the first layout safely or validate its known version."""

        row = self._connection.execute("PRAGMA user_version").fetchone()
        if row is None:
            raise RuntimeError("could not read SQLite database version")
        version = int(row[0])
        if version not in (0, DATABASE_VERSION):
            raise ValueError(f"unsupported SQLite database version: {version}")
        with self._connection:
            self._connection.executescript(_SCHEMA)

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

        with self._connection:
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

        with self._connection:
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
            f"{condition} ORDER BY created_at, event_id",
            (ticker, direction.value),
        ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def find_category_events(self, ticker: str, category: str) -> tuple[Event, ...]:
        """Find category candidates used when news has no clear market match."""

        rows = self._connection.execute(
            "SELECT * FROM events WHERE ticker = ? AND category = ? "
            "ORDER BY created_at, event_id",
            (ticker, category),
        ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def record_signal(self, signal: Signal, event: Event) -> bool:
        """Atomically save a new signal and its new or updated event."""

        with self._connection:
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

        with self._connection:
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

    def save_report(self, report: ResearchReport) -> None:
        """Persist one report for a specific event update."""

        with self._connection:
            self._write_report(report)

    def save_report_and_mark_reported(
        self,
        report: ResearchReport,
        *,
        updated_at: datetime,
    ) -> bool:
        """Atomically save a current report and mark it ready for delivery."""

        with self._connection:
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

        with self._connection:
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
        with self._connection:
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
                    """
                    UPDATE events
                    SET status = ?, updated_at = ?, last_notified_at = ?
                    WHERE event_id = ? AND current_update = ?
                    """,
                    (
                        EventStatus.NOTIFIED.value,
                        _timestamp(updated_at),
                        _timestamp(attempt.attempted_at),
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

    def save_failure(self, failure: ProcessingFailure) -> None:
        """Persist one safe processing failure."""

        with self._connection:
            self._write_failure(failure)

    def save_failure_and_mark_failed(
        self,
        failure: ProcessingFailure,
        *,
        updated_at: datetime,
    ) -> bool:
        """Atomically save a current processing failure and failed state."""

        with self._connection:
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

    def _write_event(self, event: Event) -> None:
        self._connection.execute(
            """
            INSERT INTO events (
                event_id, ticker, direction, category, importance,
                market_windows, current_update, status, created_at,
                updated_at, last_notified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                ticker = excluded.ticker,
                direction = excluded.direction,
                category = excluded.category,
                importance = excluded.importance,
                market_windows = excluded.market_windows,
                current_update = excluded.current_update,
                status = excluded.status,
                updated_at = excluded.updated_at,
                last_notified_at = excluded.last_notified_at
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
                news_category, headline, matched_phrase
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*common_values, *specific_values),
        )

    def _write_report(self, report: ResearchReport) -> None:
        self._connection.execute(
            """
            INSERT INTO reports (
                report_id, event_id, event_update, ticker,
                event_occurred_at, created_at, summary, is_fake
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    )


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


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _required(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValueError(f"stored {field_name} must not be null")
    return value
