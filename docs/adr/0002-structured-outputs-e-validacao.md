# ADR-0002: Contrato de saída via structured outputs + validação em código

- **Status:** aceito · **Data:** 2026-10-06

## Contexto
O ERP consome a saída, então ela precisa seguir um contrato estável. Pedir "responda em JSON" no prompt não garante formato. O Claude Sonnet 5.5 **não aceita `tool_choice` forçado** (retorna 400) nem `temperature` diferente do default (verificado na documentação do SDK durante o planejamento).

## Decisão
1. O modelo gera apenas o `LlmAssessment` (decisão, risco, razões com `evidenceIds`, lacunas), imposto via **structured outputs** (`output_config.format` com JSON Schema, `additionalProperties: false`, enums fechados).
2. O código monta o restante do `PurchaseDecision` (checagens de regra, evidências, `erpPayload`, auditoria), o que diminui a superfície de alucinação e os tokens de saída.
3. Validação em camadas: JSON Schema do assessment → ranges → grounding (IDs existem, valores R$ aparecem nas evidências) → guardrails → JSON Schema do contrato final antes de responder.
4. Um reparo guiado pelos erros; se persistir, `FALLBACK`.

## Alternativas
- **Tool use forçado:** padrão comum para extrair JSON, mas incompatível com o Sonnet 5.5. `tool_choice: auto` com instrução funcionaria, mas sem a garantia de schema.
- **Parsing tolerante + defaults:** esconde erros do modelo. Rejeitada: preferimos falhar visivelmente e reparar.
- **Temperatura 0 para consistência:** não é suportada no modelo escolhido; a consistência vem de schema, regras, validador e evals.

## Consequências
- (+) Saída sempre válida para o ERP, inclusive em falha; alucinação de evidência é detectada de forma determinística.
- (−) A verificação de valores R$ é estrita: valores calculados pelo modelo são rejeitados. Isso motivou o `analyst@1.1.0`, que orienta o uso de `ratioToAverage`/`percentOfAvailable`.
