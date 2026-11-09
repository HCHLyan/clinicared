"""HTTP adapter to the messaging provider (SMS / WhatsApp / e-mail).

The provider contract required by ADR-005: it must honour the
``Idempotency-Key`` header for at least 24 hours, returning the original
``message_id`` for repeated keys instead of sending again.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from src.domain.enums import Channel
from src.services.ports import GatewayResult, PermanentGatewayError, TransientGatewayError

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
#: Fallback unit costs (COP) when the provider does not report a price.
DEFAULT_COST_COP: dict[Channel, Decimal] = {
    Channel.SMS: Decimal("45.00"),
    Channel.WHATSAPP: Decimal("38.00"),
    Channel.EMAIL: Decimal("2.00"),
}


class HttpMessagingGateway:
    """Synchronous HTTP client with connection pooling.

    Args:
        base_url: Provider ``send`` endpoint.
        token: Bearer token (from Secrets Manager).
        timeout_seconds: Total request timeout; must be below the claim lease.
    """

    def __init__(self, base_url: str, token: str, timeout_seconds: float = 5.0) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._url = base_url
        self._client = httpx.Client(timeout=timeout_seconds, headers=headers)

    def send(self, *, idempotency_key: str, channel: Channel, to: str, body: str) -> GatewayResult:
        """Send a message.

        Raises:
            TransientGatewayError: Network error or retryable HTTP status.
            PermanentGatewayError: Any other non-2xx response or bad payload.
        """
        try:
            response = self._client.post(
                self._url,
                json={"recipient": to, "message": body, "channel": channel.value},
                headers={"Idempotency-Key": idempotency_key},
            )
        except httpx.TransportError as exc:
            raise TransientGatewayError(f"transport error: {exc}") from exc

        if response.status_code in _RETRYABLE_STATUS:
            raise TransientGatewayError(f"provider returned {response.status_code}")
        if response.status_code >= 400:
            raise PermanentGatewayError(f"provider returned {response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PermanentGatewayError("provider returned a non-JSON body") from exc

        provider_id = payload.get("message_id")
        if not provider_id:
            raise PermanentGatewayError("provider response is missing message_id")
        provider_id = str(provider_id)
        cost = payload.get("cost_cop")
        return GatewayResult(
            provider_message_id=provider_id,
            cost_cop=Decimal(str(cost)) if cost is not None else DEFAULT_COST_COP[channel],
        )

    def close(self) -> None:
        """Release pooled connections."""
        self._client.close()
