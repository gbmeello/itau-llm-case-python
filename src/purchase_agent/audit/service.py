"""Auditoria de decisões e idempotência (mesmo conteúdo não paga outra chamada ao LLM)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from purchase_agent.agent.orchestrator import AgentOutcome
from purchase_agent.contract.models import PurchaseDecision
from purchase_agent.db import DecisionRecord


def canonical_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditService:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._sessions = session_factory

    def record(self, outcome: AgentOutcome, input_hash: str) -> None:
        d = outcome.decision
        ctx = outcome.context
        with self._sessions.begin() as s:
            s.add(DecisionRecord(
                decision_id=d.audit.decisionId, request_id=d.requestId, case_id=d.caseId, input_hash=input_hash,
                trace_id=d.audit.traceId, decision=d.decision.value, risk_level=d.riskLevel.value,
                decided_by=d.audit.decidedBy.value, skill_versions=json.dumps(d.audit.skillVersions)[:400],
                context_included=",".join(i.id for i in ctx.included)[:2000],
                context_excluded=",".join(ctx.excluded_ids)[:1000], context_tokens=ctx.estimated_tokens,
                tokens_in=d.audit.tokens.input, tokens_out=d.audit.tokens.output, cost_usd=d.audit.estimatedCostUsd,
                latency_ms=d.audit.latencyMs,
                normalized_request_json=json.dumps(dataclasses.asdict(outcome.request), default=str,
                                                   ensure_ascii=False),
                decision_json=d.model_dump_json()))

    def previous_decision(self, request_id: str, input_hash: str) -> PurchaseDecision | None:
        with self._sessions() as s:
            row = s.scalar(select(DecisionRecord)
                           .where(DecisionRecord.request_id == request_id, DecisionRecord.input_hash == input_hash,
                                  DecisionRecord.case_id.is_(None))
                           .order_by(DecisionRecord.created_at.desc()).limit(1))
            return PurchaseDecision.model_validate_json(row.decision_json) if row else None

    def find(self, decision_id: str) -> DecisionRecord | None:
        with self._sessions() as s:
            return s.get(DecisionRecord, decision_id)

    def by_request(self, request_id: str) -> list[DecisionRecord]:
        with self._sessions() as s:
            return list(s.scalars(select(DecisionRecord).where(DecisionRecord.request_id == request_id)
                                  .order_by(DecisionRecord.created_at.desc())))
