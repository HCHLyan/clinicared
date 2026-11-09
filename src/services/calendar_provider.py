"""Cached :class:`MessagingCalendar` backed by the ``holidays`` table (F5)."""

from __future__ import annotations

import logging
import time
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.domain.messaging_calendar import MessagingCalendar

logger = logging.getLogger(__name__)


def load_holidays(engine: Engine) -> set[date]:
    """Read every holiday loaded from F5."""
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(text("SELECT holiday_date FROM holidays"))}


class CachedCalendarProvider:
    """Return a calendar refreshed at most every ``ttl_seconds``.

    On a refresh failure the previous calendar is kept (the statutory
    fallback in :mod:`src.domain.holidays_co` still applies), so a database
    blip never opens the door to messages on holidays.

    Args:
        engine: SQLAlchemy engine.
        ttl_seconds: Cache lifetime.
    """

    def __init__(self, engine: Engine, ttl_seconds: int = 3600) -> None:
        self._engine = engine
        self._ttl = ttl_seconds
        self._calendar = MessagingCalendar()
        self._loaded_at = 0.0

    def get(self) -> MessagingCalendar:
        """Return the current calendar, refreshing it if stale."""
        if time.monotonic() - self._loaded_at > self._ttl:
            try:
                self._calendar = MessagingCalendar(load_holidays(self._engine))
                self._loaded_at = time.monotonic()
            except SQLAlchemyError:
                logger.exception("holiday refresh failed; keeping previous calendar")
        return self._calendar
