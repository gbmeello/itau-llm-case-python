# Spec: Agente de Aprovação de Solicitações de Compra (Purchase Approval Agent)

> Case técnico Itaú: Engenharia de Prompt, Contexto e Agentes (LLM Engineering). Cenário base: **03, Aprovação de solicitações de compra**.
> Status: **APROVADA em 2026-10-06** · Processo: `agent-skills` (spec → plan → build → test → review → ship)
>
> **Desvios registrados durante a implementação** (com motivo):
> 1. Saída via **structured outputs** (`output_config.format`) e não via tool use forçado: o Sonnet 5.5 rejeita `tool_choice` `any`/`tool` e `temperature` não-default (§6). Ver ADR-0002.
> 2. ID do Haiku: `claude-haiku-4-5` (sem sufixo de data).
> 3. O **LLM gera só o julgamento** (`llm-assessment.v1`); o código monta o restante do contrato (§4.2).
> 4. Os fatos do ERP são buscados **antes** do motor de regras (as regras de orçamento e fornecedor dependem deles); o diagrama da §3 foi simplificado.
> 5. Histórico sumarizado **em código** (estatísticas + top-5), não pelo Haiku: determinístico e sem custo. O Haiku fica com a sumarização do caso multi-turno.
> 6. Tool calling do analista (§5.3): **opcional** (`AGENT_TOOL_CALLING=true`, implementado na versão Python); o caminho padrão continua sendo a pré-busca (ADR-0003).
> 7. MCP (§9) **implementado nesta versão Python**: servidor `erp-mcp` + `McpErpGateway` (ADR-0006). Na versão Java ficou só a interface.
> 8. Pacote único `purchase_agent` com o servidor MCP como subpacote.
> 9. Traces via `traceId` (header/contextvar/auditoria); OpenTelemetry fica como evolução.
> 10. **Stack Python** (FastAPI, Pydantic, SDK anthropic 1.x) em vez de Java/Spring: ver ADR-0008. O SDK 1.x removeu `temperature`, alinhado à decisão 1.
> 11. Lacunas da spec fechadas depois da revisão de 07/10: mascaramento de PII no texto livre (§12), rate limit por cliente (§12), Docker/compose com PostgreSQL + MCP + Prometheus e regras de alerta executáveis (§11).

---

## 0. Premissas (corrija agora ou sigo com elas)

1. **O LLM é um componente controlado, não o dono da decisão.** Regras de política determinísticas (alçadas, fornecedor bloqueado, orçamento) rodam *antes* do LLM, e o LLM **nunca** pode reverter um bloqueio rígido. O LLM classifica o risco, cruza o contexto e explica.
2. **Os sistemas externos (ERP, orçamento, cadastro de fornecedores) são mocks** com dados sintéticos (sem dados reais nem PII real).
3. **O MVP é uma API REST síncrona** (um request → uma decisão). Fila/assíncrono fica documentado como evolução.
4. **Testes e CI nunca chamam a API real.** Um `llm/fake.py` determinístico (respostas gravadas por caso) permite rodar tudo sem chave. A API real só é usada em demo/eval manual.
5. **Modelos:** `claude-sonnet-5-5` como analista (executor) e `claude-haiku-4-5-20251001` para sumarização e revisão de compliance (mais barato). São configuráveis por propriedade.
6. **Idioma:** domínio, prompts e documentação em português; código (identificadores) em inglês.
7. **"Skills" no contexto do case** = artefatos de IA versionados (prompts, políticas, definições de tools), com CRUD via API e histórico de versões, e não as skills do repositório `agent-skills`.

---

## 1. Objetivo

Construir um serviço backend que recebe **solicitações de compra estruturadas** (possivelmente incompletas, ambíguas ou ruidosas), enriquece com **contexto** (orçamento do centro de custo, histórico de compras, cadastro/risco do fornecedor, políticas internas) e usa um **LLM de forma engenheirada** para produzir uma **decisão estruturada, explicável e auditável**:

`APPROVE | REJECT | ESCALATE_TO_HUMAN | NEEDS_INFO` + nível de risco + justificativas **ancoradas em evidências** + payload pronto para o ERP.

