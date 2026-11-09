"""CA-08: idempotency and resilience - redelivery, concurrency, crashes,
out-of-order delivery and webhook retries never produce a duplicate message."""

from __future__ import annotations

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from src.domain.enums import AppointmentStatus, DispatchStatus
from src.domain.messaging_calendar import MessagingCalendar
from src.services.dispatch_service import DispatchOutcome, ReminderDispatchService
from src.services.webhook_service import (
    MessageRef,
    WebhookEvent,
    WebhookEventType,
    WebhookOutcome,
    WebhookService,
    verify_signature,
)
from tests.fakes import (
    FakeClock,
    FakeProvider,
    InMemoryDispatchRepository,
    InMemoryWebhookRepository,
    make_appointment,
    make_record,
)

NOW = datetime(2026, 11, 10, 9, 0)
APPOINTMENT_AT = datetime(2026, 11, 11, 9, 0)
KEY = "reminder:v1:abc"


@pytest.fixture()
def env() -> tuple[InMemoryDispatchRepository, FakeProvider, FakeClock, ReminderDispatchService]:
    repo = InMemoryDispatchRepository()
    repo.add(make_record(KEY, send_at=NOW), make_appointment(scheduled_at=APPOINTMENT_AT))
    provider = FakeProvider()
    clock = FakeClock(NOW)
    return repo, provider, clock, ReminderDispatchService(repo, provider, MessagingCalendar(), clock)


def test_redelivery_does_not_resend(env) -> None:
    repo, provider, _, service = env
    assert service.handle(KEY) is DispatchOutcome.SENT
    assert service.handle(KEY) is DispatchOutcome.DUPLICATE
    assert provider.calls == 1
    assert repo.messages[KEY].status is DispatchStatus.SENT


def test_concurrent_consumers_send_once(env) -> None:
    _, provider, _, service = env
    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(lambda _: service.handle(KEY), range(50)))
    assert outcomes.count(DispatchOutcome.SENT) == 1
    assert provider.calls == 1


def test_stale_version_is_skipped(env) -> None:
    repo, provider, _, service = env
    repo.appointments[1] = make_appointment(version=2, scheduled_at=APPOINTMENT_AT)
    assert service.handle(KEY) is DispatchOutcome.SKIPPED
    assert repo.skip_reasons[KEY] == "STALE_APPOINTMENT_VERSION"
    assert provider.calls == 0


def test_cancelled_appointment_is_skipped(env) -> None:
    repo, provider, _, service = env
    repo.appointments[1] = make_appointment(scheduled_at=APPOINTMENT_AT, status=AppointmentStatus.CANCELLED)
    assert service.handle(KEY) is DispatchOutcome.SKIPPED
    assert provider.calls == 0


def test_out_of_order_early_delivery_is_deferred(env) -> None:
    repo, provider, _, service = env
    repo.messages[KEY] = make_record(KEY, send_at=NOW + timedelta(hours=3))
    assert service.handle(KEY) is DispatchOutcome.DEFERRED
    assert repo.messages[KEY].status is DispatchStatus.PLANNED
    assert provider.calls == 0


def test_quiet_hours_rechecked_at_send_time(env) -> None:
    repo, provider, clock, service = env
    clock.now = datetime(2026, 11, 10, 20, 30)
    repo.messages[KEY] = make_record(KEY, send_at=datetime(2026, 11, 10, 19, 50))
    assert service.handle(KEY) is DispatchOutcome.DEFERRED
    assert repo.messages[KEY].send_at == datetime(2026, 11, 11, 7, 0)
    assert provider.calls == 0


def test_transient_error_then_success(env) -> None:
    repo, provider, _, service = env
    provider.transient_failures = 1
    assert service.handle(KEY) is DispatchOutcome.RETRY
    assert repo.messages[KEY].status is DispatchStatus.ENQUEUED
    assert service.handle(KEY) is DispatchOutcome.SENT
    assert len(provider.delivered_to_patient) == 1


def test_permanent_error_is_terminal(env) -> None:
    repo, provider, _, service = env
    provider.permanent_failure = True
    assert service.handle(KEY) is DispatchOutcome.FAILED
    assert service.handle(KEY) is DispatchOutcome.DUPLICATE
    assert provider.calls == 1


