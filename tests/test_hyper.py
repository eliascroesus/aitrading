"""The fast lab (config 2.4.0): cached features, parallel cells, the prop score, forex and gold first."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from hive.backtest import engine
from hive.backtest.costs import ALPACA_STOCKS
from hive.backtest.engine import Backtester
from hive.config import REPO_ROOT, PropRules, StrategyConfig, TrendRules
from hive.hive.propfirm import prop_metrics
from hive.hive.registry import HiveRegistry
from hive.operator.iterate import load_bounds
from hive.operator.lab import read_tests
from hive.operator.sweep import queue_staged, run_sweep
from tests.test_features import bars_from_path, textbook_setup
from tests.test_lab import _cfg_and_inputs


def _run(cfg, bars):
    return Backtester({"AAA": bars, "BBB": bars * 1.01}, bars, cfg, ALPACA_STOCKS, initial_equity=100.0).run()


def test_cached_features_give_the_same_backtest_as_fresh_ones():
    close, volume = textbook_setup(n_after=200)
    bars = bars_from_path(close, volume)
    for mode in ("high_breakout", "ma_pullback", "reset_breakout"):
        cfg = StrategyConfig(stocks=TrendRules(entry_mode=mode, direction="both", short_green_mult=1.0))
        warm = _run(cfg, bars)                                     # reuses whatever earlier cells cached
        engine._FEATURES.clear()
        engine._BASES.clear()
        cold = _run(cfg, bars)
        pd.testing.assert_series_equal(warm.equity, cold.equity)
        pd.testing.assert_frame_equal(warm.trades, cold.trades)
    assert len(engine._FEATURES) <= engine.FEATURE_CACHE_GROUPS and len(engine._BASES) <= engine.FEATURE_CACHE_GROUPS


def test_the_prop_score_rewards_passes_and_punishes_breaches():
    r = PropRules(eval_horizon_days=60, eval_step_days=5, breach_penalty=0.5)
    idx = lambda n: pd.date_range("2020-01-01", periods=n, freq="D")   # noqa: E731
    up = prop_metrics(pd.Series(100 * np.cumprod(np.full(400, 1.004)), index=idx(400)), r)
    assert up["prop_score"] == up["prop_pass_rate"] == 1.0
    rets = np.full(400, 0.004)
    rets[200] = -0.07
    m = prop_metrics(pd.Series(100 * np.cumprod(1 + rets), index=idx(400)), r)
    breach = m["prop_fail_daily_rate"] + m["prop_fail_total_rate"]
    assert breach > 0 and m["prop_score"] == pytest.approx(m["prop_pass_rate"] - 0.5 * breach)
    slow = prop_metrics(pd.Series(100 * np.cumprod(np.full(400, 1.0005)), index=idx(400)), r)
    assert slow["prop_score"] == 0.0                                 # a timeout costs nothing


def test_forex_and_gold_get_the_most_turns_in_the_queue(tmp_path):
    cfg = StrategyConfig()
    q = queue_staged(HiveRegistry(tmp_path / "hive.json", cfg.survival), cfg, load_bounds(REPO_ROOT / "config" / "bounds.toml"))
    assert [sl for sl, _, _ in q[:6]] == ["stocks", "crypto", "crypto", "macro", "macro", "macro"]


def test_parallel_workers_judge_and_record_every_cell(tmp_path):
    cfg, inputs = _cfg_and_inputs()
    reg = HiveRegistry(tmp_path / "state" / "hive.json", cfg.survival)
    tier = next(t for t in cfg.survival.tiers if t.name == "hive")
    ldir = tmp_path / "lab"
    recs = run_sweep(None, cfg, reg, tier, n_cells=4, on=date(2026, 10, 21), workers=2,
                     inputs_by_sleeve={"stocks": inputs, "crypto": inputs}, ldir=ldir)
    cells = [r for r in recs if r.get("kind") == "sweep"]
    assert len(cells) == 4 and all(r.get("verdict") for r in cells)
    tests = [t for t in read_tests(ldir) if t["by"] != "parent"]
    assert len(tests) == 4 and all(t["curves"]["outofsample"]["dates"] for t in tests)
    assert all(t.get("oos_score") is not None for t in tests)


def test_a_reward_to_risk_target_takes_the_profit():
    close, volume = textbook_setup(n_after=120)
    bars = bars_from_path(close, volume)
    cfg = StrategyConfig(stocks=TrendRules(take_profit_r=2.0))
    t = _run(cfg, bars).trades
    hits = t[t["reason"] == "target"]
    assert not hits.empty and (hits["r_multiple"] >= 1.9).all()         # closed near or beyond 2R, at the next open
    assert _run(StrategyConfig(), bars).trades["reason"].ne("target").all()   # 0 = no target


def test_a_clearly_better_cell_takes_the_weakest_seat_once_a_day(tmp_path):
    from hive.hive.propfirm import rules_key
    from hive.hive.registry import Child
    from hive.operator.sweep import seat_waiting

    cfg = StrategyConfig()
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = next(t for t in cfg.survival.tiers if t.name == "base")             # four seats
    key = rules_key(cfg.prop)
    for i in range(tier.max_children):
        ov = {"stocks.trail_atr": 4.0 + i}
        c = reg.spawn(Child(id="", name=f"v{i}", kind="variant", sleeve="stocks", overrides=ov), tier)
        reg.mark(c.id, 99.0, 100.0, on=date(2026, 10, 20))                    # all a little behind the parent
        reg.record_iteration({"date": "2026-10-19", "proposal": f"v{i}", "overrides": ov, "sleeve": "stocks",
                              "oos_score": 0.10 + 0.01 * i, "prop_key": key, "verdict": f"spawned shadow child {c.id}"})
    best = {"stocks.take_profit_r": 2.0}
    reg.record_iteration({"date": "2026-10-21", "proposal": "1:2 cell", "overrides": best, "sleeve": "stocks",
                          "oos_cagr": 0.1, "oos_dd": -0.1, "is_cagr": 0.02, "oos_score": 0.30, "prop_key": key,
                          "verdict": "passed but not spawned: tier base allows 4 children; 4 active"})
    out = seat_waiting(reg, tier, date(2026, 10, 21), None, "prop", cfg)
    assert [r["kind"] for r in out] == ["displaced", "seated"]
    assert reg.get(out[0]["proposal"]).status == "retired" and out[0]["proposal"] == "v0"   # the weakest seat
    assert any(c.overrides == best and c.status == "shadow" for c in reg.children)
    reg.record_iteration({"date": "2026-10-21", "proposal": "another", "overrides": {"stocks.take_profit_r": 3.0},
                          "sleeve": "stocks", "oos_cagr": 0.1, "oos_dd": -0.1, "is_cagr": 0.02, "oos_score": 0.5,
                          "prop_key": key, "verdict": "passed but not spawned: tier base allows 4 children; 4 active"})
    assert seat_waiting(reg, tier, date(2026, 10, 21), None, "prop", cfg) == []            # once a day
