"""Servidor MCP que expõe o ERP (sintético) como ferramentas somente leitura.

Rodar (Streamable HTTP):  python -m purchase_agent.mcp_server.erp_server --port 8001
Rodar (stdio):            python -m purchase_agent.mcp_server.erp_server --stdio

Qualquer agente compatível com MCP (inclusive o Claude Desktop/Code) pode consumir estas ferramentas.
O agente de aprovação as consome via McpErpGateway quando ERP_SOURCE=mcp.
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any

from mcp.server.mcpserver import MCPServer

from purchase_agent.context.erp import ErpData

_data = ErpData()

server = MCPServer(
    name="erp-mcp",
    instructions="ERP corporativo (somente leitura): orçamento por centro de custo, cadastro de fornecedores e "
                 "histórico de compras. Dados sintéticos para o case.",
)


@server.tool(description="Orçamento anual, valor comprometido e disponível de um centro de custo.")
def get_cost_center_budget(cost_center_id: str) -> dict[str, Any]:
    found = _data.find_cost_center(cost_center_id)
    return {"found": found is not None, "costCenter": found}


@server.tool(description="Cadastro do fornecedor por CNPJ (14 dígitos): status, rating de risco, categorias.")
def get_supplier_profile(tax_id: str) -> dict[str, Any]:
    found = _data.find_supplier(tax_id)
    return {"found": found is not None, "supplier": found}


@server.tool(description="Compras do centro de custo a partir de uma data (ISO 8601), mais recentes primeiro.")
def get_purchase_history(cost_center_id: str, since: str) -> dict[str, Any]:
    return {"records": _data.history_since(cost_center_id, date.fromisoformat(since))}


def main() -> None:
    parser = argparse.ArgumentParser(description="ERP MCP server")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.stdio:
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port, json_response=True, stateless_http=True)


if __name__ == "__main__":
    main()
