import json
from decimal import Decimal

from conftest import TODAY, request

from purchase_agent.agent.validator import DecisionValidator, money_mentions
from purchase_agent.context.builder import AgentContext, ContextItem, Layer
from purchase_agent.contract.models import Decision, LlmAssessment, Reason, RiskLevel, Severity
from purchase_agent.intake.normalizer import normalize
from purchase_agent.policy.engine import PolicyCheck, PolicyOutcome, Result

V = DecisionValidator(min_approve_confidence=0.7)
CTX = AgentContext(
    (ContextItem("EV-REQ", Layer.P0_REQUEST, "req", "Solicitação: total R$ 78.000,00", {"totalAmount": "78000.00"}),
     ContextItem("EV-HIST-STATS", Layer.P3_HISTORY, "hist", "média R$ 25.100,00")),
    (), {}, 100, 8000)


def output(decision="ESCALATE_TO_HUMAN", explanation="acima", evidence="EV-HIST-STATS") -> str:
    return json.dumps({"decision": decision, "riskLevel": "HIGH", "riskScore": 70, "confidence": 0.8,
                       "summary": "Resumo.", "missingInformation": [],
                       "reasons": [{"code": "AMOUNT_ABOVE_HISTORICAL", "severity": "HIGH",
                                    "explanation": explanation, "evidenceIds": [evidence]}]})


def assessment(decision=Decision.APPROVE, confidence=0.9) -> LlmAssessment:
    return LlmAssessment(decision=decision, riskLevel=RiskLevel.LOW, riskScore=10, confidence=confidence, summary="ok",
                         reasons=[Reason(code="WITHIN_POLICY", severity=Severity.LOW, explanation="ok",
                                         evidenceIds=["EV-REQ"])], missingInformation=[])


def test_accepts_grounded_output():
    assert V.parse_and_check(output(explanation="R$ 78.000 contra média de R$ 25.100"), CTX).ok


def test_rejects_non_json():
    p = V.parse_and_check("Claro! A compra parece ok.", CTX)
    assert not p.ok and "JSON" in p.repairable_errors[0]


def test_rejects_unknown_evidence_ids():
    assert any("EV-999" in e for e in V.parse_and_check(output(evidence="EV-999"), CTX).repairable_errors)


def test_rejects_monetary_values_not_present_in_evidence():
    errors = V.parse_and_check(output(explanation="Preço de mercado é R$ 31.000"), CTX).repairable_errors
    assert any("31000" in e for e in errors)


def test_rejects_schema_violations():
    p = V.parse_and_check('{"decision":"MAYBE"}', CTX)
    assert not p.ok and all(e.startswith("Schema") for e in p.repairable_errors)


def test_approve_against_blocking_policy_is_overridden_to_escalate():
    blocking = PolicyOutcome("1.0.0", (PolicyCheck("POL-FORN-002", Result.BLOCKS_APPROVAL, "SUPPLIER_HIGH_RISK", "x"),),
                             0, "a", ())
    r = V.enforce(assessment(), blocking, normalize(request(), TODAY))
    assert r.overridden and r.assessment.decision == Decision.ESCALATE_TO_HUMAN
    assert "APPROVE_BLOCKED_BY_POLICY" in r.events


def test_low_confidence_approve_is_overridden():
    r = V.enforce(assessment(confidence=0.5), PolicyOutcome("1.0.0", (), 0, "a", ()), normalize(request(), TODAY))
    assert "APPROVE_LOW_CONFIDENCE" in r.events and r.overridden


def test_parses_brazilian_money_formats():
    assert money_mentions("R$ 78.000,00 e R$ 2.100 e R$ 950") == [Decimal("78000.00"), Decimal("2100"), Decimal("950")]
