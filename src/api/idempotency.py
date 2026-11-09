"""``Idempotency-Key`` support for non-idempotent POST endpoints.

The first successful response for ``(key, endpoint)`` is stored in the same
transaction as the business write; retries with the same key and payload get
the stored response, retries with a different payload get HTTP 422.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import Connection

MAX_KEY_LENGTH = 100


def request_fingerprint(payload: Any) -> str:
    """Stable SHA-256 of a JSON-serializable payload."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def run_idempotent(
    conn: Connection,
    *,
    key: str | None,
    endpoint: str,
    payload: Any,
    action: Callable[[], tuple[int, dict[str, Any]]],
) -> tuple[int, dict[str, Any]]:
    """Execute ``action`` at most once per idempotency key.

    Args:
        conn: Connection with an open transaction.
        key: Client-supplied key (None disables idempotency).
        endpoint: Logical endpoint name.
        payload: Request body used for the fingerprint.
        action: Performs the write and returns ``(status_code, body)``.

    Returns:
        ``(status_code, body)`` either fresh or replayed.

    Raises:
        HTTPException: 400 for an invalid key, 422 when a key is reused with a
            different payload.
    """
    if key is None:
        return action()
    if not key or len(key) > MAX_KEY_LENGTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Idempotency-Key")
    fingerprint = request_fingerprint(payload)
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{endpoint}|{key}"})
    stored = conn.execute(
        text(
            "SELECT request_hash, status_code, response_body FROM api_idempotency_keys WHERE idempotency_key = :k AND endpoint = :e"
        ),
        {"k": key, "e": endpoint},
    ).mappings().first()
    if stored is not None:
        if stored["request_hash"] != fingerprint:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Idempotency-Key reused with a different payload")
        return int(stored["status_code"]), dict(stored["response_body"])

    status_code, body = action()
    conn.execute(
        text(
            """
            INSERT INTO api_idempotency_keys (idempotency_key, endpoint, request_hash, status_code, response_body)
            VALUES (:k, :e, :h, :s, CAST(:b AS JSONB))
            """
        ),
        {"k": key, "e": endpoint, "h": fingerprint, "s": status_code, "b": json.dumps(body, default=str)},
    )
    return status_code, body
