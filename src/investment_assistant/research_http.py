"""Deadline-aware, size-bounded HTTP used only by research adapters."""

import signal
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import FrameType
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ResearchError(Exception):
    """Application-owned safe failure; never contains provider payloads."""


class ResearchTimeout(ResearchError):
    """The shared elapsed-time deadline expired."""


@dataclass(frozen=True)
class Deadline:
    expires_at: float
    monotonic: Callable[[], float] = field(repr=False, default=time.monotonic)

    @classmethod
    def start(cls, monotonic: Callable[[], float] = time.monotonic) -> Deadline:
        return cls(monotonic() + 90, monotonic)

    def remaining(self) -> float:
        remaining = self.expires_at - self.monotonic()
        if remaining <= 0:
            raise ResearchTimeout("Research deadline expired.")
        return remaining

    def wait(self, seconds: float, sleep: Callable[[float], None]) -> None:
        if seconds >= self.remaining():
            raise ResearchTimeout("Research deadline cannot accommodate pacing.")
        if seconds > 0:
            sleep(seconds)
        self.remaining()


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict)
    truncated: bool = False


class ResearchHttp(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        deadline: Deadline,
        timeout: float,
    ) -> HttpResult: ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _timeout_handler(signum: int, frame: FrameType | None) -> None:
    raise ResearchTimeout("Research HTTP deadline expired.")


@contextmanager
def _interrupt_after(seconds: float) -> Iterator[None]:
    # Socket timeouts alone reset on each read and cannot bound a slow-drip body
    # or DNS resolution. The local POSIX main thread owns this scoped alarm.
    if threading.current_thread() is not threading.main_thread() or not hasattr(
        signal, "setitimer"
    ):
        raise ResearchError("Research HTTP requires the POSIX main thread.")
    if signal.getitimer(signal.ITIMER_REAL)[0] > 0:
        raise ResearchError("Research HTTP cannot replace an active process timer.")
    handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _timeout_handler)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, handler)


class BoundedHttp:
    """One request, no redirects/retries; alarm covers connect, headers, and body."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        deadline: Deadline,
        timeout: float,
    ) -> HttpResult:
        try:
            seconds = min(timeout, deadline.remaining())
            request = Request(url, data=body, method=method, headers=dict(headers))
            with _interrupt_after(seconds):
                try:
                    response = build_opener(_NoRedirects()).open(
                        request, timeout=seconds
                    )
                except HTTPError as error:
                    # Never read or expose provider error bodies (or redirect targets).
                    with error:
                        return HttpResult(
                            error.code,
                            b"",
                            headers={k.lower(): v for k, v in error.headers.items()},
                        )
                with response:
                    parts = []
                    size = 0
                    while size < MAX_RESPONSE_BYTES:
                        deadline.remaining()
                        part = response.read1(min(65536, MAX_RESPONSE_BYTES - size))
                        deadline.remaining()
                        if not part:
                            break
                        size += len(part)
                        parts.append(part)
                    result = HttpResult(
                        int(response.status),
                        b"".join(parts),
                        {k.lower(): v for k, v in response.headers.items()},
                        truncated=size == MAX_RESPONSE_BYTES,
                    )
            deadline.remaining()
            return result
        except ResearchError:
            raise
        except Exception:
            raise ResearchError("Research HTTP request failed.") from None
