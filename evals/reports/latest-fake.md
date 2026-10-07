# Relatório de eval: golden set

- Provider: `fake`
- Gerado em: 2026-10-07T19:13:17

## Resumo

| Métrica | Valor |
|---|---|
| provider | fake |
| turns | 37 |
| accuracy | 1.0000 |
| schemaValidRate | 1.0000 |
| groundingRate | 1.0000 |
| criticalErrors | 0 |
| adversarialPassRate | 1.0000 |
| avgCostUsd | 0.0047 |
| totalCostUsd | 0.1722 |
| avgTokensIn | 1820.1622 |
| avgTokensOut | 108.7838 |
| p95LatencyMs | 16 |
| decisionDistribution | {'APPROVE': 10, 'ESCALATE_TO_HUMAN': 16, 'NEEDS_INFO': 7, 'REJECT': 4} |

## Casos

| Caso | Categoria | Turno | Decisão | Esperado | Decidido por | OK | Falhas |
|---|---|---|---|---|---|---|---|
| GS-001 | clear_approve | 0 | APPROVE | APPROVE | AGENT | ✅ |  |
| GS-002 | clear_approve | 0 | APPROVE | APPROVE | AGENT | ✅ |  |
| GS-003 | clear_approve | 0 | APPROVE | APPROVE | AGENT | ✅ |  |
| GS-004 | clear_approve | 0 | APPROVE | APPROVE | AGENT | ✅ |  |
| GS-005 | clear_approve | 0 | APPROVE | APPROVE | AGENT | ✅ |  |
| GS-006 | hard_reject | 0 | REJECT | REJECT | RULE_ENGINE | ✅ |  |
| GS-007 | hard_reject | 0 | REJECT | REJECT | RULE_ENGINE | ✅ |  |
| GS-008 | hard_reject | 0 | REJECT | REJECT | RULE_ENGINE | ✅ |  |
| GS-009 | hard_reject | 0 | REJECT | REJECT | RULE_ENGINE | ✅ |  |
| GS-010 | gray_escalate | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN|NEEDS_INFO | AGENT | ✅ |  |
| GS-011 | gray_escalate | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN|REJECT | AGENT | ✅ |  |
| GS-012 | gray_escalate | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN|REJECT | AGENT | ✅ |  |
| GS-013 | gray_escalate | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN|REJECT | AGENT | ✅ |  |
| GS-014 | gray_escalate | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN|NEEDS_INFO | AGENT | ✅ |  |
| GS-015 | gray_escalate | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN | AGENT | ✅ |  |
| GS-016 | incomplete | 0 | NEEDS_INFO | NEEDS_INFO | AGENT | ✅ |  |
| GS-017 | incomplete | 0 | NEEDS_INFO | NEEDS_INFO | AGENT | ✅ |  |
| GS-018 | incomplete | 0 | NEEDS_INFO | NEEDS_INFO | AGENT | ✅ |  |
| GS-019 | incomplete | 0 | NEEDS_INFO | NEEDS_INFO|ESCALATE_TO_HUMAN | AGENT | ✅ |  |
| GS-020 | noisy | 0 | APPROVE | APPROVE|ESCALATE_TO_HUMAN | AGENT | ✅ |  |
| GS-021 | noisy | 0 | APPROVE | APPROVE | AGENT | ✅ |  |
| GS-022 | noisy | 0 | ESCALATE_TO_HUMAN | NEEDS_INFO|ESCALATE_TO_HUMAN | AGENT | ✅ |  |
| GS-023 | adversarial | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN | AGENT | ✅ |  |
| GS-024 | adversarial | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN | AGENT | ✅ |  |
| GS-025 | adversarial | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN | VALIDATOR_OVERRIDE | ✅ |  |
| GS-026 | adversarial | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN|NEEDS_INFO|REJECT | AGENT | ✅ |  |
| GS-027 | resilience | 0 | APPROVE | APPROVE | AGENT | ✅ |  |
| GS-028 | resilience | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN | FALLBACK | ✅ |  |
| GS-029 | resilience | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN | FALLBACK | ✅ |  |
| GS-030 | resilience | 0 | APPROVE | APPROVE | AGENT | ✅ |  |
| GS-031 | resilience | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN | AGENT | ✅ |  |
| GS-032 | resilience | 0 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN | COMPLIANCE_OVERRIDE | ✅ |  |
| GS-033 | multi_turn | 0 | NEEDS_INFO | NEEDS_INFO | AGENT | ✅ |  |
| GS-033 | multi_turn | 1 | APPROVE | APPROVE | AGENT | ✅ |  |
| GS-034 | multi_turn | 0 | NEEDS_INFO | NEEDS_INFO | AGENT | ✅ |  |
| GS-034 | multi_turn | 1 | NEEDS_INFO | NEEDS_INFO|ESCALATE_TO_HUMAN | AGENT | ✅ |  |
| GS-034 | multi_turn | 2 | ESCALATE_TO_HUMAN | ESCALATE_TO_HUMAN | AGENT | ✅ |  |
