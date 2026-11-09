"""RN-01: no-show definition.

An appointment is a no-show when there is no admission (check-in, source F3)
up to 20 minutes after the scheduled time, unless it was cancelled at least
4 hours before the scheduled time. Late cancellations count as no-shows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.domain.enums import AttendanceOutcome
from src.domain.timeutils import subtract_months

NO_SHOW_GRACE: timedelta = timedelta(minutes=20)
CANCELLATION_NOTICE: timedelta = timedelta(hours=4)
HISTORY_WINDOW_MONTHS: int = 6


@dataclass(frozen=True, slots=True)
class AttendanceEvaluation:
    """Outcome of RN-01 plus a machine-readable reason code."""

    outcome: AttendanceOutcome
    reason: str


def evaluate_attendance(
    *,
    scheduled_at: datetime,
    as_of: datetime,
    admitted_at: datetime | None = None,
    cancelled_at: datetime | None = None,
) -> AttendanceEvaluation:
    """Classify an appointment according to RN-01.

    Args:
        scheduled_at: Scheduled start of the appointment.
        as_of: Evaluation instant; outcomes are only final once
            ``as_of >= scheduled_at + 20 min``.
        admitted_at: Earliest admission timestamp from F3, if any.
        cancelled_at: Cancellation timestamp, if the appointment was cancelled.

    Returns:
        The attendance evaluation. Re-evaluating with the same inputs always
        produces the same result (idempotent).
    """
    if cancelled_at is not None and cancelled_at <= scheduled_at - CANCELLATION_NOTICE:
        return AttendanceEvaluation(AttendanceOutcome.CANCELLED_ON_TIME, "CANCELLED_GE_4H")

    deadline = scheduled_at + NO_SHOW_GRACE
    if admitted_at is not None and admitted_at <= deadline:
        return AttendanceEvaluation(AttendanceOutcome.ATTENDED, "ADMITTED_WITHIN_GRACE")

    if as_of < deadline:
        return AttendanceEvaluation(AttendanceOutcome.PENDING_EVALUATION, "GRACE_PERIOD_OPEN")

    if cancelled_at is not None:
        reason = "LATE_CANCELLATION"
    elif admitted_at is not None:
        reason = "ADMITTED_AFTER_GRACE"
    else:
        reason = "NO_ADMISSION"
    return AttendanceEvaluation(AttendanceOutcome.NO_SHOW, reason)


def count_no_shows_in_window(no_show_dates: Iterable[datetime], as_of: datetime) -> int:
    """Count no-shows in the six calendar months preceding ``as_of``.

    Args:
        no_show_dates: Scheduled datetimes of the patient's past no-shows.
        as_of: Reference instant (exclusive upper bound).

    Returns:
        Number of no-shows in ``[as_of - 6 months, as_of)``.
    """
    window_start = subtract_months(as_of, HISTORY_WINDOW_MONTHS)
    return sum(1 for scheduled in no_show_dates if window_start <= scheduled < as_of)
