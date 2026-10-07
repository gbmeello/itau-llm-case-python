"""Decorator de resiliência: retry com backoff exponencial + jitter só para erros transitórios, e circuit
breaker para não martelar o provedor fora do ar (falha rápido → fallback do agente). Também emite métricas.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from typing import Callable

from purchase_agent.llm.client import LlmClient, LlmError, LlmErrorKind, LlmRequest, LlmResponse
from purchase_agent.llm.pricing import cost_usd
from purchase_agent.observability.metrics import Metrics


class CircuitBreaker:
    """Janela deslizante de N chamadas; abre com taxa de falha ≥ limiar e fica aberto por `open_seconds`."""

    def __init__(self, window: int = 20, min_calls: int = 10, failure_rate: float = 0.5, open_seconds: float = 30,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._results: deque[bool] = deque(maxlen=window)
        self._min_calls = min_calls
        self._failure_rate = failure_rate
        self._open_seconds = open_seconds
        self._opened_at: float | None = None
        self._clock = clock
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at >= self._open_seconds:  # half-open: deixa uma tentativa passar
                self._opened_at = None
                self._results.clear()
                return True
            return False

    def record(self, success: bool) -> None:
        with self._lock:
            self._results.append(success)
            failures = self._results.count(False)
            if len(self._results) >= self._min_calls and failures / len(self._results) >= self._failure_rate:
                self._opened_at = self._clock()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None


class ResilientLlmClient:
    def __init__(self, delegate: LlmClient, metrics: Metrics, max_attempts: int = 3,
                 initial_backoff: float = 0.5, breaker: CircuitBreaker | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._delegate = delegate
        self._metrics = metrics
        self._max_attempts = max_attempts
        self._initial_backoff = initial_backoff
        self.breaker = breaker or CircuitBreaker()
        self._sleep = sleep

    def complete(self, request: LlmRequest) -> LlmResponse:
        attempt = 0
        while True:
            attempt += 1
            if not self.breaker.allow():
                self._metrics.llm_errors.labels(request.purpose, LlmErrorKind.CIRCUIT_OPEN).inc()
                raise LlmError(LlmErrorKind.CIRCUIT_OPEN, "Circuit breaker aberto para o LLM")
            start = time.perf_counter()
            try:
                response = self._delegate.complete(request)
            except LlmError as e:
                self._metrics.llm_errors.labels(request.purpose, e.kind).inc()
                self._metrics.llm_latency.labels(request.purpose, "n/a", "error").observe(time.perf_counter() - start)
                if e.retryable:
                    self.breaker.record(False)
                if not e.retryable or attempt >= self._max_attempts:
                    raise
                self._metrics.llm_retries.labels(e.kind).inc()
                backoff = self._initial_backoff * (2 ** (attempt - 1))
                self._sleep(backoff * random.uniform(0.5, 1.5))  # jitter evita thundering herd
                continue
            self.breaker.record(True)
            self._record_success(request, response)
            return response

    def _record_success(self, request: LlmRequest, r: LlmResponse) -> None:
        m = self._metrics
        m.llm_latency.labels(request.purpose, r.model, "success").observe(r.latency_ms / 1000)
        m.llm_tokens.labels(request.purpose, r.model, "input").inc(r.input_tokens)
        m.llm_tokens.labels(request.purpose, r.model, "output").inc(r.output_tokens)
        m.llm_tokens.labels(request.purpose, r.model, "cache_read").inc(r.cache_read_tokens)
        m.llm_cost.labels(request.purpose, r.model).inc(cost_usd(r))
