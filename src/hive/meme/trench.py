"""Trench scout: paper-trades brand-new Solana launches (keyless public APIs, no wallet).

Discovery: GeckoTerminal's new-pools feed plus DexScreener's latest token profiles and
boosts. Every candidate is re-quoted on DexScreener (one price source for entries and
exits), filtered for a live launch (age, liquidity, last-hour volume and buyer flow, no
vertical candle, not dumping now) and checked on RugCheck. Positions live in a paper book
with its own bankroll; exits are the memecoin scout's (hard stop, trail, take half, time
stop) evaluated at each scan, plus two the trenches need: liquidity pulled means rugged and
a token that stops quoting for a few scans has vanished; both exit at zero. Nothing here
can place a real order.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hive.config import MemeRules, TrenchRules
from hive.meme.scout import MemeBook, run_scout_day

log = logging.getLogger(__name__)

GECKO_NEW_POOLS = "https://api.geckoterminal.com/api/v2/networks/{chain}/new_pools?page={page}"


def as_meme_rules(r: TrenchRules) -> MemeRules:
    """The exit and sizing rules the shared paper book understands."""
    return MemeRules(enabled=True, chain=r.chain, per_token_pct=r.per_token_pct, total_pct=r.total_pct,
                     max_open=r.max_open, hard_stop_pct=r.hard_stop_pct, trail_pct=r.trail_pct,
                     take_half_at_pct=r.take_half_at_pct, time_stop_hours=r.time_stop_hours,
                     slippage_pct=r.slippage_pct, fee_pct=r.fee_pct, require_rugcheck=r.require_rugcheck)


def trench_filters(s: dict[str, Any], r: TrenchRules) -> tuple[bool, list[str]]:
    why: list[str] = []
    if s.get("chain") != r.chain:
        why.append("chain")
    age_h = s.get("age_hours")
    if age_h is None:
        why.append("no launch time")
    elif age_h * 60 < r.min_age_minutes:
        why.append("too new")
    elif age_h > r.max_age_hours:
        why.append("not a launch")
    if (s.get("liquidity_usd") or 0) < r.min_liquidity_usd:
        why.append("liquidity")
    if (s.get("volume_1h_usd") or 0) < r.min_volume_1h_usd:
        why.append("1h volume")
    buys, sells = int(s.get("buys_1h") or 0), int(s.get("sells_1h") or 0)
    if buys < r.min_buys_1h:
        why.append("too few buyers")
    if buys / max(sells, 1) < r.min_buy_sell_ratio:
        why.append("sellers outweigh buyers")
    if (s.get("chg_1h") or 0) > r.max_chg_1h_pct:
        why.append("vertical candle")
    if (s.get("chg_5m") or 0) < r.min_chg_5m_pct:
        why.append("dumping now")
    if not s.get("price_usd"):
        why.append("no price")
    return (not why), why


def discover(r: TrenchRules) -> list[str]:  # pragma: no cover - network
    from hive.data.fetch import get_json
    from hive.meme.scanner import DEX_BOOSTS, DEX_PROFILES

    mints: list[str] = []
    for page in (1, 2):
        try:
            d = get_json(GECKO_NEW_POOLS.format(chain=r.chain, page=page), timeout=20, retries=1,
                         headers={"Accept": "application/json"})
            for pool in (d or {}).get("data") or []:
                tid = (((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id") or ""
                if tid.startswith(r.chain + "_"):
                    mints.append(tid.split("_", 1)[1])
        except Exception as exc:
            log.warning("geckoterminal new pools failed: %s", exc)
    for url in (DEX_PROFILES, DEX_BOOSTS.replace("/top/", "/latest/")):
        try:
            for t in get_json(url, timeout=20, retries=1) or []:
                if t.get("chainId") == r.chain and t.get("tokenAddress"):
                    mints.append(t["tokenAddress"])
        except Exception as exc:
            log.warning("dexscreener discovery failed (%s): %s", url, exc)
    return list(dict.fromkeys(mints))[: r.max_scan]


def quote(chain: str, mints: list[str]) -> dict[str, dict[str, Any]]:  # pragma: no cover - network
    """Best-liquidity DexScreener pair summary per mint."""
    from hive.data.fetch import get_json
    from hive.meme.scanner import DEX_TOKENS, pair_summary

    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(mints), 30):
        try:
            pairs = get_json(DEX_TOKENS.format(chain=chain, addresses=",".join(mints[i:i + 30])), timeout=20, retries=1) or []
        except Exception as exc:
            log.warning("dexscreener quotes failed: %s", exc)
            continue
        for p in pairs:
            s = pair_summary(p)
            m = s.get("mint")
            if m and (m not in out or s["liquidity_usd"] > out[m]["liquidity_usd"]):
                out[m] = s
    return out


def run_trench_round(*, book: MemeBook, rules: TrenchRules, now: datetime | None = None,
                     journal: Callable[[str, dict[str, Any]], Any] | None = None,
                     discover_fn: Callable[[TrenchRules], list[str]] = discover,
                     quote_fn: Callable[[str, list[str]], dict[str, dict[str, Any]]] = quote,
                     rug_fn: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """One scan: rugs and vanished tokens out at zero, the shared exits on fresh quotes, then
    entries from the launches that pass the filters and RugCheck."""
    from hive.meme.scanner import rugcheck_summary

    now = now or datetime.now(UTC)
    log_ = journal or (lambda k, p: None)
    rug_fn = rug_fn or rugcheck_summary
    held = list(book.positions)
    held_q = quote_fn(rules.chain, held) if held else {}
    dead: list[dict[str, Any]] = []
    for mint in held:
        pos = book.positions[mint]
        q = held_q.get(mint)
        reason = None
        if q is None or not q.get("price_usd"):
            pos.misses += 1
            if pos.misses >= rules.vanish_after_misses:
                reason = f"vanished: no quote for {pos.misses} scans"
        else:
            pos.misses = 0
            if (q.get("liquidity_usd") or 0) < rules.rugged_liquidity_usd:
                reason = f"rugged: liquidity ${q.get('liquidity_usd') or 0:,.0f}"
        if reason:
            ev = {"mint": mint, "symbol": pos.symbol, "event": "exit", "reason": reason, "price": 0.0, "proceeds": 0.0,
                  "pnl_approx": -pos.cost_usd * (0.5 if pos.took_half else 1.0)}
            book.history.append(ev)
            dead.append(ev)
            log_("meme_exit", ev)
            del book.positions[mint]
    prices = {m: q["price_usd"] for m, q in held_q.items() if m in book.positions and q.get("price_usd")}
    found = [m for m in discover_fn(rules) if m not in book.positions]
    quotes = quote_fn(rules.chain, found) if found else {}
    cands: list[dict[str, Any]] = []
    for s in quotes.values():
        ok, why = trench_filters(s, rules)
        cands.append({**s, "passes": ok, "why": why})
    cands.sort(key=lambda c: (not c["passes"], -(c.get("volume_1h_usd") or 0)))
    checked = 0
    for c in cands:
        if not c["passes"] or not rules.require_rugcheck:
            continue
        if checked >= rules.max_rugchecks:
            c["passes"], c["why"] = False, ["not rug-checked this scan"]
            continue
        checked += 1
        rc = rug_fn(c["mint"])
        c["rugcheck"] = rc
        if not rc.get("ok"):
            c["passes"], c["why"] = False, [f"rugcheck: {', '.join((rc.get('risks') or [])[:3])}"]
    equity_before = book.equity(prices)
    out = run_scout_day(book=book, rules=as_meme_rules(rules), candidates=cands, prices=prices,
                        sleeve_equity=equity_before, now=now, journal=log_)
    out["exits"] = dead + out["exits"]
    open_rows = [{"mint": m, "symbol": p.symbol, "entry": p.entry_price, "last": prices.get(m, p.entry_price),
                  "value": p.qty * prices.get(m, p.entry_price), "cost": p.cost_usd, "entered_at": p.entered_at,
                  "took_half": p.took_half} for m, p in book.positions.items()]
    rejects: dict[str, int] = {}
    for c in cands:
        for w in c.get("why") or []:
            rejects[w.split(":")[0]] = rejects.get(w.split(":")[0], 0) + 1
    return {"ts": now.isoformat(timespec="seconds"), "scanned": len(cands), "discovered": len(found),
            "passed": sum(1 for c in cands if c["passes"]), "rugchecked": checked, "rejects": rejects,
            "entries": out["entries"], "exits": out["exits"], "equity": out["equity"], "cash": book.cash,
            "n_open": out["n_open"], "open": open_rows,
            "top": [{k: c.get(k) for k in ("symbol", "mint", "age_hours", "liquidity_usd", "volume_1h_usd", "buys_1h",
                                           "sells_1h", "chg_1h", "chg_5m", "passes", "why")} for c in cands[:12]]}
