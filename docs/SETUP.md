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
| around the clock (`watch.yml`) | `hive trench` + `hive sweep --minutes 7 --round N --rounds 6`, six rounds a session | **the lab**: sessions of six seven-minute rounds that chain themselves back to back (the hourly schedule only restarts the chain); each round scans the trenches on paper and backtests the next cells of the strategy space (the grid of entry families, exits and sizing first, then refinements around the best cells, then mutations of the best cells, which never run out) in- and out-of-sample against the parent; winners take a paper seat (best waiting cell first); every cell is kept in `reports/lab/` and committed after every round; a round yields at once if a trading, weekly or promotion run is waiting; no LLM |
| after every lab round | `hive watch` + `hive ui build` + Pages deploy | heartbeat (live positions and prices) and a fresh hub every eight minutes or so; the open page checks for a newer build every thirty seconds and re-renders in place, with every time in your own zone |
| every day 03:41 UTC | `hive run` (safety net) | trades the bar only if the 00:37 run was dropped; otherwise the once-per-bar guard makes it a no-op for orders and for the sub-agents' shadow days |
| every day after the cycle | `hive sweep --cells 3` | three more lab cells after the daily cycle |
| automatic, any day | promotion by evidence | a sub-agent that beat the parent for 10 shadow days by ≥ 1% is promoted by the operator into the primary (`state/promoted_overrides.json`); a baseline sub-agent on the pre-registered rules reverts it if it wins back by ≥ 2% over 21 days. Actions → **promote-sub-agent** stays as a human override |
| automatic, any day | punishment by evidence | a sub-agent 3% behind the parent after 10 days retires; one whose own paper book is down 15% from its start dies the same day; monitors expire after 28 days (config 1.5.0) |

## 4b. Watching it work

* **The hub** (GitHub Pages): rebuilt and published after every lab round
  (about every eight minutes, around the clock); the open page re-renders in
  place when a newer build is up, and every time on it is in your own zone.
  The badge reads LIVE when the build is under 15 minutes old, DELAYED under
  75, STALE beyond. *Right now* says what each part of the firm is doing; *The
  firm, live* draws every agent, and a node pulses only when the record shows
  it acted in the last twelve minutes; the *live wire* lists every test, trade
  and scan as it lands; the lab shows leaderboards, a map of every test, the
  curve of any test you click, the best result found over time, backtests per
  hour and a gallery of the newest sixty curves; *Rules in force* lists every
  setting, its value and who can change it, read from the config. GitHub starts scheduled
  jobs late by up to an hour at busy times; the rounds keep the firm busy for
  most of the hour whenever the job actually starts. The *Strategy lab*
  cards show every backtest the firm has ever run: a map of out-of-sample
  return against drawdown (filled dots beat the parent, hollow ones were
  rejected, the diamond is the parent), the equity curve of any test you click
  (in-sample or out-of-sample, against the parent and buy & hold), the full
  log with a filter box, and what is next in the queue. The *Live book* card
  carries the broker's positions and prices from the heartbeat; *The desks*
  card shows what every employee said about every candidate on the last run
  (news desk, numbers desk, sentiment desk, debate, portfolio manager), the
  news watch on open positions, and the memecoin scout's latest scan; the
  *Firm activity* table interleaves the lab's tests with the desks' events.
* **Journals** (`state/journal/<date>.jsonl`, hash-chained, committed daily):
  every headline digest, analyst view, decision, order, exit and cost.
* **Daily report** (`reports/daily/<date>.md`) and the **research log**
  (`state/research_log.jsonl`) the operator writes every Sunday.
* The news desk reads Finnhub (with a key) or Google News, plus keyless
  StockTwits and Reddit posts (`HIVE_SOCIAL_DATA`, on by default). Only the
  quarantined reader ever sees that text.

## 5. How the hive grows (the one rule)

