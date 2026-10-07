"""Logs estruturados em JSON com traceId (contextvar propagado pelo middleware).

Não logamos prompt completo nem justificativa: o que o modelo viu é reconstituível pela auditoria.
"""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime

trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "@timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "log.level": record.levelname,
            "log.logger": record.name,
            "message": record.getMessage(),
            "trace.id": trace_id_var.get(),
        }
        event = getattr(record, "event", None)
        if isinstance(event, dict):
            payload["event"] = event
        if record.exc_info:
            payload["error.stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure(json_logs: bool = True, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger("purchase_agent")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
