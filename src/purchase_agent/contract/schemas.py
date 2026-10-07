"""Validação contra os JSON Schemas versionados (draft 2020-12). Validadores compilados uma vez."""

from __future__ import annotations

from functools import cache
from typing import Any

from jsonschema import Draft202012Validator

from purchase_agent import resources

REQUEST_V1 = "purchase-request.v1.json"
DECISION_V1 = "purchase-decision.v1.json"
LLM_ASSESSMENT_V1 = "llm-assessment.v1.json"
COMPLIANCE_REVIEW_V1 = "compliance-review.v1.json"


@cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(resources.schema(name))


def validate(name: str, instance: Any) -> list[str]:
    errors = []
    for e in _validator(name).iter_errors(instance):
        location = "/".join(str(p) for p in e.absolute_path) or "$"
        errors.append(f"{location}: {e.message}")
    return sorted(errors)
