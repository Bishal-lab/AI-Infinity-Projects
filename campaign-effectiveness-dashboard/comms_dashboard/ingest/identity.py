"""Identifier canonicalisation and pseudonymisation.

Channel exports carry emails, phone numbers and LMS logins; the roster carries
all of them against one employee id. Canonicalising both sides the same way is
what lets a WhatsApp number and an LMS login resolve to the same person — and
therefore what makes cross-channel deduplication possible at all.

Identifiers are then replaced by a keyed HMAC-SHA256 digest so the warehouse
holds no direct identifiers. Be clear about what that buys: this is
**pseudonymisation, not anonymisation**. An attacker who knows your email
convention can hash a name list and match it. It reduces casual exposure; it
does not put the data outside GDPR/DPDP.
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256

from ..models import DIMENSION_SOURCES  # noqa: F401  (re-exported for callers)

_NON_DIGIT = re.compile(r"\D")


def canonicalise(value: object, rules: tuple[str, ...], country_code: str = "+44") -> str | None:
    """Apply the canonicalisation rules named in the mapping YAML."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    for rule in rules:
        if rule == "strip":
            text = text.strip()
        elif rule == "lower":
            text = text.lower()
        elif rule == "upper":
            text = text.upper()
        elif rule == "e164":
            text = to_e164(text, country_code) or text
        elif rule == "strip_domain":
            text = text.split("@", 1)[0]
    return text or None


def to_e164(value: object, country_code: str = "+44") -> str | None:
    """Normalise a phone number to E.164.

    Handles the two things that reliably happen to phone columns: Excel strips
    the leading ``+`` and turns the number into a float, and different systems
    write the same number as ``07724 829869``, ``+447724829869`` or
    ``00447724829869``.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    # Excel float round-trip: "447724829869.0"
    if text.endswith(".0"):
        text = text[:-2]

    had_plus = text.startswith("+")
    digits = _NON_DIGIT.sub("", text)
    if not digits:
        return None

    cc = _NON_DIGIT.sub("", country_code) or "44"
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        # National trunk prefix: 07724... -> 447724...
        digits = cc + digits[1:]
    elif not had_plus and not digits.startswith(cc):
        digits = cc + digits
    return "+" + digits


def hash_identifier(value: str, salt: bytes) -> str:
    """Keyed digest of an identifier. Truncated to 32 hex chars — 128 bits is
    far beyond collision risk at workforce scale and keeps the tables readable."""
    return hmac.new(salt, value.encode("utf-8"), sha256).hexdigest()[:32]


def make_key(
    value: object,
    rules: tuple[str, ...] = ("strip", "lower"),
    *,
    salt: bytes | None = None,
    hash_enabled: bool = True,
    country_code: str = "+44",
) -> str | None:
    """Canonicalise then (optionally) hash, producing a storable recipient key."""
    canonical = canonicalise(value, rules, country_code)
    if canonical is None:
        return None
    if hash_enabled and salt:
        return hash_identifier(canonical, salt)
    return canonical


#: How each channel's native identifier maps onto a roster column, so a
#: recipient key can be looked up in dim_employee_identity.
ID_TYPE_CANONICALISATION: dict[str, tuple[str, ...]] = {
    "email": ("strip", "lower"),
    "upn": ("strip", "lower"),
    "msisdn": ("strip", "e164"),
    "lms_user_id": ("strip", "lower"),
    "employee_no": ("strip", "upper"),
}

#: Roster columns that feed dim_employee_identity, and the id_type each becomes.
ROSTER_IDENTITY_COLUMNS: dict[str, str] = {
    "work_email": "email",
    "upn": "upn",
    "mobile": "msisdn",
    "lms_user_id": "lms_user_id",
}
