# Changelog de Skills (artefatos de IA)

Rastreabilidade de **criação, atualização e remoção** de prompts, políticas e exemplos. Cada decisão registra em `audit.skillVersions` as versões exatas que usou, então é sempre possível responder "qual prompt gerou esta decisão?".

**Regras do registry** (`registry/skills.py`):
- Versões são **imutáveis** (semver). "Atualizar" é criar uma nova versão.
- Há uma versão `ACTIVE` por skill. Ativar outra deprecia a anterior, e **rollback** é reativar uma versão antiga.
- **Remoção é lógica** (`DELETED`) para não quebrar a auditoria de decisões passadas. Skills essenciais (`analyst`, `repair`, `compliance-reviewer`, `case-summarizer`, `purchase-policies`) não podem ser removidas, só versionadas.
- Fonte inicial: arquivos em `src/purchase_agent/resources/skills/<tipo>/<skill>/<versão>.*`, versionados em git e carregados pelo `seed_from_resources` (a versão mais alta fica ativa). Em runtime, a evolução acontece pela API `/v1/skills`.
- Gate: uma mudança de prompt só deve ser ativada depois do eval (`pytest -m eval` no CI, ``LLM_PROVIDER=anthropic pytest -m eval`` com o modelo real) sem regressão contra o baseline.

## Histórico

| Data | Skill | Versão | Operação | Motivo / evidência |
|---|---|---|---|---|
| 2026-10-06 | `analyst` | 1.0.0 | **Criação** | Prompt inicial do analista: papel, 10 regras, sinais de risco e catálogo de códigos |
| 2026-10-06 | `repair` | 1.0.0 | **Criação** | Prompt de reparo guiado pelos erros do validador |
| 2026-10-06 | `compliance-reviewer` | 1.0.0 | **Criação** | Revisor conservador (na dúvida, discorda) |
| 2026-10-06 | `case-summarizer` | 1.0.0 | **Criação** | Resumo incremental com teto de 80 palavras |
| 2026-10-06 | `analyst-examples` | 1.0.0 | **Criação** | 3 exemplos few-shot (APPROVE, NEEDS_INFO, ESCALATE) |
| 2026-10-06 | `purchase-policies` | 1.0.0 | **Criação** | Alçadas, fornecedor, orçamento, categorias restritas, fracionamento, cotações |
| 2026-10-06 | `analyst` | 1.1.0 | **Atualização** | Ao revisar a interação entre prompt e validador, vimos que a v1.0.0 não proibia *valores calculados*. O validador de grounding rejeita valores fora das evidências, então o modelo real faria reparos desnecessários (custo e latência a mais). A v1.1.0 orienta o uso de `ratioToAverage` e `percentOfAvailable` e adiciona uma regra explícita para fontes indisponíveis (`EV-SRC-*`). Commit: `feat(skills): analyst 1.1.0` |
| 2026-10-06 | `tmp-examples` | 1.0.0 | **Criação → Remoção** | Demonstração do ciclo completo via API (`tests/test_api.py::test_skill_crud_is_versioned_and_traceable_in_decisions` e `scripts/demo-skills.sh`): criada, removida logicamente e mantida no histórico como `DELETED` |

## Como reproduzir as operações

```bash
# listar
curl -s localhost:8080/v1/skills -H "X-API-Key: dev-key-change-me"
# nova versão (update) já ativa
curl -s -X POST localhost:8080/v1/skills/analyst/versions -H "X-API-Key: dev-key-change-me" -H "X-User: gabriel" \
  -H "Content-Type: application/json" -d '{"version":"1.2.0","content":"...","changelog":"...","activate":true}'
# rollback
curl -s -X POST localhost:8080/v1/skills/analyst/versions/1.0.0/activate -H "X-API-Key: dev-key-change-me"
# remoção lógica (skill não essencial)
curl -s -X DELETE localhost:8080/v1/skills/analyst-examples -H "X-API-Key: dev-key-change-me"
```
