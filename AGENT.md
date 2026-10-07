# AGENT.md: Agente de Aprovação de Solicitações de Compra

> Documento formal do agente: papéis, contratos de entrada e saída, exemplos, limitações e estratégias de fallback.
> Versões das skills em vigor: ver `GET /v1/skills` e [CHANGELOG-SKILLS.md](CHANGELOG-SKILLS.md).

## 1. Propósito

Recomendar, de forma **explicável, auditável e conservadora**, se uma solicitação de compra deve ser aprovada, rejeitada, devolvida ao solicitante ou encaminhada a um humano. A saída é consumida por um ERP (campo `erpPayload`) e por aprovadores humanos (campos `summary` e `reasons`).

O agente **não executa** nenhuma ação em sistema externo. Todas as ferramentas que ele usa são somente leitura.

## 2. Papéis (multi-agente)

| Papel | Implementação | Modelo | Quando roda | Pode decidir? |
|---|---|---|---|---|
| **Intake** | `intake/normalizer.py` (código) | — | Sempre | Não: normaliza dados e sinaliza lacunas, ruído e injeção |
| **Motor de regras** | `policy/engine.py` (código) | — | Sempre | Sim, só para **REJECT obrigatório** (fornecedor bloqueado, orçamento estourado), sem chamar o LLM |
| **Context engineer** | `context/builder.py` (código) | — | Sempre que não houver rejeição obrigatória | Não: seleciona, prioriza e corta o contexto |
| **Analista** (executor) | skill `analyst` | Claude Sonnet 5.5 | Sempre que não houver rejeição obrigatória | Recomenda a decisão |
| **Reparo** | skill `repair` | Claude Sonnet 5.5 | Quando o validador rejeita a saída (máx. 1×) | Corrige a saída |
| **Validador** | `agent/validator.py` (código) | — | Após cada saída do analista | Rejeita saída inválida e sobrescreve violações de guardrail para `ESCALATE_TO_HUMAN` |
| **Compliance** (auditor) | skill `compliance-reviewer` | Claude Haiku 4.5 | `APPROVE` acima de R$ 10 mil ou com risco ≥ MEDIUM; `REJECT` vindo do modelo | Se discordar, a decisão vira `ESCALATE_TO_HUMAN` |
| **Sumarizador de caso** | skill `case-summarizer` | Claude Haiku 4.5 | A cada nova rodada de um caso `NEEDS_INFO` | Não |

**Por que o validador é código e não outro LLM:** ele é determinístico, barato e testável, e não tem como ser "convencido". O LLM revisor (compliance) fica restrito à segunda opinião onde o erro custa caro.

## 3. Contrato de entrada: `purchase-request.v1`

Schema: [`src/purchase_agent/resources/schemas/purchase-request.v1.json`](src/purchase_agent/resources/schemas/purchase-request.v1.json)

- **Obrigatórios:** `requestId`, `requester.costCenter`, `items[≥1]`. Se faltarem, a API responde `400 CONTRACT_VIOLATION` e o LLM não é chamado.
- **Todo o resto é tolerado como ausente** e vira `dataQuality.issues` e, quando relevante, `missingInformation`.
- **Limites:** payload ≤ 64 KB, ≤ 100 itens, justificativa ≤ 4.000 caracteres (truncada em 2.000 antes do LLM).

| Situação | Tratamento |
|---|---|
| Total declarado ≠ soma dos itens | Recalcula pelos itens e registra `TOTAL_MISMATCH_RECALCULATED` |
| Moeda ausente | Assume BRL e registra `CURRENCY_DEFAULTED` |
| CNPJ formatado / inválido | Normaliza para dígitos; DV inválido gera `SUPPLIER_TAXID_INVALID` e entra em `missingInformation` |
| Categoria/urgência despadronizada | Normaliza (`it hardware` → `IT_HARDWARE`, `urgente` → `HIGH`) |
| Texto com padrão de instrução | `SUSPICIOUS_INPUT` → regra `SEC-INPUT` bloqueia aprovação |
| Nome do solicitante | **Descartado** no intake (minimização de PII): só IDs seguem no pipeline |

