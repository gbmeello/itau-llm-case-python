from datetime import date

from conftest import ACME, API_KEY, TODAY, body, request
from fastapi.testclient import TestClient

from purchase_agent import resources
from purchase_agent.agent.tools import ToolExecutor
from purchase_agent.api.app import create_app
from purchase_agent.api.ratelimit import TokenBucketLimiter
from purchase_agent.config import Settings
from purchase_agent.context.erp import MockErpGateway
from purchase_agent.intake import pii
from purchase_agent.intake.normalizer import normalize
from purchase_agent.policy.engine import PolicyConfig

EVAL = "/v1/purchase-requests/evaluate"
AUTH = {"X-API-Key": API_KEY}
CFG = PolicyConfig.model_validate_json(
    resources.path("skills", "policies", "purchase-policies", "1.0.0.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ PII

def test_masks_cpf_email_phone_and_card_but_keeps_cnpj():
    text = ("Falar com joao.silva@empresa.com, CPF 123.456.789-09, tel (11) 91234-5678, cartão 4111 1111 1111 1111. "
            "Fornecedor CNPJ 11.222.333/0001-81, pedido de 10 unidades.")
    out, kinds = pii.mask(text)
    assert kinds == ["CARTAO", "CPF", "EMAIL", "TELEFONE"]
    for leaked in ("joao.silva@empresa.com", "123.456.789-09", "91234-5678", "4111 1111 1111 1111"):
        assert leaked not in out
    assert "11.222.333/0001-81" in out and "10 unidades" in out


def test_pii_never_reaches_the_llm_context():
    n = normalize(request(justification="Contato do solicitante: maria@x.com, CPF 52998224725. Compra urgente."),
                  TODAY)
    assert "maria@x.com" not in n.justification and "52998224725" not in n.justification
    assert "PII_MASKED:CPF,EMAIL" in n.data_quality_issues


def test_pii_is_masked_in_api_output(client, auth):
    r = client.post(EVAL, headers=auth, json=body(justification="Monitores novos. Dúvidas: ana@corp.com.br"))
    assert "ana@corp.com.br" not in r.text


# ------------------------------------------------------------------ rate limit

def test_token_bucket_refills_over_time():
    now = [0.0]
    limiter = TokenBucketLimiter(per_minute=60, burst=2, clock=lambda: now[0])
    assert limiter.acquire("k") is None and limiter.acquire("k") is None
    wait = limiter.acquire("k")
    assert wait is not None and 0 < wait <= 1
    assert limiter.acquire("other") is None  # buckets são por cliente
    now[0] = 1.0
    assert limiter.acquire("k") is None


def test_api_returns_429_with_retry_after_and_metric():
    client = TestClient(create_app(Settings(api_key=API_KEY, rate_limit_per_minute=2)))
    codes = [client.post(EVAL, headers=AUTH, json=body()).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = client.post(EVAL, headers=AUTH, json=body())
    assert r.json()["code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) >= 1
    assert client.get("/v1/skills", headers=AUTH).status_code == 200  # leitura não consome LLM: não é limitada
    assert 'api_rate_limited_total{route="evaluate"}' in client.get("/metrics").text


# ------------------------------------------------------------------ tool calling

def tool_client() -> TestClient:
    return TestClient(create_app(Settings(api_key=API_KEY, tool_calling=True, llm_initial_backoff_seconds=0.001)))


def test_analyst_uses_tool_and_cites_tool_evidence():
    d = tool_client().post(EVAL, headers=AUTH, json=body(request_id="PR-FAKE-TOOLS-1")).json()
    assert d["decision"] == "APPROVE"
    assert "TOOL_ROUND_1" in d["audit"]["guardrailEvents"]
    assert "EV-T1" in {e["id"] for e in d["evidence"]}
    assert any("EV-T1" in r["evidenceIds"] for r in d["reasons"])  # grounding aceita a evidência da ferramenta
    assert d["audit"]["llmCalls"] == 2


def test_tool_loop_is_bounded_and_tool_errors_go_back_to_the_model():
    c = tool_client()
    d = c.post(EVAL, headers=AUTH, json=body(request_id="PR-FAKE-TOOL-LOOP-1")).json()
    assert [e for e in d["audit"]["guardrailEvents"] if e.startswith("TOOL_ROUND")] == [
        "TOOL_ROUND_1", "TOOL_ROUND_2", "TOOL_ROUND_3"]
    assert d["audit"]["llmCalls"] == 4  # 3 rodadas + resposta final forçada (tool_choice none)
    assert d["decision"] in ("APPROVE", "ESCALATE_TO_HUMAN")
    assert 'agent_tool_calls_total{outcome="error",tool="get_policy_text"} 3.0' in c.get("/metrics").text


def test_tool_executor_validates_input_and_budget():
    ex = ToolExecutor(MockErpGateway(), normalize(request(), TODAY), CFG, date(2026, 10, 6))
    assert ex.execute("get_purchase_history_details", {"category": "IT_HARDWARE", "supplier_tax_id": "",
                                                       "months": 99}).is_error
    assert ex.execute("drop_table", {}).is_error
    assert ex.execute("get_supplier_profile", {"tax_id": "123"}).is_error
    ok = ex.execute("get_supplier_profile", {"tax_id": ACME})
    assert not ok.is_error and ok.evidence.id == "EV-T1"
    assert ex.execute("get_policy_text", {"policy_id": "pol-cot-006"}).evidence.id == "EV-T2"


def test_tools_go_through_mcp_when_enabled():
    from purchase_agent.context.mcp_gateway import McpErpGateway
    from purchase_agent.mcp_server.erp_server import server
    c = TestClient(create_app(Settings(api_key=API_KEY, tool_calling=True), erp=McpErpGateway(server)))
    d = c.post(EVAL, headers=AUTH, json=body(request_id="PR-FAKE-TOOLS-MCP")).json()
    assert "EV-T1" in {e["id"] for e in d["evidence"]}
