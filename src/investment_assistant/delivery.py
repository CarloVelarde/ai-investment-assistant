"""Durable Discord delivery state; only this boundary owns submission claims."""

import fcntl
import hashlib
import logging
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Literal
from uuid import uuid4

from investment_assistant.clock import Clock
from investment_assistant.discord_notify import (
    DeliveryOutcome,
    SendResult,
    validate_webhook_url,
)
from investment_assistant.models import Event, EventStatus, ResearchReport
from investment_assistant.ops_log import watch
from investment_assistant.storage import SQLiteStorage, _datetime, _timestamp

type Sender = Callable[[Event, ResearchReport, str], SendResult]
type Lookup = Callable[[str, str], SendResult]

logger = logging.getLogger(__name__)
_RECEIPT_ID = re.compile(r"[0-9]+\Z")


def destination_fingerprint(url: str) -> str:
    return hashlib.sha256(validate_webhook_url(url).encode()).hexdigest()


def delivery_id(event_id: str, event_update: int) -> str:
    identity = f"{event_id}:{event_update}".encode()
    return "d-" + hashlib.sha256(identity).hexdigest()[:24]


def _wait_until(now: datetime, seconds: Decimal) -> datetime:
    if not seconds.is_finite() or seconds < 0:
        raise ValueError("invalid provider wait")
    microseconds = int((seconds * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    return now + timedelta(microseconds=microseconds)


class DatabaseOwner:
    """Process-lifetime OS lock for one canonical local database path."""

    def __init__(self, database_path: Path) -> None:
        self._path = Path(str(database_path.resolve()) + ".owner.lock")
        self._file: BinaryIO | None = None

    def __enter__(self) -> DatabaseOwner:
        lock_file = self._path.open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            lock_file.close()
            raise RuntimeError("database is already owned by a live process") from error
        self._file = lock_file
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        lock_file = self._file
        if lock_file is not None:
            lock_file.close()
            self._file = None


@dataclass(frozen=True, slots=True)
class Claim:
    delivery_id: str
    attempt_id: str
    event: Event
    report: ResearchReport
    resend: bool
    manual: bool = False


class DeliveryState(StrEnum):
    READY = "READY"
    CLAIMED = "CLAIMED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    UNCERTAIN = "UNCERTAIN"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    delivery_id: str
    event_id: str
    event_update: int
    destination: Literal["console", "discord"]
    destination_fingerprint: str | None
    state: DeliveryState
    created_at: datetime
    updated_at: datetime
    next_attempt_at: datetime | None
    ordinary_count: int
    active_attempt_id: str | None
    message_id: str | None
    uncertain_since: datetime | None
    resend_not_before: datetime | None
    uncertain_resend_used: bool
    completed_at: datetime | None
    safe_reason: str | None
    manual_authorized: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> DeliveryRecord:
        record = cls(
            delivery_id=str(row["delivery_id"]),
            event_id=str(row["event_id"]),
            event_update=int(row["event_update"]),
            destination=str(row["destination"]),  # type: ignore[arg-type]
            destination_fingerprint=row["destination_fingerprint"],
            state=DeliveryState(row["state"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
            next_attempt_at=(
                _datetime(row["next_attempt_at"]) if row["next_attempt_at"] else None
            ),
            ordinary_count=int(row["ordinary_count"]),
            active_attempt_id=row["active_attempt_id"],
            message_id=row["message_id"],
            uncertain_since=(
                _datetime(row["uncertain_since"]) if row["uncertain_since"] else None
            ),
            resend_not_before=(
                _datetime(row["resend_not_before"])
                if row["resend_not_before"]
                else None
            ),
            uncertain_resend_used=bool(row["uncertain_resend_used"]),
            completed_at=(
                _datetime(row["completed_at"]) if row["completed_at"] else None
            ),
            safe_reason=row["safe_reason"] or dict(row).get("last_attempt_reason"),
            manual_authorized=bool(row["manual_authorized"]),
        )
        if (
            record.event_update < 1
            or not 0 <= record.ordinary_count <= 5
            or (record.destination == "discord") != bool(record.destination_fingerprint)
            or (record.state is DeliveryState.CLAIMED) != bool(record.active_attempt_id)
            or (record.state is DeliveryState.SUCCEEDED) != bool(record.completed_at)
            or (
                record.destination == "discord"
                and record.state
                in {DeliveryState.ACKNOWLEDGED, DeliveryState.SUCCEEDED}
                and (
                    record.message_id is None
                    or not _RECEIPT_ID.fullmatch(record.message_id)
                )
            )
            or (
                record.state is DeliveryState.UNCERTAIN
                and (record.uncertain_since is None or record.resend_not_before is None)
            )
        ):
            raise ValueError("invalid delivery state")
        return record


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    attempt_id: str
    delivery_id: str
    destination_fingerprint: str | None
    attempt_number: int
    resend: bool
    claimed_at: datetime
    completed_at: datetime | None
    outcome: DeliveryOutcome | None
    message_id: str | None
    safe_reason: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SubmissionRecord:
        record = cls(
            attempt_id=str(row["attempt_id"]),
            delivery_id=str(row["delivery_id"]),
            destination_fingerprint=row["destination_fingerprint"],
            attempt_number=int(row["attempt_number"]),
            resend=bool(row["resend"]),
            claimed_at=_datetime(row["claimed_at"]),
            completed_at=(
                _datetime(row["completed_at"]) if row["completed_at"] else None
            ),
            outcome=(DeliveryOutcome(row["outcome"]) if row["outcome"] else None),
            message_id=row["message_id"],
            safe_reason=row["safe_reason"],
        )
        if (
            record.attempt_number < 1
            or (record.outcome is None) != (record.completed_at is None)
            or (
                record.outcome is DeliveryOutcome.ACKNOWLEDGED
                and (
                    record.message_id is None
                    or not _RECEIPT_ID.fullmatch(record.message_id)
                )
            )
        ):
            raise ValueError("invalid submission state")
        return record


def list_deliveries(storage: SQLiteStorage) -> tuple[DeliveryRecord, ...]:
    """Return current and historical held work without report bodies or secrets."""

    rows = storage._connection.execute(_LIST_DELIVERIES_SQL).fetchall()
    return tuple(DeliveryRecord.from_row(row) for row in rows)


def read_only_deliveries(path: Path) -> tuple[DeliveryRecord, ...]:
    """Inspect held history without initializing or modifying the database."""

    if not path.exists():
        return ()
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'notification_deliveries'"
        ).fetchone()
        if table is None:
            return ()
        rows = connection.execute(_LIST_DELIVERIES_SQL).fetchall()
        return tuple(DeliveryRecord.from_row(row) for row in rows)


_LIST_DELIVERIES_SQL = """
SELECT d.*, (SELECT s.safe_reason FROM notification_submissions s
    WHERE s.delivery_id = d.delivery_id AND s.safe_reason IS NOT NULL
    ORDER BY s.attempt_number DESC LIMIT 1) AS last_attempt_reason
FROM notification_deliveries d
WHERE d.state NOT IN ('SUCCEEDED', 'READY')
   OR EXISTS (SELECT 1 FROM notification_submissions s
              WHERE s.delivery_id = d.delivery_id AND s.safe_reason IS NOT NULL)
ORDER BY d.created_at, d.delivery_id
"""


class DeliveryManager:
    """Select, claim, send, and settle at most one Discord alert per call."""

    def __init__(
        self,
        storage: SQLiteStorage,
        *,
        clock: Clock,
        webhook_url: str,
        sender: Sender,
        lookup: Lookup | None = None,
    ) -> None:
        self._storage = storage
        self._clock = clock
        self._fingerprint = destination_fingerprint(webhook_url)
        self._sender = sender
        self._lookup = lookup

    @property
    def _db(self) -> sqlite3.Connection:
        return self._storage._connection

    def get_delivery(self, identity: str) -> DeliveryRecord | None:
        row = self._db.execute(
            "SELECT * FROM notification_deliveries WHERE delivery_id = ?", (identity,)
        ).fetchone()
        return None if row is None else DeliveryRecord.from_row(row)

    def list_submissions(self, identity: str) -> tuple[SubmissionRecord, ...]:
        rows = self._db.execute(
            """SELECT * FROM notification_submissions WHERE delivery_id = ?
            ORDER BY attempt_number""",
            (identity,),
        ).fetchall()
        return tuple(SubmissionRecord.from_row(row) for row in rows)

    def authorize_retry(self, identity: str) -> bool:
        """Persist one extra submission; a pending automatic resend takes precedence."""

        now = self._clock.now()
        self._recover_claims(now)
        self._discover_and_supersede(now)
        with self._storage.transaction():
            row = self._db.execute(
                """SELECT d.* FROM notification_deliveries d JOIN events e
                ON e.event_id = d.event_id AND e.current_update = d.event_update
                JOIN reports r ON r.event_id = d.event_id AND r.event_update = d.event_update
                WHERE d.delivery_id = ? AND d.destination = 'discord'
                AND e.status IN ('REPORTED', 'FAILED')""",
                (identity,),
            ).fetchone()
            if row is None or row["state"] not in ("PERMANENT_FAILURE", "UNCERTAIN"):
                return False
            if row["state"] == "UNCERTAIN" and not row["uncertain_resend_used"]:
                return False
            if row["manual_authorized"]:
                return True
            self._db.execute(
                "UPDATE notification_deliveries SET manual_authorized = 1, updated_at = ? WHERE delivery_id = ?",
                (_timestamp(now), identity),
            )
            self._log_transition("Delivery retry authorized", delivery_id=identity)
            return True

    def confirm_receipt(self, identity: str, message_id: str) -> bool:
        """Look up a Discord message, then retain only a matching receipt."""

        if not _RECEIPT_ID.fullmatch(message_id) or self._lookup is None:
            return False
        now = self._clock.now()
        self._recover_claims(now)
        self._discover_and_supersede(now)
        record = self.get_delivery(identity)
        if (
            record is None
            or record.destination != "discord"
            or (
                record.destination_fingerprint != self._fingerprint
                and not any(
                    submission.destination_fingerprint == self._fingerprint
                    for submission in self.list_submissions(identity)
                )
            )
        ):
            return False
        if record.state is DeliveryState.SUCCEEDED:
            return record.message_id == message_id
        if record.state is DeliveryState.CLAIMED:
            return False
        if self._waiting(now, include_destination=False):
            return False
        destination = self._db.execute(
            "SELECT wait_until FROM notification_destination_state WHERE fingerprint = ?",
            (self._fingerprint,),
        ).fetchone()
        if (
            destination
            and destination["wait_until"]
            and _datetime(destination["wait_until"]) > now
        ):
            return False
        result = self._lookup(message_id, identity)
        if result.wait_seconds is not None:
            with self._storage.transaction():
                self._store_provider_wait(result, self._clock.now())
        if (
            result.outcome is not DeliveryOutcome.ACKNOWLEDGED
            or result.message_id != message_id
        ):
            return False
        finished_at = self._clock.now()
        with self._storage.transaction():
            if record.state is DeliveryState.SUPERSEDED:
                self._db.execute(
                    """UPDATE notification_deliveries SET message_id = ?, updated_at = ?
                    WHERE delivery_id = ? AND state = 'SUPERSEDED'""",
                    (message_id, _timestamp(finished_at), identity),
                )
            else:
                self._db.execute(
                    """UPDATE notification_deliveries SET state = 'ACKNOWLEDGED', message_id = ?,
                    updated_at = ?, manual_authorized = 0 WHERE delivery_id = ?
                    AND state NOT IN ('SUCCEEDED', 'CLAIMED', 'SUPERSEDED')""",
                    (message_id, _timestamp(finished_at), identity),
                )
        self._complete(identity, finished_at)
        self._log_transition("Delivery receipt confirmed", delivery_id=identity)
        return True

    def process_one(self) -> bool:
        """Return whether an external submission was attempted."""

        now = self._clock.now()
        self._recover_claims(now)
        self._finish_receipts(now)
        self._discover_and_supersede(now)
        claim = self._claim_next(now)
        if claim is None:
            return False
        try:
            result = self._sender(claim.event, claim.report, claim.delivery_id)
        except Exception as error:
            result = SendResult(
                DeliveryOutcome.UNCERTAIN, safe_reason=type(error).__name__[:300]
            )
        if result.outcome is DeliveryOutcome.ACKNOWLEDGED and (
            not isinstance(result.message_id, str)
            or not _RECEIPT_ID.fullmatch(result.message_id)
        ):
            result = SendResult(DeliveryOutcome.UNCERTAIN, safe_reason="NO_RECEIPT")
        finished_at = self._clock.now()
        if (
            result.outcome is DeliveryOutcome.ACKNOWLEDGED
            and result.message_id is not None
        ):
            self._save_receipt(claim, result, finished_at)
            self._complete(claim.delivery_id, finished_at)
            if claim.manual:
                with self._storage.transaction():
                    self._db.execute(
                        "UPDATE notification_destination_state SET disabled_reason = NULL WHERE fingerprint = ?",
                        (self._fingerprint,),
                    )
            logger.info(
                "Discord delivery completed",
                extra={
                    "ticker": claim.event.ticker,
                    "event_id": claim.event.event_id,
                    "event_update": claim.event.current_update,
                    "delivery_id": claim.delivery_id,
                },
            )
            watch(
                "Discord delivery completed",
                ticker=claim.event.ticker,
                event_id=claim.event.event_id,
                event_update=claim.event.current_update,
                delivery_id=claim.delivery_id,
            )
        else:
            self._settle_failure(claim, result, finished_at)
        return True

    def _recover_claims(self, now: datetime) -> None:
        with self._storage.transaction():
            rows = self._db.execute(
                "SELECT delivery_id, active_attempt_id, uncertain_since, "
                "uncertain_resend_used FROM notification_deliveries WHERE state = 'CLAIMED'"
            ).fetchall()
            for row in rows:
                since = (
                    _datetime(row["uncertain_since"]) if row["uncertain_since"] else now
                )
                deadline = since + timedelta(minutes=15)
                self._db.execute(
                    """UPDATE notification_submissions SET completed_at = ?,
                    outcome = 'UNCERTAIN', safe_reason = 'INTERRUPTED'
                    WHERE attempt_id = ? AND outcome IS NULL""",
                    (_timestamp(now), row["active_attempt_id"]),
                )
                self._log_transition(
                    "Interrupted delivery became uncertain",
                    delivery_id=str(row["delivery_id"]),
                    retry_at=_timestamp(deadline),
                )
                self._db.execute(
                    """UPDATE notification_deliveries SET state = 'UNCERTAIN',
                    updated_at = ?, active_attempt_id = NULL,
                    uncertain_since = COALESCE(uncertain_since, ?),
                    resend_not_before = COALESCE(resend_not_before, ?),
                    safe_reason = 'INTERRUPTED'
                    WHERE delivery_id = ?""",
                    (
                        _timestamp(now),
                        _timestamp(since),
                        _timestamp(deadline),
                        row["delivery_id"],
                    ),
                )

    def _finish_receipts(self, now: datetime) -> None:
        rows = self._db.execute(
            "SELECT delivery_id FROM notification_deliveries WHERE state = 'ACKNOWLEDGED'"
        ).fetchall()
        for row in rows:
            self._complete(str(row["delivery_id"]), now)

    def _discover_and_supersede(self, now: datetime) -> None:
        with self._storage.transaction():
            self._db.execute(
                """UPDATE notification_deliveries SET state = 'SUPERSEDED',
                updated_at = ? WHERE state NOT IN ('SUCCEEDED', 'SUPERSEDED', 'CLAIMED', 'ACKNOWLEDGED')
                AND NOT EXISTS (SELECT 1 FROM events e WHERE e.event_id = notification_deliveries.event_id
                AND e.current_update = notification_deliveries.event_update)""",
                (_timestamp(now),),
            )
            rows = self._db.execute(
                """SELECT e.event_id, e.current_update, r.created_at FROM events e
                JOIN reports r ON r.event_id = e.event_id AND r.event_update = e.current_update
                WHERE e.status IN ('REPORTED', 'FAILED') ORDER BY r.created_at, e.event_id"""
            ).fetchall()
            for row in rows:
                event_id = str(row["event_id"])
                update = int(row["current_update"])
                self._db.execute(
                    """INSERT OR IGNORE INTO notification_deliveries
                    (delivery_id, event_id, event_update, destination,
                     destination_fingerprint, state, created_at, updated_at)
                    VALUES (?, ?, ?, 'discord', ?, 'READY', ?, ?)""",
                    (
                        delivery_id(event_id, update),
                        event_id,
                        update,
                        self._fingerprint,
                        row["created_at"],
                        _timestamp(now),
                    ),
                )
            self._db.execute(
                """UPDATE notification_deliveries SET destination_fingerprint = ?, updated_at = ?
                WHERE destination = 'discord' AND state = 'READY'
                AND ordinary_count = 0 AND uncertain_since IS NULL
                AND destination_fingerprint != ?""",
                (self._fingerprint, _timestamp(now), self._fingerprint),
            )
            self._db.execute(
                """UPDATE notification_deliveries SET state = 'READY',
                destination_fingerprint = ?, updated_at = ?, safe_reason = NULL
                WHERE destination = 'discord' AND state = 'PERMANENT_FAILURE'
                AND ordinary_count < 5 AND uncertain_since IS NULL
                AND (safe_reason IN ('HTTP_401', 'HTTP_403', 'HTTP_404')
                     OR safe_reason LIKE 'HTTP_%_REDIRECT')
                AND destination_fingerprint != ?""",
                (self._fingerprint, _timestamp(now), self._fingerprint),
            )

    def _claim_next(self, now: datetime) -> Claim | None:
        if self._waiting(now, include_destination=False):
            return None
        destination = self._db.execute(
            "SELECT wait_until, disabled_reason FROM notification_destination_state WHERE fingerprint = ?",
            (self._fingerprint,),
        ).fetchone()
        if (
            destination
            and destination["wait_until"]
            and _datetime(destination["wait_until"]) > now
        ):
            return None
        disabled = bool(destination and destination["disabled_reason"])
        rows = self._db.execute(
            """SELECT d.* FROM notification_deliveries d
            JOIN events e ON e.event_id = d.event_id AND e.current_update = d.event_update
            JOIN reports r ON r.event_id = d.event_id AND r.event_update = d.event_update
            WHERE d.destination = 'discord' AND e.status IN ('REPORTED', 'FAILED')
            AND d.state IN ('READY', 'RETRY_WAIT', 'UNCERTAIN', 'PERMANENT_FAILURE')
            ORDER BY COALESCE(d.next_attempt_at, d.resend_not_before, d.created_at), d.delivery_id"""
        ).fetchall()
        for row in rows:
            manual = bool(row["manual_authorized"])
            if disabled and not manual:
                continue
            if row["destination_fingerprint"] != self._fingerprint and not manual:
                continue
            state = str(row["state"])
            if not manual and state == "PERMANENT_FAILURE":
                continue
            if (
                not manual
                and state == "RETRY_WAIT"
                and (
                    row["next_attempt_at"] is None
                    or _datetime(row["next_attempt_at"]) > now
                )
            ):
                continue
            resend = state == "UNCERTAIN" and not manual
            if resend and (
                row["uncertain_resend_used"]
                or row["resend_not_before"] is None
                or _datetime(row["resend_not_before"]) > now
            ):
                continue
            event = self._storage.get_event(str(row["event_id"]))
            report = self._storage.get_report_for_update(
                str(row["event_id"]), int(row["event_update"])
            )
            if event is None or report is None:
                continue
            attempt_id = "submission:" + uuid4().hex
            attempt_number = (
                int(
                    self._db.execute(
                        "SELECT count(*) FROM notification_submissions WHERE delivery_id = ?",
                        (row["delivery_id"],),
                    ).fetchone()[0]
                )
                + 1
            )
            with self._storage.transaction():
                changed = self._db.execute(
                    """UPDATE notification_deliveries SET state = 'CLAIMED',
                    active_attempt_id = ?, updated_at = ?, ordinary_count = ordinary_count + ?,
                    manual_authorized = 0, destination_fingerprint = ?,
                    uncertain_resend_used = CASE WHEN ? THEN 1 ELSE uncertain_resend_used END
                    WHERE delivery_id = ? AND state = ? AND active_attempt_id IS NULL
                    AND (? OR ordinary_count < 5)
                    AND EXISTS (SELECT 1 FROM events e JOIN reports r
                        ON r.event_id = e.event_id AND r.event_update = e.current_update
                        WHERE e.event_id = notification_deliveries.event_id
                        AND e.current_update = notification_deliveries.event_update
                        AND e.status IN ('REPORTED', 'FAILED'))""",
                    (
                        attempt_id,
                        _timestamp(now),
                        int(not resend and not manual),
                        self._fingerprint,
                        int(resend or manual),
                        row["delivery_id"],
                        state,
                        int(resend or manual),
                    ),
                ).rowcount
                if not changed:
                    continue
                self._db.execute(
                    """INSERT INTO notification_submissions
                    (attempt_id, delivery_id, destination_fingerprint,
                     attempt_number, resend, claimed_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        attempt_id,
                        row["delivery_id"],
                        self._fingerprint,
                        attempt_number,
                        int(resend),
                        _timestamp(now),
                    ),
                )
            self._log_transition(
                "Discord resend claimed" if resend else "Discord delivery claimed",
                ticker=event.ticker,
                event_id=event.event_id,
                event_update=event.current_update,
                delivery_id=str(row["delivery_id"]),
            )
            return Claim(
                str(row["delivery_id"]), attempt_id, event, report, resend, manual
            )
        return None

    def _waiting(self, now: datetime, *, include_destination: bool = True) -> bool:
        global_row = self._db.execute(
            "SELECT wait_until FROM notification_global_state WHERE id = 1"
        ).fetchone()
        if (
            global_row
            and global_row["wait_until"]
            and _datetime(global_row["wait_until"]) > now
        ):
            return True
        if not include_destination:
            return False
        destination = self._db.execute(
            "SELECT wait_until, disabled_reason FROM notification_destination_state WHERE fingerprint = ?",
            (self._fingerprint,),
        ).fetchone()
        return bool(
            destination
            and (
                destination["disabled_reason"]
                or (
                    destination["wait_until"]
                    and _datetime(destination["wait_until"]) > now
                )
            )
        )

    def _save_receipt(self, claim: Claim, result: SendResult, now: datetime) -> None:
        with self._storage.transaction():
            self._db.execute(
                """UPDATE notification_submissions SET completed_at = ?, outcome = 'ACKNOWLEDGED',
                message_id = ? WHERE attempt_id = ? AND outcome IS NULL""",
                (_timestamp(now), result.message_id, claim.attempt_id),
            )
            self._db.execute(
                """UPDATE notification_deliveries SET state = 'ACKNOWLEDGED', message_id = ?,
                active_attempt_id = NULL, updated_at = ?
                WHERE delivery_id = ? AND active_attempt_id = ?""",
                (
                    result.message_id,
                    _timestamp(now),
                    claim.delivery_id,
                    claim.attempt_id,
                ),
            )
            self._store_provider_wait(result, now)

    @staticmethod
    def _log_transition(message: str, **fields: object) -> None:
        with suppress(Exception):
            logger.info(message, extra=fields)
        watch(message, **fields)

    def _complete(self, identity: str, now: datetime) -> None:
        with self._storage.transaction():
            row = self._db.execute(
                "SELECT * FROM notification_deliveries WHERE delivery_id = ? AND state = 'ACKNOWLEDGED'",
                (identity,),
            ).fetchone()
            if row is None or row["message_id"] is None:
                return
            self._db.execute(
                """UPDATE notification_deliveries SET state = 'SUCCEEDED', completed_at = ?,
                updated_at = ? WHERE delivery_id = ? AND state = 'ACKNOWLEDGED'""",
                (_timestamp(now), _timestamp(now), identity),
            )
            self._db.execute(
                """UPDATE events SET status = ?, updated_at = ?, last_notified_at = ?
                WHERE event_id = ? AND current_update = ? AND status IN ('REPORTED', 'FAILED')""",
                (
                    EventStatus.NOTIFIED.value,
                    _timestamp(now),
                    _timestamp(now),
                    row["event_id"],
                    row["event_update"],
                ),
            )

    def _settle_failure(self, claim: Claim, result: SendResult, now: datetime) -> None:
        with self._storage.transaction():
            row = self._db.execute(
                "SELECT ordinary_count, uncertain_since FROM notification_deliveries WHERE delivery_id = ?",
                (claim.delivery_id,),
            ).fetchone()
            if row is None:
                return
            reason = (result.safe_reason or result.outcome.value)[:300]
            self._db.execute(
                """UPDATE notification_submissions SET completed_at = ?, outcome = ?,
                safe_reason = ? WHERE attempt_id = ? AND outcome IS NULL""",
                (_timestamp(now), result.outcome.value, reason, claim.attempt_id),
            )
            next_attempt: datetime | None = None
            uncertain_since = (
                _datetime(row["uncertain_since"]) if row["uncertain_since"] else None
            )
            resend_deadline: datetime | None = None
            if claim.resend:
                state = "UNCERTAIN"
            elif claim.manual and result.outcome is DeliveryOutcome.UNCERTAIN:
                state = "UNCERTAIN"
                uncertain_since = uncertain_since or now
                resend_deadline = uncertain_since + timedelta(minutes=15)
            elif claim.manual:
                state = "PERMANENT_FAILURE"
            elif result.outcome is DeliveryOutcome.UNCERTAIN:
                state = "UNCERTAIN"
                uncertain_since = uncertain_since or now
                resend_deadline = uncertain_since + timedelta(minutes=15)
            elif (
                result.outcome is DeliveryOutcome.PERMANENT
                or int(row["ordinary_count"]) >= 5
            ):
                state = "PERMANENT_FAILURE"
            else:
                state = "RETRY_WAIT"
                delay = (1, 2, 4, 8)[int(row["ordinary_count"]) - 1]
                next_attempt = now + timedelta(minutes=delay)
            if result.wait_seconds is not None:
                try:
                    provider_until = _wait_until(now, result.wait_seconds)
                    if next_attempt is not None:
                        next_attempt = max(next_attempt, provider_until)
                except OverflowError, ValueError:
                    self._disable_destination("UNREPRESENTABLE_WAIT")
            self._db.execute(
                """UPDATE notification_deliveries SET state = ?, active_attempt_id = NULL,
                updated_at = ?, next_attempt_at = ?,
                uncertain_since = COALESCE(uncertain_since, ?),
                resend_not_before = COALESCE(resend_not_before, ?), safe_reason = ?
                WHERE delivery_id = ? AND active_attempt_id = ?""",
                (
                    state,
                    _timestamp(now),
                    _timestamp(next_attempt) if next_attempt else None,
                    _timestamp(uncertain_since) if uncertain_since else None,
                    _timestamp(resend_deadline) if resend_deadline else None,
                    reason,
                    claim.delivery_id,
                    claim.attempt_id,
                ),
            )
            if result.disable_destination:
                self._disable_destination(reason)
            self._store_provider_wait(result, now)
        retry_at = next_attempt or resend_deadline
        message = (
            "Discord resend exhausted"
            if claim.resend
            else "Discord delivery " + state.lower()
        )
        self._log_transition(
            message,
            ticker=claim.event.ticker,
            event_id=claim.event.event_id,
            event_update=claim.event.current_update,
            delivery_id=claim.delivery_id,
            reason=reason,
            retry_at=_timestamp(retry_at) if retry_at is not None else None,
        )

    def _disable_destination(self, reason: str) -> None:
        self._db.execute(
            """INSERT INTO notification_destination_state (fingerprint, disabled_reason)
            VALUES (?, ?) ON CONFLICT(fingerprint) DO UPDATE SET disabled_reason = excluded.disabled_reason""",
            (self._fingerprint, reason),
        )

    def _store_provider_wait(self, result: SendResult, now: datetime) -> None:
        if result.wait_seconds is None:
            return
        try:
            until = _wait_until(now, result.wait_seconds)
        except OverflowError, ValueError:
            self._disable_destination("UNREPRESENTABLE_WAIT")
            return
        if result.global_wait:
            self._db.execute(
                """INSERT INTO notification_global_state (id, wait_until) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET wait_until =
                CASE WHEN notification_global_state.wait_until IS NULL
                    OR notification_global_state.wait_until < excluded.wait_until
                    THEN excluded.wait_until ELSE notification_global_state.wait_until END""",
                (_timestamp(until),),
            )
        else:
            self._db.execute(
                """INSERT INTO notification_destination_state (fingerprint, wait_until) VALUES (?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET wait_until =
                CASE WHEN notification_destination_state.wait_until IS NULL
                    OR notification_destination_state.wait_until < excluded.wait_until
                    THEN excluded.wait_until ELSE notification_destination_state.wait_until END""",
                (self._fingerprint, _timestamp(until)),
            )
