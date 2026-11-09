"""RN-03 (risk-based schedule), RN-04 (quiet hours) and RN-11 (minors).

RN-03:
    HIGH   -> reminders 72 h and 24 h before the appointment.
    MEDIUM -> reminder 24 h before.
    LOW    -> no reminder, unless it is the patient's first appointment
              (then 24 h before).
RN-04:
    Nothing is sent between 20:00 and 07:00 or on holidays; the send time is
    rolled forward to the next valid slot. If rolling forward would land too
    close to the appointment, it is rolled backward instead; if neither
    works, the reminder is skipped with an explicit reason.
RN-11:
    Messages for minors go exclusively to the registered guardian. A minor
    without a guardian receives nothing (and a data-quality flag is raised).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.domain.enums import RecipientType, ReminderKind, RiskLevel
from src.domain.idempotency import reminder_key
from src.domain.messaging_calendar import MessagingCalendar
from src.domain.timeutils import age_on

ADULT_AGE: int = 18
#: A reminder must reach the recipient at least this long before the visit.
MIN_LEAD_BEFORE_APPOINTMENT: timedelta = timedelta(hours=2)
REMINDER_OFFSETS: dict[ReminderKind, timedelta] = {
    ReminderKind.H72: timedelta(hours=72),
    ReminderKind.H24: timedelta(hours=24),
}
#: RabbitMQ priority (0-10). Waitlist offers outrank everything (RN-06).
PRIORITY_BY_RISK: dict[RiskLevel, int] = {RiskLevel.HIGH: 8, RiskLevel.MEDIUM: 5, RiskLevel.LOW: 2}
WAITLIST_OFFER_PRIORITY: int = 10


@dataclass(frozen=True, slots=True)
class ReminderPlanInput:
    """Everything needed to plan the reminders of one appointment."""

    appointment_id: int
    appointment_version: int
    scheduled_at: datetime
    risk_level: RiskLevel
    is_first_appointment: bool
    patient_birth_date: date | None
    has_patient_contact: bool
    has_guardian_contact: bool
    now: datetime


@dataclass(frozen=True, slots=True)
class PlannedReminder:
    """One reminder that must be persisted (status PLANNED)."""

    appointment_id: int
    appointment_version: int
    kind: ReminderKind
    recipient_type: RecipientType
    nominal_send_at: datetime
    send_at: datetime
    priority: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SkippedReminder:
    """A reminder required by RN-03 that cannot be sent, with the reason."""

    kind: ReminderKind | None
    reason: str


@dataclass(frozen=True, slots=True)
class ReminderPlan:
    """Result of planning: reminders to persist plus explained omissions."""

    reminders: tuple[PlannedReminder, ...]
    skipped: tuple[SkippedReminder, ...]


def required_kinds(risk_level: RiskLevel, is_first_appointment: bool) -> tuple[ReminderKind, ...]:
    """Return the reminder kinds mandated by RN-03, earliest first."""
    if risk_level is RiskLevel.HIGH:
        return (ReminderKind.H72, ReminderKind.H24)
    if risk_level is RiskLevel.MEDIUM:
        return (ReminderKind.H24,)
    return (ReminderKind.H24,) if is_first_appointment else ()


def is_minor(birth_date: date, on_date: date) -> bool:
    """Return True if the person is under 18 on ``on_date``."""
    return age_on(birth_date, on_date) < ADULT_AGE


def resolve_recipient(
    *,
    birth_date: date | None,
    appointment_date: date,
    has_patient_contact: bool,
    has_guardian_contact: bool,
) -> RecipientType | str:
    """Apply RN-11 and decide who may receive messages.

    Unknown age is handled conservatively: we only message the guardian if
    one exists, because we cannot rule out that the patient is a minor.

    Returns:
        A :class:`RecipientType`, or a reason code string when nobody may be
        contacted.
    """
    if birth_date is None:
        return RecipientType.GUARDIAN if has_guardian_contact else "UNKNOWN_AGE_NO_GUARDIAN"
    if is_minor(birth_date, appointment_date):
        return RecipientType.GUARDIAN if has_guardian_contact else "MINOR_WITHOUT_GUARDIAN"
    return RecipientType.PATIENT if has_patient_contact else "NO_PATIENT_CONTACT"


def _resolve_send_time(
    nominal: datetime, now: datetime, latest_allowed: datetime, calendar: MessagingCalendar
) -> datetime | None:
    """Find a valid send instant in ``[now, latest_allowed]`` (RN-04)."""
    forward = calendar.next_valid_slot(max(nominal, now))
    if forward <= latest_allowed:
        return forward
    backward = calendar.previous_valid_slot(nominal)
    if now <= backward <= latest_allowed:
        return backward
    return None


def plan_reminders(plan_input: ReminderPlanInput, calendar: MessagingCalendar) -> ReminderPlan:
    """Plan the reminders of an appointment applying RN-03, RN-04 and RN-11.

    Overdue reminders (nominal time already in the past) are dropped when a
    later reminder is still on time; the last reminder of the plan is kept as
    a catch-up and sent at the next valid slot.

    Args:
        plan_input: Appointment snapshot and patient context.
        calendar: Messaging calendar with holidays loaded.

    Returns:
        The reminder plan. Calling it twice with the same inputs returns the
        same idempotency keys.
    """
    kinds = required_kinds(plan_input.risk_level, plan_input.is_first_appointment)
    if not kinds:
        return ReminderPlan((), ())

    recipient = resolve_recipient(
        birth_date=plan_input.patient_birth_date,
        appointment_date=plan_input.scheduled_at.date(),
        has_patient_contact=plan_input.has_patient_contact,
        has_guardian_contact=plan_input.has_guardian_contact,
    )
    if not isinstance(recipient, RecipientType):
        return ReminderPlan((), tuple(SkippedReminder(kind, recipient) for kind in kinds))

    latest_allowed = plan_input.scheduled_at - MIN_LEAD_BEFORE_APPOINTMENT
    if plan_input.now > latest_allowed:
        return ReminderPlan((), tuple(SkippedReminder(kind, "APPOINTMENT_TOO_CLOSE") for kind in kinds))

    planned: list[PlannedReminder] = []
    skipped: list[SkippedReminder] = []
    for index, kind in enumerate(kinds):
        nominal = plan_input.scheduled_at - REMINDER_OFFSETS[kind]
        is_last = index == len(kinds) - 1
        if nominal < plan_input.now and not is_last:
            skipped.append(SkippedReminder(kind, "WINDOW_ELAPSED"))
            continue
        send_at = _resolve_send_time(nominal, plan_input.now, latest_allowed, calendar)
        if send_at is None:
            skipped.append(SkippedReminder(kind, "NO_VALID_WINDOW"))
            continue
        if planned and planned[-1].send_at == send_at:
            # Both reminders collapsed onto the same slot: keep only the later kind.
            skipped.append(SkippedReminder(planned.pop().kind, "COLLAPSED_INTO_LATER_REMINDER"))
        planned.append(
            PlannedReminder(
                appointment_id=plan_input.appointment_id,
                appointment_version=plan_input.appointment_version,
                kind=kind,
                recipient_type=recipient,
                nominal_send_at=nominal,
                send_at=send_at,
                priority=PRIORITY_BY_RISK[plan_input.risk_level],
                idempotency_key=reminder_key(
                    plan_input.appointment_id, plan_input.appointment_version, kind.value, recipient.value
                ),
            )
        )
    return ReminderPlan(tuple(planned), tuple(skipped))
