"""Hyperliquid public info API: daily candles, funding, and perp metadata.

No key required. ``candleSnapshot`` returns at most ~5000 candles per call.
Mainnet: https://api.hyperliquid.xyz/info  Testnet: https://api.hyperliquid-testnet.xyz/info
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from hive.data.bars import normalize_bars
from hive.data.fetch import post_json

MAINNET_INFO = "https://api.hyperliquid.xyz/info"
TESTNET_INFO = "https://api.hyperliquid-testnet.xyz/info"


def candles_to_bars(candles: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert ``candleSnapshot`` rows (keys t,o,h,l,c,v) to a bars frame."""
    if not candles:
        return normalize_bars(pd.DataFrame({"date": [], "close": []}))
    df = pd.DataFrame(candles)
    df["date"] = pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return normalize_bars(df[["date", "open", "high", "low", "close", "volume"]])


def fetch_daily(coin: str, *, days: int = 2000, testnet: bool = False) -> pd.DataFrame:
    url = TESTNET_INFO if testnet else MAINNET_INFO
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    payload = {
        "type": "candleSnapshot",
        "req": {"coin": coin.upper(), "interval": "1d", "startTime": start_ms, "endTime": end_ms},
    }
    return candles_to_bars(post_json(url, payload, timeout=60))


def fetch_meta(*, testnet: bool = False) -> dict[str, Any]:
    """Perp universe (szDecimals, maxLeverage per coin)."""
    url = TESTNET_INFO if testnet else MAINNET_INFO
    return post_json(url, {"type": "meta"}, timeout=30)


def fetch_funding_history(coin: str, *, days: int = 30, testnet: bool = False) -> pd.DataFrame:
    url = TESTNET_INFO if testnet else MAINNET_INFO
    end_ms = int(time.time() * 1000)
    rows = post_json(
        url,
        {"type": "fundingHistory", "coin": coin.upper(), "startTime": end_ms - days * 86_400_000},
        timeout=30,
    )
    if not rows:
        return pd.DataFrame(columns=["funding_rate", "premium"])
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["time"].astype("int64"), unit="ms", utc=True)
    out = pd.DataFrame(
        {"funding_rate": df["fundingRate"].astype(float), "premium": df["premium"].astype(float)}
    )
    return out.sort_index()
