#!/usr/bin/env bash
# Sobe o servidor MCP do ERP (porta 8001) e a API consumindo o ERP via MCP (porta 8080).
# Uso: ./scripts/run-with-mcp.sh   (Ctrl+C encerra os dois)
set -euo pipefail
PY="${PYTHON:-python}"
"$PY" -m purchase_agent.mcp_server.erp_server --port 8001 &
MCP_PID=$!
trap 'kill $MCP_PID 2>/dev/null || true' EXIT
sleep 2
ERP_SOURCE=mcp MCP_ERP_URL=http://127.0.0.1:8001/mcp "$PY" -m purchase_agent
