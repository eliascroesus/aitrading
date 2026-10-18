# Setup — from zero to a running study

Everything the system needs, in the order to set it up. Items marked **free**
cost nothing; the whole stack runs for roughly **$2–7 a month**, which on a
$100 bankroll is the main hurdle the strategy must clear (see `RESEARCH.md`).

## 1. Accounts and keys

| Need | Service | Cost | What to do |
|---|---|---|---|
| LLM for the agents and the operator | **Anthropic API** — https://console.anthropic.com | pay per token; the in-code daily budget is ~0.05% of equity (≈ $0.05/day at $100) | Create a key → `ANTHROPIC_API_KEY`. Set a **monthly spend cap** in the Console as a second line of defence. Models default to `claude-opus-5-5` for decisions and `claude-haiku-5-5` for bulk roles (`HIVE_LLM_MODEL`, `HIVE_LLM_CHEAP_MODEL`). |
| US stocks/ETFs (and spot crypto) | **Alpaca** — https://app.alpaca.markets | **free** paper; live has no commissions, $1 minimum fractional orders, no margin/shorting under $2k | Create the account, generate **paper** keys first → `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER=true`. Reset the paper balance to **$100** so paper mirrors live. The PDT rule no longer applies (retired 2026-06-04). |
| Crypto perps (optional) | **Hyperliquid** — https://app.hyperliquid.xyz | **free**; 0.045%/0.015% fees; $10 minimum order | **Its terms restrict US persons.** Only if eligible: create an **API/agent wallet** (cannot withdraw) → `HL_AGENT_PRIVATE_KEY`, `HL_ACCOUNT_ADDRESS` (the master address), `HL_TESTNET=true`, `HL_OPERATOR_NOT_RESTRICTED_PERSON=true`. Fund with USDC on Arbitrum (≥ 5 USDC). Testnet faucet needs a prior mainnet deposit. |
| Company news + earnings calendar (optional) | **Finnhub** — https://finnhub.io | **free** (60 calls/min) | Key → `FINNHUB_API_KEY`. Without it the system uses Google News RSS (free, keyless) and yfinance earnings dates. |
| Memecoin scanner | **DexScreener**, **RugCheck** | **free**, keyless | Nothing to set up. The scout is paper-only (see §6). |
| X / social sentiment (optional, off by default) | X API (pay-per-use, ~$0.005/post) or xAI Grok `x_search` | ~$1–4/month if enabled | Not wired by default; the research shows 1-day sentiment is a veto/tie-breaker, not an entry signal. |
| Market history for backtests | Qlib US daily dump, CoinMetrics CSVs, PIT S&P 500 membership, VIX | **free** | See `DATA.md`; `hive data seed`. |

Put keys in `.env` (copy `.env.example`). Never commit it.

## 2. Where it runs

