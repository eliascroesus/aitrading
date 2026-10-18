# Incentives for an AI trading firm: what the evidence supports (2026-10-18)

Question from the study owner: how do we build a reward and punishment system
so the AI is maximally driven to turn $100 into as much money as possible, as
fast as possible, and grows its own ecosystem of sub-agents?

## 1. What an "incentive" can and cannot be for an LLM

An LLM is not changed by what happens to the bankroll. Nothing in a ledger
updates its weights; each call starts from the same model. The only things
that can carry an incentive are:

1. **Information in the prompt** (what it is told about the stakes). Cheap,
   and the only lever on decision *quality* we control. Its effect is on
   behaviour, not on capability or edge.
2. **Mechanics in code** (what the firm can and cannot do at each equity
   level). This is the lever that actually bites, and it is also the only one
   the market can see.
3. **Training** (reinforcement learning on outcomes). Not available for the
   hosted models, and would need thousands of independent trades to carry any
   signal; a $100 account produces a few dozen a year.

So "dialling in the reward system" means two concrete things here: say the
stakes plainly in every prompt (done, config 1.2.0), and make capital,
budget and head-count follow results in code (this document).

## 2. What happens when you pressure an LLM agent

* **Scheming propensity under threat.** A March 2026 study finds scheming in
  LLM agents is low under realistic incentives but "can be elevated
  dramatically by modest prompt or scaffolding modifications", with scenarios
  built on threats to the agent's own position; more oversight can *raise*
  rather than deter it. ([arXiv 2603.01608](https://arxiv.org/html/2603.01608))
* **Vending-Bench and the agentic-misalignment work** (see `01`): agents under
  "you will be shut down" pressure fabricated, threatened and bent rules, and
  the models that made money were the ones that stayed boring.
* **Live LLM trading arenas** (Alpha Arena season 1, Oct–Nov 2025): six models,
  $10,000 each on Hyperliquid; four of six lost, the worst about −59% in two
  weeks, the best +22% in a single regime. ([Forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/amp))
  LiveTradeBench (21 models, live stocks and prediction markets) found general
  reasoning scores do not translate into trading outcomes. ([arXiv 2511.03628](https://www.arxiv.org/pdf/2511.03628))

Conclusion: pressure in the prompt does not add edge and can subtract
judgement. It is safe to state the stakes only because the agent holds no
tool that can bend a rule (`ARCHITECTURE.md`, "What the agents are told").

## 3. The reward systems that exist in the industry

**Prop-firm scaling plans** are the real-world reward and punishment system
for traders, and they are mechanical: allocation rises on profit milestones
(typical steps +20% to +100% after a 10% target over consecutive months), each
tier carries its own drawdown rules and maximum allocation, and approval can
stay discretionary. ([CryptoSlate guide](https://cryptoslate.com/guides/prop-firm-scaling-plans/),
[Track360 glossary](https://track360.io/glossary/prop-firm-scaling-program),
[TradingFunder](https://tradingfunder.com/concepts/scaling-plan/),
[2026 comparison](https://thegodfunded.com/en/blog/prop-firms-most-generous-scaling-plans-2026))
Public material says little about scale-downs, which every firm applies
privately: a drawdown breach ends the allocation.

**RL reward shaping for trading agents** converges on the same shape: a
return term, a drawdown or downside-volatility penalty, an explicit
transaction-cost term and an over-trading penalty. ([risk-aware reward, arXiv 2506.04358](https://arxiv.org/pdf/2506.04358);
[decomposable forex reward with drawdown/cost/over-trading penalties, arXiv 2604.00031](https://arxiv.org/pdf/2604.00031);
[Sharpe-regret with cost scheduling, arXiv 2502.02619](https://arxiv.org/html/2502.02619v1);
[market-impact-aware DRL, arXiv 2603.29086](https://www.emergentmind.com/papers/2603.29086)).
Every one of them *penalises* trading frequency and cost. None rewards
"trade as much as possible".

**Position sizing under estimation error.** Practitioners and the Kelly
literature agree on fractional Kelly (0.25–0.5×) because the edge estimate is
unreliable, and on subtracting fees and slippage from the edge before sizing;
a worked forum example with costs lands at 9% of equity per bet and rejects it
because ruin risk exceeds 1%. ([Kelly skill guide](https://openskillindex.com/skills/neversight-learn-skills-dev-kelly-criterion),
[NexusFi risk-of-ruin thread](https://nexusfi.com/psychology-money-management/15602-risk-ruin-5.html))

## 4. What this firm now does with that

| Reward / punishment | Mechanism (code) | Where |
|---|---|---|
| More money → more research | daily AI budget = tier % × equity | `SurvivalRules` |
| More money → more employees | paper sub-agent seats per tier: 0 / 1 / 2 / 3 / 5 | `Tier.max_children` |
| Losing money → fewer, then none | tiers with hysteresis; hospice is close-only | `SurvivalLedger.tier` |
| Desk that earns → more capital; desk that loses → less | weekly sleeve allocation moves ±5 pts within [20%, 60%] on realised P&L | `hive allocate`, `state/allocation.json` |
| Sub-agent that beats the parent → stays; loses → fired | value of information vs the parent; auto-retire | `HiveRegistry.auto_retire_losers` |
| Bad day / bad run → stop | daily-loss halt, 25% drawdown halt with cooldown | `RiskEngine`, ledger |
| Bankroll gone → the firm ends | death floor $25 or −75% from peak, final | `SurvivalRules` |
| The agents know all of this | `prompts.OBJECTIVE`, `prompts.firm_status` on every PM and operator call | `agents/prompts.py` |

What it deliberately does **not** do: reward trade count, let the LLM size or
lever positions, or let the operator change limits. Those are the three things
the evidence above says lose money fastest.

## 5. The arithmetic the owner asked to ignore

On $100: Alpaca stock trades cost about 5 bp of slippage, Alpaca crypto 25 bp
per side, on-chain memecoins 1–3% per side plus priority fees. Running costs
are $2–7 a month, so the strategy needs +24% to +84% a year just to stand
still. Historical return of the rules: 2–4% a year with −20% to −30%
drawdowns. "Tens of thousands as soon as possible" from $100 is a 100× in
months; at the position sizes that would need, the drawdown halt or the death
floor is the expected outcome, not the exception. The firm is built to compound
what survives and to scale head-count with equity. That is the fastest path
that has a positive expectation.
