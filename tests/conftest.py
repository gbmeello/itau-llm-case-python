from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from purchase_agent.api.app import create_app
from purchase_agent.config import Settings
from purchase_agent.contract.models import Item, PurchaseRequest, Requester, Supplier

TODAY = date(2026, 10, 6)  # coerente com os dados sintéticos do ERP
ACME = "11222333000181"
BLOCKED = "33445566000186"
RAPIDEZ = "99887755000117"
API_KEY = "test-key"


def request(cost_center: str = "CC-4410", supplier: str | None = ACME, category: str | None = "IT_HARDWARE",
            qty: int = 1, unit_price: str | None = "100", justification: str | None =
            "Substituição de equipamentos com garantia vencida, padrão homologado.") -> PurchaseRequest:
    price = Decimal(unit_price) if unit_price is not None else None
    return PurchaseRequest(
        requestId="PR-T-1",
        requester=Requester(employeeId="E-1", name="Fulano de Tal", department="TI", costCenter=cost_center),
        supplier=Supplier(taxId=supplier, name="Fornecedor") if supplier else None,
        items=[Item(sku="SKU-1", description="Item de teste", category=category, quantity=qty, unitPrice=price)],
        totalAmount=price * qty if price is not None else None, currency="BRL", justification=justification,
        neededBy="2026-11-01", urgency="NORMAL")


def body(request_id: str | None = None, supplier: str | None = ACME, qty: int = 2, price: float | None = 1950.0,
         justification: str | None = "Monitores para novos analistas, padrão homologado.") -> dict:
    payload: dict = {
        "requestId": request_id or f"PR-IT-{uuid.uuid4().hex[:8]}",
        "requester": {"employeeId": "E-1042", "name": "Ana Souza", "department": "TI", "costCenter": "CC-4410"},
        "items": [{"sku": "MON-27", "description": "Monitor 27", "category": "IT_HARDWARE", "quantity": qty,
                   "unitPrice": price}],
        "currency": "BRL", "justification": justification, "urgency": "NORMAL",
    }
    if supplier:
        payload["supplier"] = {"taxId": supplier, "name": "X"}
    return payload


@pytest.fixture
def settings() -> Settings:
    return Settings(api_key=API_KEY, llm_initial_backoff_seconds=0.001)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}
