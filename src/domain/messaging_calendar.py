"""RN-04: quiet hours and holiday restrictions for outbound messages."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

from src.domain.holidays_co import colombian_holidays

#: Messages are forbidden from QUIET_START (inclusive) to QUIET_END (exclusive).
QUIET_START: time = time(20, 0)
QUIET_END: time = time(7, 0)
#: When rolling *backwards* we target this time so the message clears the
#: queue well before quiet hours begin.
BACKWARD_ANCHOR: time = time(19, 30)
_MAX_ITERATIONS = 400


class MessagingCalendar:
    """Answers "may a message be sent at instant X?" according to RN-04.

    Args:
        holidays: Holiday dates loaded from source F5.
        include_computed_holidays: Also treat the statutory Colombian
            calendar as holidays (protects against an incomplete F5 load).
    """

    def __init__(self, holidays: Iterable[date] = (), *, include_computed_holidays: bool = True) -> None:
        self._holidays: frozenset[date] = frozenset(holidays)
        self._include_computed = include_computed_holidays

    def is_holiday(self, day: date) -> bool:
        """Return True if ``day`` is a public holiday."""
        if day in self._holidays:
            return True
        return self._include_computed and day in colombian_holidays(day.year)

    def is_valid_send_time(self, instant: datetime) -> bool:
        """Return True if a message may be sent at ``instant``."""
        if self.is_holiday(instant.date()):
            return False
        return QUIET_END <= instant.time() < QUIET_START

    def next_valid_slot(self, instant: datetime) -> datetime:
        """Roll ``instant`` forward to the first allowed sending instant.

        Args:
            instant: Desired sending time.

        Returns:
            ``instant`` itself if allowed, otherwise the next 07:00 of a
            non-holiday day.

        Raises:
            RuntimeError: If no valid slot exists within ~a year (corrupt
                holiday data).
        """
        candidate = instant
        for _ in range(_MAX_ITERATIONS):
            day = candidate.date()
            if self.is_holiday(day):
                candidate = datetime.combine(day + timedelta(days=1), QUIET_END)
                continue
            if candidate.time() < QUIET_END:
                candidate = datetime.combine(day, QUIET_END)
                continue
            if candidate.time() >= QUIET_START:
                candidate = datetime.combine(day + timedelta(days=1), QUIET_END)
                continue
            return candidate
        raise RuntimeError(f"No valid forward messaging slot found from {instant.isoformat()}")

    def previous_valid_slot(self, instant: datetime) -> datetime:
        """Roll ``instant`` backward to the latest allowed sending instant.

        Used only as a fallback when rolling forward would deliver the
        reminder too close to (or after) the appointment.

        Args:
            instant: Desired sending time.

        Returns:
            ``instant`` itself if allowed, otherwise 19:30 of the latest
            preceding non-holiday day.

        Raises:
            RuntimeError: If no valid slot exists within ~a year.
        """
        candidate = instant
        for _ in range(_MAX_ITERATIONS):
            day = candidate.date()
            if self.is_holiday(day):
                candidate = datetime.combine(day - timedelta(days=1), BACKWARD_ANCHOR)
                continue
            if candidate.time() >= QUIET_START:
                candidate = datetime.combine(day, BACKWARD_ANCHOR)
                continue
            if candidate.time() < QUIET_END:
                candidate = datetime.combine(day - timedelta(days=1), BACKWARD_ANCHOR)
                continue
            return candidate
        raise RuntimeError(f"No valid backward messaging slot found from {instant.isoformat()}")
