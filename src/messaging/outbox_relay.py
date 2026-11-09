"""Transactional-outbox relay.

Business transactions (e.g. a cancellation that creates a waitlist offer)
insert into ``outbox_events`` atomically with their data. This relay
publishes pending events with publisher confirms and stamps
``published_at``. If it crashes between publish and stamp, the event is
published again; consumers are idempotent, so that is harmless.

Run: ``python -m src.messaging.outbox_relay``
"""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.core.config import settings
from src.core.logging_config import configure_logging
from src.core.metrics import MetricsEmitter
from src.messaging.connection import connection_parameters
from src.messaging.publisher import PublishError, ReliablePublisher

logger = logging.getLogger(__name__)

BATCH_SIZE = 200
IDLE_SLEEP_SECONDS = 2.0

_FETCH = text(
    """
    SELECT id, event_key, exchange, routing_key, priority, payload
      FROM outbox_events
     WHERE published_at IS NULL
     ORDER BY id
     LIMIT :limit
     FOR UPDATE SKIP LOCKED
    """
)
_MARK = text("UPDATE outbox_events SET published_at = CURRENT_TIMESTAMP WHERE id = :id")


def relay_once(engine: Engine, publisher: ReliablePublisher, batch_size: int = BATCH_SIZE) -> int:
    """Publish one batch of pending outbox events.

    ``SKIP LOCKED`` lets several relay replicas run without double work.

    Returns:
        Number of events published.
    """
    published = 0
    with engine.begin() as conn:
        rows = conn.execute(_FETCH, {"limit": batch_size}).mappings().all()
        for row in rows:
            publisher.publish(
                exchange=row["exchange"],
                routing_key=row["routing_key"],
                payload=row["payload"],
                message_id=row["event_key"],
                priority=row["priority"],
            )
            conn.execute(_MARK, {"id": row["id"]})
            published += 1
    return published


def main() -> None:
    """Relay loop with graceful SIGTERM handling."""
    configure_logging("outbox-relay", settings.LOG_LEVEL)
    from src.core.database import engine

    metrics = MetricsEmitter(settings.METRICS_NAMESPACE, "outbox-relay")
    publisher = ReliablePublisher(connection_parameters(settings.RABBITMQ_URL.get_secret_value(), client_name="outbox-relay"))
    stopping = False

    def _stop(signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stopping:
        try:
            count = relay_once(engine, publisher)
            if count:
                metrics.emit("OutboxEventsPublished", count)
            else:
                time.sleep(IDLE_SLEEP_SECONDS)
        except PublishError:
            logger.exception("outbox batch failed; will retry")
            metrics.emit("OutboxPublishFailures", 1)
            time.sleep(5)
    publisher.close()


if __name__ == "__main__":
    main()
