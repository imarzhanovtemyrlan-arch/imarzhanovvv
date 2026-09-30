import time

from app.arbitrage.calculator import compute_profit
from app.models.market import OrderBook, OrderBookLevel


def make_book(exchange: str, symbol: str, bids, asks) -> OrderBook:
    now = time.time()
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        timestamp=now,
        bids=[OrderBookLevel(p, q) for p, q in bids],
        asks=[OrderBookLevel(p, q) for p, q in asks],
        received_at=now,
    )


def test_profitable_spread_after_fees():
    buy_book = make_book("A", "BTCUSDT", bids=[(99900, 5)], asks=[(100000, 5)])
    sell_book = make_book("B", "BTCUSDT", bids=[(100500, 5)], asks=[(100600, 5)])

    breakdown = compute_profit(
        buy_book=buy_book,
        sell_book=sell_book,
        trade_size_quote=1000.0,
        buy_taker_fee=0.001,
        sell_taker_fee=0.001,
    )

    assert breakdown.gross_spread > 0
    # gross ~0.5%, fees 0.2% total, slippage ~0 (plenty depth) -> net should be positive
    assert breakdown.net_spread > 0
    assert breakdown.estimated_profit > 0


def test_fees_can_erase_profit():
    buy_book = make_book("A", "BTCUSDT", bids=[(99990, 5)], asks=[(100000, 5)])
    sell_book = make_book("B", "BTCUSDT", bids=[(100010, 5)], asks=[(100020, 5)])

    breakdown = compute_profit(
        buy_book=buy_book,
        sell_book=sell_book,
        trade_size_quote=1000.0,
        buy_taker_fee=0.005,  # 0.5% - larger than the ~0.02% gross spread
        sell_taker_fee=0.005,
    )

    assert breakdown.net_spread < 0
    assert breakdown.estimated_profit < 0


def test_thin_book_produces_slippage():
    # Only enough depth at the top level to cover a fraction of requested size
    buy_book = make_book("A", "BTCUSDT", bids=[(99900, 5)], asks=[(100000, 0.001), (100200, 5)])
    sell_book = make_book("B", "BTCUSDT", bids=[(100500, 0.001), (100300, 5)], asks=[(100600, 5)])

    breakdown = compute_profit(
        buy_book=buy_book,
        sell_book=sell_book,
        trade_size_quote=1000.0,
        buy_taker_fee=0.001,
        sell_taker_fee=0.001,
    )

    assert breakdown.estimated_slippage > 0
    assert breakdown.buy_vwap > 100000  # walked into the worse second level


def test_vwap_slippage_is_not_subtracted_twice():
    buy_book = make_book("A", "BTCUSDT", bids=[(99900, 5)], asks=[(100000, 0.001), (100200, 5)])
    sell_book = make_book("B", "BTCUSDT", bids=[(100500, 0.001), (100300, 5)], asks=[(100600, 5)])
    breakdown = compute_profit(buy_book, sell_book, 1000.0, 0.001, 0.001)
    expected = breakdown.gross_spread - breakdown.buy_fee - breakdown.sell_fee
    assert abs(breakdown.net_spread - expected) < 1e-12
