"""The strategy lab: every backtest is recorded with its curves, the search refines around
the best cells inside the bounds, never retests a cell, and stops on its time budget."""

import json
from datetime import date

from hive.backtest.costs import ALPACA_STOCKS
from hive.config import REPO_ROOT, StrategyConfig
from hive.hive.propfirm import rules_key
from hive.hive.registry import HiveRegistry
from hive.operator.iterate import evaluate, load_bounds, validate_overrides
from hive.operator.lab import (
    cell_key,
    data_fingerprint,
    downsample,
    lab_record,
    load_parent,
    parent_eval,
    parent_key,
    read_tests,
    record_test,
    recorded_keys,
    summary,
)
from hive.operator.sweep import grid, queue, refine, results_so_far, run_sweep, untested
from tests.test_features import bars_from_path, textbook_setup


def _tier(cfg, name):
    return next(t for t in cfg.survival.tiers if t.name == name)


def _cfg_and_inputs():
    close, volume = textbook_setup(n_after=400)
    bars = bars_from_path(close, volume, start="2010-01-01")
    cfg = StrategyConfig()
    cfg.operator.insample_start, cfg.operator.insample_end = "2010-01-01", "2011-12-31"
    cfg.operator.outofsample_start, cfg.operator.outofsample_end = "2012-01-01", None
    inputs = {"bars": {"AAA": bars}, "classes": {"AAA": "stock"}, "bench": bars, "cost": ALPACA_STOCKS}
    return cfg, inputs


def test_evaluate_keeps_downsampled_curves_with_benchmark():
    cfg, inputs = _cfg_and_inputs()
    ev = evaluate(cfg, {}, inputs, keep_curves=True)
    for win in ("insample", "outofsample"):
        cv = ev.curves[win]
        assert 2 <= len(cv["dates"]) <= 160 and len(cv["equity"]) == len(cv["dates"]) == len(cv["benchmark"])
        assert cv["dates"] == sorted(cv["dates"])
        assert abs(cv["benchmark"][0] - cfg.survival.starting_bankroll_usd) < 1e-6
    assert evaluate(cfg, {}, inputs).curves == {}


def test_downsample_keeps_ends_and_bounds_points():
    import pandas as pd

    s = pd.Series(range(1000), index=pd.bdate_range("2010-01-01", periods=1000), dtype=float)
    d = downsample(s, 50)
    assert len(d["dates"]) == 50 and d["equity"][0] == 0.0 and d["equity"][-1] == 999.0


def test_records_are_append_only_and_dedup_the_search(tmp_path):
    ldir = tmp_path / "lab"
    rec = lab_record(by="sweep", sleeve="stocks", name="x", overrides={"stocks.trail_atr": 3.0}, ev=None,
                      parent=None, verdict="rejected: no", on="2026-10-19")
    p1 = record_test(ldir, dict(rec))
    p2 = record_test(ldir, dict(rec))
    assert p1 != p2 and len(read_tests(ldir)) == 2
    assert recorded_keys(ldir) == {cell_key("stocks", {"stocks.trail_atr": 3.0})}
    assert rec["passed"] is False
    assert lab_record(by="sweep", sleeve="stocks", name="y", overrides={}, ev=None, parent=None,
                       verdict="passed but not spawned: full", on="2026-10-19")["passed"] is True
    assert lab_record(by="parent", sleeve="stocks", name="p", overrides={}, ev=None, parent=None,
                       verdict="reference", on="2026-10-19")["passed"] is None
    reg = HiveRegistry(tmp_path / "hive.json", StrategyConfig().survival)
    cells = [("stocks", {"stocks.trail_atr": 3.0}), ("stocks", {"stocks.trail_atr": 7.0}), ("stocks", {"stocks.trail_atr": 7.0})]
    assert untested(reg, cells, ldir) == [("stocks", {"stocks.trail_atr": 7.0})]   # lab-tested and duplicates dropped


