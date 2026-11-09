"""Deterministic idempotency keys.

A key is derived only from business identity (never from wall-clock time or
random values), so every producer that plans "the same message" computes the
same key. The database enforces uniqueness on it, the broker carries it as
``message_id`` and the messaging provider receives it as its own
idempotency header: three layers of deduplication.
"""

from __future__ import annotations

import hashlib

KEY_VERSION = "v1"


def build_idempotency_key(namespace: str, *parts: object) -> str:
    """Build a stable, opaque idempotency key.

    Args:
        namespace: Logical namespace such as ``"reminder"`` or ``"waitlist"``.
        *parts: Business identifiers; ``None`` is encoded as an empty string.

    Returns:
        ``"<namespace>:v1:<sha256-hex>"`` (at most 80 characters).

    Raises:
        ValueError: If no parts are supplied or the namespace is empty.
    """
    if not namespace:
        raise ValueError("namespace is required")
    if not parts:
        raise ValueError("at least one identity part is required")
    canonical = "|".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(f"{namespace}|{KEY_VERSION}|{canonical}".encode()).hexdigest()
    return f"{namespace}:{KEY_VERSION}:{digest[:64]}"


def reminder_key(appointment_id: int, appointment_version: int, kind: str, recipient_type: str) -> str:
    """Idempotency key of a scheduled reminder.

    The appointment version is part of the identity: rescheduling bumps the
    version and therefore produces new reminders, while every older reminder
    becomes stale and is skipped by the dispatcher.
    """
    return build_idempotency_key("reminder", appointment_id, appointment_version, kind, recipient_type)


def waitlist_offer_key(released_appointment_id: int, waitlist_entry_id: int) -> str:
    """Idempotency key of a waitlist offer notification (RN-06)."""
    return build_idempotency_key("waitlist", released_appointment_id, waitlist_entry_id)
