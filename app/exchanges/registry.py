"""
ExchangeRegistry: central place where adapters register themselves.

Adding a new exchange requires ZERO changes to arbitrage engine, API, or
this file's logic - only a new adapter module that calls `register()`.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from app.exchanges.base import ExchangeAdapter, ExchangeUnsupportedError
from app.models.market import ExchangeConnectionStatus, ExchangeHealth

logger = logging.getLogger(__name__)

AdapterFactory = Callable[[], ExchangeAdapter]


class ExchangeRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}
        self._instances: dict[str, ExchangeAdapter] = {}
        self._unsupported_reasons: dict[str, str] = {}

    def register(self, name: str, factory: AdapterFactory) -> None:
        if name in self._factories:
            raise ValueError(f"Exchange '{name}' is already registered")
        self._factories[name] = factory
        logger.info("Registered exchange adapter: %s", name)

    def mark_unsupported(self, name: str, reason: str) -> None:
        """Record that an exchange cannot be reliably supported, without
        crashing the rest of the system (spec section 4)."""
        self._unsupported_reasons[name] = reason
        logger.warning("Exchange '%s' marked UNSUPPORTED: %s", name, reason)

    def registered_names(self) -> list[str]:
        return sorted(self._factories.keys())

    def get(self, name: str) -> ExchangeAdapter:
        if name in self._instances:
            return self._instances[name]
        if name not in self._factories:
            raise KeyError(f"Exchange '{name}' is not registered")
        instance = self._factories[name]()
        self._instances[name] = instance
        return instance

    def all_instances(self) -> dict[str, ExchangeAdapter]:
        for name in self._factories:
            if name not in self._instances and name not in self._unsupported_reasons:
                self.get(name)
        return dict(self._instances)

    async def connect_all(self) -> None:
        for name, adapter in self.all_instances().items():
            try:
                await adapter.connect()
            except ExchangeUnsupportedError as exc:
                self.mark_unsupported(name, str(exc))
            except Exception as exc:  # noqa: BLE001 - isolate one bad exchange from the rest
                logger.exception("Failed to connect exchange '%s'", name)
                self.mark_unsupported(name, f"connect() raised: {exc}")

    async def disconnect_all(self) -> None:
        for adapter in self._instances.values():
            try:
                await adapter.disconnect()
            except Exception:  # noqa: BLE001
                logger.exception("Error disconnecting exchange '%s'", adapter.name)

    async def health_snapshot(self) -> list[ExchangeHealth]:
        results: list[ExchangeHealth] = []
        for name in self.registered_names():
            if name in self._unsupported_reasons:
                results.append(
                    ExchangeHealth(
                        exchange=name,
                        status=ExchangeConnectionStatus.UNSUPPORTED,
                        markets_count=0,
                        last_update=None,
                        latency_ms=None,
                        reason=self._unsupported_reasons[name],
                    )
                )
                continue
            adapter = self._instances.get(name)
            if adapter is None:
                results.append(
                    ExchangeHealth(
                        exchange=name,
                        status=ExchangeConnectionStatus.DISCONNECTED,
                        markets_count=0,
                        last_update=None,
                        latency_ms=None,
                        reason="not yet initialized",
                    )
                )
                continue
            try:
                results.append(await adapter.get_exchange_status())
            except Exception as exc:  # noqa: BLE001
                results.append(
                    ExchangeHealth(
                        exchange=name,
                        status=ExchangeConnectionStatus.ERROR,
                        markets_count=0,
                        last_update=None,
                        latency_ms=None,
                        reason=str(exc),
                    )
                )
        return results


# Process-wide singleton. Adapters are registered against this in
# app.exchanges.adapters.__init__ (imported once at startup).
registry = ExchangeRegistry()
