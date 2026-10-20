"""Hyperliquid public market data (the ``/info`` endpoint): no account, no keys, no orders.

Used only by the paper desk: the universe ranked by day volume, daily candles, and the
hourly funding history a long position would have paid. Every call is a read.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

HL_INFO = "https://api.hyperliquid.xyz/info"


def _post(payload: dict[str, Any]) -> Any:  # pragma: no cover - network
    from hive.data.fetch import post_json

    return post_json(HL_INFO, payload, timeout=30, retries=2)


def parse_universe(payload: Any, *, top: int) -> list[dict[str, Any]]:
    """``metaAndAssetCtxs`` -> the ``top`` listed perps by day notional volume."""
    meta, ctxs = payload[0], payload[1]
    rows = []
    for u, c in zip(meta.get("universe") or [], ctxs or [], strict=False):
        if u.get("isDelisted"):
            continue
        rows.append({"coin": u.get("name"), "max_leverage": u.get("maxLeverage"),
                     "volume_usd": float(c.get("dayNtlVlm") or 0.0), "funding_hourly": float(c.get("funding") or 0.0),
                     "mark": float(c.get("markPx") or 0.0), "open_interest": float(c.get("openInterest") or 0.0)})
    rows.sort(key=lambda r: -r["volume_usd"])
    return [r for r in rows if r["coin"]][:top]


def parse_candles(payload: list[dict[str, Any]]) -> pd.DataFrame:
    """``candleSnapshot`` (1d) -> bars indexed by UTC day; the forming day is dropped."""
    rows = [{"date": pd.Timestamp(int(c["t"]), unit="ms").normalize(), "open": float(c["o"]), "high": float(c["h"]),
             "low": float(c["l"]), "close": float(c["c"]), "volume": float(c.get("v") or 0.0)} for c in payload or []]
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "adj_close", "volume"])
    df = pd.DataFrame(rows).drop_duplicates("date").set_index("date").sort_index()
    df["adj_close"] = df["close"]
    today = pd.Timestamp(datetime.now(UTC).date())
    df = df[df.index < today]
    return df[["open", "high", "low", "close", "adj_close", "volume"]]


def parse_funding(payload: list[dict[str, Any]], since_ms: int) -> float:
    """Sum of the hourly funding rates paid after ``since_ms`` (positive: longs pay shorts)."""
    return float(sum(float(f.get("fundingRate") or 0.0) for f in payload or [] if int(f.get("time") or 0) > since_ms))


def universe(top: int) -> list[dict[str, Any]]:  # pragma: no cover - network
    return parse_universe(_post({"type": "metaAndAssetCtxs"}), top=top)


def candles(coin: str, days: int = 900) -> pd.DataFrame:  # pragma: no cover - network
    now = int(datetime.now(UTC).timestamp() * 1000)
    return parse_candles(_post({"type": "candleSnapshot",
                                "req": {"coin": coin, "interval": "1d", "startTime": now - days * 86_400_000, "endTime": now}}))


def funding_since(coin: str, since_ms: int) -> float:  # pragma: no cover - network
    return parse_funding(_post({"type": "fundingHistory", "coin": coin, "startTime": since_ms}), since_ms)