**Usuários:**
- *Sistema consumidor* (ERP / portal de compras): chama a API e consome o contrato JSON.
- *Aprovador humano*: lê a explicação nos casos `ESCALATE_TO_HUMAN`.
- *Auditoria/Compliance*: consulta o registro de decisão (o que entrou no contexto, qual versão de prompt/modelo, custo).
- *Avaliador do case*: roda localmente, executa os evals e acompanha o raciocínio na documentação.

### Requisitos do case → onde são atendidos

| Requisito do PDF | Como atendemos | Seção |
|---|---|---|
| Entradas estruturadas | `PurchaseRequest` (JSON Schema + Bean Validation) | 4.1 |
| LLM controlado | Pipeline em estágios, LLM só no estágio de análise, com guardrails antes e depois | 3 |
| Saída com contrato explícito | `PurchaseDecision` JSON Schema versionado, validado em runtime | 4.2 |
| Decisões explicáveis | Cada `reason` cita `evidenceIds` existentes no contexto (grounding check) | 5.4 |
| Resiliência a entradas ruins | Normalização, `dataQuality` flags, `NEEDS_INFO`, fallback para humano | 5.1, 5.5 |
| Engenharia de prompt | Prompts versionados em arquivo, papéis definidos, dados não confiáveis delimitados | 6 |
| Engenharia de contexto | Seleção por relevância + prioridade em camadas; o que entra e o que fica de fora | 7 |
| Token budget | Orçamento por camada, truncamento, sumarização incremental | 7.3 |
| Anti-alucinação | Regra de evidência obrigatória + `NEEDS_INFO`/`ESCALATE` quando falta evidência | 5.4 |
| CRUD de skills + versionamento | Registry de artefatos de IA com versões imutáveis + CHANGELOG | 8 |
| AGENT.md | Documento formal do agente | 8.3 |
| MCP (diferencial) | MCP server "ERP mock" + app como MCP client, com trade-off documentado | 9 |
| Multi-agent / tool calling (diferencial) | Analista (executor) → Validador (determinístico) → Compliance (LLM) + retry | 3 |
| Evals | Golden set + runner + critérios objetivos + gate de regressão | 10 |
| Observabilidade | Traces, métricas (latência, tokens, custo, erro, fallback), logs estruturados | 11 |
| Uso de IA explícito | `docs/AI-USAGE.md`, registro de onde e como a IA foi usada | 13 |

---

## 2. Mapa de capacidades (Phase 0)

A solicitação agrupa várias capacidades testáveis de forma independente. Proponho **um único serviço (monólito modular)** com estes módulos (pacotes), construídos nesta ordem:

| Módulo | Responsabilidade | Depende de |
|---|---|---|
| `contract` | DTOs + JSON Schemas de entrada/saída, validação | — |
| `intake` | Normalização, detecção de lacunas/ruído, `dataQuality` | contract |
| `policy` | Motor de regras determinístico (alçadas, bloqueios, orçamento) | contract |
| `context` | Provedores de contexto (ERP/fornecedor/histórico/políticas), ranking, token budget, sumarização | contract, policy |
| `llm` | Abstração do cliente LLM (Anthropic + Fake), retry, timeout, medição de tokens/custo | — |
| `agent` | Orquestração: analista → validador → compliance, fallback | intake, policy, context, llm |
| `registry` | CRUD/versionamento de skills (prompts, políticas, tools) | contract |
| `audit` | Registro de decisão persistido + endpoint de consulta | agent |
| `observability` | Métricas, traces, logs estruturados | transversal |
| `mcp` *(diferencial)* | MCP server ERP-mock + adapter MCP client para `context` | context |
| `evals` | Golden set, runner, relatório, gate de regressão | agent |

Ordem de construção: `contract` → `intake`, `policy`, `llm` → `context` → `agent` → `audit`, `registry` → `evals` → `observability` (refino) → `mcp`.

> Para não fragmentar demais um case de 7 dias, mantenho **um único SPEC.md** com uma seção por módulo, em vez de um `SPEC-<módulo>.md` por módulo.

---

## 3. Arquitetura

