"""Porta para sistemas externos (ERP, cadastro de fornecedores). Somente leitura.

Implementações: MockErpGateway (in-process, JSON sintético) e McpErpGateway (cliente MCP; ver mcp_server/).
O agente depende apenas do Protocol, então trocar a fonte não muda o pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from purchase_agent import resources


class ContextSourceUnavailable(RuntimeError):
    """Fonte de contexto fora do ar. Vira EV-SRC-* e bloqueia aprovação; nunca derruba o fluxo."""


@dataclass(frozen=True)
class CostCenterBudget:
    id: str
    name: str
    fiscal_year: int
    annual_budget: Decimal
    committed: Decimal

    @property
    def available(self) -> Decimal:
        return self.annual_budget - self.committed


@dataclass(frozen=True)
class SupplierProfile:
    tax_id: str
    name: str
    status: str
    risk_rating: str
    registered_since: date
    categories: tuple[str, ...]
    notes: str

    @property
    def blocked(self) -> bool:
        return self.status == "BLOCKED"


@dataclass(frozen=True)
class PurchaseRecord:
    request_id: str
    date: date
    cost_center: str
    category: str
    supplier_tax_id: str
    amount: Decimal
    status: str


class ErpGateway(Protocol):
    def cost_center_budget(self, cost_center_id: str) -> CostCenterBudget | None: ...

    def supplier_profile(self, tax_id: str) -> SupplierProfile | None: ...

    def purchase_history(self, cost_center_id: str, since: date) -> list[PurchaseRecord]: ...


# ---------- (de)serialização compartilhada entre o mock e o servidor/cliente MCP ----------


def budget_from(d: dict[str, Any]) -> CostCenterBudget:
    return CostCenterBudget(d["id"], d["name"], int(d["fiscalYear"]), Decimal(str(d["annualBudget"])),
                            Decimal(str(d["committed"])))


def supplier_from(d: dict[str, Any]) -> SupplierProfile:
    return SupplierProfile(d["taxId"], d["name"], d["status"], d["riskRating"],
                           date.fromisoformat(d["registeredSince"]), tuple(d["categories"]), d.get("notes", ""))


def record_from(d: dict[str, Any]) -> PurchaseRecord:
    return PurchaseRecord(d["requestId"], date.fromisoformat(d["date"]), d["costCenter"], d["category"],
                          d["supplierTaxId"], Decimal(str(d["amount"])), d["status"])


class ErpData:
    """Dados sintéticos de mock-data/erp.json (servidos in-process ou pelo servidor MCP)."""

    def __init__(self) -> None:
        raw = json.loads(resources.path("mock-data", "erp.json").read_text(encoding="utf-8"))
        self.cost_centers: list[dict[str, Any]] = raw["costCenters"]
        self.suppliers: list[dict[str, Any]] = raw["suppliers"]
        self.history: list[dict[str, Any]] = raw["purchaseHistory"]

    def find_cost_center(self, cost_center_id: str) -> dict[str, Any] | None:
        return next((c for c in self.cost_centers if c["id"].upper() == cost_center_id.upper()), None)

    def find_supplier(self, tax_id: str) -> dict[str, Any] | None:
        return next((s for s in self.suppliers if s["taxId"] == tax_id), None)

    def history_since(self, cost_center_id: str, since: date) -> list[dict[str, Any]]:
        rows = [h for h in self.history
                if h["costCenter"].upper() == cost_center_id.upper() and date.fromisoformat(h["date"]) >= since]
        return sorted(rows, key=lambda h: h["date"], reverse=True)


class MockErpGateway:
    """ERP in-process. `unavailable=True` simula indisponibilidade para testes de fallback."""

    def __init__(self, data: ErpData | None = None) -> None:
        self._data = data or ErpData()
        self.unavailable = False

    def _check(self) -> None:
        if self.unavailable:
            raise ContextSourceUnavailable("ERP mock indisponível (simulado)")

    def cost_center_budget(self, cost_center_id: str) -> CostCenterBudget | None:
        self._check()
        found = self._data.find_cost_center(cost_center_id)
        return budget_from(found) if found else None

    def supplier_profile(self, tax_id: str) -> SupplierProfile | None:
        self._check()
        found = self._data.find_supplier(tax_id)
        return supplier_from(found) if found else None

    def purchase_history(self, cost_center_id: str, since: date) -> list[PurchaseRecord]:
        self._check()
        return [record_from(h) for h in self._data.history_since(cost_center_id, since)]
