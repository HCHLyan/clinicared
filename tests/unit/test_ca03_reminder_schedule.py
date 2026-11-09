"""CA-03 (RN-03 + RN-04 interplay): risk-based reminder schedule."""

from __future__ import annotations

from datetime import date, datetime

from src.domain.enums import RecipientType, ReminderKind, RiskLevel
from src.domain.messaging_calendar import MessagingCalendar
from src.domain.rules.reminders import ReminderPlanInput, plan_reminders, required_kinds

CAL = MessagingCalendar()
NOW = datetime(2026, 11, 10, 8, 0)  # Tuesday
ADULT = date(1990, 5, 5)


def _plan(scheduled: datetime, risk: RiskLevel, *, first: bool = False, now: datetime = NOW, version: int = 1):
    return plan_reminders(
        ReminderPlanInput(
            appointment_id=77,
            appointment_version=version,
            scheduled_at=scheduled,
            risk_level=risk,
            is_first_appointment=first,
            patient_birth_date=ADULT,
            has_patient_contact=True,
            has_guardian_contact=False,
            now=now,
        ),
        CAL,
    )


def test_required_kinds_per_rn03() -> None:
    assert required_kinds(RiskLevel.HIGH, False) == (ReminderKind.H72, ReminderKind.H24)
    assert required_kinds(RiskLevel.MEDIUM, False) == (ReminderKind.H24,)
    assert required_kinds(RiskLevel.LOW, False) == ()
    assert required_kinds(RiskLevel.LOW, True) == (ReminderKind.H24,)


def test_high_risk_gets_72h_and_24h() -> None:
    plan = _plan(datetime(2026, 11, 20, 10, 0), RiskLevel.HIGH)
    assert [(r.kind, r.send_at) for r in plan.reminders] == [
        (ReminderKind.H72, datetime(2026, 11, 17, 10, 0)),
        (ReminderKind.H24, datetime(2026, 11, 19, 10, 0)),
    ]
    assert all(r.recipient_type is RecipientType.PATIENT for r in plan.reminders)
    assert plan.reminders[0].priority > _plan(datetime(2026, 11, 20, 10, 0), RiskLevel.MEDIUM).reminders[0].priority


def test_medium_gets_only_24h_and_low_none_unless_first() -> None:
    assert [r.kind for r in _plan(datetime(2026, 11, 20, 10, 0), RiskLevel.MEDIUM).reminders] == [ReminderKind.H24]
    assert _plan(datetime(2026, 11, 20, 10, 0), RiskLevel.LOW).reminders == ()
    assert [r.kind for r in _plan(datetime(2026, 11, 20, 10, 0), RiskLevel.LOW, first=True).reminders] == [ReminderKind.H24]


def test_quiet_hours_roll_forward() -> None:
    plan = _plan(datetime(2026, 11, 19, 6, 30), RiskLevel.MEDIUM)
    assert plan.reminders[0].nominal_send_at == datetime(2026, 11, 18, 6, 30)
    assert plan.reminders[0].send_at == datetime(2026, 11, 18, 7, 0)


def test_holiday_rolls_backward_when_forward_is_too_late() -> None:
    # Monday 2026-11-16 is a holiday; appointment Tuesday 08:00.
    plan = _plan(datetime(2026, 11, 17, 8, 0), RiskLevel.MEDIUM)
    assert plan.reminders[0].send_at == datetime(2026, 11, 15, 19, 30)


def test_elapsed_72h_is_dropped_but_24h_kept() -> None:
    plan = _plan(datetime(2026, 11, 11, 10, 0), RiskLevel.HIGH)
    assert [r.kind for r in plan.reminders] == [ReminderKind.H24]
    assert [(s.kind, s.reason) for s in plan.skipped] == [(ReminderKind.H72, "WINDOW_ELAPSED")]


def test_last_reminder_is_sent_as_catch_up() -> None:
    plan = _plan(datetime(2026, 11, 10, 18, 0), RiskLevel.MEDIUM)
    assert plan.reminders[0].send_at == NOW


def test_too_close_to_appointment_sends_nothing() -> None:
    plan = _plan(datetime(2026, 11, 10, 9, 30), RiskLevel.HIGH)
    assert plan.reminders == ()
    assert {s.reason for s in plan.skipped} == {"APPOINTMENT_TOO_CLOSE"}


def test_keys_are_deterministic_and_versioned() -> None:
    first = _plan(datetime(2026, 11, 20, 10, 0), RiskLevel.HIGH)
    again = _plan(datetime(2026, 11, 20, 10, 0), RiskLevel.HIGH)
    rescheduled = _plan(datetime(2026, 11, 20, 10, 0), RiskLevel.HIGH, version=2)
    assert [r.idempotency_key for r in first.reminders] == [r.idempotency_key for r in again.reminders]
    assert not {r.idempotency_key for r in first.reminders} & {r.idempotency_key for r in rescheduled.reminders}
