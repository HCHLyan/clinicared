"""Enumerations shared across the domain, persistence and messaging layers."""

from __future__ import annotations

from enum import StrEnum


class RiskLevel(StrEnum):
    """No-show risk level assigned by RN-02."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AppointmentStatus(StrEnum):
    """Lifecycle status of an appointment in the operational database."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    ATTENDED = "ATTENDED"
    NO_SHOW = "NO_SHOW"


class AttendanceOutcome(StrEnum):
    """Result of evaluating RN-01 for a single appointment."""

    ATTENDED = "ATTENDED"
    NO_SHOW = "NO_SHOW"
    CANCELLED_ON_TIME = "CANCELLED_ON_TIME"
    PENDING_EVALUATION = "PENDING_EVALUATION"


class ReminderKind(StrEnum):
    """Type of outbound message tied to an appointment."""

    H72 = "H72"
    H24 = "H24"
    WAITLIST_OFFER = "WAITLIST_OFFER"


class RecipientType(StrEnum):
    """Who receives a message (RN-11)."""

    PATIENT = "PATIENT"
    GUARDIAN = "GUARDIAN"


class Channel(StrEnum):
    """Outbound messaging channel."""

    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"


class DispatchStatus(StrEnum):
    """State machine of a row in ``messages`` (one outbound message)."""

    PLANNED = "PLANNED"
    ENQUEUED = "ENQUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


#: States from which a message must never be sent again.
TERMINAL_DISPATCH_STATUSES: frozenset[DispatchStatus] = frozenset(
    {
        DispatchStatus.SENT,
        DispatchStatus.DELIVERED,
        DispatchStatus.READ,
        DispatchStatus.FAILED,
        DispatchStatus.CANCELLED,
        DispatchStatus.SKIPPED,
    }
)


class BookingChannel(StrEnum):
    """Channel through which an appointment is booked (RN-07)."""

    SELF_SERVICE = "SELF_SERVICE"
    CALL_CENTER = "CALL_CENTER"
    STAFF = "STAFF"
    WAITLIST = "WAITLIST"