* **The objective** (config 2.1.0, rules updated in 2.5.0): pass prop-firm
  evaluations. Every backtest is also run as a series of simulated two-phase
  challenges (one starting every 5 days: +8% in phase 1, then +5% from a fresh
  balance in phase 2, at least 5 trading days in each, within 60 trading days,
  without a day worse than −5% or a fall of 10% below the phase's start; a
  FundedNext Stellar 2-Step style challenge), and the lab ranks and judges
  cells by the share they pass out of sample, growth breaking ties. A cell
  earns a paper seat when its pass rate beats the parent's and it made money
  in sample (today's code still also rejects a cell whose out-of-sample
  drawdown is more than 10% worse than the parent's; see the owner's pending
  decisions in `docs/REVIEW_LOG.md`). Set `objective = "growth"` under
  `[operator]` to go back to ranking by growth alone.
  Since config 2.4.0 the number the lab climbs is the **prop score**: each
  simulated evaluation that passes earns 1, each that breaks the daily or total
  loss limit costs `prop.breach_penalty` (0.5: the fee is lost), a timeout costs
  nothing; ties go to the faster median pass. The lab runs one backtest per CPU
  core (four on a GitHub runner), reuses every computed indicator across cells
  that share it, and gives forex and gold three turns for every one stock cell.
  Since 2.5.0 the lab also searches fixed reward-to-risk exits
  (`take_profit_r`: 2 is the 1:2 trade, decided on the close and sold at the next
  open, the same in the backtest and in the daily run), tighter initial stops,
  holds as short as 5 days, up to 12 positions and 6 new entries a day.
  Since 2.6.0 the loss limits are checked as firms check them, against equity
  during the day: the backtest also records each day's worst equity (every open
  position at its worst price of the day at once), a day's loss is measured from
  the previous close in units of the starting balance, and the target still
  counts on the close (`prop.intraday_breach`).
* **Punishment** follows the same rules: a sub-agent whose own paper book
  breaks the daily or total loss limit is retired that day, as a firm would
  close the account; one that lags the parent for 10 days is retired; one
  down 75% dies.
* **The models are paid by what they add** (config 2.3.0,
  `hive/survival/earned.py`). The daily desks' budget is the tier's share of
  equity times a multiplier set by what the AI-filtered primary added over the
  rules-only shadow in the last 30 days, after its own costs (+1% = +25%,
  between 0.25x and 4x). The operator's research pot is multiplied by its
  scorecard: its proposals' pass rate against the lab's free search, plus 0.5x
  per proposal later promoted (0.25x to 4x). The operator is shown its record
  and how it is paid before it proposes. Every call is still paid from the
  bankroll and capped at $2 a day.
* **The drawdown halt** (liquidate at 25% below the peak, pause 21 days) applies
  with real money only (config 2.3.0). Backtests and paper books never pause.
* **Sizing is the evidence's** (config 2.2.0): risk per trade, the largest
  position in a stock, ETF, coin or DOGE/SHIB/PEPE (up to 1.0, which is no cap),
  open risk, the volatility target, the halt and the crypto sleeve are lab
  levers in `config/bounds.toml`, and so is *how* risk per trade is set:
  a fixed share, or `sizing_mode = "edge"`, where each book risks a fraction
  of the Kelly bet its own last 50 closed trades imply (between 0.25% and a
  ceiling the lab also picks). The starting values are where they were; they
  move only when a cell that moves them wins. Leverage is not available at
  this size.
* **Tiers** cap how many paper sub-agents may run at once: hospice 0, survival
  2, base 4, growth ($250+, 30 net-positive days) 6, hive ($1k+, 60 days) 8.
  Paper sub-agents cost nothing but compute, so the firm can start iterating on
  day one.
* A child is a **paper portfolio** with its own sleeve (`stocks`, `crypto`,
  `all`, `meme`) and config overrides. Its value is measured against the
  parent over the same days (value of information). Losers retire themselves.
* The operator may also propose a **monitor**: a news-channel sub-agent that
  watches up to five topics or symbols and files bounded daily reports the
  portfolio manager reads. Monitors cost about a cent a day, take a seat, and
  expire after 28 days unless re-proposed.
* The operator **can**: propose variants (entries, exits and sizing levers)
  and monitors, read every journal, recommend. Budgets and the universe are
  code's; promotion is the evidence rule's. Nothing in the firm needs a person.

## 5b. Prop-firm readiness: how the firm tells you it is ready

Every daily cycle runs `hive prop`, which reads each book (the primary, and
every active sub-agent) as a prop-firm evaluation that began when its strategy
last changed, and checks four things:

1. at least 30 trading days of paper record;
2. never broke the daily or total loss limit, before or after reaching the
   target (a funded account keeps the same limits);
3. reached the profit target on paper;
4. its strategy passes at least 50% of simulated evaluations out of sample
   (the primary uses its weaker sleeve, since one account carries both).

