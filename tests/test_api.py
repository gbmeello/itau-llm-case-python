from conftest import ACME, BLOCKED, body
from fastapi.testclient import TestClient

from purchase_agent.api.app import create_app
from purchase_agent.context.mcp_gateway import McpErpGateway
from purchase_agent.mcp_server.erp_server import server

EVAL = "/v1/purchase-requests/evaluate"


def test_rejects_missing_api_key(client):
    assert client.post(EVAL, json={}).status_code == 401


def test_contract_violation_returns_400_with_details(client, auth):
    r = client.post(EVAL, json={"requestId": "X", "items": []}, headers=auth)
    assert r.status_code == 400
    assert r.json()["code"] == "CONTRACT_VIOLATION" and r.json()["details"]


def test_malformed_json_returns_400(client, auth):
    r = client.post(EVAL, content=b"{nope", headers={**auth, "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["code"] == "MALFORMED_JSON"


def test_approves_small_purchase_with_grounded_explanation_and_audit_trail(client, auth):
    payload = body()
    r = client.post(EVAL, json=payload, headers=auth)
    assert r.status_code == 200
    assert r.headers["X-Idempotent-Replay"] == "false" and r.headers["X-Trace-Id"]
    d = r.json()
    assert d["decision"] == "APPROVE"
    assert d["erpPayload"]["action"] == "AUTO_APPROVE"
    assert d["audit"]["skillVersions"]["analyst"] == "1.1.0"
    assert d["reasons"][0]["evidenceIds"]
    assert "Ana Souza" not in r.text  # PII não vaza para contexto/saída

    audit = client.get(f"/v1/decisions/{d['audit']['decisionId']}", headers=auth).json()
    assert audit["contextIncluded"] and audit["decisionPayload"]["requestId"] == payload["requestId"]


def test_identical_request_is_replayed_without_new_llm_call(client, auth):
    payload = body()
    first = client.post(EVAL, json=payload, headers=auth).json()
    again = client.post(EVAL, json=payload, headers=auth)
    assert again.headers["X-Idempotent-Replay"] == "true"
    assert again.json()["audit"]["decisionId"] == first["audit"]["decisionId"]


def test_blocked_supplier_is_rejected_by_rules_without_calling_llm(client, auth):
    d = client.post(EVAL, json=body(supplier=BLOCKED, qty=1, price=100.0), headers=auth).json()
    assert d["decision"] == "REJECT" and d["audit"]["decidedBy"] == "RULE_ENGINE"
    assert d["audit"]["llmCalls"] == 0 and d["audit"]["estimatedCostUsd"] == 0


def test_llm_outage_falls_back_to_human_review_with_valid_contract(client, auth):
    d = client.post(EVAL, json=body(request_id="PR-FAKE-DOWN-1", qty=1, price=100.0), headers=auth).json()
    assert d["decision"] == "ESCALATE_TO_HUMAN"
    assert d["audit"]["decidedBy"] == "FALLBACK" and d["audit"]["fallbackReason"] == "LLM_OVERLOADED"


def test_needs_info_opens_case_and_follow_up_resolves_it(client, auth):
    first = client.post(EVAL, json=body(supplier=None, qty=3,
                                        justification="Monitores para os novos analistas de infraestrutura."),
                        headers=auth).json()
    assert first["decision"] == "NEEDS_INFO" and first["caseId"]
    second = client.post(f"/v1/cases/{first['caseId']}/messages", headers=auth, json={
        "message": "Fornecedor é a Acme, homologada.",
        "updates": {"supplier": {"taxId": ACME, "name": "Acme Hardware Ltda"}}}).json()
    assert second["decision"] == "APPROVE" and second["caseId"] == first["caseId"]
    case = client.get(f"/v1/cases/{first['caseId']}", headers=auth).json()
    assert case["status"] == "CLOSED" and case["round"] == 2


def test_skill_crud_is_versioned_and_traceable_in_decisions(client, auth):
    content = "---\nid: analyst\nversion: 9.0.0\n---\nVocê é o Analista de Risco. Use só as evidências."
    r = client.post("/v1/skills/analyst/versions", headers=auth,
                    json={"version": "9.0.0", "content": content, "changelog": "teste", "activate": True})
    assert r.status_code == 201 and r.json()["status"] == "ACTIVE"
    d = client.post(EVAL, json=body(qty=1, price=100.0), headers=auth).json()
    assert d["audit"]["skillVersions"]["analyst"] == "9.0.0"
    # versões são imutáveis
    assert client.post("/v1/skills/analyst/versions", headers=auth,
                       json={"version": "9.0.0", "content": "x"}).status_code == 409
    # rollback
    assert client.post("/v1/skills/analyst/versions/1.1.0/activate", headers=auth).json()["status"] == "ACTIVE"
    # skill essencial não pode ser removida
    assert client.delete("/v1/skills/analyst", headers=auth).status_code == 409
    # create + delete lógico de skill opcional
    assert client.post("/v1/skills", headers=auth,
                       json={"skillId": "tmp-examples", "type": "EXAMPLES", "content": "exemplo"}).status_code == 201
    assert client.delete("/v1/skills/tmp-examples", headers=auth).status_code == 204
    assert client.get("/v1/skills/tmp-examples", headers=auth).json()[0]["status"] == "DELETED"


def test_exposes_llm_and_agent_metrics(client, auth):
    client.post(EVAL, json=body(qty=1, price=100.0), headers=auth)
    text = client.get("/metrics").text
    for name in ("llm_tokens_total", "llm_cost_usd_total", "agent_decisions_total", "context_tokens_by_layer"):
        assert name in text


def test_full_pipeline_over_mcp_erp(settings, auth):
    """Mesmo agente, ERP consumido via MCP: a troca de fonte não muda a decisão."""
    mcp_client = TestClient(create_app(settings, erp=McpErpGateway(server)))
    d = mcp_client.post(EVAL, json=body(), headers=auth).json()
    assert d["decision"] == "APPROVE"
    assert {"EV-BUD", "EV-SUP", "EV-HIST-STATS"} <= {e["id"] for e in d["evidence"]}
    blocked = mcp_client.post(EVAL, json=body(supplier=BLOCKED), headers=auth).json()
    assert blocked["decision"] == "REJECT" and blocked["audit"]["decidedBy"] == "RULE_ENGINE"