def test_crash_after_provider_accept_is_deduplicated_by_provider(env) -> None:
    repo, provider, clock, service = env
    repo.fail_mark_sent = True
    with pytest.raises(RuntimeError):
        service.handle(KEY)
    assert repo.messages[KEY].status is DispatchStatus.SENDING
    # Redelivered before the lease expires: another worker must not send.
    assert service.handle(KEY) is DispatchOutcome.DUPLICATE
    # After the lease expires the message is re-sent with the SAME key.
    repo.fail_mark_sent = False
    clock.advance(timedelta(minutes=3))
    assert service.handle(KEY) is DispatchOutcome.SENT
    assert provider.calls == 2
    assert len(provider.delivered_to_patient) == 1  # the patient got exactly one message


def test_unknown_key_goes_to_dlq(env) -> None:
    _, _, _, service = env
    assert service.handle("reminder:v1:missing") is DispatchOutcome.UNKNOWN


def test_waitlist_offer_validity_comes_from_offer(env) -> None:
    repo, provider, _, service = env
    offer_key = "waitlist:v1:xyz"
    repo.add(make_record(offer_key, appointment_id=99, send_at=NOW, kind="WAITLIST_OFFER"))
    repo.offers[offer_key] = NOW + timedelta(minutes=30)
    assert service.handle(offer_key) is DispatchOutcome.SENT
    expired_key = "waitlist:v1:old"
    repo.add(make_record(expired_key, appointment_id=99, send_at=NOW, kind="WAITLIST_OFFER"))
    assert service.handle(expired_key) is DispatchOutcome.SKIPPED
    assert provider.calls == 1


# ---------------------------------------------------------------- webhooks
def _event(event_id: str, status: DispatchStatus | None = None, reply: str | None = None) -> WebhookEvent:
    return WebhookEvent(
        provider_event_id=event_id,
        event_type=WebhookEventType.REPLY if reply is not None else WebhookEventType.STATUS,
        provider_message_id="prov-1",
        occurred_at=NOW,
        status=status,
        reply_text=reply,
    )


@pytest.fixture()
def webhooks() -> tuple[InMemoryWebhookRepository, WebhookService]:
    repo = InMemoryWebhookRepository()
    repo.messages["prov-1"] = MessageRef(KEY, 1, DispatchStatus.SENT)
    return repo, WebhookService(repo)


def test_webhook_retry_is_processed_once(webhooks) -> None:
    _, service = webhooks
    assert service.process(_event("e1", DispatchStatus.DELIVERED)) is WebhookOutcome.APPLIED
    assert service.process(_event("e1", DispatchStatus.DELIVERED)) is WebhookOutcome.DUPLICATE


def test_webhook_out_of_order_never_regresses(webhooks) -> None:
    repo, service = webhooks
    assert service.process(_event("e2", DispatchStatus.READ)) is WebhookOutcome.APPLIED
    assert service.process(_event("e3", DispatchStatus.DELIVERED)) is WebhookOutcome.OUT_OF_ORDER_IGNORED
    assert service.process(_event("e4", DispatchStatus.FAILED)) is WebhookOutcome.OUT_OF_ORDER_IGNORED
    assert repo.messages["prov-1"].status is DispatchStatus.READ


def test_confirmation_reply_is_idempotent(webhooks) -> None:
    repo, service = webhooks
    assert service.process(_event("r1", reply="SI")) is WebhookOutcome.CONFIRMED
    assert service.process(_event("r2", reply="sí")) is WebhookOutcome.ALREADY_CONFIRMED
    assert service.process(_event("r3", reply="tal vez")) is WebhookOutcome.IGNORED
    assert list(repo.confirmed) == [1]


def test_reply_to_waitlist_offer_accepts_offer_once(webhooks) -> None:
    repo, service = webhooks
    repo.messages["prov-2"] = MessageRef("waitlist:v1:z", 5, DispatchStatus.DELIVERED, kind="WAITLIST_OFFER", waitlist_offer_id=42)
    event = WebhookEvent("w1", WebhookEventType.REPLY, "prov-2", NOW, reply_text="SI")
    assert service.process(event) is WebhookOutcome.CONFIRMED
    assert service.process(WebhookEvent("w2", WebhookEventType.REPLY, "prov-2", NOW, reply_text="SI")) is WebhookOutcome.ALREADY_CONFIRMED
    assert repo.accepted_offers == {42}


def test_webhook_signature() -> None:
    body = b'{"event_id":"e1"}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature(body, good, "secret")
    assert not verify_signature(body, good, "other")
    assert not verify_signature(body, None, "secret")
    assert not verify_signature(body + b" ", good, "secret")
