"""Declarative RabbitMQ topology (ADR-004).

::

    producers ──► clinicared.notifications (topic, durable)
                    ├─ reminder.#        ──► q.reminders.dispatch  (priority 0-10, DLX)
                    └─ waitlist.offer.#  ──► q.waitlist.offers     (priority 0-10, DLX)

    retry: worker publishes a copy to q.<name>.retry.<delay> (TTL = delay,
           dead-letters back to the work queue through the default exchange)
    DLQ:   q.<name>.dlq bound to clinicared.notifications.dlx (direct)

Fixed-TTL retry queues (one per backoff step) avoid the head-of-line blocking
that per-message TTLs cause in a single delay queue.

Declarations are idempotent: re-declaring with identical arguments is a no-op,
so every process declares the topology on start-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pika.adapters.blocking_connection import BlockingChannel

MAIN_EXCHANGE = "clinicared.notifications"
DLX_EXCHANGE = "clinicared.notifications.dlx"
MAX_PRIORITY = 10
#: Exponential backoff: 2 s, 8 s, 32 s, 128 s, 512 s (base 2 s, factor 4).
DEFAULT_RETRY_DELAYS_MS: tuple[int, ...] = (2_000, 8_000, 32_000, 128_000, 512_000)


@dataclass(frozen=True, slots=True)
class WorkQueueSpec:
    """A consumable work queue with its retry ladder and DLQ."""

    name: str
    binding_keys: tuple[str, ...]
    retry_delays_ms: tuple[int, ...] = field(default=DEFAULT_RETRY_DELAYS_MS)

    @property
    def dlq_name(self) -> str:
        """Dead-letter queue name."""
        return f"{self.name}.dlq"

    @property
    def max_attempts(self) -> int:
        """Total deliveries allowed: first attempt plus one per retry step."""
        return len(self.retry_delays_ms) + 1

    def retry_queue_name(self, delay_ms: int) -> str:
        """Name of the TTL queue for a given delay."""
        return f"{self.name}.retry.{delay_ms}"

    def retry_queue_for_attempt(self, attempt: int) -> str:
        """Retry queue to use after the ``attempt``-th failed delivery (1-based).

        Raises:
            ValueError: If no retry step exists for that attempt.
        """
        if attempt < 1 or attempt > len(self.retry_delays_ms):
            raise ValueError(f"no retry step for attempt {attempt}")
        return self.retry_queue_name(self.retry_delays_ms[attempt - 1])


REMINDERS_QUEUE = WorkQueueSpec(name="q.reminders.dispatch", binding_keys=("reminder.#",))
WAITLIST_QUEUE = WorkQueueSpec(name="q.waitlist.offers", binding_keys=("waitlist.offer.#",))
ALL_QUEUES: tuple[WorkQueueSpec, ...] = (REMINDERS_QUEUE, WAITLIST_QUEUE)


def reminder_routing_key(risk_level: str, kind: str) -> str:
    """``reminder.<risk>.<kind>`` e.g. ``reminder.high.h72``."""
    return f"reminder.{risk_level.lower()}.{kind.lower()}"


WAITLIST_ROUTING_KEY = "waitlist.offer.created"


def declare_topology(channel: BlockingChannel, queues: tuple[WorkQueueSpec, ...] = ALL_QUEUES) -> None:
    """Declare exchanges, work queues, retry queues and DLQs (idempotent).

    Args:
        channel: Open channel.
        queues: Work queues to declare.
    """
    channel.exchange_declare(exchange=MAIN_EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(exchange=DLX_EXCHANGE, exchange_type="direct", durable=True)

    for spec in queues:
        channel.queue_declare(
            queue=spec.name,
            durable=True,
            arguments={
                "x-queue-type": "classic",
                "x-max-priority": MAX_PRIORITY,
                "x-dead-letter-exchange": DLX_EXCHANGE,
                "x-dead-letter-routing-key": spec.dlq_name,
            },
        )
        for key in spec.binding_keys:
            channel.queue_bind(queue=spec.name, exchange=MAIN_EXCHANGE, routing_key=key)

        channel.queue_declare(queue=spec.dlq_name, durable=True, arguments={"x-queue-type": "classic"})
        channel.queue_bind(queue=spec.dlq_name, exchange=DLX_EXCHANGE, routing_key=spec.dlq_name)

        for delay in spec.retry_delays_ms:
            channel.queue_declare(
                queue=spec.retry_queue_name(delay),
                durable=True,
                arguments={
                    "x-queue-type": "classic",
                    "x-message-ttl": delay,
                    "x-dead-letter-exchange": "",
                    "x-dead-letter-routing-key": spec.name,
                },
            )
