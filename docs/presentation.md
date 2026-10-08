# Roteiro da apresentação (30 min + 30 min de discussão)

Formato: 19 slides + **demo ao vivo** no ambiente Docker (`docker compose up` e `./scripts/demo.sh` no Git Bash). As seções do deck seguem os quatro pontos de avaliação do PDF.

| Bloco do PDF | Slides | Tempo |
|---|---|---|
| 1. Contexto da solução | 1–5 | 0:00–6:30 |
| 2. Decisões técnicas e trade-offs | 6–13 | 6:30–17:15 |
| 3. Qualidade, operação e evolução | 14–15 (com demo) | 17:15–25:00 |
| 4. Estratégia ampliada | 16–17 | 25:00–27:45 |
| Uso de IA e encerramento | 18–19 | 27:45–30:00 |

Se o tempo apertar, encurte a demo para 4 minutos (pule DBeaver e skills). Não corte os slides 5, 13 e 16: eles respondem a pontos que o PDF cita pelo nome.

## 1. Contexto da solução

| # | Slide | Tempo | Mensagem |
|---|---|---|---|
| 1 | Capa | 1:00 | Cenário 03; tese: o LLM é um componente de julgamento dentro de um sistema determinístico |
| 2 | O desafio | 1:30 | Entrada imperfeita, contexto do ERP, saída em contrato; 4 decisões possíveis |
| 3 | A tese | 0:45 | "O LLM julga. O sistema decide o que ele pode decidir." |
| 4 | Pipeline | 1:45 | 6 estágios, só 2 com LLM; atalho de rejeição por regra sem tokens |
| 5 | Requisitos → solução | 1:30 | Tabela: 5 requisitos funcionais do PDF + 4 não funcionais, cada um com o mecanismo |

## 2. Decisões técnicas e trade-offs

| # | Slide | Tempo | Decisão · alternativa rejeitada |
|---|---|---|---|
| 6 | Quem decide o quê | 1:15 | `decidedBy` em toda resposta · regras no prompt ([ADR-0001](adr/0001-llm-como-componente-controlado.md)) |
| 7 | Contratos | 1:15 | Structured outputs, o modelo gera só o julgamento · tool use forçado, rejeitado pelo Sonnet 5.5 ([ADR-0002](adr/0002-structured-outputs-e-validacao.md)) |
| 8 | Grounding | 1:45 | Evidência com ID conferida em código; reparo 1× e fallback |
| 9 | Contexto e tokens | 1:15 | 8k tokens em camadas P0–P4; o que fica de fora; resumo com teto no multi-turno |
| 10 | Multi-agente, tool calling e MCP | 1:45 | Papéis com orquestração em código · agente autônomo ([ADR-0003](adr/0003-contexto-pre-buscado-vs-tool-calling.md), [0004](adr/0004-multi-agente-e-modelos.md), [0006](adr/0006-mcp.md)) |
| 11 | Skills versionadas | 0:45 | Versões imutáveis, rollback, rastreabilidade ([ADR-0005](adr/0005-registry-de-skills.md)) |
| 12 | Segurança | 1:15 | Injeção tratada na estrutura, PII mascarada, rate limit, fail-safe |
| 13 | Escalabilidade, resiliência e stack | 1:30 | Stateless, gargalo no provedor; retry, circuit breaker, idempotência; o porquê de cada tecnologia ([ADR-0008](adr/0008-python-vs-java.md)) |

## 3. Qualidade, operação e demo

| # | Slide | Tempo | Mensagem |
|---|---|---|---|
| 14 | Qualidade e operação | 1:45 | 34 casos, 59 testes, 9 alertas, ~US$ 0,006 por decisão; gate de regressão; transparência sobre o fake |
| 15 | Demo ao vivo | 6:00 | `demo.sh` (7 cenários, multi-turno, idempotência) → resposta por dentro → Prometheus (alerta disparando) → DBeaver (`decision_record`) → logs do `erp-mcp` → skills, se sobrar tempo |

## 4. Estratégia ampliada

| # | Slide | Tempo | Mensagem |
|---|---|---|---|
| 16 | Como priorizei | 1:30 | Ordem pelo custo de errar: não errar caro → provar → operar → diferenciais; o que cortei; maiores riscos |
| 17 | Limitações e evolução | 1:15 | Simplificações conscientes; próximos passos em ordem, começando pelo eval com o Claude real |

## Encerramento

| # | Slide | Tempo | Mensagem |
|---|---|---|---|
| 18 | Uso de IA | 1:30 | Claude Code com spec aprovada antes do código; decisões humanas; erros da IA que a verificação pegou ([AI-USAGE.md](AI-USAGE.md)) |
| 19 | Encerramento | 0:45 | Repetir a tese e abrir para perguntas |

## Antes de apresentar

- `docker compose up -d` na pasta do projeto e `./scripts/demo.sh` para popular o banco.
- Provocar um alerta: duas requisições com `FAKE-INVALID-ALWAYS` no `requestId`.
- Deixar abertos: slides, Git Bash na pasta do projeto, `docker compose logs -f erp-mcp`, http://localhost:9090/alerts, DBeaver em `localhost:5432` (agent/agent), a collection do Postman e os arquivos `agent/validator.py`, `context/builder.py` e `agent/orchestrator.py`.
- Plano B se a demo falhar: respostas reais em `examples/responses/` e o relatório em `evals/reports/latest-fake.md`.

## Perguntas prováveis (respostas curtas)

| Pergunta | Resposta |
|---|---|
| Como a arquitetura atende aos requisitos não funcionais? | Segurança estrutural, retry + circuit breaker + fail-safe, métricas + alertas + auditoria, serviço stateless, regra sem LLM para custo (slide 5). |
| Como você priorizou? | Pelo custo de errar: não aprovar indevidamente primeiro, depois provar, operar e só então os diferenciais (slide 16). |
| E se o modelo alucinar uma política? | Políticas só entram como evidência com ID; razão sem evidência existente → reparo → fallback. |
| Por que não deixar o agente escolher tools livremente? | Dados conhecidos; pré-busca é mais barata, previsível e segura; tools são opcionais para aprofundar. |
| Consistência sem temperatura 0? | Schema fechado, regras, validador, idempotência e eval. |
| Onde está o gargalo de escala? | Na cota do provedor de LLM, não no serviço stateless; mitigado com regra antes do LLM, cache, rate limit e fila como evolução. |
| Quanto custa? | ~US$ 0,006 por decisão típica; zero quando a regra decide. |
| Maior risco hoje? | Não ter medido o Claude real; e, no negócio, excesso de escalonamentos virar gargalo humano. |
