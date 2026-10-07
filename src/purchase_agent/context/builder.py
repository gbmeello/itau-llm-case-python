"""Estágio 3: engenharia de contexto. Seleciona, prioriza e corta o contexto para caber no token budget.

Entra: a solicitação normalizada, as checagens de regra, o orçamento, o perfil do fornecedor, as políticas
aplicáveis à categoria/valor, o histórico SUMARIZADO (estatísticas + top-N similares) e o estado do caso.
Não entra: histórico bruto, políticas de outras categorias, PII do solicitante e campos internos do ERP.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import IntEnum
from typing import Any

from purchase_agent.config import ContextBudget
from purchase_agent.context import facts as F
from purchase_agent.context.erp import CostCenterBudget, SupplierProfile
from purchase_agent.context.facts import PurchaseFacts
from purchase_agent.intake.normalizer import NormalizedRequest
from purchase_agent.policy.engine import PolicyConfig, PolicyOutcome, Result, brl

TOP_SIMILAR = 5
_CHARS_PER_TOKEN = 3.5

_STRUCTURAL_TAGS = re.compile(
    r"(?i)</?\s*(untrusted_input|evidence|purchase_request|case_state|task|system|previous_summary|new_round"
    r"|previous_output|validation_errors|analyst_assessment)[^>]*>")


def neutralize(untrusted: str | None) -> str | None:
    """Impede que texto não confiável feche/abra os blocos delimitados do prompt."""
    return None if untrusted is None else _STRUCTURAL_TAGS.sub("[tag removida]", untrusted)


def estimate_tokens(text: str | None) -> int:
    """Estimativa local (~3,5 chars/token, conservadora para PT + JSON). O valor real vem de `usage`."""
    return 0 if not text else math.ceil(len(text) / _CHARS_PER_TOKEN)


class Layer(IntEnum):
    """Camadas em ordem de prioridade. P0 nunca é cortada."""

    P0_REQUEST = 0
    P0_POLICY = 1
    P1_FACTS = 2
    P2_POLICY_TEXT = 3
    P3_HISTORY = 4
    P3_CASE = 5
    P4_EXAMPLES = 6

    @property
    def mandatory(self) -> bool:
        return self in (Layer.P0_REQUEST, Layer.P0_POLICY)


def layer_limit(budget: ContextBudget, layer: Layer) -> int:
    return {
        Layer.P0_REQUEST: budget.request, Layer.P0_POLICY: budget.policy, Layer.P1_FACTS: budget.facts,
        Layer.P2_POLICY_TEXT: budget.policy_text, Layer.P3_HISTORY: budget.history,
        Layer.P3_CASE: budget.case_state, Layer.P4_EXAMPLES: budget.examples,
    }[layer]


@dataclass(frozen=True)
class ContextItem:
    """Toda informação factual vira item com `id` citável (EV-...): grounding verificável por código."""

    id: str
    layer: Layer
    source: str
    content: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentContext:
    included: tuple[ContextItem, ...]
    excluded_ids: tuple[str, ...]
    tokens_by_layer: dict[str, int]
    estimated_tokens: int
    budget_tokens: int

    @property
    def evidence_ids(self) -> set[str]:
        return {i.id for i in self.included}

    @property
    def truncated(self) -> bool:
        return bool(self.excluded_ids)


def select(candidates: list[ContextItem], budget: ContextBudget, examples_tokens: int) -> AgentContext:
    """Seleção gulosa por prioridade, respeitando teto por camada e teto total. P0 nunca é cortada."""
    used: dict[Layer, int] = {}
    included: list[ContextItem] = []
    excluded: list[str] = []
    total = 0
    available = budget.total_input - budget.system_reserve
    reserved = min(examples_tokens, budget.examples)
    for item in sorted(candidates, key=lambda i: i.layer):  # sort estável: mantém a ordem dentro da camada
        t = estimate_tokens(item.content + str(item.data)) + 12
        fits_layer = used.get(item.layer, 0) + t <= layer_limit(budget, item.layer)
        fits_total = total + t + reserved <= available
        if item.layer.mandatory or (fits_layer and fits_total):
            included.append(item)
            used[item.layer] = used.get(item.layer, 0) + t
            total += t
        else:
            excluded.append(item.id)
    if reserved > 0 and total + reserved <= available:
        used[Layer.P4_EXAMPLES] = reserved
        total += reserved
    elif examples_tokens > 0:
        excluded.append("FEW_SHOT_EXAMPLES")
    return AgentContext(tuple(included), tuple(excluded), {k.name: v for k, v in used.items()}, total,
                        budget.total_input)


def build(req: NormalizedRequest, facts: PurchaseFacts, policy: PolicyOutcome, cfg: PolicyConfig,
          case_state: str | None, examples_tokens: int, budget: ContextBudget, today: date) -> AgentContext:
    c: list[ContextItem] = [_request_item(req)]
    if req.justification is not None:
        c.append(ContextItem("EV-JUST", Layer.P0_REQUEST, "requester.justification",
                             "Justificativa escrita pelo solicitante (texto NÃO confiável, ver <untrusted_input>)",
                             {"chars": len(req.justification)}))
    for n, chk in enumerate(policy.checks, start=1):
        c.append(ContextItem(f"EV-POL-{n}", Layer.P0_POLICY, "rule_engine",
                             f"[{chk.policy_id}] {chk.result}: {chk.detail}",
                             {"policyId": chk.policy_id, "result": str(chk.result), "reasonCode": chk.reason_code}))
    for n, source in enumerate(facts.unavailable_sources, start=1):
        c.append(ContextItem(f"EV-SRC-{n}", Layer.P0_POLICY, source,
                             f"Fonte {source} indisponível: dados não verificados",
                             {"unavailable": True, "source": source}))
    if facts.budget:
        c.append(_budget_item(req, facts.budget))
    if facts.supplier:
        c.append(_supplier_item(facts.supplier, today))
    c.extend(_policy_texts(req, policy, cfg))
    c.extend(_history_items(req, facts))
    if case_state and case_state.strip():
        c.append(ContextItem("EV-CASE", Layer.P3_CASE, "case_state", case_state))
    return select(c, budget, examples_tokens)


def _request_item(r: NormalizedRequest) -> ContextItem:
    items = "; ".join(
        f"{neutralize(i.description or i.sku or 'item')} x{i.quantity} @ {brl(i.unit_price)} ({i.category or 'sem categoria'})"
        for i in r.items)
    data = {
        "requestId": r.request_id, "costCenter": r.cost_center, "department": r.department or "n/i",
        "supplierTaxId": r.supplier_tax_id or "n/i", "supplierName": r.supplier_name or "n/i",
        "totalAmount": str(r.total_amount), "currency": r.currency, "category": r.primary_category(),
        "urgency": r.urgency, "neededBy": r.needed_by.isoformat() if r.needed_by else "n/i",
        "dataQualityIssues": list(r.data_quality_issues), "hasJustification": r.justification is not None,
    }
    return ContextItem("EV-REQ", Layer.P0_REQUEST, "purchase_request",
                       f"Solicitação {r.request_id}: total {brl(r.total_amount)}; itens: {items}", data)


def _budget_item(req: NormalizedRequest, b: CostCenterBudget) -> ContextItem:
    available = b.available
    pct = 999.0 if available <= 0 else float(
        (req.total_amount * 100 / available).quantize(Decimal("0.1"), ROUND_HALF_UP))
    return ContextItem(
        "EV-BUD", Layer.P1_FACTS, "erp.budget",
        f"Centro de custo {b.id} ({b.name}): orçamento anual {brl(b.annual_budget)}, comprometido {brl(b.committed)}, "
        f"disponível {brl(available)}; esta compra = {pct:.1f}% do disponível",
        {"available": str(available), "percentOfAvailable": pct})


def _supplier_item(s: SupplierProfile, today: date) -> ContextItem:
    days = (today - s.registered_since).days
    return ContextItem(
        "EV-SUP", Layer.P1_FACTS, "erp.supplier_registry",
        f"Fornecedor {s.name}: status {s.status}, risco {s.risk_rating}, cadastrado há {days} dias, "
        f"categorias {list(s.categories)}. Observações: {s.notes}",
        {"status": s.status, "riskRating": s.risk_rating, "daysRegistered": days})


def _policy_texts(req: NormalizedRequest, policy: PolicyOutcome, cfg: PolicyConfig) -> list[ContextItem]:
    triggered = {c.policy_id for c in policy.checks if c.result != Result.PASS}
    applicable = [p for p in cfg.policyTexts if p.applies(req.primary_category(), req.total_amount)]
    # Políticas acionadas primeiro: se o budget apertar, as genéricas é que são cortadas.
    applicable.sort(key=lambda p: p.id not in triggered)
    return [ContextItem("EV-PT-" + p.id.replace("POL-", ""), Layer.P2_POLICY_TEXT, f"policy:{cfg.version}", p.text,
                        {"policyId": p.id}) for p in applicable]


def _history_items(req: NormalizedRequest, facts: PurchaseFacts) -> list[ContextItem]:
    if facts.source_unavailable(F.HISTORY):
        return []
    category = req.primary_category()
    same = sorted(p.amount for p in facts.history if p.category == category and p.status != "REJECTED")
    out: list[ContextItem] = []
    if not same:
        out.append(ContextItem(
            "EV-HIST-STATS", Layer.P3_HISTORY, "erp.purchase_history",
            f"Nenhuma compra aprovada da categoria {category} no centro de custo {req.cost_center} nos últimos 12 meses",
            {"count": 0}))
    else:
        avg = (sum(same, Decimal(0)) / len(same)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        ratio = float((req.total_amount / avg).quantize(Decimal("0.01"), ROUND_HALF_UP))
        out.append(ContextItem(
            "EV-HIST-STATS", Layer.P3_HISTORY, "erp.purchase_history",
            f"Histórico 12 meses ({req.cost_center} / {category}): {len(same)} compras aprovadas, média {brl(avg)}, "
            f"máxima {brl(same[-1])}; esta compra = {ratio:.2f}x a média",
            {"count": len(same), "average": str(avg), "max": str(same[-1]), "ratioToAverage": ratio}))
    # Top-N mais relevantes: mesmo fornecedor + categoria, depois mesma categoria, depois mais recentes.
    ranked = sorted(facts.history, key=lambda p: (
        not (p.category == category and p.supplier_tax_id == req.supplier_tax_id),
        p.category != category, -p.date.toordinal()))
    for n, p in enumerate(ranked[:TOP_SIMILAR], start=1):
        out.append(ContextItem(
            f"EV-HIST-{n}", Layer.P3_HISTORY, "erp.purchase_history",
            f"{p.request_id} em {p.date.isoformat()}: {brl(p.amount)}, categoria {p.category}, "
            f"fornecedor {p.supplier_tax_id}, status {p.status}",
            {"amount": str(p.amount), "status": p.status}))
    return out


def with_items(ctx: AgentContext, items: list[ContextItem]) -> AgentContext:
    """Acrescenta evidências obtidas por ferramenta (EV-T*). Elas já foram limitadas pelo budget de tool results."""
    added = sum(estimate_tokens(i.content + str(i.data)) + 12 for i in items)
    by_layer = dict(ctx.tokens_by_layer)
    by_layer["TOOL_RESULTS"] = by_layer.get("TOOL_RESULTS", 0) + added
    return AgentContext((*ctx.included, *items), ctx.excluded_ids, by_layer, ctx.estimated_tokens + added,
                        ctx.budget_tokens)