def test_parent_is_cached_per_data_snapshot(tmp_path):
    cfg, inputs = _cfg_and_inputs()
    ldir = tmp_path / "lab"
    a = parent_eval(cfg, "stocks", inputs, ldir, on="2026-10-19")
    assert a.curves and (ldir / "parent-stocks.json").exists()
    assert [t["by"] for t in read_tests(ldir)] == ["parent"]
    b = parent_eval(cfg, "stocks", inputs, ldir, on="2026-10-20")
    assert b.outofsample == a.outofsample and len(read_tests(ldir)) == 1      # cache hit: no new record
    key = parent_key(cfg, "stocks", data_fingerprint(inputs))
    assert load_parent(ldir, "stocks", key) is not None
    assert load_parent(ldir, "stocks", key + "x") is None                      # a new snapshot recomputes


def test_refine_stays_in_bounds_typed_and_family_aware():
    cfg = StrategyConfig()
    bounds = load_bounds(REPO_ROOT / "config" / "bounds.toml")
    results = [
        {"sleeve": "stocks", "overrides": {"stocks.entry_mode": "template_cross"}, "oos_cagr": 0.09, "oos_dd": -0.17, "verdict": "spawned"},
        {"sleeve": "stocks", "overrides": {"stocks.entry_mode": "ma_pullback"}, "oos_cagr": 0.07, "oos_dd": -0.13, "verdict": "passed"},
        {"sleeve": "stocks", "overrides": {"stocks.exit_closes_below_mid": 3}, "oos_cagr": 0.04, "oos_dd": -0.2, "verdict": "rejected"},
        {"sleeve": "stocks", "overrides": {"stocks.trail_atr": 9.0}, "oos_cagr": 0.2, "oos_dd": -0.1, "verdict": "rejected: backtest failed: x"},
        {"sleeve": "crypto", "overrides": {"crypto.entry_mode": "high_breakout"}, "oos_cagr": 0.3, "oos_dd": -0.4, "verdict": "rejected"},
    ]
    cells = refine(results, bounds, cfg)
    assert cells
    keys = set()
    for sleeve, ov in cells:
        ok, why = validate_overrides(ov, bounds)
        assert ok, (ov, why)
        assert all(k.startswith((sleeve + ".", "risk.")) for k in ov)
        keys.add(cell_key(sleeve, ov))
        for k, v in ov.items():
            if k.endswith(("_bars", "_lookback", "_window", "_period", "exit_closes_below_mid", "max_hold_bars", "review_bars")):
                assert isinstance(v, int), (k, v)
    assert len(keys) == len(cells)                                        # no duplicates
    # family-aware: the template_cross winner is not refined on the breakout lookback,
    # the ma_pullback winner is refined on its touch bars, and failed cells are never a base
    assert not any(ov.get("stocks.entry_mode") == "template_cross" and "stocks.breakout_lookback" in ov for _, ov in cells)
    assert any(ov.get("stocks.entry_mode") == "ma_pullback" and "stocks.pullback_touch_bars" in ov for _, ov in cells)
    assert not any(ov.get("stocks.trail_atr") == 9.0 and len(ov) > 1 for _, ov in cells)
    # the two best stocks cells with disjoint knobs are merged
    assert ("stocks", {"stocks.entry_mode": "template_cross", "stocks.exit_closes_below_mid": 3}) in cells
    # crypto winner refined on its own knobs only
    assert any(s == "crypto" and "crypto.breakout_lookback" in ov for s, ov in cells)
    assert refine([], bounds, cfg) == []


