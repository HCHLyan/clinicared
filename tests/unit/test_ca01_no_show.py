"""CA-01 (RN-01): no-show definition, including both boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.domain.enums import AttendanceOutcome
from src.domain.rules.no_show import count_no_shows_in_window, evaluate_attendance

SCHEDULED = datetime(2026, 11, 10, 9, 0)
AFTER = SCHEDULED + timedelta(hours=2)


@pytest.mark.parametrize(
    ("admitted_offset_min", "expected", "reason"),
    [
        (-30, AttendanceOutcome.ATTENDED, "ADMITTED_WITHIN_GRACE"),
        (20, AttendanceOutcome.ATTENDED, "ADMITTED_WITHIN_GRACE"),
        (21, AttendanceOutcome.NO_SHOW, "ADMITTED_AFTER_GRACE"),
    ],
)
def test_admission_grace_boundary(admitted_offset_min: int, expected: AttendanceOutcome, reason: str) -> None:
    result = evaluate_attendance(
        scheduled_at=SCHEDULED, as_of=AFTER, admitted_at=SCHEDULED + timedelta(minutes=admitted_offset_min)
    )
    assert (result.outcome, result.reason) == (expected, reason)


def test_no_admission_is_no_show_after_grace() -> None:
    assert evaluate_attendance(scheduled_at=SCHEDULED, as_of=AFTER).outcome is AttendanceOutcome.NO_SHOW


def test_pending_while_grace_open() -> None:
    result = evaluate_attendance(scheduled_at=SCHEDULED, as_of=SCHEDULED + timedelta(minutes=19))
    assert result.outcome is AttendanceOutcome.PENDING_EVALUATION


def test_cancellation_exactly_four_hours_before_is_not_no_show() -> None:
    result = evaluate_attendance(scheduled_at=SCHEDULED, as_of=AFTER, cancelled_at=SCHEDULED - timedelta(hours=4))
    assert result.outcome is AttendanceOutcome.CANCELLED_ON_TIME


def test_late_cancellation_counts_as_no_show() -> None:
    result = evaluate_attendance(
        scheduled_at=SCHEDULED, as_of=AFTER, cancelled_at=SCHEDULED - timedelta(hours=3, minutes=59)
    )
    assert (result.outcome, result.reason) == (AttendanceOutcome.NO_SHOW, "LATE_CANCELLATION")


def test_evaluation_is_idempotent() -> None:
    kwargs = {"scheduled_at": SCHEDULED, "as_of": AFTER, "admitted_at": SCHEDULED + timedelta(minutes=25)}
    assert evaluate_attendance(**kwargs) == evaluate_attendance(**kwargs)


def test_six_month_window_uses_calendar_months_and_is_half_open() -> None:
    as_of = datetime(2026, 8, 31, 12, 0)
    history = [
        datetime(2026, 2, 28, 12, 0),  # window start (clamped from Feb 31) -> included
        datetime(2026, 2, 28, 11, 59),  # just before -> excluded
        datetime(2026, 8, 31, 12, 0),  # == as_of -> excluded (upper bound open)
        datetime(2026, 5, 1, 8, 0),
    ]
    assert count_no_shows_in_window(history, as_of) == 2
