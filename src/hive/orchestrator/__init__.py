"""The daily run. One cycle = one trading day, evaluated after the close.

Order of operations (never reordered):
    kill switch -> survival status (death check, AI budget) -> data refresh ->
    regime -> features -> manage open positions (exits, stop ratchet) ->
    ranked candidates -> LLM filter (optional, budgeted) -> risk engine ->
    orders (+ protective stops) -> ledger (equity mark, AI costs) -> journal -> report.

The same cycle runs a rules-only *shadow* paper portfolio next to the primary
one, so the study can answer whether the LLM layer adds value net of its cost.
"""
