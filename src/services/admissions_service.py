"""F3 admissions consolidation and RN-01 no-show evaluation (daily batch)."""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.domain.enums import AttendanceOutcome
from src.domain.privacy import normalize_national_id
from src.domain.rules.no_show import evaluate_attendance

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("admission_id", "national_id", "admitted_at")
#: Window used to link an admission without appointment_id to an appointment.
LINK_WINDOW = timedelta(hours=3)


@dataclass(frozen=True, slots=True)
class LoadReport:
    """Counts of an F3 file load."""

    read: int
    loaded: int
    rejected: int


def parse_admissions_csv(content: str) -> tuple[list[dict[str, object]], int]:
    """Parse and validate an F3 CSV.

    Args:
        content: File content (UTF-8, header row).

    Returns:
        ``(valid_rows, rejected_count)``.

    Raises:
        ValueError: If a required column is missing from the header.
    """
    reader = csv.DictReader(io.StringIO(content))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"F3 file missing columns: {missing}")
    valid: list[dict[str, object]] = []
    rejected = 0
    for raw in reader:
        try:
            admission_id = (raw.get("admission_id") or "").strip()
            national_id = normalize_national_id(raw.get("national_id") or "")
            admitted_at = datetime.fromisoformat((raw.get("admitted_at") or "").strip())
            if not admission_id or not national_id:
                raise ValueError("empty identifier")
            appointment_raw = (raw.get("appointment_id") or "").strip()
            valid.append(
                {
                    "admission_id": admission_id,
                    "appointment_id": int(appointment_raw) if appointment_raw else None,
                    "national_id": national_id,
                    "location_code": (raw.get("location_code") or "").strip() or None,
                    "admitted_at": admitted_at.replace(tzinfo=None),
                }
            )
        except ValueError:
            rejected += 1
    return valid, rejected


def load_admissions(engine: Engine, rows: Iterable[Mapping[str, object]], source_file: str) -> int:
    """Upsert admissions and link them to appointments (idempotent).

    Returns:
        Number of rows upserted.
    """
    payload = [{**row, "source_file": source_file} for row in rows]
    if not payload:
        return 0
    upsert = text(
        """
        INSERT INTO admissions (admission_id, appointment_id, national_id, location_code, admitted_at, source_file)
        VALUES (:admission_id, :appointment_id, :national_id, :location_code, :admitted_at, :source_file)
        ON CONFLICT (admission_id) DO UPDATE SET
            appointment_id = COALESCE(EXCLUDED.appointment_id, admissions.appointment_id),
            national_id = EXCLUDED.national_id,
            location_code = EXCLUDED.location_code,
            admitted_at = EXCLUDED.admitted_at,
            source_file = EXCLUDED.source_file,
            loaded_at = CURRENT_TIMESTAMP
        """
    )
    # Admissions without a (valid) appointment id are linked by patient + nearest time.
    link = text(
        """
        UPDATE admissions a SET appointment_id = m.appointment_id
          FROM (
            SELECT DISTINCT ON (a2.admission_id) a2.admission_id, ap.id AS appointment_id
              FROM admissions a2
              JOIN patients p ON regexp_replace(upper(p.national_id), '[^0-9A-Z]', '', 'g') = a2.national_id
              JOIN appointments ap ON ap.patient_id = p.id
               AND ap.scheduled_datetime BETWEEN a2.admitted_at - :window AND a2.admitted_at + :window
             WHERE a2.appointment_id IS NULL
             ORDER BY a2.admission_id, abs(extract(epoch FROM ap.scheduled_datetime - a2.admitted_at))
          ) m
         WHERE a.admission_id = m.admission_id
        """
    )
    with engine.begin() as conn:
        # An unknown appointment id would violate the FK and abort the batch: null it first.
        valid_ids = {
            r[0]
            for r in conn.execute(
                text("SELECT id FROM appointments WHERE id = ANY(:ids)"),
                {"ids": [p["appointment_id"] for p in payload if p["appointment_id"] is not None]},
            )
        }
        for p in payload:
            if p["appointment_id"] is not None and p["appointment_id"] not in valid_ids:
                p["appointment_id"] = None
        conn.execute(upsert, payload)
        conn.execute(link, {"window": LINK_WINDOW})
    return len(payload)


def evaluate_no_shows(engine: Engine, target_date: date, as_of: datetime, lookback_days: int = 3) -> dict[str, int]:
    """Apply RN-01 to appointments of ``target_date`` and the previous days.

    Re-evaluating previous days absorbs late-arriving F3 files. The update is
    deterministic, so reruns and backfills are idempotent.

    Returns:
        Count of appointments per outcome.
    """
    start = datetime.combine(target_date - timedelta(days=lookback_days), time.min)
    end = datetime.combine(target_date + timedelta(days=1), time.min)
    select = text(
        """
        SELECT ap.id, ap.scheduled_datetime, ap.cancelled_at, ap.status,
               (SELECT min(ad.admitted_at) FROM admissions ad WHERE ad.appointment_id = ap.id) AS admitted_at
          FROM appointments ap
         WHERE ap.scheduled_datetime >= :start AND ap.scheduled_datetime < :end
        """
    )
    upsert_attendance = text(
        """
        INSERT INTO attendances (appointment_id, status, check_in_time, notes)
        VALUES (:id, :outcome, :admitted_at, :reason)
        ON CONFLICT (appointment_id) DO UPDATE SET
            status = EXCLUDED.status, check_in_time = EXCLUDED.check_in_time, notes = EXCLUDED.notes
        """
    )
    update_appointment = text(
        """
        UPDATE appointments SET status = :status, outcome_reason = :reason
         WHERE id = :id AND status <> 'CANCELLED' AND (status <> :status OR outcome_reason IS DISTINCT FROM :reason)
        """
    )
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        rows = conn.execute(select, {"start": start, "end": end}).mappings().all()
        attendance_rows: list[dict[str, object]] = []
        status_rows: list[dict[str, object]] = []
        for row in rows:
            evaluation = evaluate_attendance(
                scheduled_at=row["scheduled_datetime"],
                as_of=as_of,
                admitted_at=row["admitted_at"],
                cancelled_at=row["cancelled_at"],
            )
            counts[evaluation.outcome.value] = counts.get(evaluation.outcome.value, 0) + 1
            if evaluation.outcome is AttendanceOutcome.PENDING_EVALUATION:
                continue
            attendance_rows.append(
                {"id": row["id"], "outcome": evaluation.outcome.value, "admitted_at": row["admitted_at"], "reason": evaluation.reason}
            )
            if evaluation.outcome in (AttendanceOutcome.ATTENDED, AttendanceOutcome.NO_SHOW):
                status_rows.append({"id": row["id"], "status": evaluation.outcome.value, "reason": evaluation.reason})
        if attendance_rows:
            conn.execute(upsert_attendance, attendance_rows)
        if status_rows:
            conn.execute(update_appointment, status_rows)
    logger.info("no-show evaluation finished", extra={"target_date": str(target_date), "counts": counts})
    return counts
