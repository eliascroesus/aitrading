"""Deterministic signal layer. No LLM calls live here, by design.

Everything in this package is a pure function of a price/volume DataFrame and
the pre-registered ``StrategyConfig``. That makes every candidate the LLM
agents later see reproducible from cached data alone.
"""
