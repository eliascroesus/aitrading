"""Hard risk limits, enforced in code, outside the LLM.

The LLM layer produces *intents* (approve / reduce / veto) on candidates the
deterministic signal layer generated. Nothing the LLM says can increase size,
leverage, or bypass a gate below. The same ``RiskEngine`` runs in paper and
live, and its verdicts are written to the audit journal.
"""
