"""RN-05: controlled overbooking.

For each agenda block of a professional, one extra appointment is allowed for
every 3 HIGH-risk regular appointments, capped at +2, and never for
specialties flagged as non-overbookable in source F7.

Assumption (documented in ADR-006): an "agenda block" is the professional's
agenda within a fixed window (``OVERBOOK_BLOCK_MINUTES``, default 60) at a
branch. Only regular, non-cancelled appointments count towards the HIGH-risk
total, so overbooked appointments never justify further overbooking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

HIGH_RISK_PER_EXTRA_SLOT: int = 3
MAX_EXTRA_SLOTS: int = 2
DEFAULT_BLOCK_MINUTES: int = 60


@dataclass(frozen=True, slots=True)
class OverbookDecision:
    """Whether one more overbooked appointment fits in a block."""

    allowed: bool
    allowed_extra_slots: int
    used_extra_slots: int
    reason: str


def block_start(instant: datetime, block_minutes: int = DEFAULT_BLOCK_MINUTES) -> datetime:
    """Truncate ``instant`` to the start of its agenda block.

    Raises:
        ValueError: If ``block_minutes`` does not divide a day evenly.
    """
    if block_minutes <= 0 or (24 * 60) % block_minutes:
        raise ValueError("block_minutes must be a positive divisor of 1440")
    midnight = instant.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes = (instant - midnight) // timedelta(minutes=1)
    return midnight + timedelta(minutes=(minutes // block_minutes) * block_minutes)


def allowed_extra_slots(high_risk_regular_count: int, specialty_overbookable: bool) -> int:
    """Return the overbooking capacity of a block.

    Args:
        high_risk_regular_count: HIGH-risk, regular, non-cancelled
            appointments in the block.
        specialty_overbookable: Flag from F7.

    Returns:
        ``min(2, count // 3)`` or 0 for non-overbookable specialties.

    Raises:
        ValueError: If the count is negative.
    """
    if high_risk_regular_count < 0:
        raise ValueError("high_risk_regular_count cannot be negative")
    if not specialty_overbookable:
        return 0
    return min(MAX_EXTRA_SLOTS, high_risk_regular_count // HIGH_RISK_PER_EXTRA_SLOT)


def evaluate_overbook_request(
    *, high_risk_regular_count: int, used_extra_slots: int, specialty_overbookable: bool
) -> OverbookDecision:
    """Decide whether one additional overbooked appointment may be created."""
    if not specialty_overbookable:
        return OverbookDecision(False, 0, used_extra_slots, "SPECIALTY_NOT_OVERBOOKABLE")
    capacity = allowed_extra_slots(high_risk_regular_count, specialty_overbookable)
    if used_extra_slots >= capacity:
        reason = "NO_HIGH_RISK_QUOTA" if capacity == 0 else "OVERBOOK_CAPACITY_EXHAUSTED"
        return OverbookDecision(False, capacity, used_extra_slots, reason)
    return OverbookDecision(True, capacity, used_extra_slots, "WITHIN_CAPACITY")
