"""LLM agent layer. The agents research, debate and veto; code trades.

Contract (see docs/ARCHITECTURE.md):

* Inputs are numbers precomputed by the signal layer, plus *bounded* fields
  from the quarantined news reader. The decision model never sees raw text.
* Outputs are pydantic-validated. An invalid, refused or missing reply is a
  veto (fail closed), never an approve.
* Every call's tokens and dollar cost are returned and booked to the ledger.
* The objective in every prompt is neutral: risk-adjusted return net of
  costs, and shutdown is an acceptable outcome.
"""
