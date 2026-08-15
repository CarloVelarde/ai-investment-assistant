"""Tests for Alpaca REST history pagination, 429 backoff, and SIP 403."""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investment_assistant.alpaca import (
    SIP_PERMISSION_HINT,
    AlpacaMarketData,
    HistoryHttpResponse,
)
from investment_assistant.clock import FixedClock
from investment_assistant.market_data import (
    AlpacaFeed,
    MarketDataError,
    MarketDataPermissionError,
    fetch_all_history,
)
from investment_assistant.models import MarketTimeframe

START = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
END = datetime(2026, 2, 2, 15, 30, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 2, 2, 15, 31, tzinfo=UTC)


class ScriptedHistoryHttp:
    def __init__(self, responses: list[HistoryHttpResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, str]] = []

    def get_stock_bars(self, params: Mapping[str, str]) -> HistoryHttpResponse:
        self.requests.append(dict(params))
        if not self._responses:
            raise AssertionError("unexpected history request")
        return self._responses.pop(0)


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _bar_payload(start: datetime, close: float) -> dict[str, object]:
    return {
        "t": start.isoformat().replace("+00:00", "Z"),
        "o": close,
        "h": close,
        "l": close,
        "c": close,
        "v": 1000,
        "n": 10,
        "vw": close,
    }


def _ok(body: Mapping[str, object]) -> HistoryHttpResponse:
    return HistoryHttpResponse(status_code=200, body=body)


def _provider(
    http: ScriptedHistoryHttp,
    sleeper: RecordingSleeper | None = None,
    feed: AlpacaFeed = "iex",
) -> AlpacaMarketData:
    return AlpacaMarketData(
        http=http,
        clock=FixedClock(RETRIEVED_AT),
        feed=feed,
        sleeper=sleeper or RecordingSleeper(),
    )


def test_multi_symbol_page_maps_each_series() -> None:
    http = ScriptedHistoryHttp(
        [
            _ok(
                {
                    "bars": {
                        "AAPL": [_bar_payload(START, 178.21)],
                        "TSLA": [_bar_payload(START, 250.1)],
                    },
                    "next_page_token": None,
                }
            )
        ]
    )
    provider = _provider(http)

    page = provider.fetch_history(
        symbols=("AAPL", "TSLA"),
        timeframe=MarketTimeframe.ONE_MINUTE,
        start=START,
        end=END,
    )

    assert [bar.ticker for bar in page.bars] == ["AAPL", "TSLA"]
    assert [bar.close for bar in page.bars] == [Decimal("178.21"), Decimal("250.1")]
    assert page.bars[0].retrieved_at == RETRIEVED_AT
    assert all(bar.provider == "alpaca" for bar in page.bars)
    assert all(bar.feed == "iex" for bar in page.bars)
    assert all(bar.is_complete for bar in page.bars)
    assert http.requests[0]["adjustment"] == "raw"
    assert http.requests[0]["feed"] == "iex"
    assert http.requests[0]["timeframe"] == "1Min"


def test_daily_history_requests_split_adjustment() -> None:
    http = ScriptedHistoryHttp([_ok({"bars": {}, "next_page_token": None})])
    provider = _provider(http, feed="sip")

    provider.fetch_history(
        symbols=("SPY",),
        timeframe=MarketTimeframe.ONE_DAY,
        start=START,
        end=END,
    )

    assert http.requests[0]["adjustment"] == "split"
    assert http.requests[0]["feed"] == "sip"
    assert http.requests[0]["timeframe"] == "1Day"


