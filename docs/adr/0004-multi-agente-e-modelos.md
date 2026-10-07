# ADR-0004: Papéis especializados e escolha de modelos

- **Status:** aceito · **Data:** 2026-10-06

## Decisão
| Papel | Implementação | Motivo |
|---|---|---|
| Analista (executor) | Claude **Sonnet 5.5**, effort `medium` | Julgamento com bom custo-benefício; effort médio é suficiente para uma tarefa de classificação com contexto curto |
| Validador | **Código** | Determinístico, barato, impossível de "convencer" |
| Compliance (auditor) | Claude **Haiku 4.5** | Segunda opinião barata, só quando o risco justifica (APPROVE > R$ 10 mil, risco ≥ MEDIUM, REJECT do modelo) |
| Sumarizador de caso | Claude Haiku 4.5 | Tarefa simples e de alto volume |

Discordância do compliance → `ESCALATE_TO_HUMAN` (conservador: duas opiniões divergentes significam que um humano decide).

## Alternativas
- **Opus 5.5 como analista:** mais capaz e cerca de 2× mais caro. Só se justificaria se o eval real mostrar lacuna de qualidade no Sonnet. Os modelos são configuráveis (`AGENT_ANALYST_MODEL`), então a troca é uma questão de rodar o eval.
- **Validador como LLM:** não determinístico, caro e sujeito à mesma injeção. Rejeitada.
- **Compliance em 100% das decisões:** dobra o custo em casos triviais. Rejeitada.

## Consequências
- Custo por decisão com limite conhecido (≤ 3 chamadas: analista, reparo e compliance).
- Não há conversa livre entre agentes: o orquestrador é código, e a ordem é fixa e auditável.
