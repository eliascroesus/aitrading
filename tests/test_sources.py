import pandas as pd

from hive.data.sources import coinmetrics, csv_folder, hyperliquid_source
from hive.data.store import BarStore

COINMETRICS_SAMPLE = """time,AdrActCnt,PriceUSD,volume_reported_spot_usd_1d
2020-01-01,100,7200.17,1.5e9
2020-01-02,101,6985.47,1.6e9
2020-01-03,102,,1.7e9
"""


def test_coinmetrics_parse_drops_missing_price_and_fills_ohlc():
    bars = coinmetrics.parse_csv(COINMETRICS_SAMPLE)
    assert len(bars) == 2
    assert bars.loc["2020-01-02", "close"] == 6985.47
    assert bars.loc["2020-01-02", "high"] == 6985.47
    assert bars.loc["2020-01-01", "volume"] == 1.5e9


def test_csv_folder_seed_store(tmp_path):
    folder = tmp_path / "stocks"
    folder.mkdir()
    (folder / "aapl.csv").write_text(
        "Date,Open,High,Low,Close,Adj Close,Volume\n"
        "2024-01-02,10,11,9,10.5,10.4,100\n2024-01-03,10.5,12,10,11.5,11.4,120\n"
    )
    (folder / "junk.csv").write_text("not,a,bars,file\n1,2,3,4\n")
    store = BarStore(tmp_path / "cache")
    loaded = csv_folder.seed_store(store, folder, "stock")
    assert loaded == ["AAPL"]
    assert store.get("stock", "AAPL").loc["2024-01-03", "adj_close"] == 11.4


def test_hyperliquid_candles_to_bars():
    candles = [
        {"t": 1704067200000, "T": 1704153599999, "s": "BTC", "i": "1d", "o": "42000", "c": "42500",
         "h": "43000", "l": "41500", "v": "123.4", "n": 10},
        {"t": 1704153600000, "T": 1704239999999, "s": "BTC", "i": "1d", "o": "42500", "c": "43500",
         "h": "44000", "l": "42400", "v": "150.0", "n": 12},
    ]
    bars = hyperliquid_source.candles_to_bars(candles)
    assert list(bars.index) == [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")]
    assert bars.iloc[1]["high"] == 44000.0 and bars.iloc[0]["volume"] == 123.4
    assert hyperliquid_source.candles_to_bars([]).empty
