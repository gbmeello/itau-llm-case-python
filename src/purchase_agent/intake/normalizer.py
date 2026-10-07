"""Estágio 1: transforma uma entrada possivelmente incompleta ou ruidosa em NormalizedRequest.

Nunca rejeita por qualidade de dado (isso é papel do JSON Schema): registra o problema e segue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from purchase_agent.contract.models import PurchaseRequest
from purchase_agent.intake import cnpj, pii
from purchase_agent.intake.injection import is_suspicious

MAX_JUSTIFICATION_CHARS = 2000
_TOLERANCE = Decimal("0.01")
_SUPPORTED_CURRENCIES = {"BRL"}
_URGENCIES = {"LOW", "NORMAL", "HIGH", "CRITICAL"}
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class NormalizedItem:
    sku: str | None
    description: str | None
    category: str | None
    quantity: int
    unit_price: Decimal

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class NormalizedRequest:
    """O que o resto do pipeline enxerga. PII (nome do solicitante) não segue adiante: só IDs."""

    request_id: str
    employee_id: str | None
    department: str | None
    cost_center: str
    supplier_tax_id: str | None
    supplier_name: str | None
    items: tuple[NormalizedItem, ...]
    total_amount: Decimal
    currency: str
    justification: str | None
    needed_by: date | None
    urgency: str
    data_quality_issues: tuple[str, ...]
    missing_information: tuple[str, ...]
    suspicious_input: bool

    def primary_category(self) -> str:
        """Categoria dominante por valor: seleciona políticas e histórico relevantes."""
        categorized = [i for i in self.items if i.category]
        if not categorized:
            return "UNCATEGORIZED"
        return max(categorized, key=lambda i: i.total).category or "UNCATEGORIZED"

    def data_quality_score(self) -> float:
        return max(0.0, 1.0 - 0.1 * len(self.data_quality_issues))


def _blank(s: str | None) -> bool:
    return s is None or not s.strip()


def _trim(s: str | None) -> str | None:
    return None if _blank(s) else s.strip()  # type: ignore[union-attr]


def normalize(req: PurchaseRequest, today: date) -> NormalizedRequest:
    issues: list[str] = []
    missing: list[str] = []
    pii_found: set[str] = set()

    def masked(text: str | None) -> str | None:
        out, kinds = pii.mask(text)
        pii_found.update(kinds)
        return out

    items: list[NormalizedItem] = []
    price_missing = False
    for it in req.items:
        sku = _trim(it.sku)
        qty = it.quantity or 0
        if not qty:
            issues.append(f"ITEM_QUANTITY_MISSING:{sku or '?'}")
            qty = max(qty, 1)
        price = it.unitPrice
        if price is None:
            price_missing = True
            issues.append(f"ITEM_PRICE_MISSING:{sku or '?'}")
            price = Decimal(0)
        category = _trim(it.category)
        if category is None:
            issues.append(f"ITEM_CATEGORY_MISSING:{sku or '?'}")
        else:
            category = category.upper().replace(" ", "_")
        items.append(NormalizedItem(sku, masked(_trim(it.description)), category, qty, price.quantize(_CENT, ROUND_HALF_UP)))
    if price_missing:
        missing.append("Preço unitário de todos os itens")

    computed = sum((i.total for i in items), Decimal(0)).quantize(_CENT, ROUND_HALF_UP)
    if req.totalAmount is None:
        issues.append("TOTAL_MISSING_COMPUTED")
        total = computed
    else:
        declared = req.totalAmount.quantize(_CENT, ROUND_HALF_UP)
        if computed > 0 and abs(declared - computed) > _TOLERANCE:
            # A fonte da verdade são os itens; a divergência vira sinal para o modelo e para auditoria.
            issues.append("TOTAL_MISMATCH_RECALCULATED")
        total = computed if computed > 0 else declared

    if _blank(req.currency):
        issues.append("CURRENCY_DEFAULTED")
        currency = "BRL"
    else:
        currency = req.currency.strip().upper().replace("R$", "BRL")  # type: ignore[union-attr]
        if currency not in _SUPPORTED_CURRENCIES:
            issues.append(f"UNSUPPORTED_CURRENCY:{currency}")

    supplier_tax_id = supplier_name = None
    if req.supplier is None or (_blank(req.supplier.taxId) and _blank(req.supplier.name)):
        issues.append("SUPPLIER_MISSING")
        missing.append("Fornecedor (CNPJ e razão social)")
    else:
        supplier_name = _trim(req.supplier.name)
        digits = re.sub(r"\D", "", req.supplier.taxId or "")
        supplier_tax_id = digits or None
        if supplier_tax_id is None:
            issues.append("SUPPLIER_TAXID_MISSING")
            missing.append("CNPJ do fornecedor")
        elif not cnpj.is_valid(supplier_tax_id):
            issues.append("SUPPLIER_TAXID_INVALID")
            missing.append("CNPJ válido do fornecedor")

    justification = masked(_trim(req.justification))
    if justification is None:
        issues.append("JUSTIFICATION_MISSING")
        missing.append("Justificativa de negócio da compra")
    else:
        if len(justification) > MAX_JUSTIFICATION_CHARS:
            justification = justification[:MAX_JUSTIFICATION_CHARS]
            issues.append("JUSTIFICATION_TRUNCATED")
        if len(justification) < 15:
            issues.append("JUSTIFICATION_TOO_SHORT")

    if pii_found:
        issues.append("PII_MASKED:" + ",".join(sorted(pii_found)))

    suspicious = is_suspicious(req.justification) or any(is_suspicious(i.description) for i in req.items)
    if suspicious:
        issues.append("SUSPICIOUS_INPUT")

    needed_by = None
    if not _blank(req.neededBy):
        try:
            needed_by = date.fromisoformat(req.neededBy.strip()[:10])  # type: ignore[union-attr]
            if needed_by < today:
                issues.append("NEEDED_BY_IN_PAST")
        except ValueError:
            issues.append("NEEDED_BY_INVALID")

    urgency = "NORMAL"
    if not _blank(req.urgency):
        u = req.urgency.strip().upper()  # type: ignore[union-attr]
        u = "HIGH" if u in ("URGENTE", "URGENT") else u
        if u in _URGENCIES:
            urgency = u
        else:
            issues.append("URGENCY_UNKNOWN")

    return NormalizedRequest(
        request_id=req.requestId.strip(),
        employee_id=req.requester.employeeId,
        department=_trim(req.requester.department),
        cost_center=req.requester.costCenter.strip().upper(),
        supplier_tax_id=supplier_tax_id,
        supplier_name=supplier_name,
        items=tuple(items),
        total_amount=total,
        currency=currency,
        justification=justification,
        needed_by=needed_by,
        urgency=urgency,
        data_quality_issues=tuple(issues),
        missing_information=tuple(missing),
        suspicious_input=suspicious,
    )
