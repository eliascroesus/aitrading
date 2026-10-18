# Example dashboard — 2020 replay

`index.html` (open it in a browser; `data.json` is the same data as JSON) is
what `hive ui build` produces after **32 replayed trading days, 2020-05-26 to
2020-07-10**, on the cached Qlib/CoinMetrics history. It exists so the UI can be
seen before the live study starts. The live dashboard is published to GitHub
Pages by `.github/workflows/pages.yml` after every daily run.

What produced it, and what that means for reading it:

* `hive run --as-of <date> --no-llm` for each session, with `HIVE_NETWORK_DATA=false`
  and no broker keys, so both the primary and the rules-only shadow are
  `PaperBroker` books. No LLM calls were made, so the "rules + LLM" line equals
  the shadow line and the AI-spend chart is flat at zero. In a current run the
  two lines diverge wherever the portfolio manager vetoes or halves a trade,
  and the spend line carries every call's cost.
* The three children are paper portfolios: two were spawned by hand with
  `hive hive spawn` (a wider 6-ATR trail; a BTC/ETH sleeve on the parent config)
  and one, `impulse-window-90`, by the operator loop (`hive operate`), whose
  backtest verdict is the single entry under *Operator iterations*. The tier is
  `base`, which allows **0** children, so all three stay in shadow regardless of
  their value-of-information score — the ladder gates promotion, not observation.
* Regime shows **Yellow** because the VIX was above 30 for most of the window;
  that halves stock risk and position caps, which is why positions are small.
* "Integrity" counts names the data-quality guard skipped in the last 30 days
  (a corrupted print in the Qlib dump excludes a name from candidates for that
  day); it is not a ledger or journal failure.
* Backtest and experiment panels are the committed outputs under `reports/`.

Regenerate (about a minute per day on a laptop):

```bash
export HIVE_STATE_DIR=/tmp/demo_state HIVE_REPORTS_DIR=/tmp/demo_reports HIVE_NETWORK_DATA=false
days=$(python -c "import pandas as pd; from hive.data.store import BarStore; from hive.config import REPO_ROOT; \
  b = BarStore(REPO_ROOT/'data'/'cache').get('etf','SPY'); print(' '.join(d.date().isoformat() for d in b.loc['2020-05-26':'2020-07-10'].index))")
for d in $days; do hive run --as-of "$d" --no-llm; done
cp -r reports/backtest-* reports/experiments "$HIVE_REPORTS_DIR"/
hive ui build --out docs/examples/dashboard-replay-2020
```
