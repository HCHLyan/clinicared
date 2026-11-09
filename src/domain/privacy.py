"""RN-09: pseudonymization and data minimization for the analytical layer.

The pseudonym is ``sha256(pepper || "|" || normalized_national_id)``. The
same formula is implemented in Spark with the native ``sha2`` function (no
Python UDF), so keys produced by the API, the simulation and the Gold layer
are identical. The pepper is a secret stored in AWS Secrets Manager; without
it, pseudonyms cannot be reversed by dictionary attack over the (small)
national-id space. See ADR-009 for the HMAC trade-off discussion.
"""

from __future__ import annotations

import hashlib
import re

MIN_PEPPER_LENGTH = 32
_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")

AGE_BANDS: tuple[tuple[int, int, str], ...] = (
    (0, 17, "0-17"),
    (18, 29, "18-29"),
    (30, 44, "30-44"),
    (45, 59, "45-59"),
    (60, 74, "60-74"),
    (75, 200, "75+"),
)


def normalize_national_id(national_id: str) -> str:
    """Strip separators and upper-case a national id (``"1.020-33"`` -> ``"102033"``)."""
    return _NON_ALNUM.sub("", national_id).upper()


class Pseudonymizer:
    """Keyed, deterministic pseudonymization of patient identifiers.

    Args:
        pepper: Secret of at least 32 characters.

    Raises:
        ValueError: If the pepper is too short.
    """

    def __init__(self, pepper: str) -> None:
        if len(pepper) < MIN_PEPPER_LENGTH:
            raise ValueError(f"pepper must have at least {MIN_PEPPER_LENGTH} characters")
        self._pepper = pepper

    def patient_key(self, national_id: str) -> str:
        """Return the 64-hex-char pseudonymous key of a patient.

        Raises:
            ValueError: If the national id is empty after normalization.
        """
        normalized = normalize_national_id(national_id)
        if not normalized:
            raise ValueError("national_id is empty")
        return hashlib.sha256(f"{self._pepper}|{normalized}".encode()).hexdigest()


def age_band(age: int | None) -> str:
    """Generalize an age into a band; invalid or unknown ages map to ``"UNKNOWN"``."""
    if age is None or age < 0:
        return "UNKNOWN"
    for low, high, label in AGE_BANDS:
        if low <= age <= high:
            return label
    return "UNKNOWN"


def has_chronic_condition(raw_conditions: str | None) -> bool:
    """Reduce the raw chronic-condition field (F1) to a boolean flag.

    The raw diagnosis list never leaves the operational/silver zone.
    """
    if raw_conditions is None:
        return False
    cleaned = raw_conditions.strip().lower()
    return cleaned not in {"", "none", "ninguna", "n/a", "na", "null"}
