"""
Regression tests for MultiplexedOrderbookStream.

An earlier version of every real adapter (Binance/Bybit/OKX/Gate.io) opened
one WebSocket connection PER SYMBOL. Once the number of matched pairs across
exchanges grew into the hundreds, this blew past exchange-side per-IP
connection limits (e.g. Gate.io documents <=300 connections/IP) and caused
mass simultaneous disconnects. These tests assert the fix: exactly ONE
connection is opened per exchange adapter, regardless of how many symbols
are subscribed, and that connection is resubscribed to every known symbol
on reconnect.
"""
from __future__ import annotations

import asyncio

import pytest

from app.exchanges.adapters import _ws_common as ws_common_module
from app.exchanges.adapters._ws_common import MultiplexedOrderbookStream


class FakeConnection:
    """Stands in for a `websockets` connection object."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def close(self) -> None:
        await self._queue.put(None)

    async def push(self, msg: str) -> None:
        await self._queue.put(msg)


class _ConnectionContext:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    async def __aenter__(self) -> FakeConnection:
        return self.conn

    async def __aexit__(self, *exc) -> bool:
        return False


class FakeConnect:
    """Stands in for `websockets.connect`. Records every connection opened
    so tests can assert exactly one was created."""

    def __init__(self) -> None:
        self.connections: list[FakeConnection] = []

    def __call__(self, url: str, **kwargs):
        conn = FakeConnection()
        self.connections.append(conn)
        return _ConnectionContext(conn)


@pytest.fixture
def fake_connect(monkeypatch):
    fc = FakeConnect()
    monkeypatch.setattr(ws_common_module.websockets, "connect", fc)
    monkeypatch.setattr(ws_common_module, "RECONNECT_MIN_DELAY", 0.01)
    monkeypatch.setattr(ws_common_module, "RECONNECT_MAX_DELAY", 0.05)
    return fc


@pytest.mark.asyncio
async def test_multiple_symbols_share_one_connection(fake_connect):
    stream = MultiplexedOrderbookStream(
        url="wss://example.invalid/ws",
        build_subscribe_message=lambda symbols: [f"SUB:{','.join(symbols)}"],
        on_message=lambda raw: None,
        exchange_label="test",
    )

    stream.add_symbol("BTCUSDT")
    await asyncio.sleep(0.05)

    assert len(fake_connect.connections) == 1
    conn = fake_connect.connections[0]
    assert conn.sent == ["SUB:BTCUSDT"]

    # This is the regression case: adding a second symbol while already
    # connected must send an incremental subscribe on the SAME connection,
    # never open a new one.
    stream.add_symbol("ETHUSDT")
    await asyncio.sleep(0.05)

    assert len(fake_connect.connections) == 1
    assert "SUB:ETHUSDT" in conn.sent

    await stream.stop()


@pytest.mark.asyncio
async def test_reconnect_resubscribes_all_known_symbols(fake_connect):
    stream = MultiplexedOrderbookStream(
        url="wss://example.invalid/ws",
        build_subscribe_message=lambda symbols: [f"SUB:{','.join(sorted(symbols))}"],
        on_message=lambda raw: None,
        exchange_label="test",
    )

    stream.add_symbol("BTCUSDT")
    await asyncio.sleep(0.03)
    stream.add_symbol("ETHUSDT")
    await asyncio.sleep(0.03)

    first_conn = fake_connect.connections[0]
    await first_conn.close()  # simulate the exchange dropping the connection
    await asyncio.sleep(0.1)  # allow the backoff + reconnect to run

    assert len(fake_connect.connections) == 2
    second_conn = fake_connect.connections[1]
    assert second_conn.sent == ["SUB:BTCUSDT,ETHUSDT"]

    await stream.stop()


@pytest.mark.asyncio
async def test_incoming_messages_are_dispatched(fake_connect):
    received = []
    stream = MultiplexedOrderbookStream(
        url="wss://example.invalid/ws",
        build_subscribe_message=lambda symbols: [f"SUB:{','.join(symbols)}"],
        on_message=received.append,
        exchange_label="test",
    )

    stream.add_symbol("BTCUSDT")
    await asyncio.sleep(0.03)

    conn = fake_connect.connections[0]
    await conn.push('{"topic": "orderbook.50.BTCUSDT", "type": "snapshot"}')
    await asyncio.sleep(0.03)

    assert received == ['{"topic": "orderbook.50.BTCUSDT", "type": "snapshot"}']

    await stream.stop()


@pytest.mark.asyncio
async def test_remove_symbol_stops_tracking(fake_connect):
    stream = MultiplexedOrderbookStream(
        url="wss://example.invalid/ws",
        build_subscribe_message=lambda symbols: [f"SUB:{','.join(symbols)}"],
        on_message=lambda raw: None,
        exchange_label="test",
    )
    stream.add_symbol("BTCUSDT")
    await asyncio.sleep(0.02)
    stream.remove_symbol("BTCUSDT")
    assert "BTCUSDT" not in stream._symbols  # noqa: SLF001 - internal state check

    await stream.stop()
