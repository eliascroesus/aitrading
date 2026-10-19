"""Keyless social feeds for the quarantined reader: StockTwits symbol streams and Reddit search.

Everything returned here is untrusted text. It goes only to the quarantined reader,
which returns bounded fields (events, sentiment, attention spike, injection flag);
nothing here is a signal on its own, and the decision model never sees it.
"""

from __future__ import annotations

import hashlib
import logging
from urllib.parse import quote_plus

from hive.data.fetch import get_json, get_text

log = logging.getLogger(__name__)

STOCKTWITS = "https://api.stocktwits.com/api/2/streams/symbol/{sym}.json"
REDDIT_RSS = "https://www.reddit.com/search.rss?q={q}&sort=new&t=week"


def _sid(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def stocktwits_symbol(symbol: str, asset_class: str) -> str:
    """StockTwits quotes crypto as ``BTC.X``."""
    return f"{symbol.upper()}.X" if asset_class == "crypto" else symbol.upper()


def parse_stocktwits(payload: dict, *, max_items: int = 20) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in (payload.get("messages") or [])[:max_items]:
        body = (m.get("body") or "").strip().replace("\n", " ")
        if not body:
            continue
        tag = (((m.get("entities") or {}).get("sentiment") or {}).get("basic") or "").lower()
        created = m.get("created_at", "")
        label = f"social:stocktwits{(' ' + tag) if tag else ''}"
        out.append((f"st-{m.get('id', _sid(body))}", f"[{label}] {body[:280]} ({created})"))
    return out


def stocktwits_messages(symbol: str, asset_class: str = "stock", *, max_items: int = 20) -> list[tuple[str, str]]:
    url = STOCKTWITS.format(sym=stocktwits_symbol(symbol, asset_class))
    try:
        data = get_json(url, timeout=20, retries=1)
    except Exception as exc:
        log.info("stocktwits unavailable for %s: %s", symbol, exc)
        return []
    return parse_stocktwits(data or {}, max_items=max_items)


def parse_reddit_rss(xml_text: str, *, max_items: int = 15) -> list[tuple[str, str]]:
    import feedparser

    feed = feedparser.parse(xml_text)
    out: list[tuple[str, str]] = []
    for e in feed.entries[:max_items]:
        title = (getattr(e, "title", "") or "").strip()
        published = getattr(e, "published", "") or getattr(e, "updated", "") or ""
        if title:
            out.append((f"rd-{_sid(title + published)}", f"[social:reddit] {title[:200]} ({published})"))
    return out


def reddit_posts(query: str, *, max_items: int = 15) -> list[tuple[str, str]]:
    url = REDDIT_RSS.format(q=quote_plus(query))
    try:
        text = get_text(url, timeout=20, retries=1)
    except Exception as exc:
        log.info("reddit unavailable for %r: %s", query, exc)
        return []
    return parse_reddit_rss(text, max_items=max_items)


def social_items(symbol: str, asset_class: str = "stock", *, max_items: int = 25) -> list[tuple[str, str]]:
    """StockTwits stream plus Reddit posts for the symbol, newest first, capped."""
    q = f"{symbol} crypto" if asset_class == "crypto" else f"${symbol} stock"
    items = stocktwits_messages(symbol, asset_class) + reddit_posts(q)
    return items[:max_items]
