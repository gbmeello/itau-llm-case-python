"""Cliente Anthropic real contra um transporte HTTP simulado: valida o formato da requisição (o que vai para a API)
e o mapeamento de respostas e erros, sem chave e sem rede."""

import json

import anthropic
import httpx2
import pytest

from purchase_agent.llm.anthropic_client import AnthropicLlmClient
from purchase_agent.llm.client import LlmError, LlmErrorKind, LlmRequest

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


def message(content: list[dict], stop_reason: str = "end_turn") -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "content": content,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 80, "cache_read_input_tokens": 900,
                      "cache_creation_input_tokens": 0}}


def client_with(handler) -> AnthropicLlmClient:
    sdk = anthropic.Anthropic(api_key="test-key", base_url="http://anthropic.test", max_retries=0,
                              http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return AnthropicLlmClient(client=sdk)


def request(**kw) -> LlmRequest:
    return LlmRequest(kw.pop("purpose", "analyst"), "claude-sonnet-5-5", "SYSTEM ESTÁVEL", "<purchase_request>...",
                      SCHEMA, 4000, "medium", **kw)


def test_request_shape_structured_output_cache_and_no_sampling_params():
    seen = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(req.content))
        seen["_path"] = req.url.path
        return httpx2.Response(200, json=message([{"type": "text", "text": '{"ok": true}'}]))

    r = client_with(handler).complete(request())

    assert seen["_path"] == "/v1/messages"
    assert seen["model"] == "claude-sonnet-5-5" and seen["max_tokens"] == 4000
    assert seen["system"] == [{"type": "text", "text": "SYSTEM ESTÁVEL", "cache_control": {"type": "ephemeral"}}]
    assert seen["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "medium"}
    assert seen["messages"] == [{"role": "user", "content": "<purchase_request>..."}]
    assert not {"temperature", "top_p", "top_k", "tool_choice"} & seen.keys()
    assert r.text == '{"ok": true}' and r.input_tokens == 1200 and r.cache_read_tokens == 900


def test_tool_use_round_trip_and_forced_final_answer():
    seen = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(req.content))
        return httpx2.Response(200, json=message(
            [{"type": "tool_use", "id": "toolu_1", "name": "get_policy_text", "input": {"policy_id": "POL-COT-006"}}],
            stop_reason="tool_use"))

    tools = [{"name": "get_policy_text", "input_schema": {"type": "object"}}]
    r = client_with(handler).complete(request(tools=tools, forbid_tools=True,
                                              messages=[{"role": "user", "content": "x"}]))
    assert seen["tools"] == tools and seen["tool_choice"] == {"type": "none"}
    assert r.tool_calls[0].name == "get_policy_text" and r.tool_calls[0].input == {"policy_id": "POL-COT-006"}
    assert r.assistant_content[0]["type"] == "tool_use"


@pytest.mark.parametrize(("status", "kind"), [
    (429, LlmErrorKind.RATE_LIMITED), (529, LlmErrorKind.OVERLOADED), (500, LlmErrorKind.SERVER_ERROR),
    (400, LlmErrorKind.BAD_REQUEST), (401, LlmErrorKind.AUTH),
])
def test_maps_http_errors_to_retryable_or_not(status, kind):
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "simulado"}})

    with pytest.raises(LlmError) as e:
        client_with(handler).complete(request())
    assert e.value.kind == kind
    assert e.value.retryable == (kind in {LlmErrorKind.RATE_LIMITED, LlmErrorKind.OVERLOADED, LlmErrorKind.SERVER_ERROR})


def test_refusal_is_an_error_not_a_decision():
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message([], stop_reason="refusal"))

    with pytest.raises(LlmError) as e:
        client_with(handler).complete(request())
    assert e.value.kind == LlmErrorKind.REFUSAL
