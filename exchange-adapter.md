# Adding a New Exchange

Adding an exchange requires **zero changes** to the arbitrage engine, the
API layer, or the registry implementation. You only add one new file plus
one registration line.

## Steps

1. **Read the exchange's official API docs first.** Do not guess endpoints.
   Confirm:
   - REST base URL and the market-metadata endpoint (symbols, filters/precision, status)
   - Whether a public WebSocket order book stream exists, and its format
     (diff-depth vs. partial/snapshot depth)
   - Whether public endpoints require an API key (most spot market-data
     endpoints don't - if trading fees require auth, use the exchange's
     documented default public rate instead, clearly flagged as an
     approximation, exactly like `BinanceAdapter.get_fees`)
   - Rate limits

2. **Create `backend/app/exchanges/adapters/<name>.py`:**

   ```python
   from app.exchanges.base import ExchangeAdapter, ExchangeUnsupportedError, OrderBookCallback

   class MyExchangeAdapter(ExchangeAdapter):
       name = "myexchange"
       is_supported = True  # only after you've verified it against real docs

       async def get_markets(self): ...
       async def get_fees(self, symbol=None): ...
       async def get_exchange_status(self): ...
       async def get_orderbook(self, symbol, depth=50): ...
       async def get_ticker(self, symbol): ...
       async def subscribe_orderbook(self, symbol, callback): ...
       async def unsubscribe_orderbook(self, symbol): ...
       async def connect(self): ...
       async def disconnect(self): ...
   ```

   Reference implementations, in increasing order of order-book protocol
   complexity:
   - `app/exchanges/adapters/binance.py` - WebSocket partial-depth stream
     that resends a full top-N snapshot on every update. Simplest correct
     approach when the exchange offers it.
   - `app/exchanges/adapters/okx.py` - same "full snapshot every push"
     pattern (`books5` channel), plus a text-based `ping`/`pong` heartbeat
     instead of protocol-level WS pings.
   - `app/exchanges/adapters/gate.py` - same "full snapshot every push"
     pattern (`spot.order_book` channel), JSON-based `spot.ping`/`spot.pong`
     heartbeat message.
   - `app/exchanges/adapters/bybit.py` - the exchange only offers
     snapshot+delta (no repeated full snapshots), so this adapter
     maintains a local price->quantity map per side and applies deltas
     correctly (quantity `"0"` removes a level). The merge logic is
     extracted into a standalone `merge_levels()` function specifically so
     it can be unit-tested without any network dependency - see
     `tests/test_bybit_orderbook_merge.py`. **Prefer the snapshot-resend
     pattern when the exchange supports it** - it has no merge logic to
     get subtly wrong. Only implement delta merging when that's genuinely
     the only option, and give it dedicated tests like Bybit's.
   - `app/exchanges/adapters/mexc.py` - the exchange's public order-book
     WebSocket is Protobuf-encoded, and no `.proto` schema was available to
     decode it correctly here. Rather than guess a binary wire format
     (exactly what spec section 4 forbids - "не имитируй его работу"),
     this adapter polls the REST depth endpoint on an interval instead.
     **This is the fallback of last resort** - only reach for it when
     neither a snapshot-resend nor a delta-merge WebSocket is something you
     can implement correctly from documentation actually in hand.

3. **Register it** in `backend/app/exchanges/adapters/__init__.py`:

   ```python
   from app.exchanges.adapters.myexchange import MyExchangeAdapter
   registry.register("myexchange", MyExchangeAdapter)
   ```

4. **If the exchange's API can't be reliably integrated** (no usable public
   WebSocket, undocumented behavior, etc.), do not fake it. Either:
   - Implement REST-polling fallback only (still valid, just less real-time), or
   - Register it, but have `connect()` raise `ExchangeUnsupportedError("reason")`.
     The registry will catch this and mark the exchange `UNSUPPORTED` with
     your reason, visible on the EXCHANGES screen, without crashing anything else.

5. **Write tests** using the same patterns as `tests/test_registry_and_mock.py`
   - your adapter's normalization and error-handling logic should be testable
   without hitting the real network (mock the HTTP/WS layer, or extract the
   pure-normalization functions so they can be unit tested directly).

## Symbol normalization

If the exchange spells symbols in a way `SymbolNormalizer` doesn't already
handle (see `app/exchanges/symbol_normalizer.py`), extend the known quote
asset list or separator handling there - don't special-case it inside your
adapter, since normalization must stay exchange-agnostic for the arbitrage
engine to work across all exchanges uniformly.
