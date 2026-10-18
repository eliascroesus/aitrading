"""Weekly research draws on unspent daily budget, never on the day's decision budget."""

from datetime import date

from hive.config import StrategyConfig
from hive.survival.ledger import SurvivalLedger


def test_research_pot_accrues_and_is_separate_from_the_daily_budget(tmp_path):
    cfg = StrategyConfig()
    led = SurvivalLedger(tmp_path / "ledger.jsonl", cfg.survival)
    on = date(2026, 10, 18)
    for i in range(7, 0, -1):
        led.append("equity_mark", 100.0, {"label": "primary", "date": date(2026, 10, 18 - i).isoformat()})
    led.append("ai_cost", 0.02, {"label": "primary", "date": "2026-10-16"})
    st = led.status(100.0, on=on)
    tier = led.tier(st.equity_net)
    per_day = led.budget_today(st.equity_net, tier)
    assert led.research_budget(st.equity_net, tier, on) == pytest_approx(7 * per_day - 0.02)
    led.append("ai_cost", 0.03, {"label": "operator", "date": f"{on.isoformat()}-research"})
    assert led.research_budget(st.equity_net, tier, on) == pytest_approx(7 * per_day - 0.05)
    # the day's decision budget does not see research spend
    assert led.status(100.0, on=on).ai_cost_today == 0.0
    assert led.status(100.0, on=date(2026, 10, 16)).ai_cost_today == pytest_approx(0.02)
    # but net equity pays for everything
    assert led.status(100.0, on=on).equity_net == pytest_approx(100.0 - 0.05)


def pytest_approx(x):
    import pytest

    return pytest.approx(x, abs=1e-9)
