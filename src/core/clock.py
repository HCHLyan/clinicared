"""Single source of "now" in clinic-local time."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Bogota")


def local_now() -> datetime:
    """Return the current naive local time (America/Bogota, UTC-5, no DST)."""
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)
