import numpy as np
import pandas as pd

from hive.data.bars import BAR_COLUMNS
from hive.data.quality import clean_universe, find_first_anomaly, truncate_at_first_anomaly


def _bars(closes):
    idx = pd.bdate_range("2020-01-01", periods=len(closes))
    df = pd.DataFrame({c: closes for c in BAR_COLUMNS}, index=idx, dtype=float)
    df["high"] = df["close"] * 1.01
    df["low"] = df["close"] * 0.99
    return df


def test_reverting_spike_is_an_anomaly():
    closes = np.r_[np.linspace(10, 12, 50), [12.0 * 900], np.linspace(12, 12.5, 20)]
    bars = _bars(closes)
    a = find_first_anomaly(bars)
    assert a is not None and a.kind == "spike_reverted" and a.date == bars.index[50]
    cut, _ = truncate_at_first_anomaly(bars)
    assert len(cut) == 50


def test_unverifiable_move_at_end_of_series_is_an_anomaly():
    closes = np.r_[np.linspace(10, 12, 50), [12.0 * 1000, 12_100.0]]  # the delisting-day case
    a = find_first_anomaly(_bars(closes))
    assert a is not None and a.kind == "unverifiable_move"


def test_genuine_large_move_that_persists_is_kept():
    closes = np.r_[np.linspace(10, 12, 50), 12 * 1.6 * np.linspace(1.0, 1.1, 30)]  # +60%, stays up
    assert find_first_anomaly(_bars(closes)) is None
    crash = np.r_[np.linspace(10, 12, 50), 12 * 0.4 * np.linspace(1.0, 0.95, 30)]  # -60%, stays down
    assert find_first_anomaly(_bars(crash)) is None


def test_ohlc_inconsistency_flagged():
    bars = _bars(np.linspace(10, 14, 60))
    assert find_first_anomaly(bars) is None
    bad = bars.copy()
    bad.iloc[30, bad.columns.get_loc("high")] = 1.0  # high below low
    assert find_first_anomaly(bad).kind == "ohlc"


def test_clean_universe_drops_short_series():
    good = _bars(np.linspace(10, 14, 60))
    spiked = _bars(np.r_[np.linspace(10, 11, 5), [11000.0], np.linspace(11, 12, 54)])
    cleaned, anomalies = clean_universe({"GOOD": good, "BAD": spiked}, min_bars=20)
    assert "GOOD" in cleaned and "BAD" not in cleaned and "BAD" in anomalies