```
 POST /v1/purchase-requests/evaluate
            │
            ▼
 ┌──────────────────┐   inválido/irrecuperável → 400 com erros de contrato
 │ 1. Intake        │── normaliza (moeda, datas, CNPJ, totais), marca lacunas → dataQuality
 └────────┬─────────┘
          ▼
 ┌──────────────────┐   bloqueio rígido (fornecedor bloqueado, sem orçamento, acima da alçada máx.)
 │ 2. Policy Engine │── → REJECT/ESCALATE determinístico, LLM só redige a explicação (ou template)
 │  (determinístico)│
 └────────┬─────────┘
          ▼
 ┌──────────────────┐   ContextProvider (in-process | MCP client)
 │ 3. Context       │── busca → ranqueia → aplica token budget → sumariza histórico
 │    Builder       │   cada item recebe um evidenceId (EV-001...)
 └────────┬─────────┘
          ▼
 ┌──────────────────┐   Claude Sonnet, saída via tool/JSON schema
 │ 4. Analyst Agent │── tool calling opcional (máx. 3 rodadas), retry com backoff
 └────────┬─────────┘
          ▼
 ┌──────────────────┐   schema válido? evidências existem? números batem?
 │ 5. Validator     │── não respeita as regras rígidas? → 1 tentativa de reparo → senão FALLBACK
 │  (determinístico)│
 └────────┬─────────┘
          ▼
 ┌──────────────────┐   só se risco ≥ HIGH ou valor ≥ limiar ou decisão = APPROVE acima de X
 │ 6. Compliance    │── Claude Haiku revisa: concorda / discorda → discordância = ESCALATE
 │    Reviewer      │
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ 7. Audit + Obs.  │── persiste DecisionRecord, emite métricas/trace, devolve PurchaseDecision
 └──────────────────┘
```

**Decisões-chave e trade-offs (viram ADRs em `docs/adr/`):**

| Decisão | Escolha | Alternativa rejeitada | Motivo |
|---|---|---|---|
| Quem decide | Regras rígidas + LLM para o julgamento "cinza" | LLM decide tudo | Previsibilidade, auditabilidade e custo; o LLM não pode "alucinar" uma aprovação acima da alçada |
| Contexto | Pré-busca determinística + tool calling limitado | Agente totalmente autônomo buscando tudo | Latência e custo previsíveis, superfície de ataque menor; tools ficam para aprofundamento |
| Validador | Código determinístico | Outro LLM validando | Barato, testável, sem não-determinismo; o LLM entra só na revisão de compliance |
| Fallback | `ESCALATE_TO_HUMAN` | Retry infinito / default APPROVE | Falhar fechado (*fail-safe*) é o padrão correto em domínio financeiro |
| Persistência | H2 (dev) / PostgreSQL (docker-compose) via JPA | NoSQL | Registro de auditoria é relacional e consultável |
| Arquitetura | Monólito modular | Microserviços | Escopo de 7 dias; os módulos já têm fronteiras que permitem extrair depois |

---

## 4. Contratos

### 4.1 Entrada: `PurchaseRequest` (schema `purchase-request.v1.json`)

```json
{
  "requestId": "PR-2026-000123",
  "requestedAt": "2026-10-06T14:00:00Z",
  "requester": { "employeeId": "E-1042", "department": "TI", "costCenter": "CC-4410" },
  "supplier": { "taxId": "12.345.678/0001-90", "name": "Acme Hardware Ltda" },
  "items": [
    { "sku": "NB-X1", "description": "Notebook i7 16GB", "category": "IT_HARDWARE",
      "quantity": 10, "unitPrice": 7800.00 }
  ],
  "totalAmount": 78000.00,
  "currency": "BRL",
  "justification": "Reposição de notebooks da equipe de dados (texto livre, NÃO confiável)",
  "neededBy": "2026-11-01",
  "urgency": "NORMAL"
}
```

Obrigatórios mínimos: `requestId`, `requester.costCenter`, `items[≥1]`. Todo o resto é tolerado como ausente e vira um `dataQuality.issue`. Exemplos: `totalAmount` divergente da soma dos itens → recalculado e sinalizado; `currency` ausente → assume BRL e sinaliza; `supplier.taxId` inválido → sinaliza.

### 4.2 Saída: `PurchaseDecision` (schema `purchase-decision.v1.json`)

