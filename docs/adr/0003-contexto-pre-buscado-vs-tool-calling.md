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
