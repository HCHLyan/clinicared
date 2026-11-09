"""Daily risk calculation (RN-02), reminder planning (RN-03/04/11) and
overbooking capacity (RN-05) for the next N days.

All writes are upserts keyed by business identity, so reruns of the same
DAG run (or backfills) converge to the same state.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from src.domain.enums import Channel, RecipientType, RiskLevel
from src.domain.messaging_calendar import MessagingCalendar
from src.domain.rules.no_show import count_no_shows_in_window
from src.domain.rules.overbooking import allowed_extra_slots, block_start
from src.domain.rules.reminders import ReminderPlanInput, plan_reminders
from src.domain.rules.risk import RiskInput, classify_risk
from src.services.templates import render_message

logger = logging.getLogger(__name__)

HORIZON_DAYS = 7
HISTORY_LOOKBACK = timedelta(days=200)  # superset of 6 calendar months


@dataclass(frozen=True, slots=True)
class RiskRunReport:
    """Summary of a risk + planning run."""

    appointments: int
    by_level: dict[str, int]
    reminders_planned: int
    reminders_skipped: dict[str, int]
    overbook_blocks: int


_APPOINTMENTS_SQL = text(
    """
    SELECT ap.id, ap.patient_id, ap.professional_id, ap.location_id, ap.specialty_id,
           ap.scheduled_datetime, ap.created_at, ap.confirmed_at, ap.version, ap.is_overbook,
           p.date_of_birth, p.phone, p.email, p.preferred_channel,
           g.phone AS guardian_phone, g.email AS guardian_email,
           l.name AS branch_name, l.city,
           s.is_overbookable,
           COALESCE(w.is_severe, FALSE) AS severe_weather,
           NOT EXISTS (
               SELECT 1 FROM appointments prev
                WHERE prev.patient_id = ap.patient_id AND prev.id <> ap.id
                  AND prev.scheduled_datetime < ap.scheduled_datetime AND prev.status <> 'CANCELLED'
           ) AS is_first_appointment
      FROM appointments ap
      JOIN patients p     ON p.id = ap.patient_id
      JOIN locations l    ON l.id = ap.location_id
      JOIN specialties s  ON s.id = ap.specialty_id
      LEFT JOIN guardians g ON g.patient_id = p.id AND g.is_primary
      LEFT JOIN weather_daily w ON w.city = l.city AND w.weather_date = ap.scheduled_datetime::date
     WHERE ap.scheduled_datetime >= :start AND ap.scheduled_datetime < :end
       AND ap.status IN ('PENDING', 'CONFIRMED')
    """
)

_NO_SHOW_HISTORY_SQL = text(
    """
    SELECT ap.patient_id, ap.scheduled_datetime
      FROM attendances at
      JOIN appointments ap ON ap.id = at.appointment_id
     WHERE at.status = 'NO_SHOW'
       AND ap.patient_id = ANY(:patient_ids)
       AND ap.scheduled_datetime >= :since AND ap.scheduled_datetime < :as_of
    """
)

_UPSERT_RISK_SQL = text(
    """
    INSERT INTO risk_scores (appointment_id, risk_level, reasons, no_shows_6m, lead_time_days, calculated_at, calc_run_id)
    VALUES (:appointment_id, :risk_level, :reasons, :no_shows_6m, :lead_time_days, :calculated_at, :run_id)
    ON CONFLICT (appointment_id) DO UPDATE SET
        risk_level = EXCLUDED.risk_level, reasons = EXCLUDED.reasons, no_shows_6m = EXCLUDED.no_shows_6m,
        lead_time_days = EXCLUDED.lead_time_days, calculated_at = EXCLUDED.calculated_at, calc_run_id = EXCLUDED.calc_run_id
    """
)

_INSERT_MESSAGE_SQL = text(
    """
    INSERT INTO messages (appointment_id, patient_id, message_type, status, idempotency_key, appointment_version,
                          recipient_type, channel, recipient_address, body, risk_level, send_at, priority)
    VALUES (:appointment_id, :patient_id, :kind, 'PLANNED', :key, :version,
            :recipient_type, :channel, :address, :body, :risk_level, :send_at, :priority)
    ON CONFLICT (idempotency_key) DO UPDATE SET
        send_at = EXCLUDED.send_at, body = EXCLUDED.body, priority = EXCLUDED.priority,
        risk_level = EXCLUDED.risk_level, recipient_address = EXCLUDED.recipient_address, channel = EXCLUDED.channel
     WHERE messages.status = 'PLANNED'
    """
)

_CANCEL_OBSOLETE_SQL = text(
    """
    UPDATE messages SET status = 'CANCELLED', skip_reason = 'NO_LONGER_REQUIRED'
     WHERE appointment_id = :appointment_id AND message_type IN ('H72', 'H24')
       AND status IN ('PLANNED', 'ENQUEUED') AND NOT (idempotency_key = ANY(:keep))
    """
)

_UPSERT_OVERBOOK_SQL = text(
    """
    INSERT INTO overbook_capacity (professional_id, location_id, block_start, specialty_id, high_risk_count, allowed_extra, calculated_at)
    VALUES (:professional_id, :location_id, :block_start, :specialty_id, :high_risk_count, :allowed_extra, :calculated_at)
    ON CONFLICT (professional_id, location_id, block_start) DO UPDATE SET
        specialty_id = EXCLUDED.specialty_id, high_risk_count = EXCLUDED.high_risk_count,
        allowed_extra = EXCLUDED.allowed_extra, calculated_at = EXCLUDED.calculated_at
    """
)


def _contact(row: dict[str, object], recipient: RecipientType) -> tuple[Channel, str | None]:
    """Choose channel and address for the recipient (preferred channel first)."""
    phone = row["guardian_phone"] if recipient is RecipientType.GUARDIAN else row["phone"]
    email = row["guardian_email"] if recipient is RecipientType.GUARDIAN else row["email"]
    preferred = Channel(str(row["preferred_channel"] or "SMS"))
    if preferred is Channel.EMAIL and email:
        return Channel.EMAIL, str(email)
    if phone:
        return (preferred if preferred is not Channel.EMAIL else Channel.SMS), str(phone)
    if email:
        return Channel.EMAIL, str(email)
    return preferred, None


def run_risk_and_planning(
    engine: Engine,
    *,
    as_of: datetime,
    run_id: str,
    calendar: MessagingCalendar,
    horizon_days: int = HORIZON_DAYS,
    block_minutes: int = 60,
) -> RiskRunReport:
    """Classify, plan reminders and compute overbooking for the horizon.

    Args:
        engine: SQLAlchemy engine.
        as_of: Point-in-time reference (DAG ``data_interval_end`` in local time).
        run_id: Airflow run id (lineage).
        calendar: RN-04 calendar.
        horizon_days: Days ahead to process (RN: 7).
        block_minutes: RN-05 agenda block size.

    Returns:
        Run report.
    """
    start = as_of
    end = as_of + timedelta(days=horizon_days)
    with engine.begin() as conn:
        rows = [dict(r) for r in conn.execute(_APPOINTMENTS_SQL, {"start": start, "end": end}).mappings()]
        history = _load_no_show_history(conn, {int(r["patient_id"]) for r in rows}, as_of)

        by_level: dict[str, int] = defaultdict(int)
        skipped: dict[str, int] = defaultdict(int)
        risk_rows: list[dict[str, object]] = []
        message_rows: list[dict[str, object]] = []
        cancel_rows: list[dict[str, object]] = []
        high_by_block: dict[tuple[int, int, datetime], list[int]] = defaultdict(list)
        block_meta: dict[tuple[int, int, datetime], tuple[int, bool]] = {}

        for row in rows:
            no_shows = count_no_shows_in_window(history.get(int(row["patient_id"]), ()), as_of)
            assessment = classify_risk(
                RiskInput(
                    scheduled_at=row["scheduled_datetime"],
                    booked_at=row["created_at"] or as_of,
                    is_confirmed=row["confirmed_at"] is not None,
                    no_shows_last_6m=no_shows,
                )
            )
            by_level[assessment.level.value] += 1
            risk_rows.append(
                {
                    "appointment_id": row["id"],
                    "risk_level": assessment.level.value,
                    "reasons": list(assessment.reasons),
                    "no_shows_6m": no_shows,
                    "lead_time_days": assessment.lead_time_days,
                    "calculated_at": as_of,
                    "run_id": run_id,
                }
            )

            block_key = (int(row["professional_id"]), int(row["location_id"]), block_start(row["scheduled_datetime"], block_minutes))
            block_meta[block_key] = (int(row["specialty_id"]), bool(row["is_overbookable"]))
            if assessment.level is RiskLevel.HIGH and not row["is_overbook"]:
                high_by_block[block_key].append(int(row["id"]))
            else:
                high_by_block.setdefault(block_key, [])

            plan = plan_reminders(
                ReminderPlanInput(
                    appointment_id=int(row["id"]),
                    appointment_version=int(row["version"]),
                    scheduled_at=row["scheduled_datetime"],
                    risk_level=assessment.level,
                    is_first_appointment=bool(row["is_first_appointment"]),
                    patient_birth_date=row["date_of_birth"],
                    has_patient_contact=bool(row["phone"] or row["email"]),
                    has_guardian_contact=bool(row["guardian_phone"] or row["guardian_email"]),
                    now=as_of,
                ),
                calendar,
            )
            for item in plan.skipped:
                skipped[item.reason] += 1
            for reminder in plan.reminders:
                channel, address = _contact(row, reminder.recipient_type)
                message_rows.append(
                    {
                        "appointment_id": row["id"],
                        "patient_id": row["patient_id"],
                        "kind": reminder.kind.value,
                        "key": reminder.idempotency_key,
                        "version": reminder.appointment_version,
                        "recipient_type": reminder.recipient_type.value,
                        "channel": channel.value,
                        "address": address,
                        "body": render_message(
                            kind=reminder.kind,
                            recipient_type=reminder.recipient_type,
                            scheduled_at=row["scheduled_datetime"],
                            branch_name=str(row["branch_name"]),
                            severe_weather=bool(row["severe_weather"]),
                        ),
                        "risk_level": assessment.level.value,
                        "send_at": reminder.send_at,
                        "priority": reminder.priority,
                    }
                )
            cancel_rows.append({"appointment_id": row["id"], "keep": [r.idempotency_key for r in plan.reminders]})

        overbook_rows = [
            {
                "professional_id": key[0],
                "location_id": key[1],
                "block_start": key[2],
                "specialty_id": block_meta[key][0],
                "high_risk_count": len(ids),
                "allowed_extra": allowed_extra_slots(len(ids), block_meta[key][1]),
                "calculated_at": as_of,
            }
            for key, ids in high_by_block.items()
        ]

        if risk_rows:
            conn.execute(_UPSERT_RISK_SQL, risk_rows)
        if cancel_rows:
            conn.execute(_CANCEL_OBSOLETE_SQL, cancel_rows)
        if message_rows:
            conn.execute(_INSERT_MESSAGE_SQL, message_rows)
        if overbook_rows:
            conn.execute(_UPSERT_OVERBOOK_SQL, overbook_rows)

    report = RiskRunReport(len(rows), dict(by_level), len(message_rows), dict(skipped), len(overbook_rows))
    logger.info("risk and planning run finished", extra={"report": report, "run_id": run_id})
    return report


def _load_no_show_history(conn: Connection, patient_ids: set[int], as_of: datetime) -> dict[int, list[datetime]]:
    """Return no-show datetimes per patient within the lookback window."""
    if not patient_ids:
        return {}
    history: dict[int, list[datetime]] = defaultdict(list)
    result = conn.execute(
        _NO_SHOW_HISTORY_SQL, {"patient_ids": list(patient_ids), "since": as_of - HISTORY_LOOKBACK, "as_of": as_of}
    )
    for patient_id, scheduled in result:
        history[int(patient_id)].append(scheduled)
    return history
