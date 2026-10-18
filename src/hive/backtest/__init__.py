"""Daily-bar, next-open-fill backtester for the deterministic strategy.

Design choices that keep results honest:

* Signals are computed on the close of day *t*; entries and rule exits fill at
  the **open of t+1** (never at the same close that produced the signal).
* Stops are checked against the *low* of each day and fill at the stop price,
  or at the open when the day gaps through the stop.
* Every fill pays commission and slippage; cash accounts cannot lever.
* Universe membership is point-in-time when a ``PointInTimeUniverse`` is given.
* The same ``StrategyConfig`` object drives backtest, paper and live runs.
"""
