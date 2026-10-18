# Experiment outputs on cleaned data (the ones `docs/BACKTEST_RESULTS.md` cites)

All runs: $100 start, PIT S&P 500 + 24 ETFs, Alpaca stock costs (5 bps slippage
per side), SPY/VIX regime gate, data-quality guard on (`docs/DATA.md`).
In-sample window 2001-01-01 → 2012-12-31; out-of-sample 2013-01-01 → 2020-11-10.

| File | What |
|---|---|
| `oat-preregistered-insample.csv` | the pre-registered one-at-a-time sensitivity (impulse window, reset period, impulse threshold, trail) |
| `oat-exits-posthoc-insample.csv` | post-hoc exit exploration (**not** pre-registered) |
| `null-base-full.csv` (+ `.base.csv`) | random-entry null test, spec config, full period, 5 seeds |
| `grid-walkforward-insample.csv` / `-outofsample.csv` | exit-off × trail × impulse-window grid on both windows |
| `template-cross-insample.csv` / `-outofsample.csv` | alternative entry: the day the trend template first passes |
| `null-v11-insample.csv` / `-outofsample.csv` | null test for the exit-fixed candidate config |
| `wide-exit-entry-modes.csv` | 10-ATR trail, no failure stop, 252-bar max hold × {spec, template-cross, random} entries × {IS, OOS, full} |

Regenerate with the commands in `docs/BACKTEST_RESULTS.md` / the `hive experiment` CLI.
