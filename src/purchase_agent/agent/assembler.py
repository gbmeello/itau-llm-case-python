"""Monta o PurchaseDecision final: a parte que é responsabilidade do código, não do modelo."""

from __future__ import annotations

from purchase_agent.context.builder import AgentContext
from purchase_agent.contract.models import (
    Audit, DataQualityView, DecidedBy, Decision, ErpPayload, EvidenceView, LlmAssessment, PolicyCheckView,
    PurchaseDecision, Reason, RiskLevel, Severity, Tokens,
)
from purchase_agent.intake.normalizer import NormalizedRequest
from purchase_agent.llm.client import LlmResponse
from purchase_agent.llm.pricing import cost_usd
from purchase_agent.policy.engine import PolicyOutcome, Result


class UsageTracker:
    """Consumo de todas as chamadas LLM de uma decisão (custo por decisão é a unidade de FinOps)."""

    def __init__(self) -> None:
        self.calls: list[LlmResponse] = []
        self.failed = 0

    def add(self, r: LlmResponse) -> None:
        self.calls.append(r)

    @property
    def call_count(self) -> int:
        return len(self.calls) + self.failed

    @property
    def cost(self) -> float:
        return round(sum(cost_usd(c) for c in self.calls), 6)

    def tokens(self) -> Tokens:
        return Tokens(input=sum(c.input_tokens for c in self.calls),
                      output=sum(c.output_tokens for c in self.calls),
                      cacheRead=sum(c.cache_read_tokens for c in self.calls))

    @property
    def primary_model(self) -> str | None:
        return self.calls[0].model if self.calls else None


def erp_payload(decision: Decision, policy: PolicyOutcome, req: NormalizedRequest) -> ErpPayload:
    level = policy.approval_level
    action, lvl = {
        Decision.APPROVE: ("AUTO_APPROVE", 0) if level == 0 else ("ROUTE_FOR_APPROVAL", level),
        Decision.REJECT: ("REJECT", level),
        Decision.ESCALATE_TO_HUMAN: ("ROUTE_FOR_APPROVAL", max(1, level)),
        Decision.NEEDS_INFO: ("RETURN_TO_REQUESTER", level),
    }[decision]
    return ErpPayload(action=action, approvalLevel=lvl, costCenter=req.cost_center, requestId=req.request_id)


def assemble(req: NormalizedRequest, policy: PolicyOutcome, ctx: AgentContext, a: LlmAssessment,
             decided_by: DecidedBy, case_id: str | None, trace_id: str, decision_id: str, skills: dict[str, str],
             usage: UsageTracker, latency_ms: int, fallback_reason: str | None,
             guardrail_events: list[str]) -> PurchaseDecision:
    missing = list(dict.fromkeys([*a.missingInformation,
                                  *((*req.missing_information, *policy.missing_information)
                                    if a.decision == Decision.NEEDS_INFO else ())]))
    return PurchaseDecision(
        requestId=req.request_id, caseId=case_id, decision=a.decision, riskLevel=a.riskLevel,
        riskScore=a.riskScore, confidence=a.confidence, summary=a.summary, reasons=a.reasons,
        policyChecks=[PolicyCheckView(policyId=c.policy_id, version=policy.policy_version, result=c.result.value,
                                      detail=c.detail) for c in policy.checks],
        missingInformation=missing,
        dataQuality=DataQualityView(score=req.data_quality_score(), issues=list(req.data_quality_issues)),
        evidence=[EvidenceView(id=i.id, source=i.source, summary=i.content) for i in ctx.included],
        erpPayload=erp_payload(a.decision, policy, req),
        audit=Audit(traceId=trace_id, decisionId=decision_id, decidedBy=decided_by, skillVersions=skills,
                    model=usage.primary_model, tokens=usage.tokens(), estimatedCostUsd=usage.cost,
                    latencyMs=latency_ms, llmCalls=usage.call_count, fallbackReason=fallback_reason,
                    guardrailEvents=guardrail_events))


def _reasons_from_policy(policy: PolicyOutcome, ctx: AgentContext, result: Result, severity: Severity) -> list[Reason]:
    out = []
    for n, chk in enumerate(policy.checks, start=1):
        if chk.result == result:
            ev = f"EV-POL-{n}"
            out.append(Reason(code=chk.reason_code, severity=severity, explanation=chk.detail,
                              evidenceIds=[ev] if ev in ctx.evidence_ids else ["EV-REQ"]))
    return out


def rule_engine_reject(policy: PolicyOutcome, ctx: AgentContext) -> LlmAssessment:
    """Decisão do motor de regras (sem LLM): rejeição obrigatória."""
    reasons = _reasons_from_policy(policy, ctx, Result.HARD_REJECT, Severity.CRITICAL)
    blocked = any(r.code == "SUPPLIER_BLOCKED" for r in reasons)
    details = "; ".join(c.detail for c in policy.with_result(Result.HARD_REJECT))
    return LlmAssessment(decision=Decision.REJECT, riskLevel=RiskLevel.CRITICAL if blocked else RiskLevel.HIGH,
                         riskScore=95 if blocked else 75, confidence=1.0,
                         summary=f"Rejeitado por regra obrigatória: {details}.", reasons=reasons,
                         missingInformation=[])


def fallback(policy: PolicyOutcome, ctx: AgentContext, reason: str) -> LlmAssessment:
    """LLM sem avaliação utilizável: falha fechada para humano, ainda com razões ancoradas em evidência."""
    reasons = [*_reasons_from_policy(policy, ctx, Result.BLOCKS_APPROVAL, Severity.HIGH),
               *_reasons_from_policy(policy, ctx, Result.WARN, Severity.MEDIUM),
               Reason(code="OTHER", severity=Severity.MEDIUM,
                      explanation=f"Análise automática indisponível ({reason}); decisão requer revisão humana.",
                      evidenceIds=["EV-REQ"])]
    return LlmAssessment(decision=Decision.ESCALATE_TO_HUMAN, riskLevel=RiskLevel.MEDIUM, riskScore=40,
                         confidence=0.0,
                         summary="Análise automática indisponível; solicitação encaminhada para revisão humana "
                                 "com as checagens de regra.",
                         reasons=reasons, missingInformation=[])
