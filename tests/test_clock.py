"""Tests for controllable application time."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from investment_assistant.clock import Clock, FixedClock


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
