"""HTTP middleware: correlation id, latency metrics and RN-10 audit logging."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from src.core.logging_config import request_id_var
from src.core.metrics import MetricsEmitter

logger = logging.getLogger("api.access")

#: Route prefixes that expose patient data (RN-10).
AUDITED_PREFIXES: tuple[str, ...] = ("/patients", "/appointments", "/waitlist")
_ID_PATTERN = re.compile(r"^/(?P<resource>[a-z_]+)(?:/(?P<id>[0-9]+))?")
_METHOD_TO_ACTION = {"GET": "READ", "POST": "CREATE", "PUT": "UPDATE", "PATCH": "UPDATE", "DELETE": "DELETE"}

CallNext = Callable[[Request], Awaitable[Response]]


def resolve_actor(request: Request) -> str:
    """Identity injected by the ALB OIDC authenticator, or an explicit header.

    The ALB strips client-supplied ``x-amzn-oidc-*`` headers, so the value is
    trustworthy in AWS. Locally, ``X-Actor-Id`` is used for testing.
    """
    return request.headers.get("x-amzn-oidc-identity") or request.headers.get("x-actor-id") or "anonymous"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, log access lines and emit ``ApiLatency`` (EMF)."""

    def __init__(self, app: ASGIApp, metrics: MetricsEmitter) -> None:
        super().__init__(app)
        self._metrics = metrics

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        """Wrap the request with timing and correlation."""
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            route = getattr(request.scope.get("route"), "path", "unmatched")
            self._metrics.emit(
                "ApiLatency",
                round(elapsed_ms, 2),
                unit="Milliseconds",
                dimensions={"Route": f"{request.method} {route}"},
                properties={"status_code": status_code, "request_id": request_id},
            )
            if status_code >= 500:
                self._metrics.emit("Api5xx", 1, dimensions={"Route": f"{request.method} {route}"})
            logger.info(
                "request",
                extra={"method": request.method, "path": route, "status_code": status_code, "latency_ms": round(elapsed_ms, 2)},
            )
            request_id_var.reset(token)


class AuditMiddleware(BaseHTTPMiddleware):
    """RN-10: append an immutable audit row for every access to patient data.

    Fail-closed: if the audit row cannot be written, the client receives 503
    instead of the data. The table itself is append-only (triggers + grants)
    and hash-chained (see ``02_us04_schema.sql``).
    """

    def __init__(self, app: ASGIApp, engine: Engine) -> None:
        super().__init__(app)
        self._engine = engine

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        """Write the audit entry after the handler runs."""
        path = request.url.path
        if not path.startswith(AUDITED_PREFIXES):
            return await call_next(request)
        response = await call_next(request)
        match = _ID_PATTERN.match(path)
        entry: dict[str, Any] = {
            "actor": resolve_actor(request),
            "action": _METHOD_TO_ACTION.get(request.method, request.method),
            "resource_type": match.group("resource") if match else "unknown",
            "resource_id": match.group("id") if match else None,
            "request_id": request_id_var.get(),
            "http_path": path[:255],
            "status_code": response.status_code,
            "client_ip": request.headers.get("x-forwarded-for", request.client.host if request.client else None),
        }
        try:
            await run_in_threadpool(self._write, entry)
        except Exception:  # noqa: BLE001 - any failure must block the response
            logger.exception("audit write failed; blocking response", extra={"path": path})
            return JSONResponse(status_code=503, content={"detail": "Audit unavailable; request blocked (RN-10)"})
        return response

    def _write(self, entry: dict[str, Any]) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO audit_log (actor, action, resource_type, resource_id, request_id, http_path, status_code, client_ip)
                    VALUES (:actor, :action, :resource_type, :resource_id, :request_id, :http_path, :status_code, :client_ip)
                    """
                ),
                entry,
            )
