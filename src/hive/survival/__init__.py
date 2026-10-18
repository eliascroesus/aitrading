"""The survival economy: the bankroll is the AI's life.

* Every AI/data/infra cost is appended to a hash-chained ledger and deducted
  from reported equity, so the public equity curve is *net of the AI's own
  running costs*.
* Death is decided by code at a floor above zero, where fees and minimum
  order sizes make trading impossible anyway. There is no gambling tier.
* Growth unlocks tiers (with hysteresis) that may spawn sub-agents; children
  start in shadow mode and must earn a budget.

Design note: the agents are told the stakes (docs/ARCHITECTURE.md, "What the
agents are told"). Survival pressure is documented to produce rule-bending in
LLMs that hold tools able to bend rules (docs/RESEARCH.md); here every limit is
enforced in code and the LLM can only veto, halve, propose within bounds and
recommend, so the framing changes decisions, not what is possible.
"""
