"""Appointment endpoints: book, cancel (RN-06), confirm, accept waitlist offer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from src.api.idempotency import run_idempotent
from src.core.clock import LOCAL_TZ, local_now
from src.core.config import settings
from src.core.database import engine
from src.core.metrics import MetricsEmitter
from src.domain.enums import BookingChannel
from src.services.appointment_service import (
    BookingRequest,
    BusinessRuleViolation,
    NotFoundError,
    accept_waitlist_offer,
    book_appointment,
    cancel_appointment,
    confirm_appointment,
)
from src.services.calendar_provider import CachedCalendarProvider

router = APIRouter(tags=["Appointments"])
_calendars = CachedCalendarProvider(engine)
_metrics = MetricsEmitter(settings.METRICS_NAMESPACE, "api")


class BookingIn(BaseModel):
    """Booking request body."""

    patient_id: int = Field(gt=0)
    professional_id: int = Field(gt=0)
    location_id: int = Field(gt=0)
    specialty_id: int = Field(gt=0)
    scheduled_at: datetime
    channel: BookingChannel = BookingChannel.SELF_SERVICE
    overbook: bool = False
    override_reason: str | None = Field(default=None, max_length=500)

    @field_validator("scheduled_at")
    @classmethod
    def _to_local_naive(cls, value: datetime) -> datetime:
        """Store clinic-local naive time; convert aware datetimes first."""
        return value.astimezone(LOCAL_TZ).replace(tzinfo=None) if value.tzinfo else value


def _rule_error(exc: BusinessRuleViolation) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail={"code": exc.code, "message": str(exc)})


@router.post("/appointments", status_code=status.HTTP_201_CREATED)
def create_appointment(
    body: BookingIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")
) -> JSONResponse:
    """Book an appointment (RN-07 restriction, RN-05 overbooking)."""
    if body.channel is BookingChannel.WAITLIST:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "WAITLIST bookings go through offer acceptance")
    now = local_now()
    request = BookingRequest(**body.model_dump())

    def action() -> tuple[int, dict[str, Any]]:
        result = book_appointment(conn, request, now, settings.OVERBOOK_BLOCK_MINUTES)
        return 201, {
            "appointment_id": result.appointment_id,
            "booking_reason": result.booking_reason,
            "staff_override": result.requires_audit,
        }

    try:
        with engine.begin() as conn:
            code, payload = run_idempotent(
                conn, key=idempotency_key, endpoint="POST /appointments", payload=body.model_dump(mode="json"), action=action
            )
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except BusinessRuleViolation as exc:
        raise _rule_error(exc) from exc
    return JSONResponse(status_code=code, content=payload)


@router.post("/appointments/{appointment_id}/cancel")
def cancel(appointment_id: int) -> dict[str, Any]:
    """Cancel (idempotent) and release the slot to the waitlist within the same transaction."""
    now = local_now()
    try:
        with engine.begin() as conn:
            result = cancel_appointment(conn, appointment_id, now, _calendars.get())
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except BusinessRuleViolation as exc:
        raise _rule_error(exc) from exc
    offer = result.offer
    if offer is not None and offer.offered and offer.release_latency_seconds is not None:
        _metrics.emit("WaitlistReleaseSeconds", offer.release_latency_seconds, unit="Seconds")
    return {
        "appointment_id": appointment_id,
        "already_cancelled": result.already_cancelled,
        "waitlist": None if offer is None else {"offered": offer.offered, "reason": offer.reason, "offer_id": offer.offer_id},
    }


@router.post("/appointments/{appointment_id}/confirm")
def confirm(appointment_id: int) -> dict[str, Any]:
    """Confirm attendance (idempotent)."""
    try:
        with engine.begin() as conn:
            changed = confirm_appointment(conn, appointment_id, local_now())
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"appointment_id": appointment_id, "confirmed_now": changed}


@router.post("/waitlist/offers/{offer_id}/accept")
def accept_offer(offer_id: int) -> dict[str, Any]:
    """Accept a waitlist offer (idempotent: repeated calls return the same appointment)."""
    try:
        with engine.begin() as conn:
            new_id = accept_waitlist_offer(conn, offer_id, local_now())
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except BusinessRuleViolation as exc:
        raise _rule_error(exc) from exc
    return {"offer_id": offer_id, "appointment_id": new_id}
