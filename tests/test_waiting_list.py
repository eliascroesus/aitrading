"""Cells that passed without a seat are seated when one frees; scouts take no seat; forming session bars are dropped."""

from datetime import UTC, date, datetime

import pandas as pd

from hive.config import StrategyConfig
from hive.data.refresh import complete_sessions
from hive.hive.registry import Child, HiveRegistry
from hive.operator.sweep import seat_waiting


def _tier(cfg, name):
    return next(t for t in cfg.survival.tiers if t.name == name)


def test_waiting_list_is_seated_when_a_seat_frees(tmp_path):
    cfg = StrategyConfig()
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = _tier(cfg, "survival")   # two seats (config 1.5.0: base has four)
    reg.spawn(Child(id="", name="scout", kind="scout", sleeve="meme"), tier)          # no seat
    a = reg.spawn(Child(id="", name="a", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 6}), tier)
    reg.spawn(Child(id="", name="b", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 7}), tier)
    assert not reg.can_spawn(tier)[0]
    reg.record_iteration({"date": "2026-10-19", "proposal": "sweep stocks: entry_mode=ma_pullback",
                          "overrides": {"stocks.entry_mode": "ma_pullback"}, "sleeve": "stocks",
                          "oos_cagr": 0.069, "parent_oos_cagr": 0.028, "oos_dd": -0.135,
                          "verdict": "passed but not spawned: tier base allows 2 children; 2 active"})
    assert seat_waiting(reg, tier, date(2026, 10, 20)) == []                          # still full
    reg.retire(a.id, "loser")
    out = seat_waiting(reg, tier, date(2026, 10, 21))
    assert len(out) == 1 and out[0]["kind"] == "seated"
    assert any(c.overrides == {"stocks.entry_mode": "ma_pullback"} and c.status == "shadow" for c in reg.children)
    assert seat_waiting(reg, tier, date(2026, 10, 22)) == []                          # never seated twice


def test_forming_us_session_bar_is_dropped_until_the_close():
    today = pd.Timestamp(datetime.now(UTC).date())
    idx = pd.date_range(today - pd.Timedelta(days=2), today, freq="D")
    bars = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx)
    noon = datetime.now(UTC).replace(hour=15, minute=0)
    after = datetime.now(UTC).replace(hour=22, minute=0)
    assert len(complete_sessions(bars, now=noon)) == 2
    assert len(complete_sessions(bars, now=after)) == 3
