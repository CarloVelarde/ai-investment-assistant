"""Tests for Alpaca REST news pagination, isolation, and secret-safe errors."""

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from investment_assistant.clock import FixedClock
from investment_assistant.news import (
    NEWS_API_PATH,
    NEWS_PAGE_SIZE,
    AlpacaNewsProvider,
    NewsHttpResponse,
    NewsPermissionError,
    NewsProviderError,
    article_from_alpaca,
    articles_from_response,
    canonical_url,
    strip_html,
)

START = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
END = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 8, 21, 14, 1, tzinfo=UTC)
SECRET = "test-alpaca-secret-do-not-log"


class ScriptedNewsHttp:
    def __init__(self, responses: list[NewsHttpResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, str]] = []

    def get_news(self, params: Mapping[str, str]) -> NewsHttpResponse:
        self.requests.append(dict(params))
        if not self._responses:
            raise AssertionError("unexpected news request")
        return self._responses.pop(0)


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _item(
    article_id: int = 24843171,
    *,
    symbols: list[str] | None = None,
    headline: str = "Tesla reports record deliveries",
    url: str = "https://www.benzinga.com/news/tesla-deliveries?utm=1",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": article_id,
        "headline": headline,
        "summary": "Vehicle deliveries rose.",
        "content": "<p>Tesla said quarterly deliveries <b>increased</b>.</p><script>alert(1)</script>",
        "author": "Charles Gross",
        "created_at": "2026-08-20T15:00:00Z",
        "updated_at": "2026-08-20T15:00:01Z",
        "url": url,
        "symbols": symbols if symbols is not None else ["TSLA", "tsla"],
        "source": "benzinga",
        "images": [],
    }
    if extra:
        payload.update(extra)
    return payload


def _ok(body: Mapping[str, object]) -> NewsHttpResponse:
    return NewsHttpResponse(status_code=200, body=body)


def _provider(http: ScriptedNewsHttp) -> AlpacaNewsProvider:
    return AlpacaNewsProvider(
        http=http,
        clock=FixedClock(RETRIEVED_AT),
        sleeper=RecordingSleeper(),
        secret=SECRET,
    )


def test_news_request_uses_oldest_first_watchlist_and_page_size() -> None:
    http = ScriptedNewsHttp([_ok({"news": [], "next_page_token": None})])

    page = _provider(http).fetch_news(
        symbols=("tsla", "amd"),
        start=START,
        end=END,
    )

    assert page.articles == ()
    assert http.requests[0]["symbols"] == "TSLA,AMD"
    assert http.requests[0]["sort"] == "asc"
    assert http.requests[0]["limit"] == str(NEWS_PAGE_SIZE)
    assert http.requests[0]["include_content"] == "true"
    assert http.requests[0]["start"].startswith("2026-08-18")


def test_page_token_is_followed_and_omitted_when_absent() -> None:
    http = ScriptedNewsHttp(
        [
            _ok({"news": [_item(1)], "next_page_token": "page-2"}),
            _ok({"news": [_item(2, symbols=["AMD"])], "next_page_token": None}),
        ]
    )
    provider = _provider(http)

    first = provider.fetch_news(symbols=("TSLA", "AMD"), start=START, end=END)
    second = provider.fetch_news(
        symbols=("TSLA", "AMD"),
        start=START,
        end=END,
        page_token="page-2",
    )

    assert first.next_page_token == "page-2"
    assert second.next_page_token is None
    assert http.requests[1]["page_token"] == "page-2"
    assert NEWS_API_PATH == "/v1beta1/news"


def test_html_and_scripts_are_stripped_and_url_is_canonicalized() -> None:
    article = article_from_alpaca(_item(), retrieved_at=RETRIEVED_AT)

    assert "increased" in article.content
    assert "<" not in article.content
    assert "alert" not in article.content
    assert article.canonical_url == "https://www.benzinga.com/news/tesla-deliveries"
    assert article.symbols == ("TSLA",)
    assert article.provider == "alpaca"
    assert article.retrieved_at == RETRIEVED_AT
    assert article.content_fingerprint


def test_malformed_sibling_does_not_stop_valid_articles() -> None:
    articles, diagnostics = articles_from_response(
        {
            "news": [
                _item(1),
                "not-an-object",
                _item(2, extra={"headline": ""}),
                _item(3, symbols=["AMD"]),
            ]
        },
        retrieved_at=RETRIEVED_AT,
    )

    assert [article.provider_article_id for article in articles] == ["1", "3"]
    assert len(diagnostics) == 2


def test_permission_failure_is_secret_safe() -> None:
    http = ScriptedNewsHttp(
        [NewsHttpResponse(status_code=403, body={"message": SECRET})]
    )

    with pytest.raises(NewsPermissionError, match="403") as error:
        _provider(http).fetch_news(symbols=("TSLA",), start=START, end=END)

    assert SECRET not in str(error.value)


def test_rate_limit_retries_then_fails_safely() -> None:
    sleeper = RecordingSleeper()
    http = ScriptedNewsHttp(
        [NewsHttpResponse(status_code=429, body={}, headers={"Retry-After": "1"})] * 5
    )
    provider = AlpacaNewsProvider(
        http=http,
        clock=FixedClock(RETRIEVED_AT),
        sleeper=sleeper,
        secret=SECRET,
    )

    with pytest.raises(NewsProviderError, match="rate limited"):
        provider.fetch_news(symbols=("TSLA",), start=START, end=END)

    assert sleeper.delays
    assert SECRET not in str(sleeper.delays)


def test_canonical_url_and_html_helpers() -> None:
    assert (
        canonical_url("HTTPS://Example.COM/Path/?q=1#frag")
        == "https://example.com/Path"
    )
    assert strip_html("<style>x</style><p>Hello <b>TSLA</b></p>") == "Hello TSLA"
