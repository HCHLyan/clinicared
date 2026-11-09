"""Publisher with publisher confirms, persistence and mandatory routing."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from typing import Any

import pika
from pika.exceptions import AMQPConnectionError, AMQPError, NackError, StreamLostError, UnroutableError

from src.messaging.topology import declare_topology

logger = logging.getLogger(__name__)


class PublishError(Exception):
    """The broker did not confirm the message."""


class ReliablePublisher:
    """Blocking publisher that only returns once the broker confirmed the message.

    * ``delivery_mode=2`` (persistent) + durable queues survive broker restarts.
    * ``confirm_delivery`` makes ``basic_publish`` raise on broker NACK.
    * ``mandatory=True`` makes unroutable messages raise instead of vanishing.
    * ``message_id`` carries the idempotency key for consumer deduplication.

    Args:
        parameters: Connection parameters.
        max_reconnects: Reconnection attempts per publish on connection loss.
    """

    def __init__(self, parameters: pika.ConnectionParameters, max_reconnects: int = 3) -> None:
        self._parameters = parameters
        self._max_reconnects = max_reconnects
        self._connection: pika.BlockingConnection | None = None
        self._channel: Any = None

    def _ensure_channel(self) -> Any:
        if self._connection is None or self._connection.is_closed or self._channel is None or self._channel.is_closed:
            self._connection = pika.BlockingConnection(self._parameters)
            self._channel = self._connection.channel()
            self._channel.confirm_delivery()
            declare_topology(self._channel)
        return self._channel

    def publish(
        self,
        *,
        exchange: str,
        routing_key: str,
        payload: Mapping[str, Any],
        message_id: str,
        priority: int = 0,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        """Publish one JSON message and wait for the broker confirm.

        Raises:
            PublishError: The message was nacked/unroutable or the broker is
                unreachable after the configured reconnection attempts.
        """
        body = json.dumps(payload, default=str).encode()
        properties = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.DeliveryMode.Persistent,
            message_id=message_id,
            priority=max(0, min(priority, 10)),
            timestamp=int(time.time()),
            headers=dict(headers or {}),
        )
        for attempt in range(1, self._max_reconnects + 1):
            try:
                channel = self._ensure_channel()
                channel.basic_publish(
                    exchange=exchange, routing_key=routing_key, body=body, properties=properties, mandatory=True
                )
                return
            except (UnroutableError, NackError) as exc:
                raise PublishError(f"broker rejected message {message_id}: {exc}") from exc
            except (AMQPConnectionError, StreamLostError, AMQPError) as exc:
                logger.warning("publish failed, reconnecting", extra={"attempt": attempt, "error": str(exc)})
                self._connection = None
                time.sleep(min(2**attempt, 10))
        raise PublishError(f"could not publish {message_id} after {self._max_reconnects} attempts")

    def close(self) -> None:
        """Close the connection if open."""
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
