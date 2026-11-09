"""Inspect, replay or discard dead-lettered messages (runbook RB-01).

Replaying is safe: the dispatcher is idempotent, so messages already sent are
acknowledged as DUPLICATE and stale ones are SKIPPED.

Usage::

    python scripts/ops/dlq_tool.py inspect --queue reminders
    python scripts/ops/dlq_tool.py replay  --queue reminders [--limit 100]
    python scripts/ops/dlq_tool.py discard --queue reminders --only-chaos
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import pika

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.messaging.connection import connection_parameters  # noqa: E402
from src.messaging.topology import REMINDERS_QUEUE, WAITLIST_QUEUE  # noqa: E402

QUEUES = {"reminders": REMINDERS_QUEUE, "waitlist": WAITLIST_QUEUE}


def main() -> None:
    """Process the DLQ one message at a time with explicit acks."""
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inspect", "replay", "discard"])
    parser.add_argument("--queue", choices=sorted(QUEUES), required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--only-chaos", action="store_true", help="discard only x-chaos-test messages")
    ns = parser.parse_args()

    spec = QUEUES[ns.queue]
    connection = pika.BlockingConnection(connection_parameters(os.environ["RABBITMQ_URL"], client_name="dlq-tool"))
    channel = connection.channel()
    channel.confirm_delivery()
    # Messages that are fetched but not acked stay "held" by this connection and
    # return to the DLQ, in order, when it closes: inspect never loses anything.
    processed = 0
    try:
        for _ in range(ns.limit):
            method, props, body = channel.basic_get(queue=spec.dlq_name, auto_ack=False)
            if method is None:
                break
            headers = dict(props.headers or {})
            deaths = headers.get("x-death", [])
            reason = deaths[0].get("reason") if deaths else "unknown"
            if ns.action == "inspect":
                print(json.dumps({"message_id": props.message_id, "reason": str(reason), "body": body.decode(errors="replace")}))
                processed += 1
                continue
            if ns.action == "replay":
                headers.pop("x-attempt", None)
                headers.pop("x-death", None)
                channel.basic_publish(
                    exchange="",
                    routing_key=spec.name,
                    body=body,
                    properties=pika.BasicProperties(
                        content_type=props.content_type, delivery_mode=2, message_id=props.message_id, priority=props.priority, headers=headers
                    ),
                    mandatory=True,
                )
                channel.basic_ack(method.delivery_tag)
            elif ns.action == "discard":
                if ns.only_chaos and not headers.get("x-chaos-test"):
                    continue  # left unacked -> returns to the DLQ on close
                channel.basic_ack(method.delivery_tag)
            processed += 1
    finally:
        connection.close()
    print(f"{ns.action}: {processed} message(s)")


if __name__ == "__main__":
    main()
