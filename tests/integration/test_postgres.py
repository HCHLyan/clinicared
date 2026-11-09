"""Integration tests against a real PostgreSQL (CI service container).

Run with ``pytest -m integration``; requires ``INTEGRATION_DATABASE_URL``
pointing to a disposable database (the schema is created from
``docs/database/*.sql``).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("INTEGRATION_DATABASE_URL not set", allow_module_level=True)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402

from src.domain.messaging_calendar import MessagingCalendar  # noqa: E402
from src.repositories.sql_dispatch_repository import SqlDispatchRepository  # noqa: E402
from src.services.appointment_service import cancel_appointment  # noqa: E402
from src.services.ports import GatewayResult  # noqa: E402

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "database"
NOW = datetime(2026, 11, 10, 9, 0)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DATABASE_URL, pool_size=20)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        # Raw DBAPI cursor without parameters: the scripts contain literal '%'.
        cursor = conn.connection.cursor()
        for script in sorted(SCHEMA_DIR.glob("0*.sql")):
            cursor.execute(script.read_text(encoding="utf-8"))
        cursor.close()
        conn.execute(
            text(
                """
                INSERT INTO locations (name, address, city) VALUES ('Sede Norte', 'Cra 51B', 'Barranquilla');
                INSERT INTO specialties (name) VALUES ('Medicina General');
                INSERT INTO professionals (first_name, last_name, email, medical_license) VALUES ('Ana', 'Ruiz', 'a@x.co', 'RM-1');
                INSERT INTO patients (national_id, first_name, last_name, email, phone, date_of_birth)
                     VALUES ('100', 'Luis', 'Paz', 'l@x.co', '+573000000001', '1990-01-01'),
                            ('200', 'Eva', 'Gil', 'e@x.co', '+573000000002', '1985-01-01');
                """
            )
        )
    yield eng
    eng.dispose()


def _appointment(engine, scheduled: datetime) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO appointments (patient_id, professional_id, location_id, specialty_id, scheduled_datetime, created_at)
                VALUES (1, 1, 1, 1, :at, :now) RETURNING id
                """
            ),
            {"at": scheduled, "now": NOW - timedelta(days=3)},
        ).scalar_one()


def _message(engine, appointment_id: int, key: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO messages (appointment_id, patient_id, message_type, status, idempotency_key, channel,
                                      recipient_address, body, send_at, priority)
                VALUES (:a, 1, 'H24', 'ENQUEUED', :k, 'SMS', '+573000000001', 'x', :at, 5)
                ON CONFLICT (idempotency_key) DO NOTHING
                """
            ),
            {"a": appointment_id, "k": key, "at": NOW},
        )


def test_duplicate_planning_is_a_noop(engine) -> None:
    appointment_id = _appointment(engine, NOW + timedelta(days=1))
    for _ in range(3):
        _message(engine, appointment_id, "it:dup")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM messages WHERE idempotency_key = 'it:dup'")).scalar_one() == 1


def test_only_one_concurrent_claim_wins(engine) -> None:
    appointment_id = _appointment(engine, NOW + timedelta(days=1, hours=1))
    _message(engine, appointment_id, "it:claim")
    repo = SqlDispatchRepository(engine)
    with ThreadPoolExecutor(max_workers=10) as pool:
        wins = list(pool.map(lambda _: repo.try_claim("it:claim", NOW, timedelta(minutes=2)), range(20)))
    assert wins.count(True) == 1
    repo.mark_sent("it:claim", GatewayResult("prov-it-1", Decimal("45")), NOW)
    assert repo.try_claim("it:claim", NOW + timedelta(hours=1), timedelta(minutes=2)) is False


def test_audit_log_is_immutable_and_chained(engine) -> None:
    with engine.begin() as conn:
        for i in range(3):
            conn.execute(
                text(
                    "INSERT INTO audit_log (actor, action, resource_type, resource_id, request_id, http_path, status_code) "
                    "VALUES ('t', 'READ', 'patients', :i, 'r', '/patients', 200)"
                ),
                {"i": str(i)},
            )
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE audit_log SET actor = 'evil'"))
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM audit_log"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM audit_log_verify_chain()")).scalar_one() == 0


def test_cancel_releases_slot_and_is_idempotent(engine) -> None:
    appointment_id = _appointment(engine, NOW + timedelta(days=2))
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO waitlist (patient_id, specialty_id, requested_at) VALUES (2, 1, :t)"), {"t": NOW - timedelta(days=1)})
    with engine.begin() as conn:
        first = cancel_appointment(conn, appointment_id, NOW, MessagingCalendar())
    with engine.begin() as conn:
        second = cancel_appointment(conn, appointment_id, NOW, MessagingCalendar())
    assert first.offer is not None and first.offer.offered
    assert first.offer.release_latency_seconds is not None and first.offer.release_latency_seconds <= 600
    assert second.already_cancelled
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM waitlist_offers WHERE released_appointment_id = :a"), {"a": appointment_id}).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM outbox_events")).scalar_one() == 1
