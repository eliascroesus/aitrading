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


def test_a_cell_judged_under_old_rules_waits_behind_todays_and_never_blocks_a_seat(tmp_path):
    from hive.hive.propfirm import rules_key
    from hive.hive.registry import Child
    from hive.operator.sweep import seat_waiting, waiting_list

    cfg = StrategyConfig()
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = next(t for t in cfg.survival.tiers if t.name == "base")
    key = rules_key(cfg.prop)
    for i in range(tier.max_children):
        c = reg.spawn(Child(id="", name=f"v{i}", kind="variant", sleeve="stocks",
                            overrides={"stocks.trail_atr": 4.0 + i}), tier)
        reg.mark(c.id, 99.0, 100.0, on=date(2026, 10, 20))
    wait = "passed but not spawned: tier base allows 4 children; 4 active"
    reg.record_iteration({"date": "2026-10-20", "proposal": "old rules", "overrides": {"stocks.trail_atr": 9.0},
                          "sleeve": "stocks", "oos_cagr": 0.3, "oos_dd": -0.1, "is_cagr": 0.02, "oos_prop": 0.6,
                          "oos_score": 0.6, "prop_key": "0.1/0.0/old", "verdict": wait})
    reg.record_iteration({"date": "2026-10-21", "proposal": "today", "overrides": {"stocks.take_profit_r": 2.0},
                          "sleeve": "stocks", "oos_cagr": 0.1, "oos_dd": -0.1, "is_cagr": 0.02, "oos_score": 0.2,
                          "prop_key": key, "verdict": wait})
    assert [w["proposal"] for w in waiting_list(reg, None, "prop")] == ["old rules", "today"]
    assert [w["proposal"] for w in waiting_list(reg, None, "prop", key)] == ["today", "old rules"]
    out = seat_waiting(reg, tier, date(2026, 10, 21), None, "prop", cfg)
    assert [r["kind"] for r in out] == ["displaced", "seated"]
    assert any(c.overrides == {"stocks.take_profit_r": 2.0} and c.status == "shadow" for c in reg.children)


def test_rescores_take_every_other_turn_and_the_seated_cells_go_first(tmp_path):
    from hive.hive.registry import Child

    cfg = StrategyConfig()
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = next(t for t in cfg.survival.tiers if t.name == "hive")
    for i in range(5):                                         # scored under other prop rules
        reg.record_iteration({"date": "2026-10-20", "proposal": f"old{i}", "sleeve": "stocks",
                              "overrides": {"stocks.trail_atr": 4.0 + 0.5 * i}, "oos_cagr": 0.3 - 0.01 * i,
                              "oos_dd": -0.1, "is_cagr": 0.01 * i, "oos_score": 0.1, "prop_key": "old",
                              "verdict": "rejected: x"})
    reg.spawn(Child(id="", name="seat", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 6.0}), tier)
    q = [(ov, st) for sl, ov, st in queue_staged(reg, cfg, load_bounds(REPO_ROOT / "config" / "bounds.toml"))
         if sl == "stocks"]
    assert q[0] == ({"stocks.trail_atr": 6.0}, "rescore")      # the seated cell, though it ranks last
    assert [st for _, st in q[:10]] == ["rescore", "sweep"] * 5


def test_the_heat_guard_raises_every_stop_until_the_book_risks_no_more_than_the_cap():
    from hive.backtest.engine import Position

    long = Position(symbol="A", asset_class="stock", qty=10.0, entry_price=90.0, entry_date=pd.Timestamp("2020-01-01"),
                    stop_price=80.0, r_per_share=10.0, highest_close=100.0)
    short = Position(symbol="B~S", asset_class="stock", qty=1.0, entry_price=0.02, entry_date=pd.Timestamp("2020-01-01"),
                     stop_price=1 / 60.0, r_per_share=0.0, highest_close=0.02, short=True, shares=10.0,
                     real_entry=50.0, collateral=500.0)
    pos, closes = {"A": long, "B~S": short}, {"A": 100.0, "B~S": 1 / 50.0}
    heat = lambda: sum(Backtester._risk(p, closes[s]) for s, p in pos.items())   # noqa: E731
    assert heat() == pytest.approx(200.0 + 100.0)                  # 10 x (100 - 80) and 10 x (60 - 50)
    Backtester._guard_heat(pos, closes, 10_000.0, 0.05)              # 300 is under 5% of 10,000: untouched
    assert long.stop_price == 80.0
    Backtester._guard_heat(pos, closes, 10_000.0, 0.015)             # 150 allowed: every distance halves
    assert heat() == pytest.approx(150.0)
    assert long.stop_price == pytest.approx(90.0) and 1 / short.stop_price == pytest.approx(55.0)


def test_the_heat_guard_holds_a_days_loss_in_the_backtest_and_the_daily_run():
    from types import SimpleNamespace

    from hive.orchestrator.book import OpenPosition
    from hive.orchestrator.run import DailyRun

    close, volume = textbook_setup(n_after=200)
    bars = bars_from_path(close, volume)
    free = _run(StrategyConfig(), bars)
    cfg = StrategyConfig()
    cfg.risk.heat_guard_pct = 0.004
    held = _run(cfg, bars)
    assert not held.equity.equals(free.equity)
    day = (held.equity_low - held.equity.shift(1)) / held.equity.shift(1)
    assert day.min() >= -0.004 - 0.003                                # the cap, plus the open's gap

    armed = []
    run = SimpleNamespace(cfg=cfg, broker=SimpleNamespace(equity=lambda: 1_000.0, cancel_open_orders=lambda s: None),
                          journal=SimpleNamespace(write=lambda *a: armed.append(a)),
                          book=SimpleNamespace(save=lambda: None), _arm_stop=lambda p: armed.append(p.symbol))
    p = OpenPosition(symbol="AAA", asset_class="stock", qty=2.0, entry_price=90.0, entry_date="2020-01-01",
                     r_per_share=10.0, stop_price=80.0, highest_close=100.0)
    DailyRun._guard_heat(run, [("AAA", p, 100.0)])                    # 40 at risk, 4 allowed
    assert p.stop_price == pytest.approx(98.0) and "AAA" in armed and armed[-1][0] == "heat_guard"


def test_a_score_made_on_a_few_lucky_evaluations_does_not_outrank_a_steady_one():
    from hive.operator.sweep import rank_key

    r = PropRules(eval_horizon_days=60, eval_step_days=5)
    idx = pd.date_range("2020-01-01", periods=1300, freq="D")
    rng = np.random.default_rng(1)
    m = prop_metrics(pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.012, 1300)), index=idx), r)
    p, b = m["prop_pass_rate"], m["prop_fail_daily_rate"] + m["prop_fail_total_rate"]
    sd = np.sqrt(p + 0.25 * b - m["prop_score"] ** 2)                  # the spread of 1 / -0.5 / 0 outcomes
    n = m["prop_n"]
    assert m["prop_score_se"] == pytest.approx(sd * np.sqrt(n / (n - 1)) / np.sqrt(n * 5 / 60), rel=1e-9)
    lucky = {"oos_score": 0.14, "oos_score_se": 0.08}
    steady = {"oos_score": 0.11, "oos_score_se": 0.02}
    assert rank_key(steady, "prop") < rank_key(lucky, "prop")
    idle = {"oos_score": 0.0, "oos_score_se": 0.0}                    # never passes, never breaches
    small = {"oos_score": 0.03, "oos_score_se": 0.035, "oos_prop_days": 31}
    assert rank_key(small, "prop") < rank_key(idle, "prop")
