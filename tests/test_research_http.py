"""HTTP boundary tests use injected streams and timers, never live network."""

from contextlib import contextmanager
from email.message import Message
from urllib.error import HTTPError

import pytest

from investment_assistant import research_http
from investment_assistant.research_http import (
    MAX_RESPONSE_BYTES,
    BoundedHttp,
    Deadline,
    ResearchError,
    ResearchTimeout,
)
from test_sec import Timer


class Stream:
    status = 200
    headers = {"Content-Type": "text/plain"}

    def __init__(self, body: bytes, timer: Timer, drip: float = 0) -> None:
        self.body = body
        self.timer = timer
        self.drip = drip
        self.sizes: list[int] = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read1(self, size):
        self.sizes.append(size)
        self.timer.value += self.drip
        part = self.body[:size]
        self.body = self.body[size:]
        return part


class Opener:
    def __init__(self, result, timer, connect_delay=0):
        self.result = result
        self.timer = timer
        self.connect_delay = connect_delay
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        self.timer.value += self.connect_delay
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def request(timer):
    return BoundedHttp().request(
        "GET",
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": "fixture"},
        body=None,
        deadline=Deadline.start(timer),
        timeout=10,
    )


def test_response_limit_reads_no_more_than_two_mib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timer = Timer()
    stream = Stream(b"x" * (MAX_RESPONSE_BYTES + 100), timer)
    opener = Opener(stream, timer)
    monkeypatch.setattr(research_http, "build_opener", lambda *args: opener)
    result = request(timer)
    assert len(result.body) == MAX_RESPONSE_BYTES and result.truncated
    assert len(stream.body) == 100 and stream.closed
    assert sum(stream.sizes) == MAX_RESPONSE_BYTES
    assert result.headers["content-type"] == "text/plain"
    assert len(opener.calls) == 1


@pytest.mark.parametrize("phase", ["connect", "read"])
def test_deadline_alarm_is_active_across_blocking_phases(
    monkeypatch: pytest.MonkeyPatch, phase
) -> None:
    timer = Timer()
    active = []

    @contextmanager
    def interrupt(seconds):
        active.append(seconds)
        try:
            yield
        finally:
            active.clear()

    class BlockingStream(Stream):
        def read1(self, size):
            assert active == [10]
            research_http._timeout_handler(0, None)

    class BlockingOpener(Opener):
        def open(self, request, timeout):
            assert active == [10]
            if phase == "connect":
                research_http._timeout_handler(0, None)
            return super().open(request, timeout)

    stream = BlockingStream(b"", timer)
    monkeypatch.setattr(research_http, "_interrupt_after", interrupt)
    monkeypatch.setattr(
        research_http, "build_opener", lambda *args: BlockingOpener(stream, timer)
    )
    with pytest.raises(ResearchTimeout):
        request(timer)
    assert not active
    if phase == "read":
        assert stream.closed


def test_slow_drip_is_rejected_even_when_each_read_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timer = Timer()
    stream = Stream(b"x" * 150000, timer, drip=31)
    opener = Opener(stream, timer)
    monkeypatch.setattr(research_http, "build_opener", lambda *args: opener)
    with pytest.raises(ResearchTimeout):
        request(timer)
    assert stream.closed


def test_error_or_redirect_never_reads_body_or_follows_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timer = Timer()
    headers = Message()
    headers["Location"] = "https://evil.test/secret"
    error = HTTPError("https://www.sec.gov", 302, "redirect", headers, None)
    opener = Opener(error, timer)
    handlers = []

    def build(handler):
        handlers.append(handler)
        return opener

    monkeypatch.setattr(research_http, "build_opener", build)
    result = request(timer)
    assert result.status == 302 and result.body == b""
    assert len(opener.calls) == 1
    assert (
        handlers[0].redirect_request(None, None, 302, "", None, "https://evil.test")
        is None
    )


def test_transport_errors_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    timer = Timer()
    monkeypatch.setattr(
        research_http,
        "build_opener",
        lambda *args: Opener(RuntimeError("secret token"), timer),
    )
    with pytest.raises(ResearchError, match="HTTP request failed") as error:
        request(timer)
    assert "secret" not in str(error.value)


def test_scoped_timer_restores_handler_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    old_handler = object()
    monkeypatch.setattr(research_http.signal, "getitimer", lambda *args: (0, 0))
    monkeypatch.setattr(research_http.signal, "getsignal", lambda *args: old_handler)
    monkeypatch.setattr(
        research_http.signal, "signal", lambda *args: calls.append(("handler", *args))
    )
    monkeypatch.setattr(
        research_http.signal, "setitimer", lambda *args: calls.append(("timer", *args))
    )
    with pytest.raises(RuntimeError), research_http._interrupt_after(3):
        raise RuntimeError("safe fixture")
    assert calls[1][-1] == 3
    assert calls[-2][-1] == 0 and calls[-1][-1] is old_handler


def test_existing_alarm_cannot_be_overwritten(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(research_http.signal, "getitimer", lambda *args: (2, 0))
    with (
        pytest.raises(ResearchError, match="active process timer"),
        research_http._interrupt_after(3),
    ):
        pytest.fail("must not start")
