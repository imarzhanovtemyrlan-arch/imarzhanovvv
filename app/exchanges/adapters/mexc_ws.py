"""MEXC spot WebSocket feed (partial book depth, Protobuf).

Endpoint wss://wbs-api.mexc.com/ws, max 30 streams per connection,
so symbols are spread over several connections ("shards").
Schema: github.com/mexcdevelop/websocket-proto
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, NamedTuple, Optional

from app.exchanges.adapters._ws_common import MultiplexedOrderbookStream, chunked

logger = logging.getLogger(__name__)

WS_URL = "wss://wbs-api.mexc.com/ws"
DEPTH_LEVELS = 5
MAX_STREAMS_PER_CONNECTION = 30
PING_INTERVAL = 20.0
NEW_CONNECTION_STAGGER = 0.2
CHANNEL_TEMPLATE = "spot@public.limit.depth.v3.api.pb@{symbol}@{levels}"

Level = tuple[float, float]


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _iter_fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    pos = 0
    n = len(buf)
    while pos < n:
        tag, pos = _read_varint(buf, pos)
        field_no, wire_type = tag >> 3, tag & 7
        if wire_type == 0:
            value, pos = _read_varint(buf, pos)
        elif wire_type == 2:
            length, pos = _read_varint(buf, pos)
            end = pos + length
            if end > n:
                raise ValueError("truncated field")
            value = buf[pos:end]
            pos = end
        elif wire_type == 1:
            end = pos + 8
            if end > n:
                raise ValueError("truncated fixed64")
            value = buf[pos:end]
            pos = end
        elif wire_type == 5:
            end = pos + 4
            if end > n:
                raise ValueError("truncated fixed32")
            value = buf[pos:end]
            pos = end
        else:
            raise ValueError(f"unsupported wire type {wire_type}")
        yield field_no, wire_type, value


class DepthUpdate(NamedTuple):
    symbol: str
    bids: list[Level]
    asks: list[Level]


def decode_limit_depth(data: bytes) -> Optional[DepthUpdate]:
    channel: Optional[str] = None
    symbol: Optional[str] = None
    body: Optional[bytes] = None

    for field_no, wire_type, value in _iter_fields(data):
        if wire_type != 2:
            continue
        if field_no == 1:
            channel = value.decode("utf-8")
        elif field_no == 3:
            symbol = value.decode("utf-8")
        elif field_no == 303:
            body = value

    if body is None:
        return None
    if not symbol and channel:
        parts = channel.split("@")
        if len(parts) >= 2:
            symbol = parts[-2]
    if not symbol:
        return None

    asks: list[Level] = []
    bids: list[Level] = []
    for field_no, wire_type, value in _iter_fields(body):
        if wire_type != 2 or field_no not in (1, 2):
            continue
        price: Optional[str] = None
        quantity: Optional[str] = None
        for item_no, item_wt, item_val in _iter_fields(value):
            if item_wt != 2:
                continue
            if item_no == 1:
                price = item_val.decode("utf-8")
            elif item_no == 2:
                quantity = item_val.decode("utf-8")
        if price is None or quantity is None:
            continue
        p, q = float(price), float(quantity)
        if q > 0:
            (asks if field_no == 1 else bids).append((p, q))

    asks.sort(key=lambda lv: lv[0])
    bids.sort(key=lambda lv: -lv[0])
    return DepthUpdate(symbol, bids, asks)


@dataclass
class _Shard:
    index: int
    stream: MultiplexedOrderbookStream
    symbols: set[str] = field(default_factory=set)


class MexcWebSocketFeed:
    def __init__(self, on_book: Callable[[str, list[Level], list[Level]], None]) -> None:
        self._on_book = on_book
        self._shards: list[_Shard] = []
        self._symbol_shard: dict[str, _Shard] = {}
        self._next_index = 0
        self._decode_errors = 0
        self._rejected = 0
        self.last_message_at: Optional[float] = None

    async def add_symbol(self, symbol: str) -> None:
        if symbol in self._symbol_shard:
            return
        shard = next((s for s in self._shards if len(s.symbols) < MAX_STREAMS_PER_CONNECTION), None)
        created = False
        if shard is None:
            shard = self._new_shard()
            created = True
        shard.symbols.add(symbol)
        self._symbol_shard[symbol] = shard
        shard.stream.add_symbol(symbol)
        if created:
            await asyncio.sleep(NEW_CONNECTION_STAGGER)

    async def remove_symbol(self, symbol: str) -> None:
        shard = self._symbol_shard.pop(symbol, None)
        if shard is None:
            return
        shard.symbols.discard(symbol)
        shard.stream.remove_symbol(symbol)
        if not shard.symbols:
            if shard in self._shards:
                self._shards.remove(shard)
            await shard.stream.stop()

    async def stop(self) -> None:
        shards = list(self._shards)
        self._shards.clear()
        self._symbol_shard.clear()
        for shard in shards:
            await shard.stream.stop()

    def _new_shard(self) -> _Shard:
        index = self._next_index
        self._next_index += 1
        stream = MultiplexedOrderbookStream(
            url=WS_URL,
            build_subscribe_message=self._subscribe_frames,
            build_unsubscribe_message=self._unsubscribe_frames,
            on_message=self._handle_message,
            heartbeat_message=lambda: json.dumps({"method": "PING"}),
            heartbeat_interval=PING_INTERVAL,
            exchange_label=f"mexc-ws-{index}",
        )
        shard = _Shard(index=index, stream=stream)
        self._shards.append(shard)
        return shard

    @staticmethod
    def _frames(method: str, symbols: list[str]) -> list[str]:
        channels = [CHANNEL_TEMPLATE.format(symbol=s, levels=DEPTH_LEVELS) for s in symbols]
        return [
            json.dumps({"method": method, "params": chunk})
            for chunk in chunked(channels, MAX_STREAMS_PER_CONNECTION)
        ]

    def _subscribe_frames(self, symbols: list[str]) -> list[str]:
        return self._frames("SUBSCRIPTION", symbols)

    def _unsubscribe_frames(self, symbols: list[str]) -> list[str]:
        return self._frames("UNSUBSCRIPTION", symbols)

    def _handle_message(self, raw) -> None:
        if isinstance(raw, (bytes, bytearray)):
            try:
                update = decode_limit_depth(bytes(raw))
            except Exception as exc:  # noqa: BLE001
                self._decode_errors += 1
                if self._decode_errors <= 5 or self._decode_errors % 1000 == 0:
                    logger.warning("mexc-ws: cannot decode a data frame (%s), total errors: %d", exc, self._decode_errors)
                return
            if update is None:
                return
            self.last_message_at = time.time()
            self._on_book(update.symbol, update.bids, update.asks)
            return

        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        text = str(msg.get("msg", "")) if isinstance(msg, dict) else ""
        if "Not Subscribed" in text or "Blocked" in text:
            self._rejected += 1
            if self._rejected <= 10 or self._rejected % 100 == 0:
                logger.warning("mexc-ws: subscription rejected (%d so far): %s", self._rejected, text[:200])