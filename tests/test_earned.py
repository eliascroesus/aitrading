"""Earned budgets (config 2.3.0): the models are paid by what they add, and the operator by its record."""

from datetime import date, timedelta

import pytest

from hive.backtest.costs import ALPACA_CRYPTO, ALPACA_STOCKS
from hive.config import StrategyConfig
from hive.hive.registry import Child, HiveRegistry
from hive.operator.iterate import Proposal, operate, read_research_log
from hive.survival.earned import (
    desk_multiplier,
    desk_value_add,
    explain_operator,
    operator_multiplier,
    operator_scorecard,
)
from hive.survival.ledger import SurvivalLedger
from tests.test_features import bars_from_path, textbook_setup


def _books(tmp_path, primary_daily: float, shadow_daily: float, days: int, ai_per_day: float = 0.0):
    cfg = StrategyConfig()
    p = SurvivalLedger(tmp_path / "ledger.jsonl", cfg.survival)
    s = SurvivalLedger(tmp_path / "ledger_shadow.jsonl", cfg.survival)
    d0 = date(2026, 9, 1)
    for i in range(days + 1):
        d = (d0 + timedelta(days=i)).isoformat()
        p.append("equity_mark", 100.0 * (1 + primary_daily) ** i, {"date": d})
        s.append("equity_mark", 100.0 * (1 + shadow_daily) ** i, {"date": d})
        if i and ai_per_day:
            p.append("ai_cost", ai_per_day, {"label": "primary", "date": d})
    return p, s


def test_the_desks_are_paid_by_what_they_add_over_the_rules(tmp_path):
    p, s = _books(tmp_path / "a", 0.001, 0.0, 20)                  # +2% over the rules-only shadow
    va, n = desk_value_add(p, s)
    assert n == 20 and va == pytest.approx(1.001 ** 20 - 1, rel=1e-6)
    assert desk_multiplier(va) == pytest.approx(1 + 25 * va)        # about 1.5x
    p, s = _books(tmp_path / "b", 0.001, 0.0, 20, ai_per_day=0.15)  # the same $2 lead, eaten by $3 of AI calls
    assert desk_value_add(p, s)[0] < 0 and desk_multiplier(desk_value_add(p, s)[0]) < 1
    p, s = _books(tmp_path / "c", -0.01, 0.0, 20)                   # far behind: the floor
    assert desk_multiplier(desk_value_add(p, s)[0]) == 0.25
    p, s = _books(tmp_path / "d", 0.05, 0.0, 3)                     # too few days to judge: the base
    assert desk_value_add(p, s)[0] is None and desk_multiplier(None) == 1.0


def test_the_operator_is_paid_by_its_record_against_the_free_search():
    log = [{"date": "2026-10-01", "note": "lab: 10 cells", "n_tested": 10, "n_passed": 4, "proposals": []},
           {"date": "2026-09-01", "note": "systematic sweep (code, no LLM)",
            "proposals": [{"overrides": {"stocks.trail_atr": 6}, "verdict": "spawned shadow child x"},
                          {"overrides": {"stocks.trail_atr": 7}, "verdict": "rejected: y"}]},
           {"date": "2026-10-02", "by": "operator", "note": "tried wider trails",
            "proposals": [{"name": "a", "sleeve": "stocks", "overrides": {"stocks.trail_atr": 8.0},
                           "verdict": "spawned shadow child op-a: beats parent"},
                          {"name": "b", "sleeve": "crypto", "overrides": {"crypto.trail_atr": 9.0},
                           "verdict": "rejected: OOS prop pass rate 1% does not beat parent 6%"},
                          {"name": "watch", "sleeve": "all", "overrides": {}, "verdict": "spawned monitor"}]}]
    kids = [Child(id="op-a", name="a", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 8.0},
                  status="promoted")]
    card = operator_scorecard(log, kids)
    assert (card["proposals"], card["passed"], card["lab_tests"], card["lab_passed"]) == (2, 1, 12, 5)
    assert card["seats"]["promoted"] == 1
    m = operator_multiplier(card)
    assert m == pytest.approx((2 / 4) / (6 / 14) + 0.5)
    assert "1 of 2 proposals" in explain_operator(card, m) and "paid from the bankroll" in explain_operator(card, m)
    worse = operator_scorecard([log[0], {"by": "operator", "note": "n", "proposals": [
        {"name": n, "sleeve": "stocks", "overrides": {"stocks.trail_atr": 3.0 + i}, "verdict": "rejected"}
        for i, n in enumerate("abcdefgh")]}], [])
    assert operator_multiplier(worse) < 1.0                          # worse than the free search: paid less


def test_each_proposal_is_tested_on_its_own_sleeve_and_the_operator_sees_its_record(tmp_path):
    close, volume = textbook_setup(n_after=400)
    bars = bars_from_path(close, volume, start="2010-01-01")
    cfg = StrategyConfig()
    cfg.operator.insample_start, cfg.operator.insample_end = "2010-01-01", "2011-12-31"
    cfg.operator.outofsample_start, cfg.operator.outofsample_end = "2012-01-01", None
    stocks = {"bars": {"AAA": bars}, "classes": {"AAA": "stock"}, "bench": bars, "cost": ALPACA_STOCKS}
    crypto = {"bars": {"BTC": bars}, "classes": {"BTC": "crypto"}, "bench": bars, "cost": ALPACA_CRYPTO}
    asked = []

    def inputs_for(sl):
        asked.append(sl)
        return crypto

    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = next(t for t in cfg.survival.tiers if t.name == "hive")
    recs = operate(cfg=cfg, registry=reg, tier=tier, status=None, llm=None, model="m", inputs=stocks,
                   inputs_for=inputs_for, on=date(2026, 1, 1),
                   proposals_override=[Proposal(name="c", hypothesis="h", sleeve="crypto",
                                                overrides={"crypto.trail_atr": 6.0})])
    assert asked == ["crypto"] and recs[0]["sleeve"] == "crypto"
    row = read_research_log(tmp_path / "research_log.jsonl")[-1]
    assert row["by"] == "operator" and "budget_multiplier" in row
