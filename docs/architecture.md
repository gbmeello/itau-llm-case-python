# Arquitetura

## 1. Visão de componentes

```mermaid
flowchart TB
    subgraph Borda["Borda (api/app.py)"]
        F[middleware edge_guard<br/>traceId · API key · limite 64KB]
        R1[rotas /v1/purchase-requests · /v1/cases · /v1/decisions]
        R2[rotas /v1/skills]
    end
    subgraph Aplicação
        S[api/service.py<br/>contrato · idempotência · casos]
        AG[agent/orchestrator.py<br/>PurchaseApprovalAgent]
    end
    subgraph Determinístico
        I[intake/normalizer.py<br/>+ injection.py + cnpj.py]
        FC[context/facts.py]
        P[policy/engine.py]
        CB[context/builder.py<br/>+ ContextBudget]
        V[agent/validator.py]
        DA[agent/assembler.py]
    end
    subgraph LLM
        LC[LlmClient Protocol]
        RL[llm/resilient.py<br/>retry · circuit breaker · métricas]
        AN[llm/anthropic_client.py<br/>structured outputs · prompt cache]
        FK[llm/fake.py]
    end
    subgraph Dados
        REG[(registry/skills.py<br/>prompts · políticas · exemplos)]
        AUD[(audit/service.py<br/>DecisionRecord · ApprovalCase)]
        ERP[ErpGateway Protocol]
        MOCK[MockErpGateway]
        MCPC[McpErpGateway<br/>cliente MCP]
        MCPS[erp-mcp server<br/>mcp_server/erp_server.py]
    end
    F --> R1 & R2
    R1 --> S --> AG
    R2 --> REG
    AG --> I & FC & P & CB & V & DA
    FC --> ERP
    ERP --- MOCK & MCPC
    MCPC -- Streamable HTTP --> MCPS
    AG --> LC
    LC --- RL --> AN & FK
    AG --> REG
    S --> AUD
```

**Por que um monólito modular:** o escopo é de dias, e as fronteiras entre pacotes (`contract`, `intake`, `policy`, `context`, `llm`, `agent`, `registry`, `audit`, `mcp_server`) já permitem extrair serviços depois. A dependência é sempre na direção do núcleo determinístico, e o LLM fica atrás de uma interface ([ADR-0001](adr/0001-llm-como-componente-controlado.md)).

## 2. Sequência de uma avaliação

```mermaid
sequenceDiagram
    autonumber
    participant ERPc as Sistema consumidor
    participant API as API
    participant AG as Agente
    participant R as Regras
    participant CTX as Contexto
    participant AN as Analista (Sonnet)
    participant V as Validador
    participant CO as Compliance (Haiku)
    participant DB as Auditoria

    ERPc->>API: POST /evaluate (JSON)
    API->>API: valida schema v1 (400 se violar)
    API->>DB: já decidido com o mesmo conteúdo? (idempotência)
    API->>AG: evaluate
    AG->>AG: intake (normaliza, lacunas, injeção, PII)
    AG->>R: fatos do ERP + políticas (skill purchase-policies)
    alt HARD_REJECT (fornecedor bloqueado / sem orçamento)
        R-->>AG: REJECT determinístico (0 tokens)
    else
        AG->>CTX: seleciona e prioriza evidências (token budget)
        AG->>AN: system (skill, cacheado) + user (evidências delimitadas)
        AN-->>AG: JSON (structured output)
        AG->>V: schema · IDs de evidência · valores R$
        opt inválido
            AG->>AN: reparo com a lista de erros (1x)
        end
        V->>V: guardrails (APPROVE contra regra / risco alto / baixa confiança / injeção)
        opt APPROVE > R$ 10 mil ou risco ≥ MEDIUM, ou REJECT do modelo
            AG->>CO: segunda opinião
            CO-->>AG: agree / concerns
        end
    end
    AG->>AG: monta contrato v1 (erpPayload, audit) + valida schema de saída
    AG->>DB: DecisionRecord (contexto incluído/excluído, versões, custo)
    API-->>ERPc: 200 PurchaseDecision
```

## 3. Máquina de decisão

```mermaid
stateDiagram-v2
    [*] --> Intake
    Intake --> Regras
    Regras --> REJECT_regra: HARD_REJECT
    Regras --> Analista
    Analista --> Validação
    Analista --> FALLBACK: LLM indisponível / recusa
    Validação --> Reparo: erro reparável
    Reparo --> Validação
    Validação --> FALLBACK: inválido após reparo
    Validação --> Guardrails
    Guardrails --> ESCALATE_override: viola guardrail
    Guardrails --> Compliance: sensível
    Guardrails --> Final
    Compliance --> ESCALATE_compliance: discorda / indisponível
    Compliance --> Final: concorda
    FALLBACK --> ESCALATE
    Final --> NEEDS_INFO: abre caso
    NEEDS_INFO --> Analista: nova rodada (máx. 3)
```

## 4. Multi-turno (casos `NEEDS_INFO`)

- A decisão `NEEDS_INFO` cria um `caseId`. O solicitante responde via `POST /v1/cases/{caseId}/messages` com `message` (texto livre) e `updates` (campos estruturados, mesclados no request original).
- **Coerência sem crescimento de contexto:** a resposta do solicitante é anexada à justificativa (continua em `<untrusted_input>` e passa pelo detector de injeção). O histórico de rodadas **não** é reenviado: o `case-summarizer` (Haiku) produz um resumo incremental com teto (~80 palavras, 600 caracteres), que entra como `EV-CASE` (camada P3, ≤ 300 tokens).
- No máximo 3 rodadas; depois disso, `ESCALATE_TO_HUMAN` com o evento `CASE_ROUNDS_EXHAUSTED`. Se o sumarizador falhar, usa-se um resumo determinístico truncado.

## 5. Decisões técnicas

| Tema | Decisão | ADR |
|---|---|---|
| Papel do LLM | Componente controlado; regras rígidas fora dele | [0001](adr/0001-llm-como-componente-controlado.md) |
| Contrato de saída | Structured outputs + JSON Schema + validação em código | [0002](adr/0002-structured-outputs-e-validacao.md) |
| Contexto | Pré-busca determinística em vez de tool calling autônomo | [0003](adr/0003-contexto-pre-buscado-vs-tool-calling.md) |
| Multi-agente | Analista (Sonnet) + validador (código) + compliance (Haiku) | [0004](adr/0004-multi-agente-e-modelos.md) |
| Skills | Registry versionado, imutável, soft delete | [0005](adr/0005-registry-de-skills.md) |
| MCP | Servidor `erp-mcp` + `McpErpGateway` (ERP_SOURCE=mcp) |  [0006](adr/0006-mcp.md) |
| Testes de LLM | Fake determinístico + golden set + eval real com gate | [0007](adr/0007-fake-llm-e-evals.md) |
| Linguagem | Python (port da versão Java) | [0008](adr/0008-python-vs-java.md) |
