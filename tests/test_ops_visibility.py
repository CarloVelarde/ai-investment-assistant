"""Tests for opt-in heartbeat and watch log. No network."""

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr

from investment_assistant.clock import SteppingClock
from investment_assistant.config import Settings
from investment_assistant.logging_config import JsonFormatter
from investment_assistant.main import main
from investment_assistant.market_data import FakeMarketData, MarketSession
from investment_assistant.models import MarketBar, MarketTimeframe
from investment_assistant.ops_log import (
    HEARTBEAT_INTERVAL,
    HEARTBEAT_MESSAGE,
    WATCH_LOGGER_NAME,
    configure_watch_log,
    heartbeat_is_due,
    watch,
)

SECRET = "test-alpaca-secret-do-not-log"
CLOSED_AT = datetime(2026, 2, 2, 21, 5, tzinfo=UTC)
CLOSED_SESSION = MarketSession(
    is_open=False,
    timestamp=CLOSED_AT,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 3, 21, 0, tzinfo=UTC),
)


def _daily() -> MarketBar:
    start_at = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
    return MarketBar(
        ticker="TSLA",
        timeframe=MarketTimeframe.ONE_DAY,
        start_at=start_at,
        end_at=start_at + timedelta(hours=6, minutes=30),
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal("1000000"),
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=CLOSED_AT,
    )


def _live_settings(
    database_path: Path,
    *,
    heartbeat: bool = False,
    watch_log: bool = False,
) -> Settings:
    return Settings(
        alpaca_api_key_id="test-key-id",
        alpaca_api_secret_key=SecretStr(SECRET),
        watchlist="TSLA",
        database_path=database_path,
        heartbeat=heartbeat,
        watch_log=watch_log,
        log_json=True,
        log_level="INFO",
    )


def _run_live(
    tmp_path: Path,
    *,
    heartbeat: bool,
    watch_log: bool,
    max_cycles: int,
    step: timedelta,
) -> None:
    clock = SteppingClock(CLOSED_AT)

    def sleeper(_seconds: float) -> None:
        clock.advance_to(clock.now() + step)

    main(
        settings=_live_settings(
            tmp_path / "ops.sqlite3",
            heartbeat=heartbeat,
            watch_log=watch_log,
        ),
        provider=FakeMarketData(history=(_daily(),), session=CLOSED_SESSION),
        clock=clock,
        loop=True,
        max_cycles=max_cycles,
        sleeper=sleeper,
        notifier=lambda *_: None,
    )


def test_heartbeat_is_due_after_sixty_seconds_not_fifty_nine() -> None:
    started = CLOSED_AT

    assert (
        heartbeat_is_due(
            now=started + HEARTBEAT_INTERVAL - timedelta(seconds=1),
            started_at=started,
            last_emitted_at=None,
        )
        is False
    )
    assert (
        heartbeat_is_due(
            now=started + HEARTBEAT_INTERVAL,
            started_at=started,
            last_emitted_at=None,
        )
        is True
    )


def test_default_live_run_emits_no_heartbeat_or_watch_lines(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level("INFO")
    _run_live(
        tmp_path,
        heartbeat=False,
        watch_log=False,
        max_cycles=3,
        step=timedelta(seconds=30),
    )
    captured = capsys.readouterr()
    printed = captured.out + captured.err

    assert HEARTBEAT_MESSAGE not in caplog.text
    assert " | WATCH | " not in printed
    assert SECRET not in caplog.text
    assert SECRET not in printed


def test_heartbeat_fires_on_closed_session_after_sixty_seconds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_live(
        tmp_path,
        heartbeat=True,
        watch_log=False,
        max_cycles=3,
        step=timedelta(seconds=30),
    )
    captured = capsys.readouterr()
    printed = captured.out + captured.err
    beats = [
        json.loads(line) for line in printed.splitlines() if HEARTBEAT_MESSAGE in line
    ]

    assert len(beats) == 1
    assert beats[0]["session_open"] is False
    assert beats[0]["waiting_on_socket"] is False
    assert SECRET not in printed


def test_heartbeat_does_not_fire_before_sixty_seconds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_live(
        tmp_path,
        heartbeat=True,
        watch_log=False,
        max_cycles=2,
        step=timedelta(seconds=59),
    )
    captured = capsys.readouterr()
    printed = captured.out + captured.err

    assert HEARTBEAT_MESSAGE not in printed


def test_heartbeat_stays_off_when_only_watch_log_is_on(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level("INFO")
    _run_live(
        tmp_path,
        heartbeat=False,
        watch_log=True,
        max_cycles=3,
        step=timedelta(seconds=30),
    )
    captured = capsys.readouterr()
    printed = captured.out + captured.err

    assert HEARTBEAT_MESSAGE not in caplog.text
    assert " | WATCH | Backfill complete" in printed


def test_watch_log_narrates_a_canned_live_cycle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_live(
        tmp_path,
        heartbeat=False,
        watch_log=True,
        max_cycles=1,
        step=timedelta(seconds=30),
    )
    captured = capsys.readouterr()
    printed = captured.out + captured.err

    assert " | WATCH | Application started" in printed
    assert " | WATCH | Backfill complete" in printed
    assert " | WATCH | Stock stream ready" not in printed
    assert (
        " | WATCH | After-close daily scan" in printed
        or " | WATCH | After-close daily fetch" in printed
    )
    assert SECRET not in printed


def test_watch_helper_omits_secret_fields() -> None:
    configure_watch_log(True)
    captured: list[object] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(getattr(record, "watch", {}))

    watch_logger = logging.getLogger(WATCH_LOGGER_NAME)
    handler = ListHandler()
    watch_logger.addHandler(handler)
    try:
        watch("stage", ticker="TSLA", secret=SECRET, alpaca_api_secret_key=SECRET)
    finally:
        watch_logger.removeHandler(handler)
        configure_watch_log(False)

    assert captured == [{"ticker": "TSLA"}]


def test_json_formatter_includes_heartbeat_fields() -> None:
    record = logging.LogRecord(
        name="investment_assistant.main",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=HEARTBEAT_MESSAGE,
        args=(),
        exc_info=None,
    )
    record.session_open = False
    record.waiting_on_socket = True
    record.last_message_age_seconds = 90.0
    record.last_spy_regular_end_at = None
    text = JsonFormatter().format(record)

    assert HEARTBEAT_MESSAGE in text
    assert "session_open" in text
    assert "waiting_on_socket" in text
    assert SECRET not in text
