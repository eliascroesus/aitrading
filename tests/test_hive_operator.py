from datetime import UTC, date, datetime, timedelta

import pytest

from hive.config import REPO_ROOT, MemeRules, StrategyConfig, SurvivalRules
from hive.hive.registry import Child, HiveRegistry, PromotionNeedsHumanError
from hive.meme.scanner import pair_summary, passes_filters
from hive.meme.scout import MemeBook, run_scout_day
from hive.operator.iterate import Proposal, load_bounds, validate_overrides


def _tier(cfg, name):
    return next(t for t in cfg.survival.tiers if t.name == name)


def test_registry_respects_tier_limits_and_human_promotion(tmp_path):
    cfg = StrategyConfig()
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    with pytest.raises(PermissionError):   # hospice: no sub-agents at all
        reg.spawn(Child(id="", name="x", kind="variant", sleeve="stocks"), _tier(cfg, "hospice"))
    c = reg.spawn(Child(id="", name="Wide Trail", kind="variant", sleeve="stocks", overrides={"stocks.trail_atr": 6}),
                  _tier(cfg, "survival"))
    assert c.id == "wide-trail" and c.status == "shadow"
    with pytest.raises(PermissionError):   # survival allows one
        reg.spawn(Child(id="", name="second", kind="variant", sleeve="stocks"), _tier(cfg, "survival"))
    reg.spawn(Child(id="", name="second", kind="variant", sleeve="stocks"), _tier(cfg, "base"))  # base allows two
    with pytest.raises(PermissionError):
        reg.spawn(Child(id="", name="third", kind="variant", sleeve="stocks"), _tier(cfg, "base"))
    with pytest.raises(PromotionNeedsHumanError):
        reg.promote(c.id, approved_by_human=False)
    for i in range(cfg.survival.child_shadow_days):
        reg.mark(c.id, 100 + i, 100 + i * 0.5, on=date(2026, 1, 1) + timedelta(days=i))
    assert reg.get(c.id).voi == pytest.approx((113 / 100 - 1) - (106.5 / 100 - 1))
    assert [x.id for x in reg.ready_to_promote()] == [c.id]
    reg.promote(c.id, approved_by_human=True)
    again = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    assert again.get(c.id).status == "promoted" and c.id not in [x.id for x in again.active()]  # seat freed


def test_auto_retire_losers(tmp_path):
    cfg = StrategyConfig()
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    c = reg.spawn(Child(id="", name="loser", kind="variant", sleeve="stocks"), _tier(cfg, "hive"))
    for i in range(30):
        reg.mark(c.id, 100 - i, 100.0)
    retired = reg.auto_retire_losers(min_days=28)
    assert retired and retired[0].status == "retired"


def test_bounds_validation():
    bounds = load_bounds(REPO_ROOT / "config" / "bounds.toml")
    assert "stocks.trail_atr" in bounds
    assert validate_overrides({"stocks.trail_atr": 6}, bounds)[0]
    assert not validate_overrides({"stocks.trail_atr": 50}, bounds)[0]
    assert not validate_overrides({"risk.max_drawdown_halt_pct": 0.9}, bounds)[0], "limits are not tunable"
    assert not validate_overrides({}, bounds)[0]


def test_operator_iteration_with_fixed_proposals(tmp_path):
    """End-to-end on synthetic data: evaluate, verdict, spawn when the tier allows."""
    from hive.backtest.costs import ALPACA_STOCKS
    from hive.operator.iterate import operate
    from tests.test_features import bars_from_path, textbook_setup

    close, volume = textbook_setup(n_after=400)
    bars = bars_from_path(close, volume, start="2010-01-01")
    cfg = StrategyConfig()
    cfg.operator.insample_start, cfg.operator.insample_end = "2010-01-01", "2011-12-31"
    cfg.operator.outofsample_start, cfg.operator.outofsample_end = "2012-01-01", None
    inputs = {"bars": {"AAA": bars}, "classes": {"AAA": "stock"}, "bench": bars, "cost": ALPACA_STOCKS}
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    props = [Proposal(name="trail 6", hypothesis="wider trail rides longer", overrides={"stocks.trail_atr": 6.0}),
             Proposal(name="illegal", hypothesis="x", overrides={"risk.max_leverage": 5})]
    recs = operate(cfg=cfg, registry=reg, tier=_tier(cfg, "hive"), status=None, llm=None, model="m",
                   inputs=inputs, proposals_override=props, on=date(2026, 1, 1))
    assert len(recs) == 2 and recs[1]["verdict"].startswith("rejected: risk.max_leverage")
    assert "oos_cagr" in recs[0] and reg.iterations


