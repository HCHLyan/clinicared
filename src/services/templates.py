"""Message templates.

Data minimization (RN-09 / E-15): messages never include the specialty,
professional or any clinical detail, because an SMS preview on a lock screen
can reveal sensitive health information. Only date, time and branch.
"""

from __future__ import annotations

from datetime import datetime

from src.domain.enums import RecipientType, ReminderKind

_WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _when(scheduled_at: datetime) -> str:
    return f"{_WEEKDAYS[scheduled_at.weekday()]} {scheduled_at:%d/%m} a las {scheduled_at:%H:%M}"


def render_message(
    *,
    kind: ReminderKind,
    recipient_type: RecipientType,
    scheduled_at: datetime,
    branch_name: str,
    severe_weather: bool = False,
    offer_expires_at: datetime | None = None,
) -> str:
    """Render the text of an outbound message.

    Args:
        kind: Reminder kind.
        recipient_type: Patient or guardian (RN-11 wording).
        scheduled_at: Appointment start.
        branch_name: Branch display name.
        severe_weather: F6 severe-weather flag for the branch city/date.
        offer_expires_at: Deadline to accept a waitlist offer.

    Returns:
        Message text (<= ~300 chars).
    """
    subject = "la cita de su acudido(a)" if recipient_type is RecipientType.GUARDIAN else "su cita"
    if kind is ReminderKind.WAITLIST_OFFER:
        deadline = f" antes de las {offer_expires_at:%H:%M}" if offer_expires_at else ""
        text = (
            f"ClínicaRed: se liberó un cupo el {_when(scheduled_at)} en {branch_name}. "
            f"Responda SI{deadline} para tomarlo."
        )
    else:
        lead = "en 3 días" if kind is ReminderKind.H72 else "mañana"
        text = (
            f"ClínicaRed: le recordamos {subject} {lead}, {_when(scheduled_at)}, en {branch_name}. "
            "Responda SI para confirmar o llame para cancelar."
        )
    if severe_weather:
        text += " Pronóstico de lluvias fuertes: salga con tiempo."
    return text