The result is in `state/prop_readiness.json` and in the hub's **Prop**
section. The first time a book clears all four, the daily workflow opens a
GitHub issue titled "HIVE is ready for a prop-firm evaluation" with the
book's record and checklist. GitHub emails you about it if you watch the
repository (**Watch → All activity**, or **Custom → Issues**). Deposits recorded
with `hive capital-in` are taken out of the record, so a deposit never counts
as a gain.

When the issue arrives:

1. Pick a firm that funds what HIVE trades (US stocks and ETFs, or crypto) and
   allows automated trading. Most firms trade futures or forex; check the
   instruments and the automation policy before paying.
2. Copy that firm's exact rules into `[prop]` in `config/strategy.toml`
   (target, daily and total loss, trailing or static, minimum days). The lab
   rescores its best cells under the new rules within a session; if the book
   still clears the checklist, it is ready for that firm.
3. Buy the evaluation yourself. HIVE has no connection to any prop firm: how
   orders reach the firm's platform is a follow-up once a firm is chosen.

The simulator sees daily closes only. An intraday breach the closes do not
show is invisible to it, so a real evaluation is somewhat harder than the
paper one.

## 6. Sleeves and what "live" means for each

| Sleeve | Paper | Live | Notes |
|---|---|---|---|
| Stocks/ETFs | `PaperBroker` | Alpaca | fractional from $1; the core of the study |
| Crypto (BTC/ETH/SOL) | `PaperBroker` | Alpaca spot (Hyperliquid perps adapter exists in `hive/execution/hyperliquid.py` but is **not routed**: the daily run sends every order to the one primary broker; wiring a second venue is an owner decision, see §6b) | regime gated by BTC's 200DMA; position cap and sleeve size are lab levers (start 30% and 40%, ranges up to 100%) |
| Memecoin majors (DOGE/SHIB/PEPE) | `PaperBroker` | Alpaca spot | same trend rules and BTC regime gate; position cap is a lab lever (start 10%, range 2% to 100%); they rarely sit above a rising 200-day MA, so entries are rare by design |
| Forex, gold, silver, index futures (the "macro" sleeve, config 2.3.0) | the forex & gold desk and macro sub-agents, on the lab's simulator | **no venue wired** (a forex/CFD broker or a prop firm's platform; see §6a) | 10 major pairs, XAUUSD, XAGUSD, US500, NAS100, US30 from Yahoo's daily series (metals and indices are continuous futures); long and short by default; CFD-like costs, no overnight swap charged; no leverage, so moves are small: an unlevered forex book rarely makes 10% in 60 days |
| Memecoins | scout on DexScreener prices | **not wired** (`hive/execution/jupiter.py` lists the four prerequisites) | ≤ 1%/token, ≤ 3% total, ≤ 3 open, −40% stop, 35% trail, 72h time stop; expected to go to zero. pump.fun-sized launches and X are deferred by the owner (2026-10-20) |

## 6a. Shorts, forex and gold (config 2.3.0)

* **Shorting** is a lab lever per sleeve: `stocks.direction`, `crypto.direction`
  and `macro.direction` are `long`, `short` or `both`. A short is the long rules
  applied to the market seen upside down (a breakdown below the lows, a stop
  above the price, a trailing stop that follows the lows); its money is real:
  it sells at the bid, covers at the ask, and posts its full value from cash,
  so there is no leverage and a short the price runs against loses real money.
  Never long and short one market at once. New stock and crypto shorts are
  full size when the benchmark regime is red, halved in yellow, and scaled by
  `short_green_mult` (a lever, default 0) in a green bull market; the crash
  guard blocks them. Stocks and crypto stay long by default until a cell that
  shorts wins.
* **Where shorts and forex trade today**: in the lab, and on paper on the
  lab's own simulator, one new day at a time from the day a book was seated,
  on its own $100 book (the forex & gold desk, and any sub-agent that shorts
  or trades macro). They are punished like every other book but never
  promoted into the primary: the Alpaca account trades long stocks and spot
  crypto only (Alpaca shorts need a margin account of at least $2,000; crypto
  cannot be shorted there; it offers no forex). The prop-firm readiness check
  covers them, so a forex or gold book that clears it opens the issue too.
* **To trade them for real** a venue has to be wired and approved by you:
  a forex/CFD broker with an API, or the platform of the prop firm you pick
  (most forex and gold prop firms run MetaTrader 5 or cTrader). That is a
  follow-up once a book is ready and a firm is chosen.