**Option A — GitHub Actions (recommended, free for a public repo).**
1. Push this repository to GitHub (public makes the study auditable).
2. Settings → Secrets and variables → Actions: add `ANTHROPIC_API_KEY`,
   `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, optionally `FINNHUB_API_KEY`; add
   `HIVE_MODE=paper` as a secret (change to `live` only at go-live).
3. Settings → Pages → Source: **GitHub Actions**. `.github/workflows/pages.yml`
   publishes `reports/dashboard/` after every daily run.
4. `.github/workflows/daily.yml` runs after the US close on weekdays:
   `hive data refresh` → `hive run` → `hive ui build` → commit `state/` and
   `reports/`. The git history **is** the public record.
5. Weekly, by hand or a second cron: `hive review` (auditor) and `hive operate`
   (the iteration loop).

**Option B — your own machine / VPS.** `uv venv && uv pip install -e .`, a
cron entry for the same three commands, and `hive ui serve` for the dashboard
at http://127.0.0.1:8787.

**Cloud sandbox note.** If you run it from a Claude Code cloud environment,
allow these hosts in the environment's network settings: `query1.finance.yahoo.com`,
`query2.finance.yahoo.com`, `paper-api.alpaca.markets`, `api.alpaca.markets`,
`data.alpaca.markets`, `api.hyperliquid.xyz`, `api.hyperliquid-testnet.xyz`,
`finnhub.io`, `news.google.com`, `data.sec.gov`, `api.dexscreener.com`,
`api.rugcheck.xyz`, `api.anthropic.com`, `raw.githubusercontent.com`.

## 3. First run

```bash
cp .env.example .env            # fill keys
uv venv --python 3.13 && uv pip install -e ".[dev]" && pytest -q
hive data seed --qlib-root <unzipped Qlib US dir>   # offline history (DATA.md)
hive data refresh                                   # bring the cache to today
hive run                                            # first paper cycle (primary + rules-only shadow)
hive ui build && hive ui serve                      # the dashboard
hive status
```

## 4. The daily / weekly rhythm

| When | Command | What happens |
|---|---|---|
| every weekday after the close | `hive run` | regime → signals → LLM filter (if key) → risk → orders → ledger → report; children trade their paper books |
| every weekday | `hive ui build` | dashboard rebuilt from state |
| weekly | `hive review` | the auditor reads the journals and proposes changes (never applies them) |
| weekly | `hive operate` | the operator proposes ≤ 3 config variants inside `config/bounds.toml`, code backtests them in/out of sample, winners become shadow children if the tier allows |
| when a child has earned it | `hive hive promote <id> --approve` | **a human** promotes; then apply its overrides to `config/strategy.toml` in a commit |

## 5. How the hive grows (and what it is not allowed to do)

* **Tiers** unlock children: base (< $250) none; growth ($250+, 30 net-positive
  days) one child; hive ($1k+, 60 days, human approval) up to three.
* A child is a **paper portfolio** with its own sleeve (`stocks`, `crypto`,
  `all`, `meme`) and config overrides. Its value is measured against the
  parent over the same days (value of information). Losers retire themselves.
* The operator **can**: propose variants, read every journal, recommend.
  It **cannot**: change risk limits or budgets, touch the universe, spend
  beyond the ledger's daily budget, or promote to live. Those are code and
  humans, on purpose — the research record of agents under "survive at all
  costs" framing is one of rule-bending, not of returns.

## 6. Sleeves and what "live" means for each

| Sleeve | Paper | Live | Notes |
|---|---|---|---|
| Stocks/ETFs | `PaperBroker` | Alpaca | fractional from $1; the core of the study |
| Crypto (BTC/ETH/SOL) | `PaperBroker` | Alpaca spot, or Hyperliquid perps (eligible operators, ≤ 1.5×) | regime gated by BTC's 200DMA |
| Memecoins | scout on DexScreener prices | **not wired** (`hive/execution/jupiter.py` lists the four prerequisites) | ≤ 1%/token, ≤ 3% total, ≤ 3 open, −40% stop, 35% trail, 72h time stop; expected to go to zero |

## 7. Going live (checklist)

1. ≥ 4 weeks of paper, reports committed, both lines reviewed (rules-only vs rules+LLM, net of AI cost).
2. Trading-only Alpaca live keys; `HIVE_MODE=live`, `HIVE_LIVE_CONFIRM=I_UNDERSTAND_THIS_TRADES_REAL_MONEY`; run with `hive run --live`.
3. Fund exactly $100. Any later deposit is a public `capital_in` ledger entry.
4. `state/KILL` stops all order placement instantly. Death ($25 or −75%) is final.

## 8. What to expect

On 2001–2020 data the rules make 2–4% a year with a −20% to −30% drawdown
(`BACKTEST_RESULTS.md`); nothing in the evidence supports fast, large returns
without a matching chance of ruin. The study's real output is the daily,
auditable comparison of buy-and-hold vs rules-only vs rules+AI, net of the
AI's own costs — and the record of every iteration the operator tried.
