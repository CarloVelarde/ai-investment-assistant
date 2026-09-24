"""Discord URL, payload, and response boundaries without network calls."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from investment_assistant.config import Settings
from investment_assistant.discord_notify import (
    DeliveryOutcome,
    DiscordNotifier,
    HttpResponse,
    TransportError,
    UrllibWebhookTransport,
    build_embed,
    classify_response,
    validate_webhook_url,
)
from investment_assistant.models import (
    Event,
    EventStatus,
    EvidenceSource,
    EvidenceText,
    LiveReportDetails,
    MarketWindow,
    ReportDraft,
    ResearchReport,
    ResearchUsage,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.reporting import create_fake_research_report

NOW = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)
WEBHOOK = "https://discord.com/api/webhooks/12345/test-token"


def _event() -> Event:
    return Event(
        event_id="event:test",
        ticker="TEST",
        direction=SignalDirection.UP,
        category=None,
        importance=SignalImportance.MODERATE,
        market_windows=(MarketWindow.ONE_HOUR,),
        current_update=1,
        status=EventStatus.REPORTED,
        created_at=NOW,
        updated_at=NOW,
    )


def _live_report(
    event: Event, *, source_url: str = "https://example.com/article"
) -> ResearchReport:
    details = LiveReportDetails(
        company="Test Company",
        triggering_signal_ids=("signal:test",),
        analysis=ReportDraft(
            summary="Good news @everyone <@123> " + "A" * 600,
            likely_explanation=EvidenceText(
                text="Likely", references=("packet", "source"), is_hypothesis=False
            ),
            competing_explanations=(),
            market_context=(),
            bullish_considerations=(),
            bearish_considerations=(),
            missing_information=(),
            uncertainty="May be temporary",
            scope="COMPANY",
            cause_unknown=False,
            evidence_character="UNKNOWN",
            confidence=0.5,
            posture="MONITOR",
        ),
        sources=(
            EvidenceSource(
                reference="packet",
                title="Local packet",
                kind="packet",
                identity="signal:test",
                published_at=None,
                retrieved_at=NOW,
            ),
            EvidenceSource(
                reference="source",
                title="Public article",
                kind="web",
                identity=source_url,
                published_at=NOW,
                retrieved_at=NOW,
            ),
        ),
        model_version="model-v1",
        prompt_version="prompt-v1",
        attempt_id="attempt:1",
        packet_as_of=NOW,
        usage=ResearchUsage(),
    )
    return ResearchReport(
        report_id=f"report:{event.event_id}:1",
        event_id=event.event_id,
        event_update=1,
        ticker=event.ticker,
        event_occurred_at=NOW,
        created_at=NOW,
        summary=details.analysis.summary,
        is_fake=False,
        details=details,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/123/token",
        "https://evil.example/api/webhooks/123/token",
        "https://discord.com.evil.example/api/webhooks/123/token",
        "https://discord.com:444/api/webhooks/123/token",
        "https://user@discord.com/api/webhooks/123/token",
        "https://discord.com/api/webhooks/123/token?x=1",
        "https://discord.com/api/webhooks/123/token#fragment",
        "https://discord.com/api/webhooks/123/token/extra",
        "https://discord.com/api/webhooks/123%2f456/token",
        "https://discord.com/api/webhooks/abc/token",
    ],
)
def test_rejects_unsafe_webhook_url_without_echoing_it(url: str) -> None:
    with pytest.raises(ValueError, match="invalid Discord webhook URL") as caught:
        validate_webhook_url(url)
    assert url not in str(caught.value)


def test_settings_hide_webhook_value_on_invalid_input() -> None:
    secret = "https://discord.com.evil.example/api/webhooks/123/private-token"
    with pytest.raises(ValidationError) as caught:
        Settings(
            alpaca_api_key_id="test-id",
            alpaca_api_secret_key="test-secret",  # type: ignore[arg-type]
            watchlist="TEST",
            discord_webhook_url=secret,  # type: ignore[arg-type]
        )
    assert secret not in str(caught.value)
    assert "private-token" not in str(caught.value)


def test_offline_fixture_ignores_unused_webhook_setting() -> None:
    settings = Settings(
        alpaca_api_key_id="",
        alpaca_api_secret_key=SecretStr(""),
        discord_webhook_url="unused",  # type: ignore[arg-type]
    )
    assert not settings.live_mode


def test_webhook_url_normalizes_default_port_and_host_case() -> None:
    assert (
        validate_webhook_url("https://DISCORD.COM:443/api/webhooks/12345/test-token")
        == WEBHOOK
    )


def test_embed_suppresses_mentions_and_bounds_report_content() -> None:
    event = _event()
    report = _live_report(event)
    payload = build_embed(event, report, "d-test")
    assert payload["allowed_mentions"] == {"parse": []}
    embed = payload["embeds"][0]
    assert len(embed["description"]) <= 500
    assert "@everyone" not in embed["description"]
    assert "<@123>" not in embed["description"]
    assert "https://example.com/article" in embed["fields"][1]["value"]
    assert "d-test" in embed["footer"]["text"]
    text = embed["title"] + embed["description"] + embed["footer"]["text"]
    text += "".join(field["name"] + field["value"] for field in embed["fields"])
    assert len(text) <= 5000
    assert all(len(field["value"]) <= 1024 for field in embed["fields"])


def test_fake_report_keeps_warning_label() -> None:
    event = _event()
    report = create_fake_research_report(event, ())
    payload = build_embed(event, report, "d-test")
    assert "FAKE RESEARCH" in payload["embeds"][0]["description"]


def test_long_source_link_is_never_cut_mid_url() -> None:
    event = _event()
    long_url = "https://example.com/" + "a" * 1900
    payload = build_embed(event, _live_report(event, source_url=long_url), "d-test")
    source_text = payload["embeds"][0]["fields"][1]["value"]
    assert long_url not in source_text
    assert "Public article" in source_text
    assert len(source_text) <= 1024


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        (HttpResponse(200, {}, b'{"id":"987"}'), DeliveryOutcome.ACKNOWLEDGED),
        (HttpResponse(204, {}, b""), DeliveryOutcome.UNCERTAIN),
        (HttpResponse(200, {}, b"invalid"), DeliveryOutcome.UNCERTAIN),
        (HttpResponse(200, {}, b'{"id":"bad"}'), DeliveryOutcome.UNCERTAIN),
        (HttpResponse(200, {}, b"", oversized=True), DeliveryOutcome.UNCERTAIN),
        (HttpResponse(429, {"Retry-After": "30"}, b""), DeliveryOutcome.DEFINITE_RETRY),
        (HttpResponse(500, {}, b""), DeliveryOutcome.UNCERTAIN),
        (HttpResponse(302, {}, b""), DeliveryOutcome.PERMANENT),
        (HttpResponse(403, {}, b""), DeliveryOutcome.PERMANENT),
    ],
)
def test_response_outcomes(response: HttpResponse, outcome: DeliveryOutcome) -> None:
    assert classify_response(response).outcome is outcome


def test_429_uses_longer_valid_provider_wait() -> None:
    result = classify_response(
        HttpResponse(
            429,
            {"Retry-After": "5", "X-RateLimit-Global": "true"},
            b'{"retry_after": 25}',
        )
    )
    assert result.wait_seconds == 25
    assert result.global_wait


def test_notifier_distinguishes_proven_pre_send_error_from_unknown_error() -> None:
    class FailingTransport:
        def __init__(self, sent: bool | None) -> None:
            self.sent = sent

        def post(self, _: str, __: bytes) -> HttpResponse:
            raise TransportError(sent=self.sent, reason="CONNECT_FAILURE")

    event = _event()
    report = create_fake_research_report(event, ())
    assert (
        DiscordNotifier(WEBHOOK, transport=FailingTransport(False))
        .send(event, report, "d-test")
        .outcome
        is DeliveryOutcome.DEFINITE_RETRY
    )
    assert (
        DiscordNotifier(WEBHOOK, transport=FailingTransport(None))
        .send(event, report, "d-test")
        .outcome
        is DeliveryOutcome.UNCERTAIN
    )


def test_notifier_sends_wait_true_payload_once() -> None:
    class RecordingTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bytes]] = []

        def post(self, url: str, body: bytes) -> HttpResponse:
            self.calls.append((url, body))
            return HttpResponse(200, {}, b'{"id":"123456"}')

    transport = RecordingTransport()
    event = _event()
    result = DiscordNotifier(WEBHOOK, transport=transport).send(
        event, create_fake_research_report(event, ()), "d-test"
    )
    assert result.message_id == "123456"
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == WEBHOOK
    payload = json.loads(transport.calls[0][1])
    assert payload["allowed_mentions"] == {"parse": []}


def test_receipt_lookup_verifies_message_footer_without_posting() -> None:
    class LookupTransport:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def post(self, url: str, body: bytes) -> HttpResponse:
            pytest.fail("confirmation must not POST")

        def get(self, url: str) -> HttpResponse:
            self.urls.append(url)
            return HttpResponse(
                200,
                {},
                json.dumps(
                    {
                        "id": "123456",
                        "embeds": [{"footer": {"text": "For review only · d-test"}}],
                    }
                ).encode(),
            )

    transport = LookupTransport()
    notifier = DiscordNotifier(WEBHOOK, transport=transport)
    assert notifier.confirm("123456", "d-wrong").outcome is DeliveryOutcome.PERMANENT
    assert notifier.confirm("123456", "d-test").message_id == "123456"
    assert transport.urls == [f"{WEBHOOK}/messages/123456"] * 2


def test_transport_uses_wait_true_and_limits_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeSocket:
        def settimeout(self, _: float) -> None:
            pass

    class FakeResponse:
        status = 200

        def __init__(self) -> None:
            self.remaining = b"x" * (64 * 1024 + 1)

        def read(self, size: int) -> bytes:
            chunk, self.remaining = self.remaining[:size], self.remaining[size:]
            return chunk

        def getheaders(self) -> list[tuple[str, str]]:
            return []

    class FakeConnection:
        def __init__(self, _host: str, *, timeout: int) -> None:
            assert timeout == 10
            self.sock = FakeSocket()

        def connect(self) -> None:
            pass

        def request(self, method: str, target: str, **_: object) -> None:
            calls.append((method, target))

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "investment_assistant.discord_notify.http.client.HTTPSConnection",
        FakeConnection,
    )
    response = UrllibWebhookTransport().post(WEBHOOK, b"{}")
    assert calls == [("POST", "/api/webhooks/12345/test-token?wait=true")]
    assert response.oversized and response.body == b""


def test_transport_expired_before_post_is_proven_unsent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((0.0, 11.0))
    monkeypatch.setattr(
        "investment_assistant.discord_notify.time.monotonic", lambda: next(ticks)
    )

    class FakeConnection:
        sock = None

        def __init__(self, _host: str, *, timeout: int) -> None:
            pass

        def connect(self) -> None:
            pass

        def request(self, *_: object, **__: object) -> None:
            pytest.fail("sent after deadline")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "investment_assistant.discord_notify.http.client.HTTPSConnection",
        FakeConnection,
    )
    with pytest.raises(TransportError) as caught:
        UrllibWebhookTransport().post(WEBHOOK, b"{}")
    assert caught.value.sent is False


def test_transport_timeout_after_post_is_uncertain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection:
        sock = None

        def __init__(self, _host: str, *, timeout: int) -> None:
            pass

        def connect(self) -> None:
            pass

        def request(self, *_: object, **__: object) -> None:
            pass

        def getresponse(self) -> None:
            raise TimeoutError

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "investment_assistant.discord_notify.http.client.HTTPSConnection",
        FakeConnection,
    )
    with pytest.raises(TransportError) as caught:
        UrllibWebhookTransport().post(WEBHOOK, b"{}")
    assert caught.value.sent is None


def test_transport_exception_text_never_reaches_safe_result() -> None:
    class LeakingTransport:
        def post(self, _: str, __: bytes) -> HttpResponse:
            raise TransportError(sent=None, reason=WEBHOOK)

    event = _event()
    result = DiscordNotifier(WEBHOOK, transport=LeakingTransport()).send(
        event, create_fake_research_report(event, ()), "d-test"
    )
    assert result.outcome is DeliveryOutcome.UNCERTAIN
    assert WEBHOOK not in str(result)
