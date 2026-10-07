"""Validação de dígitos verificadores de CNPJ (14 dígitos numéricos)."""

_W1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_W2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def _digit(d: str, weights: tuple[int, ...]) -> int:
    mod = sum(int(c) * w for c, w in zip(d, weights, strict=False)) % 11
    return 0 if mod < 2 else 11 - mod


def is_valid(digits: str | None) -> bool:
    if not digits or len(digits) != 14 or not digits.isdigit() or len(set(digits)) == 1:
        return False
    return _digit(digits, _W1) == int(digits[12]) and _digit(digits, _W2) == int(digits[13])
