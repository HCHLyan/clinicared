"""RabbitMQ consumer: ack/nack decisions and exponential-backoff ladder
(exercised against fake channels, no broker required)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.messaging.consumer import ATTEMPT_HEADER, HandlerResult, ResilientConsumer, decide_action
from src.messaging.topology import REMINDERS_QUEUE, reminder_routing_key


class FakeChannel:
    def __init__(self, fail_publish: bool = False) -> None:
        self.acks: list[int] = []
        self.nacks: list[tuple[int, bool]] = []
        self.published: list[dict[str, Any]] = []
        self.fail_publish = fail_publish

    def basic_ack(self, delivery_tag: int) -> None:
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag: int, requeue: bool) -> None:
        self.nacks.append((delivery_tag, requeue))

    def basic_publish(self, **kwargs: Any) -> None:
        if self.fail_publish:
            from pika.exceptions import AMQPError

            raise AMQPError("broker down")
        self.published.append(kwargs)


def _consumer(result: HandlerResult, publish_channel: FakeChannel) -> ResilientConsumer:
    consumer = ResilientConsumer(parameters=None, spec=REMINDERS_QUEUE, handler=lambda _p: result)  # type: ignore[arg-type]
    consumer._publish_channel = publish_channel
    return consumer


def _deliver(consumer: ResilientConsumer, channel: FakeChannel, body: bytes, attempt: int = 0) -> None:
    method = SimpleNamespace(delivery_tag=7)
    props = SimpleNamespace(headers={ATTEMPT_HEADER: attempt} if attempt else None, message_id="k", content_type="application/json", priority=8, timestamp=1)
    consumer._on_message(channel, method, props, body)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("result", "attempt", "expected"),
    [
        (HandlerResult.ACK, 1, "ack"),
        (HandlerResult.REJECT, 1, "dead_letter"),
        (HandlerResult.RETRY, 1, "retry"),
        (HandlerResult.RETRY, 5, "retry"),
        (HandlerResult.RETRY, 6, "dead_letter"),
    ],
)
def test_decide_action(result: HandlerResult, attempt: int, expected: str) -> None:
    assert decide_action(result, attempt, REMINDERS_QUEUE.max_attempts) == expected


def test_backoff_is_exponential() -> None:
    delays = REMINDERS_QUEUE.retry_delays_ms
    assert all(later == earlier * 4 for earlier, later in zip(delays, delays[1:]))
    assert REMINDERS_QUEUE.retry_queue_for_attempt(1) == "q.reminders.dispatch.retry.2000"
    with pytest.raises(ValueError):
        REMINDERS_QUEUE.retry_queue_for_attempt(6)


def test_retry_publishes_to_delay_queue_then_acks() -> None:
    channel, publish = FakeChannel(), FakeChannel()
    _deliver(_consumer(HandlerResult.RETRY, publish), channel, json.dumps({"idempotency_key": "k"}).encode(), attempt=1)
    assert publish.published[0]["routing_key"] == "q.reminders.dispatch.retry.8000"
    assert publish.published[0]["properties"].headers[ATTEMPT_HEADER] == 2
    assert channel.acks == [7]


def test_exhausted_retries_go_to_dlq() -> None:
    channel = FakeChannel()
    _deliver(_consumer(HandlerResult.RETRY, FakeChannel()), channel, b'{"idempotency_key": "k"}', attempt=5)
    assert channel.nacks == [(7, False)]


def test_malformed_message_goes_to_dlq() -> None:
    channel = FakeChannel()
    _deliver(_consumer(HandlerResult.ACK, FakeChannel()), channel, b"not-json")
    assert channel.nacks == [(7, False)]


def test_handler_crash_is_retried() -> None:
    def boom(_payload: Any) -> HandlerResult:
        raise RuntimeError("db down")

    consumer = ResilientConsumer(parameters=None, spec=REMINDERS_QUEUE, handler=boom)  # type: ignore[arg-type]
    publish, channel = FakeChannel(), FakeChannel()
    consumer._publish_channel = publish
    _deliver(consumer, channel, b'{"idempotency_key": "k"}')
    assert len(publish.published) == 1 and channel.acks == [7]


def test_retry_publish_failure_requeues_original() -> None:
    channel = FakeChannel()
    _deliver(_consumer(HandlerResult.RETRY, FakeChannel(fail_publish=True)), channel, b'{"idempotency_key": "k"}')
    assert channel.nacks == [(7, True)] and channel.acks == []


def test_routing_key() -> None:
    assert reminder_routing_key("HIGH", "H72") == "reminder.high.h72"
