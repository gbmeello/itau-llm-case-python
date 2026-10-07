# ADR-0007: LLM fake determinístico + golden set + eval real com gate

- **Status:** aceito · **Data:** 2026-10-06

## Contexto
Testes que chamam um LLM real são lentos, caros e não determinísticos, e o CI não deve depender de chave de API. Ainda assim, precisamos testar caminhos de falha que o modelo real raramente produz (JSON inválido, alucinação, provedor fora, obediência à injeção).

## Decisão
- `LlmClient` como interface com duas implementações: `llm/anthropic_client.py` (real) e `llm/fake.py`.
- O fake é um **analista heurístico** que lê as evidências estruturadas exatamente como o modelo as recebe (o que também testa a montagem do prompt) e **injeta falhas por marcador** no `requestId` (`FAKE-INVALID-JSON`, `FAKE-HALLUCINATE`, `FAKE-DOWN`, `FAKE-OBEY`…).
- O mesmo golden set roda nos dois modos: ``pytest -m eval` (fake)` (CI, valida o sistema) e ``LLM_PROVIDER=anthropic pytest -m eval`` (manual/nightly, valida o modelo), com critérios objetivos e comparação com baseline.
- O fake reporta custo como se fosse o modelo real (prefixo `fake:`), o que permite exercitar o FinOps.

## Consequências
- (+) CI rápido (segundos), determinístico e gratuito; caminhos de falha cobertos.
- (−) **Risco de "teste que passa por construção":** o fake foi escrito junto com o golden set. Por isso o relatório do fake **não** é apresentado como medida de qualidade do modelo, e o eval real é obrigatório antes de produção.
