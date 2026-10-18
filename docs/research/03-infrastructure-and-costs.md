# Infrastructure research: $100 multi-agent trading bot (as of 2026-10-18)

**How this was researched, and how far to trust it.** Most official doc hosts (docs.alpaca.markets, hyperliquid.gitbook.io, docs.x.ai, finnhub.io, docs.github.com) could not be reached from this sandbox, either through DNS failure or a proxy block. So most facts come from search-engine extracts of those official pages, checked against third-party sources. GitHub pages were fetched directly: SDK releases, source code and the CCXT exchange list. The shared web-search budget ran out near the end, so the GitHub Actions limits and a few other details come from memory. Tags used below:
- **[OFFICIAL]**: an official page's text, seen in a search extract or fetched directly.
- **[3P]**: several third-party sources agree.
- **[UNVERIFIED]**: the sources conflict, or the claim comes from memory. Check it before relying on it.

---

## 1. Brokers and execution

### 1.1 Alpaca (US stocks/ETFs and crypto)

- **What it offers.** A REST and websocket brokerage API with commission-free US stocks and ETFs, crypto spot, options, and free paper trading. It now also offers **24/5 overnight trading** (8 PM–4 AM ET, run through the Blue Ocean ATS). Overnight orders must be **limit orders** with TIF `day` or `gtc`. Margin in the overnight session is capped at 2x. [OFFICIAL] https://docs.alpaca.markets/docs/245-trading
- **Fractional and notional orders.** The minimum is **$1 notional**, with up to 9 decimal places. Quantity-based fractional orders under $1 are rejected. Market, limit, stop and stop-limit orders work for both fractional and notional orders. Extended hours (4:00–9:30 AM and 4:00–8:00 PM ET) work only with limit orders, and the asset must be flagged `fractionable`. [OFFICIAL] https://docs.alpaca.markets/docs/fractional-trading · https://alpaca.markets/support/can-we-submit-orders-smaller-than-1-usd-in-notional-value
  - **A $100 account can use notional orders.** For example, `MarketOrderRequest(symbol="SPY", notional=12.50, ...)`.
  - Fractional orders historically required `time_in_force=day`, and fractional shares can't be shorted. [UNVERIFIED for 2026]
- **Commission and fees.** Stocks have no commission. SEC and FINRA TAF regulatory fees are passed through on sells and amount to pennies. [UNVERIFIED rates]
  - Crypto uses maker/taker tiers on 30-day volume. **Tier 1 (≤$100k) is 0.15% maker / 0.25% taker**, falling to 0% / 0.10% at the $100M+ tier. [OFFICIAL] https://docs.alpaca.markets/docs/crypto-fees
  - The minimum crypto order is about $1 notional. Check each asset's `min_order_size` and `min_trade_increment` in `/v2/assets`.
- **Account minimums and margin.** Every account is opened as a margin account. **Under $2,000 of equity you get 1x buying power, and no margin or shorting.** [OFFICIAL] https://docs.alpaca.markets/docs/margin-and-short-selling
  - There is no deposit minimum for individuals. [UNVERIFIED: memory and marketing claims]
  - Crypto has historically been unavailable to New York residents. [UNVERIFIED for 2026]
- **The PDT rule at Alpaca.** The rule is gone as of **2026-06-04**. The `PDT/DTBP` fields were deprecated, with a **sunset on 2026-07-06**; use `buying_power` instead.
  - "Intraday Buying Power" replaces the old day-trade buying power.
  - Unmet intraday margin calls within 5 business days can restrict an account for up to 90 days. [OFFICIAL] https://docs.alpaca.markets/docs/understanding-finras-new-intraday-margin-rule-and-the-end-of-pdt · https://docs.alpaca.markets/us/v1.1/changelog/2026-06-04-pdt-f400e9e
- **Python library.** `alpaca-py` is at **v0.44.0 (Aug 11)**. Its classes include `TradingClient`, `StockHistoricalDataClient`, `CryptoHistoricalDataClient` and `NewsClient`. v0.44.0 is a *breaking* release: order prices are now validated client-side, so round limit prices to the allowed tick sizes. [OFFICIAL] https://github.com/alpacahq/alpaca-py/releases
- **Gotchas.**
  - Paper resets let you pick a starting balance, so set it to **$100** to mirror the live account. A reset issues **new API keys**.
  - Several 2026 forum threads report **new paper accounts stuck at $0 buying power**. [3P] https://forum.alpaca.markets/t/new-paper-accounts-all-showing-0-buying-power-balance-9-accounts-is-this-a-known-issue/19380
  - Paper fills are simulated and optimistic. They don't model IEX-only quotes or slippage on small-cap names.

