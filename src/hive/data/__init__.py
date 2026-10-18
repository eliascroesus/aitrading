"""Point-in-time data access with an on-disk cache.

Rules of this package:

* Every frame handed to higher layers is a *bars* frame (see ``bars.py``):
  tz-naive daily DatetimeIndex, ascending, unique; float columns
  ``open, high, low, close, adj_close, volume``.
* Callers slice with ``as_of`` before computing anything. The backtester and
  the live run use the same code path, so lookahead is structurally hard.
* Sources are thin: fetch -> normalize -> store. The store is parquet under
  ``data/cache/<asset_class>/<SYMBOL>.parquet`` with a JSON sidecar recording
  provenance, so results can be reproduced offline.
"""
