"""Idempotent reminder dispatch (consumer side of the reminder queue).

Guarantees:

1. **No duplicate sends on redelivery.** A message already in a terminal
   state is acknowledged without side effects.
2. **No concurrent double send.** Only the worker that wins the atomic
   ``try_claim`` (conditional UPDATE) talks to the provider.
3. **Crash safety.** If a worker crashes after the provider accepted the
   message but before ``mark_sent``, the lease expires and another worker
   may re-send *with the same idempotency key*, which the provider
   deduplicates. This is the residual at-least-once window, closed by the
   provider contract (ADR-005).
4. **Out-of-order / stale delivery.** Messages whose appointment version
   changed, whose appointment was cancelled, or that arrive earlier than
   their ``send_at`` are skipped or deferred, never sent.
5. **RN-04 at send time.** Quiet hours are re-checked at the moment of
   sending, since queue backlogs can push a message into a quiet window.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from enum import StrEnum

from src.domain.enums import TERMINAL_DISPATCH_STATUSES, AppointmentStatus, ReminderKind
from src.domain.messaging_calendar import MessagingCalendar
from src.domain.rules.reminders import MIN_LEAD_BEFORE_APPOINTMENT
from src.services.ports import (
    Clock,
    DispatchRepository,
    MessageGateway,
    PermanentGatewayError,
    TransientGatewayError,
)

logger = logging.getLogger(__name__)

#: A message delivered up to this long before ``send_at`` is sent anyway.
EARLY_TOLERANCE: timedelta = timedelta(minutes=5)
DEFAULT_LEASE: timedelta = timedelta(minutes=2)
_INACTIVE_APPOINTMENT_STATUSES = frozenset(
    {AppointmentStatus.CANCELLED, AppointmentStatus.ATTENDED, AppointmentStatus.NO_SHOW}
)


class DispatchOutcome(StrEnum):
    """Result of handling one queue message."""

    SENT = "SENT"
    DUPLICATE = "DUPLICATE"
    SKIPPED = "SKIPPED"
    DEFERRED = "DEFERRED"
    RETRY = "RETRY"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ReminderDispatchService:
    """Handle a ``{"idempotency_key": ...}`` message from the reminder queue.

    Args:
        repository: Message persistence port.
        gateway: Messaging provider port.
        calendar: RN-04 calendar.
        clock: Local-time clock.
        lease: Claim lease duration (must exceed the provider timeout).
    """

    def __init__(
        self,
        repository: DispatchRepository,
        gateway: MessageGateway,
        calendar: MessagingCalendar,
        clock: Clock,
        lease: timedelta = DEFAULT_LEASE,
    ) -> None:
        self._repo = repository
        self._gateway = gateway
        self._calendar = calendar
        self._clock = clock
        self._lease = lease

    def handle(self, idempotency_key: str) -> DispatchOutcome:
        """Process one dispatch request.

        Args:
            idempotency_key: Key of the row in ``messages``.

        Returns:
            The outcome; the consumer maps RETRY to a delayed retry, UNKNOWN to
            the dead-letter queue and everything else to an ``ack``.
        """
        record = self._repo.get(idempotency_key)
        if record is None:
            logger.error("dispatch for unknown key", extra={"idempotency_key": idempotency_key})
            return DispatchOutcome.UNKNOWN
        if record.status in TERMINAL_DISPATCH_STATUSES:
            logger.info("duplicate delivery ignored", extra={"idempotency_key": idempotency_key, "status": record.status})
            return DispatchOutcome.DUPLICATE

        now = self._clock()
        if record.kind == ReminderKind.WAITLIST_OFFER:
            # The message references the *released* (cancelled) appointment;
            # validity is governed by the offer, not by the appointment.
            open_until = self._repo.open_offer_expiry(idempotency_key)
            if open_until is None or now >= open_until:
                self._repo.mark_skipped(idempotency_key, "OFFER_NOT_OPEN", now)
                return DispatchOutcome.SKIPPED
            latest_allowed = open_until
        else:
            appointment = self._repo.appointment_state(record.appointment_id)
            if appointment is None or appointment.status in _INACTIVE_APPOINTMENT_STATUSES:
                self._repo.mark_skipped(idempotency_key, "APPOINTMENT_INACTIVE", now)
                return DispatchOutcome.SKIPPED
            if appointment.version != record.appointment_version:
                self._repo.mark_skipped(idempotency_key, "STALE_APPOINTMENT_VERSION", now)
                return DispatchOutcome.SKIPPED
            latest_allowed = appointment.scheduled_at - MIN_LEAD_BEFORE_APPOINTMENT
            if now > latest_allowed:
                self._repo.mark_skipped(idempotency_key, "TOO_LATE_TO_REMIND", now)
                return DispatchOutcome.SKIPPED

        if now < record.send_at - EARLY_TOLERANCE:
            self._repo.reschedule(idempotency_key, record.send_at, now)
            return DispatchOutcome.DEFERRED

        if not self._calendar.is_valid_send_time(now):
            next_slot = self._calendar.next_valid_slot(now)
            if next_slot > latest_allowed:
                self._repo.mark_skipped(idempotency_key, "NO_VALID_WINDOW_AT_SEND", now)
                return DispatchOutcome.SKIPPED
            self._repo.reschedule(idempotency_key, next_slot, now)
            return DispatchOutcome.DEFERRED

        if not record.recipient_address:
            self._repo.mark_skipped(idempotency_key, "NO_RECIPIENT_ADDRESS", now)
            return DispatchOutcome.SKIPPED

        if not self._repo.try_claim(idempotency_key, now, self._lease):
            logger.info("claim lost to a concurrent worker", extra={"idempotency_key": idempotency_key})
            return DispatchOutcome.DUPLICATE

        try:
            result = self._gateway.send(
                idempotency_key=idempotency_key,
                channel=record.channel,
                to=record.recipient_address,
                body=record.body,
            )
        except TransientGatewayError as exc:
            self._repo.release_for_retry(idempotency_key, str(exc), self._clock())
            logger.warning("transient provider error", extra={"idempotency_key": idempotency_key, "error": str(exc)})
            return DispatchOutcome.RETRY
        except PermanentGatewayError as exc:
            self._repo.mark_failed(idempotency_key, str(exc), self._clock())
            logger.error("permanent provider error", extra={"idempotency_key": idempotency_key, "error": str(exc)})
            return DispatchOutcome.FAILED

        self._repo.mark_sent(idempotency_key, result, self._clock())
        logger.info(
            "message sent",
            extra={"idempotency_key": idempotency_key, "provider_message_id": result.provider_message_id},
        )
        return DispatchOutcome.SENT
