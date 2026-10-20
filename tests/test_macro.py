"""Forex, metals and index futures (config 2.3.0): data, the lab sleeve, and paper books on the simulator."""

import json
from datetime import date

import numpy as np
import pandas as pd

from hive.config import REPO_ROOT, Settings, StrategyConfig
from hive.data import refresh as refresh_mod
from hive.data.store import BarStore
from hive.hive.children import _run_engine_child, needs_engine
from hive.hive.governance import promote_earned
from hive.hive.registry import Child, HiveRegistry
from hive.operator.iterate import default_inputs, evaluate, load_bounds
from hive.operator.sweep import SLEEVES, grid, queue_staged
from hive.orchestrator.journal import Journal
from tests.test_features import bars_from_path, textbook_setup


def _store_with_macro(root, n_extra=200) -> BarStore:
    store = BarStore(root / "data" / "cache")
    close, volume = textbook_setup(n_after=n_extra)
    up = bars_from_path(close, volume, start="2011-01-03")
    down = bars_from_path(10_000.0 / close, volume, start="2011-01-03")
    store.put("macro", "XAUUSD", up, source="test")
    store.put("macro", "EURUSD", down, source="test")
    return store


def test_refresh_fetches_the_whole_history_once_then_the_last_days(tmp_path, monkeypatch):
    calls = []

    def fake(symbols, start="2000-01-01"):
        calls.append((tuple(symbols), start))
        idx = pd.bdate_range("2024-01-01", periods=5)
        df = pd.DataFrame({"open": 1.10, "high": 1.09, "low": 1.11, "close": 1.105, "adj_close": 1.105, "volume": 0.0},
                          index=idx)                                   # Yahoo's inverted high/low
        return {symbols[0]: df}

    monkeypatch.setattr(refresh_mod.yfinance_source, "fetch_daily", fake)
    cfg = StrategyConfig()
    cfg.universe.macro_symbols = {"EURUSD": "EURUSD=X"}
    store = BarStore(tmp_path)
    assert refresh_mod.refresh_macro(store, cfg)["macro"] == 1 and calls[-1] == (("EURUSD=X",), "2000-01-01")
    b = store.get("macro", "EURUSD")
    assert (b["high"] >= b[["open", "close", "low"]].max(axis=1)).all() and (b["low"] <= b["close"]).all()
    refresh_mod.refresh_macro(store, cfg)
    assert calls[-1][1] != "2000-01-01"                                  # cached: only the recent days


def test_the_lab_tests_the_macro_sleeve_long_and_short(tmp_path):
    _store_with_macro(tmp_path)
    s = Settings(hive_data_dir=tmp_path / "data", hive_state_dir=tmp_path / "state", _env_file=None)
    cfg = StrategyConfig()
    cfg.operator.insample_start, cfg.operator.insample_end = "2011-06-01", "2011-12-31"
    cfg.operator.outofsample_start = "2012-01-01"                      # the breakout (and breakdown) is in 2012
    inp = default_inputs(s, cfg, "macro")
    assert set(inp["bars"]) == {"XAUUSD", "EURUSD"} and inp["cost"].name == "macro_cfd" and len(inp["calendar"]) > 300
    ev = evaluate(cfg, {}, inp)                                         # the parent trades both directions
    assert ev.outofsample["n_trades"] > 0
    assert "macro" in SLEEVES and any(sl == "macro" for sl, _ in grid())
    assert ("macro", {"macro.direction": "long"}) in grid()
    staged = queue_staged(HiveRegistry(tmp_path / "state" / "hive.json", cfg.survival), cfg,
                          load_bounds(REPO_ROOT / "config" / "bounds.toml"))
    assert any(sl == "macro" for sl, _, _ in staged)


def test_a_book_the_primary_cannot_trade_runs_on_the_simulator_and_is_never_promoted(tmp_path):
    _store_with_macro(tmp_path)
    s = Settings(hive_data_dir=tmp_path / "data", hive_state_dir=tmp_path / "state", _env_file=None)
    cfg = StrategyConfig()
    reg = HiveRegistry(tmp_path / "state" / "hive.json", cfg.survival)
    tier = next(t for t in cfg.survival.tiers if t.name == "base")
    fx = reg.spawn(Child(id="", name="fx", kind="variant", sleeve="macro", overrides={}, born="2012-01-02"), tier)
    short = Child(id="s", name="s", kind="variant", sleeve="stocks", overrides={"stocks.direction": "both"})
    assert needs_engine(fx) and needs_engine(short)
    assert not needs_engine(Child(id="l", name="l", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 6.0}))
    cdir = tmp_path / "state" / "children" / fx.id
    j = Journal(tmp_path / "state" / "journal", f"2012-06-29-child-{fx.id}")
    eq = _run_engine_child(s, cfg, fx, cdir, j, date(2012, 6, 29), {})
    latest = json.loads((cdir / "latest.json").read_text())
    assert latest["equity"] == eq and latest["markets"] == 2 and latest["since"] == "2012-01-02"
    assert np.isfinite(eq) and eq > 0
    # it out-earns the parent for the whole shadow period, but no venue at the primary trades it
    for i in range(cfg.survival.child_shadow_days):
        reg.mark(fx.id, 100.0 + 2 * i, 100.0, on=date(2012, 7, 2 + i))
    assert promote_earned(s, cfg, reg, tier, date(2012, 7, 20)) == []
    assert reg.get(fx.id).status == "shadow"
