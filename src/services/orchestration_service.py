"""Enqueue due messages to RabbitMQ and recover stuck ones (runs every 10 min)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.messaging.publisher import ReliablePublisher
from src.messaging.topology import MAIN_EXCHANGE, WAITLIST_ROUTING_KEY, reminder_routing_key

logger = logging.getLogger(__name__)

ENQUEUE_BATCH = 2000
STUCK_ENQUEUED_AFTER = timedelta(hours=1)
STUCK_SENDING_AFTER = timedelta(minutes=15)

_DUE_SQL = text(
    """
    SELECT id, idempotency_key, message_type, COALESCE(risk_level, 'LOW') AS risk_level, priority
      FROM messages
     WHERE status = 'PLANNED' AND send_at <= :now
     ORDER BY priority DESC, send_at
     LIMIT :limit
     FOR UPDATE SKIP LOCKED
    """
)
_MARK_ENQUEUED_SQL = text("UPDATE messages SET status = 'ENQUEUED', enqueued_at = :now WHERE id = :id AND status = 'PLANNED'")


def enqueue_due_messages(engine: Engine, publisher: ReliablePublisher, now: datetime, batch_size: int = ENQUEUE_BATCH) -> int:
    """Publish PLANNED messages whose ``send_at`` has arrived.

    Publish happens before commit: if the commit fails, the rows remain
    PLANNED and are published again next run; the consumer deduplicates by
    idempotency key, so no patient receives two messages.

    Returns:
        Number of messages enqueued.
    """
    with engine.begin() as conn:
        rows = conn.execute(_DUE_SQL, {"now": now, "limit": batch_size}).mappings().all()
        for row in rows:
            kind = str(row["message_type"])
            routing_key = WAITLIST_ROUTING_KEY if kind == "WAITLIST_OFFER" else reminder_routing_key(row["risk_level"], kind)
            publisher.publish(
                exchange=MAIN_EXCHANGE,
                routing_key=routing_key,
                payload={"idempotency_key": row["idempotency_key"]},
                message_id=row["idempotency_key"],
                priority=int(row["priority"]),
            )
            conn.execute(_MARK_ENQUEUED_SQL, {"id": row["id"], "now": now})
    logger.info("due messages enqueued", extra={"count": len(rows)})
    return len(rows)


def recover_stuck_messages(engine: Engine, now: datetime) -> dict[str, int]:
    """Return lost messages to PLANNED so they are enqueued again.

    * ENQUEUED for over an hour: the broker message was lost (e.g. DLQ purge).
    * SENDING with a lease expired for over 15 min: the worker died mid-send;
      re-sending reuses the same idempotency key (provider dedup).
    """
    with engine.begin() as conn:
        enqueued = conn.execute(
            text(
                """
                UPDATE messages SET status = 'PLANNED', enqueued_at = NULL
                 WHERE status = 'ENQUEUED' AND enqueued_at < :limit
                """
            ),
            {"limit": now - STUCK_ENQUEUED_AFTER},
        ).rowcount
        sending = conn.execute(
            text(
                """
                UPDATE messages SET status = 'PLANNED', lease_expires_at = NULL, enqueued_at = NULL
                 WHERE status = 'SENDING' AND lease_expires_at < :limit
                """
            ),
            {"limit": now - STUCK_SENDING_AFTER},
        ).rowcount
    result = {"enqueued_recovered": enqueued, "sending_recovered": sending}
    logger.info("stuck message recovery", extra=result)
    return result
