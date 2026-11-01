from types import SimpleNamespace

import pytest

import backtest
from backtest import BacktestMarkets, Market, Replay

H = 3600
T0 = 1_790_000_000 // H * H


def opts(**kw):
    base = dict(series=["KXT"], fill_cap=0.5, poly_markets=10, poly_query="", poly_half_spread=0.01, poly_max=100,
                step_hours=1, quiet=True)
    return SimpleNamespace(**{**base, **kw})


def bar(end, bid, ask, vol=1000, bid_hi=None, ask_lo=None):
    return (end, bid, ask, bid, bid_hi if bid_hi is not None else bid, ask_lo if ask_lo is not None else ask, ask, vol)


def make(bars, result="no", close=T0 + 5 * H):
    replay = Replay(None, T0, T0 + 10 * H, opts())
    m = Market("kalshi", "KXT-1", "Will X say Y?", "Y", "KXT", T0 - H, close, "2026-12-01T00:00:00Z", result, series="KXT")
    m._bars = bars
    replay._kalshi = [m]
    bt = BacktestMarkets("s", replay, opts())
    bt.fund(100)
    return bt, m


def test_prices_come_from_the_last_completed_hour_and_result_is_hidden():
    bt, _ = make([bar(T0, 0.10, 0.12), bar(T0 + H, 0.60, 0.62)])
    rows = bt.mentions()
    assert (rows[0]["yes_bid"], rows[0]["yes_ask"]) == (0.10, 0.12)  # the T0+H bar is the future
    assert rows[0]["result"] is None
    assert rows[0]["close_time"] == "2026-12-01T00:00:00Z"  # scheduled close, not the real early close


def test_buy_no_fills_at_one_minus_bid_with_kalshi_fee_and_volume_cap():
    bt, _ = make([bar(T0, 0.10, 0.12, vol=40)])
    o = bt.buy("kalshi", "KXT-1", "no", 30)
    assert o["filled_count"] == 20  # 50% of 40 contracts
    assert o["avg_price"] == pytest.approx(0.90)
    assert o["fees"] == 0.13  # ceil(0.07 * 20 * .9 * .1 * 100) / 100
    assert bt.book.cash == pytest.approx(100 - 18 - 0.13)


def test_settles_on_real_result_after_close():
    bt, _ = make([bar(T0, 0.10, 0.12)], result="no", close=T0 + 2 * H)
    bt.buy("kalshi", "KXT-1", "no", 10)
    bt.advance(T0 + H)
    assert bt.position("kalshi", "KXT-1", "no") == 10
    bt.advance(T0 + 2 * H)
    assert bt.position("kalshi", "KXT-1", "no") == 0
    assert bt.book.realized_pnl == pytest.approx(10 * (1 - 0.90))


def test_resting_limit_fills_when_next_hour_trades_through():
    bt, _ = make([bar(T0, 0.10, 0.12), bar(T0 + H, 0.10, 0.12, ask_lo=0.07)])
    o = bt.buy("kalshi", "KXT-1", "yes", 10, limit=0.08)
    assert o["status"] == "open" and bt.book.reserved == pytest.approx(0.8)
    bt.advance(T0 + H)
    assert bt.book.orders[0].status == "filled"
    assert bt.book.reserved == pytest.approx(0)
    assert bt.position("kalshi", "KXT-1", "yes") == 10


def test_live_client_is_replaced_during_a_backtest():
    import oms_client
    bt, _ = make([bar(T0, 0.10, 0.12)])
    oms_client._BACKTEST = bt
    try:
        assert oms_client.PredictionMarkets("s") is bt
    finally:
        oms_client._BACKTEST = None
