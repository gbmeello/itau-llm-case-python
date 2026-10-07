# Roteiro da apresentação (30 min + 30 min de discussão)

Formato sugerido: slides curtos baseados nos diagramas de [architecture.md](architecture.md) + **demo ao vivo** (`python -m purchase_agent` + `./scripts/demo.sh`).

## 0. Abertura (1 min)
"Construí um agente de aprovação de compras em que o LLM é um componente de julgamento dentro de um sistema determinístico. A tese: **confiabilidade vem da engenharia em volta do modelo**, não do modelo sozinho."

## 1. Contexto da solução (5 min)
- O problema: aprovar compras exige regras objetivas (alçada, orçamento, fornecedor) e um julgamento cinza (coerência, conflito de interesses, fracionamento).
- O contrato: entrada `purchase-request.v1` (tolerante) → saída `purchase-decision.v1` (estrita, com `erpPayload`).
- Diagrama de componentes + máquina de decisão (4 saídas possíveis e quem decide cada uma: `decidedBy`).

## 2. Decisões técnicas e trade-offs (10 min)
Uma decisão por slide, sempre com a alternativa rejeitada:
1. **Regras fora do LLM** ([ADR-0001](adr/0001-llm-como-componente-controlado.md)): fornecedor bloqueado = 0 tokens; o LLM não tem como aprovar contra a regra.
2. **Structured outputs + validador em código** ([ADR-0002](adr/0002-structured-outputs-e-validacao.md)): mostrar o achado do Sonnet 5.5 sem `tool_choice` forçado.
3. **Grounding verificável:** evidências com ID, validador recusa ID inexistente e valor R$ inventado → reparo → fallback.
4. **Engenharia de contexto:** o que entra e o que não entra, camadas P0–P4, budget de 8k ([context-and-tokens.md](context-and-tokens.md)).
5. **Multi-agente com papéis e modelos certos** ([ADR-0004](adr/0004-multi-agente-e-modelos.md)): Sonnet analisa, código valida, Haiku audita só quando o risco justifica.
6. **MCP** ([ADR-0006](adr/0006-mcp.md)): o ERP como servidor MCP; ferramentas chamadas pelo código, não pelo modelo.
7. **Skills versionadas** ([ADR-0005](adr/0005-registry-de-skills.md)): imutáveis, rollback, `skillVersions` em cada decisão.
8. **Segurança:** prompt injection tratado de forma estrutural (dados delimitados, regras fora do LLM, guardrail) + heurística como sinal; PII descartada no intake.

## 3. Demo ao vivo (7 min)
```bash
./scripts/run-with-mcp.sh     # terminal 1: servidor MCP do ERP + API (ERP via MCP)
./scripts/demo.sh               # terminal 2
./scripts/demo-skills.sh
```
Roteiro:
1. `01-approve` → AUTO_APPROVE com evidências citadas.
2. `02-escalate-above-history` → 3,11× a média sem cotação → humano, alçada 2.
3. `04-blocked-supplier` → `RULE_ENGINE`, `llmCalls: 0`, custo 0.
4. `05-prompt-injection` → ESCALATE; mostrar `SEC-INPUT`.
5. `06-llm-outage` → FALLBACK com contrato válido.
6. Multi-turno: NEEDS_INFO → complemento → APPROVE.
7. `GET /v1/decisions/{id}`: o que entrou e o que foi cortado do contexto, versões e custo.
8. `/metrics`: decisões por `decidedBy`, custo, fallbacks.
9. Skills: nova versão do analyst → decisão registra `analyst@1.2.0` → rollback.
10. MCP: mostrar o log do servidor `erp-mcp` recebendo as chamadas de ferramenta (ERP_SOURCE=mcp) e explicar o trade-off (ADR-0006).

## 4. Qualidade, operação e evolução (5 min)
- Pirâmide de testes + golden set (34 casos, 8 categorias) + critérios objetivos (schema 100%, grounding 100%, 0 decisões proibidas, adversariais 100%, acurácia ≥ 90%).
- **Transparência:** o relatório do fake prova o sistema, não o modelo; o eval com o Claude real é o próximo passo ([testing-strategy.md](testing-strategy.md) §5).
- Observabilidade: métricas e alertas propostos (drift de decisões, grounding, fallbacks, custo).
- FinOps: ≈ US$ 0,006–0,009 por decisão típica; alavancas (regra sem LLM, cache, Haiku seletivo, idempotência).
- Evoluções priorizadas ([limitations-and-evolution.md](limitations-and-evolution.md)): eval real, MCP, human-in-the-loop alimentando o golden set, fila assíncrona.

## 5. Uso de IA (2 min)
[AI-USAGE.md](AI-USAGE.md): Claude Code + workflow `agent-skills` com gates (spec aprovada antes do código), o que foi decisão minha (cenário, primeira versão em Java e esta em Python), e os erros que a IA cometeu e que a verificação pegou.

## Perguntas prováveis (e respostas curtas)
| Pergunta | Resposta |
|---|---|
| E se o modelo alucinar uma política? | Políticas só entram como `EV-PT-*`; razão sem evidência existente → reparo → fallback. O motor de regras é a fonte da verdade para regras objetivas. |
| Por que não deixar o agente chamar tools? | Dados necessários são conhecidos; a pré-busca é mais barata, previsível e segura. Tools seriam a evolução para documentos sob demanda (ADR-0003). |
| Como garantir consistência sem temperatura 0? | Schema fechado, regras determinísticas, validador, idempotência e eval com critérios; a variação fica restrita à zona cinza. |
| Como evitar que o contexto cresça em conversas longas? | Resumo incremental com teto, no máximo 3 rodadas, histórico sempre sumarizado e corte por prioridade com registro do que saiu. |
| Quanto custa? | ~US$ 0,006–0,009 por decisão típica; rejeição por regra custa 0; métrica de custo por decisão em tempo real. |
| Como mudar um prompt com segurança? | Nova versão de skill → eval fake (CI) + eval real vs. baseline → ativar → monitorar drift → rollback em 1 chamada se necessário. |
| O que você faria diferente com mais tempo? | Eval real primeiro; MCP; human-in-the-loop alimentando o golden set; OpenTelemetry. |
