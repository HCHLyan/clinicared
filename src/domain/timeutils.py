"""Small, dependency-free date helpers."""

from __future__ import annotations

import calendar
from datetime import date, datetime


def subtract_months(value: datetime, months: int) -> datetime:
    """Return ``value`` shifted back by a number of calendar months.

    The day is clamped to the last valid day of the target month, so
    ``2026-08-31 - 6 months`` yields ``2026-02-28``.

    Args:
        value: Reference datetime.
        months: Non-negative number of months to subtract.

    Returns:
        The shifted datetime, preserving the time component.

    Raises:
        ValueError: If ``months`` is negative.
    """
    if months < 0:
        raise ValueError("months must be non-negative")
    month_index = value.year * 12 + (value.month - 1) - months
    year, month_zero_based = divmod(month_index, 12)
    month = month_zero_based + 1
    last_day = calendar.monthrange(year, month)[1]
    return value.replace(year=year, month=month, day=min(value.day, last_day))


def age_on(birth_date: date, on_date: date) -> int:
    """Return the age in completed years of a person on a given date.

    Args:
        birth_date: Date of birth.
        on_date: Date at which the age is evaluated.

    Returns:
        Completed years (may be negative if ``birth_date`` is in the future,
        which callers must treat as a data-quality defect).
    """
    years = on_date.year - birth_date.year
    if (on_date.month, on_date.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years
