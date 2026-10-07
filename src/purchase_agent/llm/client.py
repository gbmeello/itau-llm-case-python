"""Abstração mínima sobre o provedor de LLM.

Permite trocar Anthropic por Fake (testes/evals/demo sem chave) e decorar com resiliência e métricas
sem tocar no agente.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


@dataclass(frozen=True)
class LlmRequest:
    purpose: str  # analyst | repair | compliance | case-summarizer (métricas e fake)
    model: str
    system_prompt: str  # parte estável (cacheável)
    user_content: str  # parte variável: dados da solicitação e contexto
    output_schema: dict[str, Any] | None  # JSON Schema imposto via structured outputs
    max_tokens: int
    effort: str | None = None


@dataclass(frozen=True)
class LlmResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: int
    stop_reason: str


class LlmErrorKind(StrEnum):
    RATE_LIMITED = "RATE_LIMITED"
    OVERLOADED = "OVERLOADED"
    SERVER_ERROR = "SERVER_ERROR"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    REFUSAL = "REFUSAL"
    BAD_REQUEST = "BAD_REQUEST"
    AUTH = "AUTH"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    UNKNOWN = "UNKNOWN"


_RETRYABLE = {LlmErrorKind.RATE_LIMITED, LlmErrorKind.OVERLOADED, LlmErrorKind.SERVER_ERROR,
              LlmErrorKind.TIMEOUT, LlmErrorKind.NETWORK}


class LlmError(RuntimeError):
    def __init__(self, kind: LlmErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind in _RETRYABLE


class LlmClient(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse: ...
