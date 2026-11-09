"""PostgreSQL implementation of :class:`~src.services.webhook_service.WebhookRepository`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.domain.enums import DispatchStatus
from src.services.appointment_service import BusinessRuleViolation, NotFoundError, accept_waitlist_offer
from src.services.webhook_service import MessageRef, WebhookEvent, WebhookOutcome

_RANKED = ("SENDING", "SENT", "DELIVERED", "READ")


class SqlWebhookRepository:
    """Transaction-scoped repository; the caller owns commit/rollback.

    Args:
        conn: Connection with an open transaction.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def register_event(self, event: WebhookEvent) -> bool:
        """Insert-once on ``provider_event_id``."""
        sql = text(
            """
            INSERT INTO webhook_events (provider_event_id, event_type, provider_message_id, occurred_at, outcome)
            VALUES (:eid, :etype, :pid, :occurred, 'PROCESSING')
            ON CONFLICT (provider_event_id) DO NOTHING
            """
        )
        result = self._conn.execute(
            sql,
            {
                "eid": event.provider_event_id,
                "etype": event.event_type.value,
                "pid": event.provider_message_id,
                "occurred": event.occurred_at,
            },
        )
        return result.rowcount == 1

    def set_event_outcome(self, provider_event_id: str, outcome: WebhookOutcome) -> None:
        """Persist the outcome for traceability."""
        self._conn.execute(
            text("UPDATE webhook_events SET outcome = :o WHERE provider_event_id = :eid"),
            {"o": outcome.value, "eid": provider_event_id},
        )

    def find_message(self, provider_message_id: str) -> MessageRef | None:
        """Lock and return the referenced message."""
        row = (
            self._conn.execute(
                text(
                    """
                    SELECT idempotency_key, appointment_id, status, message_type, waitlist_offer_id FROM messages
                     WHERE provider_message_id = :pid FOR UPDATE
                    """
                ),
                {"pid": provider_message_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return MessageRef(
            idempotency_key=row["idempotency_key"],
            appointment_id=row["appointment_id"],
            status=DispatchStatus(row["status"]),
            kind=row["message_type"],
            waitlist_offer_id=row["waitlist_offer_id"],
        )

    def accept_waitlist_offer(self, offer_id: int, accepted_at: datetime) -> bool:
        """Delegate to the transactional use case; business refusals return False."""
        try:
            accept_waitlist_offer(self._conn, offer_id, accepted_at)
        except (BusinessRuleViolation, NotFoundError):
            return False
        return True

    def advance_status(self, idempotency_key: str, status: DispatchStatus, occurred_at: datetime) -> bool:
        """Monotonic transition guarded in SQL as well (defence in depth)."""
        if status is DispatchStatus.FAILED:
            allowed_from: tuple[str, ...] = ("SENDING", "SENT")
        else:
            allowed_from = _RANKED[: _RANKED.index(status.value)]
        result = self._conn.execute(
            text(
                """
                UPDATE messages SET status = :s, delivery_status_at = :at
                 WHERE idempotency_key = :k AND status = ANY(:allowed)
                """
            ),
            {"s": status.value, "at": occurred_at, "k": idempotency_key, "allowed": list(allowed_from)},
        )
        return result.rowcount == 1

    def confirm_appointment(self, appointment_id: int, confirmed_at: datetime) -> bool:
        """First confirmation wins; later replays change nothing."""
        result = self._conn.execute(
            text(
                """
                UPDATE appointments SET confirmed_at = :at, status = 'CONFIRMED'
                 WHERE id = :id AND confirmed_at IS NULL AND status = 'PENDING'
                """
            ),
            {"at": confirmed_at, "id": appointment_id},
        )
        return result.rowcount == 1
