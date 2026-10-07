import pytest
from conftest import BLOCKED, RAPIDEZ, TODAY, request

from purchase_agent import resources
from purchase_agent.context import facts
from purchase_agent.context.erp import MockErpGateway
from purchase_agent.intake.normalizer import normalize
from purchase_agent.policy import engine
from purchase_agent.policy.engine import PolicyConfig, Result

CFG = PolicyConfig.model_validate_json(
    resources.path("skills", "policies", "purchase-policies", "1.0.0.json").read_text(encoding="utf-8"))


@pytest.fixture
def erp() -> MockErpGateway:
    return MockErpGateway()


def evaluate(erp, r):
    n = normalize(r, TODAY)
    return engine.evaluate(n, facts.collect(erp, n, TODAY), CFG, TODAY)


def codes(outcome, result):
    return [c.reason_code for c in outcome.with_result(result)]


def test_small_purchase_from_approved_supplier_passes_at_level_zero(erp):
    o = evaluate(erp, request(qty=2, unit_price="1950"))
    assert o.approval_level == 0 and not o.blocks_approval and not o.hard_reject


def test_blocked_supplier_is_hard_reject(erp):
    o = evaluate(erp, request(supplier=BLOCKED))
    assert codes(o, Result.HARD_REJECT) == ["SUPPLIER_BLOCKED"]


def test_amount_above_available_budget_is_hard_reject(erp):
    # CC-4410: 600k - 410k = 190k disponíveis
    assert codes(evaluate(erp, request(qty=4, unit_price="50000")), Result.HARD_REJECT) == ["BUDGET_INSUFFICIENT"]


def test_approval_levels_follow_thresholds(erp):
    assert evaluate(erp, request(unit_price="10000")).approval_level == 0
    assert evaluate(erp, request(unit_price="10000.01")).approval_level == 1
    assert evaluate(erp, request(unit_price="60000")).approval_level == 2


def test_new_high_risk_supplier_blocks_approval(erp):
    o = evaluate(erp, request(cost_center="CC-9000", supplier=RAPIDEZ, category="CONSULTING", unit_price="45000"))
    assert {"SUPPLIER_HIGH_RISK", "SUPPLIER_NEW"} <= set(codes(o, Result.BLOCKS_APPROVAL))


def test_detects_split_purchase_across_thirty_days(erp):
    # CC-2200 já tem 4.800 + 4.900 com o mesmo fornecedor/categoria nos últimos 30 dias
    o = evaluate(erp, request(cost_center="CC-2200", supplier="78124569000156", category="OFFICE_SUPPLIES",
                              unit_price="4950"))
    assert "SPLIT_PURCHASE_SUSPECTED" in codes(o, Result.BLOCKS_APPROVAL)


def test_unavailable_erp_blocks_approval_instead_of_failing(erp):
    erp.unavailable = True
    o = evaluate(erp, request())
    assert o.blocks_approval and not o.hard_reject


def test_restricted_category_above_limit_blocks_approval(erp):
    o = evaluate(erp, request(cost_center="CC-2200", supplier="60504030000167", category="GIFTS", qty=10,
                              unit_price="350"))
    assert "POL-CAT-004" in [c.policy_id for c in o.with_result(Result.BLOCKS_APPROVAL)]