def test_queue_interleaves_sleeves_and_grid_comes_first(tmp_path):
    cfg = StrategyConfig()
    bounds = load_bounds(REPO_ROOT / "config" / "bounds.toml")
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    from hive.operator.sweep import queue_staged

    q = queue(reg, cfg, bounds, tmp_path / "lab")
    assert q[0][0] == "stocks" and q[1][0] == "crypto"
    staged = queue_staged(reg, cfg, bounds, tmp_path / "lab")
    stages = [st for _, _, st in staged]
    for sleeve in ("stocks", "crypto"):                                 # per sleeve, the grid comes first ...
        mine = [st for sl, _, st in staged if sl == sleeve]
        n = sum(1 for sl, _ in grid() if sl == sleeve)
        assert mine[:n] == ["sweep"] * n and "sweep" not in mine[n:]
    assert "evolve" in stages and len(q) > len(grid())                  # ... and the search never runs dry
    assert all(c in q for c in grid())
    reg.record_iteration({"date": "2026-10-19", "proposal": "x", "overrides": {"stocks.entry_mode": "template_cross"},
                          "sleeve": "stocks", "oos_cagr": 0.09, "oos_dd": -0.17, "verdict": "spawned"})
    staged2 = queue_staged(reg, cfg, bounds, tmp_path / "lab")
    cell = ("stocks", {"stocks.entry_mode": "template_cross"})
    # tested before prop scoring existed: re-tested once, first, to get its prop score
    assert [(sl, ov) for sl, ov, st in staged2 if st == "rescore"] == [cell]
    assert sum(1 for sl, ov, _ in staged2 if (sl, ov) == cell) == 1
    reg.record_iteration({"date": "2026-10-20", "proposal": "x", "overrides": {"stocks.entry_mode": "template_cross"},
                          "sleeve": "stocks", "oos_cagr": 0.09, "oos_dd": -0.17, "oos_prop": 0.2, "oos_score": 0.15,
                          "prop_key": rules_key(cfg.prop), "verdict": "spawned"})
    q2 = queue(reg, cfg, bounds, tmp_path / "lab")
    assert len(q2) > len(grid()) and cell not in q2                    # scored: never again
    reg.record_iteration({"date": "2026-10-21", "proposal": "x", "overrides": {"stocks.entry_mode": "template_cross"},
                          "sleeve": "stocks", "oos_cagr": 0.09, "oos_dd": -0.17, "oos_prop": 0.2, "oos_score": 0.15,
                          "prop_key": "old-rules", "verdict": "spawned"})
    assert cell in [(sl, ov) for sl, ov, st in queue_staged(reg, cfg, bounds, tmp_path / "lab") if st == "rescore"]
    reg.record_iteration({"date": "2026-10-21", "proposal": "x", "overrides": {"stocks.entry_mode": "template_cross"},
                          "sleeve": "stocks", "oos_cagr": 0.09, "oos_dd": -0.17, "oos_prop": 0.2, "oos_score": 0.15,
                          "prop_key": rules_key(cfg.prop), "verdict": "spawned"})   # rescored under today's rules
    assert len(results_so_far(reg, tmp_path / "lab")) == 1
    # a cell with exactly the same results differs only in a knob that does nothing: never rescored
    reg.record_iteration({"date": "2026-10-20", "proposal": "y", "sleeve": "stocks", "oos_cagr": 0.09, "oos_dd": -0.17,
                          "overrides": {"stocks.entry_mode": "template_cross", "risk.halt_cooldown_bars": 11},
                          "verdict": "spawned"})
    assert not [1 for _, _, st in queue_staged(reg, cfg, bounds, tmp_path / "lab") if st == "rescore"]


