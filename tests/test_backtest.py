import numpy as np
import pandas as pd
import pytest

from hive.backtest.costs import ALPACA_STOCKS, CostModel
from hive.backtest.engine import Backtester
from hive.backtest.metrics import equity_metrics, trade_metrics
from hive.config import RiskLimits, StrategyConfig
from tests.test_features import bars_from_path, textbook_setup


@pytest.fixture
def cfg() -> StrategyConfig:
    return StrategyConfig(risk=RiskLimits(max_positions=3, max_new_entries_per_day=3))


def _scenario():
    close, volume = textbook_setup(n_after=120)
    return bars_from_path(close, volume)


def test_cost_model_moves_price_against_us():
    cm = CostModel(commission_pct=0.001, slippage_bps=10)
    assert cm.fill_price(100, "buy") == pytest.approx(100.1)
    assert cm.fill_price(100, "sell") == pytest.approx(99.9)
    assert cm.commission(1000) == pytest.approx(1.0)


def test_backtester_trades_and_accounts_consistently(cfg):
    bars = _scenario()
    bt = Backtester({"AAA": bars}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0)
    res = bt.run()
    assert len(res.equity) == len(bars)
    assert res.equity.iloc[0] == pytest.approx(100.0, abs=1.0)
    assert not res.trades.empty, "the breakout after the quiet pullback should be traded"
    # P&L accounting: final equity == start + sum(trade pnl) (all positions liquidated at end)
    assert res.equity.iloc[-1] == pytest.approx(100.0 + res.trades["pnl"].sum(), abs=1e-6)
    # Entries fill the bar *after* the signal (next-open convention)
    f = bt.features["AAA"]
    for _, t in res.trades.iterrows():
        prev = f.index[f.index.get_loc(t["entry_date"]) - 1]
        assert bool(f.loc[prev, "entry_signal"])
        assert t["entry_date"] > prev
    assert set(res.trades["reason"]) <= {"stop", "trend_break", "failure_stop", "review", "max_hold",
                                         "end_of_test", "dd_halt"}
    assert res.metrics["n_trades"] == len(res.trades)
    assert "r_multiple" in res.trades.columns


def test_regime_filter_blocks_entries_when_benchmark_weak(cfg):
    bars = _scenario()
    weak_bench = bars_from_path(100 * np.cumprod(np.full(len(bars), 0.999)))
    res = Backtester({"AAA": bars}, weak_bench, cfg, ALPACA_STOCKS, initial_equity=100.0).run()
    assert res.trades.empty
    res2 = Backtester({"AAA": bars}, weak_bench, cfg, ALPACA_STOCKS, initial_equity=100.0,
                      use_regime_filter=False).run()
    assert not res2.trades.empty


def test_etfs_may_enter_in_red_regime_but_stocks_may_not(cfg):
    bars = _scenario()
    weak_bench = bars_from_path(100 * np.cumprod(np.full(len(bars), 0.999)))
    res = Backtester({"AAA": bars}, weak_bench, cfg, ALPACA_STOCKS, initial_equity=100.0,
                     asset_class_by_symbol={"AAA": "etf"}).run()
    assert not res.trades.empty


def test_point_in_time_universe_respected(cfg):
    from hive.data.universe import PointInTimeUniverse

    bars = _scenario()
    uni = PointInTimeUniverse.static(["BBB"])
    res = Backtester({"AAA": bars, "BBB": bars * 2}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0,
                     universe=uni).run()
    assert set(res.trades["symbol"]) <= {"BBB"}


def test_candidate_filter_hook_can_veto(cfg):
    bars = _scenario()
    veto = lambda day, cands: cands.iloc[0:0]  # noqa: E731
    res = Backtester({"AAA": bars}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0,
                     candidate_filter=veto).run()
    assert res.trades.empty


def test_risk_caps_bound_position_size(cfg):
    bars = _scenario()
    res = Backtester({"AAA": bars}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0).run()
    t = res.trades.iloc[0]
    notional = t["qty"] * t["entry_price"]
    assert notional <= 100.0 * cfg.risk.max_position_pct_stock + 1e-6
    # Dollar risk to the initial stop is ~1% of equity (slippage can nudge it slightly).
    assert t["qty"] * t["r_per_share"] <= 100.0 * cfg.risk.risk_per_trade_pct * 1.05


def test_alternative_entry_modes(cfg):
    bars = _scenario()
    bt = Backtester({"AAA": bars}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0, entry_mode="template_cross")
    # template_cross fires only on the first day the template passes
    f = bt.features["AAA"]
    days = [d for d, syms in bt.signal_days.items() if "AAA" in syms]
    assert days and all(bool(f.loc[d, "template_ok"]) for d in days)
    first_ok = f.index[f["template_ok"].to_numpy()][0]
    assert days[0] == first_ok
    res = bt.run()
    assert not res.trades.empty
    null = Backtester({"AAA": bars}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0,
                      entry_mode="random_template", seed=1)
    assert all(bool(f.loc[d, "template_ok"]) for d in null.signal_days)


def test_drawdown_halt_liquidates(cfg):
    cfg.risk.max_drawdown_halt_pct = 0.02
    close, volume = textbook_setup()
    crash = close[-1] * np.cumprod(np.full(60, 0.97))
    full = np.concatenate([close, crash])
    vol = np.concatenate([volume, np.full(60, 3e6)])
    bars = bars_from_path(full, vol)
    res = Backtester({"AAA": bars}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0).run()
    assert res.equity.min() > 60.0


def test_metrics_basic():
    idx = pd.bdate_range("2020-01-01", periods=253)
    eq = pd.Series(np.linspace(100, 110, 253), index=idx)
    m = equity_metrics(eq)
    assert m["total_return"] == pytest.approx(0.10)
    assert m["max_drawdown"] == 0.0
    trades = pd.DataFrame({"pnl": [10, -5, 3], "return_pct": [10, -5, 3], "hold_days": [10, 5, 7], "fees": [0.1, 0.1, 0.1]})
    t = trade_metrics(trades)
    assert t["win_rate"] == pytest.approx(2 / 3)
    assert t["profit_factor"] == pytest.approx(13 / 5)
