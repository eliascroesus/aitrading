"""Headline sources for the quarantined news reader. Headlines only, never bodies.

Everything returned here is *untrusted text*: it goes only to the quarantined
reader after ticker/company anonymisation.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

from hive.data.fetch import get_json, get_text

log = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
FINNHUB_NEWS = "https://finnhub.io/api/v1/company-news"


def _sid(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def parse_google_rss(xml_text: str, *, max_items: int = 25) -> list[tuple[str, str]]:
    import feedparser

    feed = feedparser.parse(xml_text)
    out: list[tuple[str, str]] = []
    for e in feed.entries[:max_items]:
        title = (getattr(e, "title", "") or "").strip()
        source = getattr(getattr(e, "source", None), "title", "") or ""
        published = getattr(e, "published", "") or ""
        if title:
            out.append((_sid(title + published), f"{title} — {source} ({published})"))
    return out


def google_news_headlines(query: str, *, days: int = 5, max_items: int = 25) -> list[tuple[str, str]]:
    q = quote_plus(f"{query} when:{days}d")
    try:
        text = get_text(GOOGLE_NEWS_RSS.format(q=q), timeout=30, retries=1)
    except Exception as exc:
        log.warning("google news fetch failed for %r: %s", query, exc)
        return []
    return parse_google_rss(text, max_items=max_items)


def finnhub_company_news(symbol: str, api_key: str, *, days: int = 5, max_items: int = 25) -> list[tuple[str, str]]:
    today = datetime.now(UTC).date()
    params = {"symbol": symbol.upper(), "from": (today - timedelta(days=days)).isoformat(),
              "to": today.isoformat(), "token": api_key}
    try:
        rows = get_json(FINNHUB_NEWS, params=params, timeout=30, retries=1)
    except Exception as exc:
        log.warning("finnhub news failed for %s: %s", symbol, exc)
        return []
    out = []
    for r in (rows or [])[:max_items]:
        headline = str(r.get("headline", "")).strip()
        if headline:
            out.append((str(r.get("id", _sid(headline))), f"{headline} — {r.get('source', '')}"))
    return out
