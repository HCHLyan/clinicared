"""Transactional appointment use cases: book (RN-05, RN-07), cancel (RN-06),
confirm and accept a waitlist offer.

Every function receives a ``Connection`` with an open transaction owned by
the caller (API route), so the business write, the waitlist offer, the
outbox event and the audit entry commit or roll back together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.domain.enums import BookingChannel
from src.domain.messaging_calendar import MessagingCalendar
from src.domain.rules.booking import evaluate_booking
from src.domain.rules.no_show import count_no_shows_in_window
from src.domain.rules.overbooking import block_start, evaluate_overbook_request
from src.domain.timeutils import subtract_months
from src.services.waitlist_service import OfferResult, offer_released_slot


class BusinessRuleViolation(Exception):
    """A business rule rejected the operation (mapped to HTTP 409/422)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class NotFoundError(Exception):
    """Entity does not exist (HTTP 404)."""


@dataclass(frozen=True, slots=True)
class BookingRequest:
    """Input of a booking."""

    patient_id: int
    professional_id: int
    location_id: int
    specialty_id: int
    scheduled_at: datetime
    channel: BookingChannel
    overbook: bool = False
    override_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BookingResult:
    """Created appointment and the rule decisions taken."""

    appointment_id: int
    booking_reason: str
    requires_audit: bool


@dataclass(frozen=True, slots=True)
class CancelResult:
    """Cancellation outcome including the RN-06 release."""

    appointment_id: int
    already_cancelled: bool
    offer: OfferResult | None


def patient_no_shows_6m(conn: Connection, patient_id: int, as_of: datetime) -> int:
    """Count the patient's RN-01 no-shows in the six months before ``as_of``."""
    rows = conn.execute(
        text(
            """
            SELECT ap.scheduled_datetime FROM attendances at JOIN appointments ap ON ap.id = at.appointment_id
             WHERE ap.patient_id = :pid AND at.status = 'NO_SHOW' AND ap.scheduled_datetime >= :since
            """
        ),
        {"pid": patient_id, "since": subtract_months(as_of, 6)},
    ).all()
    return count_no_shows_in_window((r[0] for r in rows), as_of)


