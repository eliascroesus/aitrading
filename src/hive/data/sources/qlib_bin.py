"""Reader for Qlib's binary daily dataset layout (offline backtest data).

Layout::

    <root>/calendars/day.txt            one ISO date per line
    <root>/instruments/all.txt          SYMBOL \t start \t end [\t feature-dir]
    <root>/features/<dir>/<field>.day.bin

Each ``.bin`` is little-endian float32: the first value is the index into the
calendar of the first observation, followed by one value per calendar day.
Qlib's Yahoo collector stores split/dividend-adjusted prices and a ``factor``
column; we expose both the stored (adjusted) series and ``raw = close / factor``
when the factor is available. Which one is "dollar" is established empirically
by ``probe`` and documented in docs/DATA.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from hive.data.bars import normalize_bars

FIELDS = ("open", "high", "low", "close", "volume", "factor")


class QlibDataset:
    def __init__(self, root: Path):
        self.root = Path(root)
        cal_path = self.root / "calendars" / "day.txt"
        self.calendar = pd.DatetimeIndex(pd.to_datetime(cal_path.read_text().split()))
        self.instruments = self._read_instruments(self.root / "instruments" / "all.txt")

    @staticmethod
    def _read_instruments(path: Path) -> pd.DataFrame:
        rows = []
        for line in path.read_text().splitlines():
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            sym, start, end = parts[0], parts[1], parts[2]
            # Feature directories are lower-cased on disk ("_SPY" -> "_spy").
            feat_dir = (parts[3] if len(parts) > 3 else sym).lower()
            rows.append({"symbol": sym.upper(), "start": start, "end": end, "dir": feat_dir})
        df = pd.DataFrame(rows)
        df["start"] = pd.to_datetime(df["start"])
        df["end"] = pd.to_datetime(df["end"])
        return df.set_index("symbol")

    def symbols(self) -> list[str]:
        return list(self.instruments.index)

    def _feature_dir(self, symbol: str) -> Path:
        row = self.instruments.loc[symbol.upper()]
        return self.root / "features" / str(row["dir"])

    def read_field(self, symbol: str, field: str) -> pd.Series:
        path = self._feature_dir(symbol) / f"{field}.day.bin"
        if not path.exists():
            raise FileNotFoundError(path)
        arr = np.fromfile(path, dtype="<f4")
        if arr.size == 0:
            return pd.Series(dtype="float64", name=field)
        start = int(arr[0])
        values = arr[1:].astype("float64")
        idx = self.calendar[start : start + len(values)]
        return pd.Series(values[: len(idx)], index=idx, name=field)

    def read_symbol(self, symbol: str, fields: Iterable[str] = FIELDS) -> pd.DataFrame:
        cols = {}
        for f in fields:
            try:
                cols[f] = self.read_field(symbol, f)
            except FileNotFoundError:
                continue
        df = pd.DataFrame(cols)
        df.index.name = "date"
        return df

    def bars(self, symbol: str, *, prices: str = "stored") -> pd.DataFrame:
        """Canonical bars.

        ``prices="stored"`` uses the adjusted series as both ``close`` and
        ``adj_close`` (volume as stored). ``prices="raw"`` divides by ``factor``
        for open/high/low/close and keeps the stored adjusted close as
        ``adj_close``.
        """
        df = self.read_symbol(symbol)
        if df.empty or "close" not in df:
            return normalize_bars(pd.DataFrame({"date": [], "close": []}))
        out = pd.DataFrame(index=df.index)
        if prices == "raw" and "factor" in df and df["factor"].notna().any():
            fac = df["factor"].replace(0.0, np.nan)
            for c in ("open", "high", "low", "close"):
                out[c] = df[c] / fac
            out["adj_close"] = df["close"]
        else:
            for c in ("open", "high", "low", "close"):
                out[c] = df[c]
            out["adj_close"] = df["close"]
        out["volume"] = df.get("volume", 0.0)
        out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=["close"])
        return normalize_bars(out)
