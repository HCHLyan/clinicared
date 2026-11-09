"""Reminder orchestration every 10 minutes.

1. Recover messages stuck in ENQUEUED/SENDING (lost broker messages, dead workers).
2. Publish due PLANNED messages to RabbitMQ (priority by risk).
3. Expire lapsed waitlist offers and re-offer the slot (RN-06).

Airflow itself runs on the CeleryExecutor with RabbitMQ as Celery broker
(see docker-compose.yml); patient messages use a *separate* vhost and
exchanges so Airflow load never competes with dispatch traffic.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from airflow.sdk import dag, task

from clinicared_common import DEFAULT_ARGS, START_DATE, get_calendar, get_engine, get_publisher, local_now


@dag(
    dag_id="clinicared_reminder_orchestration",
    schedule="*/10 * * * *",
    start_date=START_DATE,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=9),
    default_args={**DEFAULT_ARGS, "retries": 1, "retry_delay": timedelta(minutes=1)},
    tags=["clinicared", "us04", "rn-03", "rn-04", "rn-06"],
    doc_md=__doc__,
)
def reminder_orchestration() -> None:
    """Recover -> enqueue; waitlist sweeper in parallel."""

    @task
    def recover_stuck() -> dict[str, int]:
        from src.services.orchestration_service import recover_stuck_messages

        return recover_stuck_messages(get_engine(), local_now())

    @task
    def enqueue_due() -> int:
        from src.services.orchestration_service import enqueue_due_messages

        publisher = get_publisher()
        try:
            return enqueue_due_messages(get_engine(), publisher, local_now())
        finally:
            publisher.close()

    @task
    def waitlist_sweeper() -> dict[str, Any]:
        from src.services.waitlist_service import expire_and_reoffer

        engine = get_engine()
        return expire_and_reoffer(engine, local_now(), get_calendar(engine))

    recover_stuck() >> enqueue_due()
    waitlist_sweeper()


reminder_orchestration()