def book_appointment(conn: Connection, request: BookingRequest, now: datetime, block_minutes: int) -> BookingResult:
    """Book an appointment enforcing RN-07 and, for overbooking, RN-05.

    Raises:
        NotFoundError: Patient or specialty does not exist.
        BusinessRuleViolation: Restricted patient, slot taken or no overbook quota.
    """
    if request.scheduled_at <= now:
        raise BusinessRuleViolation("PAST_SLOT", "Cannot book an appointment in the past")
    if conn.execute(text("SELECT 1 FROM patients WHERE id = :id"), {"id": request.patient_id}).first() is None:
        raise NotFoundError(f"patient {request.patient_id} not found")
    overbookable = conn.execute(
        text("SELECT is_overbookable FROM specialties WHERE id = :id"), {"id": request.specialty_id}
    ).scalar_one_or_none()
    if overbookable is None:
        raise NotFoundError(f"specialty {request.specialty_id} not found")

    decision = evaluate_booking(
        no_shows_last_6m=patient_no_shows_6m(conn, request.patient_id, now),
        channel=request.channel,
        override_reason=request.override_reason,
    )
    if not decision.allowed:
        raise BusinessRuleViolation(decision.reason, "Booking restricted by RN-07")

    if request.overbook:
        start = block_start(request.scheduled_at, block_minutes)
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"overbook:{request.professional_id}:{request.location_id}:{start.isoformat()}"},
        )
        counts = conn.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE NOT ap.is_overbook AND rs.risk_level = 'HIGH') AS high_regular,
                  count(*) FILTER (WHERE ap.is_overbook) AS used
                  FROM appointments ap LEFT JOIN risk_scores rs ON rs.appointment_id = ap.id
                 WHERE ap.professional_id = :prof AND ap.location_id = :loc AND ap.status <> 'CANCELLED'
                   AND ap.scheduled_datetime >= :start AND ap.scheduled_datetime < :start + make_interval(mins => :mins)
                """
            ),
            {"prof": request.professional_id, "loc": request.location_id, "start": start, "mins": block_minutes},
        ).mappings().one()
        overbook = evaluate_overbook_request(
            high_risk_regular_count=int(counts["high_regular"]),
            used_extra_slots=int(counts["used"]),
            specialty_overbookable=bool(overbookable),
        )
        if not overbook.allowed:
            raise BusinessRuleViolation(overbook.reason, "Overbooking not allowed by RN-05")
    else:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"slot:{request.professional_id}:{request.scheduled_at.isoformat()}"},
        )
        taken = conn.execute(
            text(
                """
                SELECT 1 FROM appointments WHERE professional_id = :prof AND scheduled_datetime = :at
                   AND status <> 'CANCELLED' AND NOT is_overbook
                """
            ),
            {"prof": request.professional_id, "at": request.scheduled_at},
        ).first()
        if taken:
            raise BusinessRuleViolation("SLOT_TAKEN", "The slot is already booked")

    appointment_id = conn.execute(
        text(
            """
            INSERT INTO appointments (patient_id, professional_id, location_id, specialty_id, scheduled_datetime,
                                      status, is_overbook, booking_channel, created_at)
            VALUES (:patient, :prof, :loc, :spec, :at, 'PENDING', :overbook, :channel, :now)
            RETURNING id
            """
        ),
        {
            "patient": request.patient_id,
            "prof": request.professional_id,
            "loc": request.location_id,
            "spec": request.specialty_id,
            "at": request.scheduled_at,
            "overbook": request.overbook,
            "channel": request.channel.value,
            "now": now,
        },
    ).scalar_one()
    return BookingResult(int(appointment_id), decision.reason, decision.requires_audit)


def cancel_appointment(conn: Connection, appointment_id: int, now: datetime, calendar: MessagingCalendar) -> CancelResult:
    """Cancel an appointment and release the slot to the waitlist (RN-06).

    Idempotent: cancelling twice returns ``already_cancelled=True``.

    Raises:
        NotFoundError: Unknown appointment.
        BusinessRuleViolation: Appointment already attended / evaluated.
    """
    row = conn.execute(
        text("SELECT id, status, is_overbook FROM appointments WHERE id = :id FOR UPDATE"), {"id": appointment_id}
    ).mappings().first()
    if row is None:
        raise NotFoundError(f"appointment {appointment_id} not found")
    if row["status"] == "CANCELLED":
        return CancelResult(appointment_id, True, None)
    if row["status"] in ("ATTENDED", "NO_SHOW"):
        raise BusinessRuleViolation("INVALID_STATE", f"Appointment is {row['status']}")

    conn.execute(
        text("UPDATE appointments SET status = 'CANCELLED', cancelled_at = :now, version = version + 1 WHERE id = :id"),
        {"now": now, "id": appointment_id},
    )
    conn.execute(
        text(
            """
            UPDATE messages SET status = 'CANCELLED', skip_reason = 'APPOINTMENT_CANCELLED'
             WHERE appointment_id = :id AND message_type IN ('H72', 'H24') AND status IN ('PLANNED', 'ENQUEUED')
            """
        ),
        {"id": appointment_id},
    )
    offer = None if row["is_overbook"] else offer_released_slot(conn, appointment_id, now, calendar)
    return CancelResult(appointment_id, False, offer)


def confirm_appointment(conn: Connection, appointment_id: int, now: datetime) -> bool:
    """Confirm an appointment (first confirmation wins).

    Raises:
        NotFoundError: Unknown appointment.
    """
    status = conn.execute(text("SELECT status FROM appointments WHERE id = :id"), {"id": appointment_id}).scalar_one_or_none()
    if status is None:
        raise NotFoundError(f"appointment {appointment_id} not found")
    result = conn.execute(
        text(
            """
            UPDATE appointments SET confirmed_at = :now, status = 'CONFIRMED'
             WHERE id = :id AND confirmed_at IS NULL AND status = 'PENDING'
            """
        ),
        {"now": now, "id": appointment_id},
    )
    return result.rowcount == 1


def accept_waitlist_offer(conn: Connection, offer_id: int, now: datetime) -> int:
    """Book the released slot for the offered patient (idempotent).

    Returns:
        Id of the appointment created for the waitlist patient.

    Raises:
        NotFoundError: Unknown offer.
        BusinessRuleViolation: Offer expired/declined.
    """
    offer = conn.execute(
        text(
            """
            SELECT o.id, o.status, o.expires_at, o.accepted_appointment_id, o.waitlist_id, w.patient_id,
                   ap.professional_id, ap.location_id, ap.specialty_id, ap.scheduled_datetime
              FROM waitlist_offers o
              JOIN waitlist w ON w.id = o.waitlist_id
              JOIN appointments ap ON ap.id = o.released_appointment_id
             WHERE o.id = :id
               FOR UPDATE OF o
            """
        ),
        {"id": offer_id},
    ).mappings().first()
    if offer is None:
        raise NotFoundError(f"offer {offer_id} not found")
    if offer["status"] == "ACCEPTED" and offer["accepted_appointment_id"] is not None:
        return int(offer["accepted_appointment_id"])
    if offer["status"] != "OPEN" or now >= offer["expires_at"]:
        raise BusinessRuleViolation("OFFER_NOT_AVAILABLE", "The offer is no longer available")

    new_id = conn.execute(
        text(
            """
            INSERT INTO appointments (patient_id, professional_id, location_id, specialty_id, scheduled_datetime,
                                      status, confirmed_at, booking_channel, created_at)
            VALUES (:patient, :prof, :loc, :spec, :at, 'CONFIRMED', :now, 'WAITLIST', :now)
            RETURNING id
            """
        ),
        {
            "patient": offer["patient_id"],
            "prof": offer["professional_id"],
            "loc": offer["location_id"],
            "spec": offer["specialty_id"],
            "at": offer["scheduled_datetime"],
            "now": now,
        },
    ).scalar_one()
    conn.execute(
        text("UPDATE waitlist_offers SET status = 'ACCEPTED', accepted_appointment_id = :aid WHERE id = :id"),
        {"aid": new_id, "id": offer_id},
    )
    conn.execute(text("UPDATE waitlist SET status = 'BOOKED' WHERE id = :id"), {"id": offer["waitlist_id"]})
    return int(new_id)
