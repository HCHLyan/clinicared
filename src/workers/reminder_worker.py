"""Reminder / waitlist-offer dispatch worker.

Run: ``python -m src.workers.reminder_worker --queue reminders`` (or ``waitlist``).

Each ECS task runs one consumer. Horizontal scaling is safe because the
dispatch service is idempotent and claims are atomic in PostgreSQL.
"""

from __future__ import annotations

import argparse
import logging
import threading
from collections.abc import Mapping
from typing import Any

import pika

from src.core.clock import local_now
from src.core.config import settings
from src.core.logging_config import configure_logging
from src.core.metrics import MetricsEmitter
from src.messaging.connection import connection_parameters
from src.messaging.consumer import HandlerResult, ResilientConsumer
from src.messaging.topology import REMINDERS_QUEUE, WAITLIST_QUEUE, WorkQueueSpec
from src.services.dispatch_service import DispatchOutcome, ReminderDispatchService

logger = logging.getLogger(__name__)

QUEUES: dict[str, WorkQueueSpec] = {"reminders": REMINDERS_QUEUE, "waitlist": WAITLIST_QUEUE}
_OUTCOME_TO_RESULT: dict[DispatchOutcome, HandlerResult] = {
    DispatchOutcome.RETRY: HandlerResult.RETRY,
    DispatchOutcome.UNKNOWN: HandlerResult.REJECT,
}


def build_handler(service_factory: Any, metrics: MetricsEmitter, queue: str) -> Any:
    """Adapt the dispatch service to the consumer handler signature."""

    def handle(payload: Mapping[str, Any]) -> HandlerResult:
        key = payload.get("idempotency_key")
        if not isinstance(key, str) or not key:
            return HandlerResult.REJECT
        outcome = service_factory().handle(key)
        metrics.emit("DispatchOutcome", 1, dimensions={"Queue": queue, "Outcome": outcome.value})
        return _OUTCOME_TO_RESULT.get(outcome, HandlerResult.ACK)

    return handle


def start_queue_depth_monitor(params: pika.ConnectionParameters, spec: WorkQueueSpec, metrics: MetricsEmitter, interval_s: int = 60) -> None:
    """Emit ``QueueDepth`` / ``DlqDepth`` metrics from a daemon thread.

    Uses its own connection (pika connections are not thread-safe). The
    ``DlqDepth`` metric backs the CloudWatch alarm described in the runbook.
    """

    def loop() -> None:
        stop = threading.Event()
        while not stop.wait(interval_s):
            try:
                connection = pika.BlockingConnection(params)
                try:
                    channel = connection.channel()
                    work = channel.queue_declare(queue=spec.name, passive=True)
                    dlq = channel.queue_declare(queue=spec.dlq_name, passive=True)
                    metrics.emit("QueueDepth", work.method.message_count, dimensions={"Queue": spec.name})
                    metrics.emit("DlqDepth", dlq.method.message_count, dimensions={"Queue": spec.dlq_name})
                finally:
                    connection.close()
            except Exception:  # noqa: BLE001 - monitoring must never kill the worker
                logger.exception("queue depth probe failed")

    threading.Thread(target=loop, name="queue-depth-monitor", daemon=True).start()


def main() -> None:
    """Wire adapters and start consuming."""
    parser = argparse.ArgumentParser(description="ClínicaRed dispatch worker")
    parser.add_argument("--queue", choices=sorted(QUEUES), default="reminders")
    args = parser.parse_args()

    configure_logging(f"{args.queue}-worker", settings.LOG_LEVEL)
    from src.core.database import engine
    from src.gateways.http_messaging_gateway import HttpMessagingGateway
    from src.repositories.sql_dispatch_repository import SqlDispatchRepository
    from src.services.calendar_provider import CachedCalendarProvider

    spec = QUEUES[args.queue]
    metrics = MetricsEmitter(settings.METRICS_NAMESPACE, f"{args.queue}-worker")
    repository = SqlDispatchRepository(engine)
    gateway = HttpMessagingGateway(
        settings.MESSAGING_GATEWAY_URL,
        settings.MESSAGING_GATEWAY_TOKEN.get_secret_value(),
        settings.MESSAGING_TIMEOUT_SECONDS,
    )
    calendars = CachedCalendarProvider(engine)

    def service_factory() -> ReminderDispatchService:
        return ReminderDispatchService(repository, gateway, calendars.get(), local_now)

    params = connection_parameters(settings.RABBITMQ_URL.get_secret_value(), client_name=f"{args.queue}-worker")
    start_queue_depth_monitor(params, spec, metrics)
    ResilientConsumer(params, spec, build_handler(service_factory, metrics, args.queue), settings.RABBITMQ_PREFETCH).run()
    gateway.close()


if __name__ == "__main__":
    main()
