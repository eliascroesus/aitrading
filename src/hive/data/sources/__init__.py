"""Data sources. Each exposes plain functions returning canonical bars frames.

Offline-capable (files on disk / public CSV mirrors):
    coinmetrics   - daily BTC/ETH reference prices since inception
    csv_folder    - per-ticker CSV folders (Kaggle/Yahoo layout)
Network (used in paper/live, and to refresh the cache):
    yfinance_source
    hyperliquid_source
"""
