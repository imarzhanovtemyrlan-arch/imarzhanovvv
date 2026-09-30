"""
Import this module once at application startup to populate the registry
with all available exchange adapters.

To add a new exchange:
  1. Create exchanges/adapters/<name>.py implementing ExchangeAdapter.
  2. Add `registry.register("<name>", YourAdapterClass)` below.
No other file needs to change (spec section 32).

Each real adapter's import is wrapped so a missing optional dependency
(e.g. websockets/httpx not yet installed) marks that one exchange
unsupported at startup instead of crashing the whole app - the mock adapter
always registers regardless.
"""
from app.exchanges.adapters.mock import MockExchangeAdapter
from app.exchanges.registry import registry

registry.register("mock", MockExchangeAdapter)

try:
    from app.exchanges.adapters.binance import BinanceAdapter

    registry.register("binance", BinanceAdapter)
except ImportError:  # pragma: no cover - optional dependency not yet installed
    pass

try:
    from app.exchanges.adapters.bybit import BybitAdapter

    registry.register("bybit", BybitAdapter)
except ImportError:  # pragma: no cover
    pass

try:
    from app.exchanges.adapters.okx import OkxAdapter

    registry.register("okx", OkxAdapter)
except ImportError:  # pragma: no cover
    pass

try:
    from app.exchanges.adapters.mexc import MexcAdapter

    registry.register("mexc", MexcAdapter)
except ImportError:  # pragma: no cover
    pass

try:
    from app.exchanges.adapters.gate import GateAdapter

    registry.register("gate", GateAdapter)
except ImportError:  # pragma: no cover
    pass

try:
    from app.exchanges.adapters.bitget import BitgetAdapter

    registry.register("bitget", BitgetAdapter)
except ImportError:  # pragma: no cover
    pass