def test_run_sweep_records_every_cell_with_curves_and_honours_the_budget(tmp_path):
    cfg, inputs = _cfg_and_inputs()
    ldir = tmp_path / "lab"
    reg = HiveRegistry(tmp_path / "state" / "hive.json", cfg.survival)
    recs = run_sweep(None, cfg, reg, _tier(cfg, "hive"), n_cells=2, on=date(2026, 10, 19),
                     inputs_by_sleeve={"stocks": inputs, "crypto": inputs}, ldir=ldir)
    assert len(recs) == 2 and all(r["kind"] == "sweep" and r["stage"] == "sweep" for r in recs)
    tests = read_tests(ldir)
    # the sleeves interleave: parent stocks, a stocks cell, parent crypto, a crypto cell
    assert [t["by"] for t in tests] == ["parent", "sweep", "parent", "sweep"] and len(tests) == 4
    assert [t["sleeve"] for t in tests] == ["stocks", "stocks", "crypto", "crypto"]
    for t in [x for x in tests if x["by"] != "parent"]:
        assert t["overrides"] and "oos_cagr" in t and t["curves"]["outofsample"]["dates"]
        assert t["parent_oos_cagr"] is not None and t["elapsed_s"] is not None and t["data"]["n_symbols"] == 1
    assert (tmp_path / "state" / "research_log.jsonl").exists()
    sm = summary(ldir, today="2026-10-19")
    assert sm["n_tests"] == 2 and sm["last_ts"]
    # a zero budget runs nothing new; the parent cache means no new parent record either
    again = run_sweep(None, cfg, reg, _tier(cfg, "hive"), budget_s=0.0, on=date(2026, 10, 19),
                      inputs_by_sleeve={"stocks": inputs, "crypto": inputs}, ldir=ldir)
    assert again == [] and len(read_tests(ldir)) == 4
    # the next run continues the queue: never the same cells again
    more = run_sweep(None, cfg, reg, _tier(cfg, "hive"), n_cells=1, on=date(2026, 10, 19),
                     inputs_by_sleeve={"stocks": inputs, "crypto": inputs}, ldir=ldir)
    keys = [cell_key(t["sleeve"], t["overrides"]) for t in read_tests(ldir) if t["overrides"]]
    assert len(more) == 1 and len(keys) == len(set(keys)) == 3


def test_dashboard_collects_the_lab(tmp_path):
    from hive.config import Settings
    from hive.ui.collect import collect

    cfg, inputs = _cfg_and_inputs()
    s = Settings(hive_mode="paper", hive_data_dir=tmp_path, hive_state_dir=tmp_path / "state",
                 hive_reports_dir=tmp_path / "reports", _env_file=None)
    reg = HiveRegistry(tmp_path / "state" / "hive.json", cfg.survival)
    run_sweep(s, cfg, reg, _tier(cfg, "hive"), n_cells=2, on=date(2026, 10, 19),
              inputs_by_sleeve={"stocks": inputs, "crypto": inputs})
    d = collect(s, cfg)["lab"]
    assert d["n_tests"] == 2 and len(d["tests"]) == 4 and d["tests"][0]["ts"] >= d["tests"][-1]["ts"]
    assert "stocks" in d["parents"] and d["parents"]["stocks"]["curves"]["outofsample"]["benchmark"]
    assert all(t["id"] in d["curves"] for t in d["tests"] if t["by"] != "parent")
    assert d["n_queued"] > 0 and d["queue"] and d["grid_size"] == len(grid())
    assert any(e["who"] == "lab" for e in d["timeline"])
    json.dumps(d)   # serialisable


def test_waiting_list_is_best_first_from_the_complete_record(tmp_path):
    from hive.operator.sweep import seat_waiting, waiting_list

    cfg = StrategyConfig()
    ldir = tmp_path / "lab"
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    # three cells passed without a seat: two only in the lab's record (older than the registry
    # window), one in the registry; the best out-of-sample return must be seated first
    for name, ov, oos in (("lab weaker", {"stocks.trail_atr": 3.0}, 0.04), ("lab best", {"crypto.entry_mode": "high_breakout"}, 0.22)):
        record_test(ldir, lab_record(by="sweep", sleeve=("crypto" if "crypto" in str(ov) else "stocks"),
                                     name=name, overrides=ov, ev=None, parent=None,
                                     verdict="passed but not spawned: tier base allows 2 children; 2 active",
                                     on="2026-10-18") | {"oos_cagr": oos, "oos_dd": -0.2})
    reg.record_iteration({"date": "2026-10-19", "proposal": "registry middle", "overrides": {"stocks.trail_atr": 7.0},
                          "sleeve": "stocks", "oos_cagr": 0.10, "oos_dd": -0.15,
                          "verdict": "passed but not spawned: tier base allows 2 children; 2 active"})
    names = [w["proposal"] for w in waiting_list(reg, ldir)]
    assert names == ["lab best", "registry middle", "lab weaker"]
    from hive.hive.registry import Child

    reg.spawn(Child(id="", name="incumbent", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 9.0}),
              _tier(cfg, "survival"))                                                # one of two seats taken
    out = seat_waiting(reg, _tier(cfg, "survival"), date(2026, 10, 19), ldir)     # one seat free
    assert len(out) == 1 and "lab best" in out[0]["proposal"] and len(reg.active()) == 2
    assert [w["proposal"] for w in waiting_list(reg, ldir)] == ["registry middle", "lab weaker"]
    assert any(t["by"] == "seat" for t in read_tests(ldir))
    assert summary(ldir)["n_tests"] == 2                                            # seat records are not tests
    assert seat_waiting(reg, _tier(cfg, "survival"), date(2026, 10, 19), ldir) == []  # no free seat: nothing


