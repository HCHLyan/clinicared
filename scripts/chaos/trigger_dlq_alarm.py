"""Controlled failure injection for the DLQ alarm demo (runbook RB-01).

Publishes ``--count`` well-formed messages whose idempotency keys do not exist
in ``messages``. The worker classifies them as UNKNOWN -> REJECT -> DLQ, the
``DlqDepth`` metric becomes > 0 and ``clinicared-reminders-dlq-not-empty``
fires within ~3 minutes. No patient is contacted (the keys match no row).

Usage (staging only)::

    RABBITMQ_URL=amqps://... python scripts/chaos/trigger_dlq_alarm.py --count 3
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.messaging.connection import connection_parameters  # noqa: E402
from src.messaging.publisher import ReliablePublisher  # noqa: E402
from src.messaging.topology import MAIN_EXCHANGE, reminder_routing_key  # noqa: E402


def main() -> None:
    """Publish poison messages tagged as chaos tests."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--i-understand-this-is-not-production", action="store_true", required=True)
    ns = parser.parse_args()
    publisher = ReliablePublisher(connection_parameters(os.environ["RABBITMQ_URL"], client_name="chaos"))
    try:
        for _ in range(ns.count):
            key = f"chaos:{uuid.uuid4().hex}"
            publisher.publish(
                exchange=MAIN_EXCHANGE,
                routing_key=reminder_routing_key("LOW", "H24"),
                payload={"idempotency_key": key},
                message_id=key,
                headers={"x-chaos-test": True},
            )
            print(f"published {key}")
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
