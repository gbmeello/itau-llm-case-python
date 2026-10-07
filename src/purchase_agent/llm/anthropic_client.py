"""Cliente real (SDK oficial `anthropic` 1.x).

Decisões:
- Contrato de saída via structured outputs (`output_config.format`): Sonnet 5.5 rejeita tool_choice forçado.
- System prompt com `cache_control`: a parte estável (regras + exemplos) é cacheada.
- Sem `temperature`: removido do SDK 1.x e rejeitado pelo Sonnet 5.5 se não-default. A consistência vem de
  schema + validador + evals.
- `max_retries=0` no SDK: o retry fica no ResilientLlmClient para termos métrica e controle.
"""

from __future__ import annotations

import time
from typing import Any

import anthropic

from purchase_agent.llm.client import LlmError, LlmErrorKind, LlmRequest, LlmResponse, ToolCall


class AnthropicLlmClient:
    def __init__(self, timeout_seconds: float = 60.0, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic(max_retries=0, timeout=timeout_seconds)

    def complete(self, req: LlmRequest) -> LlmResponse:
        params: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": [{"type": "text", "text": req.system_prompt, "cache_control": {"type": "ephemeral"}}],
            "messages": req.messages or [{"role": "user", "content": req.user_content}],
        }
        if req.tools:
            params["tools"] = req.tools
            if req.forbid_tools:
                params["tool_choice"] = {"type": "none"}  # força a resposta final; `any`/`tool` são 400 no Sonnet 5.5
        output_config: dict[str, Any] = {}
        if req.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": req.output_schema}
        if req.effort:
            output_config["effort"] = req.effort
        if output_config:
            params["output_config"] = output_config

        start = time.perf_counter()
        try:
            message = self._client.messages.create(**params)
        except anthropic.RateLimitError as e:
            raise LlmError(LlmErrorKind.RATE_LIMITED, str(e)) from e
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LlmError(LlmErrorKind.AUTH, str(e)) from e
        except anthropic.BadRequestError as e:
            raise LlmError(LlmErrorKind.BAD_REQUEST, str(e)) from e
        except anthropic.APITimeoutError as e:  # subclasse de APIConnectionError: vem antes
            raise LlmError(LlmErrorKind.TIMEOUT, str(e)) from e
        except anthropic.APIConnectionError as e:
            raise LlmError(LlmErrorKind.NETWORK, str(e)) from e
        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                raise LlmError(LlmErrorKind.OVERLOADED, str(e)) from e
            kind = LlmErrorKind.SERVER_ERROR if e.status_code >= 500 else LlmErrorKind.UNKNOWN
            raise LlmError(kind, str(e)) from e
        latency_ms = int((time.perf_counter() - start) * 1000)

        if message.stop_reason == "refusal":
            raise LlmError(LlmErrorKind.REFUSAL, "Modelo recusou a solicitação")
        text = "".join(block.text for block in message.content if block.type == "text")
        tool_calls = tuple(ToolCall(b.id, b.name, dict(b.input)) for b in message.content if b.type == "tool_use")
        usage = message.usage
        return LlmResponse(
            text=text,
            model=message.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
            latency_ms=latency_ms,
            stop_reason=message.stop_reason or "unknown",
            tool_calls=tool_calls,
            # Blocos devolvidos exatamente como vieram (inclusive thinking): o histórico é append-only.
            assistant_content=[b.model_dump(mode="json", exclude_none=True) for b in message.content],
        )
