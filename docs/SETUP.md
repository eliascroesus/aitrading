# Setup — from zero to a running study

Everything the system needs, in the order to set it up. Items marked **free**
cost nothing; the whole stack runs for roughly **$2–7 a month**, which on a
$100 bankroll is the main hurdle the strategy must clear (see `RESEARCH.md`).

## 1. Accounts and keys

| Need | Service | Cost | What to do |
|---|---|---|---|
| LLM for the agents and the operator | **Anthropic API** — https://console.anthropic.com | pay per token; the in-code daily budget is ~0.05% of equity (≈ $0.05/day at $100) | Create a key → `ANTHROPIC_API_KEY`. Set a **monthly spend cap** in the Console as a second line of defence. Models default to `claude-opus-5-5` for decisions and `claude-haiku-5-5` for bulk roles (`HIVE_LLM_MODEL`, `HIVE_LLM_CHEAP_MODEL`). |
| US stocks/ETFs (and spot crypto) | **Alpaca** — https://app.alpaca.markets | **free** paper; live has no commissions, $1 minimum fractional orders, no margin/shorting under $2k | Create the account. **First** reset the paper account to **$100** (the *Reset* button next to the equity figure asks for the new amount; a reset issues new API keys, so reset before you copy keys anywhere). **Then** generate paper keys → `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER=true`. The run refuses to trade if the broker reports more than twice the study's bankroll, so an un-reset $100,000 account fails loudly instead of trading 1,000× size. Crypto on Alpaca covers BTC, ETH, SOL and the large-cap memecoins DOGE, SHIB, PEPE; anything the account cannot trade is skipped and journaled as `untradable`. The PDT rule no longer applies (retired 2026-06-04). |
| Crypto perps (optional) | **Hyperliquid** — https://app.hyperliquid.xyz | **free**; 0.045%/0.015% fees; $10 minimum order | **Its terms restrict US persons.** Only if eligible: create an **API/agent wallet** (cannot withdraw) → `HL_AGENT_PRIVATE_KEY`, `HL_ACCOUNT_ADDRESS` (the master address), `HL_TESTNET=true`, `HL_OPERATOR_NOT_RESTRICTED_PERSON=true`. Fund with USDC on Arbitrum (≥ 5 USDC). Testnet faucet needs a prior mainnet deposit. |
| Company news + earnings calendar (optional) | **Finnhub** — https://finnhub.io | **free** (60 calls/min) | Key → `FINNHUB_API_KEY`. Without it the system uses Google News RSS (free, keyless) and yfinance earnings dates. |
| Memecoin scanner | **DexScreener**, **RugCheck** | **free**, keyless | Nothing to set up. The scout is paper-only (see §6). |
| X / social sentiment (optional, off by default) | X API (pay-per-use, ~$0.005/post) or xAI Grok `x_search` | ~$1–4/month if enabled | Not wired by default; the research shows 1-day sentiment is a veto/tie-breaker, not an entry signal. |
| Market history for backtests | Qlib US daily dump, CoinMetrics CSVs, PIT S&P 500 membership, VIX | **free** | See `DATA.md`; `hive data seed`. |

Put keys in `.env` (copy `.env.example`). Never commit it.

## 2. Where it runs

**Option A — GitHub Actions (recommended, free for a public repo).**
1. Push this repository to GitHub (public makes the study auditable) and merge
   the work onto the **default branch**: GitHub runs schedules only from there.
