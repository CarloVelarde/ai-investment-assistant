"""Tests for the replaceable stock-stream transport. No network."""

from collections.abc import Mapping

import pytest

from investment_assistant.stock_stream import (
    STOCK_STREAM_PREFIX,
    FakeStockStreamTransport,
    StockStreamDisconnect,
    StockStreamError,
    WebsocketStockStreamTransport,
    parse_stock_stream_frame,
    stock_stream_url,
)

CONNECTED = {"T": "success", "msg": "connected"}


class ScriptedSocket:
    def __init__(self, replies: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self.closed = False
        self.timeout: float | None = None
        self._replies = list(replies or [])

    def send(self, data: str) -> None:
        self.sent.append(data)

    def recv(self) -> str:
        if not self._replies:
            raise TimeoutError("timed out")
        return self._replies.pop(0)

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def close(self) -> None:
        self.closed = True


def test_stock_stream_url_is_the_v2_feed() -> None:
    assert stock_stream_url("iex") == f"{STOCK_STREAM_PREFIX}iex"
    assert stock_stream_url("sip") == f"{STOCK_STREAM_PREFIX}sip"


def test_production_transport_rejects_non_stock_urls() -> None:
    with pytest.raises(ValueError, match="IEX or SIP"):
        WebsocketStockStreamTransport("wss://example.test/v2/iex")
    with pytest.raises(ValueError, match="IEX or SIP"):
        WebsocketStockStreamTransport(f"{STOCK_STREAM_PREFIX}test")


def test_fake_transport_connect_auth_subscribe_recv_close() -> None:
    transport = FakeStockStreamTransport(
        incoming=[[CONNECTED], [{"T": "b", "S": "SPY"}]]
    )

    transport.connect()
    transport.authenticate(key_id="key-id", secret="stream-secret")
    transport.subscribe(("tsla", "SPY", "tsla"))
    first = transport.recv_frames()
    second = transport.recv_frames()
    idle = transport.recv_frames(timeout=0.1)
    transport.close()

    assert transport.connect_count == 1
    assert transport.authenticate_calls == [("key-id", "stream-secret")]
    assert transport.subscribe_calls == [("TSLA", "SPY")]
    assert transport.sent[1] == {
        "action": "subscribe",
        "bars": ["TSLA", "SPY"],
        "updatedBars": ["TSLA", "SPY"],
    }
    assert "dailyBars" not in transport.sent[1]
    assert first == (CONNECTED,)
    assert second[0]["T"] == "b"
    assert idle == ()
    assert transport.connected is False
    assert transport.close_count == 1


def test_fake_transport_requires_connect_before_io() -> None:
    transport = FakeStockStreamTransport()

    with pytest.raises(StockStreamError, match="not connected"):
        transport.recv_frames()


def test_fake_transport_can_signal_a_disconnect() -> None:
    transport = FakeStockStreamTransport()
    transport.connect()
    transport.queue_disconnect()

    with pytest.raises(StockStreamDisconnect):
        transport.recv_frames()
    assert transport.connected is False


def test_production_transport_uses_one_injected_socket() -> None:
    sockets: list[ScriptedSocket] = []

    def opener(url: str) -> ScriptedSocket:
        socket = ScriptedSocket(replies=['[{"T":"success","msg":"connected"}]'])
        sockets.append(socket)
        assert url == stock_stream_url("iex")
        return socket

    transport = WebsocketStockStreamTransport(stock_stream_url("iex"), opener=opener)
    transport.connect()
    transport.connect()
    transport.authenticate(key_id="key-id", secret="stream-secret")
    transport.subscribe(("TSLA", "SPY"))
    frames = transport.recv_frames(timeout=1.0)
    idle = transport.recv_frames(timeout=0.1)
    transport.close()

    assert len(sockets) == 1
    assert transport.url == "wss://stream.data.alpaca.markets/v2/iex"
    assert '"action":"auth"' in sockets[0].sent[0]
    assert "stream-secret" in sockets[0].sent[0]
    assert '"bars":["TSLA","SPY"]' in sockets[0].sent[1]
    assert '"updatedBars":["TSLA","SPY"]' in sockets[0].sent[1]
    assert "dailyBars" not in sockets[0].sent[1]
    assert frames == (CONNECTED,)
    assert idle == ()
    assert sockets[0].closed is True


def test_production_transport_maps_a_closed_socket_to_disconnect() -> None:
    class ClosingSocket(ScriptedSocket):
        def recv(self) -> str:
            raise ConnectionError("closed")

    transport = WebsocketStockStreamTransport(
        stock_stream_url("sip"),
        opener=lambda _url: ClosingSocket(),
    )
    transport.connect()

    with pytest.raises(StockStreamDisconnect):
        transport.recv_frames()


def test_parse_stock_stream_frame_accepts_batched_mappings() -> None:
    frames = parse_stock_stream_frame(b'[{"T":"b","S":"AAPL"},{"T":"u","S":"AAPL"}]')

    assert [frame["T"] for frame in frames] == ["b", "u"]


def test_parse_stock_stream_frame_rejects_objects() -> None:
    with pytest.raises(StockStreamError, match="JSON array"):
        parse_stock_stream_frame('{"T":"b"}')


def test_fake_transport_never_opens_a_network_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_connect(url: str, timeout: float | None = None) -> Mapping[str, object]:
        raise AssertionError(f"unexpected network connect to {url}")

    monkeypatch.setattr(
        "investment_assistant.stock_stream._open_websocket",
        fail_connect,
    )
    monkeypatch.setattr("websocket.create_connection", fail_connect)

    transport = FakeStockStreamTransport(incoming=[[CONNECTED]])
    transport.connect()
    assert transport.recv_frames() == (CONNECTED,)
