"""Queued entries survive weekends; a bar is never processed twice."""

from datetime import date

from hive.orchestrator.book import OpenPosition
from hive.orchestrator.run import keep_pending


def _pos(entry_date="2026-10-16", order_id="abc"):
    return OpenPosition(symbol="TRV", asset_class="stock", qty=0.04, entry_price=389.5, entry_date=entry_date,
                        r_per_share=22.6, stop_price=366.9, highest_close=389.5, source="primary",
                        meta={"fill_pending": True, "order_id": order_id})


def test_pending_entry_is_kept_while_the_order_is_open_or_within_grace():
    assert keep_pending(_pos(), {"TRV"}, set(), date(2026, 10, 18))          # broker still shows the order
    assert keep_pending(_pos(), set(), {"abc"}, date(2026, 10, 18))          # matched by order id
    assert keep_pending(_pos(), set(), set(), date(2026, 10, 18))            # weekend: 2 days, within grace
    assert keep_pending(_pos(), set(), set(), date(2026, 10, 20))            # 4 days still fine
    assert not keep_pending(_pos(), set(), set(), date(2026, 10, 21))        # 5 days and no order: drop
    assert not keep_pending(_pos(entry_date="bad"), set(), set(), date(2026, 10, 18))
