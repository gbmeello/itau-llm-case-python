"""Heurística barata para sinalizar texto livre que tenta instruir o modelo.

Não é a defesa principal. A defesa é estrutural: dados delimitados, regras fora do LLM e validação da saída.
A heurística serve para forçar revisão humana e gerar métrica.
"""

from __future__ import annotations

import re
import unicodedata

_PATTERNS = [
    re.compile(p)
    for p in (
        r"ignor\w*\s+(as\s+|todas\s+as\s+|all\s+|the\s+|previous\s+|anteriores\s+)?(instruc|instruct|regra|rule|polit|polic)",
        r"(disregard|desconsider\w*)\s+.{0,30}(instruc|instruct|regra|rule|polit|polic)",
        r"(system\s*prompt|prompt\s+do\s+sistema)",
        r"(voce|you)\s+(agora\s+e|are\s+now)",
        r"(aprov\w*|approve)\s+(automaticamente|automatically|sem\s+(analise|restric)|without)",
        r"\"?decision\"?\s*[:=]\s*\"?(approve|aprov)",
        r"</?\s*(untrusted_input|system|evidence|instructions?)\b",
        r"(responda|retorne|return|respond)\s+(apenas|somente|only)\s+.{0,20}(approve|aprov)",
    )
]


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()


def is_suspicious(text: str | None) -> bool:
    if not text or not text.strip():
        return False
    normalized = _normalize(text)
    return any(p.search(normalized) for p in _PATTERNS)
