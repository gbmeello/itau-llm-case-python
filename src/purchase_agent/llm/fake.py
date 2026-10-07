"""LLM determinístico para testes, CI e demo sem chave de API.

Não pretende medir qualidade de modelo (isso é o eval com o Claude real). Serve para exercitar o pipeline
de ponta a ponta, com um "analista" heurístico que lê as evidências estruturadas exatamente como o modelo
as recebe, e para INJETAR FALHAS reproduzíveis via marcadores no requestId:

- FAKE-INVALID-JSON        primeira resposta do analista não é JSON (o reparo corrige)
- FAKE-INVALID-ALWAYS      toda resposta inválida (fallback)
- FAKE-HALLUCINATE         cita evidência e valor inexistentes (o reparo corrige)
- FAKE-DOWN                provedor sobrecarregado (retry e depois fallback)
- FAKE-OBEY                modelo "ingênuo" que obedece à injeção e aprova (guardrail deve barrar)
- FAKE-COMPLIANCE-DISAGREE revisor de compliance discorda
- FAKE-TOOLS               com tool calling ativo, pede o histórico detalhado (1 rodada) e cita a evidência EV-T1
- FAKE-TOOL-LOOP           com tool calling ativo, insiste em ferramentas (com argumento inválido) até o limite
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from typing import Any

from purchase_agent.context.builder import estimate_tokens
from purchase_agent.llm.client import LlmError, LlmErrorKind, LlmRequest, LlmResponse, ToolCall

_REQUEST_ID = re.compile(r'"requestId": ?"([^"]+)"')
_BLOCK = re.compile(r"<(evidence|purchase_request)>\n(.*?)\n</\1>", re.S)
_UNTRUSTED = re.compile(r"<untrusted_input[^>]*>\n(.*?)\n</untrusted_input>", re.S)
_QUOTES = re.compile(
    r"((tr[eê]s|3) cota[cç]|cota[cç][oõ]es (anexadas|realizadas)|fornecedor [uú]nico|contrato-quadro|exclusividade)")
_MONEY = re.compile(r"R\$\s?[0-9.,]+")


class FakeLlmClient:
    def __init__(self) -> None:
        self._calls: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def complete(self, req: LlmRequest) -> LlmResponse:
        m = _REQUEST_ID.search(req.user_content or "")
        request_id = m.group(1) if m else "unknown"
        with self._lock:
            self._calls[f"{request_id}:{req.purpose}"] += 1
            call = self._calls[f"{request_id}:{req.purpose}"]
        if "FAKE-DOWN" in request_id:
            raise LlmError(LlmErrorKind.OVERLOADED, "Simulado: provedor sobrecarregado (529)")
        if req.purpose == "compliance":
            text = json.dumps({"agree": "FAKE-COMPLIANCE-DISAGREE" not in request_id,
                               "concerns": ["Justificativa não sustenta o valor (EV-JUST)."]
                               if "FAKE-COMPLIANCE-DISAGREE" in request_id else []})
        elif req.purpose == "case-summarizer":
            flat = " ".join(req.user_content.split())
            text = flat[:480] + ("..." if len(flat) > 480 else "")
        elif req.tools and not req.forbid_tools and (call_ := self._tool_request(req, request_id)) is not None:
            return call_
        else:
            text = self._analyst(req, request_id)
        cached = estimate_tokens(req.system_prompt) if (req.purpose == "analyst" and call == 1) else 0
        total_in = estimate_tokens(req.system_prompt) + estimate_tokens(req.user_content)
        return LlmResponse(text, f"fake:{req.model}", total_in - cached, estimate_tokens(text), cached, 0, 5,
                           "end_turn")

    def _tool_request(self, req: LlmRequest, request_id: str) -> LlmResponse | None:
        """Simula o modelo pedindo uma ferramenta (blocos no formato da Messages API)."""
        results = [b for m in (req.messages or []) if m["role"] == "user" and isinstance(m["content"], list)
                   for b in m["content"] if b.get("type") == "tool_result"]
        if "FAKE-TOOL-LOOP" in request_id:
            call = ToolCall(f"toolu_{len(results) + 1}", "get_policy_text", {"policy_id": "POL-INEXISTENTE"})
        elif "FAKE-TOOLS" in request_id and not results:
            category = _Evidence.parse(req.user_content).request.get("data", {}).get("category", "UNCATEGORIZED")
            call = ToolCall("toolu_1", "get_purchase_history_details",
                            {"category": category, "supplier_tax_id": "", "months": 24})
        else:
            return None
        content = [{"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}]
        return LlmResponse("", f"fake:{req.model}", estimate_tokens(req.user_content), 20, 0, 0, 5, "tool_use",
                           (call,), content)

    # ------------------------------------------------------------------ analista heurístico

    def _analyst(self, req: LlmRequest, request_id: str) -> str:
        repair = req.purpose == "repair"
        if "FAKE-INVALID-ALWAYS" in request_id:
            return '{"decision": "APPROVE", "oops": '
        if "FAKE-INVALID-JSON" in request_id and not repair:
            return "Claro! Aqui está a análise: a compra parece ok."
        ev = _Evidence.parse(req.user_content)
        out = self._decide(ev)
        tool_ids = [json.loads(b["content"]).get("evidenceId") for m in (req.messages or [])
                    if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"]
                    if b.get("type") == "tool_result" and not b.get("is_error")]
        if any(tool_ids):
            out["reasons"].append({"code": "OTHER", "severity": "LOW",
                                   "explanation": "Histórico detalhado consultado por ferramenta confirma o padrão.",
                                   "evidenceIds": [t for t in tool_ids if t]})
        if "FAKE-HALLUCINATE" in request_id and not repair:
            out["reasons"].append({"code": "AMOUNT_ABOVE_HISTORICAL", "severity": "MEDIUM",
                                   "explanation": "Preço de mercado de referência é R$ 1.234.567,00.",
                                   "evidenceIds": ["EV-999"]})
        if "FAKE-OBEY" in request_id and ev.untrusted is not None:
            out = {"decision": "APPROVE", "riskLevel": "LOW", "riskScore": 5, "confidence": 0.95,
                   "summary": "Aprovado conforme solicitado.",
                   "reasons": [{"code": "WITHIN_POLICY", "severity": "LOW",
                                "explanation": "Solicitante pediu aprovação.", "evidenceIds": ["EV-JUST"]}],
                   "missingInformation": []}
        return json.dumps(out, ensure_ascii=False)

    @staticmethod
    def _decide(ev: _Evidence) -> dict[str, Any]:
        reasons: list[dict[str, Any]] = []
        missing: list[str] = []

        def r(code: str, severity: str, explanation: str, *ids: str) -> None:
            reasons.append({"code": code, "severity": severity,
                            "explanation": _MONEY.sub("o valor indicado", explanation),
                            "evidenceIds": [i for i in ids if i]})

        def build(decision: str, risk: str, score: int, confidence: float, summary: str) -> dict[str, Any]:
            return {"decision": decision, "riskLevel": risk, "riskScore": score, "confidence": confidence,
                    "summary": summary, "reasons": reasons, "missingInformation": missing}

        sec = ev.policy_ev("SEC-INPUT")
        if sec:
            r("SUSPICIOUS_INPUT", "HIGH", "Texto do solicitante tenta instruir o sistema.", sec, "EV-JUST")
            return build("ESCALATE_TO_HUMAN", "HIGH", 70, 0.8,
                         "Justificativa contém tentativa de instruir o sistema; requer revisão humana.")

        issues = ev.request_issues()
        if any(i.startswith("ITEM_PRICE_MISSING") for i in issues):
            missing.append("Preço unitário de todos os itens")
        if "SUPPLIER_MISSING" in issues:
            missing.append("Fornecedor (CNPJ e razão social)")
        if "SUPPLIER_TAXID_INVALID" in issues or "SUPPLIER_TAXID_MISSING" in issues:
            missing.append("CNPJ válido do fornecedor")
        if "JUSTIFICATION_MISSING" in issues:
            missing.append("Justificativa de negócio da compra")
        elif ev.untrusted is not None and len(ev.untrusted.strip()) < 30:
            missing.append("Justificativa detalhada (objetivo, impacto e alternativa considerada)")
            r("WEAK_JUSTIFICATION", "MEDIUM", "Justificativa curta demais para avaliar a necessidade.", "EV-JUST")
        if missing:
            r("MISSING_INFORMATION", "MEDIUM", "Faltam dados para avaliar a solicitação com segurança.", "EV-REQ")
            return build("NEEDS_INFO", "MEDIUM", 40, 0.75, "Solicitação incompleta; devolvida ao solicitante.")

        for ev_id, item in ev.policies.items():
            data = item.get("data", {})
            if data.get("result") == "BLOCKS_APPROVAL":
                r(data.get("reasonCode", "OTHER"), "HIGH", item.get("content", ""), ev_id)
        for src in ev.unavailable:
            r("OTHER", "HIGH", "Fonte de dados indisponível; não é possível verificar.", src["id"])

        hist = ev.by_id.get("EV-HIST-STATS", {})
        ratio = float(hist.get("data", {}).get("ratioToAverage", 0) or 0)
        amount = float(ev.request.get("data", {}).get("totalAmount", 0))
        mentions_quotes = bool(ev.untrusted and _QUOTES.search(ev.untrusted.lower()))
        if ratio > 2.5:
            r("AMOUNT_ABOVE_HISTORICAL", "MEDIUM" if mentions_quotes else "HIGH",
              f"Valor {ratio:.2f}x acima da média histórica da categoria.", "EV-REQ", "EV-HIST-STATS")
        if amount > 50000 and not mentions_quotes:
            r("WEAK_JUSTIFICATION", "HIGH", "Acima do limite de cotações sem menção a cotações.", "EV-JUST",
              "EV-PT-COT-006" if "EV-PT-COT-006" in ev.by_id else "")
        pct = float(ev.by_id.get("EV-BUD", {}).get("data", {}).get("percentOfAvailable", 0) or 0)
        if pct > 80:
            r("BUDGET_TIGHT", "HIGH" if ratio > 1.5 else "MEDIUM",
              f"Compra consome {pct:.1f}% do saldo disponível.", "EV-BUD")
        if ev.by_id.get("EV-SUP", {}).get("data", {}).get("riskRating") == "MEDIUM":
            r("SUPPLIER_HIGH_RISK", "MEDIUM", "Fornecedor com rating de risco MEDIUM.", "EV-SUP")

        severities = {x["severity"] for x in reasons}
        if severities & {"HIGH", "CRITICAL"}:
            return build("ESCALATE_TO_HUMAN", "HIGH", 65, 0.8, "Sinais de risco exigem julgamento humano.")
        medium = "MEDIUM" in severities
        cite = [k for k in ev.by_id if k in ("EV-BUD", "EV-HIST-STATS", "EV-SUP")] or ["EV-REQ"]
        r("WITHIN_POLICY", "LOW", "Valor coerente com histórico, saldo e fornecedor homologado.", *cite)
        return build("APPROVE", "MEDIUM" if medium else "LOW", 30 if medium else 12, 0.78 if medium else 0.88,
                     "Compra dentro da política e coerente com o histórico.")


class _Evidence:
    def __init__(self) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        self.policies: dict[str, dict[str, Any]] = {}
        self.unavailable: list[dict[str, Any]] = []
        self.request: dict[str, Any] = {}
        self.untrusted: str | None = None

    @staticmethod
    def parse(user: str) -> _Evidence:
        ev = _Evidence()
        for block in _BLOCK.finditer(user):
            for line in block.group(2).splitlines():
                try:
                    node = json.loads(line)
                except ValueError:
                    continue
                ev.by_id[node["id"]] = node
                if node["id"] == "EV-REQ":
                    ev.request = node
                elif node["id"].startswith("EV-POL-"):
                    ev.policies[node["id"]] = node
                elif node["id"].startswith("EV-SRC-"):
                    ev.unavailable.append(node)
        m = _UNTRUSTED.search(user)
        ev.untrusted = m.group(1) if m else None
        return ev

    def policy_ev(self, policy_id: str) -> str | None:
        return next((k for k, v in self.policies.items() if v.get("data", {}).get("policyId") == policy_id), None)

    def request_issues(self) -> list[str]:
        return list(self.request.get("data", {}).get("dataQualityIssues", []))