### 1.2 Pattern Day Trader rule, status in 2026
- The **SEC approved** FINRA's amendments to Rule 4210 (SR-FINRA-2025-017) on **2026-04-14**. **Regulatory Notice 26-10** (2026-04-20) set the **effective date at 2026-06-04**.
  - The PDT designation, the 4-trades-in-5-days count, and the **$25k minimum** are all removed.
  - They are replaced by risk-based **intraday margin**: firms monitor in real time or at end of day.
- **Brokers have until 2027-10-20 to implement.** Until a given broker switches, its old rules may still apply.
- **The $2,000 minimum equity for margin still applies.** A $100 account is therefore a 1x cash-style account. It can day-trade freely, but only with its own buying power. Whether same-day reuse of unsettled sale proceeds is unlimited under 1x is [UNVERIFIED]; test it live.
- Sources: [OFFICIAL/3P] https://advisorservices.schwab.com/story/sec-approves-scrapping-25000-day-trader-minimum · https://www.finra.org/sites/default/files/2026-03/FINRA-2025-017-Response-to-Comments-3-18-2026.pdf · https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/

### 1.3 Hyperliquid (crypto perps and spot, HIP-3 equity perps)

- **Python SDK.** `pip install hyperliquid-python-sdk`, latest **0.24.0 (Jun 4)**. Two relevant fixes: 0.20.1 "fix market orders for HIP-3 DEXs" and 0.21.0 "fix market_close for builder-deployed perps". [OFFICIAL] https://github.com/hyperliquid-dex/hyperliquid-python-sdk/releases
  - `Info(base_url, skip_ws, meta, spot_meta, perp_dexs: list[str] | None, timeout)`. Pass `perp_dexs=["", "xyz"]` to load native perps plus trade.xyz. HIP-3 asset ids start at 110000. [OFFICIAL, from source] https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/info.py
  - `candles_snapshot(name, interval, startTime, endTime)`, with times in ms.
- **Testnet.** Use `constants.TESTNET_API_URL`.
  - **The faucet (1,000 mock USDC) requires a prior mainnet deposit from the same address.** Email/Privy logins get different addresses on mainnet and testnet. [OFFICIAL] https://hyperliquid.gitbook.io/hyperliquid-docs/onboarding/testnet-faucet
  - Testnet asset indices and margin tiers differ from mainnet.
- **API ("agent") wallets.** Create one at app.hyperliquid.xyz/API and approve it from the master account.
  - The agent can trade but **cannot withdraw**.
  - Set `account_address` to the *master* address and `secret_key` to the agent key.
  - **Approvals are per network.** "User or API Wallet does not exist" means the wallet isn't funded or approved on that network. [OFFICIAL/3P] https://docs.chainstack.com/docs/hyperliquid-order-precision.md
- **Minimum order.** **$10 notional** (10 USDC on spot). The one exception is a reduce-only order that closes a position exactly.
  - Round sizes *down* to `szDecimals`. Prices allow at most 5 significant figures. [3P]
  - **With $100 this means at most about 9 open positions.** You also can't scale out of a $15 position in halves, so size positions at $20 or more.
- **Fees.** Tiers use 14-day volume, and spot volume counts double.
  - **Tier 0 perps: 0.045% taker / 0.015% maker.** Tier 1 (>$5M) is 0.040% / 0.012%.
  - HYPE staking discounts fees by 5–40%.
  - Spot base is about 0.070% / 0.040% [UNVERIFIED].
  - [OFFICIAL] https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees · cross-check https://www.dwellir.com/blog/hyperliquid-fees-explained
  - **Read your exact rate from `info.user_fees()` rather than hard-coding it.**
- **Leverage.** Each asset has its own `maxLeverage` in `meta`, and margin tiers lower it as notional grows. The docs' table showing BTC 40x and ETH 25x is labeled *testnet*.
  - Third parties put mainnet BTC/ETH at 40–50x, SOL around 20–40x, and long-tail alts at 3–20x. [UNVERIFIED] https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margin-tiers
  - **Read `meta` programmatically.**