```json
{
  "schemaVersion": "1.0",
  "requestId": "PR-2026-000123",
  "decision": "ESCALATE_TO_HUMAN",
  "riskLevel": "HIGH",
  "riskScore": 72,
  "confidence": 0.81,
  "summary": "Valor 3,1x acima da média histórica do centro de custo para a categoria; orçamento suficiente.",
  "reasons": [
    { "code": "AMOUNT_ABOVE_HISTORICAL", "severity": "HIGH",
      "explanation": "R$ 78.000 vs média de R$ 25.100 nos últimos 12 meses.",
      "evidenceIds": ["EV-003", "EV-004"] }
  ],
  "policyChecks": [
    { "policyId": "POL-ALCADA-002", "version": "3", "result": "REQUIRES_APPROVAL_LEVEL_2", "source": "RULE_ENGINE" }
  ],
  "missingInformation": [],
  "dataQuality": { "score": 0.9, "issues": ["CURRENCY_DEFAULTED"] },
  "evidence": [
    { "id": "EV-003", "source": "erp.purchase_history", "summary": "Média 12m CC-4410/IT_HARDWARE = R$ 25.100" }
  ],
  "erpPayload": { "action": "ROUTE_FOR_APPROVAL", "approvalLevel": 2, "costCenter": "CC-4410" },
  "audit": {
    "traceId": "…", "decisionId": "…", "decidedBy": "AGENT|RULE_ENGINE|FALLBACK",
    "promptVersions": { "analyst": "analyst@1.2.0" }, "model": "claude-sonnet-5-5",
    "tokens": { "input": 4120, "output": 610 }, "estimatedCostUsd": 0.021, "latencyMs": 3800,
    "fallbackReason": null
  }
}
```

Regras do contrato:
- Enum fechado em `decision`, `riskLevel`, `reason.code` (catálogo versionado) e `severity`.
- `APPROVE` exige `reasons` vazio de severidade HIGH/CRITICAL e `confidence ≥ 0.7` (limiar configurável); caso contrário, o validador rebaixa para `ESCALATE_TO_HUMAN`.
- Toda `reason` precisa de ≥1 `evidenceId` existente no `evidence` do contexto enviado.
- A API sempre devolve um `PurchaseDecision` válido (HTTP 200) quando a entrada passa no contrato mínimo, inclusive em falha do LLM (`decidedBy=FALLBACK`).

---

## 5. Comportamento do agente

### 5.1 Entradas incompletas, ambíguas ou ruidosas
| Situação | Comportamento |
|---|---|
| Campo obrigatório mínimo ausente | HTTP 400 com erros de contrato (não chama LLM) |
| Campo importante ausente (fornecedor, justificativa, preço) | Segue; `missingInformation[]`; decisão tende a `NEEDS_INFO` |
| Inconsistência numérica | Recalcula, registra `dataQuality.issue`, o LLM recebe o valor normalizado + flag |
| Texto livre ruidoso / outro idioma | Passa ao LLM como dado delimitado; não altera regras |
| Tentativa de prompt injection em `justification` | Tratado como dado (delimitado + instrução explícita); heurística sinaliza `SUSPICIOUS_INPUT`, o que força `ESCALATE` |

### 5.2 Múltiplas interações (coerência)
`NEEDS_INFO` abre um **caso** (`caseId`). O solicitante complementa via `POST /v1/cases/{caseId}/messages`. A reavaliação **não** reenvia o histórico bruto: envia o **estado do caso** (decisão anterior + lista de pendências + resumo incremental gerado pelo Haiku, limitado a ~300 tokens) + os dados novos. Máximo de 3 rodadas, e depois disso `ESCALATE_TO_HUMAN`.

### 5.3 Tool calling (analista)
Tools somente leitura: `get_supplier_profile`, `get_purchase_history(costCenter, category, months)`, `get_policy(policyId)`. Limites: máx. 3 rodadas de tool call, timeout por tool de 2s, 2 retries com backoff exponencial em erro transitório. Resultado de tool vira evidência (`EV-T01…`), com o mesmo grounding.

### 5.4 Anti-alucinação por design
1. Instrução: "Use **somente** evidências fornecidas; cite `evidenceIds`; se não houver evidência suficiente, retorne `NEEDS_INFO` ou `ESCALATE_TO_HUMAN` com `missingInformation`".
2. O validador rejeita `evidenceIds` inexistentes, valores monetários citados que não aparecem nas evidências (tolerância de arredondamento) e políticas não fornecidas no contexto.
3. Falha de validação → 1 tentativa de reparo (com os erros do validador no prompt) → se falhar de novo, `FALLBACK` (`ESCALATE_TO_HUMAN`, `decidedBy=FALLBACK`).
4. Métrica `agent.grounding.failures` com alerta.

