"""CoinMetrics community data: daily close-only prices since inception.

Public CSV mirror: ``https://raw.githubusercontent.com/coinmetrics/data/master/csv/<asset>.csv``
Columns of interest: ``time``, ``PriceUSD``, ``volume_reported_spot_usd_1d``.
Close-only, so ``open/high/low`` are filled with ``close`` (ATR falls back to
close-to-close ranges downstream). Good for long-horizon trend tests, not for
intraday stops.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd

from hive.data.bars import normalize_bars
from hive.data.fetch import get_text

RAW_URL = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{asset}.csv"
SUPPORTED = ("btc", "eth", "sol", "ltc", "xrp", "ada", "doge", "link", "avax", "bnb")


def parse_csv(text_or_path: str | Path) -> pd.DataFrame:
    src = Path(text_or_path) if isinstance(text_or_path, Path) else StringIO(text_or_path)
    raw = pd.read_csv(
        src, usecols=lambda c: c in ("time", "PriceUSD", "volume_reported_spot_usd_1d"),
        low_memory=False,
    )
    raw = raw.rename(columns={"volume_reported_spot_usd_1d": "volume"})
    raw = raw.dropna(subset=["PriceUSD"])
    return normalize_bars(raw, date_col="time")


def fetch(asset: str) -> pd.DataFrame:
    asset = asset.lower()
    text = get_text(RAW_URL.format(asset=asset), timeout=120)
    return parse_csv(text)
