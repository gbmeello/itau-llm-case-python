"""Estágio 5: validador determinístico da saída do LLM.

Dois tipos de problema:
- REPARÁVEIS (schema, IDs de evidência inexistentes, valores não encontrados nas evidências, NEEDS_INFO sem
  pendências): voltam ao modelo uma vez com a lista de erros.
- VIOLAÇÃO DE GUARDRAIL (APPROVE contra regra, com risco alto, com baixa confiança, ou com entrada suspeita):
  não se negocia com o modelo; a decisão é sobrescrita para ESCALATE_TO_HUMAN.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from purchase_agent.context.builder import AgentContext
from purchase_agent.contract import schemas
from purchase_agent.contract.models import Decision, LlmAssessment, Reason, RiskLevel, Severity
from purchase_agent.intake.normalizer import NormalizedRequest
from purchase_agent.policy.engine import PolicyOutcome

_MONEY = re.compile(r"R\$\s?([0-9][0-9.]*(?:,[0-9]{1,2})?)")
_NUMBER = re.compile(r"[0-9][0-9.]*(?:,[0-9]{1,2})?")
_BANDS = {RiskLevel.LOW: (0, 24), RiskLevel.MEDIUM: (25, 49), RiskLevel.HIGH: (50, 79), RiskLevel.CRITICAL: (80, 100)}


@dataclass(frozen=True)
class Parsed:
    assessment: LlmAssessment | None
    repairable_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.assessment is not None and not self.repairable_errors


@dataclass(frozen=True)
class GuardrailResult:
    assessment: LlmAssessment
    events: list[str]
    overridden: bool


def parse_money(raw: str) -> Decimal | None:
    """Aceita '78.000', '78.000,00', '78000.00', '25.100'."""
    s = raw.rstrip(".,")
    try:
        if "," in s:
            return Decimal(s.replace(".", "").replace(",", "."))
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            return Decimal(s.replace(".", ""))
        return Decimal(s)
    except InvalidOperation:
        return None


def money_mentions(text: str) -> list[Decimal]:
    return [v for m in _MONEY.finditer(text) if (v := parse_money(m.group(1))) is not None]


def _close(known: Decimal, mentioned: Decimal) -> bool:
    """Tolerância de 1%: 'R$ 25.100' passa; 'R$ 25 mil' inventado a partir de 25.100 não."""
    if known == 0:
        return mentioned == 0
    return abs(known - mentioned) / abs(known) <= Decimal("0.01")


def _strip_fences(s: str) -> str:
    t = (s or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(json)?", "", t)
        t = re.sub(r"```$", "", t).strip()
    return t


class DecisionValidator:
    def __init__(self, min_approve_confidence: float) -> None:
        self._min_confidence = min_approve_confidence

    def parse_and_check(self, raw_output: str, ctx: AgentContext) -> Parsed:
        try:
            node = json.loads(_strip_fences(raw_output))
        except ValueError as e:
            return Parsed(None, [f"Saída não é JSON válido: {e}"])
        if not isinstance(node, dict):
            return Parsed(None, ["Saída deve ser um objeto JSON"])
        schema_errors = schemas.validate(schemas.LLM_ASSESSMENT_V1, node)
        if schema_errors:
            return Parsed(None, [f"Schema: {e}" for e in schema_errors])
        a = LlmAssessment.model_validate(node)

        errors: list[str] = []
        if not 0 <= a.riskScore <= 100:
            errors.append("riskScore deve estar entre 0 e 100")
        if not 0 <= a.confidence <= 1:
            errors.append("confidence deve estar entre 0 e 1")
        if not a.summary.strip():
            errors.append("summary não pode ser vazio")
        if not a.reasons:
            errors.append("Inclua ao menos uma razão com evidência")
        ids = ctx.evidence_ids
        for i, r in enumerate(a.reasons):
            if not r.evidenceIds:
                errors.append(f"reasons[{i}] ({r.code}) não cita evidência")
            unknown = [e for e in r.evidenceIds if e not in ids]
            if unknown:
                errors.append(f"reasons[{i}] cita evidências inexistentes {unknown}; use apenas {sorted(ids)}")
        known = self._known_numbers(ctx)
        narrative = a.summary + " " + " ".join(r.explanation for r in a.reasons)
        for amount in money_mentions(narrative):
            if not any(_close(k, amount) for k in known):
                errors.append(f"Valor R$ {amount} citado não aparece nas evidências "
                              "(não calcule nem invente valores)")
        if a.decision == Decision.NEEDS_INFO and not a.missingInformation:
            errors.append("NEEDS_INFO exige missingInformation não vazio")
        return Parsed(a, errors)

    def enforce(self, a: LlmAssessment, policy: PolicyOutcome, req: NormalizedRequest) -> GuardrailResult:
        """Guardrails de decisão: o LLM não negocia regras."""
        events: list[str] = []
        a = self._normalize_risk_band(a, events)
        if a.decision == Decision.APPROVE:
            if policy.blocks_approval:
                events.append("APPROVE_BLOCKED_BY_POLICY")
            if any(r.severity in (Severity.HIGH, Severity.CRITICAL) for r in a.reasons) \
                    or a.riskLevel.at_least(RiskLevel.HIGH):
                events.append("APPROVE_WITH_HIGH_RISK")
            if a.confidence < self._min_confidence:
                events.append("APPROVE_LOW_CONFIDENCE")
        if req.suspicious_input and a.decision in (Decision.APPROVE, Decision.NEEDS_INFO):
            events.append("SUSPICIOUS_INPUT_NOT_ESCALATED")
        overridden = any(not e.startswith("RISK_SCORE_") for e in events)
        if overridden:
            a = escalate(a, f"Recomendação do modelo ({a.decision}) sobrescrita por guardrail: {events}",
                         " (Encaminhado para revisão humana por regra de segurança.)")
        return GuardrailResult(a, events, overridden)

    @staticmethod
    def _normalize_risk_band(a: LlmAssessment, events: list[str]) -> LlmAssessment:
        """Coerência riskScore × riskLevel: o nível (categórico) prevalece; o score é ajustado à faixa."""
        lo, hi = _BANDS[a.riskLevel]
        score = max(lo, min(hi, a.riskScore))
        if score != a.riskScore:
            events.append("RISK_SCORE_ADJUSTED_TO_BAND")
            return a.model_copy(update={"riskScore": score})
        return a

    @staticmethod
    def _known_numbers(ctx: AgentContext) -> set[Decimal]:
        out: set[Decimal] = set()
        for item in ctx.included:
            text = item.content + " " + " ".join(str(v) for v in item.data.values())
            for m in _NUMBER.finditer(text):
                if (v := parse_money(m.group())) is not None:
                    out.add(v)
        return out


def escalate(a: LlmAssessment, why: str, summary_suffix: str) -> LlmAssessment:
    """Falha fechada para humano, preservando as razões originais (que continuam ancoradas em evidência)."""
    cited = list(dict.fromkeys(e for r in a.reasons for e in r.evidenceIds)) or ["EV-REQ"]
    reasons = [*a.reasons, Reason(code="OTHER", severity=Severity.HIGH, explanation=why, evidenceIds=cited)]
    risk = a.riskLevel if a.riskLevel.at_least(RiskLevel.HIGH) else RiskLevel.HIGH
    return LlmAssessment(decision=Decision.ESCALATE_TO_HUMAN, riskLevel=risk, riskScore=max(a.riskScore, 50),
                         confidence=min(a.confidence, 0.5), summary=a.summary + summary_suffix, reasons=reasons,
                         missingInformation=a.missingInformation)
