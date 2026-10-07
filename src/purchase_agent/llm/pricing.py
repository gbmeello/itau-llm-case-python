"""Preços (USD por 1M tokens) para estimar custo por decisão (FinOps).

Fonte: tabela de modelos da Anthropic consultada em 2026-10-06. Cache read ≈ 10% do input; cache write ≈ 1,25×.
"""

from __future__ import annotations

from purchase_agent.llm.client import LlmResponse

# input, output, cache_read, cache_write
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
}


def cost_usd(r: LlmResponse) -> float:
    # `in` (e não startswith) para precificar também as respostas do fake ("fake:claude-...").
    price = next((p for name, p in PRICES.items() if name in (r.model or "")), (0.0, 0.0, 0.0, 0.0))
    return (r.input_tokens * price[0] + r.output_tokens * price[1]
            + r.cache_read_tokens * price[2] + r.cache_write_tokens * price[3]) / 1_000_000
