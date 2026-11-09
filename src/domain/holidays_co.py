"""Colombian public holiday calendar (fallback for source F5).

Source F5 (official holiday feed) is the system of record. This module
computes the same calendar from Law 51 of 1983 ("Ley Emiliani") so the
platform keeps working, and tests are deterministic, when the feed for a
year has not been loaded yet. Loaders merge both sets.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def easter_sunday(year: int) -> date:
    """Compute Western Easter Sunday (anonymous Gregorian algorithm).

    Args:
        year: Gregorian year.

    Returns:
        Date of Easter Sunday.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _next_monday(value: date) -> date:
    """Move a date to the following Monday unless it already is one."""
    return value + timedelta(days=(7 - value.weekday()) % 7)


@lru_cache(maxsize=64)
def colombian_holidays(year: int) -> frozenset[date]:
    """Return every national public holiday in Colombia for ``year``.

    Args:
        year: Gregorian year.

    Returns:
        Immutable set of holiday dates.
    """
    fixed = {
        date(year, 1, 1),
        date(year, 5, 1),
        date(year, 7, 20),
        date(year, 8, 7),
        date(year, 12, 8),
        date(year, 12, 25),
    }
    moved_to_monday = {
        _next_monday(date(year, 1, 6)),
        _next_monday(date(year, 3, 19)),
        _next_monday(date(year, 6, 29)),
        _next_monday(date(year, 8, 15)),
        _next_monday(date(year, 10, 12)),
        _next_monday(date(year, 11, 1)),
        _next_monday(date(year, 11, 11)),
    }
    easter = easter_sunday(year)
    easter_based = {
        easter - timedelta(days=3),  # Holy Thursday
        easter - timedelta(days=2),  # Good Friday
        _next_monday(easter + timedelta(days=39)),  # Ascension
        _next_monday(easter + timedelta(days=60)),  # Corpus Christi
        _next_monday(easter + timedelta(days=68)),  # Sacred Heart
    }
    return frozenset(fixed | moved_to_monday | easter_based)
