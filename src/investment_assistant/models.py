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


class NewsCategory(StrEnum):
    """Controlled event categories for structured news classification."""

    EARNINGS = "EARNINGS"
    GUIDANCE = "GUIDANCE"
    MERGERS_ACQUISITIONS = "MERGERS_ACQUISITIONS"
    REGULATORY_LEGAL = "REGULATORY_LEGAL"
    PRODUCT_SAFETY = "PRODUCT_SAFETY"
    MANAGEMENT = "MANAGEMENT"
    FINANCING_CAPITAL = "FINANCING_CAPITAL"
    OPERATIONS = "OPERATIONS"
    MACRO_SECTOR = "MACRO_SECTOR"
    OTHER = "OTHER"


class NewsDirection(StrEnum):
    """Classifier direction, including stories that are not clearly bullish or bearish."""

    UP = "UP"
    DOWN = "DOWN"
    UNCLEAR = "UNCLEAR"


class ClassificationStatus(StrEnum):
    """Persisted outcome of one article/ticker classification attempt."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    FILTERED = "FILTERED"
    DEFERRED = "DEFERRED"


class MarketWindow(StrEnum):
    """Market horizons planned for the shared signal contract."""

    SESSION_OPEN = "SESSION_OPEN"
    ONE_HOUR = "ONE_HOUR"
    FIVE_DAYS = "FIVE_DAYS"
    TWENTY_DAYS = "TWENTY_DAYS"


class MarketTimeframe(StrEnum):
    """Completed-bar period stored in market history."""

    ONE_MINUTE = "1Min"
    ONE_DAY = "1Day"


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
class MarketBar:
    """One OHLCV period for a ticker.

    Detectors evaluate only bars with ``is_complete=True``. Incomplete bars
    may be stored, but they must still have valid times and OHLCV values.
    """

    ticker: str
    timeframe: MarketTimeframe
    start_at: datetime
    end_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_complete: bool
    provider: str
    feed: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticker", _ticker(self.ticker))
        object.__setattr__(self, "start_at", _as_utc(self.start_at, "start_at"))
        object.__setattr__(self, "end_at", _as_utc(self.end_at, "end_at"))
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        _validate_ohlcv(self.open, self.high, self.low, self.close, self.volume)
        object.__setattr__(self, "provider", _non_blank(self.provider, "provider"))
        object.__setattr__(self, "feed", _non_blank(self.feed, "feed"))
        object.__setattr__(
            self,
            "retrieved_at",
            _as_utc(self.retrieved_at, "retrieved_at"),
        )

    @property
    def bar_id(self) -> str:
        """Return the stable identity used for idempotent bar storage."""

        return f"bar:{self.ticker}:{self.timeframe}:{self.start_at.isoformat()}"


@dataclass(frozen=True, slots=True)
class DetectorState:
    """Last emitted importance for one detector key, or clear/armed."""

    ticker: str
    rule: str
    window: MarketWindow
    direction: SignalDirection
    last_emitted_importance: SignalImportance | None
    updated_at: datetime
    last_evaluated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticker", _ticker(self.ticker))
        object.__setattr__(self, "rule", _non_blank(self.rule, "rule"))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at, "updated_at"))
        if self.last_evaluated_at is not None:
            object.__setattr__(
                self,
                "last_evaluated_at",
                _as_utc(self.last_evaluated_at, "last_evaluated_at"),
            )


@dataclass(frozen=True, slots=True)
class NewsRecord:
    """Normalized news data from one article."""

    symbol: str
    headline: str
    summary: str
    published_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class NewsArticle:
    """Provenance-complete live news article after provider normalization."""

    provider: str
    provider_article_id: str
    symbols: tuple[str, ...]
    headline: str
    summary: str
    content: str
    url: str
    canonical_url: str
    source: str
    created_at: datetime
    updated_at: datetime
    retrieved_at: datetime
    content_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _non_blank(self.provider, "provider"))
        object.__setattr__(
            self,
            "provider_article_id",
            _non_blank(self.provider_article_id, "provider_article_id"),
        )
        object.__setattr__(
            self,
            "symbols",
            tuple(_ticker(symbol) for symbol in self.symbols),
        )
        if not self.symbols:
            raise ValueError("symbols must not be empty")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must not contain duplicates")
        object.__setattr__(
            self,
            "headline",
            _bounded_text(self.headline, "headline", MAX_NEWS_HEADLINE_CHARS),
        )
        object.__setattr__(
            self,
            "summary",
            _bounded_optional_text(self.summary, "summary", MAX_NEWS_SUMMARY_CHARS),
        )
        object.__setattr__(
            self,
            "content",
            _bounded_optional_text(self.content, "content", MAX_NEWS_CONTENT_CHARS),
        )
        object.__setattr__(self, "url", _non_blank(self.url, "url"))
        object.__setattr__(
            self,
            "canonical_url",
            _non_blank(self.canonical_url, "canonical_url"),
        )
        object.__setattr__(self, "source", _non_blank(self.source, "source"))
        object.__setattr__(self, "created_at", _as_utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at, "updated_at"))
        object.__setattr__(
            self,
            "retrieved_at",
            _as_utc(self.retrieved_at, "retrieved_at"),
        )
        object.__setattr__(
            self,
            "content_fingerprint",
            _non_blank(self.content_fingerprint, "content_fingerprint"),
        )

    @property
    def article_id(self) -> str:
        """Return the stable identity used for idempotent article storage."""

        return f"{self.provider}:{self.provider_article_id}"


@dataclass(frozen=True, slots=True)
class NewsClassification:
    """One article/ticker classification attempt, including safe failures."""

    article_id: str
    ticker: str
    prompt_version: str
    model_version: str
    status: ClassificationStatus
    attempted_at: datetime
    relevant: bool | None = None
    category: NewsCategory | None = None
    significant: bool | None = None
    direction: NewsDirection | None = None
    importance: SignalImportance | None = None
    confidence: float | None = None
    rationale: str | None = None
    safe_error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "article_id", _non_blank(self.article_id, "article_id")
        )
        object.__setattr__(self, "ticker", _ticker(self.ticker))
        object.__setattr__(
            self,
            "prompt_version",
            _non_blank(self.prompt_version, "prompt_version"),
        )
        object.__setattr__(
            self,
            "model_version",
            _non_blank(self.model_version, "model_version"),
        )
        object.__setattr__(
            self,
            "attempted_at",
            _as_utc(self.attempted_at, "attempted_at"),
        )
        if self.confidence is not None and (
            not _is_number(self.confidence) or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("confidence must be between 0 and 1")
        if self.rationale is not None:
            object.__setattr__(
                self,
                "rationale",
                _bounded_text(self.rationale, "rationale", MAX_NEWS_RATIONALE_CHARS),
            )
        if self.safe_error is not None:
            object.__setattr__(
                self,
                "safe_error",
                _bounded_text(self.safe_error, "safe_error", MAX_SAFE_ERROR_CHARS),
            )
        if self.status is ClassificationStatus.SUCCEEDED:
            if (
                self.relevant is None
                or self.category is None
                or self.significant is None
                or self.direction is None
                or self.importance is None
                or self.confidence is None
                or self.rationale is None
            ):
                raise ValueError("successful classification requires a complete result")
        else:
            if self.safe_error is None:
                raise ValueError("unsuccessful classification requires safe_error")


@dataclass(frozen=True, slots=True)
class MarketSignal:
    """A normalized qualifying market signal.

    ``price_decline_ratio`` is the non-negative magnitude of the move in
    ``direction`` (a 5% drop and a 5% rise both store ``0.05``).
    """

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
    baseline_price: Decimal | None = None
    observed_price: Decimal | None = None
    comparison_return_ratio: Decimal | None = None

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
        if self.baseline_price is not None and self.baseline_price <= 0:
            raise ValueError("baseline_price must be positive")
        if self.observed_price is not None and self.observed_price <= 0:
            raise ValueError("observed_price must be positive")

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
    episode_open: bool = True
    closed_at: datetime | None = None

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
        if self.closed_at is not None:
            object.__setattr__(self, "closed_at", _as_utc(self.closed_at, "closed_at"))
        if self.episode_open and self.closed_at is not None:
            raise ValueError("open episode must not have closed_at")
        if not self.episode_open and self.closed_at is None:
            raise ValueError("closed episode requires closed_at")


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


MAX_NEWS_HEADLINE_CHARS = 500
MAX_NEWS_SUMMARY_CHARS = 2000
MAX_NEWS_CONTENT_CHARS = 4000
MAX_NEWS_RATIONALE_CHARS = 500
MAX_SAFE_ERROR_CHARS = 300


def _validate_ohlcv(
    open_price: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal,
) -> None:
    for field_name, value in (
        ("open", open_price),
        ("high", high),
        ("low", low),
        ("close", close),
    ):
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")
    if high < low:
        raise ValueError("high must be at least low")
    if high < open_price or high < close:
        raise ValueError("high must be at least open and close")
    if low > open_price or low > close:
        raise ValueError("low must be at most open and close")
    if volume < 0:
        raise ValueError("volume must not be negative")


def _non_blank(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _bounded_text(value: str, field_name: str, max_chars: int) -> str:
    normalized = _non_blank(value, field_name)
    if len(normalized) > max_chars:
        raise ValueError(f"{field_name} must be at most {max_chars} characters")
    return normalized


def _bounded_optional_text(value: str, field_name: str, max_chars: int) -> str:
    normalized = value.strip()
    if len(normalized) > max_chars:
        raise ValueError(f"{field_name} must be at most {max_chars} characters")
    return normalized


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ticker(value: str) -> str:
    return _non_blank(value, "ticker").upper()


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
