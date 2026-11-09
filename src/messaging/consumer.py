"""Resilient consumer with manual acknowledgements and exponential backoff.

Per-message decision table:

=================  ===========================================================
Handler result     Broker action
=================  ===========================================================
ACK                ``basic_ack``
RETRY / exception  copy to ``<queue>.retry.<delay>`` (confirmed) then ``ack``;
                   after ``max_attempts`` -> ``basic_nack(requeue=False)`` (DLQ)
REJECT             ``basic_nack(requeue=False)`` -> DLQ immediately
malformed JSON     ``basic_nack(requeue=False)`` -> DLQ immediately
retry publish err  ``basic_nack(requeue=True)`` (never lose the message)
=================  ===========================================================
"""

from __future__ import annotations

import json
import logging
import signal
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from types import FrameType
from typing import Any

import pika
from pika.exceptions import AMQPConnectionError, AMQPError, StreamLostError
from pika.spec import Basic, BasicProperties

from src.messaging.topology import WorkQueueSpec, declare_topology

logger = logging.getLogger(__name__)

ATTEMPT_HEADER = "x-attempt"


class HandlerResult(StrEnum):
    """What the handler wants done with a message."""

    ACK = "ACK"
    RETRY = "RETRY"
    REJECT = "REJECT"


Handler = Callable[[Mapping[str, Any]], HandlerResult]


def decide_action(result: HandlerResult, attempt: int, max_attempts: int) -> str:
    """Pure decision function (unit-tested): ``ack``, ``retry`` or ``dead_letter``.

    Args:
        result: Handler outcome.
        attempt: 1-based number of the delivery that just failed/succeeded.
        max_attempts: Total deliveries allowed.
    """
    if result is HandlerResult.ACK:
        return "ack"
    if result is HandlerResult.REJECT:
        return "dead_letter"
    return "retry" if attempt < max_attempts else "dead_letter"


class ResilientConsumer:
    """Blocking consumer with reconnection and graceful shutdown on SIGTERM.

    Args:
        parameters: Connection parameters.
        spec: Work queue to consume.
        handler: Business handler; must be idempotent.
        prefetch: QoS prefetch count (bounds in-flight unacked messages).
    """

    def __init__(self, parameters: pika.ConnectionParameters, spec: WorkQueueSpec, handler: Handler, prefetch: int = 20) -> None:
        self._parameters = parameters
        self._spec = spec
        self._handler = handler
        self._prefetch = prefetch
        self._stopping = False
        self._connection: pika.BlockingConnection | None = None
        self._consume_channel: Any = None
        self._publish_channel: Any = None

    def run(self) -> None:
        """Consume forever, reconnecting with capped backoff on connection loss."""
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        backoff = 1
        while not self._stopping:
            try:
                self._connect()
                backoff = 1
                logger.info("consumer started", extra={"queue": self._spec.name})
                self._consume_channel.start_consuming()
            except (AMQPConnectionError, StreamLostError) as exc:
                if self._stopping:
                    break
                logger.warning("connection lost, reconnecting", extra={"error": str(exc), "backoff_s": backoff})
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except AMQPError:
                logger.exception("unexpected AMQP error, reconnecting")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
        self._close()
        logger.info("consumer stopped", extra={"queue": self._spec.name})

    def _connect(self) -> None:
        self._connection = pika.BlockingConnection(self._parameters)
        self._consume_channel = self._connection.channel()
        declare_topology(self._consume_channel, (self._spec,))
        self._consume_channel.basic_qos(prefetch_count=self._prefetch)
        self._publish_channel = self._connection.channel()
        self._publish_channel.confirm_delivery()
        self._consume_channel.basic_consume(queue=self._spec.name, on_message_callback=self._on_message, auto_ack=False)

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        logger.info("shutdown signal received", extra={"signal": signum})
        self._stopping = True
        if self._connection is not None and self._connection.is_open:
            self._connection.add_callback_threadsafe(self._stop_consuming)

    def _stop_consuming(self) -> None:
        if self._consume_channel is not None and self._consume_channel.is_open:
            self._consume_channel.stop_consuming()

    def _close(self) -> None:
        if self._connection is not None and self._connection.is_open:
            self._connection.close()

    def _on_message(self, channel: Any, method: Basic.Deliver, properties: BasicProperties, body: bytes) -> None:
        headers = dict(properties.headers or {})
        attempt = int(headers.get(ATTEMPT_HEADER, 0)) + 1
        log_ctx = {"queue": self._spec.name, "message_id": properties.message_id, "attempt": attempt}

        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
        except ValueError:
            logger.error("malformed message sent to DLQ", extra=log_ctx)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        try:
            result = self._handler(payload)
        except Exception:  # noqa: BLE001 - any handler crash becomes a retry
            logger.exception("handler raised; scheduling retry", extra=log_ctx)
            result = HandlerResult.RETRY

        action = decide_action(result, attempt, self._spec.max_attempts)
        if action == "ack":
            channel.basic_ack(delivery_tag=method.delivery_tag)
        elif action == "dead_letter":
            logger.error("message dead-lettered", extra={**log_ctx, "result": result.value})
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        else:
            self._schedule_retry(channel, method, properties, body, headers, attempt, log_ctx)

    def _schedule_retry(
        self,
        channel: Any,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
        headers: dict[str, Any],
        attempt: int,
        log_ctx: dict[str, Any],
    ) -> None:
        retry_queue = self._spec.retry_queue_for_attempt(attempt)
        retry_properties = pika.BasicProperties(
            content_type=properties.content_type,
            delivery_mode=pika.DeliveryMode.Persistent,
            message_id=properties.message_id,
            priority=properties.priority,
            timestamp=properties.timestamp,
            headers={**headers, ATTEMPT_HEADER: attempt},
        )
        try:
            self._publish_channel.basic_publish(
                exchange="", routing_key=retry_queue, body=body, properties=retry_properties, mandatory=True
            )
        except AMQPError:
            logger.exception("retry publish failed; requeueing original", extra=log_ctx)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
        channel.basic_ack(delivery_tag=method.delivery_tag)
        logger.warning("message scheduled for retry", extra={**log_ctx, "retry_queue": retry_queue})