def test_lab_status_file_reaches_the_dashboard(tmp_path):
    from hive.config import Settings
    from hive.operator.lab import read_status, write_status
    from hive.ui.collect import collect

    s = Settings(hive_mode="paper", hive_data_dir=tmp_path, hive_state_dir=tmp_path / "state",
                 hive_reports_dir=tmp_path / "reports", _env_file=None)
    assert read_status(tmp_path / "state") == {}
    write_status(tmp_path / "state", {"state": "done", "round": 2, "rounds": 5, "cells": 9, "queued": 120,
                                      "started": "2026-10-19T23:07:00+00:00", "finished": "2026-10-19T23:15:30+00:00"})
    st = read_status(tmp_path / "state")
    assert st["round"] == 2 and st["cells"] == 9
    assert collect(s, StrategyConfig())["lab"]["status"]["queued"] == 120


def test_sizing_levers_are_in_the_space_for_both_sleeves():
    """Config 2.0.0: the risk.* levers are searched by the grid and the refinement, each sleeve
    getting only the levers that apply to it; leverage is not in the space."""
    cfg = StrategyConfig()
    bounds = load_bounds(REPO_ROOT / "config" / "bounds.toml")
    assert "risk.risk_per_trade_pct" in bounds and "stocks.direction" in bounds
    assert "risk.max_drawdown_halt_pct" not in bounds          # 2.3.0: the halt is for real money only
    assert "risk.max_leverage" not in bounds and "risk.crypto_max_leverage" not in bounds
    cells = grid()
    for sleeve, ov in cells:
        ok, why = validate_overrides(ov, bounds)
        assert ok, (sleeve, ov, why)
    assert ("stocks", {"risk.max_position_pct_stock": 0.5}) in cells
    assert ("crypto", {"risk.crypto_sleeve_pct": 1.0}) in cells
    assert not any(s == "crypto" and "risk.max_position_pct_stock" in ov for s, ov in cells)
    results = [{"sleeve": "stocks", "overrides": {"stocks.entry_mode": "template_cross"}, "oos_cagr": 0.09,
                "oos_dd": -0.17, "verdict": "spawned"},
               {"sleeve": "crypto", "overrides": {"crypto.entry_mode": "high_breakout"}, "oos_cagr": 0.2,
                "oos_dd": -0.3, "verdict": "spawned"}]
    ref = refine(results, bounds, cfg)
    assert any(s == "stocks" and "risk.risk_per_trade_pct" in ov for s, ov in ref)
    assert any(s == "crypto" and "risk.max_position_pct_crypto" in ov for s, ov in ref)
    assert not any(s == "stocks" and "risk.max_position_pct_crypto" in ov for s, ov in ref)
    assert not any(s == "crypto" and "risk.max_position_pct_stock" in ov for s, ov in ref)
    for sleeve, ov in ref:
        ok, why = validate_overrides(ov, bounds)
        assert ok, (sleeve, ov, why)
        assert isinstance(ov.get("risk.max_positions", 3), int)


