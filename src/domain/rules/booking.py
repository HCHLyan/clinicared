"""RN-07: restricted booking for patients with >= 3 no-shows in 6 months.

Interpretation (ADR-006): restricted patients cannot book through
self-service or the call center, and are never offered waitlist slots. Staff
may book on their behalf with a written justification, which is audited.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.enums import BookingChannel

RESTRICTION_THRESHOLD: int = 3
MIN_OVERRIDE_REASON_LENGTH: int = 10


@dataclass(frozen=True, slots=True)
class BookingDecision:
    """Outcome of the RN-07 check."""

    allowed: bool
    reason: str
    requires_audit: bool


def is_restricted(no_shows_last_6m: int) -> bool:
    """Return True if RN-07 restricts the patient."""
    return no_shows_last_6m >= RESTRICTION_THRESHOLD


def evaluate_booking(
    *, no_shows_last_6m: int, channel: BookingChannel, override_reason: str | None = None
) -> BookingDecision:
    """Apply RN-07 to a booking attempt.

    Args:
        no_shows_last_6m: Patient's no-shows in the last six months.
        channel: Booking channel.
        override_reason: Mandatory justification for staff overrides.

    Returns:
        The booking decision.
    """
    if not is_restricted(no_shows_last_6m):
        return BookingDecision(True, "NOT_RESTRICTED", False)
    if channel is BookingChannel.STAFF:
        reason = (override_reason or "").strip()
        if len(reason) >= MIN_OVERRIDE_REASON_LENGTH:
            return BookingDecision(True, "STAFF_OVERRIDE", True)
        return BookingDecision(False, "OVERRIDE_REASON_REQUIRED", True)
    return BookingDecision(False, "RESTRICTED_NO_SHOWS_GE_3_IN_6M", True)
