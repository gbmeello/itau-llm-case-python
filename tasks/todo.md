# Tarefas: versão Python

Mesma spec e mesmo plano da versão Java ([gbmeello/itau-llm-case](https://github.com/gbmeello/itau-llm-case/blob/main/tasks/todo.md)); aqui o port e o diferencial.

- [x] **P1 Ambiente**: Python 3.12, venv, dependências fixadas (pyproject).
- [x] **P2 Recursos compartilhados**: schemas, skills, mock-data, golden set e exemplos copiados sem alteração.
- [x] **P3 Port do núcleo**: contract, intake, policy, context, llm (Anthropic 1.x + fake + resiliência), agent, registry, audit, api.
- [x] **P4 MCP**: servidor `erp-mcp` (mcp 2.x) + `McpErpGateway` + teste do pipeline via MCP + demo em processos separados.
- [x] **P5 Testes**: 42 testes (paridade com Java + circuit breaker + MCP).
- [x] **P6 Evals**: golden set 37 turnos / 100% contra o fake (mesmo resultado da versão Java).
- [x] **P7 Docs**: adaptadas da versão Java; ADR-0006 reescrito (MCP implementado), ADR-0008 (Python vs Java), AI-USAGE atualizado.
- [x] **P8 Repo + CI**: GitHub Actions (pytest + eval fake).
- [ ] **P9 Eval real**: `LLM_PROVIDER=anthropic pytest -m eval` (aguarda chave de API).