def test_storage_is_compact_and_rolls_up_finished_days(tmp_path):
    """Curves share a calendar; rejected cells keep a lean out-of-sample curve; a finished UTC day
    rolls into day files and reads back identically, curves included."""
    from hive.operator.lab import CAL_DIR, DAYS_DIR, load_curves, rollup

    cfg, inputs = _cfg_and_inputs()
    ldir = tmp_path / "lab"
    ev = evaluate(cfg, {"stocks.trail_atr": 7.0}, inputs, keep_curves=True)
    rej = lab_record(by="sweep", sleeve="stocks", name="rejected", overrides={"stocks.trail_atr": 7.0}, ev=ev,
                     parent=None, verdict="rejected: no", on="2026-10-18") | {"ts": "2026-10-18T10:00:00+00:00"}
    win = lab_record(by="sweep", sleeve="stocks", name="winner", overrides={"stocks.trail_atr": 6.0}, ev=ev,
                     parent=None, verdict="spawned shadow child x: grows", on="2026-10-18") | {"ts": "2026-10-18T11:00:00+00:00"}
    today = lab_record(by="sweep", sleeve="crypto", name="today", overrides={"crypto.trail_atr": 6.0}, ev=ev,
                       parent=None, verdict="rejected: no", on="2026-10-19") | {"ts": "2026-10-19T09:00:00+00:00"}
    for r in (rej, win, today):
        record_test(ldir, r)
    raw = json.loads(next(p for p in ldir.glob("*.json") if "today" not in p.read_text() and "rejected" in p.read_text()).read_text())
    assert set(raw["curves"]) == {"outofsample"} and set(raw["curves"]["outofsample"]) == {"cal", "equity"}
    assert len(raw["curves"]["outofsample"]["equity"]) <= 100
    assert len(list((ldir / CAL_DIR).glob("*.json"))) >= 2              # lean and full calendars
    before = read_tests(ldir)
    assert rollup(ldir, today="2026-10-19") == 2 and rollup(ldir, today="2026-10-19") == 0
    assert len(list(ldir.glob("2*.json"))) == 1                        # only today's record file is left
    assert (ldir / DAYS_DIR / "2026-10-18.jsonl").exists() and (ldir / DAYS_DIR / "2026-10-18.curves.jsonl").exists()
    after = read_tests(ldir)
    assert [t["id"] for t in after] == [t["id"] for t in before]
    assert [t["curves"] for t in after] == [t["curves"] for t in before]
    winner = next(t for t in after if t["name"] == "winner")
    assert set(winner["curves"]) == {"insample", "outofsample"} and winner["curves"]["outofsample"]["dates"]
    assert load_curves(ldir, [{"id": winner["id"], "ts": winner["ts"]}])[winner["id"]] == winner["curves"]
    assert summary(ldir, today="2026-10-19")["n_tests"] == 3


def test_evolve_never_runs_dry_and_stays_in_bounds():
    from hive.operator.sweep import evolve

    cfg = StrategyConfig()
    bounds = load_bounds(REPO_ROOT / "config" / "bounds.toml")
    results = [{"sleeve": "stocks", "overrides": {"stocks.entry_mode": "ma_pullback"}, "oos_cagr": 0.07,
                "oos_dd": -0.13, "verdict": "passed"}]
    a = evolve(results, bounds, cfg, seed=7)
    b = evolve(results, bounds, cfg, seed=8)
    assert len(a) >= 100 and a != b and a == evolve(results, bounds, cfg, seed=7)   # fresh per seed, replayable
    assert any(s == "crypto" for s, _ in a)                                          # untested sleeves mutate the parent
    for sleeve, ov in a:
        ok, why = validate_overrides(ov, bounds)
        assert ok, (sleeve, ov, why)
        assert ov and len(ov) <= 6
        assert all(v != getattr(getattr(cfg, k.split(".")[0]), k.split(".")[1]) for k, v in ov.items())
