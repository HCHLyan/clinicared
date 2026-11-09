"""CA-06 (RN-06): cancellation releases the slot to the waitlist within 10 minutes."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.domain.rules.waitlist import (
    ReleasedSlot,
    WaitlistEntry,
    offer_expiry,
    released_within_sla,
    select_candidate,
)

NOW = datetime(2026, 11, 10, 9, 0)
SLOT = ReleasedSlot(appointment_id=10, specialty_id=3, location_id=1, scheduled_at=NOW + timedelta(days=1), cancelled_at=NOW)


def _entry(entry_id: int, *, priority: int = 0, minutes_ago: int = 60, location: int | None = 1, specialty: int = 3, restricted: bool = False) -> WaitlistEntry:
    return WaitlistEntry(entry_id, 100 + entry_id, specialty, location, NOW - timedelta(minutes=minutes_ago), priority, restricted)


def test_priority_then_fifo_then_id() -> None:
    entries = [_entry(1, minutes_ago=500), _entry(2, priority=5, minutes_ago=10), _entry(3, priority=5, minutes_ago=20)]
    chosen = select_candidate(SLOT, entries, NOW)
    assert chosen is not None and chosen.entry_id == 3


def test_filters_specialty_location_restriction_and_previous_offers() -> None:
    entries = [
        _entry(1, specialty=99),
        _entry(2, location=7),
        _entry(3, restricted=True),  # RN-07
        _entry(4, location=None),  # any branch
        _entry(5),
    ]
    chosen = select_candidate(SLOT, entries, NOW, excluded_patient_ids=frozenset({104}))
    assert chosen is not None and chosen.entry_id == 5


def test_no_offer_when_slot_starts_within_an_hour() -> None:
    soon = ReleasedSlot(10, 3, 1, NOW + timedelta(minutes=59), NOW)
    assert select_candidate(soon, [_entry(1)], NOW) is None


def test_sla_is_ten_minutes_inclusive() -> None:
    assert released_within_sla(NOW, NOW + timedelta(minutes=10))
    assert not released_within_sla(NOW, NOW + timedelta(minutes=10, seconds=1))


def test_offer_expiry_never_beyond_slot_minus_lead() -> None:
    assert offer_expiry(NOW, NOW + timedelta(days=1)) == NOW + timedelta(minutes=30)
    assert offer_expiry(NOW, NOW + timedelta(minutes=80)) == NOW + timedelta(minutes=20)


def test_selection_is_deterministic() -> None:
    entries = [_entry(i, priority=i % 3, minutes_ago=i * 7) for i in range(1, 30)]
    assert select_candidate(SLOT, entries, NOW) == select_candidate(SLOT, list(reversed(entries)), NOW)
