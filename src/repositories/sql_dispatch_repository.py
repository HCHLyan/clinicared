"""PostgreSQL implementation of :class:`~src.services.ports.DispatchRepository`.

Every state transition is a single conditional ``UPDATE ... WHERE status IN
(...)``; concurrency control therefore relies on row-level locking inside
PostgreSQL rather than on application locks, and replays are no-ops.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.domain.enums import AppointmentStatus, Channel, DispatchStatus
from src.services.ports import AppointmentState, DispatchRecord, GatewayResult

_MAX_ERROR_LENGTH = 2000


class SqlDispatchRepository:
    """Message persistence backed by the ``messages`` table.

    Args:
        engine: SQLAlchemy engine (each call runs in its own transaction).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, idempotency_key: str) -> DispatchRecord | None:
        """Return the message identified by ``idempotency_key``."""
        sql = text(
            """
            SELECT idempotency_key, appointment_id, appointment_version, message_type, channel,
                   recipient_address, COALESCE(body, '') AS body, send_at, status, attempts, provider_message_id
            FROM messages WHERE idempotency_key = :k
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"k": idempotency_key}).mappings().first()
        if row is None:
            return None
        return DispatchRecord(
            idempotency_key=row["idempotency_key"],
            appointment_id=row["appointment_id"],
            appointment_version=row["appointment_version"],
            kind=row["message_type"],
            channel=Channel(row["channel"]),
            recipient_address=row["recipient_address"],
            body=row["body"],
            send_at=row["send_at"],
            status=DispatchStatus(row["status"]),
            attempts=row["attempts"],
            provider_message_id=row["provider_message_id"],
        )

    def appointment_state(self, appointment_id: int) -> AppointmentState | None:
        """Return version/status/schedule of an appointment."""
        sql = text("SELECT id, version, status, scheduled_datetime FROM appointments WHERE id = :id")
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"id": appointment_id}).mappings().first()
        if row is None:
            return None
        return AppointmentState(row["id"], row["version"], AppointmentStatus(row["status"]), row["scheduled_datetime"])

    def open_offer_expiry(self, idempotency_key: str) -> datetime | None:
        """Return ``expires_at`` of the OPEN offer linked to the message."""
        sql = text(
            """
            SELECT o.expires_at FROM messages m
            JOIN waitlist_offers o ON o.id = m.waitlist_offer_id
            WHERE m.idempotency_key = :k AND o.status = 'OPEN'
            """
        )
        with self._engine.connect() as conn:
            return conn.execute(sql, {"k": idempotency_key}).scalar_one_or_none()

    def try_claim(self, idempotency_key: str, now: datetime, lease: timedelta) -> bool:
        """Win the right to send; only one worker can succeed at a time."""
        sql = text(
            """
            UPDATE messages
               SET status = 'SENDING', attempts = attempts + 1, lease_expires_at = :lease_until
             WHERE idempotency_key = :k
               AND (status IN ('PLANNED', 'ENQUEUED') OR (status = 'SENDING' AND lease_expires_at < :now))
            """
        )
        with self._engine.begin() as conn:
            result = conn.execute(sql, {"k": idempotency_key, "now": now, "lease_until": now + lease})
        return result.rowcount == 1

    def mark_sent(self, idempotency_key: str, result: GatewayResult, now: datetime) -> None:
        """SENDING -> SENT."""
        sql = text(
            """
            UPDATE messages
               SET status = 'SENT', sent_at = :now, provider_message_id = :pid, cost_cop = :cost,
                   lease_expires_at = NULL, last_error = NULL
             WHERE idempotency_key = :k AND status = 'SENDING'
            """
        )
        with self._engine.begin() as conn:
            conn.execute(sql, {"k": idempotency_key, "now": now, "pid": result.provider_message_id, "cost": result.cost_cop})

    def release_for_retry(self, idempotency_key: str, error: str, now: datetime) -> None:
        """SENDING -> ENQUEUED so the delayed retry can claim it again."""
        sql = text(
            """
            UPDATE messages SET status = 'ENQUEUED', lease_expires_at = NULL, last_error = :err
             WHERE idempotency_key = :k AND status = 'SENDING'
            """
        )
        with self._engine.begin() as conn:
            conn.execute(sql, {"k": idempotency_key, "err": error[:_MAX_ERROR_LENGTH]})

    def mark_failed(self, idempotency_key: str, error: str, now: datetime) -> None:
        """Any non-terminal state -> FAILED."""
        sql = text(
            """
            UPDATE messages SET status = 'FAILED', lease_expires_at = NULL, last_error = :err
             WHERE idempotency_key = :k AND status IN ('PLANNED', 'ENQUEUED', 'SENDING')
            """
        )
        with self._engine.begin() as conn:
            conn.execute(sql, {"k": idempotency_key, "err": error[:_MAX_ERROR_LENGTH]})

    def mark_skipped(self, idempotency_key: str, reason: str, now: datetime) -> None:
        """Any non-terminal state -> SKIPPED."""
        sql = text(
            """
            UPDATE messages SET status = 'SKIPPED', skip_reason = :reason, lease_expires_at = NULL
             WHERE idempotency_key = :k AND status IN ('PLANNED', 'ENQUEUED', 'SENDING')
            """
        )
        with self._engine.begin() as conn:
            conn.execute(sql, {"k": idempotency_key, "reason": reason})

    def reschedule(self, idempotency_key: str, send_at: datetime, now: datetime) -> None:
        """ENQUEUED -> PLANNED with a new ``send_at`` (picked up by the orchestrator)."""
        sql = text(
            """
            UPDATE messages SET status = 'PLANNED', send_at = :send_at, enqueued_at = NULL
             WHERE idempotency_key = :k AND status IN ('PLANNED', 'ENQUEUED')
            """
        )
        with self._engine.begin() as conn:
            conn.execute(sql, {"k": idempotency_key, "send_at": send_at})
