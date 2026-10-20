"""DexScreener + RugCheck scanner (keyless public APIs). Returns bounded dicts only."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from hive.config import MemeRules
from hive.data.fetch import get_json

log = logging.getLogger(__name__)

DEX_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"
DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/{chain}/{addresses}"
RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
RUG_RISK_NAMES = ("Mint Authority", "Freeze Authority", "Mutable metadata", "LP Unlocked", "Low Liquidity",
                  "Single holder ownership", "Copycat token")


def pair_summary(p: dict[str, Any]) -> dict[str, Any]:
    """Numbers and identifiers only — no names/descriptions reach any model."""
    g = lambda d, *ks: next((d[k] for k in ks if isinstance(d, dict) and k in d), None)  # noqa: E731
    created_ms = p.get("pairCreatedAt")
    age_h = (datetime.now(UTC).timestamp() * 1000 - created_ms) / 3.6e6 if created_ms else None
    return {
        "chain": p.get("chainId"), "pair": p.get("pairAddress"), "mint": (p.get("baseToken") or {}).get("address"),
        "symbol": (p.get("baseToken") or {}).get("symbol"), "price_usd": float(p.get("priceUsd") or 0) or None,
        "liquidity_usd": float(g(p.get("liquidity") or {}, "usd") or 0),
        "volume_24h_usd": float(g(p.get("volume") or {}, "h24") or 0),
        "chg_1h": float(g(p.get("priceChange") or {}, "h1") or 0), "chg_6h": float(g(p.get("priceChange") or {}, "h6") or 0),
        "chg_24h": float(g(p.get("priceChange") or {}, "h24") or 0), "fdv_usd": float(p.get("fdv") or 0),
        "age_hours": age_h,
        "chg_5m": float(g(p.get("priceChange") or {}, "m5") or 0),
        "volume_1h_usd": float(g(p.get("volume") or {}, "h1") or 0),
        "buys_1h": int(g((p.get("txns") or {}).get("h1") or {}, "buys") or 0),
        "sells_1h": int(g((p.get("txns") or {}).get("h1") or {}, "sells") or 0),
    }


def passes_filters(s: dict[str, Any], rules: MemeRules) -> tuple[bool, list[str]]:
    why = []
    if s.get("chain") != rules.chain:
        why.append("chain")
    if s.get("age_hours") is None or s["age_hours"] < rules.min_age_hours:
        why.append("too young (never buy on the bonding curve)")
    if s.get("liquidity_usd", 0) < rules.min_liquidity_usd:
        why.append("liquidity")
    if s.get("volume_24h_usd", 0) < rules.min_volume_24h_usd:
        why.append("volume")
    if s.get("chg_24h", 0) > rules.max_24h_change_pct:
        why.append("no retrace")
    if s.get("chg_6h", 0) < rules.min_6h_change_pct or s.get("chg_1h", 0) < rules.min_1h_change_pct:
        why.append("not reclaiming")
    if not s.get("price_usd"):
        why.append("no price")
    return (not why), why


def rugcheck_summary(mint: str) -> dict[str, Any]:
    """Best effort. Returns {"ok": bool, "score": int|None, "risks": [names]}; failures are *not* ok."""
    try:
        d = get_json(RUGCHECK.format(mint=mint), timeout=20, retries=1)
    except Exception as exc:
        return {"ok": False, "score": None, "risks": [f"rugcheck unavailable: {str(exc)[:60]}"]}
    risks = [r.get("name", "") for r in (d.get("risks") or [])]
    bad = [r for r in risks if any(r.lower().startswith(n.lower()) for n in RUG_RISK_NAMES)]
    return {"ok": not bad, "score": d.get("score"), "risks": risks}


def scan(rules: MemeRules, *, max_tokens: int = 60) -> list[dict[str, Any]]:
    """Discover candidate pairs on the chain and apply the hard filters."""
    addrs: list[str] = []
    for url in (DEX_PROFILES, DEX_BOOSTS):
        try:
            for t in get_json(url, timeout=20, retries=1) or []:
                if t.get("chainId") == rules.chain and t.get("tokenAddress"):
                    addrs.append(t["tokenAddress"])
        except Exception as exc:
            log.warning("dexscreener discovery failed (%s): %s", url, exc)
    addrs = list(dict.fromkeys(addrs))[:max_tokens]
    out: list[dict[str, Any]] = []
    for i in range(0, len(addrs), 30):
        chunk = ",".join(addrs[i:i + 30])
        try:
            pairs = get_json(DEX_TOKENS.format(chain=rules.chain, addresses=chunk), timeout=20, retries=1) or []
        except Exception as exc:
            log.warning("dexscreener tokens failed: %s", exc)
            continue
        best: dict[str, dict[str, Any]] = {}
        for p in pairs:
            s = pair_summary(p)
            if s["mint"] and (s["mint"] not in best or s["liquidity_usd"] > best[s["mint"]]["liquidity_usd"]):
                best[s["mint"]] = s
        for s in best.values():
            ok, why = passes_filters(s, rules)
            s["passes"], s["why"] = ok, why
            if ok and rules.require_rugcheck:
                rc = rugcheck_summary(s["mint"])
                s["rugcheck"] = rc
                if not rc["ok"]:
                    s["passes"], s["why"] = False, [f"rugcheck: {', '.join(rc['risks'][:3])}"]
            out.append(s)
    return out


def current_prices(chain: str, mints: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(mints), 30):
        try:
            pairs = get_json(DEX_TOKENS.format(chain=chain, addresses=",".join(mints[i:i + 30])), timeout=20, retries=1) or []
        except Exception as exc:
            log.warning("dexscreener prices failed: %s", exc)
            continue
        for p in pairs:
            s = pair_summary(p)
            if s["mint"] and s["price_usd"] and (s["mint"] not in out):
                out[s["mint"]] = s["price_usd"]
    return out
