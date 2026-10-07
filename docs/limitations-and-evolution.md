# Simplificações, limitações e evoluções

## 1. Simplificações conscientes (escopo de 1 dia)

| Simplificação | Por que é aceitável aqui | Como seria em produção |
|---|---|---|
| ERP, orçamento e cadastro **mockados** (JSON sintético) | O foco do case é engenharia de IA, não integração | `ErpGateway` sobre APIs reais ou MCP ([ADR-0006](adr/0006-mcp.md)), com cache e timeouts |
| "Hoje" fixo (`AGENT_FIXED_DATE=2026-10-06`) | Torna reproduzíveis regras temporais (fornecedor novo, fracionamento) e os evals | Relógio real; os evals continuam usando um relógio fixo |
| Autenticação por API key estática | Demonstra o ponto de controle | OAuth2/mTLS, autorização por papel (solicitante x aprovador x admin de skills) |
| API síncrona | Latência típica de segundos é aceitável para aprovação | Fila (Kafka/SQS) + workers + callback/webhook para o ERP; backpressure natural sob rate limit |
| SQLite em memória por padrão | Roda sem infraestrutura | `DATABASE_URL` para PostgreSQL + migrações (Alembic) |
| Políticas filtradas por metadados (categoria e valor) | Há 9 políticas | RAG com busca híbrida (BM25 + embeddings) quando houver centenas de documentos |
| Traces via `traceId` em contextvar/headers | Já permite correlacionar log, auditoria e resposta | OpenTelemetry com spans por estágio e convenções `gen_ai.*` |
| Gate "eval antes de ativar skill" é processo | Documentado e com runner pronto | Ativação exige um ID de relatório de eval aprovado; pipeline de promoção (dev → staging → prod) |
| Detector de injeção por regex | Barato, explica o sinal, gera métrica | Classificador dedicado (ex.: modelo pequeno) como sinal adicional; as defesas estruturais continuam sendo as principais |

## 2. Limitações conhecidas

1. **Qualidade do modelo não medida com o Claude real**: sem chave de API até a entrega. O eval fake valida o sistema, não o julgamento. **Próximo passo nº 1.**
2. **Grounding parcial:** verifica IDs e valores monetários, mas não afirmações qualitativas ("fornecedor confiável"). Mitigado pelo compliance e pela revisão humana. Evolução: citações em nível de trecho e um verificador NLI barato.
3. **Engenharia social sem padrão léxico** (GS-026) não é detectada pela heurística. A proteção vem das regras (cotações acima de R$ 50 mil) e do julgamento do modelo; o resultado com o modelo real ainda precisa ser medido.
4. **Estimativa de tokens local** (3,5 caracteres/token) pode errar em ±20%. O budget tem folga e o valor real é medido.
5. **Fake "passa por construção":** foi escrito junto com o golden set (ver [ADR-0007](adr/0007-fake-llm-e-evals.md)).
6. **Idempotência por hash exato:** uma mudança irrelevante no payload (ex.: espaço) gera uma nova avaliação. A normalização canônica cobre a ordem das chaves, mas não espaços dentro de strings.
7. **Um caso por `requestId` não é imposto:** reenviar com conteúdo diferente cria uma nova decisão (fica auditado, mas pode confundir o consumidor).
8. **MCP sem autenticação e com uma sessão por chamada** (ver ADR-0006): adequado à demo, não à produção.

## 3. Evoluções priorizadas

| Prioridade | Evolução | Valor |
|---|---|---|
| P0 | Rodar ``LLM_PROVIDER=anthropic pytest -m eval`` com Claude real, salvar baseline e ajustar o prompt pelo relatório | Mede a qualidade de verdade |
| P0 | CI com ``pytest -m eval` (fake)` em todo PR e ``LLM_PROVIDER=anthropic pytest -m eval`` nightly com orçamento de custo | Regressão contínua |
| P1 | MCP em produção: OAuth + TLS no `erp-mcp`, sessão por requisição e ferramenta agregada | Segurança e latência |
| P1 | Human-in-the-loop: endpoint para o aprovador registrar a decisão final e alimentar o golden set com divergências | Avaliação contínua com dados reais |
| P1 | Fila assíncrona + webhook ao ERP | Escala e resiliência a rate limit |
| P2 | OpenTelemetry (spans por estágio, `gen_ai.*`) e dashboards | Diagnóstico de latência |
| P2 | Tool calling limitado para documentos sob demanda (contratos, notas fiscais) | Contexto rico sem inflar o prompt |
| P2 | Batch API para reprocessamentos/backtesting (50% do custo) | FinOps |
| P3 | Experimentos A/B de versões de prompt em produção (shadow mode) | Evolução segura de skills |

## 4. Escalabilidade (conceitual)

- O serviço é **stateless** (o estado fica no banco), então escala horizontalmente atrás de um load balancer.
- O gargalo é o **rate limit do provedor**: o circuit breaker e o retry com jitter protegem; em volume alto, uma fila desacopla a entrada da capacidade de processamento.
- O cache de prompt reduz custo e latência no volume; a idempotência evita retrabalho.
- Fontes externas (ERP) com cache de curta duração (orçamento muda pouco ao longo do dia; cadastro de fornecedor, menos ainda).