- **Funding.** Paid **every hour** at 1/8 of the 8-hour rate. The interest component is 0.01% per 8h, and the premium is sampled every 5 s. **The cap is 4% per hour.** Funding is computed on the oracle price. [OFFICIAL] https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
- **Rate limits.**
  - Per IP: **1,200 weight per minute** on REST. `candleSnapshot` returns at most **5,000 candles**, with intervals from 1m to 1M.
  - **Per address:** a 10,000-request starting buffer, then **1 request per $1 of cumulative volume**. Once the buffer is gone, the limit is about **1 request every 10 s**. Cancels get a higher cap.
  - **A $100 bot that churns order modifications will exhaust the address budget.** Batch orders and avoid order-modify loops.
  - [OFFICIAL] https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits
- **Deposits.** Send **native USDC on Arbitrum One** (not USDC.e) to the bridge. The **minimum is 5 USDC; smaller deposits are lost.** Deposits credit in under a minute.
  - Withdrawals cost a **1 USDC fee** and arrive in 3–4 minutes.
  - You need a little ETH on Arbitrum for the deposit transaction. [OFFICIAL] https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/bridge2
- **HIP-3 builder-deployed perps.** Deployers stake 500k HYPE and set the oracle and leverage. **Margin is isolated only.**
  - **User fees are 2x normal**, with 50% going to the deployer. "Growth mode" markets cut fees by 90% or more [UNVERIFIED which markets]. [OFFICIAL] https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals
  - **trade.xyz** (dex `xyz`) is the largest. It lists stock and index perps such as `xyz:NVDA`, `xyz:TSLA`, `xyz:AAPL`, SP500 and XYZ100 (Nasdaq).
  - Leverage is roughly 10x for most stocks, 20x for NVDA/AAPL, 30x for XYZ100 and 50x for SP500 (as of May 2026). The taker fee was reported at 0.09%. [3P] https://docs.trade.xyz/about-trade-xyz/hyperliquid-xyz-and-hip-3 · https://www.datawallet.com/de/krypto/tradexyz-explained · https://forklog.com/en/?p=104909
  - **They are tradable via the API.** Pass `"dex":"xyz"` on info calls; coin names come back with the prefix, e.g. `xyz:NVDA`.
  - Symbols are *not unique* across deployers, so always key on the full `dex:SYMBOL`.
  - Sources conflict on hours: 24/7 versus Sunday 20:00 to Friday 20:00 ET. Expect wide spreads off-hours. [UNVERIFIED]
- **KYC and geography.** There is no KYC. However, the ToS classify **US residents as "Restricted Persons"**, enforced by IP geofencing; this was still in force as of August 2026.
  - **For a public study by a US person, Hyperliquid (including trade.xyz) is a ToS violation. Using a VPN would be visible in a public log.** Treat Hyperliquid as usable only if the operator is outside the US and other restricted jurisdictions. [3P] https://onekey.so/blog/ecosystem/geo-block-hyperliquid-dydx-us/ · https://www.ainvest.com/news/hyperliquid-28m-washington-push-won-open-2608/

### 1.4 CCXT
- **Hyperliquid:** listed as a DEX with **CCXT Certified and Pro** status. Credentials are `walletAddress` plus `privateKey`, and CCXT signs requests itself. `set_sandbox_mode(True)` switches to testnet.
  - HIP-3 dexes load through a markets option (`'dexes': ['xyz', ...]`).
  - Market orders need a price for slippage bounds. [OFFICIAL] https://github.com/ccxt/ccxt/wiki/Exchange-Markets · https://docs.ccxt.com/docs/comparisons/ccxt-vs-hyperliquid-api · https://docs.ccxt.com/docs/examples/py/hyperliquid-load-hip3-dexes
- **Alpaca:** listed (Pro) but **crypto only**, with no equities or options. Use `alpaca-py` for stocks. [OFFICIAL] https://docs.ccxt.com/docs/comparisons/ccxt-vs-alpaca-api
- **Recommendation:** use the native SDKs. CCXT is worth it only if you plan to add more crypto venues.

