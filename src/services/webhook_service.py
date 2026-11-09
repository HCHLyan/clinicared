"""Messaging-provider webhook processing (delivery receipts and replies).

Providers retry webhooks and deliver them out of order. This service:

* verifies the HMAC signature of the raw body;
* deduplicates by ``provider_event_id`` (insert-once table);
* applies delivery statuses monotonically (SENT < DELIVERED < READ), so a late
  "SENT" never overwrites "DELIVERED";
* treats patient confirmations ("SI"/"YES") as an idempotent update of
  ``appointments.confirmed_at`` (first write wins).

It never triggers an outbound send, so webhook storms cannot cause
duplicate messages to patients.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from src.domain.enums import DispatchStatus

STATUS_RANK: dict[DispatchStatus, int] = {
    DispatchStatus.SENDING: 0,
    DispatchStatus.SENT: 1,
    DispatchStatus.DELIVERED: 2,
    DispatchStatus.READ: 3,
}
_CLOSED_STATUSES = frozenset({DispatchStatus.FAILED, DispatchStatus.CANCELLED, DispatchStatus.SKIPPED})
CONFIRMATION_WORDS = frozenset({"si", "sí", "yes", "confirmo", "1"})


class WebhookEventType(StrEnum):
    """Event types accepted from the provider."""

    STATUS = "STATUS"
    REPLY = "REPLY"


class WebhookOutcome(StrEnum):
    """Processing result, persisted in ``webhook_events.outcome``."""

    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER_IGNORED = "OUT_OF_ORDER_IGNORED"
    UNKNOWN_MESSAGE = "UNKNOWN_MESSAGE"
    CONFIRMED = "CONFIRMED"
    ALREADY_CONFIRMED = "ALREADY_CONFIRMED"
    IGNORED = "IGNORED"


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """Normalized provider callback."""

    provider_event_id: str
    event_type: WebhookEventType
    provider_message_id: str
    occurred_at: datetime
    status: DispatchStatus | None = None
    reply_text: str | None = None


@dataclass(frozen=True, slots=True)
class MessageRef:
    """Minimal view of the message a webhook refers to."""

    idempotency_key: str
    appointment_id: int
    status: DispatchStatus
    kind: str = "H24"
    waitlist_offer_id: int | None = None


class WebhookRepository(Protocol):
    """Persistence port for webhook processing (all calls in one transaction)."""

    def register_event(self, event: WebhookEvent) -> bool:
        """Insert the event id; return False if it already existed."""
        ...

    def set_event_outcome(self, provider_event_id: str, outcome: WebhookOutcome) -> None:
        """Store the processing outcome of an event."""
        ...

    def find_message(self, provider_message_id: str) -> MessageRef | None:
        """Look up the message by provider id."""
        ...

    def advance_status(self, idempotency_key: str, status: DispatchStatus, occurred_at: datetime) -> bool:
        """Conditionally move to a higher-ranked status; False if not applied."""
        ...

    def confirm_appointment(self, appointment_id: int, confirmed_at: datetime) -> bool:
        """Set confirmed_at if still NULL; False if already confirmed."""
        ...

    def accept_waitlist_offer(self, offer_id: int, accepted_at: datetime) -> bool:
        """Book the offered slot; False if the offer is no longer available."""
        ...


def verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify ``X-Signature: sha256=<hex>`` with a constant-time comparison."""
    if not signature_header or not secret or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


class WebhookService:
    """Apply provider callbacks idempotently.

    Args:
        repository: Transaction-scoped repository.
    """

    def __init__(self, repository: WebhookRepository) -> None:
        self._repo = repository

    def process(self, event: WebhookEvent) -> WebhookOutcome:
        """Process one event exactly once."""
        if not self._repo.register_event(event):
            return WebhookOutcome.DUPLICATE
        outcome = self._apply(event)
        self._repo.set_event_outcome(event.provider_event_id, outcome)
        return outcome

    def _apply(self, event: WebhookEvent) -> WebhookOutcome:
        message = self._repo.find_message(event.provider_message_id)
        if message is None:
            return WebhookOutcome.UNKNOWN_MESSAGE

        if event.event_type is WebhookEventType.REPLY:
            text = (event.reply_text or "").strip().lower()
            if text not in CONFIRMATION_WORDS:
                return WebhookOutcome.IGNORED
            if message.kind == "WAITLIST_OFFER" and message.waitlist_offer_id is not None:
                accepted = self._repo.accept_waitlist_offer(message.waitlist_offer_id, event.occurred_at)
                return WebhookOutcome.CONFIRMED if accepted else WebhookOutcome.ALREADY_CONFIRMED
            applied = self._repo.confirm_appointment(message.appointment_id, event.occurred_at)
            return WebhookOutcome.CONFIRMED if applied else WebhookOutcome.ALREADY_CONFIRMED

        new_status = event.status
        if new_status is None or message.status in _CLOSED_STATUSES:
            return WebhookOutcome.IGNORED
        current_rank = STATUS_RANK.get(message.status, -1)
        if new_status is DispatchStatus.FAILED:
            # A failure receipt only counts if nothing better was reported.
            if current_rank >= STATUS_RANK[DispatchStatus.DELIVERED]:
                return WebhookOutcome.OUT_OF_ORDER_IGNORED
        elif new_status not in STATUS_RANK:
            return WebhookOutcome.IGNORED
        elif STATUS_RANK[new_status] <= current_rank:
            return WebhookOutcome.OUT_OF_ORDER_IGNORED

        applied = self._repo.advance_status(message.idempotency_key, new_status, event.occurred_at)
        return WebhookOutcome.APPLIED if applied else WebhookOutcome.OUT_OF_ORDER_IGNORED
