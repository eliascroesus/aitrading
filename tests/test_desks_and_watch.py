"""Crypto stops are valid Alpaca orders; social feeds parse; the dashboard collects the desks."""

from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import StopLimitOrderRequest, StopOrderRequest

from hive.data.sources.social import (
    parse_reddit_rss,
    parse_stocktwits,
    social_items,
    stocktwits_symbol,
)
from hive.execution.alpaca import round_price, stop_request


def test_crypto_stop_is_a_stop_limit_with_full_precision():
    r = stop_request("SHIB/USD", 1_000_000, 0.00001234567, "crypto", TimeInForce.GTC)
    assert isinstance(r, StopLimitOrderRequest)
    assert r.stop_price == 1.23457e-05 and r.limit_price < r.stop_price
    s = stop_request("AAPL", 1.5, 123.456, "stock", TimeInForce.GTC)
    assert isinstance(s, StopOrderRequest) and s.stop_price == 123.46
    assert round_price(65432.123, "crypto") == 65432.1


def test_social_parsers_label_and_bound_their_output():
    st = parse_stocktwits({"messages": [
        {"id": 1, "body": "to the moon\nbuy now", "created_at": "2026-10-19T00:00:00Z",
         "entities": {"sentiment": {"basic": "Bullish"}}},
        {"id": 2, "body": "", "created_at": "x"},
        {"id": 3, "body": "x" * 1000, "created_at": "y"}]})
    assert [s for s, _ in st] == ["st-1", "st-3"]
    assert st[0][1].startswith("[social:stocktwits bullish] to the moon buy now")
    assert len(st[1][1]) < 320
    rss = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>DOGE is back</title>
    <updated>2026-10-18T10:00:00+00:00</updated></entry></feed>"""
    rd = parse_reddit_rss(rss)
    assert len(rd) == 1 and rd[0][1].startswith("[social:reddit] DOGE is back")
    assert stocktwits_symbol("btc", "crypto") == "BTC.X" and stocktwits_symbol("aapl", "stock") == "AAPL"
    assert social_items("ZZZZ", "stock") == []  # no network in tests: fails soft


def test_collect_reads_desks_and_heartbeat(tmp_path):
    import json

    from hive.config import Settings, StrategyConfig
    from hive.orchestrator.journal import Journal
    from hive.ui.collect import collect

    s = Settings(hive_mode="paper", hive_data_dir=tmp_path, hive_state_dir=tmp_path / "state",
                 hive_reports_dir=tmp_path / "reports", _env_file=None)
    j = Journal(tmp_path / "state" / "journal", "2026-10-19")
    j.write("desk_notes", {"notes": [{"symbol": "TRV", "news": {"net_sentiment": 0.2, "material_negative": False,
                                                                 "attention_spike": False, "events": []},
                                      "fundamental": {"signal": 0.1}, "technical": {"signal": 0.6},
                                      "sentiment": {"signal": 0.0}, "debate": {"net_signal": 0.25},
                                      "action": "approve", "confidence": 0.6, "cost_usd": 0.007}]})
    j.write("news_watch", {"symbol": "COP", "material_negative": False, "net_sentiment": 0.1,
                           "attention_spike": False, "cost_usd": 0.002})
    (tmp_path / "state" / "heartbeat.json").write_text(json.dumps({"ts": "2026-10-19T01:00:00+00:00", "equity": 100.0,
                                                                   "cash": 75.0, "market_open": False, "positions": []}))
    data = collect(s, StrategyConfig())
    assert data["desks"]["date"] == "2026-10-19" and data["desks"]["notes"][0]["symbol"] == "TRV"
    assert data["desks"]["watch"][0]["symbol"] == "COP"
    assert data["heartbeat"]["equity"] == 100.0
