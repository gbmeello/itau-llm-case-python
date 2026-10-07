"""Caso de uso da API: valida contrato, aplica idempotência, chama o agente, audita e gerencia casos multi-turno."""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import sessionmaker

from purchase_agent.agent import renderer
from purchase_agent.agent.orchestrator import AgentOutcome, PurchaseApprovalAgent
from purchase_agent.audit.service import AuditService, canonical_hash
from purchase_agent.config import Settings
from purchase_agent.context.builder import neutralize
from purchase_agent.contract import schemas
from purchase_agent.contract.models import Decision, ErpPayload, PurchaseDecision, PurchaseRequest
from purchase_agent.db import ApprovalCase, utcnow
from purchase_agent.intake import pii
from purchase_agent.llm.client import LlmClient, LlmError, LlmRequest
from purchase_agent.registry import skills as sk
from purchase_agent.registry.skills import SkillRegistry

MAX_JUSTIFICATION = 4000


class ContractViolation(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("Entrada viola o contrato purchase-request.v1")
        self.errors = errors


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class Result:
    decision: PurchaseDecision
    replayed: bool


def _merge(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for k, v in updates.items():
        if isinstance(target.get(k), dict) and isinstance(v, dict):
            _merge(target[k], v)
        else:
            target[k] = v


class EvaluationService:
    def __init__(self, settings: Settings, agent: PurchaseApprovalAgent, audit: AuditService,
                 sessions: sessionmaker, registry: SkillRegistry, llm: LlmClient) -> None:
        self._s = settings
        self._agent = agent
        self._audit = audit
        self._sessions = sessions
        self._registry = registry
        self._llm = llm

    @staticmethod
    def parse(body: Any) -> PurchaseRequest:
        errors = schemas.validate(schemas.REQUEST_V1, body)
        if errors:
            raise ContractViolation(errors)
        return PurchaseRequest.model_validate(body)

    def evaluate(self, body: Any, trace_id: str) -> Result:
        request = self.parse(body)
        input_hash = canonical_hash(body)
        previous = self._audit.previous_decision(request.requestId, input_hash)
        if previous is not None:
            return Result(previous, True)
        outcome = self._agent.evaluate(request, trace_id)
        if outcome.decision.decision == Decision.NEEDS_INFO:
            case_id = str(uuid.uuid4())
            outcome = self._with_decision(outcome, outcome.decision.model_copy(update={"caseId": case_id}))
            with self._sessions.begin() as s:
                s.add(ApprovalCase(case_id=case_id, request_id=request.requestId, status="OPEN", round=1,
                                   state_summary="Rodada 1: NEEDS_INFO. Pendências: "
                                                 + "; ".join(outcome.decision.missingInformation),
                                   request_json=json.dumps(body, ensure_ascii=False),
                                   last_decision_id=outcome.decision.audit.decisionId))
        self._audit.record(outcome, input_hash)
        return Result(outcome.decision, False)

    def continue_case(self, case_id: str, message: str | None, updates: dict[str, Any] | None,
                      trace_id: str) -> Result:
        """Nova rodada de um caso NEEDS_INFO.

        A resposta do solicitante complementa a justificativa (continua sendo dado não confiável) e o estado do caso
        é re-sumarizado com teto fixo, então o contexto não cresce por rodada. A chamada ao LLM acontece fora de
        transação de banco.
        """
        case = self.get_case(case_id)
        if case.status == "CLOSED":
            raise Conflict("Caso encerrado")
        if message and len(message) > 2000:
            raise ContractViolation(["message: acima de 2000 caracteres"])
        message = pii.mask(message)[0]  # a mensagem vai para o resumo (LLM) e para o caso persistido
        merged: dict[str, Any] = json.loads(case.request_json)
        if isinstance(updates, dict):
            _merge(merged, copy.deepcopy(updates))
        merged["requestId"] = case.request_id
        round_no = case.round + 1
        if message and message.strip():
            prev = merged.get("justification") or ""
            added = f"[Complemento rodada {round_no}] {message.strip()}"
            room = max(0, MAX_JUSTIFICATION - len(added) - 1)  # mantém o contrato (4.000), preserva o mais recente
            merged["justification"] = (prev[-room:] + "\n" if prev and room else "") + added
        request = self.parse(merged)
        summary = self._summarize(case.state_summary, message, updates)
        outcome = self._agent.evaluate(request, trace_id, case_id, summary)
        decision = outcome.decision

        if decision.decision == Decision.NEEDS_INFO and round_no >= self._s.max_case_rounds:
            decision = decision.model_copy(update={
                "decision": Decision.ESCALATE_TO_HUMAN,
                "summary": decision.summary + " (Limite de rodadas de informação atingido; encaminhado para "
                                              "revisão humana.)",
                "erpPayload": ErpPayload(action="ROUTE_FOR_APPROVAL",
                                         approvalLevel=max(1, decision.erpPayload.approvalLevel),
                                         costCenter=decision.erpPayload.costCenter, requestId=decision.requestId),
                "audit": decision.audit.model_copy(update={
                    "guardrailEvents": [*decision.audit.guardrailEvents, "CASE_ROUNDS_EXHAUSTED"]}),
            })
            outcome = self._with_decision(outcome, decision)
        new_state = f"{summary} | Rodada {round_no}: {decision.decision.value}"[-1900:]
        with self._sessions.begin() as s:
            c = s.get(ApprovalCase, case_id)
            c.round = round_no
            c.request_json = json.dumps(merged, ensure_ascii=False)
            c.state_summary = new_state
            c.last_decision_id = decision.audit.decisionId
            c.status = "OPEN" if decision.decision == Decision.NEEDS_INFO else "CLOSED"
            c.updated_at = utcnow()
        self._audit.record(outcome, canonical_hash(merged))
        return Result(decision, False)

    def get_case(self, case_id: str) -> ApprovalCase:
        with self._sessions() as s:
            case = s.get(ApprovalCase, case_id)
        if case is None:
            raise NotFound("Caso não encontrado")
        return case

    def _summarize(self, previous: str, message: str | None, updates: dict[str, Any] | None) -> str:
        new_round = f"Mensagem do solicitante: {message or '(nenhuma)'}"
        if updates:
            new_round += f" | Campos atualizados: {json.dumps(updates, ensure_ascii=False)}"
        try:
            skill = self._registry.required(sk.CASE_SUMMARIZER)
            r = self._llm.complete(LlmRequest(
                "case-summarizer", self._s.reviewer_model, renderer.system(skill.content),
                f"<previous_summary>\n{previous}\n</previous_summary>\n<new_round>\n{neutralize(new_round)}\n"
                "</new_round>", None, 400))
            return r.text[:600]
        except LlmError:
            return f"{previous} | {new_round}"[-600:]  # fallback determinístico com teto fixo

    @staticmethod
    def _with_decision(outcome: AgentOutcome, decision: PurchaseDecision) -> AgentOutcome:
        return AgentOutcome(decision, outcome.request, outcome.context, outcome.policy)
