"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from src.api.middleware import AuditMiddleware, RequestContextMiddleware
from src.api.routes import appointments, patients, simulators, webhooks
from src.core.config import settings
from src.core.database import engine
from src.core.logging_config import configure_logging
from src.core.metrics import MetricsEmitter

# The schema is owned by docs/database/*.sql (ADR-001); the app no longer runs
# create_all at import time, which also lets tests import the app without a DB.
configure_logging("api", settings.LOG_LEVEL)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="API central para la gestión de citas, pacientes, admisiones y recordatorios.",
    version=settings.VERSION,
)

# Starlette: the last middleware added is the outermost one.
app.add_middleware(AuditMiddleware, engine=engine)
app.add_middleware(RequestContextMiddleware, metrics=MetricsEmitter(settings.METRICS_NAMESPACE, "api"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if settings.ENVIRONMENT == "production" else ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(patients.router)
app.include_router(appointments.router)
app.include_router(webhooks.router)
if settings.ENVIRONMENT != "production":
    app.include_router(simulators.router)


@app.get("/", include_in_schema=False)
def read_root() -> RedirectResponse:
    """Redirect to the interactive documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["Health"])
def health_check() -> dict[str, str]:
    """Liveness probe (no dependencies)."""
    return {"status": "ok", "message": "ClínicaRed API running smoothly"}


@app.get("/health/ready", tags=["Health"])
def readiness_check() -> dict[str, str]:
    """Readiness probe used by the ALB target group: checks the database."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}
