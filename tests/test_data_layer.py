import numpy as np
import pandas as pd
import pytest

from hive.data.bars import BAR_COLUMNS, adjust_ohlc, as_of, has_true_ohlc, normalize_bars
from hive.data.store import BarStore
from hive.data.universe import PointInTimeUniverse


def test_normalize_yahoo_style_columns():
    df = pd.DataFrame(
        {
            "Date": ["2024-01-03", "2024-01-02", "2024-01-02"],
            "Open": [10, 9, 9.5],
            "High": [11, 10, 10],
            "Low": [9, 8, 8],
            "Close": [10.5, 9.5, 9.6],
            "Adj Close": [10.4, 9.4, 9.5],
            "Volume": [100, 200, 250],
        }
    )
    out = normalize_bars(df)
    assert list(out.columns) == BAR_COLUMNS
    assert out.index.is_monotonic_increasing and out.index.is_unique
    # duplicate 2024-01-02 -> keep last row
    assert out.loc["2024-01-02", "close"] == pytest.approx(9.6)
    assert out.index.tz is None


def test_normalize_close_only_source_fills_ohlc():
    df = pd.DataFrame({"time": ["2020-01-01", "2020-01-02"], "PriceUSD": [7000.0, 7100.0]})
    out = normalize_bars(df)
    assert (out["high"] == out["close"]).all()
    assert out["volume"].eq(0).all()
    assert not has_true_ohlc(out)


def test_adjust_ohlc_scales_all_price_columns():
    idx = pd.bdate_range("2024-01-01", periods=2)
    bars = pd.DataFrame(
        {
            "open": [100.0, 100.0],
            "high": [110.0, 110.0],
            "low": [90.0, 90.0],
            "close": [100.0, 100.0],
            "adj_close": [50.0, 100.0],
            "volume": [1.0, 1.0],
        },
        index=idx,
    )
    adj = adjust_ohlc(bars)
    assert adj.iloc[0][["open", "high", "low", "close"]].tolist() == [50.0, 55.0, 45.0, 50.0]
    assert adj.iloc[1]["close"] == 100.0


def test_as_of_is_inclusive_and_blocks_future():
    idx = pd.bdate_range("2024-01-01", periods=5)
    bars = pd.DataFrame({c: np.arange(5, dtype=float) for c in BAR_COLUMNS}, index=idx)
    cut = as_of(bars, "2024-01-03")
    assert len(cut) == 3 and cut.index[-1] == pd.Timestamp("2024-01-03")


def test_store_roundtrip_and_upsert(tmp_path):
    store = BarStore(tmp_path)
    idx = pd.bdate_range("2024-01-01", periods=3)
    bars = pd.DataFrame({c: [1.0, 2.0, 3.0] for c in BAR_COLUMNS}, index=idx)
    store.put("stock", "aapl", bars, source="test")
    assert store.has("stock", "AAPL")
    back = store.get("stock", "AAPL")
    pd.testing.assert_frame_equal(back, bars, check_names=False, check_freq=False)
    meta = store.meta("stock", "AAPL")
    assert meta["rows"] == 3 and meta["source"] == "test"
    # upsert overlapping + new
    idx2 = pd.bdate_range("2024-01-03", periods=2)
    more = pd.DataFrame({c: [30.0, 40.0] for c in BAR_COLUMNS}, index=idx2)
    store.upsert("stock", "AAPL", more, source="test2")
    merged = store.get("stock", "AAPL")
    assert len(merged) == 4 and merged.loc["2024-01-03", "close"] == 30.0
    assert store.symbols("stock") == ["AAPL"]


def test_point_in_time_universe(tmp_path):
    csv = tmp_path / "m.csv"
    csv.write_text('date,tickers\n2020-01-01,"AAA,BBB"\n2021-01-01,"BBB,CCC"\n')
    u = PointInTimeUniverse.from_membership_csv(csv)
    assert u.members("2019-12-31") == frozenset()
    assert u.members("2020-06-01") == {"AAA", "BBB"}
    assert u.members("2021-01-01") == {"BBB", "CCC"}
    assert u.all_tickers() == {"AAA", "BBB", "CCC"}
    assert PointInTimeUniverse.static(["spy"]).members("2000-01-01") == {"SPY"}
