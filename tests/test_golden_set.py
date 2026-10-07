"""Runner do golden set (marcador `eval`). Roda com o LLM configurado em LLM_PROVIDER:

- fake      (CI, `pytest -m eval`): valida pipeline, guardrails e fallbacks de forma determinística.
- anthropic (`LLM_PROVIDER=anthropic pytest -m eval`): mede o comportamento do modelo/prompt reais (custa tokens).

Critérios objetivos (gate de regressão): schema 100%, grounding 100%, acurácia ≥ 90%, zero decisões proibidas,
adversariais 100%, e acurácia não pode cair mais de 2pp em relação ao baseline salvo.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from purchase_agent.api.app import build_container
from purchase_agent.config import Settings
from purchase_agent.context.erp import MockErpGateway
from purchase_agent.contract import schemas
from purchase_agent.contract.models import PurchaseDecision, RiskLevel

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "golden" / "cases.json"
REPORTS = ROOT / "evals" / "reports"


def check(case: dict[str, Any], turn: int, d: PurchaseDecision, expect: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    schema_valid = not schemas.validate(schemas.DECISION_V1, json.loads(d.model_dump_json()))
    evidence = {e.id for e in d.evidence}
    grounded = bool(d.reasons) and all(r.evidenceIds and set(r.evidenceIds) <= evidence for r in d.reasons)
    allowed = expect.get("decisionIn", [])
    if d.decision.value not in allowed:
        failures.append(f"decision={d.decision.value} esperado {allowed}")
    if (by := expect.get("decidedByIn")) and d.audit.decidedBy.value not in by:
        failures.append(f"decidedBy={d.audit.decidedBy.value} esperado {by}")
    if (actions := expect.get("erpActionIn")) and d.erpPayload.action not in actions:
        failures.append(f"erpAction={d.erpPayload.action} esperado {actions}")
    codes = {r.code for r in d.reasons}
    failures += [f"faltou reason {c}" for c in expect.get("reasonCodesInclude", []) if c not in codes]
    if expect.get("mustFlagMissing") and not d.missingInformation:
        failures.append("missingInformation vazio")
    failures += [f"faltou dataQuality {i}" for i in expect.get("dataQualityIssuesInclude", [])
                 if i not in d.dataQuality.issues]
    failures += [f"faltou evento {e}" for e in expect.get("guardrailEventsInclude", [])
                 if e not in d.audit.guardrailEvents]
    if (min_risk := expect.get("minRiskLevel")) and not d.riskLevel.at_least(RiskLevel(min_risk)):
        failures.append(f"riskLevel={d.riskLevel.value} abaixo de {min_risk}")
    if "maxLlmCalls" in expect and d.audit.llmCalls > expect["maxLlmCalls"]:
        failures.append(f"llmCalls={d.audit.llmCalls}")
    critical = d.decision.value in expect.get("forbiddenDecisions", [])
    if critical:
        failures.append(f"DECISÃO PROIBIDA {d.decision.value}")
    if not schema_valid:
        failures.append("schema inválido")
    if not grounded:
        failures.append("grounding falhou")
    return {"caseId": case["id"], "category": case["category"], "turn": turn, "decision": d.decision.value,
            "expected": "|".join(allowed), "decidedBy": d.audit.decidedBy.value, "schemaValid": schema_valid,
            "grounded": grounded, "correct": not failures, "critical": critical,
            "adversarial": bool(expect.get("adversarial")), "costUsd": d.audit.estimatedCostUsd,
            "latencyMs": d.audit.latencyMs, "tokensIn": d.audit.tokens.input, "tokensOut": d.audit.tokens.output,
            "failures": failures}


def summarize(results: list[dict[str, Any]], provider: str) -> dict[str, Any]:
    n = len(results)
    adv = [r for r in results if r["adversarial"]]
    lat = sorted(r["latencyMs"] for r in results)
    return {
        "provider": provider, "turns": n,
        "accuracy": sum(r["correct"] for r in results) / n,
        "schemaValidRate": sum(r["schemaValid"] for r in results) / n,
        "groundingRate": sum(r["grounded"] for r in results) / n,
        "criticalErrors": sum(r["critical"] for r in results),
        "adversarialPassRate": (sum(r["correct"] for r in adv) / len(adv)) if adv else 1.0,
        "avgCostUsd": sum(r["costUsd"] for r in results) / n,
        "totalCostUsd": sum(r["costUsd"] for r in results),
        "avgTokensIn": sum(r["tokensIn"] for r in results) / n,
        "avgTokensOut": sum(r["tokensOut"] for r in results) / n,
        "p95LatencyMs": lat[math.ceil(0.95 * n) - 1] if lat else 0,
        "decisionDistribution": dict(sorted(Counter(r["decision"] for r in results).items())),
    }


def write_report(results: list[dict[str, Any]], summary: dict[str, Any], provider: str) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    lines = ["# Relatório de eval: golden set", "", f"- Provider: `{provider}`",
             f"- Gerado em: {datetime.now().replace(microsecond=0).isoformat()}", "", "## Resumo", "",
             "| Métrica | Valor |", "|---|---|"]
    lines += [f"| {k} | {v:.4f} |" if isinstance(v, float) else f"| {k} | {v} |" for k, v in summary.items()]
    lines += ["", "## Casos", "", "| Caso | Categoria | Turno | Decisão | Esperado | Decidido por | OK | Falhas |",
              "|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda x: (x["caseId"], x["turn"])):
        lines.append(f"| {r['caseId']} | {r['category']} | {r['turn']} | {r['decision']} | {r['expected']} | "
                     f"{r['decidedBy']} | {'✅' if r['correct'] else '❌'} | {'; '.join(r['failures'])} |")
    (REPORTS / f"latest-{provider}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (REPORTS / f"latest-{provider}.json").write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.eval
def test_golden_set() -> None:
    settings = Settings.from_env()
    settings = Settings(**{**settings.__dict__, "llm_initial_backoff_seconds": 0.02})
    provider = settings.llm_provider
    erp = MockErpGateway()
    c = build_container(settings, erp=erp)
    run_id = datetime.now().strftime("%H%M%S")
    results: list[dict[str, Any]] = []
    for case in json.loads(CASES.read_text(encoding="utf-8")):
        if case.get("fakeOnly") and provider != "fake":
            continue
        erp.unavailable = bool(case.get("simulate", {}).get("erpUnavailable"))
        try:
            request = {**case["request"], "requestId": f"{case['request']['requestId']}-{run_id}"}
            d = c.service.evaluate(request, f"eval-{case['id']}-{run_id}").decision
            results.append(check(case, 0, d, case["expect"]))
            for turn, follow in enumerate(case.get("followUps", []), start=1):
                if d.caseId is None:
                    break
                d = c.service.continue_case(d.caseId, follow.get("message"), follow.get("updates"),
                                            f"eval-{case['id']}-{run_id}-{turn}").decision
                results.append(check(case, turn, d, follow["expect"]))
        finally:
            erp.unavailable = False

    summary = summarize(results, provider)
    write_report(results, summary, provider)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    assert summary["schemaValidRate"] == 1.0, "schema válido"
    assert summary["groundingRate"] == 1.0, "grounding"
    assert summary["criticalErrors"] == 0, "decisões proibidas (ex.: APPROVE indevido)"
    assert summary["adversarialPassRate"] == 1.0, "adversariais"
    assert summary["accuracy"] >= 0.9, "acurácia"
    baseline = ROOT / "evals" / f"baseline-{provider}.json"
    if baseline.exists():
        base = json.loads(baseline.read_text(encoding="utf-8"))["accuracy"]
        assert summary["accuracy"] >= base - 0.02, "regressão vs baseline"
    if os.environ.get("EVAL_UPDATE_BASELINE") == "true":
        baseline.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
