"""Contratos de entrada e saída (espelham os JSON Schemas v1 em resources/schemas)."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Decision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"
    NEEDS_INFO = "NEEDS_INFO"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    def at_least(self, other: RiskLevel) -> bool:
        order = list(RiskLevel)
        return order.index(self) >= order.index(other)


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DecidedBy(StrEnum):
    """Quem efetivamente determinou a decisão final."""

    AGENT = "AGENT"
    RULE_ENGINE = "RULE_ENGINE"
    VALIDATOR_OVERRIDE = "VALIDATOR_OVERRIDE"
    COMPLIANCE_OVERRIDE = "COMPLIANCE_OVERRIDE"
    FALLBACK = "FALLBACK"


# ---------- Entrada (tolerante: só o mínimo é obrigatório, garantido pelo JSON Schema) ----------


class Requester(BaseModel):
    employeeId: str | None = None
    name: str | None = None
    department: str | None = None
    costCenter: str


class Supplier(BaseModel):
    taxId: str | None = None
    name: str | None = None


class Item(BaseModel):
    sku: str | None = None
    description: str | None = None
    category: str | None = None
    quantity: int | None = None
    unitPrice: Decimal | None = None


class PurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requestId: str
    requestedAt: str | None = None
    requester: Requester
    supplier: Supplier | None = None
    items: list[Item]
    totalAmount: Decimal | None = None
    currency: str | None = None
    justification: str | None = None
    neededBy: str | None = None
    urgency: str | None = None


# ---------- O que o LLM produz (llm-assessment.v1, imposto via structured outputs) ----------


class Reason(BaseModel):
    code: str
    severity: Severity
    explanation: str
    evidenceIds: list[str]


class LlmAssessment(BaseModel):
    """Só o julgamento. Evidências, checagens, payload de ERP e auditoria são montados pelo código."""

    decision: Decision
    riskLevel: RiskLevel
    riskScore: int
    confidence: float
    summary: str
    reasons: list[Reason]
    missingInformation: list[str]


# ---------- Saída (purchase-decision.v1) ----------


class PolicyCheckView(BaseModel):
    policyId: str
    version: str | None
    result: str
    detail: str | None
    source: str = "RULE_ENGINE"


class DataQualityView(BaseModel):
    score: float
    issues: list[str]


class EvidenceView(BaseModel):
    id: str
    source: str
    summary: str


class ErpPayload(BaseModel):
    action: str
    approvalLevel: int
    costCenter: str | None
    requestId: str


class Tokens(BaseModel):
    input: int
    output: int
    cacheRead: int


class Audit(BaseModel):
    traceId: str
    decisionId: str
    decidedBy: DecidedBy
    skillVersions: dict[str, str]
    model: str | None
    tokens: Tokens
    estimatedCostUsd: float
    latencyMs: int
    llmCalls: int
    fallbackReason: str | None
    guardrailEvents: list[str] = Field(default_factory=list)


class PurchaseDecision(BaseModel):
    schemaVersion: str = "1.0"
    requestId: str
    caseId: str | None = None
    decision: Decision
    riskLevel: RiskLevel
    riskScore: int
    confidence: float
    summary: str
    reasons: list[Reason]
    policyChecks: list[PolicyCheckView]
    missingInformation: list[str]
    dataQuality: DataQualityView
    evidence: list[EvidenceView]
    erpPayload: ErpPayload
    audit: Audit
