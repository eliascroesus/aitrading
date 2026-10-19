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
    out = build(s, cfg, tmp_path / "dash")
    with playwright.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(out["html"].resolve().as_uri())
        page.wait_for_timeout(500)
        text = page.inner_text("body")
        browser.close()
    assert errors == [], errors
    for needle in ("Sub-agents", "fed watch", "meme scout", "template_cross", "Operator's notes", "Auditor",
                   "Live book", "Firm activity", "staying selective"):
        assert needle in text, needle
