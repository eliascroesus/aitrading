from datetime import UTC, datetime, timedelta

import pytest

from hive.config import RiskLimits, SurvivalRules
from hive.risk.engine import OrderIntent, PortfolioSnapshot, PositionInfo, RiskEngine
from hive.survival.ledger import SurvivalLedger


def buy(sym="AAA", notional=20.0, price=10.0, stop=9.0, ac="stock"):
    return OrderIntent(sym, ac, "buy", notional, notional / price, price, stop)


def test_risk_engine_requires_stop_and_clips_caps():
    lim = RiskLimits(max_positions=6, max_position_pct_stock=0.2, max_open_risk_pct=0.06)
    eng = RiskEngine(lim)
    snap = PortfolioSnapshot(equity=100.0, cash=100.0)
    v = eng.pre_trade([buy(notional=50.0)], snap)[0]
    assert v.approved and v.adjusted.notional == pytest.approx(20.0)
    assert "clipped: position cap" in v.reasons
    bad = OrderIntent("BBB", "stock", "buy", 10.0, 1.0, 10.0, None)
    assert not eng.pre_trade([bad], snap)[0].approved


def test_risk_engine_heat_cap():
    lim = RiskLimits(max_positions=6, max_new_entries_per_day=5, max_open_risk_pct=0.02,
                     max_position_pct_stock=0.5)
    eng = RiskEngine(lim)
    snap = PortfolioSnapshot(equity=100.0, cash=100.0)
    # each order risks 10% of notional (stop 10% below); heat cap 2% of equity -> $20 notional total
    vs = eng.pre_trade([buy("A", 20.0), buy("B", 20.0)], snap)
    assert vs[0].approved and vs[0].adjusted.notional == pytest.approx(20.0)
    assert not vs[1].approved and "below min notional" in vs[1].reasons  # heat exhausted


def test_risk_engine_entries_per_day():
    lim = RiskLimits(max_positions=6, max_new_entries_per_day=2, max_open_risk_pct=0.10,
                     max_position_pct_stock=0.5)
    eng = RiskEngine(lim)
    snap = PortfolioSnapshot(equity=100.0, cash=100.0)
    vs = eng.pre_trade([buy("A", 20.0), buy("B", 20.0), buy("C", 20.0)], snap)
    assert vs[0].approved and vs[1].approved
    assert not vs[2].approved and "max new entries today" in vs[2].reasons


def test_risk_engine_blocks_on_daily_loss_and_kill_switch():
    lim = RiskLimits(max_daily_loss_pct=0.04)
    eng = RiskEngine(lim)
    snap = PortfolioSnapshot(equity=95.0, cash=95.0, day_start_equity=100.0)
    v = eng.pre_trade([buy()], snap)[0]
    assert not v.approved and "daily loss" in v.reasons[0]
    eng2 = RiskEngine(lim, kill_switch=lambda: True)
    assert not eng2.pre_trade([buy()], PortfolioSnapshot(100.0, 100.0))[0].approved
    # Sells are always allowed through
    held = PortfolioSnapshot(100.0, 50.0, {"AAA": PositionInfo("AAA", "stock", 5, 10, 9)})
    sell = OrderIntent("AAA", "stock", "sell", 50.0, 5.0, 10.0)
    assert eng2.pre_trade([sell], held)[0].approved


def test_llm_can_only_reduce():
    it = buy(notional=20.0)
    assert RiskEngine.apply_llm_action(it, "veto") is None
    half = RiskEngine.apply_llm_action(it, "reduce_half")
    assert half.notional == 10.0 and half.qty == pytest.approx(1.0)
    assert RiskEngine.apply_llm_action(it, "approve") == it
    sell = OrderIntent("AAA", "stock", "sell", 10.0, 1.0, 10.0)
    assert RiskEngine.apply_llm_action(sell, "veto") == sell


def test_tier_multiplier_and_close_only():
    eng = RiskEngine(RiskLimits(), tier_risk_multiplier=0.5)
    v = eng.pre_trade([buy(notional=10.0)], PortfolioSnapshot(100.0, 100.0))[0]
    assert v.adjusted.notional == pytest.approx(5.0)
    eng2 = RiskEngine(RiskLimits(), close_only=True)
    assert not eng2.pre_trade([buy()], PortfolioSnapshot(100.0, 100.0))[0].approved


def test_ledger_chain_costs_tiers_and_death(tmp_path):
    rules = SurvivalRules()
    led = SurvivalLedger(tmp_path / "ledger.jsonl", rules)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    led.append("equity_mark", 100.0, ts=t0)
    led.append("ai_cost", 0.03, {"model": "x"}, ts=t0)
    led.append("ai_cost", 0.02, {"model": "x"}, ts=t0)
    st = led.status(100.0, on=t0.date())
    assert st.equity_net == pytest.approx(99.95)
    assert st.ai_cost_today == pytest.approx(0.05)
    assert st.tier == "base"
    assert st.budget_today == pytest.approx(max(99.95 * 0.0005, rules.daily_ai_budget_min_usd))
    assert not st.dead
    # chain verifies and reloads
    assert led.verify()
    led2 = SurvivalLedger(tmp_path / "ledger.jsonl", rules)
    assert len(led2.entries) == 3 and led2.verify()
    # tamper -> reload fails
    lines = (tmp_path / "ledger.jsonl").read_text().splitlines()
    lines[1] = lines[1].replace("0.03", "0.30")
    (tmp_path / "ledger.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError):
        SurvivalLedger(tmp_path / "ledger.jsonl", rules)


def test_tier_hysteresis_and_upgrade_requirements(tmp_path):
    rules = SurvivalRules()
    led = SurvivalLedger(tmp_path / "l.jsonl", rules)
    base = led.tier(100.0)
    assert base.name == "base"
    led.record_tier(base)
    # Dropping just under $90 keeps base (hysteresis 15% -> floor 76.5)
    assert led.tier(85.0).name == "base"
    assert led.tier(70.0).name == "survival"
    # Jumping to $300 without a 30-day positive streak stays base
    assert led.tier(300.0).name == "base"
    t = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(31):
        led.append("equity_mark", 100.0 + i * 7, ts=t + timedelta(days=i))
    assert led.net_positive_days() >= 30
    assert led.tier(300.0).name == "growth"


def test_death_conditions(tmp_path):
    rules = SurvivalRules(death_floor_usd=25.0, death_drawdown_pct=0.75)
    led = SurvivalLedger(tmp_path / "l.jsonl", rules)
    led.append("equity_mark", 400.0)
    assert led.is_dead(24.0)[0]
    assert led.is_dead(99.0)[0]  # 75% drawdown from the 400 peak
    assert not led.is_dead(150.0)[0]
