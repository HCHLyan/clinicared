"""Messaging provider webhooks (signed, deduplicated, order-tolerant)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from src.core.config import settings
from src.core.database import engine
from src.domain.enums import DispatchStatus
from src.repositories.sql_webhook_repository import SqlWebhookRepository
from src.services.webhook_service import WebhookEvent, WebhookEventType, WebhookOutcome, WebhookService, verify_signature

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


class ProviderEventIn(BaseModel):
    """Provider callback body."""

    event_id: str
    type: WebhookEventType
    message_id: str
    occurred_at: datetime
    status: DispatchStatus | None = None
    reply_text: str | None = None


class _RetryLater(Exception):
    """Abort the transaction so the provider retries later."""


@router.post("/messaging")
async def messaging_webhook(request: Request, x_signature: str | None = Header(default=None)) -> dict[str, str]:
    """Receive delivery receipts and replies.

    * 401 on invalid signature.
    * 409 when the message is not yet known (race with ``mark_sent``); the
      transaction is rolled back so the provider's retry is processed later.
    * 200 otherwise, including duplicates (so the provider stops retrying).
    """
    raw = await request.body()
    if not verify_signature(raw, x_signature, settings.WEBHOOK_SIGNING_SECRET.get_secret_value()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    try:
        body = ProviderEventIn.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.errors()) from exc

    event = WebhookEvent(
        provider_event_id=body.event_id,
        event_type=body.type,
        provider_message_id=body.message_id,
        occurred_at=body.occurred_at.replace(tzinfo=None),
        status=body.status,
        reply_text=body.reply_text,
    )

    def process() -> WebhookOutcome:
        with engine.begin() as conn:
            outcome = WebhookService(SqlWebhookRepository(conn)).process(event)
            if outcome is WebhookOutcome.UNKNOWN_MESSAGE:
                raise _RetryLater
            return outcome

    try:
        outcome = await run_in_threadpool(process)
    except _RetryLater as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Message not yet registered; retry later") from exc
    return {"event_id": body.event_id, "outcome": outcome.value}
