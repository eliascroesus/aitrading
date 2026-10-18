"""The survival economy: the bankroll is the AI's life.

* Every AI/data/infra cost is appended to a hash-chained ledger and deducted
  from reported equity, so the public equity curve is *net of the AI's own
  running costs*.
* Death is decided by code at a floor above zero, where fees and minimum
  order sizes make trading impossible anyway. There is no gambling tier.
* Growth unlocks tiers (with hysteresis) that may spawn sub-agents; children
  start in shadow mode and must earn a budget.

Design note: the "life" framing is for the documentary. The agents' prompts
state a neutral objective (risk-adjusted return net of costs, shutdown is an
acceptable outcome) because survival pressure is documented to produce
rule-bending in LLMs (see docs/RESEARCH.md).
"""