## 4. Contrato de saída: `purchase-decision.v1`

Schema: [`src/purchase_agent/resources/schemas/purchase-decision.v1.json`](src/purchase_agent/resources/schemas/purchase-decision.v1.json). Toda resposta é validada contra ele antes de sair.

| Campo | Quem produz | Observação |
|---|---|---|
| `decision`, `riskLevel`, `riskScore`, `confidence`, `summary`, `reasons`, `missingInformation` | LLM (`llm-assessment.v1`), validado e possivelmente sobrescrito por guardrail | Enums fechados; `reasons[].evidenceIds` obrigatórios e existentes |
| `policyChecks` | Motor de regras | `source = RULE_ENGINE` |
| `evidence` | ContextBuilder | Exatamente o que o modelo recebeu |
| `dataQuality` | Intake | `score` = 1 − 0,1 × nº de issues |
| `erpPayload` | Código | `AUTO_APPROVE` só se `APPROVE` **e** alçada 0 (≤ R$ 10 mil) |
| `audit` | Código | traceId, versões de skills, modelo, tokens, custo, latência, `decidedBy`, `fallbackReason`, `guardrailEvents` |

### Semântica das decisões

| Decisão | Quando | `erpPayload.action` |
|---|---|---|
| `APPROVE` | Sem razão HIGH/CRITICAL, confiança ≥ 0,7, nenhuma regra bloqueando | `AUTO_APPROVE` (alçada 0) ou `ROUTE_FOR_APPROVAL` com a recomendação |
| `REJECT` | Violação clara de política sustentada por evidência | `REJECT` |
| `ESCALATE_TO_HUMAN` | Exige julgamento humano, regra bloqueia aprovação, fonte indisponível ou fallback | `ROUTE_FOR_APPROVAL` (alçada ≥ 1) |
| `NEEDS_INFO` | Falta dado que o solicitante pode fornecer | `RETURN_TO_REQUESTER` e abre um caso (`caseId`) |

## 5. Comportamento sem evidência suficiente (anti-alucinação por design)

1. **Instrução:** "use somente as evidências fornecidas; cite `evidenceIds`; sem evidência suficiente → `NEEDS_INFO` (dado faltante) ou `ESCALATE_TO_HUMAN` (julgamento)".
2. **Estrutura:** cada fato do contexto tem um ID (`EV-REQ`, `EV-BUD`, `EV-SUP`, `EV-HIST-*`, `EV-POL-*`, `EV-PT-*`, `EV-SRC-*`, `EV-CASE`), e o modelo responde em JSON Schema fechado (structured outputs).
3. **Verificação (código):** IDs citados precisam existir; todo valor `R$` citado precisa aparecer nas evidências (tolerância de 1%); `NEEDS_INFO` sem pendências é inválido.
4. **Reparo:** uma tentativa, com os erros do validador no prompt.
5. **Falha fechada:** se a saída continuar inválida, a resposta é `ESCALATE_TO_HUMAN` com `decidedBy=FALLBACK`, e as razões vêm das checagens de regra (continuam ancoradas em evidência).

## 6. Estratégias de fallback

| Falha | Detecção | Resposta | `decidedBy` / `fallbackReason` |
|---|---|---|---|
| Rate limit / 5xx / 529 / timeout / rede | `LlmException` retryable | Retry exponencial com jitter (3 tentativas); depois, fallback | `FALLBACK` / `LLM_RATE_LIMITED`, `LLM_OVERLOADED`… |
| Provedor fora do ar de forma sustentada | Circuit breaker (50% de falha em 20 chamadas) | Falha rápida sem chamar a API | `FALLBACK` / `LLM_CIRCUIT_OPEN` |
| Recusa do modelo (`stop_reason=refusal`) | `LlmException(REFUSAL)` | Fallback | `FALLBACK` / `LLM_REFUSAL` |
| Saída fora do schema ou sem grounding | `agent/validator.py` | Reparo 1×; depois, fallback | `FALLBACK` / `VALIDATION_FAILED` |
| `APPROVE` contra regra, com risco alto, com baixa confiança ou com entrada suspeita | Guardrail | Sobrescreve para `ESCALATE_TO_HUMAN` | `VALIDATOR_OVERRIDE` |
| Compliance discorda ou está indisponível | Revisor | `ESCALATE_TO_HUMAN` | `COMPLIANCE_OVERRIDE` |
| ERP ou cadastro indisponível | `context/facts.py` | Segue sem a fonte, registra `EV-SRC-*` e bloqueia aprovação | `AGENT` |
| Caso `NEEDS_INFO` sem solução após 3 rodadas | `api/service.py` | `ESCALATE_TO_HUMAN` | evento `CASE_ROUNDS_EXHAUSTED` |

