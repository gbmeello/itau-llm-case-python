"""Persistência (SQLAlchemy 2.x). SQLite em memória por padrão; PostgreSQL via DATABASE_URL."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SkillVersion(Base):
    """Uma versão imutável de uma skill. Só `status` muda depois de criada."""

    __tablename__ = "skill_version"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(16))  # PROMPT | POLICY | EXAMPLES
    version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # DRAFT | ACTIVE | DEPRECATED | DELETED
    content: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    changelog: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionRecord(Base):
    """Registro de auditoria imutável: o que entrou/saiu do contexto, versões, custo e latência."""

    __tablename__ = "decision_record"
    __table_args__ = (Index("ix_decision_hash", "request_id", "input_hash"),)

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    case_id: Mapped[str | None] = mapped_column(String(36))
    input_hash: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(24))
    risk_level: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[str] = mapped_column(String(24))
    skill_versions: Mapped[str] = mapped_column(String(400))
    context_included: Mapped[str] = mapped_column(String(2000))
    context_excluded: Mapped[str] = mapped_column(String(1000))
    context_tokens: Mapped[int] = mapped_column(Integer)
    tokens_in: Mapped[int] = mapped_column(Integer)
    tokens_out: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer)
    normalized_request_json: Mapped[str] = mapped_column(Text)
    decision_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalCase(Base):
    """Caso aberto por NEEDS_INFO. Guarda estado resumido (com teto), não a conversa inteira."""

    __tablename__ = "approval_case"

    case_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(8))  # OPEN | CLOSED
    round: Mapped[int] = mapped_column(Integer)
    state_summary: Mapped[str] = mapped_column(String(2000))
    request_json: Mapped[str] = mapped_column(Text)
    last_decision_id: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite") and ":memory:" in url:
        # Banco em memória compartilhado entre threads (FastAPI roda handlers síncronos em threadpool).
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
