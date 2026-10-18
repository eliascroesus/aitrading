"""Precomputed fundamental facts (numbers only) for the analysts and the earnings blackout."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from hive.data.fetch import get_json

log = logging.getLogger(__name__)
FINNHUB_EARNINGS = "https://finnhub.io/api/v1/calendar/earnings"


def days_until(target: date | None, as_of: date) -> int | None:
    return None if target is None else (target - as_of).days


def finnhub_next_earnings(symbol: str, api_key: str, *, as_of: date, horizon_days: int = 45) -> date | None:
    params = {"symbol": symbol.upper(), "from": as_of.isoformat(),
              "to": (as_of + timedelta(days=horizon_days)).isoformat(), "token": api_key}
    try:
        data = get_json(FINNHUB_EARNINGS, params=params, timeout=30, retries=1)
    except Exception as exc:
        log.warning("finnhub earnings failed for %s: %s", symbol, exc)
        return None
    return parse_finnhub_earnings(data, as_of)


def parse_finnhub_earnings(data: dict, as_of: date) -> date | None:
    rows = (data or {}).get("earningsCalendar", []) or []
    dates = []
    for r in rows:
        try:
            d = datetime.strptime(str(r.get("date")), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if d >= as_of:
            dates.append(d)
    return min(dates) if dates else None


def yfinance_next_earnings(symbol: str, *, as_of: date) -> date | None:
    try:
        import yfinance as yf

        cal = yf.Ticker(symbol).calendar or {}
        raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if not raw:
            return None
        cands = raw if isinstance(raw, list | tuple) else [raw]
        dates = [c.date() if hasattr(c, "date") else c for c in cands]
        dates = [d for d in dates if isinstance(d, date) and d >= as_of]
        return min(dates) if dates else None
    except Exception as exc:
        log.warning("yfinance calendar failed for %s: %s", symbol, exc)
        return None


def fundamentals_snapshot(symbol: str, *, as_of: date, finnhub_key: str | None) -> dict[str, object]:
    """Numbers only. Missing data stays missing (never guessed)."""
    nxt = None
    if finnhub_key:
        nxt = finnhub_next_earnings(symbol, finnhub_key, as_of=as_of)
    if nxt is None:
        nxt = yfinance_next_earnings(symbol, as_of=as_of)
    return {
        "as_of": as_of.isoformat(),
        "next_earnings_date": nxt.isoformat() if nxt else None,
        "earnings_in_days": days_until(nxt, as_of),
    }
