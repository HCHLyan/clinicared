"""In-memory adapters implementing the service ports (thread-safe)."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from src.domain.enums import AppointmentStatus, Channel, DispatchStatus
from src.services.ports import (
    AppointmentState,
    DispatchRecord,
    GatewayResult,
    PermanentGatewayError,
    TransientGatewayError,
)
from src.services.webhook_service import MessageRef, WebhookEvent, WebhookOutcome


class FakeClock:
    """Mutable clock."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class InMemoryDispatchRepository:
    """Mirrors the conditional UPDATE semantics of ``SqlDispatchRepository``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.messages: dict[str, DispatchRecord] = {}
        self.leases: dict[str, datetime] = {}
        self.appointments: dict[int, AppointmentState] = {}
        self.offers: dict[str, datetime] = {}
        self.skip_reasons: dict[str, str] = {}
        self.fail_mark_sent = False

    def add(self, record: DispatchRecord, appointment: AppointmentState | None = None) -> None:
        self.messages[record.idempotency_key] = record
        if appointment is not None:
            self.appointments[appointment.appointment_id] = appointment

    def _set(self, key: str, **changes: object) -> None:
        self.messages[key] = replace(self.messages[key], **changes)  # type: ignore[arg-type]

    def get(self, idempotency_key: str) -> DispatchRecord | None:
        with self._lock:
            return self.messages.get(idempotency_key)

    def appointment_state(self, appointment_id: int) -> AppointmentState | None:
        return self.appointments.get(appointment_id)

    def open_offer_expiry(self, idempotency_key: str) -> datetime | None:
        return self.offers.get(idempotency_key)

    def try_claim(self, idempotency_key: str, now: datetime, lease: timedelta) -> bool:
        with self._lock:
            rec = self.messages[idempotency_key]
            expired = rec.status is DispatchStatus.SENDING and self.leases.get(idempotency_key, now) < now
            if rec.status in (DispatchStatus.PLANNED, DispatchStatus.ENQUEUED) or expired:
                self._set(idempotency_key, status=DispatchStatus.SENDING, attempts=rec.attempts + 1)
                self.leases[idempotency_key] = now + lease
                return True
            return False

    def mark_sent(self, idempotency_key: str, result: GatewayResult, now: datetime) -> None:
        if self.fail_mark_sent:
            raise RuntimeError("simulated crash after provider accepted the message")
        with self._lock:
            if self.messages[idempotency_key].status is DispatchStatus.SENDING:
                self._set(idempotency_key, status=DispatchStatus.SENT, provider_message_id=result.provider_message_id)

    def release_for_retry(self, idempotency_key: str, error: str, now: datetime) -> None:
        with self._lock:
            if self.messages[idempotency_key].status is DispatchStatus.SENDING:
                self._set(idempotency_key, status=DispatchStatus.ENQUEUED)

    def mark_failed(self, idempotency_key: str, error: str, now: datetime) -> None:
        with self._lock:
            if self.messages[idempotency_key].status in (DispatchStatus.PLANNED, DispatchStatus.ENQUEUED, DispatchStatus.SENDING):
                self._set(idempotency_key, status=DispatchStatus.FAILED)

    def mark_skipped(self, idempotency_key: str, reason: str, now: datetime) -> None:
        with self._lock:
            if self.messages[idempotency_key].status in (DispatchStatus.PLANNED, DispatchStatus.ENQUEUED, DispatchStatus.SENDING):
                self._set(idempotency_key, status=DispatchStatus.SKIPPED)
                self.skip_reasons[idempotency_key] = reason

    def reschedule(self, idempotency_key: str, send_at: datetime, now: datetime) -> None:
        with self._lock:
            if self.messages[idempotency_key].status in (DispatchStatus.PLANNED, DispatchStatus.ENQUEUED):
                self._set(idempotency_key, status=DispatchStatus.PLANNED, send_at=send_at)


class FakeProvider:
    """Messaging provider honouring ``Idempotency-Key`` (as required by ADR-005)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.delivered_to_patient: dict[str, int] = {}
        self.transient_failures = 0
        self.permanent_failure = False

    def send(self, *, idempotency_key: str, channel: Channel, to: str, body: str) -> GatewayResult:
        with self._lock:
            self.calls += 1
            if self.permanent_failure:
                raise PermanentGatewayError("invalid number")
            if self.transient_failures > 0:
                self.transient_failures -= 1
                raise TransientGatewayError("503 from provider")
            if idempotency_key not in self.delivered_to_patient:
                self.delivered_to_patient[idempotency_key] = 1  # provider-side dedup
            return GatewayResult("prov-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:12], Decimal("45.00"))


class InMemoryWebhookRepository:
    """Webhook port fake."""

    def __init__(self) -> None:
        self.events: dict[str, WebhookOutcome | None] = {}
        self.messages: dict[str, MessageRef] = {}
        self.confirmed: dict[int, datetime] = {}
        self.accepted_offers: set[int] = set()

    def register_event(self, event: WebhookEvent) -> bool:
        if event.provider_event_id in self.events:
            return False
        self.events[event.provider_event_id] = None
        return True

    def set_event_outcome(self, provider_event_id: str, outcome: WebhookOutcome) -> None:
        self.events[provider_event_id] = outcome

    def find_message(self, provider_message_id: str) -> MessageRef | None:
        return self.messages.get(provider_message_id)

    def advance_status(self, idempotency_key: str, status: DispatchStatus, occurred_at: datetime) -> bool:
        for pid, ref in self.messages.items():
            if ref.idempotency_key == idempotency_key:
                self.messages[pid] = replace(ref, status=status)
                return True
        return False

    def confirm_appointment(self, appointment_id: int, confirmed_at: datetime) -> bool:
        if appointment_id in self.confirmed:
            return False
        self.confirmed[appointment_id] = confirmed_at
        return True

    def accept_waitlist_offer(self, offer_id: int, accepted_at: datetime) -> bool:
        if offer_id in self.accepted_offers:
            return False
        self.accepted_offers.add(offer_id)
        return True


def make_record(
    key: str = "reminder:v1:abc",
    *,
    appointment_id: int = 1,
    version: int = 1,
    send_at: datetime,
    status: DispatchStatus = DispatchStatus.ENQUEUED,
    kind: str = "H24",
) -> DispatchRecord:
    """Build a dispatch record with sensible defaults."""
    return DispatchRecord(
        idempotency_key=key,
        appointment_id=appointment_id,
        appointment_version=version,
        kind=kind,
        channel=Channel.SMS,
        recipient_address="+573001112233",
        body="ClínicaRed: recordatorio",
        send_at=send_at,
        status=status,
        attempts=0,
    )


def make_appointment(appointment_id: int = 1, *, version: int = 1, scheduled_at: datetime, status: AppointmentStatus = AppointmentStatus.PENDING) -> AppointmentState:
    """Build an appointment state."""
    return AppointmentState(appointment_id, version, status, scheduled_at)
