"""News polling stays independent of the regular session and stock socket."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import SecretStr

from investment_assistant.clock import SteppingClock
from investment_assistant.config import Settings
from investment_assistant.main import main
from investment_assistant.market_data import FakeMarketData, MarketSession
from investment_assistant.models import (
    ClassificationStatus,
    EventStatus,
    MarketBar,
    MarketTimeframe,
    NewsArticle,
    NewsCategory,
    NewsClassification,
    NewsDirection,
    SignalImportance,
)
from investment_assistant.news import FakeNewsProvider, NewsPage, NewsProviderError
from investment_assistant.news_classifier import (
    CLASSIFIER_MODEL,
    CLASSIFIER_PROMPT_VERSION,
    FakeNewsClassifier,
)
from investment_assistant.storage import SQLiteStorage

CLOSED_AT = datetime(2026, 8, 21, 21, 5, tzinfo=UTC)
CLOSED_SESSION = MarketSession(
    is_open=False,
    timestamp=CLOSED_AT,
    next_open=datetime(2026, 8, 24, 13, 30, tzinfo=UTC),
    next_close=datetime(2026, 8, 24, 20, 0, tzinfo=UTC),
)
OPEN_AT = datetime(2026, 8, 21, 15, 31, tzinfo=UTC)
OPEN_SESSION = MarketSession(
    is_open=True,
    timestamp=OPEN_AT,
    next_open=datetime(2026, 8, 24, 13, 30, tzinfo=UTC),
    next_close=datetime(2026, 8, 21, 20, 0, tzinfo=UTC),
)


def _daily() -> MarketBar:
    start_at = datetime(2026, 8, 21, 13, 30, tzinfo=UTC)
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


def _article() -> NewsArticle:
    created = CLOSED_AT - timedelta(hours=2)
    return NewsArticle(
        provider="alpaca",
        provider_article_id="live-1",
        symbols=("TSLA",),
        headline="Tesla announces a product recall",
        summary="A safety recall was announced.",
        content="Tesla is recalling vehicles.",
        url="https://www.benzinga.com/news/tesla-recall",
        canonical_url="https://www.benzinga.com/news/tesla-recall",
        source="benzinga",
        created_at=created,
        updated_at=created,
        retrieved_at=CLOSED_AT,
        content_fingerprint="c" * 64,
    )


def _success(article: NewsArticle) -> NewsClassification:
    return NewsClassification(
        article_id=article.article_id,
        ticker="TSLA",
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        model_version=CLASSIFIER_MODEL,
        status=ClassificationStatus.SUCCEEDED,
        attempted_at=CLOSED_AT,
        relevant=True,
        category=NewsCategory.PRODUCT_SAFETY,
        significant=True,
        direction=NewsDirection.DOWN,
        importance=SignalImportance.HIGH,
        confidence=0.88,
        rationale="A product recall is material for Tesla.",
    )


def _settings(database_path: Path) -> Settings:
    return Settings(
        alpaca_api_key_id="test-key-id",
        alpaca_api_secret_key=SecretStr("test-alpaca-secret-do-not-log"),
        watchlist="TSLA",
        database_path=database_path,
    )


def test_news_polls_after_close_without_opening_the_stock_socket(
    tmp_path: Path,
) -> None:
    article = _article()
    research_calls: list[str] = []

    def researcher(event, signals):  # type: ignore[no-untyped-def]
        from investment_assistant.reporting import create_fake_research_report

        research_calls.append(event.event_id)
        return create_fake_research_report(event, signals)

    main(
        settings=_settings(tmp_path / "closed-news.sqlite3"),
        provider=FakeMarketData(history=(_daily(),), session=CLOSED_SESSION),
        news_provider=FakeNewsProvider((NewsPage(articles=(article,)),)),
        classifier=FakeNewsClassifier({("live-1", "TSLA"): _success(article)}),
        clock=SteppingClock(CLOSED_AT),
        loop=False,
        sleeper=lambda _seconds: None,
        researcher=researcher,
        notifier=lambda *_: None,
    )

    with SQLiteStorage(tmp_path / "closed-news.sqlite3") as storage:
        events = storage.list_events()
        assert len(events) == 1
        assert events[0].status is EventStatus.NOTIFIED
        assert storage.get_news_article(article.article_id) is not None
    assert research_calls


def test_news_failure_does_not_stop_market_processing(tmp_path: Path) -> None:
    database_path = tmp_path / "news-fail.sqlite3"
    bar = _daily()
    result = main(
        settings=_settings(database_path),
        provider=FakeMarketData(history=(bar,), session=CLOSED_SESSION),
        news_provider=FakeNewsProvider(
            error=NewsProviderError("Alpaca news request failed with status 500")
        ),
        classifier=FakeNewsClassifier(),
        clock=SteppingClock(CLOSED_AT),
        loop=False,
        sleeper=lambda _seconds: None,
        notifier=lambda *_: None,
    )

    assert result is not None
    assert bar.bar_id in result.persisted_bar_ids
    with SQLiteStorage(database_path) as storage:
        assert storage.get_market_bar(bar.bar_id) == bar
        assert storage.list_events() == ()


def test_existing_live_cycle_without_news_provider_stays_quiet(tmp_path: Path) -> None:
    database_path = tmp_path / "no-news.sqlite3"
    main(
        settings=_settings(database_path),
        provider=FakeMarketData(history=(_daily(),), session=OPEN_SESSION),
        clock=SteppingClock(OPEN_AT),
        loop=False,
        sleeper=lambda _seconds: None,
        notifier=lambda *_: None,
    )

    with SQLiteStorage(database_path) as storage:
        assert storage.list_news_articles_since(OPEN_AT - timedelta(days=3)) == ()
        assert storage.list_events() == ()
