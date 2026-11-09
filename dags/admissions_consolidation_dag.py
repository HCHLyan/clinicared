"""Daily consolidation of F3 admissions and RN-01 no-show evaluation.

Schedule: 02:00 America/Bogota. Processes the admissions of the data
interval's day, re-evaluates the previous 3 days (late files) and publishes
the ``attendances`` dataset consumed by the risk DAG.

Idempotency: F3 rows are upserted by ``admission_id``; RN-01 is a
deterministic function of (appointment, admissions, cancellation), so
reruns and backfills converge to the same state.
"""

from __future__ import annotations

import os
from typing import Any

from airflow.sdk import Asset, dag, task
from airflow.sdk.exceptions import AirflowSkipException

from clinicared_common import DEFAULT_ARGS, START_DATE, get_engine, read_landing_files, to_local_naive

F3_LANDING_URI = os.getenv("F3_LANDING_URI", "/opt/airflow/landing/admissions")
# Airflow 3 validates postgres asset URIs as host:port/database/schema/table.
ATTENDANCES_DATASET = Asset("postgres://postgres:5432/clinicared_db/public/attendances")


@dag(
    dag_id="clinicared_admissions_consolidation",
    schedule="0 2 * * *",
    start_date=START_DATE,
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["clinicared", "us04", "f3", "rn-01"],
    doc_md=__doc__,
)
def admissions_consolidation() -> None:
    """Load F3, then evaluate RN-01."""

    @task
    def load_f3(data_interval_start: Any = None) -> dict[str, int]:
        from src.services.admissions_service import load_admissions, parse_admissions_csv

        target = to_local_naive(data_interval_start).date()
        files = read_landing_files(F3_LANDING_URI, f"dt={target.isoformat()}/*.csv")
        if not files:
            raise AirflowSkipException(f"No F3 files for {target}")
        engine = get_engine()
        totals = {"files": len(files), "loaded": 0, "rejected": 0}
        for uri, content in files:
            rows, rejected = parse_admissions_csv(content)
            totals["loaded"] += load_admissions(engine, rows, uri)
            totals["rejected"] += rejected
        if totals["loaded"] and totals["rejected"] / (totals["loaded"] + totals["rejected"]) > 0.05:
            raise ValueError(f"F3 rejection rate above 5%: {totals}")
        return totals

    @task(outlets=[ATTENDANCES_DATASET], trigger_rule="none_failed")
    def evaluate_rn01(data_interval_start: Any = None, data_interval_end: Any = None) -> dict[str, int]:
        from src.services.admissions_service import evaluate_no_shows

        return evaluate_no_shows(
            get_engine(),
            target_date=to_local_naive(data_interval_start).date(),
            as_of=to_local_naive(data_interval_end),
        )

    load_f3() >> evaluate_rn01()


admissions_consolidation()
