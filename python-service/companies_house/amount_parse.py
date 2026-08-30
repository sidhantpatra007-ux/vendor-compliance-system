"""
companies_house/amount_parse.py

Locale- and currency-aware parsing of monetary amounts found in filing
text. Shared between pdf_extract.py and ocr_extract.py so both tiers
apply the same rules for currency symbols (£/€/$) and separator
conventions (UK/US "1,234.56" vs EU "1.234,56").
"""

import re
from typing import Optional

CURRENCY_SYMBOLS = {
    "£": "GBP",
    "€": "EUR",
    "$": "USD",  # ambiguous (USD/CAD/AUD/etc) — flagged, not resolved further
}

_AMOUNT_TOKEN_RE = re.compile(
    r"(?P<open_paren>\()?"
    r"\s*(?P<currency>[£€$])?\s*"
    r"(?P<digits>\d[\d.,\s]*\d|\d)"
    r"\s*(?P<currency2>[£€$])?"
    r"\s*(?P<close_paren>\))?"
)


def _resolve_separators(digits: str) -> Optional[float]:
    """
    Decides which of '.' / ',' is the decimal separator vs thousands
    grouping and returns a float, or None if unparseable.
    """
    cleaned = digits.strip().replace(" ", "")
    has_comma = "," in cleaned
    has_dot = "." in cleaned

    if has_comma and has_dot:
        last_comma = cleaned.rfind(",")
        last_dot = cleaned.rfind(".")
        if last_dot > last_comma:
            decimal_sep, thousands_sep = ".", ","
        else:
            decimal_sep, thousands_sep = ",", "."
        cleaned = cleaned.replace(thousands_sep, "").replace(decimal_sep, ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    if has_dot and not has_comma:
        parts = cleaned.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            cleaned = cleaned.replace(".", "")
        try:
            return float(cleaned)
        except ValueError:
            return None

    if has_comma and not has_dot:
        parts = cleaned.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_amounts_in_line(text: str) -> list[dict]:
    """
    Finds every amount-like token in a line, left to right. Returns:
      [{"value": float, "currency": str|None, "raw": str}, ...]
    Empty list if nothing parseable was found.
    """
    results = []
    for m in _AMOUNT_TOKEN_RE.finditer(text):
        digits = m.group("digits")
        if not digits or not any(c.isdigit() for c in digits):
            continue
        value = _resolve_separators(digits)
        if value is None:
            continue
        if m.group("open_paren") or m.group("close_paren"):
            value = -value
        currency_symbol = m.group("currency") or m.group("currency2")
        results.append({
            "value": value,
            "currency": CURRENCY_SYMBOLS.get(currency_symbol),
            "raw": m.group(0).strip(),
        })
    return results