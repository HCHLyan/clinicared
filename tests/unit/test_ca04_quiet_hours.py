"""CA-04 (RN-04): quiet hours 20:00-07:00 and holidays (F5 + statutory fallback)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from src.domain.holidays_co import colombian_holidays, easter_sunday
from src.domain.messaging_calendar import MessagingCalendar

CAL = MessagingCalendar()


@pytest.mark.parametrize(
    ("instant", "valid"),
    [
        (datetime(2026, 11, 10, 6, 59), False),
        (datetime(2026, 11, 10, 7, 0), True),
        (datetime(2026, 11, 10, 19, 59), True),
        (datetime(2026, 11, 10, 20, 0), False),
        (datetime(2026, 12, 8, 10, 0), False),  # Inmaculada Concepción
    ],
)
def test_valid_send_time(instant: datetime, valid: bool) -> None:
    assert CAL.is_valid_send_time(instant) is valid


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 11, 10, 21, 0), datetime(2026, 11, 11, 7, 0)),
        (datetime(2026, 11, 10, 3, 0), datetime(2026, 11, 10, 7, 0)),
        (datetime(2026, 12, 8, 10, 0), datetime(2026, 12, 9, 7, 0)),
        (datetime(2026, 11, 15, 21, 0), datetime(2026, 11, 17, 7, 0)),  # Sun night -> Mon holiday -> Tue
        (datetime(2026, 11, 10, 12, 0), datetime(2026, 11, 10, 12, 0)),
    ],
)
def test_next_valid_slot(instant: datetime, expected: datetime) -> None:
    assert CAL.next_valid_slot(instant) == expected


def test_previous_valid_slot_skips_holiday() -> None:
    assert CAL.previous_valid_slot(datetime(2026, 11, 16, 8, 0)) == datetime(2026, 11, 15, 19, 30)


def test_f5_holidays_are_honoured_on_top_of_statutory_calendar() -> None:
    custom = MessagingCalendar({date(2026, 11, 12)})
    assert not custom.is_valid_send_time(datetime(2026, 11, 12, 10, 0))
    assert custom.next_valid_slot(datetime(2026, 11, 12, 10, 0)) == datetime(2026, 11, 13, 7, 0)


def test_colombian_2026_calendar() -> None:
    assert easter_sunday(2026) == date(2026, 4, 5)
    expected = {
        date(2026, 1, 1), date(2026, 1, 12), date(2026, 3, 23), date(2026, 4, 2), date(2026, 4, 3),
        date(2026, 5, 1), date(2026, 5, 18), date(2026, 6, 8), date(2026, 6, 15), date(2026, 6, 29),
        date(2026, 7, 20), date(2026, 8, 7), date(2026, 8, 17), date(2026, 10, 12), date(2026, 11, 2),
        date(2026, 11, 16), date(2026, 12, 8), date(2026, 12, 25),
    }
    assert colombian_holidays(2026) == expected
