"""Live Solana memecoin execution — deliberately NOT implemented.

Turning the paper scout into a live trader needs, in this order, and each one
is a human decision:

1. A dedicated Solana hot wallet funded with only the sleeve's allocation
   (≤ 3% of equity) plus SOL for fees; its key held by the operator, never by
   an LLM tool.
2. An RPC endpoint (Helius/Triton/QuickNode free tiers exist) and the Jupiter
   swap API (quote -> swap transaction -> sign -> send), with the slippage
   bound from ``MemeRules.slippage_pct`` and a priority-fee cap.
3. Token-safety checks *before* every buy (RugCheck summary: no mint/freeze
   authority, LP locked, holder concentration) and a simulated sell to rule
   out honeypots.
4. Exchange-side protection does not exist on a DEX: exits are software
   stops, so the process must run more often than daily (hourly at least) or
   accept that the −40% hard stop will slip badly.

Until all four exist, ``JupiterBroker`` refuses to construct. This is a
guard, not a placeholder to fill in silently.
"""

from __future__ import annotations


class JupiterBroker:
    name = "jupiter-live"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Live memecoin execution is intentionally not wired. See hive/execution/jupiter.py "
            "for the four prerequisites; the scout runs paper-only until a human completes them."
        )