### 1.5 Other venues for $100
- **Coinbase Advanced.** The entry tier is about **0.40% maker / 0.60% taker**. The tier can be set by 30-day volume *or* by asset balance. Python library: `coinbase-advanced-py`. [3P] https://www.coinbase.com/en-gb/advanced-markets · https://brokerchooser.com/news/coinbase-vs-kraken-fee-structures-and-trading-features-compared--bbeea493
- **Kraken Pro.** Sources disagree: either 0.40% / 0.80% at the base tier after a **July 2026 fee overhaul**, or the historical 0.25% / 0.40%. [UNVERIFIED]
- **Conclusion:** for US-compliant crypto spot, **Alpaca at 0.15% / 0.25% is cheaper than Coinbase or Kraken**. Hyperliquid is about 5x cheaper still, but only for eligible non-US persons.

---

## 2. Market data

| Source | Free tier / limits | Python / endpoint | Gotchas |
|---|---|---|---|
| **Alpaca Data (Basic, free)** [OFFICIAL] | Real-time **IEX only**. 30 websocket symbols. **200 historical calls/min**. History since 2016. **SIP bars are available if the query ends more than 15 minutes ago**. Crypto data is included. Algo Trader Plus costs $99/mo. | `StockHistoricalDataClient.get_stock_bars(feed="sip"/"iex")` | The default feed is SIP, so a "latest" query returns 403/422 unless you pass `feed="iex"` or set end ≤ now−15min. IEX volume is roughly 2–3% of the tape. https://docs.alpaca.markets/us/docs/about-market-data-api |
| **yfinance** | Free and unofficial (scrapes Yahoo). Latest **1.7.0 (Aug 26)**. 1.5.2 fixed a breakage with curl_cffi ≥0.16. | `yf.download`, `Ticker.history` | **429 `YFRateLimitError`**. IP blocks have been reported lasting over 24 h. Shared cloud IPs such as GitHub Actions are hit or miss. Pin the version, batch with `yf.download([...])`, and cache to disk. https://github.com/ranaroussi/yfinance/releases · https://github.com/ranaroussi/yfinance/discussions/2431 |
| **Hyperliquid info API** [OFFICIAL] | Free. `candleSnapshot` returns ≤5,000 candles per call; intervals 1m–1M. 1,200 weight/min per IP. | `Info.candles_snapshot`, `meta_and_asset_ctxs`, `funding_history`, `l2_snapshot` | Pre-April 2023 candles are backfilled with **zero volume**, so filter `v>0`. Candle weight is quoted as both 2 and 60; reconcile against the docs. |
| **CoinGecko Demo** | Free key, **10,000 credits/mo**. The rate limit is quoted as both 30 and 100 calls/min [UNVERIFIED]. Basic plan is about $35/mo. | `api.coingecko.com/api/v3` with header `x-cg-demo-api-key` | Attribution is required. 10k/mo is about 330 calls/day, which is plenty for a daily run. https://docs.coingecko.com |
| **DexScreener** | **No key**. About 300 req/min for pair/search endpoints and 60/min for token-profile/boost endpoints [3P]. | `GET api.dexscreener.com/latest/dex/search?q=` and `/tokens/v1/{chain}/{addr}` | No historical OHLCV; snapshots only. https://www.coingecko.com/learn/coingecko-api-vs-dexscreener-api |
| **GeckoTerminal** | Free, beta. **30 calls/min** [OFFICIAL]. Keyless shared-IP pools may get only about 10/min. | `api.geckoterminal.com/api/v2/networks/{net}/pools/{pool}/ohlcv/{tf}` | **Free memecoin OHLCV.** Attribution is requested. https://apiguide.geckoterminal.com |
| **Birdeye** | Free "Standard": **30k compute units, 1 rps**, limited endpoints. Starter costs $99. [OFFICIAL] | REST with `X-API-KEY` header | Mostly Solana. The free tier is too small for scanning. https://docs.birdeye.so/docs/pricing |
| **Massive (formerly Polygon.io)** | **Basic $0: 5 calls/min, end-of-day/15-min delayed data**, with 2 years (or 12 months) of history [UNVERIFIED]. Starter $29. Rebranded 2025-10-30; old keys still work. | `massive`/`polygon-api-client` (`api.polygon.io` still serves) | Fine for end-of-day grouped bars (the whole market in one call). https://massive.com/pricing |
| **Tiingo** | Free: **500 unique symbols/mo, 50 req/hr**, 1,000/day [UNVERIFIED]. No news or fundamentals on the free plan. Paid from about $30/mo. | `tiingo` package, REST | A good clean end-of-day backup. https://www.tiingo.com/about/pricing |
| **Stooq** | Free, but **since about March 2026 it needs an API key** (obtained through a CAPTCHA). | Custom `requests` call | `pandas-datareader`'s `stooq` source is broken (HTML comes back instead of CSV). https://github.com/pydata/pandas-datareader/issues/1012 |

