"""Promotion and demotion by evidence, no person in the loop."""

from datetime import date

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
