# Confiabilidade e observabilidade

## 1. Três perguntas que a observabilidade precisa responder

1. **O agente está saudável?** (latência, erro, fallback, custo)
2. **O modelo está se comportando como esperado?** (grounding, overrides, distribuição de decisões)
3. **Por que esta decisão específica foi tomada?** (auditoria por `decisionId` / `traceId`)

## 2. Métricas (prometheus-client → `/metrics`)

| Métrica | Tags | Para quê |
|---|---|---|
| `llm_request_latency_seconds` (p50/p95/p99) | purpose, model, outcome | SLO de latência por papel (analista, reparo, compliance) |
| `llm_tokens_total` | purpose, model, direction (input/output/cache_read) | Consumo e taxa de cache hit |
| `llm_cost_usd_total` | purpose, model | Custo acumulado por papel e modelo |
| `llm_errors_total` | purpose, type (RATE_LIMITED, OVERLOADED, TIMEOUT, REFUSAL, CIRCUIT_OPEN…) | Saúde do provedor |
| `llm_retries_total` | type | Pressão de rate limit |
| `agent_decisions_total` | decision, decided_by, risk_level | Distribuição de decisões (detecção de drift) |
| `agent_decision_latency_seconds` | decided_by | Latência ponta a ponta |
| `agent_decision_cost_usd` | — | Custo por decisão (FinOps) |
| `agent_fallbacks_total` | reason | Quanto o sistema está decidindo sem o LLM |
| `agent_guardrail_events_total` | event (APPROVE_BLOCKED_BY_POLICY, APPROVE_WITH_HIGH_RISK, REPAIR_ATTEMPT_1, COMPLIANCE_DISAGREED…) | Comportamento inesperado do modelo |
| `agent_validation_failures_total`, `agent_grounding_errors_total` | stage | Alucinação e saída fora do contrato |
| `agent_compliance_disagreements_total` | — | Divergência entre analista e auditor |
| `context_tokens_by_layer`, `context_tokens_by_layer` | layer | Calibração do budget |
| `context_truncations_total` | — | Contexto cortado (risco de decisão com informação faltante) |
| `api_rate_limited_total` | route | Consumidor batendo no limite (abuso, loop ou capacidade) |
| `agent_tool_calls_total` | tool, outcome | Uso de ferramentas pelo analista (tool calling) e taxa de erro |

## 3. Logs estruturados

- **JSON com campos no estilo ECS** (`observability/logging.py`; `LOG_FORMAT=text` para leitura humana), com `trace.id` vindo de um contextvar, propagado do header `X-Trace-Id` ou gerado.
- Uma linha de log por decisão (`message: decision`, campo `event`) com requestId, decisionId, decision, risk, decidedBy, llmCalls, tokens, custo, latência, tokens de contexto, IDs excluídos, eventos de guardrail e versões de skills.
- **Não se loga** prompt completo nem justificativa (podem conter PII ou dados sensíveis). O conteúdo exato que o modelo viu é reconstituível pela auditoria (`evidence` + versões das skills).

## 4. Auditoria

`GET /v1/decisions/{decisionId}` devolve a decisão completa, os IDs de contexto incluídos e excluídos, as versões de skills, os tokens, o custo, a latência e o traceId. O `DecisionRecord` é imutável e é gravado em toda decisão, inclusive em fallbacks.

## 5. Alertas

Regras prontas em [`deploy/prometheus/alerts.yml`](../deploy/prometheus/alerts.yml), carregadas pelo Prometheus do `docker-compose` e validadas no CI com `promtool check rules`. Cada alerta tem `severity` e `runbook`.

| Alerta | Condição | Severidade | Ação |
|---|---|---|---|
| Fallback alto | `agent_fallbacks_total` > 5% das decisões em 15 min | Alta | Verificar provedor/circuit breaker; aprovadores humanos recebem mais volume |
| Grounding | `agent_grounding_errors_total` após reparo > 0 em 1 h | Alta | Possível regressão de prompt/modelo: congelar ativação de skills e rodar eval |
| Overrides de guardrail | `APPROVE_BLOCKED_BY_POLICY` > 1% | Média | O modelo está tentando aprovar contra a regra: revisar prompt |
| Drift de decisões | % de APPROVE varia ±15 pp vs. média de 7 dias | Média | Mudança de dados, de prompt ou de modelo |
| Custo | `llm_cost_usd_total` por hora > orçamento | Média | Verificar loops de reparo, cache e volume |
| Latência | p95 `agent_decision_latency_seconds` > 15 s | Média | Provedor lento: avaliar effort e timeout |
| Circuit aberto | `llm_errors_total{type="CIRCUIT_OPEN"}` > 0 | Alta | Provedor fora do ar |
| Compliance divergente | `agent_compliance_disagreements_total` > 10% dos revisados | Baixa | Calibração entre analista e revisor |

## 6. Comportamento sem evidência suficiente

Quando o modelo não tem evidência, o comportamento esperado é `NEEDS_INFO` (dado faltante) ou `ESCALATE_TO_HUMAN` (julgamento). Isso é garantido em três níveis: instrução no prompt, verificação de grounding em código e falha fechada (`FALLBACK` → `ESCALATE`). Fontes indisponíveis aparecem como `EV-SRC-*` e bloqueiam a aprovação automática. Ver [AGENT.md §5–6](../AGENT.md).

## 7. Traces

Hoje o `traceId` é propagado em header, contextvar, log e auditoria. A evolução natural é o OpenTelemetry (SDK Python + instrumentação FastAPI) e spans por estágio (`intake`, `facts`, `policy`, `context`, `llm.analyst`, `validate`, `llm.compliance`), com o atributo `gen_ai.*` (convenções semânticas OTel para GenAI) nos spans de LLM.
