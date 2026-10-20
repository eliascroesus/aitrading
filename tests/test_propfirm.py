"""Prop-firm evaluations: the simulator, forward status, punishment and the readiness checklist."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from hive.config import PropRules, Settings, StrategyConfig
from hive.hive.children import retire_prop_failures
from hive.hive.propfirm import forward_status, prop_metrics, readiness
from hive.hive.registry import Child, HiveRegistry
from hive.survival.ledger import SurvivalLedger


def _series(rets, start=100.0):
    return pd.Series(start * np.cumprod(1 + np.asarray(rets, dtype=float)),
                     index=pd.date_range("2020-01-01", periods=len(rets), freq="D"))


def test_steady_winner_passes_and_a_crash_fails():
    r = PropRules(eval_horizon_days=60, eval_step_days=5)
    up = prop_metrics(_series([0.004] * 400), r)               # +0.4% a day: +10% in about 24 days
    assert up["prop_pass_rate"] == 1.0 and 20 <= up["prop_median_days"] <= 30
    crash = [0.004] * 400
    crash[200] = -0.07                                          # one day beyond the 5% daily limit
    m = prop_metrics(_series(crash), r)
    assert 0 < m["prop_fail_daily_rate"] < 1 and m["prop_pass_rate"] < 1
    flat = prop_metrics(_series([0.0005] * 400), r)            # too slow to reach 10% in 60 days
    assert flat["prop_pass_rate"] == 0.0 and flat["prop_timeout_rate"] == 1.0
    bleed = prop_metrics(_series([-0.003] * 400), r)           # slow bleed: total loss limit
    assert bleed["prop_fail_total_rate"] == 1.0


def test_min_trading_days_and_trailing():
    r = PropRules(min_trading_days=4)
    st = forward_status([("d0", 100.0), ("d1", 112.0), ("d2", 112.0), ("d3", 112.0), ("d4", 112.0)], r)
    assert st["passed"] and st["days"] == 4                    # target on day one, but four days required
    trail = PropRules(trailing_total_loss=True)
    marks = [("d0", 100.0), ("d1", 104.0), ("d2", 108.0), ("d3", 104.0), ("d4", 99.0), ("d5", 97.0)]
    assert forward_status(marks, trail)["outcome"] == "failed_total"     # 10% below the 108 peak
    assert forward_status(marks, PropRules())["outcome"] == "open"       # static: 97 is above 90


def test_readiness_needs_every_check():
    r = PropRules(ready_min_forward_days=30, ready_min_backtest_pass_rate=0.5)
    good = {"days": 40, "breached": False, "passed": True, "ret": 0.11, "outcome": "passed"}
    assert readiness(forward=good, backtest_pass_rate=0.6, rules=r)["ready"]
    assert not readiness(forward={**good, "days": 10}, backtest_pass_rate=0.6, rules=r)["ready"]
    assert not readiness(forward={**good, "breached": True}, backtest_pass_rate=0.6, rules=r)["ready"]
    assert not readiness(forward=good, backtest_pass_rate=0.4, rules=r)["ready"]
    assert not readiness(forward=good, backtest_pass_rate=None, rules=r)["ready"]


def test_a_variant_that_breaches_is_retired(tmp_path):
    cfg = StrategyConfig()
    s = Settings(hive_state_dir=tmp_path, _env_file=None)
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = next(t for t in cfg.survival.tiers if t.name == "base")
    bad = reg.spawn(Child(id="", name="bad", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 6.0}), tier)
    ok = reg.spawn(Child(id="", name="ok", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 7.0}), tier)
    scout = reg.spawn(Child(id="trench-scout", name="trench", kind="scout", sleeve="trench"), tier, force=True)
    d0 = date(2026, 10, 1)
    for cid, path in ((bad.id, [100, 101, 94.5]), (ok.id, [100, 101, 99.5]), (scout.id, [100, 50, 40])):
        led = SurvivalLedger(tmp_path / "children" / cid / "ledger.jsonl", cfg.survival)
        for i, v in enumerate(path):
            led.append("equity_mark", float(v), {"label": cid, "date": (d0 + timedelta(days=i)).isoformat()})
    out = retire_prop_failures(s, cfg, reg, d0 + timedelta(days=3))
    assert [r["id"] for r in out] == [bad.id] and "daily limit breached" in out[0]["verdict"]
    assert reg.get(bad.id).status == "retired" and reg.get(ok.id).status == "shadow"
    assert reg.get(scout.id).status == "shadow"                 # scouts are judged on their own book
