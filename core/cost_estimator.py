"""Deterministic project cost estimator — the client-facing quote.

No LLM, no network. Estimates token usage from the brief size, the planned team size,
and an assumed number of turns, priced with real PROVIDER_CATALOG per-million-token
rates. Stored on WorkspaceProject and shown to the client at project initiation.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Dict, Optional, Tuple

from core.llm_providers import PROVIDER_CATALOG

_FALLBACK_RATES: Tuple[float, float] = (3.0, 15.0)   # Anthropic Sonnet $/M tokens


def model_rates(provider: str, model: str) -> Tuple[float, float]:
    """Return (input_rate, output_rate) in USD per million tokens."""
    cat = PROVIDER_CATALOG.get(provider)
    if cat:
        for m in cat.get("models", []):
            if m["id"] == model:
                return float(m["in"]), float(m["out"])
    return _FALLBACK_RATES


@dataclass
class CostEstimate:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    team_size: int
    provider: str
    model: str
    assumptions: Dict

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(blob: str) -> "CostEstimate":
        return CostEstimate(**json.loads(blob))


def estimate_project_cost(brief: str, team_size: int, *,
                          provider: str = "anthropic",
                          model: str = "claude-sonnet-4-6",
                          assumed_turns: Optional[int] = None) -> CostEstimate:
    """Deterministically estimate the cost of delivering `brief` with `team_size` agents."""
    if assumed_turns is None:
        assumed_turns = int(os.getenv("COST_ASSUMED_TURNS", "8"))
    base_ctx = int(os.getenv("COST_BASE_CONTEXT_TOKENS", "1500"))
    out_per_turn = int(os.getenv("COST_OUTPUT_TOKENS_PER_TURN", "500"))

    brief_tokens = len(brief or "") // 4
    total_turns = max(0, team_size) * assumed_turns
    input_tokens = (base_ctx + brief_tokens) * total_turns
    output_tokens = out_per_turn * total_turns

    in_rate, out_rate = model_rates(provider, model)
    cost_usd = round(input_tokens / 1e6 * in_rate + output_tokens / 1e6 * out_rate, 4)

    return CostEstimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        team_size=team_size,
        provider=provider,
        model=model,
        assumptions={
            "assumed_turns": assumed_turns,
            "base_context_tokens": base_ctx,
            "output_tokens_per_turn": out_per_turn,
            "brief_tokens": brief_tokens,
        },
    )
