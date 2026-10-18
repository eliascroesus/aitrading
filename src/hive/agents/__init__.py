"""LLM agent layer. The agents research, debate and veto; code trades.

Contract (see docs/ARCHITECTURE.md):

* Inputs are numbers precomputed by the signal layer, plus *bounded* fields
  from the quarantined news reader. The decision model never sees raw text.
* Outputs are pydantic-validated. An invalid, refused or missing reply is a
  veto (fail closed), never an approve.
* Every call's tokens and dollar cost are returned and booked to the ledger.
* Every prompt states the firm's objective plainly (grow the one bankroll net
  of every cost, including the agent's own; growth buys budget and sub-agents,
  the death floor is permanent) and that the limits in code are not negotiable.
  The PM and the operator also receive the ledger's live numbers
  (``prompts.firm_status``).
"""
