"""Estágio 2: regras de negócio determinísticas.

Rodam antes do LLM e definem limites que ele não pode ultrapassar:
HARD_REJECT (rejeição obrigatória, sem LLM) e BLOCKS_APPROVAL (o modelo não pode aprovar).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from purchase_agent.context import facts as F
from purchase_agent.context.facts import PurchaseFacts
from purchase_agent.intake.normalizer import NormalizedRequest


class Result(StrEnum):
    PASS = "PASS"
    INFO = "INFO"
    WARN = "WARN"
    BLOCKS_APPROVAL = "BLOCKS_APPROVAL"
    HARD_REJECT = "HARD_REJECT"


class ApprovalLevel(BaseModel):
    level: int
    maxAmount: Decimal | None
    approver: str


class RestrictedCategory(BaseModel):
    category: str
    maxAmount: Decimal
    reason: str


class PolicyText(BaseModel):
    id: str
    appliesTo: list[str]
    minAmount: Decimal
    text: str

    def applies(self, category: str, amount: Decimal) -> bool:
        return ("*" in self.appliesTo or category in self.appliesTo) and amount >= self.minAmount


class PolicyConfig(BaseModel):
    """Parâmetros de política: skill do tipo POLICY, versionada no registry."""

    version: str
    autoApprovalLimit: Decimal
    maxAgentAmount: Decimal
    approvalLevels: list[ApprovalLevel]
    budgetWarnRatio: float
    newSupplierDays: int
    splitWindowDays: int
    quotesThreshold: Decimal
    restrictedCategories: list[RestrictedCategory]
    policyTexts: list[PolicyText]

    def level_for(self, amount: Decimal) -> ApprovalLevel:
        for lvl in self.approvalLevels:
            if lvl.maxAmount is None or amount <= lvl.maxAmount:
                return lvl
        return self.approvalLevels[-1]


@dataclass(frozen=True)
class PolicyCheck:
    policy_id: str
    result: Result
    reason_code: str
    detail: str


@dataclass(frozen=True)
class PolicyOutcome:
    policy_version: str
    checks: tuple[PolicyCheck, ...]
    approval_level: int
    approver: str
    missing_information: tuple[str, ...]

    @property
    def hard_reject(self) -> bool:
        return any(c.result == Result.HARD_REJECT for c in self.checks)

    @property
    def blocks_approval(self) -> bool:
        return self.hard_reject or any(c.result == Result.BLOCKS_APPROVAL for c in self.checks)

    def with_result(self, result: Result) -> list[PolicyCheck]:
        return [c for c in self.checks if c.result == result]


def brl(v: Decimal | float | Any) -> str:
    s = f"{Decimal(str(v)):,.2f}"
    return "R$ " + s.replace(",", "_").replace(".", ",").replace("_", ".")


def evaluate(req: NormalizedRequest, facts: PurchaseFacts, cfg: PolicyConfig, today: date) -> PolicyOutcome:
    checks: list[PolicyCheck] = []
    missing: list[str] = []
    amount = req.total_amount

    level = cfg.level_for(amount)
    checks.append(PolicyCheck("POL-ALCADA-001", Result.PASS if level.level == 0 else Result.INFO,
                              "APPROVAL_LEVEL_REQUIRED",
                              f"Valor {brl(amount)} exige alçada {level.level} ({level.approver})"))
    if amount > cfg.maxAgentAmount:
        checks.append(PolicyCheck("POL-ALCADA-001", Result.BLOCKS_APPROVAL, "APPROVAL_LEVEL_REQUIRED",
                                  f"Acima de {brl(cfg.maxAgentAmount)}: decisão exclusiva do comitê executivo"))

    _supplier_rules(req, facts, cfg, today, checks)
    _budget_rules(req, facts, cfg, checks, missing)
    _category_rules(req, cfg, checks)
    _split_purchase_rule(req, facts, cfg, level, today, checks)

    if amount > cfg.quotesThreshold:
        checks.append(PolicyCheck("POL-COT-006", Result.INFO, "POLICY_VIOLATION",
                                  f"Acima de {brl(cfg.quotesThreshold)}: exige 3 cotações ou justificativa de fornecedor único"))
    if any(i.startswith("ITEM_PRICE_MISSING") for i in req.data_quality_issues):
        checks.append(PolicyCheck("DATA-QUALITY", Result.BLOCKS_APPROVAL, "MISSING_INFORMATION",
                                  "Itens sem preço: valor total não é confiável"))
    if req.suspicious_input:
        checks.append(PolicyCheck("SEC-INPUT", Result.BLOCKS_APPROVAL, "SUSPICIOUS_INPUT",
                                  "Texto livre contém padrão de instrução ao sistema (possível prompt injection)"))
    return PolicyOutcome(cfg.version, tuple(checks), level.level, level.approver, tuple(missing))


def _supplier_rules(req: NormalizedRequest, facts: PurchaseFacts, cfg: PolicyConfig, today: date,
                    checks: list[PolicyCheck]) -> None:
    pid = "POL-FORN-002"
    if req.supplier_tax_id is None:
        checks.append(PolicyCheck(pid, Result.BLOCKS_APPROVAL, "MISSING_INFORMATION", "Fornecedor não informado"))
        return
    if facts.source_unavailable(F.SUPPLIER):
        checks.append(PolicyCheck(pid, Result.BLOCKS_APPROVAL, "SUPPLIER_UNKNOWN",
                                  "Cadastro de fornecedores indisponível: situação do fornecedor não verificada"))
        return
    s = facts.supplier
    if s is None:
        checks.append(PolicyCheck(pid, Result.BLOCKS_APPROVAL, "SUPPLIER_UNKNOWN", "Fornecedor não homologado no cadastro"))
        return
    if s.blocked:
        checks.append(PolicyCheck(pid, Result.HARD_REJECT, "SUPPLIER_BLOCKED", f"Fornecedor {s.name} está BLOQUEADO"))
        return
    added = False
    if s.risk_rating in ("HIGH", "CRITICAL"):
        checks.append(PolicyCheck(pid, Result.BLOCKS_APPROVAL, "SUPPLIER_HIGH_RISK",
                                  f"Fornecedor com rating de risco {s.risk_rating}"))
        added = True
    days = (today - s.registered_since).days
    if days < cfg.newSupplierDays:
        checks.append(PolicyCheck(pid, Result.BLOCKS_APPROVAL, "SUPPLIER_NEW",
                                  f"Fornecedor cadastrado há {days} dias (< {cfg.newSupplierDays})"))
        added = True
    category = req.primary_category()
    if category != "UNCATEGORIZED" and category not in s.categories:
        checks.append(PolicyCheck(pid, Result.WARN, "POLICY_VIOLATION",
                                  f"Fornecedor não homologado para a categoria {category}"))
        added = True
    if not added:
        checks.append(PolicyCheck(pid, Result.PASS, "WITHIN_POLICY", f"Fornecedor ativo, risco {s.risk_rating}"))


def _budget_rules(req: NormalizedRequest, facts: PurchaseFacts, cfg: PolicyConfig, checks: list[PolicyCheck],
                  missing: list[str]) -> None:
    pid = "POL-ORC-003"
    if facts.source_unavailable(F.BUDGET):
        checks.append(PolicyCheck(pid, Result.BLOCKS_APPROVAL, "BUDGET_INSUFFICIENT",
                                  "Sistema de orçamento indisponível: saldo não verificado"))
        return
    if facts.budget is None:
        checks.append(PolicyCheck(pid, Result.BLOCKS_APPROVAL, "MISSING_INFORMATION",
                                  f"Centro de custo {req.cost_center} não encontrado"))
        missing.append("Centro de custo válido")
        return
    available = facts.budget.available
    if req.total_amount > available:
        checks.append(PolicyCheck(pid, Result.HARD_REJECT, "BUDGET_INSUFFICIENT",
                                  f"Valor {brl(req.total_amount)} excede saldo disponível {brl(available)}"))
    elif req.total_amount > available * Decimal(str(cfg.budgetWarnRatio)):
        checks.append(PolicyCheck(pid, Result.WARN, "BUDGET_TIGHT",
                                  f"Valor consome mais de {round(cfg.budgetWarnRatio * 100)}% do saldo disponível {brl(available)}"))
    else:
        checks.append(PolicyCheck(pid, Result.PASS, "WITHIN_POLICY", f"Saldo disponível {brl(available)}"))


def _category_rules(req: NormalizedRequest, cfg: PolicyConfig, checks: list[PolicyCheck]) -> None:
    for rc in cfg.restrictedCategories:
        total = sum((i.total for i in req.items if i.category == rc.category), Decimal(0))
        if total > rc.maxAmount:
            checks.append(PolicyCheck("POL-CAT-004", Result.BLOCKS_APPROVAL, "POLICY_VIOLATION",
                                      f"{rc.reason} ({brl(total)} em {rc.category})"))


def _split_purchase_rule(req: NormalizedRequest, facts: PurchaseFacts, cfg: PolicyConfig, level: ApprovalLevel,
                         today: date, checks: list[PolicyCheck]) -> None:
    if req.supplier_tax_id is None:
        return
    since = today - timedelta(days=cfg.splitWindowDays)
    category = req.primary_category()
    related = [p for p in facts.history
               if p.date >= since and p.supplier_tax_id == req.supplier_tax_id
               and p.category == category and p.status != "REJECTED"]
    if not related:
        return
    cumulative = req.total_amount + sum((p.amount for p in related), Decimal(0))
    cumulative_level = cfg.level_for(cumulative)
    if cumulative_level.level > level.level:
        checks.append(PolicyCheck(
            "POL-FRAC-005", Result.BLOCKS_APPROVAL, "SPLIT_PURCHASE_SUSPECTED",
            f"{len(related)} compra(s) do mesmo fornecedor/categoria em {cfg.splitWindowDays} dias; "
            f"acumulado {brl(cumulative)} exige alçada {cumulative_level.level}"))
