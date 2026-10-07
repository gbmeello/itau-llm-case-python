"""Minimização de dados (LGPD): mascara PII em texto livre antes de qualquer envio ao LLM ou persistência no contexto.

A decisão de compra não depende de CPF, e-mail, telefone ou cartão de quem escreveu a justificativa; então esses
dados não saem do perímetro. CNPJ não é mascarado: é dado empresarial e relevante para a decisão.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# CPF: 000.000.000-00 ou 11 dígitos isolados (não parte de um CNPJ de 14 dígitos).
_CPF = re.compile(r"(?<![\d./-])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\d/-]|\.\d)")
# Cartão: 13 a 19 dígitos, com espaços ou hífens opcionais.
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
# Telefone BR: (11) 91234-5678, 11 912345678, +55 11 91234-5678.
_PHONE = re.compile(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
    return total % 10 == 0


def mask(text: str | None) -> tuple[str | None, list[str]]:
    """Retorna (texto mascarado, tipos de PII encontrados)."""
    if not text:
        return text, []
    found: list[str] = []

    def sub(pattern: re.Pattern[str], label: str, value: str, check=None) -> str:  # type: ignore[no-untyped-def]
        def repl(m: re.Match[str]) -> str:
            raw = m.group(0)
            if check and not check(re.sub(r"\D", "", raw)):
                return raw
            found.append(label)
            return f"[{label}]"
        return pattern.sub(repl, value)

    out = sub(_EMAIL, "EMAIL", text)
    out = sub(_CARD, "CARTAO", out, check=lambda d: 13 <= len(d) <= 19 and _luhn(d))
    out = sub(_CPF, "CPF", out)
    out = sub(_PHONE, "TELEFONE", out)
    return out, sorted(set(found))
