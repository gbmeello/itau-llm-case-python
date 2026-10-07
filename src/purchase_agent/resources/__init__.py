"""Acesso aos recursos versionados (schemas, skills, dados sintéticos)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent


def path(*parts: str) -> Path:
    return ROOT.joinpath(*parts)


@cache
def schema(name: str) -> dict[str, Any]:
    return json.loads(path("schemas", name).read_text(encoding="utf-8"))