def test_fetch_all_history_follows_page_tokens() -> None:
    http = ScriptedHistoryHttp(
        [
            _ok(
                {
                    "bars": {"AAPL": [_bar_payload(START, 1)]},
                    "next_page_token": "page-2",
                }
            ),
            _ok(
                {
                    "bars": {"TSLA": [_bar_payload(START, 2)]},
                    "next_page_token": None,
                }
            ),
        ]
    )
    provider = _provider(http)

    bars = fetch_all_history(
        provider,
        symbols=("AAPL", "TSLA"),
        timeframe=MarketTimeframe.ONE_MINUTE,
        start=START,
        end=END,
    )

    assert [bar.ticker for bar in bars] == ["AAPL", "TSLA"]
    assert http.requests[1]["page_token"] == "page-2"


def test_rate_limit_retries_after_retry_after_header() -> None:
    sleeper = RecordingSleeper()
    http = ScriptedHistoryHttp(
        [
            HistoryHttpResponse(
                status_code=429,
                body={"message": "too many requests"},
                headers={"Retry-After": "2"},
            ),
            _ok(
                {
                    "bars": {"TSLA": [_bar_payload(START, 100)]},
                    "next_page_token": None,
                }
            ),
        ]
    )
    provider = _provider(http, sleeper)

    page = provider.fetch_history(
        symbols=("TSLA",),
        timeframe=MarketTimeframe.ONE_MINUTE,
        start=START,
        end=END,
    )

    assert len(page.bars) == 1
    assert sleeper.delays == [2.0]
    assert len(http.requests) == 2


def test_sip_permission_failure_is_a_configuration_error() -> None:
    sleeper = RecordingSleeper()
    http = ScriptedHistoryHttp(
        [
            HistoryHttpResponse(
                status_code=403,
                body={
                    "message": "subscription does not permit querying recent SIP data"
                },
                headers={},
            )
        ]
    )
    provider = _provider(http, sleeper, feed="sip")

    with pytest.raises(MarketDataPermissionError, match="SIP") as error:
        provider.fetch_history(
            symbols=("TSLA",),
            timeframe=MarketTimeframe.ONE_MINUTE,
            start=START,
            end=END,
        )

    assert SIP_PERMISSION_HINT in str(error.value)
    assert "secret" not in str(error.value).lower()
    assert sleeper.delays == []
    assert len(http.requests) == 1


def test_other_forbidden_errors_are_not_sip_permission_errors() -> None:
    http = ScriptedHistoryHttp(
        [
            HistoryHttpResponse(
                status_code=403,
                body={"message": "forbidden"},
                headers={},
            )
        ]
    )
    provider = _provider(http)

    with pytest.raises(MarketDataError, match="status 403") as error:
        provider.fetch_history(
            symbols=("TSLA",),
            timeframe=MarketTimeframe.ONE_MINUTE,
            start=START,
            end=END,
        )

    assert not isinstance(error.value, MarketDataPermissionError)
    assert "secret" not in str(error.value).lower()


def test_invalid_bar_for_one_symbol_does_not_drop_the_others() -> None:
    http = ScriptedHistoryHttp(
        [
            _ok(
                {
                    "bars": {
                        "BAD": [{**_bar_payload(START, 1), "o": 0}],
                        "TSLA": [_bar_payload(START, 250)],
                    },
                    "next_page_token": None,
                }
            )
        ]
    )
    provider = _provider(http)

    page = provider.fetch_history(
        symbols=("BAD", "TSLA"),
        timeframe=MarketTimeframe.ONE_MINUTE,
        start=START,
        end=END,
    )

    assert [bar.ticker for bar in page.bars] == ["TSLA"]


def test_exhausted_rate_limit_raises_without_secret() -> None:
    sleeper = RecordingSleeper()
    http = ScriptedHistoryHttp(
        [HistoryHttpResponse(status_code=429, body={"message": "slow down"})] * 5
    )
    provider = _provider(http, sleeper)

    with pytest.raises(MarketDataError, match="rate limited") as error:
        provider.fetch_history(
            symbols=("TSLA",),
            timeframe=MarketTimeframe.ONE_MINUTE,
            start=START,
            end=END,
        )

    assert "secret" not in str(error.value).lower()
    assert len(http.requests) == 5
    assert len(sleeper.delays) == 4