---

## 3. Fundamentals and earnings

| Source | Free tier | What you get | Gotchas |
|---|---|---|---|
| **SEC EDGAR** [3P] | Free, no key, **≤10 req/s**. | `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`, `/submissions/CIK….json`, `/api/xbrl/frames/...` | **A descriptive `User-Agent: "Name email@domain"` is mandatory**; without it you get a 403 and may be IP-banned. CIKs are zero-padded to 10 digits. Facts lag filings, and tag names vary by company. https://www.sec.gov/search-filings/edgar-application-programming-interfaces |
| **Finnhub** | **60 calls/min**, US only, personal use. [OFFICIAL] | Company news (about 1 year), earnings calendar (about 1 month), `/stock/earnings` (EPS surprises), `/stock/recommendation` (trends), basic financials. Package: `finnhub-python`. | The **price target (`/stock/price-target`) is likely premium**; estimate plans run $75–200/mo per market. Candles have become premium. Endpoint entitlement is [UNVERIFIED], so a 403 means "not on your plan". https://finnhub.io/pricing |
| **Financial Modeling Prep** | **250 calls/day**, US only, end-of-day data. Many endpoints are restricted by symbol or date. [3P] | Profiles, statements (limited history), earnings calendar | Non-commercial only [UNVERIFIED]. Use the "stable" API paths. https://site.financialmodelingprep.com/developer/docs/pricing |
| **Alpha Vantage** | **25 requests/day, 5/min.** [3P] | `EARNINGS` (EPS plus estimates and surprises), `EARNINGS_CALENDAR` (CSV), `NEWS_SENTIMENT` (ticker sentiment scores) | Free availability of NEWS_SENTIMENT and EARNINGS is [UNVERIFIED]; neither is badged premium. 25/day is enough for about 5 tickers × 5 calls. https://www.alphavantage.co/documentation/ |
| **yfinance** | Free, unofficial. | `.calendar`, `.earnings_dates`, `.recommendations`, `.analyst_price_targets`, `.upgrades_downgrades`, `.quarterly_income_stmt`, `.info` | `.info` is the call most often rate-limited. Use it as the **free source of price targets and earnings dates**, with caching. |

---

## 4. News and social sentiment

