"""Métricas Prometheus. Um registry por aplicação (evita colisão entre instâncias em testes).

Chamadas LLM (`llm_*`): latência, tokens, custo, erros e retries por papel e modelo.
Comportamento do agente (`agent_*`, `context_*`): decisões, fallbacks, guardrails, grounding e budget de contexto.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60)


class Metrics:
    def __init__(self) -> None:
        r = self.registry = CollectorRegistry()
        self.llm_latency = Histogram("llm_request_latency_seconds", "Latência por chamada LLM",
                                     ["purpose", "model", "outcome"], registry=r, buckets=_LATENCY_BUCKETS)
        self.llm_tokens = Counter("llm_tokens", "Tokens consumidos", ["purpose", "model", "direction"], registry=r)
        self.llm_cost = Counter("llm_cost_usd", "Custo estimado (USD)", ["purpose", "model"], registry=r)
        self.llm_errors = Counter("llm_errors", "Erros de chamada LLM", ["purpose", "type"], registry=r)
        self.llm_retries = Counter("llm_retries", "Retries de chamada LLM", ["type"], registry=r)
        self.decisions = Counter("agent_decisions", "Decisões", ["decision", "decided_by", "risk_level"], registry=r)
        self.decision_latency = Histogram("agent_decision_latency_seconds", "Latência ponta a ponta",
                                          ["decided_by"], registry=r, buckets=_LATENCY_BUCKETS)
        self.decision_cost = Histogram("agent_decision_cost_usd", "Custo por decisão", registry=r,
                                       buckets=(0, 0.001, 0.0025, 0.005, 0.01, 0.02, 0.05, 0.1))
        self.fallbacks = Counter("agent_fallbacks", "Decisões sem LLM utilizável", ["reason"], registry=r)
        self.guardrail_events = Counter("agent_guardrail_events", "Eventos de guardrail", ["event"], registry=r)
        self.validation_failures = Counter("agent_validation_failures", "Saídas inválidas", ["stage"], registry=r)
        self.grounding_errors = Counter("agent_grounding_errors", "Erros de grounding/schema", ["stage"], registry=r)
        self.compliance_disagreements = Counter("agent_compliance_disagreements", "Compliance discordou", registry=r)
        self.context_tokens = Histogram("context_tokens_by_layer", "Tokens estimados por camada", ["layer"],
                                        registry=r, buckets=(50, 100, 200, 400, 800, 1200, 2000, 4000, 8000))
        self.context_truncations = Counter("context_truncations", "Itens de contexto cortados", registry=r)

    def render(self) -> bytes:
        return generate_latest(self.registry)
