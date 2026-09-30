from app.exchanges.adapters.bybit import LocalOrderBookState, merge_levels


def test_merge_levels_upserts_and_removes():
    book_side = {100.0: 1.0, 101.0: 2.0}
    merge_levels(book_side, [["100.0", "1.5"], ["102.0", "3.0"], ["101.0", "0"]])
    assert book_side == {100.0: 1.5, 102.0: 3.0}


def test_local_state_snapshot_then_delta():
    state = LocalOrderBookState()
    state.apply_snapshot(
        bids=[["100.0", "1.0"], ["99.0", "2.0"]],
        asks=[["101.0", "1.0"], ["102.0", "2.0"]],
    )
    bids, asks = state.to_levels()
    assert bids[0].price == 100.0 and bids[0].quantity == 1.0
    assert asks[0].price == 101.0

    # Delta: update best bid, remove second bid, add a new ask level.
    state.apply_delta(
        bids=[["100.0", "5.0"], ["99.0", "0"]],
        asks=[["103.0", "0.5"]],
    )
    bids, asks = state.to_levels()
    assert len(bids) == 1
    assert bids[0].quantity == 5.0
    assert len(asks) == 3
    assert asks[-1].price == 103.0


def test_local_state_new_snapshot_resets_stale_levels():
    state = LocalOrderBookState()
    state.apply_snapshot(bids=[["100.0", "1.0"]], asks=[["101.0", "1.0"]])
    # A fresh snapshot (e.g. after Bybit re-sends one) must fully replace,
    # not merge with, the previous state.
    state.apply_snapshot(bids=[["50.0", "9.0"]], asks=[["51.0", "9.0"]])
    bids, asks = state.to_levels()
    assert [b.price for b in bids] == [50.0]
    assert [a.price for a in asks] == [51.0]
