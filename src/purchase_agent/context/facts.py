"""Pré-busca determinística dos fatos (orçamento, fornecedor, histórico de 12 meses).

Falha de uma fonte não derruba o fluxo: vira `unavailable_sources` e bloqueia aprovação automática.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, TypeVar

from purchase_agent.context.erp import CostCenterBudget, ErpGateway, PurchaseRecord, SupplierProfile
from purchase_agent.intake.normalizer import NormalizedRequest

BUDGET = "erp.budget"
SUPPLIER = "erp.supplier_registry"
HISTORY = "erp.purchase_history"

log = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class PurchaseFacts:
    budget: CostCenterBudget | None
    supplier: SupplierProfile | None
    history: tuple[PurchaseRecord, ...]
    unavailable_sources: tuple[str, ...]

    def source_unavailable(self, source: str) -> bool:
        return source in self.unavailable_sources


def collect(erp: ErpGateway, req: NormalizedRequest, today: date) -> PurchaseFacts:
    unavailable: list[str] = []

    def fetch(source: str, call: Callable[[], T], default: T) -> T:
        try:
            return call()
        except Exception as e:  # noqa: BLE001 - qualquer falha de fonte externa vira "indisponível"
            log.warning("context_source_unavailable source=%s error=%s", source, e)
            unavailable.append(source)
            return default

    budget = fetch(BUDGET, lambda: erp.cost_center_budget(req.cost_center), None)
    supplier = (fetch(SUPPLIER, lambda: erp.supplier_profile(req.supplier_tax_id), None)  # type: ignore[arg-type]
                if req.supplier_tax_id else None)
    history = fetch(HISTORY, lambda: erp.purchase_history(req.cost_center, today - timedelta(days=365)), [])
    return PurchaseFacts(budget, supplier, tuple(history), tuple(unavailable))
