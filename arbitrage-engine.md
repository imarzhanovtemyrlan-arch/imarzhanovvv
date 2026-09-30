# Arbitrage Engine

`app/arbitrage/engine.py` is the only module that decides whether an
opportunity is real and worth showing. It has no knowledge of any specific
exchange's API - it consumes only normalized `OrderBook`, `ExchangeMarket`,
and `ExchangeFees` objects (via `ExchangeSnapshot`).

## Pipeline, per symbol, per exchange pair

1. **`check_market_pair`** - reject if base/quote assets differ, or either
   market isn't in `TRADING` status.
2. **`check_orderbook_freshness`** - reject if either book is older than
   `FilterConfig.max_data_age_seconds` (default 3s), or either side is empty.
3. **Naive gross spread check** - `(best_bid - best_ask) / best_ask`. Must be
   positive to even consider the pair.
4. **`check_plausible_spread`** - a naive spread above
   `max_plausible_gross_spread` (default 15%) almost always means bad data
   (wrong precision, halted market, stale snapshot) rather than a real
   opportunity. Rejected here rather than shown "pending confirmation",
   per spec section 10.
5. **`check_available_volume`** - total notional available across both
   books' relevant side must clear `min_available_notional` (default $10).
6. **`compute_profit`** (in `calculator.py`) - the real calculation:
   - Walks the buy exchange's asks and the sell exchange's bids via VWAP
     (`vwap.py`) for the requested trade size (capped by available depth).
   - `gross_spread` = `(sell_vwap - buy_vwap) / buy_vwap`.
   - `estimated_slippage` = how much worse the VWAP fill is than the naive
     best bid/ask, summed across both legs.
   - `net_spread` = `gross_spread - buy_fee - sell_fee - estimated_slippage`.
   - `estimated_profit` = `net_spread * filled_quote` (using the SMALLER of
     the two legs' actual fill, so profit reflects what both sides can
     actually execute, not just one leg's depth).
7. Only opportunities with `net_spread > 0` are returned.
8. Results are sorted by `net_spread` descending.

## Why VWAP instead of best bid/ask

A $10,000 arbitrage using only the best bid/ask price would overstate profit
whenever the book is thin at the top. `vwap_for_notional` walks each price
level, accumulating fill until the target notional is reached, and reports
whether the book had enough depth (`fully_filled`). The pair-details screen
in the frontend shows this same calculation for five trade sizes ($100 to
$10,000) so the user can see where slippage starts to bite.

## Extending the filters

`FilterConfig` is a plain dataclass - construct a custom one per use case
(e.g. a stricter `max_data_age_seconds` for a fast-moving pair) and pass it
into `ArbitrageEngine(filter_config=...)`. Do not bypass the filters by
calling `compute_profit` directly outside the engine - that reintroduces the
false-signal risks section 10 of the spec exists to prevent.