# --- meme sleeve -------------------------------------------------------------------------
def _pair(**kw):
    base = {"chainId": "solana", "pairAddress": "p1", "baseToken": {"address": "mint1", "symbol": "DOG"},
            "priceUsd": "0.01", "liquidity": {"usd": 400_000}, "volume": {"h24": 2_000_000},
            "priceChange": {"h1": 2.0, "h6": 5.0, "h24": -30.0}, "fdv": 5_000_000,
            "pairCreatedAt": int((datetime.now(UTC) - timedelta(hours=100)).timestamp() * 1000)}
    base.update(kw)
    return base


def test_meme_filters():
    rules = MemeRules()
    ok, why = passes_filters(pair_summary(_pair()), rules)
    assert ok, why
    young = pair_summary(_pair(pairCreatedAt=int((datetime.now(UTC) - timedelta(hours=10)).timestamp() * 1000)))
    assert "too young (never buy on the bonding curve)" in passes_filters(young, rules)[1]
    thin = pair_summary(_pair(liquidity={"usd": 10_000}))
    assert "liquidity" in passes_filters(thin, rules)[1]
    pumping = pair_summary(_pair(priceChange={"h1": 5, "h6": 20, "h24": 80}))
    assert "no retrace" in passes_filters(pumping, rules)[1]


def test_meme_scout_caps_and_exits(tmp_path):
    rules = MemeRules(slippage_pct=0.0, fee_pct=0.0)
    book = MemeBook(cash=3.0)  # the sleeve's cash: 3% of a $100 account
    cands = [{"mint": f"m{i}", "symbol": f"T{i}", "price_usd": 1.0, "passes": True} for i in range(5)]
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    out = run_scout_day(book=book, rules=rules, candidates=cands, prices={}, sleeve_equity=100.0, now=t0)
    assert len(out["entries"]) == 3 and book.cash == pytest.approx(0.0)  # 1% each, max 3 open, 3% total
    # hard stop at -40%, trail at -35% from peak, time stop at 72h
    prices = {"m0": 0.5, "m1": 2.0, "m2": 1.0}
    out2 = run_scout_day(book=book, rules=rules, candidates=[], prices=prices, sleeve_equity=100.0,
                         now=t0 + timedelta(hours=10))
    reasons = {e["mint"]: e["event"] for e in out2["exits"]}
    assert reasons.get("m0") == "exit" and book.positions["m1"].took_half  # m1 doubled -> took half
    prices = {"m1": 1.2, "m2": 1.0}  # m1 fell 40% from its 2.0 peak -> trail
    out3 = run_scout_day(book=book, rules=rules, candidates=[], prices=prices, sleeve_equity=100.0,
                         now=t0 + timedelta(hours=20))
    assert any(e["mint"] == "m1" and e["reason"] == "trail" for e in out3["exits"])
    out4 = run_scout_day(book=book, rules=rules, candidates=[], prices={"m2": 1.0}, sleeve_equity=100.0,
                         now=t0 + timedelta(hours=80))
    assert any(e["mint"] == "m2" and e["reason"] == "time_stop" for e in out4["exits"])
    assert not book.positions


def test_strategy_toml_still_matches_defaults():
    assert StrategyConfig.load(REPO_ROOT / "config" / "strategy.toml").model_dump() == StrategyConfig().model_dump()


def test_jupiter_is_a_guard():
    from hive.execution.jupiter import JupiterBroker

    with pytest.raises(NotImplementedError):
        JupiterBroker()


def test_survival_rules_children_defaults():
    assert SurvivalRules().max_children == 5


def test_proposal_schema_is_typed_and_bounded_to_tunables():
    import pydantic

    from hive.operator.iterate import coerce_types, proposal_schema, to_proposals

    bounds = load_bounds(REPO_ROOT / "config" / "bounds.toml")
    schema = proposal_schema(bounds)
    parsed = schema.model_validate({"proposals": [
        {"name": "longer impulse", "hypothesis": "slower legs", "sleeve": "stocks",
         "overrides": [{"param": "stocks.impulse_window", "value": 90.0}, {"param": "stocks.trail_atr", "value": 6}]}],
        "note": ""})
    props = to_proposals(parsed, 3)
    assert props[0].overrides == {"stocks.impulse_window": 90.0, "stocks.trail_atr": 6.0}
    ov = coerce_types(StrategyConfig(), props[0].overrides)
    assert ov["stocks.impulse_window"] == 90 and isinstance(ov["stocks.impulse_window"], int)
    assert ov["stocks.trail_atr"] == 6.0
    with pytest.raises(pydantic.ValidationError):   # not a pre-registered tunable
        schema.model_validate({"proposals": [{"name": "x", "hypothesis": "y",
                                               "overrides": [{"param": "risk.max_leverage", "value": 5}]}]})
    # an empty override list is a monitor's shape; for a variant, code rejects it ("no overrides")
    empty = schema.model_validate({"proposals": [{"name": "x", "hypothesis": "y", "overrides": []}]})
    ok, why = validate_overrides(to_proposals(empty, 3)[0].overrides, bounds)
    assert not ok and "no overrides" in why
    assert to_proposals(None, 3) == []
