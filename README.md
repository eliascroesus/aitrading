# HIVE — a survival-constrained multi-agent AI trading system

A public study. An AI trading system starts with **$100**. Its own LLM and
data costs are paid out of that bankroll; if the bankroll reaches the death
floor it shuts down permanently; if it grows it unlocks sub-agents (more
research, more news coverage, a second strategy) — a hive.

One design rule, taken from every live LLM-trading experiment we could find
(see `docs/RESEARCH.md`): **deterministic code finds, sizes and protects every
trade; LLM agents may only research, debate and veto.**

> Nothing here is investment advice. Most retail trading loses money after
> costs; this project exists to measure, transparently, what an AI-run
> strategy actually does — including its own running costs.

## What is in the box

| Layer | Package | What it does |
|---|---|---|
| Data | `hive.data` | Point-in-time bars with a parquet cache; survivorship-free S&P 500 membership; offline seeders (Qlib US daily 2000–2020, CoinMetrics BTC/ETH, VIX) and network refresh (yfinance, Hyperliquid) |
| Signals | `hive.signals` | The pre-registered rule set: rising 200DMA template → strong impulse → quiet pullback that resets short-term ROC → breakout trigger; ATR sizing; regime gate |
| Backtest | `hive.backtest` | Next-open fills, intraday stops, costs, drawdown halts; metrics; robustness grid, null test, walk-forward experiments |
| Risk | `hive.risk` | Hard gates in code: stops required, position/heat/exposure caps, daily-loss and drawdown halts, kill switch; the LLM can only `approve / reduce_half / veto` |
| Agents | `hive.agents` | Quarantined news reader (anonymised, bounded outputs), fundamental/technical/sentiment analysts, one-round bull-vs-bear, portfolio manager, weekly auditor; every call cost-accounted; fail closed |
| Execution | `hive.execution` | `PaperBroker`, `AlpacaBroker` (paper by default), `HyperliquidBroker` (agent wallet, testnet by default, eligibility flag) — no transfer tool exists |
| Survival | `hive.survival` | Hash-chained ledger; equity net of AI costs; daily AI budget; death floor; tiers with hysteresis; children start in shadow mode |
| Orchestrator | `hive.orchestrator` | The daily cycle, position book, hash-chained journal, rules-only shadow portfolio, daily markdown/JSON reports |
| Hive & operator | `hive.hive`, `hive.operator` | Children (strategy variants / scouts per sleeve) as paper portfolios with value-of-information scoring; the bounded iteration loop that proposes, backtests and spawns them; human-only promotion |
| Meme scout | `hive.meme` | DexScreener/RugCheck scanner with the research's hard filters and caps; paper-only (live Jupiter execution is an explicit guard) |
| UI | `hive.ui` | Self-contained dashboard built from state; GitHub Pages deploy |

Documentation: `docs/ARCHITECTURE.md`, `docs/STRATEGY.md`, `docs/DATA.md`,
`docs/RESEARCH.md` (+ the four source reports in `docs/research/`),
`docs/BACKTEST_RESULTS.md`, `docs/REVIEW_LOG.md`, `docs/OPERATIONS.md`.

## What the evidence says (read before expecting anything)

On 2001–2020 US data the pre-registered rules made ~1% a year with a −32%
drawdown versus SPY's 7.2% / −55%; the walk-forward-validated v1.1 makes
roughly 2–4% with −20% to −30%; the entry rule does not beat random entries
inside its own trend template. The system is a drawdown-controlled, low-return
trend follower, not a money machine — and that is the honest starting point
for measuring whether an AI layer adds anything net of its own cost.

## Quick start

```bash
uv venv --python 3.13 && uv pip install -e ".[dev]"
cp .env.example .env                      # paper mode by default; add keys when ready
pytest -q && ruff check src tests

# Offline backtests (see docs/DATA.md for where the files come from)
hive data seed --qlib-root /path/to/qlib_us_1d   # + data/raw/{sp500_pit_membership.csv,coinmetrics/,vix-daily.csv}
hive backtest --sleeve stocks --start 2001-01-01
hive experiment --kind null --seeds 5
hive experiment --kind oat --params "stocks.trail_atr=3,4,5;stocks.reset_period=15,21,30"

# Paper trading cycle (post-close), replayable on cached dates
hive data refresh
hive run                     # primary (LLM filter if ANTHROPIC_API_KEY set) + rules-only shadow + children
hive run --as-of 2020-11-10  # replay a historical day on cached data
hive status
hive review                  # weekly auditor: proposals only, never applied

# The dashboard and the hive
hive ui build && hive ui serve          # http://127.0.0.1:8787 (GitHub Pages publishes the same page)
#   a pre-built example (32 replayed days of 2020): docs/examples/dashboard-replay-2020/index.html
hive operate                            # the operator proposes variants, code backtests, spawns shadow children
hive hive list | spawn | retire | promote <id> --approve   # promotion is always a human decision
```

Full setup (accounts, keys, hosts, costs, go-live checklist): `docs/SETUP.md`.

Live trading requires **both** `HIVE_MODE=live` with the confirmation phrase in
`HIVE_LIVE_CONFIRM` and the `--live` flag. Creating the file `state/KILL` stops
every run before any order.

## Scheduling

`.github/workflows/daily.yml` runs the cycle after the US close on weekdays
(paper unless secrets say otherwise) and commits `state/` and `reports/` back
to the repo so the public record is the git history itself.

## Honesty rules for the study

* All rules and thresholds are pre-registered in `config/strategy.toml`; any
  change is a commit.
* Three lines are published every day: buy-and-hold benchmark, rules-only
  shadow, rules + LLM filter — the research question is whether the LLM layer
  beats rules-only *net of its own cost*.
* Backtests of the LLM layer before the model's training cutoff are not
  evidence; only forward results count for it.
* Every death is published. No capital is added quietly.
