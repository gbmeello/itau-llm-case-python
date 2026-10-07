"""Aplicação FastAPI: composição das dependências (sem framework de DI) e rotas."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import uuid
from datetime import UTC
from dataclasses import dataclass
from typing import Any

from fastapi import Body, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from purchase_agent.agent.orchestrator import PurchaseApprovalAgent
from purchase_agent.api.ratelimit import TokenBucketLimiter
from purchase_agent.api.service import Conflict, ContractViolation, EvaluationService, NotFound
from purchase_agent.audit.service import AuditService
from purchase_agent.config import Settings
from purchase_agent.context.erp import ErpGateway, MockErpGateway
from purchase_agent.contract.models import PurchaseDecision
from purchase_agent.db import make_engine, make_session_factory
from purchase_agent.llm.client import LlmClient
from purchase_agent.llm.fake import FakeLlmClient
from purchase_agent.llm.resilient import ResilientLlmClient
from purchase_agent.observability import logging as obs_logging
from purchase_agent.observability.metrics import Metrics
from purchase_agent.registry.skills import SkillError, SkillErrorReason, SkillRegistry

MAX_BODY_BYTES = 64 * 1024
_TRACE_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    metrics: Metrics
    erp: ErpGateway
    llm: LlmClient
    registry: SkillRegistry
    audit: AuditService
    agent: PurchaseApprovalAgent
    service: EvaluationService


def build_container(settings: Settings, llm: LlmClient | None = None, erp: ErpGateway | None = None) -> Container:
    metrics = Metrics()
    sessions = make_session_factory(make_engine(settings.database_url))
    registry = SkillRegistry(sessions)
    registry.seed_from_resources()
    if erp is None:
        if settings.erp_source == "mcp":
            from purchase_agent.context.mcp_gateway import McpErpGateway
            erp = McpErpGateway(settings.mcp_erp_url)
        else:
            erp = MockErpGateway()
    if llm is None:
        if settings.llm_provider == "anthropic":
            from purchase_agent.llm.anthropic_client import AnthropicLlmClient
            base: LlmClient = AnthropicLlmClient(settings.llm_timeout_seconds)
        else:
            log.warning("llm_provider=fake: respostas determinísticas simuladas (sem chamada ao Claude)")
            base = FakeLlmClient()
        llm = ResilientLlmClient(base, metrics, settings.llm_max_attempts, settings.llm_initial_backoff_seconds)
    audit = AuditService(sessions)
    agent = PurchaseApprovalAgent(settings, erp, registry, llm, metrics)
    return Container(settings, metrics, erp, llm, registry, audit, agent,
                     EvaluationService(settings, agent, audit, sessions, registry, llm))


class CaseMessage(BaseModel):
    message: str | None = None
    updates: dict[str, Any] | None = None


class CreateSkill(BaseModel):
    skillId: str
    type: str
    version: str = "1.0.0"
    content: str
    changelog: str | None = None


class NewVersion(BaseModel):
    version: str
    content: str
    changelog: str | None = None
    activate: bool = False


def _error(status: int, code: str, message: str, details: list[str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"status": status, "code": code, "message": message,
                                                     "details": details or [],
                                                     "traceId": obs_logging.trace_id_var.get()})


def _iso(dt: Any) -> str:
    """SQLite descarta o fuso; todos os timestamps são gravados em UTC."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def _skill_view(v: Any, with_content: bool = False) -> dict[str, Any]:
    out = {"skillId": v.skill_id, "type": v.type, "version": v.version, "status": v.status, "checksum": v.checksum,
           "changelog": v.changelog, "createdBy": v.created_by, "createdAt": _iso(v.created_at),
           "statusChangedAt": _iso(v.status_changed_at)}
    if with_content:
        out["content"] = v.content
    return out


