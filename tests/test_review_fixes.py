"""Regression tests for the adversarial-review findings."""

import numpy as np
import pandas as pd
import pytest

from hive.backtest.costs import ALPACA_STOCKS, CostModel
from hive.backtest.engine import Backtester
from hive.config import RiskLimits, StrategyConfig
from hive.data.bars import BAR_COLUMNS
from hive.data.store import BarStore
from hive.execution.paper import PaperBroker
from hive.signals.regime import class_multipliers, short_multipliers
from hive.signals.sizing import size_position
from tests.test_features import bars_from_path, textbook_setup


def _scenario():
    close, volume = textbook_setup(n_after=120)
    return bars_from_path(close, volume)


# C4 — delisted / truncated names while held -----------------------------------------
def test_position_in_delisted_name_is_closed_and_equity_reconciles():
    bars = _scenario()
    cfg = StrategyConfig(risk=RiskLimits(max_positions=3, max_new_entries_per_day=3))
    bt_full = Backtester({"AAA": bars}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0)
    res_full = bt_full.run()
    assert not res_full.trades.empty
    entry = res_full.trades["entry_date"].iloc[0]
    cut_at = bars.index[bars.index.get_loc(entry) + 5]
    truncated = bars.loc[:cut_at]
    res = Backtester({"AAA": truncated}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0).run()
    assert not res.trades.empty and res.trades["reason"].iloc[0] == "delisted"
    assert res.equity.iloc[-1] == pytest.approx(100.0 + res.trades["pnl"].sum(), abs=1e-6)
    assert res.daily_log[-1]["n_pos"] == 0


def test_halt_cooldown_always_releases():
    bars = _scenario()
    cfg = StrategyConfig(risk=RiskLimits(max_positions=3, max_drawdown_halt_pct=0.001, halt_cooldown_bars=3))
    res = Backtester({"AAA": bars}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0).run()
    states = [d["state"] for d in res.daily_log]
    if "halted" in states:
        first = states.index("halted")
        assert any(s != "halted" for s in states[first:]), "a halt must release after the cooldown"


# M9 — mixed calendars -------------------------------------------------------------------
def test_regime_is_forward_filled_onto_a_wider_calendar():
    bars = _scenario()
    daily = bars.copy()
    daily.index = pd.date_range(bars.index[0], periods=len(bars), freq="D")  # 7-day calendar
    cfg = StrategyConfig()
    bt = Backtester({"AAA": bars, "BBB": daily}, bars, cfg, ALPACA_STOCKS,
                    asset_class_by_symbol={"BBB": "crypto"}, calendar=daily.index)
    assert len(bt.regime) == len(daily.index) and bt.regime["state"].notna().all()


# M1 — multipliers scale the cap too ---------------------------------------------------------
def test_regime_multiplier_scales_position_cap():
    risk = RiskLimits(max_position_pct_stock=0.2)
    full = size_position(equity=100, price=50, stop_price=49.9, risk=risk)
    half = size_position(equity=100, price=50, stop_price=49.9, risk=risk, risk_multiplier=0.5)
    assert full.notional == pytest.approx(20.0) and half.notional == pytest.approx(10.0)


def test_class_multipliers_match_spec():
    red = pd.Series({"state": "red", "risk_multiplier": 0.0, "allow_new_stock": False, "allow_new_etf": True,
                     "crash_guard": False})
    m = class_multipliers(red)
    assert m == {"stock": 0.0, "crypto": 0.0, "etf": 1.0, "macro": 1.0}       # currencies have no equity regime
    yellow = pd.Series({"state": "yellow", "risk_multiplier": 0.5, "allow_new_stock": True, "allow_new_etf": True,
                        "crash_guard": False})
    assert class_multipliers(yellow) == {"stock": 0.5, "crypto": 0.5, "etf": 0.5, "macro": 1.0}
    assert class_multipliers(red, use_filter=False) == {"stock": 1.0, "etf": 1.0, "crypto": 1.0, "macro": 1.0}
    # shorts are the mirror: full size in RED, halved in YELLOW, the lever in GREEN, none in a crash
    green = pd.Series({"state": "green", "risk_multiplier": 1.0, "allow_new_stock": True, "allow_new_etf": True,
                       "crash_guard": False})
    crash = pd.Series({"state": "red", "risk_multiplier": 0.0, "allow_new_stock": False, "allow_new_etf": False,
                       "crash_guard": True})
    g = {"stock": 0.25, "etf": 0.25, "crypto": 0.0}
    assert short_multipliers(red, g) == {"stock": 1.0, "etf": 1.0, "crypto": 1.0, "macro": 1.0}
    assert short_multipliers(yellow, g)["stock"] == 0.5 and short_multipliers(green, g)["stock"] == 0.25
    assert short_multipliers(green, g)["crypto"] == 0.0 and short_multipliers(crash, g)["stock"] == 0.0


# C2 — splicing adjustment bases -----------------------------------------------------------
def test_upsert_keeps_adjustment_factor_continuous(tmp_path):
    store = BarStore(tmp_path)
    idx = pd.bdate_range("2020-01-01", periods=400)
    close = pd.Series(np.linspace(100, 140, 400), index=idx)
    old = pd.DataFrame({c: close for c in BAR_COLUMNS}, index=idx)
    old["adj_close"] = close / close.iloc[0]  # Qlib-style: normalised to 1.0 at the start
    old["volume"] = 1.0
    store.put("stock", "AAA", old, source="qlib")
    new_idx = idx[-50:].append(pd.bdate_range(idx[-1] + pd.Timedelta(days=1), periods=20))
    new_close = pd.Series(np.linspace(close.iloc[-50], 150, 70), index=new_idx)
    new = pd.DataFrame({c: new_close for c in BAR_COLUMNS}, index=new_idx)
    new["adj_close"] = new_close * 0.98  # yfinance-style: dollars, backward-adjusted for a dividend
    new["volume"] = 1.0
    store.upsert("stock", "AAA", new, source="yfinance")
    merged = store.get("stock", "AAA")
    factor = merged["adj_close"] / merged["close"]
    assert factor.pct_change().abs().max() < 0.05, "no step in adj/close at the seam"
    assert merged["adj_close"].pct_change().abs().max() < 0.05


# H1 / C3 — paper broker fills next open and evaluates stops -----------------------------------
def test_paper_broker_queues_and_fills_at_next_open(tmp_path):
    prices = {"AAA": 10.0}
    pb = PaperBroker(tmp_path / "p.json", lambda s: prices.get(s), starting_cash=100.0,
                     cost=CostModel(commission_pct=0.0, slippage_bps=0.0))
    r = pb.submit_market("AAA", "buy", notional=20.0)
    assert r.ok and r.filled_qty == 0.0 and pb.pending_orders()
    assert pb.positions() == {}
    fills = pb.process_pending(lambda s: 10.5)  # next session opens higher
    assert len(fills) == 1 and fills[0].fill_price == pytest.approx(10.5)
    assert pb.positions()["AAA"].qty == pytest.approx(20 / 10.5)
    assert pb.submit_stop("AAA", pb.positions()["AAA"].qty, 9.0).ok
    assert pb.check_stops(low_fn=lambda s: 9.5) == []
    out = pb.check_stops(low_fn=lambda s: 8.0, open_fn=lambda s: 8.5)  # gapped through: fill at open
    assert len(out) == 1 and out[0].fill_price == pytest.approx(8.5)
    assert pb.positions() == {}
