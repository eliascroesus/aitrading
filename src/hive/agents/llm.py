"""Thin, cost-accounted wrapper over the Anthropic SDK with fail-closed parsing."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# USD per million tokens: (input, output, cache_read, cache_write). Source: docs/research/03.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


def estimate_cost_usd(model: str, usage: Any) -> float:
    p = PRICES.get(model, PRICES["claude-opus-5-5"])
    inp = float(getattr(usage, "input_tokens", 0) or 0)
    out = float(getattr(usage, "output_tokens", 0) or 0)
    cr = float(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cw = float(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return (inp * p[0] + out * p[1] + cr * p[2] + cw * p[3]) / 1_000_000.0


class BudgetExceededError(RuntimeError):
    pass


def estimate_call_cost_usd(model: str, input_chars: int, max_tokens: int, output_fraction: float = 0.25) -> float:
    """Rough pre-call estimate: ~4 chars per input token; assume a quarter of max_tokens out."""
    p = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (input_chars / 4.0 * p[0] + max_tokens * output_fraction * p[1]) / 1_000_000.0


@dataclass
class LLMCall:
    role: str
    model: str
    ok: bool
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    stop_reason: str
    error: str = ""


class LLMClient:
    """``parse()`` returns ``(parsed_or_None, LLMCall)``. ``None`` means fail closed."""

    def __init__(
        self,
        *,
        api_key: str | None,
        budget_usd: float,
        on_call: Callable[[LLMCall], None] | None = None,
        client: Any | None = None,
    ):
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.on_call = on_call or (lambda c: None)
        self.failures = 0
        self._client = client
        if self._client is None and api_key:
            import anthropic

            self._client = anthropic.Anthropic(api_key=api_key)

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def remaining_usd(self) -> float:
        return max(self.budget_usd - self.spent_usd, 0.0)

    def parse(
        self,
        *,
        role: str,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        effort: str = "low",
        max_tokens: int = 2048,
        reserve_usd: float = 0.0005,
    ) -> tuple[T | None, LLMCall]:
        if self._client is None:
            call = LLMCall(role, model, False, 0.0, 0, 0, 0, "no_client", "LLM disabled")
            self.on_call(call)
            return None, call
        # Gate on the *estimated* cost of this call, not a fixed reserve, so one
        # large portfolio-manager call cannot overshoot a small daily budget.
        est = max(estimate_call_cost_usd(model, len(system) + len(user), max_tokens), reserve_usd)
        if self.remaining_usd < est:
            raise BudgetExceededError(
                f"daily AI budget exhausted (spent ${self.spent_usd:.4f}, next call ~${est:.4f})"
            )
        try:
            resp = self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                # Stable system prompt first so the prefix caches across candidates.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": effort},
            )
        except Exception as exc:  # network, 4xx/5xx, schema errors -> fail closed
            self.failures += 1
            call = LLMCall(role, model, False, 0.0, 0, 0, 0, "exception", str(exc)[:300])
            self.on_call(call)
            log.error("LLM %s failed: %s", role, exc)
            return None, call
        usage = getattr(resp, "usage", None)
        cost = estimate_cost_usd(model, usage) if usage is not None else 0.0
        self.spent_usd += cost
        stop = str(getattr(resp, "stop_reason", ""))
        parsed = getattr(resp, "parsed_output", None)
        ok = stop in ("end_turn", "stop_sequence") and parsed is not None
        if not ok:
            self.failures += 1
        call = LLMCall(
            role, model, ok, cost,
            int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0),
            int(getattr(usage, "cache_read_input_tokens", 0) or 0), stop,
            "" if ok else f"stop_reason={stop}",
        )
        self.on_call(call)
        return (parsed if ok else None), call
