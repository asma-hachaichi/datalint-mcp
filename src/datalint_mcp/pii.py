"""Spotting personal data, so it can be reported, and hidden everywhere else, without
ever being shown.

Detection uses value patterns (with checksums where the format has one) and column names.
It is a strong hint, not proof: it can miss personal data written in unusual formats.
"""

from __future__ import annotations

import re
from typing import Callable

import pandas as pd

MAX_SCAN_VALUES = 5000
MIN_SHARE = 0.5  # share of values that must match before a column is flagged

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?[\d\s().\-]{8,24}")
_CARD = re.compile(r"\d[\d \-]{11,22}\d")
_IBAN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")
_IPV4 = re.compile(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})")
_US_SSN = re.compile(r"\d{3}-\d{2}-\d{4}")


def is_ipv4(text: str) -> bool:
    match = _IPV4.fullmatch(text)
    return bool(match) and all(int(part) <= 255 for part in match.groups())


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def is_phone(text: str) -> bool:
    if not _PHONE.fullmatch(text) or is_ipv4(text):
        return False
    count = len(_digits(text))
    # A bare run of digits is more likely an ID or an amount than a phone number.
    looks_dialled = text.startswith(("+", "0")) or bool(re.search(r"[\s().\-]", text))
    return 9 <= count <= 15 and looks_dialled


def is_card(text: str) -> bool:
    if not _CARD.fullmatch(text):
        return False
    digits = _digits(text)
    return 13 <= len(digits) <= 19 and _luhn_ok(digits)


def is_iban(text: str) -> bool:
    compact = text.replace(" ", "").upper()
    if not _IBAN.fullmatch(compact):
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1


def is_us_ssn(text: str) -> bool:
    return bool(_US_SSN.fullmatch(text))


def mentions_email(text: str) -> bool:
    return bool(_EMAIL.search(text))


# Each detector: (kind, test on one value, minimum share of values that must pass).
# Emails count even when a single free-text value mentions one; the other formats are
# common enough by accident that most of a column must match.
DETECTORS: list[tuple[str, Callable[[str], bool], float]] = [
    ("email", mentions_email, 0.0),
    ("phone", is_phone, MIN_SHARE),
    ("credit_card", is_card, MIN_SHARE),
    ("iban", is_iban, MIN_SHARE),
    ("ip_address", is_ipv4, MIN_SHARE),
    ("us_ssn", is_us_ssn, MIN_SHARE),
]

NAME_HINTS = {
    "person_name": {
        "name", "first_name", "firstname", "last_name", "lastname", "full_name", "fullname",
        "surname", "given_name", "family_name", "customer_name", "contact_name",
    },
    "date_of_birth": {"dob", "birth_date", "birthdate", "date_of_birth", "birthday"},
    "national_id": {"ssn", "passport", "passport_number", "national_id", "tax_id"},
    "email": {"email", "email_address", "e_mail"},
    "phone": {"phone", "phone_number", "mobile", "telephone"},
}


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _name_kinds(column: str) -> set[str]:
    key = _normalize_name(column)
    kinds = {kind for kind, names in NAME_HINTS.items() if key in names}
    if ("address" in key or "street" in key) and key not in {"ip_address", "email_address"}:
        kinds.add("address")
    return kinds


def scan_column(s: pd.Series, column: str) -> list[dict]:
    """Findings for one column: which kinds of personal data it seems to hold, and why."""
    findings: dict[str, dict] = {}
    values = s.dropna()
    # Decimal numbers are amounts or measurements, never contact details or card numbers.
    if not values.empty and not pd.api.types.is_float_dtype(s):
        text = values.astype(str).str.strip()
        if len(text) > MAX_SCAN_VALUES:
            text = text.sample(MAX_SCAN_VALUES, random_state=0)
        for kind, test, min_share in DETECTORS:
            matches = int(text.map(test).sum())
            if matches and matches / len(text) >= min_share:
                findings[kind] = {
                    "kind": kind,
                    "basis": "values",
                    "matching_values": matches,
                    "matching_pct": round(100 * matches / len(text), 2),
                }
    for kind in _name_kinds(column):
        findings.setdefault(kind, {"kind": kind, "basis": "column_name"})
    return list(findings.values())


def personal_kinds(s: pd.Series, column: str) -> list[str]:
    return [f["kind"] for f in scan_column(s, column)]
