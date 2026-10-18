"""Execution adapters behind one ``Broker`` protocol.

* ``PaperBroker``       — in-process simulator with a JSON state file. Used for
                          paper mode without broker keys and for the shadow
                          portfolios (rules-only vs rules+LLM).
* ``AlpacaBroker``      — US stocks/ETFs (and spot crypto) via ``alpaca-py``;
                          paper endpoint unless live is explicitly enabled.
* ``HyperliquidBroker`` — crypto perps via ``hyperliquid-python-sdk`` with an
                          agent wallet (cannot withdraw); testnet by default.

None of these expose a transfer or withdrawal method, by design.
"""