2. Settings → Secrets and variables → Actions: add `ANTHROPIC_API_KEY`,
   `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, optionally `FINNHUB_API_KEY`; add
   `HIVE_MODE=paper` as a secret (change to `live` and add `HIVE_LIVE_CONFIRM`
   only at go-live).
3. Settings → Pages → Source: **GitHub Actions**. `.github/workflows/pages.yml`
   publishes `reports/dashboard/` after every daily run.
4. Actions → **seed-data-cache** → *Run workflow* once (≈ 10 minutes). It
   downloads the reference files and the 2000–2020 history and stores them
   in the Actions cache. Optional: the daily run bootstraps itself from a
   cold cache with ~2.5 years of bars, and the weekly run fetches the full
   history when it needs it. Price data never goes into git.
5. `.github/workflows/daily.yml` runs **every day at 00:37 UTC**, after the US
   close and the crypto UTC-day close: restore cache → `hive data refresh` →
   `hive run` → `hive ui build` → commit `state/` and `reports/`. On weekends
   and US holidays only the crypto sleeve has a new bar, so only it trades. The
   git history **is** the public record (decision journals included).
6. `.github/workflows/weekly.yml` runs on Sundays: `hive review` (auditor),
   `hive operate` (the iteration loop) and `hive allocate` (capital moves one
   step toward the desk that earned, inside 20–60% for the crypto sleeve). New
   shadow children, every verdict and every allocation change land in `state/`
   and on the dashboard.
7. Caches unused for 7 days are evicted by GitHub; the next run simply
   rebuilds (step 4 happens again, automatically).

**Option B — your own machine / VPS.** `uv venv && uv pip install -e .`, a
cron entry for the same three commands, and `hive ui serve` for the dashboard
at http://127.0.0.1:8787.

**Cloud sandbox note.** If you run it from a Claude Code cloud environment,
allow these hosts in the environment's network settings: `query1.finance.yahoo.com`,
`query2.finance.yahoo.com`, `paper-api.alpaca.markets`, `api.alpaca.markets`,
`data.alpaca.markets`, `api.hyperliquid.xyz`, `api.hyperliquid-testnet.xyz`,
`finnhub.io`, `news.google.com`, `data.sec.gov`, `api.dexscreener.com`,
`api.rugcheck.xyz`, `api.anthropic.com`, `raw.githubusercontent.com`,
`github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`.

## 3. First run

```bash
cp .env.example .env            # fill keys
uv venv --python 3.13 && uv pip install -e ".[dev]" && pytest -q
bash scripts/bootstrap_data.sh --history            # reference files + 2000-2020 history (DATA.md)
hive data refresh                                   # bring the cache to today
hive run                                            # first paper cycle (primary + rules-only shadow)
hive hive spawn "crypto sleeve" --sleeve crypto     # optional: first sub-agents by hand (the operator adds more on Sundays)
hive hive spawn "meme scout" --sleeve meme          # optional: on-chain memecoin scout, paper only
hive ui build && hive ui serve                      # the dashboard
hive status
```

## 4. The daily / weekly rhythm

| When | Command | What happens |
|---|---|---|
| every day 00:37 UTC | `hive run` | regime → news desk on open positions → signals → LLM filter (if key) → risk → orders → ledger → report; children trade their paper books; weekends: crypto only |
| every day | `hive ui build` | dashboard rebuilt from state |
| weekly (Sunday, `weekly.yml`) | `hive allocate` | the crypto sleeve's share of equity moves ±5 points toward the desk that earned, within 20–60%, from realised P&L; no LLM |
| weekly (Sunday, `weekly.yml`) | `hive review` | the auditor reads the journals and proposes changes (never applies them); paid, like `hive operate`, from the research pot that unspent daily budget accrues into |
| weekly (Sunday, `weekly.yml`) | `hive operate` | the operator proposes ≤ 3 config variants inside `config/bounds.toml`, code backtests them in/out of sample, winners become shadow children if the tier allows |
| every day after the cycle | `hive sweep --cells 3` | backtests three untested cells of the strategy space (entry families, exits) in- and out-of-sample; winners take a paper seat; no LLM |
| when a sub-agent shows "ready to promote" | Actions → **promote-sub-agent** → Run workflow with its id | **a human** promotes; its overrides become the primary's live configuration (`state/promoted_overrides.json`) and it frees its seat |

## 4b. Watching it work

* **Dashboard** (GitHub Pages): the *Live book* card is refreshed every hour
  by `hourly-heartbeat` with the broker's positions and prices; *The desks*
  card shows what every employee said about every candidate on the last run
  (news desk, numbers desk, sentiment desk, debate, portfolio manager), the
  news watch on open positions, and the memecoin scout's latest scan.
* **Journals** (`state/journal/<date>.jsonl`, hash-chained, committed daily):
  every headline digest, analyst view, decision, order, exit and cost.
* **Daily report** (`reports/daily/<date>.md`) and the **research log**
  (`state/research_log.jsonl`) the operator writes every Sunday.
* The news desk reads Finnhub (with a key) or Google News, plus keyless
  StockTwits and Reddit posts (`HIVE_SOCIAL_DATA`, on by default). Only the
  quarantined reader ever sees that text.

## 5. How the hive grows (and what it is not allowed to do)

* **Tiers** cap how many paper sub-agents may run at once: hospice 0, survival
  1, base 2, growth ($250+, 30 net-positive days) 3, hive ($1k+, 60 days, human
  approval) 5. Paper sub-agents cost nothing but compute, so the firm can start
  iterating on day one; a live allocation to any of them is always a human
  decision (`hive hive promote <id> --approve`).
* A child is a **paper portfolio** with its own sleeve (`stocks`, `crypto`,
  `all`, `meme`) and config overrides. Its value is measured against the
  parent over the same days (value of information). Losers retire themselves.
* The operator may also propose a **monitor**: a news-channel sub-agent that
  watches up to five topics or symbols and files bounded daily reports the
  portfolio manager reads. Monitors cost about a cent a day, take a seat, and
  expire after 28 days unless re-proposed.
* The operator **can**: propose variants and monitors, read every journal, recommend.
  It **cannot**: change risk limits or budgets, touch the universe, spend
  beyond the ledger's daily budget, or promote to live. Those are code and
  humans, on purpose — the research record of agents under "survive at all
  costs" framing is one of rule-bending, not of returns.

## 6. Sleeves and what "live" means for each

| Sleeve | Paper | Live | Notes |
|---|---|---|---|
| Stocks/ETFs | `PaperBroker` | Alpaca | fractional from $1; the core of the study |
| Crypto (BTC/ETH/SOL) | `PaperBroker` | Alpaca spot, or Hyperliquid perps (eligible operators, ≤ 1.5×) | regime gated by BTC's 200DMA; up to 30% per position, 40% sleeve |
| Memecoin majors (DOGE/SHIB/PEPE) | `PaperBroker` | Alpaca spot | same trend rules and BTC regime gate; 10% cap per position; they rarely sit above a rising 200-day MA, so entries are rare by design |
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
