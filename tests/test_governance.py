"""Promotion and demotion by evidence, no person in the loop."""

from datetime import date, timedelta

from hive.config import Settings, StrategyConfig
from hive.hive.governance import (
    BASELINE_ID,
    demote_if_baseline_wins,
    ensure_baseline,
    promote_earned,
    read_promoted,
)
from hive.hive.registry import Child, HiveRegistry
from hive.orchestrator.app import apply_promoted


def _tier(cfg, name):
    return next(t for t in cfg.survival.tiers if t.name == name)


def test_operator_promotes_an_earned_variant_and_the_baseline_guards_it(tmp_path):
    cfg = StrategyConfig()
    s = Settings(hive_state_dir=tmp_path, _env_file=None)
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = _tier(cfg, "base")
    c = reg.spawn(Child(id="", name="cross", kind="variant", sleeve="stocks", overrides={"stocks.entry_mode": "template_cross"}), tier)
    for i in range(cfg.survival.child_shadow_days):
        reg.mark(c.id, 100 + 2 * i, 100 + i, on=date(2026, 10, 1))      # beats the parent
    assert promote_earned(s, cfg, reg, tier, date(2026, 10, 19))[0]["kind"] == "promotion"
    assert read_promoted(s)["overrides"] == {"stocks.entry_mode": "template_cross"}
    assert apply_promoted(cfg, s).stocks.entry_mode == "template_cross"
    again = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    assert again.get(c.id).status == "promoted"
    base = again.get(BASELINE_ID)
    assert base.kind == "baseline" and base.status == "shadow" and base.shadow_days == 0
    assert again.can_spawn(tier)[0]                                       # the baseline takes no seat
    # the baseline wins back by more than the lag over the demotion window: revert
    for i in range(cfg.operator.demote_after_days):
        again.mark(BASELINE_ID, 100 + 1.0 * i, 100 + 0.5 * i, on=date(2026, 11, 1))
    rec = demote_if_baseline_wins(s, cfg, again, date(2026, 11, 20))
    assert rec and rec["kind"] == "demotion"
    assert read_promoted(s)["overrides"] == {} and read_promoted(s)["history"][-1]["event"] == "reverted"
    assert apply_promoted(cfg, s).stocks.entry_mode == "reset_breakout"
    assert again.get(BASELINE_ID).shadow_days == 0                        # the comparison restarts


def test_no_promotion_below_the_evidence_bar(tmp_path):
    cfg = StrategyConfig()
    s = Settings(hive_state_dir=tmp_path, _env_file=None)
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = _tier(cfg, "base")
    c = reg.spawn(Child(id="", name="meh", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 6}), tier)
    for i in range(cfg.survival.child_shadow_days):
        reg.mark(c.id, 100 + 0.1 * i, 100 + 0.09 * i)                     # ahead, but under promote_min_voi
    assert promote_earned(s, cfg, reg, tier, date(2026, 10, 19)) == []
    assert not (tmp_path / "promoted_overrides.json").exists()
    assert ensure_baseline(reg, tier, date(2026, 10, 19)).id == BASELINE_ID
    assert demote_if_baseline_wins(s, cfg, reg, date(2026, 10, 19)) is None


def test_punishment_is_quick_drawdown_death_and_voi_retirement(tmp_path):
    """Config 1.5.0: a child whose own paper book is down 15% dies at once; one behind the
    parent by 3% after 10 days retires; the baseline is never retired."""
    cfg = StrategyConfig()
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = _tier(cfg, "base")
    dd = reg.spawn(Child(id="", name="dd", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 6.0}), tier)
    lag = reg.spawn(Child(id="", name="lag", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 7.0}), tier)
    base = reg.spawn(Child(id="", name="baseline", kind="baseline", sleeve="stocks"), tier, force=True)
    reg.mark(dd.id, 100.0, 100.0, on=date(2026, 10, 1))
    reg.mark(dd.id, 84.0, 101.0, on=date(2026, 10, 2))           # down 16% from its start after two days
    for i in range(cfg.survival.child_retire_days):
        reg.mark(lag.id, 100.0 - 0.5 * i, 100.0, on=date(2026, 10, 1) + timedelta(days=i))   # 4.5% behind
        reg.mark(base.id, 80.0, 100.0, on=date(2026, 10, 1) + timedelta(days=i))
    out = reg.auto_retire_losers(min_days=cfg.survival.child_retire_days, max_negative_voi=cfg.survival.child_retire_voi,
                                 max_drawdown=cfg.survival.child_max_drawdown_pct, on=date(2026, 10, 11))
    assert {c.id for c in out} == {dd.id, lag.id}
    assert reg.get(dd.id).status == "retired" and "dead" in (reg.get(dd.id).notes or [""])[-1]
    assert reg.get(base.id).status == "shadow"
