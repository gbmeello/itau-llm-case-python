# ADR-0003: Contexto pré-buscado em vez de tool calling autônomo

- **Status:** aceito · **Data:** 2026-10-06

## Contexto
O agente precisa de orçamento, fornecedor, histórico e políticas. Ele poderia buscá-los via tool calling (o modelo decide o que consultar) ou recebê-los pré-buscados.

## Decisão
**Pré-busca determinística** (`context/facts.py` → `context/builder.py`), com seleção, priorização e corte por token budget. Sem tool calling no fluxo principal do MVP.

## Racional
- O conjunto de dados necessário é **conhecido e pequeno**: não há exploração aberta que justifique um agente autônomo.
- Latência e custo previsíveis: 1 chamada ao modelo, em vez de N rodadas de tool use.
- Superfície de ataque menor: uma injeção no texto não consegue induzir o modelo a chamar ferramentas.
- Auditoria simples: o contexto exato é registrado (`contextIncluded`/`contextExcluded`).
- Falha de fonte tratada de forma explícita (`EV-SRC-*`), em vez de depender do modelo perceber.

## Alternativas
- **Tool calling (máx. 3 rodadas, ferramentas somente leitura):** útil se o volume de dados crescer (ex.: buscar contratos ou notas fiscais sob demanda). Fica como evolução, reaproveitando `ErpGateway` como implementação das tools, com retry e timeout já presentes no `llm/resilient.py`.

## Consequências
- (+) Simples, barato, testável e seguro.
- (−) O modelo não pode pedir um dado extra que não foi pré-buscado. Mitigação: `NEEDS_INFO`/`ESCALATE` quando falta evidência.

## Atualização (2026-10-07): tool calling opcional implementado
A pré-busca continua sendo o caminho padrão. Com `AGENT_TOOL_CALLING=true`, o analista também pode **pedir** detalhes via 3 ferramentas somente leitura (`get_purchase_history_details`, `get_policy_text`, `get_supplier_profile`), executadas pelo código via `ErpGateway` (e, portanto, via MCP quando `ERP_SOURCE=mcp`). Controles:
- máximo de 3 rodadas; depois, `tool_choice: none` força a resposta final (`any`/`tool` são rejeitados pelo Sonnet 5.5);
- retry por chamada (ResilientLlmClient); argumentos inválidos ou fonte fora do ar voltam ao modelo como `tool_result` com `is_error`;
- resultados viram evidências `EV-T*`, sujeitas ao mesmo grounding, com budget de ~1.000 tokens;
- histórico append-only (blocos do assistente, inclusive thinking, devolvidos intactos).

O golden set passa nos dois modos. Testes: `tests/test_pii_ratelimit_tools.py`.
