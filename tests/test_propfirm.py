"""Prop-firm evaluations: the simulator, forward status, punishment and the readiness checklist."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from hive.config import PropRules, Settings, StrategyConfig
from hive.hive.children import retire_prop_failures
from hive.hive.propfirm import (
    forward_status,
    notice,
    prop_metrics,
    readiness,
    readiness_report,
    time_weighted,
)
from hive.hive.registry import Child, HiveRegistry
from hive.survival.ledger import SurvivalLedger


def _series(rets, start=100.0):
    return pd.Series(start * np.cumprod(1 + np.asarray(rets, dtype=float)),
                     index=pd.date_range("2020-01-01", periods=len(rets), freq="D"))


ONE_PHASE = {"profit_target_pct": 0.10, "phase2_target_pct": 0.0, "min_trading_days": 4}


def test_steady_winner_passes_and_a_crash_fails():
    r = PropRules(eval_horizon_days=60, eval_step_days=5, **ONE_PHASE)
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
    r = PropRules(**ONE_PHASE)
    st = forward_status([("d0", 100.0), ("d1", 112.0), ("d2", 112.0), ("d3", 112.0), ("d4", 112.0)], r)
    assert st["passed"] and st["days"] == 4                    # target on day one, but four days required
    trail = PropRules(trailing_total_loss=True, **ONE_PHASE)
    marks = [("d0", 100.0), ("d1", 104.0), ("d2", 108.0), ("d3", 104.0), ("d4", 99.0), ("d5", 97.0)]
    assert forward_status(marks, trail)["outcome"] == "failed_total"     # 10% below the 108 peak
    assert forward_status(marks, PropRules(**ONE_PHASE))["outcome"] == "open"   # static: 97 is above 90


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


def test_a_breach_after_passing_still_counts_and_deposits_are_not_gains():
    r = PropRules(**ONE_PHASE)
    marks = [("d0", 100.0), ("d1", 103.0), ("d2", 106.0), ("d3", 109.0), ("d4", 111.0), ("d5", 103.0)]
    st = forward_status(marks, r)
    assert st["passed"] and st["passed_on"] == "d4"
    assert st["breached"] and st["outcome"] == "failed_daily" and st["breach_day"] == 5   # a funded account would close
    deposit = [("2026-10-01", 100.0), ("2026-10-02", 101.0), ("2026-10-03", 202.0)]  # +$100 deposited on the 3rd
    tw = time_weighted(deposit, [("2026-10-03", 100.0)])
    assert tw[-1][1] == pytest.approx(101.0 * 102.0 / 101.0)                         # +1%, then +0.99%: no +100% day
    assert time_weighted(deposit, []) == deposit


def test_the_readiness_report_and_the_announcement(tmp_path):
    cfg = StrategyConfig()
    s = Settings(hive_state_dir=tmp_path, _env_file=None)
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = next(t for t in cfg.survival.tiers if t.name == "base")
    v = reg.spawn(Child(id="", name="v", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 6.0}), tier)
    d0 = date(2026, 9, 1)
    main = SurvivalLedger(tmp_path / "ledger.jsonl", cfg.survival)
    for i in range(36):                                          # +0.4% a day for 35 days, never a bad day
        main.append("equity_mark", 100.0 * 1.004 ** i, {"date": (d0 + timedelta(days=i)).isoformat()})
    child = SurvivalLedger(tmp_path / "children" / v.id / "ledger.jsonl", cfg.survival)
    for i, x in enumerate([100.0, 101.0, 102.0]):
        child.append("equity_mark", x, {"date": (d0 + timedelta(days=i)).isoformat()})
    lab = [{"by": "parent", "sleeve": "stocks", "oos_prop": 0.62, "ts": "2026-10-01T00:00"},
           {"by": "parent", "sleeve": "crypto", "oos_prop": 0.55, "ts": "2026-10-01T00:00"},
           {"by": "parent", "sleeve": "crypto", "oos_prop": 0.40, "ts": "2026-09-01T00:00"},     # older: ignored
           {"by": "sweep", "sleeve": "stocks", "overrides": {"stocks.trail_atr": 6.0}, "oos_prop": 0.7, "ts": "2026-10-02"}]
    rep = readiness_report(s, cfg, reg, lab)
    primary = next(b for b in rep["books"] if b["kind"] == "primary")
    assert primary["ready"] and primary["backtest_pass_rate"] == 0.55          # the weaker sleeve
    assert rep["ready"] == ["primary@start"]
    var = next(b for b in rep["books"] if b["id"] == v.id)
    assert not var["ready"] and var["backtest_pass_rate"] == 0.7 and var["forward"]["days"] == 2
    body = notice(rep, rep["ready"])
    assert "the firm (primary book)" in body and "- [x]" in body and "no connection to any prop firm" in body
    assert notice(rep, []).count("###") == 2                                    # rules and next steps only


def test_a_two_phase_challenge_passes_only_when_both_phases_do():
    r = PropRules(profit_target_pct=0.08, phase2_target_pct=0.05, min_trading_days=5)
    # +2% a day: phase 1 (8%) needs five trading days, then phase 2 (5% from the new balance) five more
    marks = [(f"d{i}", 100.0 * 1.02 ** i) for i in range(12)]
    st = forward_status(marks, r)
    assert st["passed"] and st["passed_on"] == "d10"
    # the same start, but phase 2 breaks the daily limit: the challenge fails
    bad = marks[:6] + [("d6", marks[5][1] * 0.94)] + [(f"d{i}", marks[5][1] * 0.94) for i in range(7, 12)]
    st = forward_status(bad, r)
    assert not st["passed"] and st["outcome"] == "failed_daily"
    one = prop_metrics(_series([0.02] * 200), PropRules(eval_horizon_days=30, eval_step_days=5, **ONE_PHASE))
    two = prop_metrics(_series([0.02] * 200), PropRules(eval_horizon_days=30, eval_step_days=5,
                                                         profit_target_pct=0.08, phase2_target_pct=0.05,
                                                         min_trading_days=5))
    assert one["prop_median_days"] == 5 and two["prop_median_days"] == 10