| Source | Price / free tier | Python / endpoint | Gotchas |
|---|---|---|---|
| **X API** | **Pay-per-use since 2026-02-06**. Credits are prepaid; calls fail at a $0 balance. **About $0.005 per post read** (billed per post returned). User lookups about $0.010. Writes $0.015, or $0.20 with a URL. Monthly read cap is 2M or 3M posts. **Basic and Pro are closed to new users.** The free tier effectively ended (legacy users got a $10 voucher). [3P] | `tweepy` / raw v2 `GET /2/tweets/search/recent` (min `max_results=10`) | Set a spend cap in the Developer Console. https://docs.x.com · https://www.postproxy.dev/blog/x-api-pricing-2026/ · https://roboin.io/article/en/2026/02/08/x-transitions-api-to-pay-per-use-model-ending-free-plan/ |
| **xAI Grok + `x_search`** (Agent Tools, Responses API) | Changed **2026-09-21**: from a flat $5 per 1k *calls* to **$5 per 1k posts fetched and $10 per 1k profiles fetched**. Parent and quoted posts count. Web search is $5 per 1k calls. Tokens: **grok-4.1-fast at $0.20/$0.50 per MTok**; grok-4.3 at $1.25/$2.50. [3P; UNVERIFIED on docs.x.ai] Free credits ($25 signup, $150/mo for data sharing) have probably ended. | `xai-sdk` or an OpenAI-compatible client; `tools=[x_search(from_date, to_date, allowed_x_handles≤10)]` | **No documented max-results parameter.** The agent loop may fire several searches. Log `usage` server-side tool counts, and limit turns in the prompt. https://docs.x.ai/docs/guides/tools/search-tools · https://onehuman.io/news/grok-x-search-api-per-item-billing-september-2026 |
| **Reddit** | Since **Nov 2025 (Responsible Builder Policy)**, **every new OAuth app needs approval**; personal scripts are often ignored. Once approved, the free tier is 100 QPM. Unauthenticated `.json` reportedly returns 403 since 2026-05-30 [UNVERIFIED]. | `praw` (needs approved credentials) | Treat as unavailable unless you already have keys. https://www.redditapis.com/blogs/reddit-data-api-2026 |
| **StockTwits** | Official developer registration appears closed or unclear [UNVERIFIED]. The unauthenticated `api.stocktwits.com/api/2/streams/symbol/{SYM}.json` was still in use as of June 2026. | `requests` | **Cloudflare often blocks cloud IPs**, including GitHub runners. Messages carry user-tagged Bullish/Bearish labels, which make a free sentiment proxy *when it works*. |
| **Google News RSS** | Free, no key, undocumented. | `https://news.google.com/rss/search?q=NVDA+stock+when:1d&hl=en-US&gl=US&ceid=US:en` plus `feedparser` | Headlines and sources only. Links are Google redirects. Percent-encode operators. |
| **GDELT DOC 2.0** | Free. About **1 request per 5 s**. `maxrecords` ≤250. Rolling window of about 3 months [UNVERIFIED]. | `gdeltdoc` package; modes `ArtList` and `TimelineTone` | A good macro/news-volume and tone signal. Noisy for individual tickers. https://pypi.org/project/gdeltdoc/ |
| **Finnhub news** | Free (60/min). | `/company-news?symbol=&from=&to=`, `/news?category=crypto` | Headline plus summary. |
| **Alpaca News (Benzinga feed)** | Included with Alpaca market data and rate-limited by plan (200/min on Basic). **Free on Basic was true at launch; 2026 status [UNVERIFIED].** History back to 2015, about 130+ articles/day via REST; 600–900 headlines/day on the stream. | `alpaca.data.historical.news.NewsClient` | **The cheapest source of Benzinga-quality headlines.** Direct Benzinga APIs are sales-quoted (Pro Plus at about $457/mo). https://docs.alpaca.markets/us/docs/historical-news-data |
| **Marketaux** | Free: about 100 req/day, 3 articles per request, with entity sentiment [UNVERIFIED]. | REST | Small quota. https://www.marketaux.com/pricing |
| **NewsAPI.org** | Developer plan: 100 req/day, **24 h delay**, development use only. | REST | Useless for live trading. https://newsapi.org/pricing |
| **CryptoPanic** | **The free Developer plan was discontinued (removed 2026-04-01)**. Paid "Growth" plans are roughly $50/week to $199/mo [UNVERIFIED]. | `cryptopanic.com/api/<plan>/v2/posts/?auth_token=` | Skip it; use Google News RSS or GDELT for crypto. https://dlthub.com/context/source/cryptopanic |
| **LunarCrush** | Free "Hobby/Discover": **4 req/min, 100/day, market endpoints only**; social endpoints are paid. Individual is about **$90/mo** (also quoted at $24) [UNVERIFIED]. | REST/MCP | Too expensive for this budget. https://lunarcrush.com/support/how-much-does-lunarcrush-cost |

**What is the cheapest reliable way to get X sentiment for a handful of tickers daily?**
1. **The best value is one batched Grok call per day** on `grok-4.1-fast` with `x_search`. Set `from_date` to yesterday, ask for all 3–5 cashtags in one prompt, and tell it to run one or two searches at most.
   - At about 20–40 posts fetched, that is roughly **$0.10–0.20/day plus about $0.005 in tokens**, or **$3–6/month**.
   - Under the old per-call billing it would be **under $0.50/month**.
   - Verify by reading `usage`.
