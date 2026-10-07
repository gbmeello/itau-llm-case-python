# Estratégia de testes e evals

## 1. Pirâmide

| Nível | O que cobre | Onde | Roda em |
|---|---|---|---|
| **Unitário** | Intake (normalização, CNPJ, PII, injeção), motor de regras (alçadas, bloqueio, orçamento, fracionamento, categoria, ERP fora), context builder (prioridade, teto por camada, corte de exemplos), validador (JSON, schema, IDs, valores R$, guardrails), resiliência (retry só em erro transitório, custo) | `tests/` | `pytest` |
| **Integração** | API ponta a ponta com LLM fake: autenticação, contrato 400, aprovação com auditoria, idempotência, regra sem LLM, fallback por indisponibilidade, multi-turno, CRUD de skills com rastreabilidade, métricas | `tests/test_api.py` | `pytest` |
| **Contrato** | Toda decisão é validada contra `purchase-decision.v1.json` em runtime **e** no eval; a entrada, contra `purchase-request.v1.json` | `contract/schemas.py` | sempre |
| **Eval: pipeline** | Golden set com LLM fake: guardrails, fallbacks e falhas injetadas (JSON inválido, alucinação, provedor fora, modelo que obedece à injeção, compliance discordando) | `tests/test_golden_set.py` | `pytest -m eval` (CI) |
| **Eval: modelo** | Mesmo golden set com o Claude real: qualidade de julgamento e aderência ao prompt | `tests/test_golden_set.py` | `LLM_PROVIDER=anthropic pytest -m eval` (manual/nightly, custa tokens) |

## 2. Golden set ([evals/golden/cases.json](../evals/golden/cases.json))

34 casos (37 turnos), cada um com expectativas objetivas: `decisionIn` (conjunto aceito, já que alguns casos admitem mais de uma resposta correta), `forbiddenDecisions`, `decidedByIn`, `reasonCodesInclude`, `erpActionIn`, `mustFlagMissing`, `dataQualityIssuesInclude`, `minRiskLevel`, `guardrailEventsInclude` e `maxLlmCalls`.

| Categoria | Casos | Exemplos |
|---|---|---|
| Aprovação clara | 5 | Monitores na alçada automática; renovação em contrato-quadro |
| Rejeição por regra | 4 | Fornecedor bloqueado (mesmo com justificativa forte e urgência); orçamento estourado |
| Zona cinza → humano | 6 | 3× a média sem cotação; consultoria nova com conflito de interesses; fracionamento; brindes; fornecedor não homologado; acima do limite do agente |
| Incompletos | 4 | Sem fornecedor, sem preço, sem justificativa, CNPJ inválido |
| Ruidosos | 3 | Total divergente + moeda ausente; inglês com erros; centro de custo inexistente |
| Adversariais | 4 | Injeção direta; quebra de tag `</untrusted_input>`; modelo ingênuo que obedece (fake); engenharia social sem padrão detectável |
| Resiliência | 6 | JSON inválido → reparo; sempre inválido → fallback; provedor fora; alucinação → reparo; ERP fora; compliance discorda |
| Multi-turno | 2 | NEEDS_INFO → complemento → APPROVE; nunca informa preço → escala após 3 rodadas |

Casos `fakeOnly` dependem de falhas injetadas e só rodam com o fake.

## 3. Critérios objetivos de sucesso (gate)

| Critério | Meta | Por quê |
|---|---|---|
| Validade de schema | **100%** | O ERP depende do contrato |
| Grounding (toda razão cita evidência existente) | **100%** | Explicabilidade e anti-alucinação |
| Decisões proibidas (ex.: APPROVE em caso de bloqueio) | **0** | É o erro caro (financeiro e de compliance) |
| Adversariais | **100%** | Segurança |
| Acurácia (todas as expectativas do turno) | **≥ 90%** | Qualidade geral; a margem cobre a ambiguidade legítima da zona cinza |
| Regressão vs. baseline | acurácia ≥ baseline − 2 pp | Mudança de prompt ou modelo não pode piorar |
| Custo médio e p95 de latência | Registrados no relatório e comparados ao baseline | FinOps e SLO |

O relatório é gravado em `evals/reports/latest-<provider>.md|json`. O baseline é salvo com `EVAL_UPDATE_BASELINE=true` (`evals/baseline-<provider>.json`).

## 4. Estratégia de regressão quando prompt ou modelo mudam

1. A mudança de prompt entra como **nova versão de skill** (arquivo + changelog), e a versão anterior continua disponível para rollback.
2. O CI roda ``pytest -m eval` (fake)`: garante que o pipeline e os guardrails continuam íntegros. É barato e determinístico.
3. Antes de ativar em produção: ``LLM_PROVIDER=anthropic pytest -m eval`` com o modelo real, comparando com o baseline. Se algum critério crítico piorar, a versão não é ativada.
4. A troca de modelo (ex.: Sonnet 5.5 → sucessor) segue o mesmo fluxo, com o modelo como parâmetro (`AGENT_ANALYST_MODEL`).
5. Em produção, as métricas de drift (distribuição de decisões, overrides, grounding) funcionam como "eval contínuo". Casos reais divergentes, revisados por humanos, são incorporados ao golden set.

## 5. O que o eval com fake prova, e o que não prova

- **Prova:** contratos, grounding, guardrails, fallbacks, idempotência, multi-turno, contabilização de custo, e que o prompt e o contexto são montados como esperado (o fake lê as evidências exatamente como o modelo as recebe).
- **Não prova:** que o Claude toma as decisões certas. Até a entrega, não havia chave de API, então o ``LLM_PROVIDER=anthropic pytest -m eval`` com modelo real **não foi executado**. É a primeira ação pendente (custo estimado < US$ 0,50 por execução completa).
