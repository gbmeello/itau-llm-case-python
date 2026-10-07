# Uso de IA na construção do case

> O case permite o uso de IA e pede que ele seja explícito. Este documento registra **como, quando e com quais controles** a IA foi usada, e o que foi decisão humana.

## Ferramentas

- **Claude Code** (desktop app, modelo Claude Opus 5.5) como par de programação agêntico, com acesso ao terminal e aos arquivos.
- **Workspace [`agent-skills`](https://github.com/addyosmani/agent-skills)**: skills de engenharia (spec → plan → build → test → review → ship) que guiaram o processo com gates de aprovação.
- **Skill `claude-api`** (referência oficial dos SDKs Anthropic) para usar as APIs corretas do SDK Python 1.x, sem depender de memória desatualizada.

## Linha do tempo

### Versão Java (06/10): [gbmeello/itau-llm-case](https://github.com/gbmeello/itau-llm-case)
| Fase | O que a IA fez | Decisão/validação humana |
|---|---|---|
| Entendimento | Extraiu o texto do PDF do case e mapeou requisito → solução | — |
| Decisões de alto nível | Apresentou opções com trade-offs (cenário, stack, LLM, local) e **recomendou Python** | **Humano escolheu:** cenário 03, **Java + Spring Boot**, Claude, repositório separado |
| Especificação (`/spec`) | Escreveu o SPEC.md com premissas, mapa de capacidades, contratos, budget e critérios | **Humano aprovou a spec** antes de qualquer código |
| Implementação, testes, docs | Código por módulo, 36 testes, golden set (34 casos), 7 ADRs, demo real | Prazo, repositório e visibilidade definidos pelo humano |

### Versão Python (07/10): este repositório
| Fase | O que a IA fez | Decisão/validação humana |
|---|---|---|
| Decisão | — | **Humano decidiu** fazer também a versão em Python (a recomendação original) em um repositório novo, "seguindo o mesmo padrão" |
| Ambiente | Instalou Python 3.12 (winget, hash verificado) e consultou as versões atuais no PyPI | Autorizado pelo humano |
| Pesquisa de API | Leu a referência do SDK `anthropic` **1.x** (`output_config.format`, `temperature` removido, hierarquia de exceções) e inspecionou o pacote `mcp` **2.x** instalado: `FastMCP` virou `MCPServer`, e o `Client` aceita uma instância do servidor in-process (base dos testes de MCP) | — |
| Port | Reaproveitou os artefatos de IA **sem alteração** (schemas, prompts, políticas, golden set, exemplos) e reimplementou o código em Python, módulo por módulo | — |
| **Diferencial novo** | Implementou o servidor MCP `erp-mcp` e o `McpErpGateway`, mais testes do pipeline completo via MCP e uma demo com o servidor em processo separado | — |
| Verificação | 42 testes, golden set (37 turnos, 100%), demo real (API + MCP em processos separados) | — |
| Docs | Adaptou a documentação Java (substituições mecânicas + revisão), reescreveu README, ADR-0006 (MCP agora implementado) e criou ADR-0008 (Python vs Java) | — |

## Problemas encontrados e corrigidos pela verificação

| Problema | Como foi detectado | Correção |
|---|---|---|
| *(Java)* Método inexistente numa exceção genérica | Erro de compilação | Tipo correto (`JsonProcessingException`) |
| *(Java)* Endpoint de métricas vazio nos testes | Teste de integração | `@AutoConfigureObservability` |
| *(Java/Python)* Fake considerava "Não precisa de **cotação**" como menção positiva a cotações | Revisão do caso adversarial GS-026 | Regex exige menção afirmativa |
| *(Java)* Timestamps de auditoria usando o "hoje" fixo das regras | Execução real do demo | Relógio fixo só para regras; auditoria em tempo real |
| *(Java)* Acentos corrompidos no `curl` do Git Bash | Execução do demo (400) | Payload em arquivo UTF-8 + `--data-binary` |
| *(Java)* Chamada ao LLM dentro de transação de banco em `continueCase` | Revisão de código (`/review`) | Transação removida; LLM fora da transação (a versão Python já nasceu assim) |
| *(Java)* Documentação dizia "38 turnos" no golden set | Comparação entre os relatórios Java e Python | Corrigido para 37 nos dois repositórios |
| *(Python)* SQLite descarta o fuso horário dos timestamps | Execução real do demo de skills | Normalização para UTC na resposta (`_iso`) |
| *(Ambos)* Prompt `analyst` v1.0.0 conflitava com o validador de grounding (valores calculados) | Revisão cruzada prompt × validador | `analyst@1.1.0` |


## Rodada 3 (07/10): revisão de lacunas e fechamento

A pedido do candidato ("existe algum ponto que está faltando?"), a IA comparou o entregue com o PDF do case e com a própria SPEC e listou 10 lacunas, com prioridade. O candidato pediu que todas fossem resolvidas (exceto reescrever o histórico git, que exige confirmação explícita).

| Lacuna encontrada | O que foi feito | Onde |
|---|---|---|
| Spec prometia mascarar PII no texto livre; só o nome era descartado | Mascaramento de CPF, e-mail, telefone e cartão (Luhn) antes do LLM, inclusive nas mensagens de rodada de caso | Java e Python |
| Spec prometia rate limit; não existia | Token bucket por API key nas rotas que consomem LLM (`429` + `Retry-After`, métrica) | Java e Python |
| Spec citava docker compose; não havia Docker | Dockerfile (não-root) + compose (PostgreSQL + API + Prometheus; no Python também o servidor MCP), validado no CI de ponta a ponta | Java e Python |
| Cliente Anthropic real nunca testado | Testes contra HTTP simulado: formato da requisição e mapeamento de erros/recusa, sem chave | Java e Python |
| Alertas só descritos | `deploy/prometheus/alerts.yml` (9 regras com runbook), validado com `promtool` no CI | Java e Python |
| Economia do prompt caching afirmada sem verificação | Documentação corrigida: depende do prefixo mínimo do modelo; como verificar e o que fazer se não cachear | Java e Python |
| Tool calling com retry (diferencial do PDF) ausente | Tool calling opcional do analista: 3 ferramentas somente leitura, máx. 3 rodadas, `is_error`, evidências EV-T* com grounding, via MCP | Python |
| Texto "_motivo: preencher_" público no AI-USAGE | Reescrito de forma neutra | Java |

**Problemas que a verificação pegou nesta rodada:**
- A regex de CPF não reconhecia um CPF seguido de ponto final ("…24725."), que acabava mascarado como telefone. O teste falhou e a regex foi corrigida nas duas versões.
- *(Java)* `@Lob String` no Hibernate 6 + PostgreSQL vira `oid` (large object) e exige transação na leitura. O problema foi identificado antes de rodar em Postgres, e as colunas viraram texto longo portável.
- A edição em lote de arquivos com CRLF não casava as expressões regulares; as alterações foram refeitas e conferidas uma a uma.

## Controles aplicados sobre o que a IA produziu

- **Gates por fase:** spec aprovada antes do código; port feito sobre a mesma spec.
- **Verificação executável:** nada foi declarado "pronto" sem rodar testes, golden set e demo real.
- **Paridade comprovada:** o mesmo golden set (sem alterações) produz o mesmo resultado nas duas linguagens.
- **Honestidade sobre limites:** o eval com o Claude real **não** foi executado (sem chave de API). O resultado do fake não é apresentado como qualidade do modelo.
- **Commits com coautoria** (`Co-Authored-By: Claude`).

## O que eu (candidato) devo dominar para a entrevista

- Por que o LLM não decide sozinho e onde estão as regras rígidas (`policy/engine.py`, `agent/validator.py::enforce`).
- Como funciona o grounding (`agent/validator.py::parse_and_check`) e o que ele **não** cobre.
- O algoritmo de seleção de contexto (`context/builder.py::select`) e o budget por camada.
- O fluxo de fallback e os pontos em que a decisão vira `ESCALATE_TO_HUMAN`.
- Por que structured outputs e não tool use forçado.
- MCP: como o servidor e o cliente funcionam, por que as ferramentas são chamadas pelo código e não pelo modelo, e os trade-offs (ADR-0006).
- Java vs Python: o que mudou e o que não mudou (ADR-0008).

> _Espaço para anotações pessoais de revisão do candidato antes da apresentação._
