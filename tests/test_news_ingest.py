"""Tests for news promotion, rejection, and restart-safe identity."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from investment_assistant.clock import FixedClock
from investment_assistant.event_manager import EventManager
from investment_assistant.models import (
    ClassificationStatus,
    EventStatus,
    NewsArticle,
    NewsCategory,
    NewsClassification,
    NewsDirection,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.news import FakeNewsProvider, NewsPage, NewsProviderError
from investment_assistant.news_classifier import (
    CLASSIFIER_MODEL,
    CLASSIFIER_PROMPT_VERSION,
    FakeNewsClassifier,
    NewsClassifierError,
)
from investment_assistant.news_ingest import NEWS_QUERY_OVERLAP, poll_and_classify_news
from investment_assistant.storage import SQLiteStorage

NOW = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)


def _article(
    article_id: str,
    *,
    symbols: tuple[str, ...] = ("TSLA",),
    headline: str = "Tesla reports a material development",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    url: str | None = None,
) -> NewsArticle:
    created = created_at or (NOW - timedelta(hours=1))
    path = url or f"https://www.benzinga.com/news/{article_id}"
    return NewsArticle(
        provider="alpaca",
        provider_article_id=article_id,
        symbols=symbols,
        headline=headline,
        summary="A company development.",
        content="Details of the development.",
        url=path,
        canonical_url=path,
        source="benzinga",
        created_at=created,
        updated_at=updated_at or created,
        retrieved_at=NOW,
        content_fingerprint=article_id.ljust(64, "x")[:64],
    )


def _classified(
    article: NewsArticle,
    ticker: str,
    *,
    direction: NewsDirection,
    category: NewsCategory = NewsCategory.EARNINGS,
    relevant: bool = True,
    significant: bool = True,
    confidence: float = 0.92,
    importance: SignalImportance = SignalImportance.HIGH,
) -> NewsClassification:
    return NewsClassification(
        article_id=article.article_id,
        ticker=ticker,
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        model_version=CLASSIFIER_MODEL,
        status=ClassificationStatus.SUCCEEDED,
        attempted_at=NOW,
        relevant=relevant,
        category=category,
        significant=significant,
        direction=direction,
        importance=importance,
        confidence=confidence,
        rationale="Grounded in the supplied article.",
    )


def _poll(
    tmp_path: Path,
    articles: tuple[NewsArticle, ...],
    classifier: FakeNewsClassifier,
    *,
    watchlist: tuple[str, ...] = ("TSLA",),
    name: str = "news.sqlite3",
    storage: SQLiteStorage | None = None,
) -> SQLiteStorage:
    if storage is None:
        storage = SQLiteStorage(tmp_path / name)
        storage.initialize()
    manager = EventManager(storage, clock=FixedClock(NOW))
    poll_and_classify_news(
        storage=storage,
        manager=manager,
        provider=FakeNewsProvider((NewsPage(articles=articles),)),
        classifier=classifier,
        watchlist=watchlist,
        clock=FixedClock(NOW),
    )
    return storage


def test_positive_negative_and_unclear_news_each_create_one_event(
    tmp_path: Path,
) -> None:
    beat = _article("beat", headline="Tesla beats earnings estimates")
    miss = _article("miss", headline="Tesla misses earnings estimates")
    legal = _article("legal", headline="Tesla names a special committee")
    classifier = FakeNewsClassifier(
        {
            ("beat", "TSLA"): _classified(beat, "TSLA", direction=NewsDirection.UP),
            ("miss", "TSLA"): _classified(
                miss,
                "TSLA",
                direction=NewsDirection.DOWN,
                category=NewsCategory.PRODUCT_SAFETY,
            ),
            ("legal", "TSLA"): _classified(
                legal,
                "TSLA",
                direction=NewsDirection.UNCLEAR,
                category=NewsCategory.MANAGEMENT,
            ),
        }
    )

    storage = _poll(tmp_path, (beat, miss, legal), classifier)
    events = storage.list_events()

    assert len(events) == 3
    directions = {event.direction for event in events}
    assert SignalDirection.UP in directions
    assert SignalDirection.DOWN in directions
    assert None in directions
    assert all(event.status is EventStatus.QUEUED for event in events)
    assert all(event.category is not None for event in events)


def test_insignificant_and_irrelevant_results_create_no_event(tmp_path: Path) -> None:
    weak = _article("weak")
    unrelated = _article("other")
    classifier = FakeNewsClassifier(
        {
            ("weak", "TSLA"): _classified(
                weak,
                "TSLA",
                direction=NewsDirection.DOWN,
                significant=False,
            ),
            ("other", "TSLA"): _classified(
                unrelated,
                "TSLA",
                direction=NewsDirection.UNCLEAR,
                relevant=False,
                significant=False,
            ),
        }
    )

    storage = _poll(tmp_path, (weak, unrelated), classifier)

    assert storage.list_events() == ()
    assert set(classifier.calls) == {("weak", "TSLA"), ("other", "TSLA")}
    saved = storage.get_news_classification(
        weak.article_id,
        "TSLA",
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        model_version=CLASSIFIER_MODEL,
    )
    assert saved is not None
    assert saved.status is ClassificationStatus.SUCCEEDED
    assert saved.significant is False


def test_related_news_enriches_existing_market_event_once(tmp_path: Path) -> None:
    from decimal import Decimal

    from investment_assistant.models import (
        MarketSignal,
        MarketWindow,
        SourceDetails,
    )

    storage = SQLiteStorage(tmp_path / "enrich.sqlite3")
    storage.initialize()
    manager = EventManager(storage, clock=FixedClock(NOW))
    market = MarketSignal(
        signal_id="market-tsla-1",
        ticker="TSLA",
        occurred_at=NOW - timedelta(minutes=10),
        importance=SignalImportance.HIGH,
        source_details=SourceDetails(
            provider="alpaca",
            source="iex",
            feed="iex",
            retrieved_at=NOW,
        ),
        direction=SignalDirection.DOWN,
        rule="abrupt_move",
        window=MarketWindow.ONE_HOUR,
        price_decline_ratio=Decimal("0.05"),
        volume_ratio=Decimal("1.6"),
        baseline_price=Decimal("100"),
        observed_price=Decimal("95"),
    )
    first = manager.handle_signal(market)
    article = _article("guidance", headline="Tesla cuts annual guidance")
    classifier = FakeNewsClassifier(
        {
            ("guidance", "TSLA"): _classified(
                article,
                "TSLA",
                direction=NewsDirection.DOWN,
                category=NewsCategory.GUIDANCE,
            )
        }
    )
    poll_and_classify_news(
        storage=storage,
        manager=manager,
        provider=FakeNewsProvider((NewsPage(articles=(article,)),)),
        classifier=classifier,
        watchlist=("TSLA",),
        clock=FixedClock(NOW),
    )
    events = storage.list_events()
    signals = storage.list_signals(events[0].event_id)

    assert first.event is not None
    assert len(events) == 1
    assert events[0].event_id == first.event.event_id
    assert events[0].current_update == 2
    assert events[0].status is EventStatus.QUEUED
    assert len(signals) == 2
    poll_and_classify_news(
        storage=storage,
        manager=manager,
        provider=FakeNewsProvider((NewsPage(articles=(article,)),)),
        classifier=classifier,
        watchlist=("TSLA",),
        clock=FixedClock(NOW),
    )
    assert len(storage.list_events()) == 1
    assert len(storage.list_signals(events[0].event_id)) == 2
    assert classifier.calls == [("guidance", "TSLA")]


def test_same_article_restart_does_not_reclassify_or_duplicate_signal(
    tmp_path: Path,
) -> None:
    article = _article("once")
    classifier = FakeNewsClassifier(
        {("once", "TSLA"): _classified(article, "TSLA", direction=NewsDirection.UP)}
    )
    storage = _poll(tmp_path, (article,), classifier)
    _poll(tmp_path, (article,), classifier, storage=storage)

    assert classifier.calls == [("once", "TSLA")]
    assert len(storage.list_events()) == 1
    assert len(storage.list_signals(storage.list_events()[0].event_id)) == 1


def test_provider_id_revision_is_saved_but_quiet(tmp_path: Path) -> None:
    original = _article("rev", headline="Tesla announces an investigation")
    revised = _article("rev", headline="Tesla updates an investigation")
    classifier = FakeNewsClassifier(
        {
            ("rev", "TSLA"): _classified(
                original,
                "TSLA",
                direction=NewsDirection.DOWN,
                category=NewsCategory.REGULATORY_LEGAL,
            )
        }
    )
    storage = _poll(tmp_path, (original,), classifier)
    _poll(tmp_path, (revised,), classifier, storage=storage)

    saved = storage.get_news_article(original.article_id)
    assert saved is not None
    assert saved.headline == "Tesla updates an investigation"
    assert classifier.calls == [("rev", "TSLA")]
    assert len(storage.list_events()) == 1


def test_classifier_failure_is_retryable_and_creates_no_event(tmp_path: Path) -> None:
    article = _article("fail")
    classifier = FakeNewsClassifier(
        {("fail", "TSLA"): NewsClassifierError("classification incomplete")}
    )
    storage = _poll(tmp_path, (article,), classifier, name="fail.sqlite3")
    saved = storage.get_news_classification(
        article.article_id,
        "TSLA",
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        model_version=CLASSIFIER_MODEL,
    )

    assert saved is not None
    assert saved.status is ClassificationStatus.FAILED
    assert storage.list_events() == ()
    assert storage.classifier_call_count(NOW.date().isoformat()) == 1

    classifier._results[("fail", "TSLA")] = _classified(
        article,
        "TSLA",
        direction=NewsDirection.DOWN,
    )
    _poll(tmp_path, (article,), classifier, storage=storage)
    assert len(storage.list_events()) == 1
    assert classifier.calls == [("fail", "TSLA"), ("fail", "TSLA")]


def test_news_provider_failure_keeps_earlier_pages_and_does_not_invent_signals(
    tmp_path: Path,
) -> None:
    article = _article("kept")
    storage = SQLiteStorage(tmp_path / "partial.sqlite3")
    storage.initialize()
    manager = EventManager(storage, clock=FixedClock(NOW))
    provider = FakeNewsProvider(
        (NewsPage(articles=(article,), next_page_token="page-2"),),
        error=None,
    )
    classifier = FakeNewsClassifier(
        {("kept", "TSLA"): _classified(article, "TSLA", direction=NewsDirection.UP)}
    )
    poll_and_classify_news(
        storage=storage,
        manager=manager,
        provider=provider,
        classifier=classifier,
        watchlist=("TSLA",),
        clock=FixedClock(NOW),
    )
    provider._error = NewsProviderError("Alpaca news request failed with status 500")
    poll_and_classify_news(
        storage=storage,
        manager=manager,
        provider=provider,
        classifier=classifier,
        watchlist=("TSLA",),
        clock=FixedClock(NOW),
    )

    assert storage.get_news_article(article.article_id) is not None
    assert len(storage.list_events()) == 1
    assert classifier.calls == [("kept", "TSLA")]


def test_later_poll_overlaps_the_stored_high_water_mark(tmp_path: Path) -> None:
    created = NOW - timedelta(hours=10)
    updated = NOW - timedelta(hours=1)
    article = _article("hw", created_at=created, updated_at=updated)
    classifier = FakeNewsClassifier(
        {("hw", "TSLA"): _classified(article, "TSLA", direction=NewsDirection.UP)}
    )
    storage = _poll(tmp_path, (article,), classifier, name="high-water.sqlite3")
    provider = FakeNewsProvider((NewsPage(articles=()),))
    poll_and_classify_news(
        storage=storage,
        manager=EventManager(storage, clock=FixedClock(NOW)),
        provider=provider,
        classifier=classifier,
        watchlist=("TSLA",),
        clock=FixedClock(NOW),
    )

    assert storage.get_news_high_water("alpaca") == updated
    assert provider.requests[0]["start"] == updated - NEWS_QUERY_OVERLAP
    assert classifier.calls == [("hw", "TSLA")]