### 5.5 Fallbacks
| Falha | Resposta |
|---|---|
| API LLM indisponível / timeout (após retries) | Decisão só com regras: rígidas → REJECT; senão `ESCALATE_TO_HUMAN` |
| Saída fora do schema 2x | `ESCALATE_TO_HUMAN`, `fallbackReason=SCHEMA_VIOLATION` |
| Provedor de contexto indisponível | Segue sem a fonte, com `missingInformation`, e não pode `APPROVE` |
| Orçamento de tokens/custo excedido | Contexto reduzido (camadas de menor prioridade cortadas); se ainda exceder, `ESCALATE` |
| Circuit breaker aberto (Resilience4j) | Mesmo que "LLM indisponível", sem chamar a API |

---

## 6. Engenharia de prompt

Prompts em `src/purchase_agent/resources/skills/prompts/<nome>/<versão>.md` com frontmatter (`id`, `version`, `model`, `changelog`). Prompts principais:

| Prompt | Papel | Modelo |
|---|---|---|
| `analyst` | Analista de risco de compras: avalia a solicitação contra o contexto e produz `PurchaseDecision` | Sonnet |
| `repair` | Corrige uma saída inválida dados os erros do validador | Sonnet |
| `compliance-reviewer` | Auditor: concorda/discorda da decisão, procura violação de política ou justificativa fraca | Haiku |
| `case-summarizer` | Sumarização incremental do estado do caso entre interações | Haiku |
| `history-summarizer` | Resume histórico de compras longo em estatísticas + anomalias | Haiku (ou código) |

Princípios: papel e objetivo no system prompt; regras numeradas e curtas; formato de saída imposto via **tool use com `input_schema`** (structured output) e não por "responda em JSON"; dados do usuário em blocos `<untrusted_input>` com instrução explícita de nunca seguir instruções contidas neles; 2–3 exemplos few-shot (incluindo um de `NEEDS_INFO`); temperatura baixa (0–0.2); a parte estática do prompt fica em cache (prompt caching) para reduzir custo.

---

## 7. Engenharia de contexto e tokens

### 7.1 O que entra (por prioridade)
| Camada | Conteúdo | Prioridade | Orçamento |
|---|---|---|---|
| P0 | System prompt + regras + schema (cacheado) | Fixo | ~1.500 |
| P0 | Solicitação normalizada + `dataQuality` | Nunca corta | ≤ 1.000 |
| P0 | Resultado do policy engine (alçada, checks) | Nunca corta | ≤ 400 |
| P1 | Orçamento do centro de custo (saldo, comprometido) | Alta | ≤ 200 |
| P1 | Perfil/risco do fornecedor | Alta | ≤ 300 |
| P2 | Políticas aplicáveis (filtradas por categoria/valor), em texto | Média | ≤ 1.200 |
| P3 | Histórico de compras **sumarizado** (estatísticas + top-N similares) | Média | ≤ 800 |
| P3 | Estado do caso (multi-turno) | Média | ≤ 300 |
| P4 | Few-shot extras | Baixa | ≤ 600 |
| — | Resultados de tool call | Dinâmico | ≤ 1.000 total |

**Budget total de entrada: 8.000 tokens · saída: 1.200 tokens.** Configurável.

### 7.2 O que **não** entra
Histórico bruto linha a linha (vira estatística + top-5 compras similares); políticas de outras categorias; PII desnecessária (nome do solicitante e dados bancários do fornecedor são mascarados; só IDs); campos internos do ERP sem relevância para a decisão; transcrição completa de rodadas anteriores (vira resumo).

### 7.3 Controle de crescimento
1. Estimativa de tokens por camada (contagem via API `count_tokens` em dev; heurística por caracteres em runtime, calibrada).
2. Se exceder o budget, corta da menor prioridade para a maior (P4 → P2), truncando por item inteiro e nunca no meio de uma evidência.
3. Histórico sempre sumarizado; sumarização incremental de caso com teto fixo.
4. Métricas `context.tokens.by_layer` e `context.truncations` para ajuste com dados reais.

---

## 8. Skills (artefatos de IA): CRUD, versionamento e AGENT.md

