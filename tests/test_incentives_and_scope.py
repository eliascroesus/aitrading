"""Incentive framing, bankroll guard, memecoin majors in the crypto sleeve."""

import pytest

from hive.agents import prompts
from hive.config import StrategyConfig
from hive.data.sources.yfinance_source import yahoo_crypto_symbol
from hive.operator.iterate import OPERATOR_SYSTEM
from hive.orchestrator.run import BankrollMismatchError, check_bankroll
from hive.signals.sizing import size_position


def test_alt_crypto_cap_is_smaller():
    cfg = StrategyConfig()
    # A one-cent stop makes the position cap the binding constraint.
    big = size_position(equity=100, price=10, stop_price=9.99, risk=cfg.risk, asset_class="crypto")
    small = size_position(equity=100, price=10, stop_price=9.99, risk=cfg.risk, asset_class="crypto",
                          cap_pct=cfg.risk.max_position_pct_crypto_alt)
    assert big.notional == pytest.approx(30.0)
    assert small.notional == pytest.approx(10.0)
    assert set(cfg.universe.crypto_alt_symbols) <= set(cfg.universe.crypto_symbols)


def test_bankroll_guard_refuses_an_unreset_paper_account():
    check_bankroll(103.0, 100.0, [100.0, 101.0])                 # ordinary day
    check_bankroll(260.0, 100.0, [100.0, 180.0, 240.0])          # genuine growth is in the marks
    check_bankroll(250.0, 200.0, [100.0, 105.0])                 # a recorded capital_in raises the basis
    with pytest.raises(BankrollMismatchError):
        check_bankroll(100_000.0, 100.0, [])
    with pytest.raises(BankrollMismatchError):
        check_bankroll(100_000.0, 100.0, [100.0, 99.0])


def test_firm_status_states_the_live_incentive():
    cfg = StrategyConfig()
    s = {"equity_net": 120.0, "peak_equity": 130.0, "tier": "base", "budget_today": 0.06,
         "budget_remaining": 0.02, "ai_cost_total": 1.5, "runway_days": None, "in_halt_cooldown": False}
    txt = prompts.firm_status(s, cfg.survival.tiers)
    assert "net equity $120.00" in txt and "-7.7%" in txt
    assert "allows 4 sub-agents" in txt and "'growth' at $250" in txt and "unlocks 6 sub-agents" in txt
    assert prompts.firm_status(None).startswith("Firm status")
    assert "grow that bankroll" in prompts.OBJECTIVE and "not yours to change" in prompts.OBJECTIVE


def test_operator_prompt_placeholder_does_not_eat_other_capitals():
    assert "{N}" in OPERATOR_SYSTEM
    assert "JSON" in OPERATOR_SYSTEM.replace("{N}", "3")


def test_yahoo_symbols_for_memecoin_majors():
    assert yahoo_crypto_symbol("PEPE") == "PEPE24478-USD"
    assert yahoo_crypto_symbol("DOGE") == "DOGE-USD"
    assert yahoo_crypto_symbol("SHIB-USD") == "SHIB-USD"
