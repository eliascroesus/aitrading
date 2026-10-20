"""Shorts (config 2.3.0): the long rules on the market seen upside down, real money on the real price."""

import numpy as np
import pandas as pd
import pytest

from hive.backtest.costs import ALPACA_STOCKS
from hive.backtest.engine import SHORT, Backtester
from hive.config import RiskLimits, StrategyConfig, TrendRules
from hive.data.bars import mirror_bars
from tests.test_features import bars_from_path, textbook_setup


def _cfg(direction: str, **risk) -> StrategyConfig:
    return StrategyConfig(stocks=TrendRules(direction=direction),
                          risk=RiskLimits(max_positions=3, max_new_entries_per_day=3, **risk))


def test_mirror_bars_turn_the_market_upside_down():
    b = bars_from_path(np.array([100.0, 110.0, 99.0]))
    m = mirror_bars(b)
    assert np.allclose(m["close"], 1 / b["close"]) and np.allclose(m["high"], 1 / b["low"])
    assert (m["high"] >= m["low"]).all() and np.allclose(m["volume"], b["volume"])


def test_a_falling_market_is_shorted_like_a_rising_one_is_bought():
    close, volume = textbook_setup(n_after=120)
    up = bars_from_path(close, volume)
    down = bars_from_path(10_000.0 / close, volume)          # the same market, upside down
    longs = Backtester({"AAA": up}, up, _cfg("long"), ALPACA_STOCKS, initial_equity=100.0).run().trades
    res = Backtester({"AAA": down}, down, _cfg("short"), ALPACA_STOCKS, initial_equity=100.0).run()
    shorts = res.trades
    assert not shorts.empty and set(shorts["side"]) == {"short"}
    assert list(shorts["entry_date"]) == list(longs["entry_date"])            # same signals, mirrored
    # real money: a short gains shares x (entry - exit), less fees; every trade closed at the end
    for _, t in shorts.iterrows():
        assert t["pnl"] == pytest.approx(t["qty"] * (t["entry_price"] - t["exit_price"]) - t["fees"], rel=1e-9)
    assert res.equity.iloc[-1] == pytest.approx(100.0 + shorts["pnl"].sum(), abs=1e-6)
    assert shorts["pnl"].sum() > 0                                            # it made money as the price fell
    assert Backtester({"AAA": down}, down, _cfg("long"), ALPACA_STOCKS, initial_equity=100.0).run().trades.empty


def test_a_short_the_price_runs_against_loses_real_money_and_never_borrows():
    close, volume = textbook_setup()                         # ends on the breakdown: the short goes on
    inv = 10_000.0 / close
    squeeze = inv[-1] * np.cumprod(np.full(15, 1.05))         # then +5% a day against it
    path = np.concatenate([inv, squeeze])
    bars = bars_from_path(path, np.r_[volume, np.full(15, 2e6)])
    res = Backtester({"AAA": bars}, bars, _cfg("short"), ALPACA_STOCKS, initial_equity=100.0).run()
    t = res.trades
    assert not t.empty and (t["side"] == "short").all()
    worst = t.iloc[-1]
    assert worst["exit_price"] > worst["entry_price"] and worst["pnl"] < 0
    assert res.exposure.max() <= 1.0 + 1e-9                                   # no leverage: never more than equity held
    assert res.equity.iloc[-1] == pytest.approx(100.0 + t["pnl"].sum(), abs=1e-6)


def test_both_directions_never_hold_one_market_long_and_short_at_once():
    close, volume = textbook_setup(n_after=60)
    fall = close[-1] * np.cumprod(np.tile([0.994, 1.0], 200))
    path = np.concatenate([close, fall])
    bars = bars_from_path(path, np.r_[volume, np.full(len(fall), 2e6)])
    bench = bars_from_path(np.linspace(100, 101, len(path)))                # a flat-ish, rising benchmark
    cfg = StrategyConfig(stocks=TrendRules(direction="both", short_green_mult=1.0),
                         risk=RiskLimits(max_positions=3, max_new_entries_per_day=3))
    bt = Backtester({"AAA": bars}, bench, cfg, ALPACA_STOCKS, initial_equity=100.0)
    assert {"AAA", "AAA" + SHORT} <= set(bt.features)
    t = bt.run().trades
    assert not t.empty
    spans = sorted((pd.Timestamp(r["entry_date"]), pd.Timestamp(r["exit_date"]), r["side"]) for _, r in t.iterrows())
    for (_a0, a1, _), (b0, _b1, _) in zip(spans, spans[1:], strict=False):
        assert b0 >= a1                                                     # one position per market at a time
