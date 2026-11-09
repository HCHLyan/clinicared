"""Ports (interfaces) between services and infrastructure adapters.

Production adapters live in ``src.repositories`` and ``src.gateways``; tests
use in-memory fakes. This keeps the idempotency logic verifiable without a
database or broker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from src.domain.enums import AppointmentStatus, Channel, DispatchStatus


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    """Persistent state of one outbound message (row in ``messages``)."""

    idempotency_key: str
    appointment_id: int
    appointment_version: int
    kind: str
    channel: Channel
    recipient_address: str | None
    body: str
    send_at: datetime
    status: DispatchStatus
    attempts: int
    provider_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class AppointmentState:
    """Current state of an appointment, used to detect stale messages."""

    appointment_id: int
    version: int
    status: AppointmentStatus
    scheduled_at: datetime


@dataclass(frozen=True, slots=True)
class GatewayResult:
    """Provider acknowledgement of an accepted message."""

    provider_message_id: str
    cost_cop: Decimal


class TransientGatewayError(Exception):
    """Provider failure worth retrying (timeouts, 5xx, 429)."""


class PermanentGatewayError(Exception):
    """Provider rejection that will never succeed (invalid number, 4xx)."""


class DispatchRepository(Protocol):
    """Persistence operations required by the dispatcher."""

    def get(self, idempotency_key: str) -> DispatchRecord | None:
        """Return the message or None."""
        ...

    def appointment_state(self, appointment_id: int) -> AppointmentState | None:
        """Return the current appointment state or None if it does not exist."""
        ...

    def open_offer_expiry(self, idempotency_key: str) -> datetime | None:
        """Return the expiry of the OPEN waitlist offer behind a message, else None."""
        ...

    def try_claim(self, idempotency_key: str, now: datetime, lease: timedelta) -> bool:
        """Atomically move PLANNED/ENQUEUED (or SENDING with expired lease) to SENDING."""
        ...

    def mark_sent(self, idempotency_key: str, result: GatewayResult, now: datetime) -> None:
        """Record a successful hand-off to the provider."""
        ...

    def release_for_retry(self, idempotency_key: str, error: str, now: datetime) -> None:
        """Return a SENDING message to ENQUEUED after a transient error."""
        ...

    def mark_failed(self, idempotency_key: str, error: str, now: datetime) -> None:
        """Terminal failure."""
        ...

    def mark_skipped(self, idempotency_key: str, reason: str, now: datetime) -> None:
        """Terminal skip (stale, cancelled, appointment passed...)."""
        ...

    def reschedule(self, idempotency_key: str, send_at: datetime, now: datetime) -> None:
        """Return the message to PLANNED with a new send time."""
        ...


class MessageGateway(Protocol):
    """Outbound messaging provider (SMS/WhatsApp/e-mail)."""

    def send(self, *, idempotency_key: str, channel: Channel, to: str, body: str) -> GatewayResult:
        """Send one message. Must forward ``idempotency_key`` to the provider.

        Raises:
            TransientGatewayError: Retryable failure.
            PermanentGatewayError: Non-retryable failure.
        """
        ...


class Clock(Protocol):
    """Injectable clock returning naive local (America/Bogota) time."""

    def __call__(self) -> datetime:
        """Return the current local time."""
        ...
