"""CA-07 (RN-07): restricted booking with >= 3 no-shows in six months."""

from __future__ import annotations

import pytest

from src.domain.enums import BookingChannel
from src.domain.rules.booking import evaluate_booking, is_restricted


def test_threshold() -> None:
    assert not is_restricted(2)
    assert is_restricted(3)


@pytest.mark.parametrize("channel", [BookingChannel.SELF_SERVICE, BookingChannel.CALL_CENTER, BookingChannel.WAITLIST])
def test_restricted_patient_blocked_outside_staff(channel: BookingChannel) -> None:
    decision = evaluate_booking(no_shows_last_6m=3, channel=channel)
    assert (decision.allowed, decision.reason) == (False, "RESTRICTED_NO_SHOWS_GE_3_IN_6M")


def test_staff_override_requires_justification_and_is_audited() -> None:
    denied = evaluate_booking(no_shows_last_6m=4, channel=BookingChannel.STAFF, override_reason="ok")
    allowed = evaluate_booking(
        no_shows_last_6m=4, channel=BookingChannel.STAFF, override_reason="Paciente oncológico, autorizado por coordinación"
    )
    assert (denied.allowed, denied.reason) == (False, "OVERRIDE_REASON_REQUIRED")
    assert (allowed.allowed, allowed.reason, allowed.requires_audit) == (True, "STAFF_OVERRIDE", True)


def test_unrestricted_patient_books_anywhere() -> None:
    decision = evaluate_booking(no_shows_last_6m=2, channel=BookingChannel.SELF_SERVICE)
    assert decision.allowed and not decision.requires_audit
