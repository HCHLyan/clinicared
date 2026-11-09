"""Simulators of external systems for local development and demos."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Header
from pydantic import BaseModel

router = APIRouter(prefix="/simulate", tags=["External Simulators"])

#: Unit prices used by the simulator only (COP). Real prices come from the provider.
SIMULATED_COST_COP = {"SMS": 45.0, "WHATSAPP": 38.0, "EMAIL": 2.0}


class AdmissionCheckRequest(BaseModel):
    """Admission verification request."""

    national_id: str


class MessageNotificationRequest(BaseModel):
    """Outbound message request."""

    recipient: str
    message: str
    channel: str = "SMS"


@router.post("/admissions/verify")
def simulate_admission_check(payload: AdmissionCheckRequest) -> dict[str, str]:
    """Simulate eligibility verification in the external admissions system."""
    is_active = int(payload.national_id[-1]) % 2 == 0 if payload.national_id.isdigit() else True
    return {
        "national_id": payload.national_id,
        "status": "ACTIVE" if is_active else "PENDING_AUTHORIZATION",
        "network": "ClínicaRed Preferred",
        "message": "Verificación de admisiones simulada con éxito.",
    }


@router.post("/messaging/send")
def simulate_send_message(
    payload: MessageNotificationRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")
) -> dict[str, object]:
    """Simulate a provider that honours ``Idempotency-Key``.

    The same key always yields the same ``message_id``, mirroring the
    deduplication contract required from the real provider (ADR-005).
    """
    seed = idempotency_key or f"{payload.recipient}|{payload.message}"
    channel = payload.channel.upper()
    return {
        "status": "SENT",
        "channel": channel,
        "message_id": "sim-" + hashlib.sha256(seed.encode()).hexdigest()[:32],
        "cost_cop": SIMULATED_COST_COP.get(channel, 45.0),
        "simulated_gateway_response": "OK_200_DISPATCHED",
    }
