"""HIVE — a survival-constrained, multi-agent AI trading system.

Layers (strict dependency order, lower layers never import higher ones):

    data      -> point-in-time market/fundamental/news access with on-disk cache
    signals   -> deterministic indicators and candidate generation (no LLM)
    risk      -> hard limits enforced in code; the LLM can only *reduce* risk
    agents    -> LLM analysts that research, debate and veto candidates
    execution -> broker adapters (paper simulator, Alpaca, Hyperliquid)
    survival  -> the bankroll ledger: AI costs are paid from equity; death/tier rules
    orchestrator -> the daily run that wires everything together
"""

__version__ = "0.1.0"
