#!/usr/bin/env bash
# Demo ponta a ponta contra a API rodando localmente.
# Uso: ./scripts/demo.sh [base_url]   (default http://localhost:8080)
set -euo pipefail

BASE="${1:-http://localhost:8080}"
KEY="${AGENT_API_KEY:-dev-key-change-me}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$DIR/examples/responses"
mkdir -p "$OUT"

post() { curl -s -X POST "$BASE$1" -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d "$2"; }

echo "== Cenários de avaliação =="
for f in "$DIR"/examples/*.json; do
  name="$(basename "$f" .json)"
  resp="$(post /v1/purchase-requests/evaluate "@$f")"
  echo "$resp" > "$OUT/$name.json"
  decision="$(echo "$resp" | grep -o '"decision":"[A-Z_]*"' | head -1)"
  decided_by="$(echo "$resp" | grep -o '"decidedBy":"[A-Z_]*"' | head -1)"
  printf '%-32s %-32s %s\n' "$name" "$decision" "$decided_by"
done

echo
echo "== Multi-turno: NEEDS_INFO -> complemento =="
case_id="$(grep -o '"caseId":"[^"]*"' "$OUT/03-needs-info.json" | cut -d'"' -f4)"
echo "caseId=$case_id"
post "/v1/cases/$case_id/messages" '{"message":"Fornecedor: Escritorio Total, homologado para mobiliario.","updates":{"supplier":{"taxId":"78124569000156","name":"Escritorio Total ME"}}}' \
  > "$OUT/03b-needs-info-followup.json"
grep -o "\"decision\":\"[A-Z_]*\"" "$OUT/03b-needs-info-followup.json" | head -1 || true

echo
echo "== Idempotência: reenviar a mesma solicitação não chama o LLM =="
curl -s -o /dev/null -D - -X POST "$BASE/v1/purchase-requests/evaluate" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d "@$DIR/examples/01-approve.json" | grep -i "x-idempotent-replay"

echo
echo "== Métricas (amostra) =="
curl -s "$BASE/metrics" | grep -E "^(agent_decisions_total|llm_cost_usd_total|agent_fallbacks_total|agent_guardrail_events_total)" | head -20
