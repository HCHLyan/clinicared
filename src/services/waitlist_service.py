"""RN-06: offer released slots to the waitlist inside the caller's transaction."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from src.domain.enums import Channel, RecipientType, ReminderKind
from src.domain.idempotency import waitlist_offer_key
from src.domain.messaging_calendar import MessagingCalendar
from src.domain.rules.booking import is_restricted
from src.domain.rules.no_show import count_no_shows_in_window
from src.domain.rules.reminders import WAITLIST_OFFER_PRIORITY, resolve_recipient
from src.domain.rules.waitlist import ReleasedSlot, WaitlistEntry, offer_expiry, released_within_sla, select_candidate
from src.messaging.topology import MAIN_EXCHANGE, WAITLIST_ROUTING_KEY
from src.services.templates import render_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OfferResult:
    """Outcome of trying to release a slot."""

    offered: bool
    reason: str
    offer_id: int | None = None
    waitlist_id: int | None = None
    release_latency_seconds: float | None = None


_SLOT_SQL = text(
    """
    SELECT ap.id, ap.specialty_id, ap.location_id, ap.scheduled_datetime, ap.cancelled_at, l.name AS branch_name
      FROM appointments ap JOIN locations l ON l.id = ap.location_id
     WHERE ap.id = :id AND ap.status = 'CANCELLED'
    """
)
_CANDIDATES_SQL = text(
    """
    SELECT w.id, w.patient_id, w.specialty_id, w.location_id, w.requested_at, w.priority,
           p.date_of_birth, p.phone, p.email, p.preferred_channel,
           g.phone AS guardian_phone, g.email AS guardian_email,
           ARRAY(
               SELECT ap.scheduled_datetime FROM attendances at JOIN appointments ap ON ap.id = at.appointment_id
                WHERE ap.patient_id = w.patient_id AND at.status = 'NO_SHOW'
                  AND ap.scheduled_datetime >= :now - INTERVAL '7 months'
           ) AS no_show_dates
      FROM waitlist w
      JOIN patients p ON p.id = w.patient_id
      LEFT JOIN guardians g ON g.patient_id = p.id AND g.is_primary
     WHERE w.status = 'WAITING' AND w.specialty_id = :specialty_id
       AND (w.location_id IS NULL OR w.location_id = :location_id)
     ORDER BY w.priority DESC, w.requested_at, w.id
     LIMIT 50
     FOR UPDATE OF w SKIP LOCKED
    """
)
_ALREADY_OFFERED_SQL = text(
    "SELECT w.patient_id FROM waitlist_offers o JOIN waitlist w ON w.id = o.waitlist_id WHERE o.released_appointment_id = :id"
)


def offer_released_slot(conn: Connection, appointment_id: int, now: datetime, calendar: MessagingCalendar) -> OfferResult:
    """Create the offer + message + outbox event for a cancelled appointment.

    Must run inside the cancellation transaction (or the expiry sweeper's).
    Idempotent: a second call while an offer is OPEN is a no-op thanks to the
    partial unique index ``ux_waitlist_offer_open``.
    """
    slot_row = conn.execute(_SLOT_SQL, {"id": appointment_id}).mappings().first()
    if slot_row is None:
        return OfferResult(False, "NOT_CANCELLED")
    if conn.execute(
        text("SELECT 1 FROM waitlist_offers WHERE released_appointment_id = :id AND status IN ('OPEN', 'ACCEPTED')"),
        {"id": appointment_id},
    ).first():
        return OfferResult(False, "ALREADY_OFFERED")

    notify_at = calendar.next_valid_slot(now)
    slot = ReleasedSlot(
        appointment_id=appointment_id,
        specialty_id=slot_row["specialty_id"],
        location_id=slot_row["location_id"],
        scheduled_at=slot_row["scheduled_datetime"],
        cancelled_at=slot_row["cancelled_at"] or now,
    )
    candidates = conn.execute(
        _CANDIDATES_SQL, {"specialty_id": slot.specialty_id, "location_id": slot.location_id, "now": now}
    ).mappings().all()
    excluded = frozenset(int(r[0]) for r in conn.execute(_ALREADY_OFFERED_SQL, {"id": appointment_id}))
    by_id = {int(r["id"]): r for r in candidates}
    entries = [
        WaitlistEntry(
            entry_id=int(r["id"]),
            patient_id=int(r["patient_id"]),
            specialty_id=int(r["specialty_id"]),
            location_id=r["location_id"],
            requested_at=r["requested_at"],
            priority=int(r["priority"]),
            is_restricted=is_restricted(count_no_shows_in_window(r["no_show_dates"] or [], now)),
        )
        for r in candidates
    ]
    # The offer is evaluated at notification time (RN-04 may delay it to 07:00).
    chosen = select_candidate(slot, entries, notify_at, excluded)
    if chosen is None:
        return OfferResult(False, "NO_ELIGIBLE_CANDIDATE")

    candidate = by_id[chosen.entry_id]
    recipient = resolve_recipient(
        birth_date=candidate["date_of_birth"],
        appointment_date=slot.scheduled_at.date(),
        has_patient_contact=bool(candidate["phone"] or candidate["email"]),
        has_guardian_contact=bool(candidate["guardian_phone"] or candidate["guardian_email"]),
    )
    if not isinstance(recipient, RecipientType):
        logger.warning("waitlist candidate not contactable", extra={"waitlist_id": chosen.entry_id, "reason": recipient})
        return OfferResult(False, f"CANDIDATE_{recipient}")

    expires_at = offer_expiry(notify_at, slot.scheduled_at)
    latency = (now - slot.cancelled_at).total_seconds()
    offer_id = conn.execute(
        text(
            """
            INSERT INTO waitlist_offers (released_appointment_id, waitlist_id, offered_at, expires_at, release_latency_seconds)
            VALUES (:aid, :wid, :now, :exp, :lat)
            ON CONFLICT DO NOTHING
            RETURNING id
            """
        ),
        {"aid": appointment_id, "wid": chosen.entry_id, "now": now, "exp": expires_at, "lat": max(latency, 0.0)},
    ).scalar_one_or_none()
    if offer_id is None:
        return OfferResult(False, "CONCURRENT_OFFER")

    conn.execute(text("UPDATE waitlist SET status = 'OFFERED' WHERE id = :id"), {"id": chosen.entry_id})
    phone = candidate["guardian_phone"] if recipient is RecipientType.GUARDIAN else candidate["phone"]
    email = candidate["guardian_email"] if recipient is RecipientType.GUARDIAN else candidate["email"]
    channel = Channel.EMAIL if not phone else Channel(candidate["preferred_channel"] or "SMS")
    if channel is Channel.EMAIL and not email:
        channel = Channel.SMS
    key = waitlist_offer_key(appointment_id, chosen.entry_id)
    immediate = notify_at <= now
    conn.execute(
        text(
            """
            INSERT INTO messages (appointment_id, patient_id, message_type, status, idempotency_key, recipient_type,
                                  channel, recipient_address, body, send_at, priority, waitlist_offer_id, enqueued_at)
            VALUES (:aid, :pid, :kind, :status, :key, :rtype, :channel, :address, :body, :send_at, :prio, :oid, :enq)
            ON CONFLICT (idempotency_key) DO NOTHING
            """
        ),
        {
            "aid": appointment_id,
            "pid": chosen.patient_id,
            "kind": ReminderKind.WAITLIST_OFFER.value,
            "status": "ENQUEUED" if immediate else "PLANNED",
            "key": key,
            "rtype": recipient.value,
            "channel": channel.value,
            "address": email if channel is Channel.EMAIL else phone,
            "body": render_message(
                kind=ReminderKind.WAITLIST_OFFER,
                recipient_type=recipient,
                scheduled_at=slot.scheduled_at,
                branch_name=str(slot_row["branch_name"]),
                offer_expires_at=expires_at,
            ),
            "send_at": notify_at,
            "prio": WAITLIST_OFFER_PRIORITY,
            "oid": offer_id,
            "enq": now if immediate else None,
        },
    )
    if immediate:
        conn.execute(
            text(
                """
                INSERT INTO outbox_events (event_key, exchange, routing_key, priority, payload)
                VALUES (:key, :exchange, :rk, :prio, CAST(:payload AS JSONB))
                ON CONFLICT (event_key) DO NOTHING
                """
            ),
            {
                "key": key,
                "exchange": MAIN_EXCHANGE,
                "rk": WAITLIST_ROUTING_KEY,
                "prio": WAITLIST_OFFER_PRIORITY,
                "payload": json.dumps({"idempotency_key": key}),
            },
        )
    if not released_within_sla(slot.cancelled_at, now):
        logger.warning("waitlist release outside SLA", extra={"appointment_id": appointment_id, "latency_s": latency})
    return OfferResult(True, "OFFERED", offer_id, chosen.entry_id, latency)


def expire_and_reoffer(engine: Engine, now: datetime, calendar: MessagingCalendar) -> dict[str, int]:
    """Expire lapsed offers and offer the slot to the next candidate.

    Returns:
        Counts of expired offers and new offers.
    """
    expired_slots: list[int] = []
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                UPDATE waitlist_offers SET status = 'EXPIRED'
                 WHERE status = 'OPEN' AND expires_at <= :now
                RETURNING released_appointment_id, waitlist_id
                """
            ),
            {"now": now},
        ).all()
        for released_id, waitlist_id in rows:
            conn.execute(text("UPDATE waitlist SET status = 'WAITING' WHERE id = :id AND status = 'OFFERED'"), {"id": waitlist_id})
            expired_slots.append(int(released_id))
    reoffered = 0
    for appointment_id in expired_slots:
        with engine.begin() as conn:
            if offer_released_slot(conn, appointment_id, now, calendar).offered:
                reoffered += 1
    return {"expired": len(expired_slots), "reoffered": reoffered}
