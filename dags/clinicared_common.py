"""Shared helpers for ClínicaRed DAGs.

Kept free of heavy top-level work so DAG parsing stays fast (the scheduler
re-parses files every ``min_file_process_interval``). Business logic lives
in ``src.services`` and ``src.domain``; DAGs only orchestrate.
"""

from __future__ import annotations

import fnmatch
import glob
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import pendulum

LOCAL_TZ = "America/Bogota"
POSTGRES_CONN_ID = os.getenv("CLINICARED_POSTGRES_CONN_ID", "clinicared_db")
RABBITMQ_CONN_ID = os.getenv("CLINICARED_RABBITMQ_CONN_ID", "clinicared_rabbitmq")
START_DATE = pendulum.datetime(2026, 11, 1, tz=LOCAL_TZ)

logger = logging.getLogger(__name__)


def notify_failure(context: dict[str, Any]) -> None:
    """``on_failure_callback``: structured log + SNS alert when configured."""
    ti = context.get("task_instance")
    message = (
        f"[ClínicaRed] DAG {ti.dag_id if ti else '?'} task {ti.task_id if ti else '?'} failed "
        f"(run {context.get('run_id')}): {context.get('exception')}"
    )
    logger.error(message)
    topic_arn = os.getenv("ALERTS_SNS_TOPIC_ARN")
    if topic_arn:
        from airflow.providers.amazon.aws.hooks.sns import SnsHook

        SnsHook().publish_to_target(target_arn=topic_arn, message=message, subject="Airflow task failure")


DEFAULT_ARGS: dict[str, Any] = {
    "owner": "data-platform",
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
    "on_failure_callback": notify_failure,
}


def to_local_naive(value: pendulum.DateTime) -> datetime:
    """Convert an Airflow (UTC-aware) datetime to clinic-local naive time."""
    return value.in_timezone(LOCAL_TZ).naive()


def local_now() -> datetime:
    """Wall-clock local time (for sending decisions, not for logical dates)."""
    return pendulum.now(LOCAL_TZ).naive()


def get_engine() -> Any:
    """SQLAlchemy engine from the Airflow connection (credentials in the secrets backend)."""
    from airflow.providers.postgres.hooks.postgres import PostgresHook

    return PostgresHook(postgres_conn_id=POSTGRES_CONN_ID).get_sqlalchemy_engine()


def get_publisher() -> Any:
    """Reliable RabbitMQ publisher built from the Airflow connection URI."""
    from airflow.sdk import BaseHook

    from src.messaging.connection import connection_parameters
    from src.messaging.publisher import ReliablePublisher

    conn = BaseHook.get_connection(RABBITMQ_CONN_ID)
    scheme = "amqps" if (conn.extra_dejson or {}).get("ssl") else "amqp"
    vhost = conn.schema or "%2F"
    url = f"{scheme}://{conn.login}:{conn.password}@{conn.host}:{conn.port or 5672}/{vhost}"
    return ReliablePublisher(connection_parameters(url, client_name="airflow"))


def get_calendar(engine: Any) -> Any:
    """RN-04 calendar with F5 holidays from the database."""
    from src.domain.messaging_calendar import MessagingCalendar
    from src.services.calendar_provider import load_holidays

    return MessagingCalendar(load_holidays(engine))


def read_landing_files(base_uri: str, pattern: str) -> list[tuple[str, str]]:
    """Read UTF-8 files under ``base_uri`` (``s3://`` or local path) matching ``pattern``.

    Returns:
        ``[(uri, content), ...]`` sorted by uri (deterministic processing order).
    """
    if base_uri.startswith("s3://"):
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        bucket, _, prefix = base_uri.removeprefix("s3://").partition("/")
        hook = S3Hook()
        keys = hook.list_keys(bucket_name=bucket, prefix=prefix) or []
        selected = sorted(k for k in keys if fnmatch.fnmatch(k, f"{prefix.rstrip('/')}/{pattern}"))
        return [(f"s3://{bucket}/{key}", hook.read_key(key, bucket_name=bucket)) for key in selected]
    files = sorted(glob.glob(os.path.join(base_uri, pattern)))
    result = []
    for path in files:
        with open(path, encoding="utf-8") as handle:
            result.append((path, handle.read()))
    return result
