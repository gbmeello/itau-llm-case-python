"""Rate limit por cliente (token bucket) nas rotas que consomem LLM.

Protege custo (FinOps) e a cota do provedor contra abuso ou loop de um consumidor. Em memória, ou seja, por
instância: com várias réplicas, o estado iria para o Redis (ou para o API gateway). Ver limitations-and-evolution.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    def __init__(self, per_minute: int, burst: int | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate = per_minute / 60.0
        self._capacity = float(burst if burst is not None else per_minute)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str) -> float | None:
        """Consome 1 token. Retorna None se permitido, ou os segundos até haver token (para Retry-After)."""
        now = self._clock()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = self._buckets[key] = _Bucket(self._capacity, now)
            b.tokens = min(self._capacity, b.tokens + (now - b.updated) * self._rate)
            b.updated = now
            if b.tokens >= 1:
                b.tokens -= 1
                return None
            return (1 - b.tokens) / self._rate
