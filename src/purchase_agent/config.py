"""Configuração via variáveis de ambiente (12-factor). Defaults permitem rodar tudo sem chave de API."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class ContextBudget:
    """Token budget por camada (ver docs/context-and-tokens.md)."""

    total_input: int = 8000
    system_reserve: int = 1500
    request: int = 1000
    policy: int = 600
    facts: int = 500
    policy_text: int = 1200
    history: int = 800
    case_state: int = 300
    examples: int = 600


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "fake"  # fake | anthropic
    api_key: str = "dev-key-change-me"
    analyst_model: str = "claude-sonnet-5-5"
    reviewer_model: str = "claude-haiku-4-5"
    analyst_effort: str | None = "medium"
    analyst_max_tokens: int = 4000
    min_approve_confidence: float = 0.7
    max_repair_attempts: int = 1
    max_case_rounds: int = 3
    llm_timeout_seconds: float = 60.0
    llm_max_attempts: int = 3
    llm_initial_backoff_seconds: float = 0.5
    # "Hoje" das regras de negócio (dados sintéticos do ERP são de 2026). Auditoria usa o relógio real.
    business_date: date | None = date(2026, 10, 6)
    database_url: str = "sqlite+pysqlite:///:memory:"
    erp_source: str = "mock"  # mock | mcp
    mcp_erp_url: str = "http://127.0.0.1:8001/mcp"
    context: ContextBudget = field(default_factory=ContextBudget)

    @staticmethod
    def from_env() -> Settings:
        fixed = _env("AGENT_FIXED_DATE", "2026-10-06")
        return Settings(
            llm_provider=_env("LLM_PROVIDER", "fake").lower(),
            api_key=_env("AGENT_API_KEY", "dev-key-change-me"),
            analyst_model=_env("AGENT_ANALYST_MODEL", "claude-sonnet-5-5"),
            reviewer_model=_env("AGENT_REVIEWER_MODEL", "claude-haiku-4-5"),
            business_date=None if fixed.lower() == "none" else date.fromisoformat(fixed),
            database_url=_env("DATABASE_URL", "sqlite+pysqlite:///:memory:"),
            erp_source=_env("ERP_SOURCE", "mock").lower(),
            mcp_erp_url=_env("MCP_ERP_URL", "http://127.0.0.1:8001/mcp"),
            llm_initial_backoff_seconds=float(_env("LLM_INITIAL_BACKOFF_SECONDS", "0.5")),
        )

    def today(self) -> date:
        return self.business_date or date.today()