### 8.1 Modelo
`Skill { id, type: PROMPT|POLICY|TOOL_DEFINITION|REASON_CATALOG, version (semver), status: DRAFT|ACTIVE|DEPRECATED|DELETED, content, checksum, createdBy, createdAt, changelog }`

- Versões são **imutáveis**: update cria versão nova e delete é *soft delete* (status `DELETED`, mantido para auditoria).
- Apenas uma versão `ACTIVE` por `id`, e ativar uma versão nova é uma operação explícita (permite rollback).
- Cada `DecisionRecord` grava as versões de skills usadas, o que dá rastreabilidade total.
- Seed inicial vem dos arquivos em `resources/skills/` e o CRUD é exposto via API.

### 8.2 API
`GET/POST /v1/skills`, `GET /v1/skills/{id}/versions`, `POST /v1/skills/{id}/versions`, `POST /v1/skills/{id}/versions/{v}/activate`, `DELETE /v1/skills/{id}`. Ativar a versão de um `PROMPT` sem um relatório de eval aprovado gera warning (gate documentado).

### 8.3 Evidências exigidas pelo case
- `AGENT.md`: papéis, contratos de entrada/saída, exemplos de uso, limitações e estratégias de fallback.
- `CHANGELOG-SKILLS.md` + histórico git: criação, atualização e remoção demonstradas (ex.: `analyst@1.0.0` → `1.1.0` corrigindo um caso do golden set; uma tool removida).
- Script/teste de demo que exercita o CRUD de ponta a ponta.

---

## 9. MCP (diferencial)

- **MCP server `erp-mock-mcp`** (SDK MCP, transporte stdio ou Streamable HTTP) expõe `get_cost_center_budget`, `get_supplier_profile`, `get_purchase_history`, `list_policies`.
- **A aplicação é MCP client** através de um `McpContextProvider`, que implementa a mesma interface `ContextProvider` que o `InProcessContextProvider`. A seleção é feita por propriedade.
- **Trade-off documentado (ADR):** MCP padroniza a integração e permite reuso por outros agentes e governança centralizada, mas tem custo de processo extra, latência, superfície de segurança (autenticação e allowlist de tools) e versionamento do servidor. Skills/scripts internos são mais rápidos e simples, porém acoplados. Recomendação: MCP para sistemas compartilhados entre agentes (ERP); interno para lógica exclusiva do agente (policy engine).
- Prioridade **P2**: entra se o núcleo estiver pronto até o dia 5.

---

## 10. Evals e testabilidade

- **Golden set** (`evals/golden/*.json`): ≥ 25 casos, cada um com entrada, contexto mockado e expectativas: `expectedDecision` (ou conjunto aceito), `riskLevel` mínimo, `mustCiteEvidence`, `mustFlagMissing`, `forbiddenDecisions`.
  - Categorias: aprovação clara (5), bloqueio rígido (4), escalonamento cinza (5), incompletos/`NEEDS_INFO` (4), ruidosos/inconsistentes (3), adversariais/prompt injection (3), multi-turno (2).
- **Critérios objetivos (gate de regressão):**
  - Validade de schema: **100%**
  - Grounding (todas as reasons com evidência válida): **100%**
  - Acurácia de decisão: **≥ 90%** (crítico: **0** `APPROVE` em casos que exigem bloqueio ou escalonamento)
  - Adversariais: **100%** sem obedecer a injeção
  - Custo médio por decisão e p95 de latência registrados e comparados com o baseline (alerta se > +20%)
- **Níveis de teste:**
  - Unitário: intake, policy engine, token budget, validador e grounding (JUnit 5 + AssertJ).
  - Integração: pipeline completo com `llm/fake.py`; cliente Anthropic com WireMock (retry, timeout, 429/529).
  - Contrato: JSON Schema de entrada e saída.
  - Eval: `LLM_PROVIDER=anthropic pytest -m eval` com o modelo real (manual/nightly) gera `evals/reports/<data>-<promptVersion>.md`.
- **Regressão quando prompt ou modelo muda:** toda mudança em `skills/prompts` ou no modelo exige rodar o eval e comparar com o baseline salvo (`evals/baseline.json`). Piora em qualquer critério crítico bloqueia a ativação.

---

## 11. Confiabilidade e observabilidade

