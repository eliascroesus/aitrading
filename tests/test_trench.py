"""Trench scout: launch filters, rug and vanish exits at zero, the shared exits, and entries
within caps; all offline with injected discovery, quotes and RugCheck."""

from datetime import UTC, datetime, timedelta

from hive.config import StrategyConfig, TrenchRules
from hive.hive.registry import Child, HiveRegistry
from hive.meme.scout import MemeBook, MemePosition
from hive.meme.trench import run_trench_round, trench_filters


def _q(mint, *, price=0.001, age_h=2.0, liq=40_000, vol1h=30_000, buys=120, sells=60, chg1h=40.0, chg5m=2.0):
    return {"chain": "solana", "mint": mint, "symbol": mint.upper(), "price_usd": price, "age_hours": age_h,
            "liquidity_usd": liq, "volume_1h_usd": vol1h, "buys_1h": buys, "sells_1h": sells, "chg_1h": chg1h,
            "chg_5m": chg5m, "volume_24h_usd": vol1h * 5}


def test_launch_filters():
    r = TrenchRules()
    assert trench_filters(_q("a"), r)[0]
    assert "too new" in trench_filters(_q("a", age_h=0.1), r)[1]
    assert "not a launch" in trench_filters(_q("a", age_h=30), r)[1]
    assert "sellers outweigh buyers" in trench_filters(_q("a", buys=50, sells=60), r)[1]
    assert "vertical candle" in trench_filters(_q("a", chg1h=900), r)[1]
    assert "dumping now" in trench_filters(_q("a", chg5m=-25), r)[1]
    assert "liquidity" in trench_filters(_q("a", liq=5_000), r)[1]


def test_round_rugs_vanishes_exits_and_enters_within_caps():
    r = TrenchRules()
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    book = MemeBook(cash=80.0)
    ago = (now - timedelta(hours=1)).isoformat()
    book.positions = {
        "rug": MemePosition("rug", "RUG", 5000.0, 0.001, 0.001, ago, 5.0),
        "gone": MemePosition("gone", "GONE", 5000.0, 0.001, 0.001, ago, 5.0, misses=2),
        "stop": MemePosition("stop", "STOP", 5000.0, 0.001, 0.0012, ago, 5.0),
        "keep": MemePosition("keep", "KEEP", 5000.0, 0.001, 0.001, ago, 5.0),
    }
    held = {"rug": _q("rug", liq=500), "stop": _q("stop", price=0.0006), "keep": _q("keep", price=0.0011)}
    fresh = {f"n{i}": _q(f"n{i}", vol1h=50_000 - i) for i in range(12)} | {"bad": _q("bad", buys=10)}
    quote_fn = lambda chain, mints: {m: held[m] for m in mints if m in held} if set(mints) <= set(held) | {"gone"} else {m: fresh[m] for m in mints if m in fresh}  # noqa: E731
    events = []
    out = run_trench_round(book=book, rules=r, now=now, journal=lambda k, p: events.append((k, p)),
                           discover_fn=lambda rules: list(fresh) + ["keep"], quote_fn=quote_fn,
                           rug_fn=lambda m: {"ok": m != "n1", "risks": ["Mint Authority"] if m == "n1" else []})
    reasons = {e["symbol"]: e.get("reason") for e in out["exits"]}
    assert reasons["RUG"].startswith("rugged") and reasons["GONE"].startswith("vanished")
    assert reasons["STOP"] == "hard_stop"                     # 0.0006 is 40% under 0.001
    assert "keep" in book.positions and "rug" not in book.positions and "gone" not in book.positions
    assert out["rugchecked"] == r.max_rugchecks               # bounded API use
    assert not any(e["mint"] == "n1" for e in out["entries"])  # failed RugCheck
    assert not any(e["mint"] == "bad" for e in out["entries"])
    assert len(book.positions) <= r.max_open
    held_value = sum(p.cost_usd for p in book.positions.values())
    assert held_value <= out["equity"] * r.total_pct + 5.0 + 1e-6
    equity_at_scan = 80.0 + 5000 * 0.0011 + 5000 * 0.0006     # cash + keep + stop, after the zero exits
    assert out["entries"] and all(e["notional"] <= equity_at_scan * r.per_token_pct + 1e-6 for e in out["entries"])
    assert any(k == "meme_entry" for k, _ in events) and any(k == "meme_exit" for k, _ in events)
    assert out["top"] and out["scanned"] == len(fresh)


def test_scouts_are_judged_on_their_own_book(tmp_path):
    cfg = StrategyConfig()
    reg = HiveRegistry(tmp_path / "hive.json", cfg.survival)
    tier = next(t for t in cfg.survival.tiers if t.name == "base")
    sc = reg.spawn(Child(id="trench-scout", name="trench scout", kind="scout", sleeve="trench"), tier, force=True)
    for i in range(cfg.survival.child_retire_days + 2):
        reg.mark(sc.id, 100.0, 100.0 + i)                     # lags the parent, flat on its own book
    assert reg.auto_retire_losers(min_days=cfg.survival.child_retire_days, max_negative_voi=cfg.survival.child_retire_voi,
                                  max_drawdown=cfg.survival.child_max_drawdown_pct) == []
    reg.mark(sc.id, 20.0, 110.0)                              # down 80% on its own book: dead
    out = reg.auto_retire_losers(min_days=cfg.survival.child_retire_days, max_negative_voi=cfg.survival.child_retire_voi,
                                 max_drawdown=cfg.survival.child_max_drawdown_pct)
    assert [c.id for c in out] == [sc.id]
