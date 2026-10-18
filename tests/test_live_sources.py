from datetime import date

from hive.data.sources.fundamentals import days_until, parse_finnhub_earnings
from hive.data.sources.news import parse_google_rss

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>x</title>
<item><title>Acme beats estimates, raises guidance - Reuters</title><pubDate>Mon, 13 Oct 2026 12:00:00 GMT</pubDate>
<source url="https://reuters.com">Reuters</source></item>
<item><title>Acme announces $2B share offering</title><pubDate>Tue, 14 Oct 2026 12:00:00 GMT</pubDate>
<source url="https://bloomberg.com">Bloomberg</source></item>
</channel></rss>"""


def test_parse_google_rss_returns_ids_and_headlines():
    items = parse_google_rss(RSS)
    assert len(items) == 2
    sid, text = items[0]
    assert len(sid) == 10 and "Acme beats" in text and "Reuters" in text
    assert items[0][0] != items[1][0]


def test_parse_finnhub_earnings_picks_next_date():
    data = {"earningsCalendar": [{"date": "2026-10-01"}, {"date": "2026-10-28"}, {"date": "bad"}]}
    nxt = parse_finnhub_earnings(data, date(2026, 10, 18))
    assert nxt == date(2026, 10, 28)
    assert days_until(nxt, date(2026, 10, 18)) == 10
    assert parse_finnhub_earnings({}, date(2026, 1, 1)) is None
