# ADR-0001: O LLM é um componente controlado, não o dono da decisão

- **Status:** aceito · **Data:** 2026-10-06

## Contexto
Aprovação de compras tem regras objetivas (alçadas, fornecedor bloqueado, saldo) e uma zona cinza (coerência entre justificativa, valor e histórico; sinais de conflito de interesses). Um erro de aprovação tem custo financeiro e de compliance.

## Decisão
- Regras objetivas rodam em código **antes** do LLM (`policy/engine.py`). `HARD_REJECT` decide sozinho, sem chamar o modelo. `BLOCKS_APPROVAL` impede o modelo de aprovar.
- O LLM faz só o julgamento da zona cinza e a explicação.
- A saída do LLM é verificada por código (`agent/validator.py`), e violações de guardrail são **sobrescritas** para `ESCALATE_TO_HUMAN`, nunca negociadas com o modelo.
- Toda falha termina em `ESCALATE_TO_HUMAN` (fail-safe).

## Alternativas
- **LLM decide tudo, com as regras no prompt:** mais simples, mas não é auditável nem previsível; o modelo pode "esquecer" uma alçada ou ser manipulado por injeção. Rejeitada.
- **Só regras (sem LLM):** previsível, mas não captura justificativa fraca, incoerência ou sinais qualitativos. Rejeitada.

## Consequências
- (+) Rejeições objetivas custam 0 tokens; não existe caminho em que o LLM aprove contra a política; comportamento testável.
- (−) Há duas fontes de lógica (regras e prompt) a manter coerentes. Mitigação: as políticas em texto e os parâmetros das regras vivem na **mesma skill** (`purchase-policies`), versionada junto.
