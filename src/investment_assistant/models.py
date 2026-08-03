"""Normalized records and durable application models."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class SignalImportance(StrEnum):
    """Ordered importance assigned before a signal reaches the event manager."""

    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        """Return the deterministic ordering used by event promotion."""

        return _IMPORTANCE_RANK[self]


_IMPORTANCE_RANK = {
    SignalImportance.MODERATE: 1,
    SignalImportance.HIGH: 2,
    SignalImportance.CRITICAL: 3,
}


class SignalDirection(StrEnum):
    """Shared directional meaning for market and news signals."""

    UP = "UP"
    DOWN = "DOWN"


class MarketWindow(StrEnum):
    """Market horizons planned for the shared signal contract."""

    ONE_HOUR = "ONE_HOUR"
    FIVE_DAYS = "FIVE_DAYS"
    TWENTY_DAYS = "TWENTY_DAYS"


class EventStatus(StrEnum):
    """Persisted processing state for the current event update."""

    QUEUED = "QUEUED"
    RESEARCHING = "RESEARCHING"
    REPORTED = "REPORTED"
    NOTIFIED = "NOTIFIED"
    FAILED = "FAILED"


class FailureStep(StrEnum):
    """Application step that produced a persisted failure."""

    RESEARCH = "RESEARCH"
    NOTIFICATION = "NOTIFICATION"


@dataclass(frozen=True, slots=True)
class SourceDetails:
    """Provenance needed to explain and replay a signal."""

    provider: str
    source: str
    feed: str | None
    retrieved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _non_blank(self.provider, "provider"))
        object.__setattr__(self, "source", _non_blank(self.source, "source"))
        if self.feed is not None:
            object.__setattr__(self, "feed", _non_blank(self.feed, "feed"))
        object.__setattr__(
            self,
            "retrieved_at",
            _as_utc(self.retrieved_at, "retrieved_at"),
        )


@dataclass(frozen=True, slots=True)
class MarketRecord:
    """Normalized market data from one observation."""

    symbol: str
    latest_price: Decimal
    previous_close: Decimal
    current_volume: Decimal
    average_volume: Decimal
    occurred_at: datetime
    provider: str
    feed: str


@dataclass(frozen=True, slots=True)
class NewsRecord:
    """Normalized news data from one article."""

    symbol: str
    headline: str
    summary: str
    published_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class MarketSignal:
    """A normalized qualifying market signal."""

    signal_id: str
    ticker: str
    occurred_at: datetime
    importance: SignalImportance
    source_details: SourceDetails
    direction: SignalDirection
    rule: str
    window: MarketWindow
    price_decline_ratio: Decimal
    volume_ratio: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_id", _non_blank(self.signal_id, "signal_id"))
        object.__setattr__(self, "ticker", _ticker(self.ticker))
        object.__setattr__(
            self,
            "occurred_at",
            _as_utc(self.occurred_at, "occurred_at"),
        )
        object.__setattr__(self, "rule", _non_blank(self.rule, "rule"))
        if self.price_decline_ratio < 0:
            raise ValueError("price_decline_ratio must not be negative")
        if self.volume_ratio < 0:
            raise ValueError("volume_ratio must not be negative")

    @property
    def symbol(self) -> str:
        """Return the canonical ticker for walking-skeleton compatibility."""

        return self.ticker

    @property
    def provider(self) -> str:
        """Return the provenance provider."""

        return self.source_details.provider

    @property
    def feed(self) -> str | None:
        """Return the provenance feed."""

        return self.source_details.feed


@dataclass(frozen=True, slots=True)
class NewsSignal:
    """A normalized significant news signal."""

    signal_id: str
    ticker: str
    occurred_at: datetime
    importance: SignalImportance
    source_details: SourceDetails
    category: str
    direction: SignalDirection | None
    headline: str
    matched_phrase: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_id", _non_blank(self.signal_id, "signal_id"))
        object.__setattr__(self, "ticker", _ticker(self.ticker))
        object.__setattr__(
            self,
            "occurred_at",
            _as_utc(self.occurred_at, "occurred_at"),
        )
        object.__setattr__(self, "category", _non_blank(self.category, "category"))
        object.__setattr__(self, "headline", _non_blank(self.headline, "headline"))
        object.__setattr__(
            self,
            "matched_phrase",
            _non_blank(self.matched_phrase, "matched_phrase"),
        )

    @property
    def symbol(self) -> str:
        """Return the canonical ticker for walking-skeleton compatibility."""

        return self.ticker

    @property
    def source(self) -> str:
        """Return the original news source."""

        return self.source_details.source


type Signal = MarketSignal | NewsSignal


@dataclass(frozen=True, slots=True)
class Event:
    """One durable situation grouping related qualifying signals."""

    event_id: str
    ticker: str
    direction: SignalDirection | None
    category: str | None
    importance: SignalImportance
    market_windows: tuple[MarketWindow, ...]
    current_update: int
    status: EventStatus
    created_at: datetime
    updated_at: datetime
    last_notified_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _non_blank(self.event_id, "event_id"))
        object.__setattr__(self, "ticker", _ticker(self.ticker))
        if self.category is not None:
            object.__setattr__(
                self,
                "category",
                _non_blank(self.category, "category"),
            )
        if self.direction is None and self.category is None:
            raise ValueError("event requires a direction or category")
        if self.current_update < 1:
            raise ValueError("current_update must be at least 1")
        if len(set(self.market_windows)) != len(self.market_windows):
            raise ValueError("market_windows must not contain duplicates")
        object.__setattr__(self, "created_at", _as_utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at, "updated_at"))
        if self.last_notified_at is not None:
            object.__setattr__(
                self,
                "last_notified_at",
                _as_utc(self.last_notified_at, "last_notified_at"),
            )


@dataclass(frozen=True, slots=True)
class SignificantEvent:
    """One correlated market and news event from the walking skeleton."""

    symbol: str
    occurred_at: datetime
    market_signal: MarketSignal
    news_signal: NewsSignal


@dataclass(frozen=True, slots=True)
class ResearchReport:
    """Structured research output for one specific event update."""

    report_id: str
    event_id: str
    event_update: int
    ticker: str
    event_occurred_at: datetime
    created_at: datetime
    summary: str
    is_fake: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", _non_blank(self.report_id, "report_id"))
        object.__setattr__(self, "event_id", _non_blank(self.event_id, "event_id"))
        if self.event_update < 1:
            raise ValueError("event_update must be at least 1")
        object.__setattr__(self, "ticker", _ticker(self.ticker))
        object.__setattr__(
            self,
            "event_occurred_at",
            _as_utc(self.event_occurred_at, "event_occurred_at"),
        )
        object.__setattr__(self, "created_at", _as_utc(self.created_at, "created_at"))
        object.__setattr__(self, "summary", _non_blank(self.summary, "summary"))

    @property
    def symbol(self) -> str:
        """Return the canonical ticker for walking-skeleton compatibility."""

        return self.ticker


@dataclass(frozen=True, slots=True)
class NotificationAttempt:
    """One persisted attempt to notify for an event update."""

    attempt_id: str
    event_id: str
    event_update: int
    attempted_at: datetime
    succeeded: bool
    safe_error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "attempt_id", _non_blank(self.attempt_id, "attempt_id")
        )
        object.__setattr__(self, "event_id", _non_blank(self.event_id, "event_id"))
        if self.event_update < 1:
            raise ValueError("event_update must be at least 1")
        object.__setattr__(
            self,
            "attempted_at",
            _as_utc(self.attempted_at, "attempted_at"),
        )
        if self.safe_error is not None:
            object.__setattr__(
                self,
                "safe_error",
                _non_blank(self.safe_error, "safe_error"),
            )


@dataclass(frozen=True, slots=True)
class ProcessingFailure:
    """A safe persisted description of an unfinished processing step."""

    failure_id: str
    event_id: str
    event_update: int
    step: FailureStep
    retryable: bool
    occurred_at: datetime
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "failure_id", _non_blank(self.failure_id, "failure_id")
        )
        object.__setattr__(self, "event_id", _non_blank(self.event_id, "event_id"))
        if self.event_update < 1:
            raise ValueError("event_update must be at least 1")
        object.__setattr__(
            self,
            "occurred_at",
            _as_utc(self.occurred_at, "occurred_at"),
        )
        object.__setattr__(
            self,
            "description",
            _non_blank(self.description, "description"),
        )


def _non_blank(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _ticker(value: str) -> str:
    return _non_blank(value, "ticker").upper()


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
