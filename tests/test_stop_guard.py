"""Protective stops that stay protective: Alpaca's fractional stock stops are day orders."""

from types import SimpleNamespace

import pandas as pd

from hive.cli import guard_stops
from hive.execution.alpaca import AlpacaBroker
from hive.execution.base import BrokerPosition, OrderResult
from hive.orchestrator.book import OpenPosition, PositionBook
from hive.orchestrator.run import DailyRun


class FakeBroker:
    def __init__(self, held, stops):
        self.held, self.stops, self.armed, self.closed = held, stops, [], []

    def positions(self):
        return self.held

    def pending_orders(self):
        return list(self.queued)

    queued: list = []

    def open_stops(self):
        return None if self.stops is None else dict(self.stops)

    def submit_stop(self, symbol, qty, stop_price, *, asset_class="stock"):
        self.armed.append((symbol, qty, round(stop_price, 2)))
        self.stops = {**(self.stops or {}), symbol: f"new-{symbol}"}
        return OrderResult(True, f"new-{symbol}")

    def close_position(self, symbol):
        self.closed.append(symbol)
        return OrderResult(True)

    def is_market_open(self):
        return self.session_open

    session_open = True


class Recorder:
    def __init__(self):
        self.rows = []

    def write(self, kind, payload):
        self.rows.append((kind, payload))


def _pos(sym, stop, order_id="", pending=False, avg=100.0):
    return OpenPosition(symbol=sym, asset_class="stock", qty=0.05, entry_price=avg, entry_date="2026-10-19",
                        r_per_share=10.0, stop_price=stop, highest_close=avg, stop_order_id=order_id,
                        meta={"fill_pending": True} if pending else {})


def _run(broker, positions, tmp_path):
    book = PositionBook(tmp_path / "book.json")
    for p in positions.values():
        book.add(p)
    journal = Recorder()
    ns = SimpleNamespace(book=book, broker=broker, journal=journal, run_date=pd.Timestamp("2026-10-21"))
    ns._arm_stop = lambda pos: DailyRun._arm_stop(ns, pos)
    return ns


def test_a_stop_that_lapsed_at_the_close_is_rearmed_by_the_daily_run(tmp_path):
    held = {"TRV": BrokerPosition("TRV", "stock", 0.05, 387.0, 399.0)}
    broker = FakeBroker(held, stops={})                                  # yesterday's day stop expired
    run = _run(broker, {"TRV": _pos("TRV", 364.63, order_id="expired-id", avg=387.0)}, tmp_path)
    DailyRun._sync_book_with_broker(run)
    assert run.book.positions["TRV"].stop_order_id == ""
    assert ("stop_lapsed", {"symbol": "TRV", "was": "expired-id", "now": ""}) in run.journal.rows
    DailyRun._ensure_stops(run)
    assert broker.armed == [("TRV", 0.05, 364.63)] and run.book.positions["TRV"].stop_order_id == "new-TRV"


def test_a_resting_stop_is_kept_or_adopted_and_nothing_changes_when_the_broker_cannot_say(tmp_path):
    held = {"TRV": BrokerPosition("TRV", "stock", 0.05, 387.0, 399.0)}
    run = _run(FakeBroker(held, stops={"TRV": "armed-id"}), {"TRV": _pos("TRV", 364.63, order_id="armed-id")}, tmp_path / "a")
    DailyRun._sync_book_with_broker(run)
    assert run.book.positions["TRV"].stop_order_id == "armed-id" and not run.journal.rows
    run = _run(FakeBroker(held, stops={"TRV": "guard-id"}), {"TRV": _pos("TRV", 364.63)}, tmp_path / "b")
    DailyRun._sync_book_with_broker(run)                                  # placed by the guardian
    assert run.book.positions["TRV"].stop_order_id == "guard-id"
    run = _run(FakeBroker(held, stops=None), {"TRV": _pos("TRV", 364.63, order_id="maybe")}, tmp_path / "c")
    DailyRun._sync_book_with_broker(run)
    assert run.book.positions["TRV"].stop_order_id == "maybe"


def test_the_guardian_arms_every_unprotected_position_and_closes_one_already_through_its_stop():
    held = {"TRV": BrokerPosition("TRV", "stock", 0.044, 387.0, 399.0),
            "CVX": BrokerPosition("CVX", "stock", 0.0376, 225.99, 226.78),
            "COP": BrokerPosition("COP", "stock", 0.051, 146.24, 135.0),
            "XOM": BrokerPosition("XOM", "stock", 0.1, 110.0, 112.0)}
    broker = FakeBroker(held, stops={"XOM": "resting"})
    book = SimpleNamespace(positions={"TRV": _pos("TRV", 364.63, order_id="expired"),
                                      "CVX": _pos("CVX", 212.40, pending=True, avg=225.71),
                                      "COP": _pos("COP", 136.64),
                                      "XOM": _pos("XOM", 100.0, order_id="resting")})
    journal = Recorder()
    out = guard_stops(broker, book, journal)
    assert broker.armed == [("TRV", 0.044, 364.63), ("CVX", 0.0376, 215.99)]   # the fill price less the planned risk
    assert broker.closed == ["COP"] and len(journal.rows) == 3
    assert {r["symbol"]: r["action"] for r in out} == {"TRV": "armed", "CVX": "armed", "COP": "closed_through_stop"}
    assert guard_stops(FakeBroker(held, stops=None), book, Recorder()) == []   # the broker could not be asked
    exiting = FakeBroker(held, stops={"XOM": "resting"})
    exiting.queued = [{"symbol": "TRV", "side": "orderside.sell"}, {"symbol": "CVX", "side": "orderside.sell"},
                      {"symbol": "COP", "side": "orderside.sell"}]
    assert guard_stops(exiting, book, Recorder()) == []                      # exits already queued for the open
    shut = FakeBroker(held, stops={"XOM": "resting", "TRV": "a", "CVX": "b"})
    shut.session_open = False
    assert guard_stops(shut, book, Recorder()) == [] and shut.closed == []     # through its stop after hours: waits


def test_alpaca_reports_resting_sell_stops_by_symbol():
    o = lambda sym, side, kind, oid, ac="us_equity": SimpleNamespace(symbol=sym, side=side, order_type=kind,  # noqa: E731
                                                                     id=oid, asset_class=ac)
    b = object.__new__(AlpacaBroker)
    b.client = SimpleNamespace(get_orders=lambda req: [o("TRV", "OrderSide.SELL", "OrderType.STOP", "a"),
                                                       o("BTC/USD", "OrderSide.SELL", "OrderType.STOP_LIMIT", "b", "crypto"),
                                                       o("CVX", "OrderSide.BUY", "OrderType.MARKET", "c")])
    assert b.open_stops() == {"TRV": "a", "BTC": "b"}

    def boom(req):
        raise RuntimeError("down")
    b.client = SimpleNamespace(get_orders=boom)
    assert b.open_stops() is None
