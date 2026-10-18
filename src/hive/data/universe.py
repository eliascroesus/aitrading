"""Point-in-time index membership (survivorship-bias control).

Backtests that scan "today's S&P 500" over the past 20 years are biased: the
list only contains the winners that survived. We instead carry a dated
membership table and ask "who was in the index *on that day*".
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


class PointInTimeUniverse:
    def __init__(self, membership: pd.Series):
        """``membership``: DatetimeIndex (ascending) -> frozenset[str] of tickers."""
        if not isinstance(membership.index, pd.DatetimeIndex):
            raise TypeError("membership must be indexed by date")
        self._m = membership.sort_index()

    @classmethod
    def from_membership_csv(cls, path: Path) -> PointInTimeUniverse:
        """Load a ``date,tickers`` CSV where ``tickers`` is a comma-joined string.

        This is the layout of the public ``sp_500_historical_components.csv``
        datasets (1996 -> present).
        """
        raw = pd.read_csv(path, dtype=str)
        cols = {c.lower(): c for c in raw.columns}
        dcol, tcol = cols["date"], cols["tickers"]
        idx = pd.to_datetime(raw[dcol])
        sets = raw[tcol].fillna("").map(
            lambda s: frozenset(t.strip().upper() for t in s.split(",") if t.strip())
        )
        return cls(pd.Series(list(sets), index=idx))

    @classmethod
    def static(cls, symbols: list[str], since: str = "1900-01-01") -> PointInTimeUniverse:
        """A fixed universe (ETF baskets, custom lists). Explicitly *not* PIT-safe."""
        s = pd.Series([frozenset(x.upper() for x in symbols)], index=pd.to_datetime([since]))
        return cls(s)

    def members(self, when: date | str | pd.Timestamp) -> frozenset[str]:
        ts = pd.Timestamp(when).normalize()
        hist = self._m.loc[:ts]
        if hist.empty:
            return frozenset()
        return hist.iloc[-1]

    def all_tickers(self) -> set[str]:
        out: set[str] = set()
        for s in self._m:
            out |= s
        return out

    @property
    def first_date(self) -> pd.Timestamp:
        return self._m.index[0]

    @property
    def last_date(self) -> pd.Timestamp:
        return self._m.index[-1]
