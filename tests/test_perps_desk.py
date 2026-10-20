"""Hyperliquid paper desk: public-data parsers, an end-to-end daily cycle on synthetic data, and
the funding charge. No network; nothing here can place an order."""

import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from hive.config import Settings, StrategyConfig
from hive.data.sources.hyperliquid_public import parse_candles, parse_funding, parse_universe
from hive.data.store import BarStore
from hive.execution.base import BrokerPosition
from hive.hive.perps_desk import charge_funding, run_perps_desk
from hive.hive.registry import Child
from hive.orchestrator.journal import Journal


def _bars(n, seed, start):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0012, 0.03, n)))
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame({"open": close, "high": close * 1.02, "low": close * 0.98, "close": close,
                         "adj_close": close, "volume": rng.integers(1_000, 10_000, n).astype(float)}, index=idx)


def test_parsers():
    meta = {"universe": [{"name": "BTC", "maxLeverage": 40}, {"name": "OLD", "isDelisted": True}, {"name": "ETH", "maxLeverage": 25}]}
    ctxs = [{"dayNtlVlm": "900", "funding": "0.0000125", "markPx": "60000"}, {"dayNtlVlm": "999999"}, {"dayNtlVlm": "1000", "markPx": "3000"}]
    u = parse_universe([meta, ctxs], top=5)
    assert [r["coin"] for r in u] == ["ETH", "BTC"] and u[1]["funding_hourly"] == 0.0000125
    today_ms = int(pd.Timestamp(datetime.now(UTC).date()).timestamp() * 1000)
    raw = [{"t": today_ms - 2 * 86_400_000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10"},
           {"t": today_ms - 86_400_000, "o": "1.5", "h": "2", "l": "1", "c": "1.8", "v": "12"},
           {"t": today_ms, "o": "1.8", "h": "3", "l": "1.7", "c": "2.9", "v": "40"}]          # forming day
    df = parse_candles(raw)
    assert len(df) == 2 and df["close"].iloc[-1] == 1.8 and (df["adj_close"] == df["close"]).all()
    assert parse_funding([{"time": 10, "fundingRate": "0.0001"}, {"time": 20, "fundingRate": "-0.00005"},
                          {"time": 5, "fundingRate": "0.5"}], since_ms=8) == 0.00005


def test_funding_is_charged_to_longs():
    class B:
        def __init__(self):
            self.cash = 100.0

        def positions(self):
            return {"BTC": BrokerPosition("BTC", "crypto", 0.001, 60000.0, 60000.0)}

        def adjust_cash(self, d, reason):
            self.cash += d

    class Data:
        def funding_since(self, sym, since):
            return 0.0003        # 24 hours at 0.00125% an hour

    b, ev = B(), []
    paid = charge_funding(b, Data(), 0, lambda k, p: ev.append(k))
    assert abs(paid - 60.0 * 0.0003) < 1e-9 and abs(b.cash - (100.0 - 0.018)) < 1e-9 and ev == ["funding"]


def test_desk_runs_a_daily_cycle_on_synthetic_public_data(tmp_path):
    cfg = StrategyConfig()
    s = Settings(hive_mode="paper", hive_data_dir=tmp_path / "data", hive_state_dir=tmp_path / "state",
                 hive_reports_dir=tmp_path / "reports", _env_file=None)
    run_date = (datetime.now(UTC) - timedelta(days=1)).date()
    start = run_date - timedelta(days=599)
    main = BarStore(s.hive_data_dir / "cache")
    spy = _bars(600, 1, start)
    main.put("etf", "SPY", spy, source="test")
    coins = {"BTC": _bars(600, 2, start), "ETH": _bars(600, 3, start), "HYPE": _bars(200, 4, start + timedelta(days=400))}

    class Data:
        def universe(self, top):
            return [{"coin": c, "volume_usd": 1e9 - i, "funding_hourly": 0.0000125} for i, c in enumerate(coins)][:top]

        def candles(self, coin):
            return coins[coin]

        def funding_since(self, sym, since):
            return 0.0003

    child = Child(id="hyperliquid-desk", name="Hyperliquid desk", kind="scout", sleeve="perps")
    cdir = s.hive_state_dir / "children" / child.id
    cdir.mkdir(parents=True)
    j = Journal(s.hive_state_dir / "journal", f"{run_date.isoformat()}-child-{child.id}")
    eq = run_perps_desk(s, cfg, child, cdir, j, run_date, main, False, data=Data())
    latest = json.loads((cdir / "latest.json").read_text())
    assert latest["n_listed"] == 3 and latest["n_tradable"] == 2          # HYPE lacks a year of history
    assert 90.0 <= eq <= 101.0 and latest["equity"] == eq
    assert BarStore(s.hive_data_dir / "cache_hl").get("etf", "SPY") is not None
    kinds = [e["kind"] for e in Journal.read(j.path)]
    assert "regime" in kinds and "perps_day" in kinds
    assert date.fromisoformat(latest["run_date"]) == run_date
