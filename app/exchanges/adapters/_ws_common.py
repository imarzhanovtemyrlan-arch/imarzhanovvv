"""
Shared multiplexed WebSocket stream with throttled subscription changes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import websockets

logger = logging.getLogger(__name__)

RECONNECT_MIN_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0

# Binance allows only a small number of control messages per second.
# Keep a safety margin instead of trying to use the absolute limit.
CONTROL_MESSAGE_INTERVAL = 0.25


def chunked(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


class MultiplexedOrderbookStream:
    def __init__(
        self,
        url: str,
        build_subscribe_message: Callable[[list[str]], list[str]],
        on_message: Callable[[str], None],
        heartbeat_message: Optional[Callable[[], str]] = None,
        heartbeat_interval: float = 20.0,
        exchange_label: str = "",
        build_unsubscribe_message: Optional[Callable[[list[str]], list[str]]] = None,
        control_interval: float = CONTROL_MESSAGE_INTERVAL,
    ) -> None:
        self._control_interval = control_interval
        self._url = url
        self._build_subscribe_message = build_subscribe_message
        self._build_unsubscribe_message = build_unsubscribe_message
        self._on_message = on_message
        self._heartbeat_message = heartbeat_message
        self._heartbeat_interval = heartbeat_interval
        self._label = exchange_label

        self._symbols: set[str] = set()
        self._ws = None
        self._task: Optional[asyncio.Task] = None

        self._pending_subscribes: set[str] = set()
        self._pending_unsubscribes: set[str] = set()

        self._control_event = asyncio.Event()
        self._control_task: Optional[asyncio.Task] = None

    def add_symbol(self, symbol: str) -> None:
        if symbol in self._symbols:
            return

        self._symbols.add(symbol)
        self._pending_unsubscribes.discard(symbol)

        if self._task is None:
            self._task = asyncio.create_task(self._run())

        self._pending_subscribes.add(symbol)
        self._control_event.set()

    def remove_symbol(self, symbol: str) -> None:
        if symbol not in self._symbols:
            return

        self._symbols.discard(symbol)
        self._pending_subscribes.discard(symbol)

        if self._build_unsubscribe_message is not None and self._ws is not None:
            self._pending_unsubscribes.add(symbol)
            self._control_event.set()

    async def stop(self) -> None:
        tasks = [t for t in (self._control_task, self._task) if t is not None]
        self._control_task = None
        self._task = None

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._ws = None
        self._pending_subscribes.clear()
        self._pending_unsubscribes.clear()
        self._control_event.clear()

    async def _send_frames(self, ws, frames: list[str]) -> None:
        for frame in frames:
            await ws.send(frame)
            await asyncio.sleep(self._control_interval)

    async def _flush_controls(self, ws) -> None:
        while self._ws is ws:
            await self._control_event.wait()
            self._control_event.clear()

            # Give concurrent add/remove operations a short window to
            # accumulate into one batch instead of sending one command
            # for every symbol.
            await asyncio.sleep(self._control_interval)

            subscribes = list(self._pending_subscribes)
            unsubscribes = list(self._pending_unsubscribes)

            self._pending_subscribes.clear()
            self._pending_unsubscribes.clear()

            if subscribes:
                try:
                    await self._send_frames(
                        ws,
                        self._build_subscribe_message(subscribes),
                    )
                except Exception:
                    self._pending_subscribes.update(
                        s for s in subscribes if s in self._symbols
                    )
                    raise

            if unsubscribes and self._build_unsubscribe_message is not None:
                try:
                    await self._send_frames(
                        ws,
                        self._build_unsubscribe_message(unsubscribes),
                    )
                except Exception:
                    self._pending_unsubscribes.update(
                        s for s in unsubscribes if s not in self._symbols
                    )
                    raise

    async def _run(self) -> None:
        delay = RECONNECT_MIN_DELAY

        while True:
            try:
                async with websockets.connect(
                    self._url,
                    ping_interval=None,
                ) as ws:
                    self._ws = ws

                    # Initial subscription goes through the same throttled
                    # control task, so the reader loop and heartbeat start
                    # immediately. Sending hundreds of frames inline (before
                    # reading) let the socket buffer fill and starved the
                    # heartbeat -> server dropped the connection.
                    self._pending_subscribes = set(self._symbols)
                    self._pending_unsubscribes.clear()
                    if self._pending_subscribes:
                        self._control_event.set()

                    logger.info(
                        "%s: WebSocket connected, subscribing to %d symbol(s)",
                        self._label,
                        len(self._pending_subscribes),
                    )

                    delay = RECONNECT_MIN_DELAY

                    self._control_task = asyncio.create_task(
                        self._flush_controls(ws)
                    )

                    heartbeat_task = None
                    if self._heartbeat_message is not None:
                        heartbeat_task = asyncio.create_task(
                            self._heartbeat(ws)
                        )

                    try:
                        async for raw in ws:
                            try:
                                self._on_message(raw)
                            except Exception:  # noqa: BLE001
                                logger.exception("%s: on_message handler failed", self._label)
                    finally:
                        if heartbeat_task is not None:
                            heartbeat_task.cancel()

                        if self._control_task is not None:
                            self._control_task.cancel()
                            self._control_task = None

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.warning(
                    "%s: WebSocket disconnected (%s), reconnecting in %.1fs",
                    self._label,
                    exc,
                    delay,
                )

            finally:
                self._ws = None

            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _heartbeat(self, ws) -> None:
        assert self._heartbeat_message is not None

        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                await ws.send(self._heartbeat_message())

        except asyncio.CancelledError:
            pass

        except Exception:
            pass