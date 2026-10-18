"""Yahoo Finance via ``yfinance`` — free daily bars for stocks/ETFs/crypto.

Reliability caveat: unofficial API, rate-limited, occasionally changes shape.
We only use it to *refresh* the cache; backtests read the cache.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import pandas as pd

from hive.data.bars import normalize_bars

log = logging.getLogger(__name__)


def fetch_daily(symbols: Iterable[str], start: str = "2000-01-01") -> dict[str, pd.DataFrame]:
    import yfinance as yf  # imported lazily: heavy and network-bound

    syms = [s.upper() for s in symbols]
    if not syms:
        return {}
    raw = yf.download(
        syms, start=start, interval="1d", auto_adjust=False, actions=False,
        group_by="ticker", threads=True, progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        for sym in syms:
            if sym not in raw.columns.get_level_values(0):
                continue
            df = raw[sym].dropna(how="all")
            if df.empty:
                continue
            out[sym] = normalize_bars(df)
    else:  # single symbol -> flat columns
        out[syms[0]] = normalize_bars(raw.dropna(how="all"))
    return out


# Yahoo disambiguates a few tickers with a CoinGecko id suffix.
YAHOO_CRYPTO_OVERRIDES = {"PEPE": "PEPE24478-USD"}


def yahoo_crypto_symbol(coin: str) -> str:
    """``BTC`` -> ``BTC-USD`` (``PEPE`` -> ``PEPE24478-USD``)."""
    coin = coin.upper()
    if coin in YAHOO_CRYPTO_OVERRIDES:
        return YAHOO_CRYPTO_OVERRIDES[coin]
    return coin if coin.endswith("-USD") else f"{coin}-USD"
