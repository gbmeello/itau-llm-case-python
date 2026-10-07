from datetime import date

import pytest

from purchase_agent.context.erp import ContextSourceUnavailable
from purchase_agent.context.mcp_gateway import McpErpGateway
from purchase_agent.llm.client import LlmError, LlmErrorKind, LlmRequest, LlmResponse
from purchase_agent.llm.resilient import CircuitBreaker, ResilientLlmClient
from purchase_agent.mcp_server.erp_server import server
from purchase_agent.observability.metrics import Metrics

REQ = LlmRequest("analyst", "m", "s", "u", None, 100)
OK = LlmResponse("{}", "claude-sonnet-5-5", 1000, 100, 0, 0, 10, "end_turn")


class Flaky:
    def __init__(self, failures: int, kind: LlmErrorKind) -> None:
        self.calls, self.failures, self.kind = 0, failures, kind

    def complete(self, _: LlmRequest) -> LlmResponse:
        self.calls += 1
        if self.calls <= self.failures:
            raise LlmError(self.kind, "falha")
        return OK


def sample(metrics: Metrics, name: str, labels: dict) -> float:
    return metrics.registry.get_sample_value(name, labels) or 0.0


def test_retries_transient_errors_then_succeeds_and_records_cost():
    m = Metrics()
    flaky = Flaky(2, LlmErrorKind.RATE_LIMITED)
    client = ResilientLlmClient(flaky, m, max_attempts=3, initial_backoff=0, sleep=lambda _: None)
    assert client.complete(REQ) == OK
    assert flaky.calls == 3
    assert sample(m, "llm_retries_total", {"type": "RATE_LIMITED"}) == 2
    assert sample(m, "llm_cost_usd_total", {"purpose": "analyst", "model": "claude-sonnet-5-5"}) == pytest.approx(
        (1000 * 2.0 + 100 * 10.0) / 1_000_000)


def test_does_not_retry_non_transient_errors():
    flaky = Flaky(5, LlmErrorKind.BAD_REQUEST)
    with pytest.raises(LlmError):
        ResilientLlmClient(flaky, Metrics(), sleep=lambda _: None).complete(REQ)
    assert flaky.calls == 1


def test_circuit_opens_after_sustained_failures_and_fails_fast():
    now = [0.0]
    breaker = CircuitBreaker(window=4, min_calls=4, failure_rate=0.5, open_seconds=30, clock=lambda: now[0])
    flaky = Flaky(100, LlmErrorKind.OVERLOADED)
    client = ResilientLlmClient(flaky, Metrics(), max_attempts=1, breaker=breaker, sleep=lambda _: None)
    for _ in range(4):
        with pytest.raises(LlmError):
            client.complete(REQ)
    with pytest.raises(LlmError) as e:
        client.complete(REQ)
    assert e.value.kind == LlmErrorKind.CIRCUIT_OPEN and flaky.calls == 4
    now[0] = 31  # half-open: deixa passar de novo
    with pytest.raises(LlmError) as e:
        client.complete(REQ)
    assert e.value.kind == LlmErrorKind.OVERLOADED


def test_mcp_gateway_reads_erp_through_mcp_tools():
    g = McpErpGateway(server)
    assert g.cost_center_budget("CC-4410").available == 190000
    assert g.supplier_profile("33445566000186").blocked
    assert g.supplier_profile("00000000000000") is None
    assert len(g.purchase_history("CC-4410", date(2025, 10, 6))) == 7


def test_mcp_gateway_maps_unreachable_server_to_unavailable_source():
    g = McpErpGateway("http://127.0.0.1:9/mcp", timeout_seconds=1)
    with pytest.raises(ContextSourceUnavailable):
        g.cost_center_budget("CC-4410")
