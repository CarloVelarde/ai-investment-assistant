"""Controllable application time."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Provide the current application time."""

    def now(self) -> datetime:
        """Return the current timezone-aware time."""

        ...


@dataclass(frozen=True, slots=True)
class FixedClock:
    """Return one injected time without reading the system clock."""

    current_time: datetime

    def __post_init__(self) -> None:
        if self.current_time.utcoffset() is None:
            raise ValueError("fixed time must be timezone-aware")
        object.__setattr__(self, "current_time", self.current_time.astimezone(UTC))

    def now(self) -> datetime:
        """Return the fixed UTC time."""

        return self.current_time