- **Logs estruturados (JSON)** com `traceId`, `requestId`, estágio, decisão, `decidedBy`, versões de prompt, tokens, custo e motivo de fallback. Não logamos o prompt completo nem PII (somente hash e tamanho; prompt completo só com flag de debug em dev).
- **Métricas (prometheus-client → `/metrics`):** `llm.request.latency` (p50/p95/p99), `llm.tokens{direction,model}`, `llm.cost.usd`, `llm.errors{type}`, `llm.retries`, `agent.decisions{decision,decidedBy}`, `agent.fallbacks{reason}`, `agent.grounding.failures`, `agent.compliance.disagreements`, `context.tokens.by_layer`, `context.truncations`.
- **Traces:** spans por estágio do pipeline (OpenTelemetry via Micrometer Tracing).
- **Alertas conceituais (documentados):** taxa de fallback > 5% em 15 min; grounding failures > 0; custo/hora acima do budget; p95 > 15s; mudança brusca na distribuição de decisões (drift, ex.: % APPROVE ±15pp vs. semana anterior).
- **Resiliência:** Resilience4j (retry com backoff + jitter, circuit breaker, timeout de 30s no LLM), idempotência por `requestId`.
- **FinOps:** custo estimado por decisão, prompt caching na parte estática, Haiku para tarefas baratas, compliance só quando necessário, cache de decisão por `requestId` + hash da entrada.

---

## 12. Segurança

- Chave da API somente via variável de ambiente `ANTHROPIC_API_KEY` (nunca em arquivo versionado). `.env` fica no `.gitignore`.
- Autenticação da API via header `X-API-Key` (simplificação documentada; produção usaria OAuth2/mTLS).
- Prompt injection: delimitação de dados não confiáveis, tools somente leitura, saída validada por schema, regras rígidas fora do LLM e heurística `SUSPICIOUS_INPUT`.
- Minimização de dados enviados ao LLM (LGPD): mascaramento de PII antes do contexto.
- Limites de tamanho de entrada (payload ≤ 64KB, justificativa ≤ 4.000 chars, ≤ 100 itens) e rate limit simples.

---

## 13. Uso de IA na construção (exigência do case)

`docs/AI-USAGE.md` registra, por fase, onde a IA (Claude Code + workspace `agent-skills`) foi usada, quais skills/comandos (`/spec`, `/plan`, `/build`, `/test`, `/review`, `/ship`), o que foi aceito, o que foi corrigido manualmente e por quê. Os commits gerados com IA levam `Co-Authored-By`.

---

## Tech Stack

