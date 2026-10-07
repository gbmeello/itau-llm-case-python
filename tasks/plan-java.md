# Plano de implementação

> Fonte: `SPEC.md` (aprovada em 2026-10-06). Prazo de entrega: **2026-10-07**. Apresentação: **2026-10-08 (quinta)**.
> Como o prazo é curto, a prioridade é: núcleo funcionando ponta a ponta com o LLM fake → guardrails → evals → docs → diferenciais.

## Ajustes técnicos descobertos no planejamento (fonte: documentação do SDK Anthropic)
- **Claude Sonnet 5.5 rejeita `tool_choice` forçado** (`any`/`tool` geram 400). Por isso o contrato de saída é imposto via **structured outputs** (`output_config.format` com JSON Schema), e não via tool forçada. Atualiza a seção 6 da spec.
- **Sonnet 5.5 rejeita `temperature` diferente do default.** A consistência vem do schema, das regras, do validador e dos evals, não da temperatura.
- ID correto do Haiku: `claude-haiku-4-5`.
- **O LLM gera só o "julgamento"** (`LlmAssessment`: decisão, risco, razões com evidências, lacunas). O código monta o `PurchaseDecision` final (policyChecks, evidence, erpPayload, audit). Isso reduz o espaço de alucinação e o número de tokens de saída.

## Componentes e ordem
```
contract ─┬─> intake ─┐
          ├─> policy ─┼─> context ─> agent ─> api/audit ─> evals ─> docs
llm ──────┴───────────┘                 └─> registry (skills)
```

## Fases e checkpoints
1. **Fundação** (T1–T3): projeto Maven, contratos, intake e policy engine com testes. ✔ `mvn test` verde
2. **Contexto + LLM** (T4–T5): mock ERP, context builder com token budget, clientes LLM (Anthropic + Fake) com resiliência. ✔ testes unitários
3. **Agente** (T6–T7): orquestrador analista → validador → reparo → compliance → fallback; API REST + auditoria. ✔ `curl` retorna decisão válida no perfil fake
4. **Skills** (T8): registry versionado + CRUD + rastreabilidade nas decisões. ✔ teste de integração do CRUD
5. **Evals + observabilidade** (T9–T10): golden set, runner, relatório; métricas Micrometer. ✔ `mvn verify -Peval-fake` com 100% dos critérios
6. **Entrega** (T11–T12): README, AGENT.md, ADRs, diagramas, AI-USAGE, roteiro; repositório GitHub + CI. ✔ CI verde
7. **Diferencial** (T13): MCP server ERP-mock, se houver tempo; senão, ADR + interface pronta.

## Riscos
| Risco | Mitigação |
|---|---|
| Sem chave de API até a entrega | Tudo roda com o `FakeLlmClient`. O eval real fica pronto para rodar com uma variável de ambiente, e a limitação é declarada |
| API do SDK Java diferente do esperado | Compilar cedo e iterar sobre os erros do compilador |
| Prazo | MCP é o primeiro corte; a documentação é escrita junto com o código |
