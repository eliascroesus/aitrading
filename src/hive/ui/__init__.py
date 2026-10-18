"""The dashboard: a self-contained HTML page generated from the system's own state.

No server-side logic: ``hive ui build`` reads the ledger, books, journals,
daily reports and backtest outputs and writes ``reports/dashboard/index.html``
(+ ``data.json``). The daily workflow rebuilds and commits it, so GitHub Pages
can serve the public record; ``hive ui serve`` serves it locally.
"""