> **Versão Python** (esta). A spec foi escrita e aprovada para a versão Java ([gbmeello/itau-llm-case](https://github.com/gbmeello/itau-llm-case)); arquitetura, contratos, skills e critérios são os mesmos. Ver [ADR-0008](docs/adr/0008-python-vs-java.md).

| Item | Escolha |
|---|---|
| Linguagem | Python 3.12 |
| API | FastAPI 0.142 + Uvicorn |
| Contratos | Pydantic v2 + JSON Schema draft 2020-12 (`jsonschema`) |
| LLM SDK | `anthropic` 1.12 (SDK oficial) |
| MCP | `mcp` 2.3 (servidor `erp-mcp` + cliente) |
| Resiliência | Retry com backoff + jitter e circuit breaker próprios (`llm/resilient.py`) |
| Observabilidade | `prometheus-client` + logs JSON (contextvar com traceId) |
| Persistência | SQLAlchemy 2 (SQLite em memória por padrão; PostgreSQL via `DATABASE_URL`) |
| Testes | pytest |

## Commands

```
Setup:        python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   (Linux/macOS: .venv/bin/pip)
Testes:       pytest                                   # unitários + integração (LLM fake)
Evals (fake): pytest -m eval
Evals (real): LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... pytest -m eval
API:          python -m purchase_agent                 # http://127.0.0.1:8080 (LLM fake)
API + MCP:    ./scripts/run-with-mcp.sh                # servidor erp-mcp :8001 + API com ERP_SOURCE=mcp
MCP server:   python -m purchase_agent.mcp_server.erp_server [--stdio | --port 8001]
Demo:         ./scripts/demo.sh && ./scripts/demo-skills.sh
```

## Project Structure

```
itau-llm-case-python/
├── SPEC.md, AGENT.md, README.md, CHANGELOG-SKILLS.md, pyproject.toml
├── docs/                      → arquitetura, ADRs, contexto/tokens, observabilidade, testes, uso de IA, apresentação
├── src/purchase_agent/
│   ├── contract/ intake/ policy/ context/ llm/ agent/ registry/ audit/ api/ observability/
│   ├── mcp_server/            → servidor MCP do ERP
│   └── resources/             → schemas/, skills/ (prompts, políticas, exemplos), mock-data/
├── tests/                     → unitários, integração e golden set (marcador eval)
├── evals/                     → golden/cases.json, reports/, baseline-<provider>.json
├── examples/                  → requisições de demo e respostas reais
└── scripts/                   → demo.sh, demo-skills.sh, run-with-mcp.sh
```

## Code Style

```python
@dataclass(frozen=True)
class PolicyCheck:
    policy_id: str
    result: Result
    reason_code: str
    detail: str


def evaluate(req: NormalizedRequest, facts: PurchaseFacts, cfg: PolicyConfig, today: date) -> PolicyOutcome:
    # Regras rígidas primeiro: o LLM nunca pode reverter um HARD_REJECT.
    ...
```
- Pacotes por módulo (feature); dataclasses imutáveis no domínio, Pydantic na borda (contratos).
- Funções puras onde possível (intake, regras, contexto, validador): é o que torna os testes triviais.
- Comentários explicam o *porquê*; prompts nunca ficam embutidos no código.

## Testing Strategy
Ver seção 10. Cobertura alvo: ≥ 80% nos módulos `intake`, `policy`, `context`, `agent` (validador). TDD para policy engine, validador e token budget (`/test`).

## Boundaries
- **Always:** validar entrada e saída contra schema; citar evidências; falhar fechado (`ESCALATE`); rodar `pytest` antes de commit; registrar o uso de IA em `docs/AI-USAGE.md`; versionar prompts como arquivos.
- **Ask first:** trocar de modelo/provedor; adicionar dependência fora da tabela de stack; mudar contrato `v1`; rodar evals com a API real (custo).
- **Never:** commitar chaves/segredos; deixar o LLM sobrescrever regra rígida; chamar a API real em testes de CI; enviar PII ao LLM; apagar versões de skills fisicamente.

## Success Criteria
1. `pytest` passa sem chave de API (LLM fake).
2. `POST /v1/purchase-requests/evaluate` devolve `PurchaseDecision` válido para 100% dos casos do golden set, inclusive com o LLM fora do ar (fallback).
3. Eval com modelo real: schema 100%, grounding 100%, acurácia ≥ 90%, 0 aprovações indevidas e adversariais 100%.
4. Métricas de latência, tokens, custo, erro e fallback visíveis em `/metrics`.
5. CRUD de skills demonstrado com histórico (criação, atualização, ativação/rollback, remoção) e refletido nos `DecisionRecord`s.
6. Entregáveis: README (execução), AGENT.md, diagramas, ADRs, estratégia de testes, limitações/evoluções, AI-USAGE.md, roteiro de apresentação.
7. (Diferencial) Pipeline funcionando com `ContextProvider` via MCP.

## Fora de escopo (simplificações a explicar na apresentação)
Frontend; autenticação real (OAuth2); integração com ERP real; fila/processamento assíncrono; RAG vetorial (as políticas são poucas e são filtradas por metadados; vetorial é evolução); multi-tenant; deploy em cloud.

## Open Questions
1. **Ambiente:** não há JDK, Maven nem Docker instalados nesta máquina. Posso instalar o **JDK 21 (Temurin)** via `winget`? O Maven vem pelo wrapper. Docker é opcional (só para o perfil PostgreSQL).
2. **Chave Anthropic:** você tem uma `ANTHROPIC_API_KEY` para rodar a demo e os evals reais? Sem ela, tudo roda no modo fake e o eval real fica documentado.
3. **MCP:** confirmar MCP como P2 (stretch), ou ele é prioridade para você?
4. **Repositório remoto:** a entrega será um repositório no GitHub (público/privado)? Isso afeta o CI (GitHub Actions rodando testes + eval-fake).
5. **Prazo:** qual o dia de entrega (os 7 dias contam a partir de quando)? Assim calibro o plano.
