"""Orquestrador do pipeline (multi-agente com papéis especializados):

    intake → fatos → regras → contexto → [regra rígida? decide sem LLM]
           → ANALISTA (Sonnet) → VALIDADOR (código) → [reparo 1x] → guardrails
           → COMPLIANCE (Haiku, só quando o risco justifica) → montagem do contrato → auditoria/métricas

Qualquer falha do LLM termina em decisão válida com decidedBy=FALLBACK (falha fechada).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass

from purchase_agent import resources
from purchase_agent.agent import assembler, renderer
from purchase_agent.agent.assembler import UsageTracker
from purchase_agent.agent.validator import DecisionValidator, escalate
from purchase_agent.config import Settings
from purchase_agent.context import builder, facts
from purchase_agent.context.builder import AgentContext
from purchase_agent.context.erp import ErpGateway
from purchase_agent.contract import schemas
from purchase_agent.contract.models import (
    DecidedBy, Decision, LlmAssessment, PurchaseDecision, PurchaseRequest, RiskLevel,
)
from purchase_agent.intake.normalizer import NormalizedRequest, normalize
from purchase_agent.llm.client import LlmClient, LlmError, LlmRequest, LlmResponse
from purchase_agent.observability.metrics import Metrics
from purchase_agent.policy import engine
from purchase_agent.policy.engine import PolicyConfig, PolicyOutcome
from purchase_agent.registry import skills as sk
from purchase_agent.registry.skills import SkillRegistry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentOutcome:
    """Resultado completo para auditoria (o contrato público é só `decision`)."""

    decision: PurchaseDecision
    request: NormalizedRequest
    context: AgentContext
    policy: PolicyOutcome


class PurchaseApprovalAgent:
    def __init__(self, settings: Settings, erp: ErpGateway, registry: SkillRegistry, llm: LlmClient,
                 metrics: Metrics) -> None:
        self._s = settings
        self._erp = erp
        self._registry = registry
        self._llm = llm
        self._metrics = metrics
        self._validator = DecisionValidator(settings.min_approve_confidence)
        self._assessment_schema = resources.schema(schemas.LLM_ASSESSMENT_V1)
        self._compliance_schema = resources.schema(schemas.COMPLIANCE_REVIEW_V1)

    def evaluate(self, raw: PurchaseRequest, trace_id: str | None = None, case_id: str | None = None,
                 case_state: str | None = None) -> AgentOutcome:
        start = time.perf_counter()
        trace_id = trace_id or str(uuid.uuid4())
        decision_id = str(uuid.uuid4())
        usage = UsageTracker()
        skill_refs: dict[str, str] = {}
        events: list[str] = []
        today = self._s.today()

        req = normalize(raw, today)
        f = facts.collect(self._erp, req, today)
        policy_skill = self._registry.required(sk.POLICIES)
        skill_refs[policy_skill.skill_id] = policy_skill.version
        cfg = PolicyConfig.model_validate_json(policy_skill.content)
        policy = engine.evaluate(req, f, cfg, today)

        examples = self._registry.active(sk.EXAMPLES)
        examples_text = renderer.strip_frontmatter(examples.content) if examples else ""
        ctx = builder.build(req, f, policy, cfg, case_state, builder.estimate_tokens(examples_text),
                            self._s.context, today)
        self._record_context(ctx)
        examples_included = examples is not None and "FEW_SHOT_EXAMPLES" not in ctx.excluded_ids
        if examples_included:
            skill_refs[examples.skill_id] = examples.version  # type: ignore[union-attr]

        fallback_reason: str | None = None
        if policy.hard_reject:
            # Economia e previsibilidade: violação objetiva não precisa (nem deve) passar pelo modelo.
            assessment, decided_by = assembler.rule_engine_reject(policy, ctx), DecidedBy.RULE_ENGINE
        else:
            analyst = self._registry.required(sk.ANALYST)
            skill_refs[analyst.skill_id] = analyst.version
            system = renderer.system(analyst.content, {"examples": examples_text if examples_included else ""})
            user = renderer.analyst_user(req, ctx)
            try:
                result = self._analyze(system, user, ctx, usage, skill_refs, events)
                if result is None:
                    fallback_reason = "VALIDATION_FAILED"
                    assessment = assembler.fallback(policy, ctx, "saída do modelo inválida após reparo")
                    decided_by = DecidedBy.FALLBACK
                else:
                    guarded = self._validator.enforce(result, policy, req)
                    assessment, decided_by = guarded.assessment, (
                        DecidedBy.VALIDATOR_OVERRIDE if guarded.overridden else DecidedBy.AGENT)
                    events.extend(guarded.events)
                    if self._needs_compliance(assessment, policy, cfg, req):
                        reviewed = self._compliance(user, assessment, usage, skill_refs, events)
                        if reviewed is not assessment:
                            assessment, decided_by = reviewed, DecidedBy.COMPLIANCE_OVERRIDE
            except LlmError as e:
                usage.failed += 1
                log.warning("llm_unavailable kind=%s requestId=%s", e.kind, req.request_id)
                fallback_reason = f"LLM_{e.kind}"
                assessment = assembler.fallback(policy, ctx, f"LLM indisponível: {e.kind}")
                decided_by = DecidedBy.FALLBACK

        latency_ms = int((time.perf_counter() - start) * 1000)
        decision = assembler.assemble(req, policy, ctx, assessment, decided_by, case_id, trace_id, decision_id,
                                      skill_refs, usage, latency_ms, fallback_reason, events)
        contract_errors = schemas.validate(schemas.DECISION_V1, json.loads(decision.model_dump_json()))
        if contract_errors:  # não deveria acontecer (montado por código); se acontecer é bug: deixa visível
            log.error("decision_contract_violation requestId=%s errors=%s", req.request_id, contract_errors)
        self._record_decision(decision)
        log.info("decision", extra={"event": {
            "requestId": req.request_id, "decisionId": decision_id, "decision": decision.decision.value,
            "risk": decision.riskLevel.value, "decidedBy": decided_by.value, "llmCalls": usage.call_count,
            "tokensIn": decision.audit.tokens.input, "tokensOut": decision.audit.tokens.output,
            "costUsd": decision.audit.estimatedCostUsd, "latencyMs": latency_ms,
            "contextTokens": ctx.estimated_tokens, "excluded": list(ctx.excluded_ids), "events": events,
            "skills": skill_refs}})
        return AgentOutcome(decision, req, ctx, policy)

    # ------------------------------------------------------------------ papéis

    def _analyze(self, system: str, user: str, ctx: AgentContext, usage: UsageTracker, skill_refs: dict[str, str],
                 events: list[str]) -> LlmAssessment | None:
        """Analista + validação + até N reparos. None se a saída continuar inválida."""
        r = self._call("analyst", self._s.analyst_model, system, user, self._assessment_schema,
                       self._s.analyst_max_tokens, self._s.analyst_effort, usage)
        parsed = self._validator.parse_and_check(r.text, ctx)
        attempts = 0
        while not parsed.ok and attempts < self._s.max_repair_attempts:
            attempts += 1
            self._metrics.validation_failures.labels("analyst").inc()
            self._metrics.grounding_errors.labels("analyst").inc(len(parsed.repairable_errors))
            events.append(f"REPAIR_ATTEMPT_{attempts}")
            log.info("analyst_output_invalid attempt=%s errors=%s", attempts, parsed.repairable_errors)
            repair = self._registry.required(sk.REPAIR)
            skill_refs[repair.skill_id] = repair.version
            r = self._call("repair", self._s.analyst_model, renderer.system(repair.content),
                           renderer.repair_user(user, r.text, parsed.repairable_errors), self._assessment_schema,
                           self._s.analyst_max_tokens, self._s.analyst_effort, usage)
            parsed = self._validator.parse_and_check(r.text, ctx)
        if not parsed.ok:
            self._metrics.validation_failures.labels("repair").inc()
            self._metrics.grounding_errors.labels("repair").inc(len(parsed.repairable_errors))
            log.warning("analyst_output_invalid_final errors=%s", parsed.repairable_errors)
            return None
        return parsed.assessment

    @staticmethod
    def _needs_compliance(a: LlmAssessment, policy: PolicyOutcome, cfg: PolicyConfig, req: NormalizedRequest) -> bool:
        """Segunda opinião apenas onde o erro custa caro."""
        if a.decision == Decision.APPROVE:
            return req.total_amount > cfg.autoApprovalLimit or a.riskLevel.at_least(RiskLevel.MEDIUM)
        # Rejeição pelo modelo (sem regra rígida) também afeta o solicitante: revisar.
        return a.decision == Decision.REJECT and not policy.hard_reject

    def _compliance(self, analyst_user: str, a: LlmAssessment, usage: UsageTracker, skill_refs: dict[str, str],
                    events: list[str]) -> LlmAssessment:
        reviewer = self._registry.required(sk.COMPLIANCE)
        skill_refs[reviewer.skill_id] = reviewer.version
        try:
            r = self._call("compliance", self._s.reviewer_model, renderer.system(reviewer.content),
                           renderer.compliance_user(analyst_user, a.model_dump_json()), self._compliance_schema,
                           1000, None, usage)
            review = json.loads(r.text)
        except (LlmError, ValueError):
            usage.failed += 1
            events.append("COMPLIANCE_UNAVAILABLE")
            return escalate(a, "Revisão de compliance indisponível; decisão sensível exige humano.",
                            " (Encaminhado para revisão humana pelo compliance.)")
        if review.get("agree") is True:
            events.append("COMPLIANCE_AGREED")
            return a
        self._metrics.compliance_disagreements.inc()
        events.append("COMPLIANCE_DISAGREED")
        concerns = "; ".join(str(c) for c in review.get("concerns", []))
        return escalate(a, f"Revisor de compliance discordou: {concerns}",
                        " (Encaminhado para revisão humana pelo compliance.)")

    def _call(self, purpose: str, model: str, system: str, user: str, schema: dict | None, max_tokens: int,
              effort: str | None, usage: UsageTracker) -> LlmResponse:
        r = self._llm.complete(LlmRequest(purpose, model, system, user, schema, max_tokens, effort))
        usage.add(r)
        return r

    # ------------------------------------------------------------------ métricas

    def _record_context(self, ctx: AgentContext) -> None:
        for layer, tokens in ctx.tokens_by_layer.items():
            self._metrics.context_tokens.labels(layer).observe(tokens)
        if ctx.truncated:
            self._metrics.context_truncations.inc(len(ctx.excluded_ids))

    def _record_decision(self, d: PurchaseDecision) -> None:
        m = self._metrics
        m.decisions.labels(d.decision.value, d.audit.decidedBy.value, d.riskLevel.value).inc()
        m.decision_latency.labels(d.audit.decidedBy.value).observe(d.audit.latencyMs / 1000)
        m.decision_cost.observe(d.audit.estimatedCostUsd)
        if d.audit.fallbackReason:
            m.fallbacks.labels(d.audit.fallbackReason).inc()
        for e in d.audit.guardrailEvents:
            m.guardrail_events.labels(e).inc()
