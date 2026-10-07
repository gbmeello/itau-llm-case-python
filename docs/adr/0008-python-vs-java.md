# ADR-0008: Implementação em Python (e relação com a versão Java)

- **Status:** aceito · **Data:** 2026-10-07

## Contexto
A primeira implementação foi feita em Java 21 + Spring Boot ([gbmeello/itau-llm-case](https://github.com/gbmeello/itau-llm-case)). Na definição da stack, Python havia sido a recomendação técnica, pela maturidade do ecossistema de LLM. Esta versão reimplementa a mesma solução em Python.

## Decisão
Python 3.12 + FastAPI + Pydantic v2 + SDK `anthropic` 1.x + `mcp` 2.x + SQLAlchemy 2 + prometheus-client + pytest, mantendo **idênticos**: arquitetura, contratos (JSON Schemas v1), skills (prompts e políticas), dados sintéticos, golden set e critérios de eval.

## Comparação
| Aspecto | Java / Spring | Python / FastAPI |
|---|---|---|
| Ecossistema LLM/MCP | SDK oficial Anthropic em Java; MCP disponível, menos maduro | SDKs de referência (Anthropic e MCP); o MCP foi implementado aqui |
| Contratos | Records + JSON Schema | Pydantic (tipagem + validação) + JSON Schema |
| Concorrência | Threads (Tomcat) | Handlers síncronos em threadpool (FastAPI); o SDK também tem cliente assíncrono para escalar I/O |
| Resiliência | Resilience4j | Retry e circuit breaker próprios (~60 linhas, testados), sem dependência extra |
| Observabilidade | Micrometer/Actuator | prometheus-client + logs JSON |
| Velocidade de iteração | Build Maven ~20–40 s | Testes em ~4 s |
| Aderência a stack corporativa | Alta em bancos (JVM) | Comum em times de dados e IA |

## Consequências
- A mesma bateria de verificação passa nas duas versões, inclusive o golden set com 37 turnos (100% contra o fake). Isso evidencia que o comportamento é definido pelos **contratos, regras e skills**, não pela linguagem.
- Os artefatos de IA (prompts, políticas, golden set) são portáveis entre linguagens; um repositório compartilhado de skills e evals seria a evolução natural.
- Em produção, a escolha dependeria da plataforma do time; a arquitetura não depende dela.
