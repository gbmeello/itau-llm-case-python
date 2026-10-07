# Engenharia de contexto, token budget e FinOps

## 1. O que entra no contexto (e por quê)

Cada item vira uma **evidência com ID citável**. É isso que torna o grounding verificável por código.

| ID | Camada | Conteúdo | Por que entra |
|---|---|---|---|
| `EV-REQ` | P0 (nunca corta) | Solicitação normalizada (+ dados estruturados: total, categoria, issues) | É o objeto da decisão |
| `EV-JUST` | P0 | Marcador da justificativa (texto em `<untrusted_input>`) | Permite citar a justificativa sem misturá-la às instruções |
| `EV-POL-n` | P0 | Resultado de cada checagem do motor de regras | Limites que o modelo não pode contrariar |
| `EV-SRC-n` | P0 | Fonte indisponível | Evita que o modelo presuma que um dado não verificado está OK |
| `EV-BUD` | P1 | Orçamento (anual, comprometido, disponível, % desta compra) | Sinal objetivo de capacidade |
| `EV-SUP` | P1 | Perfil do fornecedor (status, risco, tempo de cadastro, observações) | Risco de contraparte |
| `EV-PT-*` | P2 | Políticas em texto **aplicáveis à categoria e ao valor**; as acionadas vêm primeiro | O modelo precisa do texto para julgar "cotações", "conflito de interesses" |
| `EV-HIST-STATS` | P3 | Histórico **sumarizado**: n, média, máximo, razão desta compra sobre a média | Comparação com o padrão, em ~60 tokens em vez de centenas |
| `EV-HIST-1..5` | P3 | Top-5 compras mais similares (mesmo fornecedor e categoria > mesma categoria > recentes) | Exemplos concretos sem despejar o histórico |
| `EV-CASE` | P3 | Resumo incremental do caso (multi-turno) | Coerência entre rodadas sem reenviar a conversa |
| exemplos | P4 | Few-shot (skill `analyst-examples`), no system prompt cacheado | Calibração de formato e de quando usar NEEDS_INFO/ESCALATE |

## 2. O que **não** entra

| Fica de fora | Motivo |
|---|---|
| Histórico bruto linha a linha | Custo cresce com o volume e o sinal é baixo; vira estatística + top-5 |
| Políticas de outras categorias e abaixo do valor mínimo | Ruído aumenta a chance de o modelo aplicar a regra errada |
| Nome do solicitante e PII | Minimização (LGPD): o nome é descartado no intake; CPF, e-mail, telefone e cartão no texto livre são mascarados. A decisão não depende de quem é a pessoa |
| Campos internos do ERP | Irrelevantes para a decisão |
| Transcrição de rodadas anteriores | Substituída pelo resumo com teto |
| Schema JSON no prompt | Já é imposto por `output_config.format`; repeti-lo gastaria tokens |

## 3. Token budget

Configurável em `ContextBudget` (config.py) ([config.py](../src/purchase_agent/config.py)).

| Camada | Teto (tokens) |
|---|---|
| Total de entrada da chamada do analista | **8.000** |
| Reserva do system prompt (regras, cacheado) | 1.500 |
| P0 solicitação | 1.000 |
| P0 regras | 600 |
| P1 fatos | 500 |
| P2 políticas | 1.200 |
| P3 histórico | 800 |
| P3 caso | 300 |
| P4 exemplos | 600 |
| **Saída** (`max_tokens` do analista) | 4.000 (teto de segurança; o uso típico é de 150–600) |

**Algoritmo** (`builder.select`): ordena por camada; P0 sempre entra; as demais entram se couberem no teto da camada **e** no total. O corte é por item inteiro (nunca no meio de uma evidência). Se o total apertar, os exemplos few-shot são os primeiros a sair. Tudo o que é cortado fica registrado em `contextExcluded` na auditoria e na métrica `context_truncations_total`.

**Estimativa:** 3,5 caracteres/token, conservador para português + JSON, sem chamada de rede. O valor real vem de `usage` na resposta, exportado em `llm_tokens_total`, e serve para calibrar a razão.

**Controle de crescimento:**
1. Histórico sempre sumarizado (independe do volume).
2. Resumo do caso com teto fixo, re-sumarizado a cada rodada (sumarização incremental).
3. Justificativa truncada em 2.000 caracteres no intake.
4. Máximo de 3 rodadas por caso e 1 reparo por decisão, então o número de chamadas por decisão é limitado (≤ 3 de LLM: analista, reparo e compliance).

## 4. FinOps

| Alavanca | Implementação | Efeito |
|---|---|---|
| Não chamar o LLM quando a regra decide | `HARD_REJECT` → `RULE_ENGINE` | 0 tokens em rejeições objetivas |
| Idempotência | Hash canônico do payload → decisão anterior (`X-Idempotent-Replay: true`) | Retry do cliente não paga de novo |
| Prompt caching | System prompt (regras + exemplos) com `cache_control` | Leitura de cache a ~10% do preço do input ⚠️ **A verificar com a chave:** o cache só vale acima de um prefixo mínimo, que depende do modelo (512 a 4.096 tokens). O system prompt tem ~1.400 tokens; se ficar abaixo do mínimo do Sonnet 5.5, o cache é ignorado sem erro. Confirmar em `llm_tokens_total{direction="cache_read"}` > 0 no eval real; se for zero, mover as políticas fixas para o system prompt (aumenta o prefixo cacheável) |
| Modelo certo por tarefa | Sonnet no julgamento; Haiku no compliance e na sumarização | Segunda opinião ~2× mais barata |
| Compliance seletivo | Só em APPROVE > R$ 10 mil, risco ≥ MEDIUM ou REJECT do modelo | Evita dobrar o custo em casos simples |
| Contexto enxuto | Histórico sumarizado, políticas filtradas | Entrada típica de ~1,5–3 mil tokens |
| Visibilidade | `audit.estimatedCostUsd` por decisão; `llm_cost_usd_total` e `agent_decision_cost_usd` em métrica | Custo por decisão como KPI |

**Estimativa de custo por decisão** (preços Sonnet 5.5: US$ 2 / 10 por MTok; Haiku 4.5: US$ 1 / 5):
- Típica (analista, ~2.500 tokens in, ~300 out, system cacheado *se* o prefixo atingir o mínimo do modelo): **≈ US$ 0,006–0,009**
- Com compliance: **+ ≈ US$ 0,004**
- Pior caso (analista + reparo + compliance, sem cache): **≈ US$ 0,03**
- 10 mil solicitações/mês ≈ **US$ 60–120/mês** (o valor real deve sair do eval com o modelo real e de produção)
