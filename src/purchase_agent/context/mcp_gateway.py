"""ErpGateway sobre MCP: o agente consome o ERP como ferramentas de um servidor MCP.

Trade-off (ver docs/adr/0006-mcp.md): padronização e reuso entre agentes, ao custo de um hop de rede e de mais
um componente para operar. Resultados de ferramenta são tratados como dados (validados aqui, nunca executados).
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

from mcp import Client

from purchase_agent.context.erp import (
    ContextSourceUnavailable, CostCenterBudget, PurchaseRecord, SupplierProfile, budget_from, record_from,
    supplier_from,
)


class McpErpGateway:
    """`target`: URL Streamable HTTP (produção) ou instância do MCPServer (testes, in-process)."""

    def __init__(self, target: Any, timeout_seconds: float = 5.0) -> None:
        self._target = target
        self._timeout = timeout_seconds

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async def run() -> dict[str, Any]:
            async with Client(self._target, read_timeout_seconds=self._timeout) as client:
                result = await client.call_tool(tool, arguments)
            if getattr(result, "is_error", False) or getattr(result, "isError", False):
                raise ContextSourceUnavailable(f"MCP tool {tool} retornou erro")
            structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
            if structured is not None:
                return structured
            text = "".join(getattr(c, "text", "") for c in result.content)
            return json.loads(text)

        try:
            return asyncio.run(asyncio.wait_for(run(), self._timeout))
        except ContextSourceUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 - rede, timeout ou protocolo: fonte indisponível
            raise ContextSourceUnavailable(f"MCP {tool} indisponível: {e}") from e

    def cost_center_budget(self, cost_center_id: str) -> CostCenterBudget | None:
        r = self._call("get_cost_center_budget", {"cost_center_id": cost_center_id})
        return budget_from(r["costCenter"]) if r.get("found") else None

    def supplier_profile(self, tax_id: str) -> SupplierProfile | None:
        r = self._call("get_supplier_profile", {"tax_id": tax_id})
        return supplier_from(r["supplier"]) if r.get("found") else None

    def purchase_history(self, cost_center_id: str, since: date) -> list[PurchaseRecord]:
        r = self._call("get_purchase_history", {"cost_center_id": cost_center_id, "since": since.isoformat()})
        return [record_from(x) for x in r.get("records", [])]