def create_app(settings: Settings | None = None, llm: LlmClient | None = None,
               erp: ErpGateway | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    obs_logging.configure(json_logs=os.environ.get("LOG_FORMAT", "json") == "json")
    c = build_container(settings, llm, erp)
    app = FastAPI(title="Purchase Approval Agent", version="0.1.0",
                  description="Agente LLM de aprovação de compras (case Itaú - LLM Engineering)")
    app.state.container = c
    api_key = settings.api_key.encode()
    limiter = TokenBucketLimiter(settings.rate_limit_per_minute) if settings.rate_limit_per_minute > 0 else None
    llm_routes = ("/v1/purchase-requests/", "/v1/cases/")

    @app.middleware("http")
    async def edge_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Borda: traceId, autenticação por API key (simplificação; produção: OAuth2/mTLS), limite de payload."""
        trace_id = request.headers.get("x-trace-id") or ""
        if not _TRACE_ID.match(trace_id):
            trace_id = str(uuid.uuid4())
        token = obs_logging.trace_id_var.set(trace_id)
        try:
            if request.url.path.startswith("/v1/"):
                key = request.headers.get("x-api-key", "").encode()
                if not hmac.compare_digest(key, api_key):
                    response: Response = _error(401, "UNAUTHORIZED", "X-API-Key ausente ou inválida")
                elif int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
                    response = _error(413, "PAYLOAD_TOO_LARGE", "Payload acima de 64KB")
                elif (limiter and request.method == "POST" and request.url.path.startswith(llm_routes)
                      and (wait := limiter.acquire(key.decode())) is not None):
                    route = "cases" if request.url.path.startswith("/v1/cases/") else "evaluate"
                    c.metrics.rate_limited.labels(route).inc()
                    response = _error(429, "RATE_LIMITED", "Limite de requisições excedido; tente novamente mais tarde")
                    response.headers["Retry-After"] = str(max(1, round(wait)))
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            obs_logging.trace_id_var.reset(token)

    @app.exception_handler(ContractViolation)
    async def _contract(_: Request, e: ContractViolation) -> JSONResponse:
        return _error(400, "CONTRACT_VIOLATION", str(e), e.errors)

    @app.exception_handler(json.JSONDecodeError)
    async def _malformed(_: Request, __: json.JSONDecodeError) -> JSONResponse:
        return _error(400, "MALFORMED_JSON", "Corpo da requisição não é JSON válido")

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, e: RequestValidationError) -> JSONResponse:
        if any(err.get("type") == "json_invalid" for err in e.errors()):
            return _error(400, "MALFORMED_JSON", "Corpo da requisição não é JSON válido")
        return _error(400, "INVALID_REQUEST", "Requisição inválida", [str(err.get("msg")) for err in e.errors()])

    @app.exception_handler(SkillError)
    async def _skill(_: Request, e: SkillError) -> JSONResponse:
        status = {SkillErrorReason.NOT_FOUND: 404, SkillErrorReason.CONFLICT: 409, SkillErrorReason.INVALID: 400}
        return _error(status[e.reason], f"SKILL_{e.reason}", str(e))

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, e: NotFound) -> JSONResponse:
        return _error(404, "NOT_FOUND", str(e))

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, e: Conflict) -> JSONResponse:
        return _error(409, "CONFLICT", str(e))

    # -------------------------------------------------------------- avaliação

    @app.post("/v1/purchase-requests/evaluate", response_model=PurchaseDecision)
    def evaluate(body: Any = Body(None)) -> Response:
        result = c.service.evaluate(body, obs_logging.trace_id_var.get() or str(uuid.uuid4()))
        return Response(result.decision.model_dump_json(), media_type="application/json",
                        headers={"X-Idempotent-Replay": str(result.replayed).lower()})

    @app.post("/v1/cases/{case_id}/messages", response_model=PurchaseDecision)
    def continue_case(case_id: str, body: CaseMessage) -> Response:
        result = c.service.continue_case(case_id, body.message, body.updates,
                                         obs_logging.trace_id_var.get() or str(uuid.uuid4()))
        return Response(result.decision.model_dump_json(), media_type="application/json")

    @app.get("/v1/cases/{case_id}")
    def get_case(case_id: str) -> dict[str, Any]:
        case = c.service.get_case(case_id)
        return {"caseId": case.case_id, "requestId": case.request_id, "status": case.status, "round": case.round,
                "stateSummary": case.state_summary, "lastDecisionId": case.last_decision_id,
                "updatedAt": _iso(case.updated_at)}

    # -------------------------------------------------------------- auditoria

    def _decision_view(r: Any, full: bool) -> dict[str, Any]:
        out = {"decisionId": r.decision_id, "requestId": r.request_id, "caseId": r.case_id, "traceId": r.trace_id,
               "decision": r.decision, "riskLevel": r.risk_level, "decidedBy": r.decided_by,
               "skillVersions": json.loads(r.skill_versions), "contextIncluded": r.context_included,
               "contextExcluded": r.context_excluded, "contextTokensEstimated": r.context_tokens,
               "tokensIn": r.tokens_in, "tokensOut": r.tokens_out, "costUsd": r.cost_usd, "latencyMs": r.latency_ms,
               "createdAt": _iso(r.created_at)}
        if full:
            out["decisionPayload"] = json.loads(r.decision_json)
        return out

    @app.get("/v1/decisions/{decision_id}")
    def get_decision(decision_id: str) -> dict[str, Any]:
        r = c.audit.find(decision_id)
        if r is None:
            raise NotFound("Decisão não encontrada")
        return _decision_view(r, True)

    @app.get("/v1/decisions")
    def list_decisions(requestId: str = Query(...)) -> list[dict[str, Any]]:
        return [_decision_view(r, False) for r in c.audit.by_request(requestId)]

    # -------------------------------------------------------------- skills (CRUD versionado)

    @app.get("/v1/skills")
    def list_skills() -> dict[str, Any]:
        return {sid: [_skill_view(v) for v in versions] for sid, versions in c.registry.list_all().items()}

    @app.get("/v1/skills/{skill_id}")
    def skill_versions(skill_id: str) -> list[dict[str, Any]]:
        return [_skill_view(v, True) for v in c.registry.versions(skill_id)]

    @app.post("/v1/skills", status_code=201)
    def create_skill(body: CreateSkill, x_user: str = Header("api")) -> dict[str, Any]:
        return _skill_view(c.registry.create(body.skillId, body.type, body.version, _content(body.content),
                                             body.changelog, x_user))

    @app.post("/v1/skills/{skill_id}/versions", status_code=201)
    def add_version(skill_id: str, body: NewVersion, x_user: str = Header("api")) -> dict[str, Any]:
        return _skill_view(c.registry.add_version(skill_id, body.version, _content(body.content), body.changelog,
                                                  x_user, body.activate))

    @app.post("/v1/skills/{skill_id}/versions/{version}/activate")
    def activate(skill_id: str, version: str, x_user: str = Header("api")) -> dict[str, Any]:
        return _skill_view(c.registry.activate(skill_id, version, x_user))

    @app.delete("/v1/skills/{skill_id}", status_code=204)
    def delete_skill(skill_id: str, x_user: str = Header("api")) -> Response:
        c.registry.delete(skill_id, x_user)
        return Response(status_code=204)

    # -------------------------------------------------------------- operação

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "UP", "llmProvider": settings.llm_provider, "erpSource": settings.erp_source}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(c.metrics.render(), media_type="text/plain; version=0.0.4")

    return app


def _content(content: str) -> str:
    if not content.strip():
        raise SkillError(SkillErrorReason.INVALID, "content é obrigatório")
    if len(content) > 50_000:
        raise SkillError(SkillErrorReason.INVALID, "content acima de 50.000 caracteres")
    return content
