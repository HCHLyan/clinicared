"""CA-09 (RN-11): messages for minors go only to the registered guardian."""

from __future__ import annotations

from datetime import date, datetime

from src.domain.enums import RecipientType, ReminderKind, RiskLevel
from src.domain.messaging_calendar import MessagingCalendar
from src.domain.rules.reminders import ReminderPlanInput, plan_reminders, resolve_recipient
from src.services.templates import render_message

APPOINTMENT = datetime(2026, 11, 20, 10, 0)


def _recipient(birth: date | None, patient: bool = True, guardian: bool = True) -> RecipientType | str:
    return resolve_recipient(
        birth_date=birth, appointment_date=APPOINTMENT.date(), has_patient_contact=patient, has_guardian_contact=guardian
    )


def test_minor_goes_to_guardian() -> None:
    assert _recipient(date(2012, 3, 1)) is RecipientType.GUARDIAN


def test_minor_without_guardian_gets_nothing() -> None:
    assert _recipient(date(2012, 3, 1), guardian=False) == "MINOR_WITHOUT_GUARDIAN"


def test_turning_18_on_appointment_day_is_adult() -> None:
    assert _recipient(date(2008, 11, 20)) is RecipientType.PATIENT
    assert _recipient(date(2008, 11, 21)) is RecipientType.GUARDIAN


def test_unknown_age_is_conservative() -> None:
    assert _recipient(None) is RecipientType.GUARDIAN
    assert _recipient(None, guardian=False) == "UNKNOWN_AGE_NO_GUARDIAN"


def test_adult_without_contact_is_reported() -> None:
    assert _recipient(date(1980, 1, 1), patient=False, guardian=False) == "NO_PATIENT_CONTACT"


def test_planner_never_targets_the_minor() -> None:
    plan = plan_reminders(
        ReminderPlanInput(
            appointment_id=5,
            appointment_version=1,
            scheduled_at=APPOINTMENT,
            risk_level=RiskLevel.HIGH,
            is_first_appointment=False,
            patient_birth_date=date(2015, 6, 1),
            has_patient_contact=True,
            has_guardian_contact=True,
            now=datetime(2026, 11, 10, 8, 0),
        ),
        MessagingCalendar(),
    )
    assert len(plan.reminders) == 2
    assert {r.recipient_type for r in plan.reminders} == {RecipientType.GUARDIAN}


def test_guardian_wording_and_data_minimization() -> None:
    text = render_message(
        kind=ReminderKind.H24, recipient_type=RecipientType.GUARDIAN, scheduled_at=APPOINTMENT, branch_name="Sede Norte"
    )
    assert "acudido" in text
    for forbidden in ("Cardiología", "Pediatría", "Dr.", "diagnóstico"):
        assert forbidden not in text