## 6b. Paper-trading everything: what runs today, and what each venue needs from you

| Market | Runs today on paper | What you need to do |
|---|---|---|
| Stocks and ETFs | yes, Alpaca paper, daily | nothing more |
| BTC, ETH, SOL; DOGE, SHIB, PEPE | yes, Alpaca paper, every day including weekends | nothing more; for live, the Alpaca account must be crypto-enabled |
| Established memecoins on Solana (meme scout) | yes, its own paper book, daily, keyless (DexScreener, GeckoTerminal, RugCheck) | nothing |
| Brand-new launches, "the trenches" (trench scout) | yes, its own $100 paper book, every lab round (about every eight minutes while the lab runs), keyless | nothing |
| Hyperliquid perps (Hyperliquid desk) | yes, its own $100 paper book, daily, on Hyperliquid's **public** prices (no account, no keys, no orders), 1x, taker fee and hourly funding charged | nothing |
| Social pulse (StockTwits trending, Reddit cashtags, DexScreener boosts) | yes, every lab round, keyless | nothing; for X, add a paid X API bearer token as the `X_BEARER_TOKEN` secret |

**The trench scout** discovers new Solana pools (GeckoTerminal new pools,
DexScreener latest profiles and boosts), re-quotes them on DexScreener, and
buys on paper only launches between 20 minutes and 24 hours old with at least
$15k liquidity, $10k of volume and 40 buyers in the last hour, more buyers than
sellers, no vertical candle and no dump in the last five minutes, that pass
RugCheck. 5% of its book per token, at most eight open, half the book at most;
exits are a 30% hard stop, a 25% trail from the peak, half taken at +100% and a
six-hour time stop, plus exit at zero when liquidity is pulled (rugged) or the
token stops quoting for three scans (vanished). Fills assume 5% slippage and
1% fees each way. Exits are evaluated at each scan, so they fill at the next
observed price, not at the stop: the paper result is optimistic about gaps it
cannot see. The scout is judged on its own book and dies at −75% like any
sub-agent. Nothing in it can place a real order.

**The Hyperliquid desk** joins the firm on its first daily cycle: it reads
Hyperliquid's 25 most traded perps from the public `info` endpoint, keeps a
separate price store, and runs the firm's crypto rules on the coins with a year
of daily history, on its own $100 paper book at 1x, with Hyperliquid's 0.045%
taker fee and the hourly funding a long would have paid. It is a scout: no
seat, judged on its own book. It reads public data only and cannot place an
order anywhere.

**The social pulse** reads StockTwits' trending symbols, the hot posts of one
of six Reddit communities in rotation (anonymous Reddit allows a cloud runner
one read at a time, so each community is refreshed about once a session) and
DexScreener's boosts every lab round, and keeps only
symbols and counts (no post text, so nothing it reads can carry instructions
into a model). Names on two or more sources reach the operator's weekly
context, where it can propose a news monitor for them. X is paid: with a
bearer token from developer.x.com as the `X_BEARER_TOKEN` secret, the pulse
adds the last day's X post counts for the names trending elsewhere.

**Trading on Hyperliquid itself** (testnet or real) is a separate step:

1. *Hyperliquid's own testnet* (real exchange mechanics, fake money). You
   create a wallet, make a small USDC deposit on mainnet (the testnet faucet
   requires one), create an API/agent wallet at app.hyperliquid.xyz → More →
   API, and add the secrets `HL_AGENT_PRIVATE_KEY`, `HL_ACCOUNT_ADDRESS` and
   `HL_TESTNET=true`. Hyperliquid's terms
   exclude US persons and residents of some other countries, so
   `HL_OPERATOR_NOT_RESTRICTED_PERSON=true` must be a true statement about
   you. The routing change that sends the crypto sleeve to it was refused by
   this session's permission gate; it needs to be allowed and asked for again.
2. *Real money on Hyperliquid*: everything in route 1, then `HL_TESTNET=false`,
   a deposit recorded with `hive capital-in`, and `HIVE_MODE=live` with the
   confirmation phrase.

**Live DEX memecoins** stay off: `hive/execution/jupiter.py` refuses to
construct until a dedicated Solana wallet holding only the sleeve's money, an
RPC endpoint with the Jupiter swap API, pre-trade rug checks with a simulated
sell, and an always-on process for software stops all exist.

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
