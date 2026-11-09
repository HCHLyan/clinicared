"""RN-06: release of cancelled slots to the waitlist within 10 minutes.

The release (creation of an offer to the best waitlist candidate) happens
synchronously inside the cancellation transaction, so the SLA is met by
construction; the 10-minute bound is monitored as a metric and alarm. The
notification itself still obeys RN-04 (quiet hours).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

RELEASE_SLA: timedelta = timedelta(minutes=10)
#: Slots starting sooner than this are not offered (patient cannot make it).
MIN_OFFER_LEAD: timedelta = timedelta(hours=1)
#: Time the candidate has to accept before the next candidate is offered.
OFFER_TTL: timedelta = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class WaitlistEntry:
    """A patient waiting for a slot."""

    entry_id: int
    patient_id: int
    specialty_id: int
    location_id: int | None
    requested_at: datetime
    priority: int
    is_restricted: bool


@dataclass(frozen=True, slots=True)
class ReleasedSlot:
    """A slot freed by a cancellation."""

    appointment_id: int
    specialty_id: int
    location_id: int
    scheduled_at: datetime
    cancelled_at: datetime


def is_slot_offerable(slot: ReleasedSlot, now: datetime) -> bool:
    """Return True if there is still time to offer the slot."""
    return slot.scheduled_at - now >= MIN_OFFER_LEAD


def select_candidate(
    slot: ReleasedSlot, entries: Iterable[WaitlistEntry], now: datetime, excluded_patient_ids: frozenset[int] = frozenset()
) -> WaitlistEntry | None:
    """Pick the waitlist entry that receives the offer.

    Ordering: higher ``priority`` first, then oldest ``requested_at``, then
    lowest id (fully deterministic, hence idempotent). Patients restricted by
    RN-07 and patients already offered this slot are excluded.

    Args:
        slot: The released slot.
        entries: Active (WAITING) waitlist entries.
        now: Current instant.
        excluded_patient_ids: Patients who already declined/expired an offer
            for this slot.

    Returns:
        The selected entry or ``None``.
    """
    if not is_slot_offerable(slot, now):
        return None
    eligible = [
        entry
        for entry in entries
        if entry.specialty_id == slot.specialty_id
        and (entry.location_id is None or entry.location_id == slot.location_id)
        and not entry.is_restricted
        and entry.patient_id not in excluded_patient_ids
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda e: (-e.priority, e.requested_at, e.entry_id))


def offer_expiry(offered_at: datetime, scheduled_at: datetime) -> datetime:
    """Return when an offer lapses (TTL, but never after slot start minus lead)."""
    return min(offered_at + OFFER_TTL, scheduled_at - MIN_OFFER_LEAD)


def released_within_sla(cancelled_at: datetime, offered_at: datetime) -> bool:
    """Return True if the offer was created within the RN-06 SLA."""
    return offered_at - cancelled_at <= RELEASE_SLA