## 7. Ferramentas e contexto

O contexto é **pré-buscado de forma determinística** (`ErpGateway`, somente leitura): orçamento do centro de custo, perfil do fornecedor e histórico de 12 meses. Optamos por não usar tool calling autônomo no MVP: o conjunto de dados necessário é conhecido, e a pré-busca dá latência e custo previsíveis e uma superfície de ataque menor (ver [ADR-0003](docs/adr/0003-contexto-pre-buscado-vs-tool-calling.md)). O ERP pode ser consumido **via MCP** (`ERP_SOURCE=mcp`): o servidor `erp-mcp` expõe as ferramentas e o `McpErpGateway` as chama ([ADR-0006](docs/adr/0006-mcp.md)).

Budget de tokens: [docs/context-and-tokens.md](docs/context-and-tokens.md).

## 8. Exemplos de uso

```bash
curl -s -X POST localhost:8080/v1/purchase-requests/evaluate \
  -H "X-API-Key: dev-key-change-me" -H "Content-Type: application/json" \
  -d @examples/01-approve.json
```

Exemplo de resposta (resumida, LLM fake):

```json
{
  "decision": "APPROVE", "riskLevel": "LOW", "riskScore": 12, "confidence": 0.88,
  "summary": "Compra dentro da política e coerente com o histórico.",
  "reasons": [{ "code": "WITHIN_POLICY", "severity": "LOW",
                "explanation": "Valor coerente com histórico, saldo e fornecedor homologado.",
                "evidenceIds": ["EV-BUD", "EV-SUP", "EV-HIST-STATS"] }],
  "erpPayload": { "action": "AUTO_APPROVE", "approvalLevel": 0, "costCenter": "CC-4410" },
  "audit": { "decidedBy": "AGENT", "skillVersions": { "purchase-policies": "1.0.0", "analyst-examples": "1.0.0", "analyst": "1.1.0" },
             "model": "fake:claude-sonnet-5-5", "llmCalls": 1,
             "tokens": { "input": 1456, "output": 99, "cacheRead": 1386 }, "estimatedCostUsd": 0.004179 }
}
```

Outros cenários estão em [`examples/`](examples/) e no [golden set](evals/golden/cases.json). Para o fluxo multi-turno: `POST /v1/cases/{caseId}/messages` com `{"message": "...", "updates": {...}}`.

## 9. Limitações conhecidas

- **A qualidade do julgamento depende do modelo e foi medida só com o LLM fake** até haver chave de API. O fake valida pipeline e guardrails, não o raciocínio do Claude. O eval real está pronto (`LLM_PROVIDER=anthropic pytest -m eval`).
- A heurística de prompt injection é baseada em padrões e não pega engenharia social sutil (por exemplo, o caso GS-026). Contra isso, a defesa é estrutural: regras fora do LLM, dados delimitados e validação da saída.
- A verificação de grounding cobre IDs e valores monetários, mas não verifica afirmações qualitativas ("fornecedor confiável"). Esse risco fica com o compliance e com o humano.
- Políticas em texto são filtradas por metadados (categoria e valor), sem busca semântica. Funciona para dezenas de políticas; para centenas, seria preciso usar RAG.
- O ERP é um mock com dados sintéticos e "hoje" fixo em 2026-10-06 (configurável em `AGENT_FIXED_DATE`).
- A estimativa local de tokens (3,5 caracteres/token) é aproximada; o valor real vem do `usage` da API e é exportado em métrica.