2. **The X API direct is similar or more expensive:** 10 posts × 3 tickers × 30 days × $0.005 = **$4.50/month**. You also have to write your own sentiment scoring, with Haiku at roughly cents.
3. **The free fallback is the StockTwits public stream** (when it isn't blocked) **plus Alpaca and Finnhub headlines.**
4. **Cost discipline:** call X sentiment only for the 1–3 *final candidates* after the cheaper filters have run. That brings it to about **$1–2/month**.

---

## 5. Web search APIs for agents

| API | Free tier | Price | Notes |
|---|---|---|---|
| **Claude web search tool** (`web_search_20260209`) | None | **$10 per 1k searches**, plus result tokens billed as input. **Web fetch (`web_fetch_20260209`) costs no fee beyond tokens.** [OFFICIAL, from the Anthropic docs and API skill] | Results can add 10–30k tokens per search. On Haiku 5.5 that is about $0.002. Set `max_uses`. https://docs.claude.com/en/docs/about-claude/pricing |
| **Tavily** | **1,000 credits/mo, no card** | Pay-as-you-go **$0.008/credit**. Basic search = 1 credit, advanced = 2. | **The best free option**: about 33 basic searches/day. https://docs.tavily.com/documentation/api-credits |
| **Brave Search API** | No separate free tier since 2026. The "Search" plan comes with **$5/mo of credit, about 1,000 queries** [3P]. | $5 per 1k requests | Card required [UNVERIFIED]. https://brave.com/search/api/ |
| **Exa** | Signup credits ($10–20) [UNVERIFIED] | **$7 per 1k requests** (10 results), plus $1 per 1k for extra results or summaries. Deep search $12–15 per 1k. [OFFICIAL changelog, 2026-03-03] | Good semantic search. https://exa.ai/docs/changelog/pricing-update |
| **Serper** | **2,500 free queries (one-time)** | Packs from $1.00 per 1k ($50) down to $0.30 per 1k. Credits may expire after 6 months [UNVERIFIED]. | Raw Google SERP results; cheapest at volume. https://serper.dev |

---

## 6. LLM cost (the dominant variable cost)

- **Claude pricing per MTok, input / output** [OFFICIAL, Anthropic API skill, cached 2026-10-06]:
  - **Haiku 5.5: $0.10 / $0.50** (prompts ≤100K tokens; $0.50 / $2.50 above that)
  - **Sonnet 5.5: $2 / $10**
  - **Opus 5.5: $4 / $20**
  - **Batch API: 50% off.** Cache reads cost about 0.1x input (0.05x on Opus 5.5), and 5-minute cache writes cost 1.25x.
- **Gotchas on Haiku 5.5:** thinking is adaptive and **on by default** (effort `medium`), and thinking tokens bill as output. Set `output_config={"effort":"low"}` on routine agents. `temperature` must be omitted or 1. Use structured outputs to get JSON.
- **Example daily run.** Seven Haiku agents at about 8k input and 1.5k output each cost about **$0.011/day, or $0.35/month**. A Sonnet 5.5 portfolio-manager step at 15k input and 3k output adds about **$0.06/day, or $1.80/month**. Batch halves both, as long as the run isn't latency-bound.
- **Grok 4.1 Fast ($0.20 / $0.50)** is also cheap, but use it only where `x_search` is needed.

---

## 7. Hosting and scheduling for a daily run

- **GitHub Actions.** Hosted runner prices were cut by up to 39% on 2026-01-01. The self-hosted "platform fee" announced for March 2026 was **postponed** (Dec 15, 2025 notice). **Public repos remain free.** [OFFICIAL] https://resources.github.com/actions/2026-pricing-changes-for-github-actions/
  - The Free plan includes **2,000 min/mo for private repos** [UNVERIFIED for 2026; from memory].
  - `schedule:` cron runs in UTC with a 5-minute minimum interval.
  - Runs are **often delayed by 5–30+ minutes, and sometimes dropped, at busy times (the top of the hour)**, so schedule at an odd minute.
  - **Scheduled workflows in public repos are auto-disabled after 60 days without repository activity.** A daily state commit avoids this.
  - Secrets are encrypted and masked in logs. They are *not* passed to fork pull requests. [UNVERIFIED: these are 2025 doc facts from memory]
  - **For a public study, workflow logs are public**, so never print keys, account IDs, or wallet private keys.
- **Persistence options:**
  - **Commit `state/*.json` or a SQLite file back to the repo**, using `permissions: contents: write`. This is free, auditable, and fits a public study.
  - Artifacts (90-day default retention).
  - `actions/cache` (evicted after 7 days unused; not a database).
  - Supabase free (pauses after inactivity), Cloudflare R2 free tier, or Turso. [UNVERIFIED]
- **Modal.** The Starter plan is free with **$30/mo of compute credit**. Deployed crons are limited (about 5) [UNVERIFIED]. It has Volumes and Dicts for state. This is overkill unless you need more than 6-hour jobs or GPUs. https://modal.com/pricing
- **VPS.** Hetzner raised prices in April and June 2026. **CX23 is about €5.49/mo plus €0.50 for IPv4**, and cost-optimized plans are often "currently not available". [3P] https://findstack.com/resources/hetzner-price-increase-2026
  - A VPS is only worth it for websocket or always-on strategies, and €6/mo is 6% of the bankroll.
  - Oracle Cloud Always Free is a $0 alternative [UNVERIFIED].

---

## 8. Recommended stack for a $100 bankroll

| Need | Pick | Est. monthly cost |
|---|---|---|
| US stocks/ETFs execution | **Alpaca live cash-style account** (<$2k, so 1x). Use notional orders ≥$1 and limit orders. Paper-trade first with the paper balance reset to $100. | $0 plus regulatory pennies |
| Crypto spot (US-compliant) | **Alpaca crypto** (0.15% maker / 0.25% taker). Use limit orders to get maker fees. | Fee drag of about $0.50–2 at moderate turnover |
| Perps and stock perps | **Hyperliquid + trade.xyz via `hyperliquid-python-sdk` (agent wallet), only if the operator is not a US/restricted person.** Otherwise skip perps. Positions ≥$20, leverage ≤2–3x, post-only (ALO) orders for 0.015% maker fees. Develop on testnet. | Under $0.50 in fees, plus a $1 withdrawal fee |
| Prices and candles | Alpaca Data Basic (IEX live, SIP bars older than 15 min, crypto). Hyperliquid info API. yfinance as a cached fallback. GeckoTerminal and DexScreener for memecoins. | $0 |
| Fundamentals and earnings | SEC EDGAR (companyfacts), Finnhub free (calendar, surprises, recommendation trends), yfinance (price targets, earnings dates) | $0 |
| News | Alpaca News (Benzinga), Finnhub company news, Google News RSS, GDELT tone | $0 |
| X sentiment | **One batched `grok-4.1-fast` + `x_search` call/day for the final 1–3 candidates, date-filtered.** StockTwits stream as a free supplement. | **$1–4** |
| LLM agents | **Haiku 5.5 at low effort for analyst agents; Sonnet 5.5 for the final decision**; prompt caching plus the Batch API | **$1–2** |
| Agent web search | Tavily free (1,000 credits), falling back to Claude web search at $0.01/search with `max_uses=3` | $0–0.50 |
| Scheduling and state | **GitHub Actions cron (public repo)** with state committed as JSON/SQLite | $0 |
| **Total** | | **About $2–7/month** |

**Rules that matter at this scale:**
- **$5/month of API spend is 5% of equity per month, about 60% a year.** That cost is the main hurdle the strategy has to clear.
- Keep a **hard spend ledger.** Deduct every API cost from the reported P&L, cap LLM and X spend at about $3/month, and fall back to free-only signals when the cap is hit.
- **Trade rarely.** Fees (0.25% taker on Alpaca crypto) plus spread on a $100 book punish daily rebalancing. Prefer daily signals with maker or limit orders and minimum-trade thresholds of about $5–10.
- **Mind the minimums.** Alpaca's minimum is $1; Hyperliquid's is $10 notional, and its address rate budget is 1 request per $1 traded.
- **Avoid single points of failure.** yfinance (IP blocks), the StockTwits public endpoint (Cloudflare) and Reddit (approval) are fragile. Always have the official free API (Alpaca, Finnhub, SEC) as the primary source and the scraper as a fallback.
- **Re-verify before go-live**, preferably with a live test call to each source:
  - Exact xAI `x_search` billing.
  - Alpaca News free entitlement.
  - Finnhub endpoint entitlements.
  - Hyperliquid mainnet leverage and fees (`meta`, `userFees`).
  - Alpaca fractional TIF rules.
  - GitHub private-repo minutes.
