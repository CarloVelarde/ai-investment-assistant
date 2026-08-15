"""Tests for controllable application time."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from investment_assistant.clock import Clock, FixedClock, SteppingClock, SystemClock


def test_fixed_clock_returns_injected_time_without_advancing() -> None:
    injected_time = datetime(2026, 1, 15, 16, 0, tzinfo=UTC)
    clock: Clock = FixedClock(injected_time)

    assert clock.now() == injected_time
    assert clock.now() == injected_time


def test_fixed_clock_normalizes_time_to_utc() -> None:
    central_time = timezone(-timedelta(hours=6))
    clock = FixedClock(datetime(2026, 1, 15, 10, 0, tzinfo=central_time))

    assert clock.now() == datetime(2026, 1, 15, 16, 0, tzinfo=UTC)
    assert clock.now().tzinfo is UTC


def test_fixed_clock_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 1, 15, 16, 0))


def test_stepping_clock_advances_to_an_explicit_time() -> None:
    clock = SteppingClock(datetime(2026, 2, 2, 14, 30, tzinfo=UTC))
    clock.advance_to(datetime(2026, 2, 2, 15, 31, tzinfo=UTC))

    assert clock.now() == datetime(2026, 2, 2, 15, 31, tzinfo=UTC)


def test_system_clock_returns_timezone_aware_utc() -> None:
    clock: Clock = SystemClock()

    now = clock.now()

    assert now.tzinfo is UTC
    assert now <= datetime.now(UTC) + timedelta(seconds=1)


def test_stepping_clock_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SteppingClock(datetime(2026, 2, 2, 14, 30))
