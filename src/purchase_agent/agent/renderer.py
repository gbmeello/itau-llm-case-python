"""Montagem dos prompts a partir das skills e do contexto selecionado.

- System = skill (estável, cacheável). Nada variável por requisição entra aqui.
- User = dados em blocos delimitados por tags XML; texto livre do solicitante isolado em <untrusted_input>.
"""

from __future__ import annotations

import json
import re

from purchase_agent.context.builder import AgentContext, ContextItem, Layer, neutralize
from purchase_agent.intake.normalizer import NormalizedRequest

_PLACEHOLDER = re.compile(r"\{\{[a-z_]+}}")


def strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end > 0:
            return content[content.find("\n", end + 1) + 1:]
    return content


def system(skill_content: str, variables: dict[str, str] | None = None) -> str:
    body = strip_frontmatter(skill_content)
    for key, value in (variables or {}).items():
        body = body.replace("{{" + key + "}}", value or "")
    return _PLACEHOLDER.sub("", body).strip()


def _line(item: ContextItem) -> str:
    payload: dict = {"id": item.id, "source": item.source, "content": item.content}
    if item.data:
        payload["data"] = item.data
    return json.dumps(payload, ensure_ascii=False, default=str)


def analyst_user(req: NormalizedRequest, ctx: AgentContext) -> str:
    req_item = next(i for i in ctx.included if i.id == "EV-REQ")
    parts = [f"<purchase_request>\n{_line(req_item)}\n</purchase_request>\n"]
    if req.justification is not None:
        parts.append('<untrusted_input source="requester.justification" evidence_id="EV-JUST">\n'
                     f"{neutralize(req.justification)}\n</untrusted_input>\n")
    evidence = [_line(i) for i in ctx.included if i.id != "EV-REQ" and i.layer != Layer.P3_CASE]
    parts.append("<evidence>\n" + "\n".join(evidence) + "\n</evidence>")
    case = next((i for i in ctx.included if i.layer == Layer.P3_CASE), None)
    if case:
        parts.append(f'<case_state evidence_id="EV-CASE">\n{neutralize(case.content)}\n</case_state>')
    parts.append("<task>Avalie a solicitação seguindo as regras do sistema e responda no schema exigido.</task>")
    return "\n".join(parts)


def repair_user(analyst_user_content: str, previous_output: str, errors: list[str]) -> str:
    return (f"{analyst_user_content}\n\n<previous_output>\n{previous_output}\n</previous_output>\n\n"
            "<validation_errors>\n- " + "\n- ".join(errors) + "\n</validation_errors>")


def compliance_user(analyst_user_content: str, assessment_json: str) -> str:
    return f"{analyst_user_content}\n\n<analyst_assessment>\n{assessment_json}\n</analyst_assessment>"
