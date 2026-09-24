"""One bounded Discord webhook submission and its safe internal result."""

import http.client
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol, TypedDict
from urllib.parse import urlsplit

from investment_assistant.models import Event, EvidenceSource, ResearchReport

_WEBHOOK_HOSTS = frozenset(
    {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}
)
_WEBHOOK_PATH = re.compile(r"/api/(?:v10/)?webhooks/[0-9]+/[A-Za-z0-9_-]+\Z")
_MESSAGE_ID = re.compile(r"[0-9]+\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MAX_BODY = 64 * 1024


class DeliveryOutcome(StrEnum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DEFINITE_RETRY = "DEFINITE_RETRY"
    PERMANENT = "PERMANENT"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class SendResult:
    outcome: DeliveryOutcome
    message_id: str | None = None
    safe_reason: str | None = None
    wait_seconds: Decimal | None = None
    global_wait: bool = False
    disable_destination: bool = False


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    oversized: bool = False


class TransportError(Exception):
    """A safe transport failure with explicit pre-send proof when available."""

    def __init__(self, *, sent: bool | None, reason: str) -> None:
        super().__init__(reason)
        self.sent = sent
        self.reason = reason


class WebhookTransport(Protocol):
    def post(self, url: str, body: bytes) -> HttpResponse: ...


class EmbedPayload(TypedDict):
    title: str
    description: str
    fields: list[dict[str, str]]
    footer: dict[str, str]


class WebhookPayload(TypedDict):
    embeds: list[EmbedPayload]
    allowed_mentions: dict[str, list[str]]


def validate_webhook_url(value: str) -> str:
    """Reject malformed destinations without repeating a secret in errors."""

    url = value.strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise ValueError("invalid Discord webhook URL") from error
    if (
        parts.scheme != "https"
        or parts.hostname not in _WEBHOOK_HOSTS
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or parts.query
        or parts.fragment
        or not _WEBHOOK_PATH.fullmatch(parts.path)
        or parts.netloc.lower() not in (parts.hostname, f"{parts.hostname}:443")
    ):
        raise ValueError("invalid Discord webhook URL")
    return f"https://{parts.hostname}{parts.path}"


def _safe_text(value: str) -> str:
    return (
        _CONTROL.sub(" ", value)
        .replace("@", "＠")
        .replace("<", "‹")
        .replace("[", "［")
        .replace("]", "］")
    )


def _shorten(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _source_link(identity: str) -> str | None:
    try:
        parts = urlsplit(identity)
        if (
            parts.scheme in {"http", "https"}
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and len(identity) <= 2000
            and not any(char.isspace() or char in "<>[]()" for char in identity)
        ):
            return identity
    except ValueError:
        pass
    return None


def _source_field(sources: tuple[EvidenceSource, ...]) -> str:
    entries: list[str] = []
    omitted = max(0, len(sources) - 3)
    for source in sources[:3]:
        label = _shorten(_safe_text(source.title), 140)
        link = _source_link(source.identity)
        entry = f"[{label}]({link})" if link else label
        candidate = "\n".join((*entries, entry))
        if len(candidate) > 990:
            if link:
                entry = label
                candidate = "\n".join((*entries, entry))
            if len(candidate) > 990:
                omitted += 1
                continue
        entries.append(entry)
    if omitted:
        entries.append(f"+{omitted} more omitted")
    return "\n".join(entries) or "None"


def build_embed(
    event: Event, report: ResearchReport, delivery_id: str
) -> WebhookPayload:
    """Render a saved report within Discord's embed and mention limits."""

    details = report.details
    posture = "fixture report" if details is None else details.analysis.posture
    title = _shorten(_safe_text(f"{event.ticker} — {posture}"), 256)
    description = _shorten(_safe_text(report.summary), 500)
    uncertainty = (
        "Fixture research only" if details is None else details.analysis.uncertainty
    )
    source_text = "None" if details is None else _source_field(details.sources)
    fields: list[dict[str, str]] = [
        {"name": "Uncertainty", "value": _shorten(_safe_text(uncertainty), 1024)},
        {"name": "Sources", "value": source_text},
        {
            "name": "Event and report",
            "value": _shorten(
                f"{_safe_text(event.event_id)} / update {event.current_update}\n"
                f"Trigger {report.event_occurred_at.isoformat()}\n"
                f"Report {report.created_at.isoformat()}",
                1024,
            ),
        },
    ]
    footer = _shorten(f"For review only · {delivery_id}", 200)
    total = (
        len(title)
        + len(description)
        + len(footer)
        + sum(len(field["name"]) + len(field["value"]) for field in fields)
    )
    if total > 5000:
        fields[1]["value"] = _shorten(fields[1]["value"], max(1, 1024 - (total - 5000)))
    return {
        "embeds": [
            {
                "title": title,
                "description": description,
                "fields": fields,
                "footer": {"text": footer},
            }
        ],
        "allowed_mentions": {"parse": []},
    }


class UrllibWebhookTransport:
    """One HTTPS request with a total deadline and bounded response read."""

    def post(self, url: str, body: bytes) -> HttpResponse:
        return self._request("POST", url, body)

    def get(self, url: str) -> HttpResponse:
        return self._request("GET", url, None)

    def _request(self, method: str, url: str, body: bytes | None) -> HttpResponse:
        parts = urlsplit(url)
        deadline = time.monotonic() + 10
        if parts.hostname is None:
            raise TransportError(sent=False, reason="INVALID_DESTINATION")
        connection = http.client.HTTPSConnection(parts.hostname, timeout=10)
        try:
            try:
                connection.connect()
            except (OSError, TimeoutError) as error:
                raise TransportError(sent=False, reason=type(error).__name__) from None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError(sent=False, reason="DEADLINE_BEFORE_SEND")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                connection.request(
                    method,
                    parts.path + ("?wait=true" if method == "POST" else ""),
                    body=body,
                    headers={"Content-Type": "application/json"} if body else {},
                )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                response = connection.getresponse()
                chunks: list[bytes] = []
                size = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    if connection.sock is not None:
                        connection.sock.settimeout(remaining)
                    chunk = response.read(min(8192, _MAX_BODY + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > _MAX_BODY:
                        break
                return HttpResponse(
                    status=response.status,
                    headers={
                        key.lower(): value for key, value in response.getheaders()
                    },
                    body=b"".join(chunks) if size <= _MAX_BODY else b"",
                    oversized=size > _MAX_BODY,
                )
            except (OSError, TimeoutError, http.client.HTTPException) as error:
                raise TransportError(sent=None, reason=type(error).__name__) from None
        finally:
            connection.close()


class DiscordNotifier:
    def __init__(
        self, webhook_url: str, *, transport: WebhookTransport | None = None
    ) -> None:
        self.webhook_url = validate_webhook_url(webhook_url)
        self.transport = transport or UrllibWebhookTransport()

    def send(
        self, event: Event, report: ResearchReport, delivery_id: str
    ) -> SendResult:
        body = json.dumps(
            build_embed(event, report, delivery_id), ensure_ascii=False
        ).encode()
        try:
            response = self.transport.post(self.webhook_url, body)
        except TransportError as error:
            return SendResult(
                DeliveryOutcome.DEFINITE_RETRY
                if error.sent is False
                else DeliveryOutcome.UNCERTAIN,
                safe_reason=(
                    "PRE_SEND_TRANSPORT_FAILURE"
                    if error.sent is False
                    else "UNKNOWN_TRANSPORT_FAILURE"
                ),
            )
        except Exception as error:
            return SendResult(
                DeliveryOutcome.UNCERTAIN, safe_reason=type(error).__name__[:300]
            )
        return classify_response(response)

    def confirm(self, message_id: str, delivery_id: str) -> SendResult:
        """Verify a known message and its logical footer without posting."""

        if not _MESSAGE_ID.fullmatch(message_id):
            return SendResult(
                DeliveryOutcome.PERMANENT, safe_reason="INVALID_MESSAGE_ID"
            )
        try:
            getter = getattr(self.transport, "get", None)
            if not callable(getter):
                return SendResult(
                    DeliveryOutcome.PERMANENT, safe_reason="LOOKUP_UNAVAILABLE"
                )
            response = getter(f"{self.webhook_url}/messages/{message_id}")
        except TransportError:
            return SendResult(
                DeliveryOutcome.UNCERTAIN, safe_reason="LOOKUP_TRANSPORT_FAILURE"
            )
        except Exception as error:
            return SendResult(
                DeliveryOutcome.UNCERTAIN, safe_reason=type(error).__name__[:300]
            )
        if response.status == 429:
            return classify_response(response)
        if response.status != 200 or response.oversized:
            return SendResult(
                DeliveryOutcome.PERMANENT, safe_reason=f"HTTP_{response.status}"
            )
        try:
            payload = json.loads(response.body)
            embeds = payload["embeds"]
            footer = embeds[0]["footer"]["text"]
            if (
                payload["id"] == message_id
                and footer == f"For review only · {delivery_id}"
            ):
                return SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id=message_id)
        except ValueError, TypeError, KeyError, IndexError:
            pass
        return SendResult(DeliveryOutcome.PERMANENT, safe_reason="RECEIPT_MISMATCH")


def classify_response(response: HttpResponse) -> SendResult:
    status = response.status
    headers = {key.lower(): value for key, value in response.headers.items()}
    wait = _provider_wait(headers, response.body)
    global_wait = headers.get("x-ratelimit-global", "").lower() == "true"
    if not global_wait and status == 429 and not response.oversized:
        try:
            body = json.loads(response.body)
            global_wait = isinstance(body, dict) and body.get("global") is True
        except ValueError, TypeError:
            pass
    if 200 <= status < 300:
        if not response.oversized:
            try:
                body = json.loads(response.body)
                message_id = body["id"]
                if isinstance(message_id, str) and _MESSAGE_ID.fullmatch(message_id):
                    return SendResult(
                        DeliveryOutcome.ACKNOWLEDGED,
                        message_id=message_id,
                        wait_seconds=wait
                        if headers.get("x-ratelimit-remaining") == "0"
                        else None,
                        global_wait=global_wait,
                    )
            except ValueError, TypeError, KeyError:
                pass
        return SendResult(
            DeliveryOutcome.UNCERTAIN, safe_reason=f"HTTP_{status}_NO_RECEIPT"
        )
    if status == 429:
        return SendResult(
            DeliveryOutcome.DEFINITE_RETRY,
            safe_reason="HTTP_429",
            wait_seconds=wait,
            global_wait=global_wait,
        )
    if 300 <= status < 400:
        return SendResult(
            DeliveryOutcome.PERMANENT, safe_reason=f"HTTP_{status}_REDIRECT"
        )
    if status in (401, 403, 404):
        return SendResult(
            DeliveryOutcome.PERMANENT,
            safe_reason=f"HTTP_{status}",
            disable_destination=True,
        )
    if 400 <= status < 500:
        return SendResult(DeliveryOutcome.PERMANENT, safe_reason=f"HTTP_{status}")
    return SendResult(DeliveryOutcome.UNCERTAIN, safe_reason=f"HTTP_{status}")


def _provider_wait(headers: Mapping[str, str], body: bytes) -> Decimal | None:
    values: list[Decimal] = []
    for key in ("retry-after", "x-ratelimit-reset-after"):
        raw = headers.get(key)
        if raw is not None:
            try:
                value = Decimal(raw)
                if value.is_finite() and value >= 0:
                    values.append(value)
            except InvalidOperation:
                pass
    try:
        payload = json.loads(body)
        if isinstance(payload, dict) and "retry_after" in payload:
            value = Decimal(str(payload["retry_after"]))
            if value.is_finite() and value >= 0:
                values.append(value)
    except ValueError, TypeError, InvalidOperation:
        pass
    return max(values) if values else None
