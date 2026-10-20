"""The dashboard must render end to end in a browser, with every kind of sub-agent present.

A blank page is the one failure a static build test cannot catch: the script
throws in the browser. This test opens the built page in headless Chromium when
Playwright is available (CI installs it) and fails on any console error.
"""

import json
import os
from datetime import date

import pytest

from hive.config import Settings, StrategyConfig
from hive.hive.registry import Child, HiveRegistry
from hive.orchestrator.journal import Journal
from hive.survival.ledger import SurvivalLedger
from hive.ui.dashboard import build

playwright = pytest.importorskip("playwright.sync_api")


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception:
        path = os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium")
        if os.path.exists(path):
            return p.chromium.launch(executable_path=path)
        pytest.skip("no chromium available")


def _tier(cfg, name):
    return next(t for t in cfg.survival.tiers if t.name == name)


def test_dashboard_renders_with_every_kind_of_sub_agent(tmp_path):
    cfg = StrategyConfig()
    state = tmp_path / "state"
    s = Settings(hive_mode="paper", hive_data_dir=tmp_path, hive_state_dir=state,
                 hive_reports_dir=tmp_path / "reports", _env_file=None)
    led = SurvivalLedger(state / "ledger.jsonl", cfg.survival)
    for i, d in enumerate(("2026-10-16", "2026-10-18", "2026-10-19")):
        led.append("equity_mark", 100.0 + i * 0.3, {"label": "primary", "date": d})
    led.append("ai_cost", 0.04, {"label": "primary", "date": "2026-10-19"})
    reg = HiveRegistry(state / "hive.json", cfg.survival)
    tier = _tier(cfg, "base")
    v = reg.spawn(Child(id="", name="sweep stocks: entry_mode=template_cross", kind="variant", sleeve="stocks",
                        overrides={"stocks.entry_mode": "template_cross"}, hypothesis="earliest entry"), tier)
    m = reg.spawn(Child(id="", name="fed watch", kind="monitor", sleeve="all", watch=["Fed rates", "SOL"]), tier)
    sc = reg.spawn(Child(id="", name="meme scout", kind="scout", sleeve="meme"), tier)
    for i in range(3):
        reg.mark(v.id, 100 + i, 100 + i * 0.5, on=date(2026, 10, 17 + i))
    reg.record_iteration({"date": "2026-10-19", "proposal": "sweep stocks: entry_mode=ma_pullback", "kind": "sweep",
                          "overrides": {"stocks.entry_mode": "ma_pullback"}, "sleeve": "stocks", "is_cagr": 0.03,
                          "oos_cagr": 0.069, "oos_dd": -0.135, "parent_oos_cagr": 0.028,
                          "verdict": "passed but not spawned: tier base allows 2 children; 2 active"})
    cdir = state / "children"
    (cdir / v.id).mkdir(parents=True)
    SurvivalLedger(cdir / v.id / "ledger.jsonl", cfg.survival).append("equity_mark", 101.0, {"label": v.id, "date": "2026-10-19"})
    (cdir / m.id).mkdir(parents=True)
    (cdir / m.id / "latest.json").write_text(json.dumps({"date": "2026-10-19", "child": m.id, "reports": [
        {"query": "Fed rates", "n_items": 7, "net_sentiment": -0.2, "material_negative": False, "attention_spike": True,
         "event_types": ["macro"]}]}))
    j = Journal(state / "journal", "2026-10-19")
    j.write("run_start", {"label": "primary", "date": "2026-10-19", "equity": 100.0})
    j.write("desk_notes", {"notes": [{"symbol": "MNST", "news": {"net_sentiment": 0.1, "material_negative": False,
                                                                 "attention_spike": True, "events": [{"event_type": "x"}]},
                                      "fundamental": {"signal": -0.1}, "technical": {"signal": 0.5},
                                      "sentiment": {"signal": -0.2}, "debate": {"net_signal": 0.05},
                                      "action": "veto", "confidence": 0.7, "cost_usd": 0.007}],
                           "portfolio_note": "staying selective"})
    j.write("news_watch", {"symbol": "TRV", "material_negative": False, "net_sentiment": 0.4, "attention_spike": True,
                           "cost_usd": 0.0004})
    Journal(state / "journal", f"2026-10-19-child-{sc.id}").write("meme_day", {"n_candidates": 0, "scanned": 52,
                                                                                "exits": [], "entries": [],
                                                                                "equity": 3.0, "n_open": 0})
    (state / "research_log.jsonl").write_text(json.dumps({"date": "2026-10-19", "note": "loosening lost",
                                                           "parent_oos_cagr": 0.028, "proposals": []}) + "\n")
    (state / "heartbeat.json").write_text(json.dumps({"ts": "2026-10-19T19:00:00+00:00", "broker": "alpaca-paper",
                                                       "equity": 100.1, "cash": 75.0, "market_open": True,
                                                       "positions": [{"symbol": "TRV", "asset_class": "stock", "qty": 0.0445,
                                                                      "avg_price": 387.0, "current_price": 389.0,
                                                                      "market_value": 17.3, "unrealised_pct": 0.005,
                                                                      "stop_price": None}]}))
    (tmp_path / "reports" / "reviews").mkdir(parents=True)
    (tmp_path / "reports" / "reviews" / "review-2026-10-19.json").write_text(json.dumps({"report": {
        "findings": [{"severity": "info", "area": "cost", "finding": "fine", "proposed_change": "none"}],
        "llm_added_value_assessment": "not yet"}}))
    # the lab: a parent reference, a cell that beat the parent (seated) and a rejected one, with curves
    from hive.backtest.costs import ALPACA_STOCKS
    from hive.operator.iterate import evaluate
    from hive.operator.lab import lab_dir, lab_record, parent_eval, record_test
    from tests.test_features import bars_from_path, textbook_setup

    close, volume = textbook_setup(n_after=400)
    bars = bars_from_path(close, volume, start="2010-01-01")
    lcfg = StrategyConfig()
    lcfg.operator.insample_start, lcfg.operator.insample_end = "2010-01-01", "2011-12-31"
    lcfg.operator.outofsample_start = "2012-01-01"
    inputs = {"bars": {"AAA": bars}, "classes": {"AAA": "stock"}, "bench": bars, "cost": ALPACA_STOCKS}
    parent = parent_eval(lcfg, "stocks", inputs, lab_dir(s), on="2026-10-19")
    ev = evaluate(lcfg, {"stocks.entry_mode": "template_cross"}, inputs, keep_curves=True)
    record_test(lab_dir(s), lab_record(by="sweep", sleeve="stocks", name="sweep stocks: entry_mode=template_cross",
                                        overrides={"stocks.entry_mode": "template_cross"}, ev=ev, parent=parent,
                                        verdict=f"spawned shadow child {v.id}: beats parent", on="2026-10-19",
                                        child_id=v.id, cfg=lcfg, elapsed_s=1.0))
    record_test(lab_dir(s), lab_record(by="refine", sleeve="crypto", name="refine crypto: trail_atr=4.5",
                                        overrides={"crypto.trail_atr": 4.5}, ev=ev, parent=parent,
                                        verdict="rejected: OOS CAGR 1.0% does not beat parent 2.8%", on="2026-10-19",
                                        cfg=lcfg, elapsed_s=0.5))
    # the trench scout's latest scan
    tr = reg.spawn(Child(id="trench-scout", name="trench scout", kind="scout", sleeve="trench"), tier, force=True)
    (cdir / tr.id).mkdir(parents=True)
    (cdir / tr.id / "latest.json").write_text(json.dumps({
        "ts": "2026-10-19T19:00:00+00:00", "scanned": 70, "passed": 3, "rugchecked": 3, "rejects": {"liquidity": 40},
        "equity": 101.4, "cash": 90.0, "n_open": 1, "entries": [], "exits": [],
        "open": [{"mint": "m", "symbol": "FROGZ", "entry": 0.0004, "last": 0.0005, "value": 6.2, "cost": 5.0}]}))
    out = build(s, cfg, tmp_path / "dash")
    assert (tmp_path / "dash" / "version.json").exists()
    chunks = list((tmp_path / "dash" / "lab").glob("h-*.json"))          # recent curves, fetched on demand
    assert chunks and any(json.loads(c.read_text()) for c in chunks)
    with playwright.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(out["html"].resolve().as_uri())
        page.wait_for_timeout(500)
        text = page.inner_text("body")
        n_dots = page.locator("#labmap circle").count()
        n_diamonds = page.locator("#labmap path").count()
        curve_before = page.inner_text("#labtitle")                  # the newest test is selected by default
        page.locator("tr.labrow", has_text="template_cross").first.click()
        page.wait_for_timeout(100)
        curve_after = page.inner_text("#labtitle")
        page.locator("button[data-win=insample]").click()
        page.wait_for_timeout(100)
        curve_is = page.inner_text("#labtitle")
        page.fill("#labfilter", "crypto")
        page.wait_for_timeout(100)
        visible = page.locator("#labtable tr.labrow:visible").count()
        n_nodes = page.locator("#net .node").count()
        n_tiles = page.locator(".gallery .gt").count()
        n_wire = page.locator(".wire li").count()
        n_ticker = page.locator("#ticker .track > span").count()
        page.set_viewport_size({"width": 390, "height": 860})
        page.wait_for_timeout(400)
        phone_width = page.evaluate("document.documentElement.scrollWidth")
        browser.close()
    assert errors == [], errors
    for needle in ("Sub-agents", "fed watch", "meme scout", "template_cross", "Operator's notes", "Auditor",
                   "Live book", "Live wire", "staying selective", "Strategy lab", "Leaderboard", "Map: every test",
                   "Selected test", "Lab log", "Backtests", "Next up", "refine", "Trench scout", "FROGZ",
                   "Rules in force", "lab moves it", "owner edits config", "Enforced in code", "What every model is told",
                   "Right now", "The firm, live", "What the lab is learning", "Trench trades", "Hyperliquid desk",
                   "Social pulse", "no account, no keys, no orders", "X_BEARER_TOKEN"):
        assert needle.lower() in text.lower(), needle
    assert n_dots == 2 and n_diamonds >= 1                             # two tests, one parent diamond
    assert "trail_atr=4.5" in curve_before and "template_cross" in curve_after and "in-sample" in curve_is
    assert visible == 1
    assert n_nodes >= 10 and n_tiles == 2 and n_wire >= 1 and n_ticker >= 2
    assert phone_width <= 390                                          # no horizontal scroll on a phone
