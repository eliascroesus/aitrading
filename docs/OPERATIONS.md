# Operations runbook

## 0. Where things live

| Path | What |
|---|---|
| `config/strategy.toml` | pre-registered rules and limits (every change is a commit) |
| `data/cache/` | parquet bars (regenerable; gitignored) |
| `data/raw/` | offline source files (gitignored; see `DATA.md`) |
| `state/ledger.jsonl` | hash-chained survival ledger (equity marks, AI costs, tier changes) |
| `state/book_primary.json`, `state/book_shadow.json` | open positions with stops/R |
| `state/journal/<date>.jsonl` | hash-chained decision journal (regime, candidates, LLM calls, risk verdicts, orders) |
| `state/paper_*.json` | paper broker state |
| `state/KILL` | kill switch — create this file to stop all order placement |
| `reports/daily/<date>.{md,json}` | the daily public report |
| `reports/backtest-*/`, `reports/experiments/` | backtests, grids, null tests |

## 1. Secrets and settings (`.env`, never committed)

| Variable | Purpose |
|---|---|
| `HIVE_MODE` | `paper` (default) / `live` / `backtest` |
| `HIVE_LIVE_CONFIRM` | must equal `I_UNDERSTAND_THIS_TRADES_REAL_MONEY` for live |
| `ANTHROPIC_API_KEY` | enables the LLM filter; absent → rules-only |
| `HIVE_LLM_MODEL` / `HIVE_LLM_CHEAP_MODEL` | decision model (default `claude-opus-5-5`) and bulk model (default `claude-haiku-5-5`) |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` / `ALPACA_PAPER` | Alpaca; paper keys by default |
| `HL_AGENT_PRIVATE_KEY` / `HL_ACCOUNT_ADDRESS` / `HL_TESTNET` | Hyperliquid agent wallet (cannot withdraw); testnet by default |
| `HL_OPERATOR_NOT_RESTRICTED_PERSON` | must be `true` to enable Hyperliquid at all (its terms restrict US persons) |
| `FINNHUB_API_KEY` | optional: company news + earnings calendar |
| `HIVE_NETWORK_DATA` | `false` to skip headline/earnings fetches during a run |

Set an **Anthropic spend cap** in the Console as a second line of defence
behind the in-code daily budget.

## 2. Network hosts the system needs

The build sandbox blocked all of these; a paper/live runner must allow them:

`query1.finance.yahoo.com`, `query2.finance.yahoo.com` (yfinance) ·
`paper-api.alpaca.markets`, `api.alpaca.markets`, `data.alpaca.markets` ·
`api.hyperliquid.xyz`, `api.hyperliquid-testnet.xyz` · `finnhub.io` ·
`news.google.com` · `data.sec.gov` · `api.anthropic.com` ·
`raw.githubusercontent.com` (offline datasets).

## 3. Daily cycle checklist (post US close)

1. `hive data refresh` — tolerates failures; the run uses the cache.
2. `hive run` — writes `reports/daily/<date>.md`; the shadow rules-only
   portfolio runs alongside.
3. `hive status` — equity net of AI costs, tier, runway, open positions,
   ledger chain verification.
4. Weekly: `hive review` — auditor proposals; apply by editing
   `config/strategy.toml` in a commit, never automatically.
5. Any anomaly (orders failing, positions at the broker not in the book,
   ledger chain broken): create `state/KILL`, investigate, then delete it.

## 4. Going live (do not skip steps)

1. ≥ 4 weeks of paper results with the daily reports committed.
2. Both portfolios' metrics reviewed: rules-only vs rules+LLM, net of AI cost.
3. Alpaca live keys as **trading-only** permissions; Hyperliquid **agent wallet**
   only; master key offline; no transfer capability anywhere.
4. `HIVE_MODE=live`, `HIVE_LIVE_CONFIRM=...` in secrets, and run with
   `hive run --live`. Without `--live` the run stays paper even in live mode.
5. Fund exactly the starting bankroll ($100). Any later deposit must be
   booked as `capital_in` in the ledger (it is public).

## 5. Death and resurrection

Death triggers at equity ≤ $25 or a 75% drawdown from peak (`survival` in
`strategy.toml`). The run flattens everything, writes a `death` journal entry
and exits with code 2. There is no automatic restart; a new study means a new
ledger and a new public pre-registration.

## 6. Costs to expect

With the default models and ≤ 8 candidates/day the LLM spend is cents per
day; the base-tier daily budget is 0.05% of equity (min $0.02). The ledger
reports AI spend and the equivalent annual hurdle rate every day; if the
hurdle exceeds the strategy's expected edge, the honest move is to run
rules-only, which the shadow portfolio already measures.
