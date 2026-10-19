"""Monitors, the daily sweep, promotion into the primary, and the research bonus."""

import json
from datetime import date

from hive.config import Settings, StrategyConfig
from hive.hive.monitors import fetch_items, latest_desk_reports, run_monitor_child
from hive.hive.registry import Child, HiveRegistry
from hive.operator.sweep import grid, untested
from hive.orchestrator.app import apply_promoted
from hive.orchestrator.journal import Journal
from hive.survival.ledger import SurvivalLedger


def _tier(cfg, name):
    return next(t for t in cfg.survival.tiers if t.name == name)


def test_monitor_without_llm_skips_and_desk_reports_are_bounded(tmp_path):
    cfg = StrategyConfig()
    s = Settings(hive_mode="paper", hive_data_dir=tmp_path, hive_state_dir=tmp_path / "state",
                 hive_reports_dir=tmp_path / "reports", hive_social_data=False, _env_file=None)
    reg = HiveRegistry(tmp_path / "state" / "hive.json", cfg.survival)
    m = reg.spawn(Child(id="", name="fed watch", kind="monitor", sleeve="all", watch=["Fed rates", "SOL"]),
                  _tier(cfg, "base"))
    cdir = tmp_path / "state" / "children" / m.id
    j = Journal(tmp_path / "state" / "journal", "2026-10-19-child-x")
    assert run_monitor_child(s, m, cdir, j, None, date(2026, 10, 19), {"SOL": "crypto"}, None) == []
    assert fetch_items("SOL", {"SOL": "crypto"}, lambda sym: [("a", "SOL rallies")], s) == [("a", "SOL rallies")]
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "latest.json").write_text(json.dumps({"date": "2026-10-18", "child": m.id, "reports": [
        {"query": "Fed rates", "n_items": 9, "net_sentiment": -0.3, "material_negative": False,
         "attention_spike": True, "event_types": ["macro"], "cost_usd": 0.002, "raw": "never forwarded"}]}))
    reps = latest_desk_reports(s, reg)
    assert reps[0]["monitor"] == "fed watch" and "raw" not in reps[0]["reports"][0]
    assert reps[0]["reports"][0]["attention_spike"] is True
    # monitors expire
    m.born = "2026-09-01"
    reg.save()
    assert [c.id for c in reg.retire_expired_monitors(28, on=date(2026, 10, 19))] == [m.id]


def test_sweep_grid_is_inside_bounds_and_skips_tested_cells(tmp_path):
    from hive.config import REPO_ROOT
    from hive.operator.iterate import load_bounds, validate_overrides

    bounds = load_bounds(REPO_ROOT / "config" / "bounds.toml")
    cells = grid()
    assert len(cells) > 20
    for sleeve, ov in cells:
        ok, why = validate_overrides(ov, bounds)
        assert ok, (sleeve, ov, why)
    reg = HiveRegistry(tmp_path / "hive.json", StrategyConfig().survival)
    sleeve, ov = cells[0]
    reg.record_iteration({"date": "2026-10-19", "proposal": "x", "overrides": ov, "sleeve": sleeve, "verdict": "rejected"})
    assert cells[0] not in untested(reg, cells) and len(untested(reg, cells)) == len(cells) - 1


def test_promoted_overrides_reach_the_primary(tmp_path):
    cfg = StrategyConfig()
    s = Settings(hive_state_dir=tmp_path, _env_file=None)
    assert apply_promoted(cfg, s).stocks.trail_atr == cfg.stocks.trail_atr
    (tmp_path / "promoted_overrides.json").write_text(json.dumps({"overrides": {"stocks.trail_atr": 7.0,
                                                                                "stocks.entry_mode": "high_breakout"}}))
    out = apply_promoted(cfg, s)
    assert out.stocks.trail_atr == 7.0 and out.stocks.entry_mode == "high_breakout"
    assert cfg.stocks.trail_atr == 5.0  # the pre-registered object is untouched


def test_research_bonus_raises_the_pot(tmp_path):
    cfg = StrategyConfig()
    led = SurvivalLedger(tmp_path / "ledger.jsonl", cfg.survival)
    led.append("equity_mark", 100.0, {"label": "primary", "date": "2026-10-18"})
    on = date(2026, 10, 19)
    st = led.status(100.0, on=on)
    tier = led.tier(st.equity_net)
    base = led.research_budget(st.equity_net, tier, on)
    assert led.research_budget(st.equity_net, tier, on, bonus=1.5) > base
    assert led.research_budget(st.equity_net, tier, on, bonus=0.5) == base   # never below 1x
