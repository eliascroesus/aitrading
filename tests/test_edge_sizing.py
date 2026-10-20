"""Risk per trade chosen by evidence (config 2.2.0): edge sizing, and the lab's search over it."""

import pytest

from hive.backtest.costs import ALPACA_STOCKS
from hive.backtest.engine import Backtester
from hive.config import REPO_ROOT, RiskLimits, StrategyConfig
from hive.operator.iterate import load_bounds
from hive.operator.sweep import EDGE_KNOBS, evolve, grid, refine
from hive.orchestrator.journal import Journal
from hive.orchestrator.run import closed_r_multiples
from hive.signals.sizing import edge_risk_pct, size_position
from tests.test_backtest import _scenario


def test_edge_sizing_risks_what_the_record_justifies():
    edge = RiskLimits(sizing_mode="edge", edge_min_trades=5, edge_fraction=0.5,
                      edge_min_risk_pct=0.0025, max_risk_per_trade_pct=0.05)
    strong = [2.0] * 6 + [-1.0] * 4                      # W 0.6, payoff 2: Kelly 0.4
    weak = [1.0] * 3 + [-1.0] * 7                        # Kelly below zero
    assert edge_risk_pct(strong, RiskLimits()) == 0.01                       # fixed mode ignores the record
    assert edge_risk_pct(strong[:4], edge) == edge.risk_per_trade_pct       # too few trades yet
    assert edge_risk_pct(strong, edge) == 0.05                               # half-Kelly 0.2, capped
    assert edge_risk_pct(strong, edge.model_copy(update={"edge_fraction": 0.1})) == pytest.approx(0.04)
    assert edge_risk_pct(weak, edge) == 0.0025                               # no edge: the floor
    assert edge_risk_pct([1.0] * 5, edge) == 0.05 and edge_risk_pct([-1.0] * 5, edge) == 0.0025
    loose = RiskLimits(max_position_pct_stock=1.0)
    one = size_position(equity=100, price=10, stop_price=9, risk=loose)
    two = size_position(equity=100, price=10, stop_price=9, risk=loose, risk_pct=0.02)
    assert two.notional == pytest.approx(2 * one.notional)


def test_closed_r_multiples_reads_the_books_journal(tmp_path):
    a = Journal(tmp_path, "2026-10-01")
    a.write("exit", {"symbol": "AAA", "r_multiple": 2.0})
    a.write("exit", {"symbol": "BBB", "r_multiple": None})               # unfilled close: skipped
    b = Journal(tmp_path, "2026-10-02")
    b.write("reconcile", {"symbol": "CCC", "r_multiple_est": -1.0})
    b.write("fill", {"symbol": "DDD", "r_multiple": 9.0})                 # not a close
    b.write("exit", {"symbol": "EEE", "r_multiple": 0.5})
    assert closed_r_multiples(tmp_path, 10) == [2.0, -1.0, 0.5]
    assert closed_r_multiples(tmp_path, 2) == [-1.0, 0.5]


def test_the_backtester_sizes_by_edge_and_still_accounts():
    cfg = StrategyConfig(risk=RiskLimits(max_positions=3, max_new_entries_per_day=3, sizing_mode="edge",
                                         edge_min_trades=1))
    res = Backtester({"AAA": _scenario()}, _scenario(), cfg, ALPACA_STOCKS, initial_equity=100.0).run()
    assert not res.trades.empty
    assert res.equity.iloc[-1] == pytest.approx(100.0 + res.trades["pnl"].sum(), abs=1e-6)


def test_the_lab_searches_sizing_and_never_tests_an_inert_edge_knob():
    cfg = StrategyConfig()
    bounds = load_bounds(REPO_ROOT / cfg.operator.bounds_file)
    cells = grid()
    for sleeve in ("stocks", "crypto"):
        assert (sleeve, {"risk.sizing_mode": "edge"}) in cells
    assert ("stocks", {"risk.max_position_pct_stock": 1.0, "risk.max_position_pct_etf": 1.0}) in cells
    results = [
        {"sleeve": sl, "overrides": ov, "oos_cagr": c, "oos_dd": -0.1, "oos_prop": p, "verdict": "passed"}
        for sl in ("stocks", "crypto")
        for ov, c, p in (({"risk.sizing_mode": "edge"}, 0.4, 0.6), ({"risk.risk_per_trade_pct": 0.02}, 0.3, 0.5))
    ]
    out = evolve(results, bounds, cfg, seed=7, per_sleeve=200) + refine(results, bounds, cfg)
    assert out
    for sleeve, ov in out:
        if any(k in EDGE_KNOBS for k in ov):
            assert ov.get("risk.sizing_mode") == "edge", ov
        if sleeve == "crypto":
            assert not any("stock" in k or "etf" in k for k in ov), ov
    assert any(ov.get("risk.sizing_mode") == "edge" and any(k in EDGE_KNOBS for k in ov) for _, ov in out)
