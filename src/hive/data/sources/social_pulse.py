"""The social pulse: what the crowd is talking about, firm-wide, every lab round.

Keyless sources: StockTwits' trending symbols, cashtags in the hot posts of a few Reddit
communities (RSS), and DexScreener's paid token boosts (labelled as paid). X is read only
when the owner adds a paid X API bearer token (``X_BEARER_TOKEN``): then the pulse asks X
for the last day's post counts of the names trending elsewhere.

Only symbols and counts are kept. No post text is stored or passed on, so nothing here
can carry instructions into a model; the news desk's quarantined reader stays the only
place untrusted text is read.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)

STOCKTWITS_TRENDING = "https://api.stocktwits.com/api/2/trending/symbols.json"
REDDIT_HOT = "https://www.reddit.com/r/{sub}/hot.rss?limit=50"
SUBREDDITS = ("wallstreetbets", "stocks", "CryptoCurrency", "SatoshiStreetBets", "solana", "memecoins")
DEX_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"
X_COUNTS = "https://api.x.com/2/tweets/counts/recent?query={q}&granularity=day"
CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")
NOT_TICKERS = {"USD", "USDT", "USDC", "CAD", "EUR", "AUD", "GBP", "K", "M", "B"}


def parse_stocktwits_trending(payload: dict[str, Any], *, top: int = 20) -> list[dict[str, Any]]:
    out = []
    for s in (payload.get("symbols") or [])[:top]:
        sym = str(s.get("symbol") or "").upper()
        if sym:
            out.append({"symbol": sym.removesuffix(".X"), "crypto": sym.endswith(".X"),
                        "watchers": int(s.get("watchlist_count") or 0)})
    return out


def cashtags(titles: list[str]) -> Counter:
    c: Counter = Counter()
    for t in titles:
        for tag in {m.upper() for m in CASHTAG.findall(t or "")}:
            if tag not in NOT_TICKERS and not tag.isdigit():
                c[tag] += 1
    return c


def parse_reddit_titles(xml_text: str) -> list[str]:
    import feedparser

    return [(getattr(e, "title", "") or "") for e in feedparser.parse(xml_text).entries]


def parse_boosts(payload: list[dict[str, Any]], *, top: int = 15) -> list[dict[str, Any]]:
    out = []
    for b in (payload or [])[:top]:
        out.append({"chain": b.get("chainId"), "token": b.get("tokenAddress"), "boost": float(b.get("totalAmount") or b.get("amount") or 0)})
    return out


def cross_source(st: list[dict[str, Any]], reddit: Counter, x: dict[str, int]) -> list[dict[str, Any]]:
    """Names that more than one source is talking about, the strongest signal of attention."""
    seen: dict[str, set[str]] = {}
    for r in st:
        seen.setdefault(r["symbol"], set()).add("stocktwits")
    for sym in reddit:
        seen.setdefault(sym, set()).add("reddit")
    for sym, n in x.items():
        if n:
            seen.setdefault(sym, set()).add("x")
    rows = [{"symbol": s, "sources": sorted(v), "reddit_mentions": reddit.get(s, 0), "x_posts_24h": x.get(s)}
            for s, v in seen.items() if len(v) >= 2]
    return sorted(rows, key=lambda r: (-len(r["sources"]), -r["reddit_mentions"]))[:20]


def merge_reddit(by_sub: dict[str, dict[str, Any]]) -> Counter:
    """Each community counted once, at its most recent read."""
    total: Counter = Counter()
    for v in by_sub.values():
        total.update(v.get("counts") or {})
    return total


def pulse(*, x_token: str | None = None, fetch_json=None, fetch_text=None, subreddits: tuple[str, ...] = SUBREDDITS,
          known_reddit: dict[str, dict[str, Any]] | None = None, pause_s: float = 0.0) -> dict[str, Any]:
    """One pulse. ``subreddits`` are read now (Reddit rate-limits bursts, so the caller rotates
    through a couple per round); ``known_reddit`` carries each community's most recent earlier
    read, so the merged Reddit picture covers all of them."""
    import time

    from hive.data.fetch import get_json, get_text

    fetch_json = fetch_json or get_json
    fetch_text = fetch_text or get_text
    errors: list[str] = []
    st: list[dict[str, Any]] = []
    try:
        st = parse_stocktwits_trending(fetch_json(STOCKTWITS_TRENDING, timeout=20, retries=1) or {})
    except Exception as exc:
        errors.append(f"stocktwits: {str(exc)[:80]}")
    by_sub: dict[str, dict[str, Any]] = dict(known_reddit or {})
    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    for i, sub in enumerate(subreddits):
        if i and pause_s:
            time.sleep(pause_s)
        try:
            t = parse_reddit_titles(fetch_text(REDDIT_HOT.format(sub=sub), timeout=20, retries=1))
            by_sub[sub] = {"ts": now_iso, "posts": len(t), "counts": dict(cashtags(t))}
        except Exception as exc:
            errors.append(f"reddit r/{sub}: {str(exc)[:60]}")
    reddit = merge_reddit(by_sub)
    boosts: list[dict[str, Any]] = []
    try:
        boosts = parse_boosts(fetch_json(DEX_BOOSTS, timeout=20, retries=1) or [])
    except Exception as exc:
        errors.append(f"dexscreener: {str(exc)[:80]}")
    x: dict[str, int] = {}
    x_status = "not connected: add a paid X API bearer token as the X_BEARER_TOKEN secret"
    if x_token:
        names = [r["symbol"] for r in st[:6]] + [s for s, _ in reddit.most_common(6)]
        for sym in list(dict.fromkeys(names))[:10]:
            try:
                d = fetch_json(X_COUNTS.format(q=quote(f"${sym} -is:retweet")), timeout=20, retries=1,
                               headers={"Authorization": f"Bearer {x_token}"})
                x[sym] = int(((d or {}).get("meta") or {}).get("total_tweet_count") or 0)
            except Exception as exc:
                errors.append(f"x {sym}: {str(exc)[:60]}")
        x_status = f"connected: {len(x)} names counted" if x else "connected, but every call failed (tier or quota?)"
    return {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "stocktwits": st,
            "reddit": [{"symbol": s, "mentions": n} for s, n in reddit.most_common(20)],
            "reddit_posts_read": sum(int(v.get("posts") or 0) for v in by_sub.values()), "reddit_by_sub": by_sub,
            "dex_boosts": boosts, "x": x, "x_status": x_status, "cross": cross_source(st, reddit, x), "errors": errors[:10]}
