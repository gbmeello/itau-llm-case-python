#!/usr/bin/env bash
# Demonstra o CRUD versionado de skills: criar, atualizar (nova versão), rollback, remover, e rastreabilidade.
set -euo pipefail

BASE="${1:-http://localhost:8080}"
KEY="${AGENT_API_KEY:-dev-key-change-me}"
H=(-H "X-API-Key: $KEY" -H "Content-Type: application/json" -H "X-User: demo")
DIR="$(cd "$(dirname "$0")/.." && pwd)"

step() { echo; echo "== $* =="; }

step "1. Estado inicial"
curl -s "$BASE/v1/skills" "${H[@]}" | grep -o '"skillId":"[^"]*","type":"[^"]*","version":"[^"]*","status":"[^"]*"'

step "2. CREATE: nova skill de exemplos"
curl -s -X POST "$BASE/v1/skills" "${H[@]}" \
  -d '{"skillId":"demo-examples","type":"EXAMPLES","version":"1.0.0","content":"Exemplo de demonstracao","changelog":"criacao via API"}'

step "3. UPDATE: analyst 1.2.0 (nova versão imutável, ativada)"
# Payload montado em arquivo (UTF-8) com perl/JSON::PP: evita problemas de escape e de encoding do argv.
TMP="$(mktemp)"
perl -MJSON::PP -e 'local $/; open my $f, "<:raw", $ARGV[0];
  print JSON::PP->new->encode({version => "1.2.0", content => scalar <$f>,
    changelog => "demo: nova versao via API", activate => JSON::PP::true})' \
  "$DIR/src/purchase_agent/resources/skills/prompts/analyst/1.1.0.md" > "$TMP"
curl -s -X POST "$BASE/v1/skills/analyst/versions" "${H[@]}" --data-binary "@$TMP"
rm -f "$TMP"

step "4. Decisão usa analyst@1.2.0"
TMP="$(mktemp)"
sed "s/PR-DEMO-001/PR-DEMO-SKILL-$RANDOM/" "$DIR/examples/01-approve.json" > "$TMP"
curl -s -X POST "$BASE/v1/purchase-requests/evaluate" "${H[@]}" --data-binary "@$TMP" | grep -o '"skillVersions":{[^}]*}'
rm -f "$TMP"

step "5. ROLLBACK para analyst@1.1.0"
curl -s -X POST "$BASE/v1/skills/analyst/versions/1.1.0/activate" "${H[@]}"

step "6. DELETE lógico de skill não essencial e tentativa de remover skill essencial (409)"
curl -s -o /dev/null -w "demo-examples -> %{http_code}\n" -X DELETE "$BASE/v1/skills/demo-examples" "${H[@]}"
curl -s -o /dev/null -w "analyst -> %{http_code}\n" -X DELETE "$BASE/v1/skills/analyst" "${H[@]}"

step "7. Histórico do analyst"
curl -s "$BASE/v1/skills/analyst" "${H[@]}" | grep -o '"version":"[^"]*","status":"[^"]*"'
