"""Daily RN-02 risk calculation for the next 7 days, RN-03/04/11 reminder
planning and RN-05 overbooking capacity.

Schedule: 03:30 America/Bogota, after the admissions DAG. The sensor is
``soft_fail``: if consolidation is late, risk still runs on the previous
day's outcomes rather than leaving patients without reminders.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date, timedelta
from typing import Any

from airflow.providers.standard.sensors.external_task import ExternalTaskSensor
from airflow.sdk import dag, task

from clinicared_common import DEFAULT_ARGS, START_DATE, get_calendar, get_engine, read_landing_files, to_local_naive

F5_LANDING_URI = os.getenv("F5_LANDING_URI", "/opt/airflow/landing/holidays")
F6_LANDING_URI = os.getenv("F6_LANDING_URI", "/opt/airflow/landing/weather")
OVERBOOK_BLOCK_MINUTES = int(os.getenv("OVERBOOK_BLOCK_MINUTES", "60"))
SEVERE_PRECIPITATION_MM = 30.0


@dag(
    dag_id="clinicared_risk_calculation",
    schedule="30 3 * * *",
    start_date=START_DATE,
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["clinicared", "us04", "rn-02", "rn-03", "rn-05"],
    doc_md=__doc__,
)
def risk_calculation() -> None:
    """F5 + F6 refresh, then risk, reminder planning and overbooking."""

    wait_admissions = ExternalTaskSensor(
        task_id="wait_admissions_consolidation",
        external_dag_id="clinicared_admissions_consolidation",
        external_task_id="evaluate_rn01",
        execution_delta=timedelta(hours=1, minutes=30),
        mode="reschedule",
        poke_interval=300,
        timeout=2 * 3600,
        soft_fail=True,
    )

    @task
    def load_holidays_f5() -> int:
        """Upsert F5 (``holiday_date,name``); statutory fallback lives in the domain."""
        from sqlalchemy import text

        rows: list[dict[str, Any]] = []
        for _uri, content in read_landing_files(F5_LANDING_URI, "*.csv"):
            for raw in csv.DictReader(io.StringIO(content)):
                rows.append({"d": date.fromisoformat(raw["holiday_date"].strip()), "n": raw["name"].strip()[:120]})
        if rows:
            with get_engine().begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO holidays (holiday_date, name) VALUES (:d, :n)
                        ON CONFLICT (holiday_date) DO UPDATE SET name = EXCLUDED.name, loaded_at = CURRENT_TIMESTAMP
                        """
                    ),
                    rows,
                )
        return len(rows)

    @task
    def load_weather_f6(data_interval_end: Any = None) -> int:
        """Upsert the F6 forecast (``city,weather_date,precipitation_mm,max_temp_c,condition``).

        Outliers (negative precipitation, temperatures outside [-5, 45] °C) are
        nulled, never propagated.
        """
        from sqlalchemy import text

        run_day = to_local_naive(data_interval_end).date()
        rows: list[dict[str, Any]] = []
        for _uri, content in read_landing_files(F6_LANDING_URI, f"dt={run_day.isoformat()}/*.csv"):
            for raw in csv.DictReader(io.StringIO(content)):
                precipitation = _to_float(raw.get("precipitation_mm"))
                if precipitation is not None and precipitation < 0:
                    precipitation = None
                temperature = _to_float(raw.get("max_temp_c"))
                if temperature is not None and not -5 <= temperature <= 45:
                    temperature = None
                condition = (raw.get("condition") or "").strip().upper()[:40] or None
                rows.append(
                    {
                        "city": raw["city"].strip(),
                        "d": date.fromisoformat(raw["weather_date"].strip()),
                        "p": precipitation,
                        "t": temperature,
                        "c": condition,
                        "s": (precipitation or 0) >= SEVERE_PRECIPITATION_MM or condition in {"STORM", "TORMENTA"},
                    }
                )
        if rows:
            with get_engine().begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO weather_daily (city, weather_date, precipitation_mm, max_temp_c, condition, is_severe)
                        VALUES (:city, :d, :p, :t, :c, :s)
                        ON CONFLICT (city, weather_date) DO UPDATE SET
                            precipitation_mm = EXCLUDED.precipitation_mm, max_temp_c = EXCLUDED.max_temp_c,
                            condition = EXCLUDED.condition, is_severe = EXCLUDED.is_severe, loaded_at = CURRENT_TIMESTAMP
                        """
                    ),
                    rows,
                )
        return len(rows)

    @task(trigger_rule="none_failed")
    def compute_risk_and_plan(data_interval_end: Any = None, run_id: str | None = None) -> dict[str, Any]:
        from dataclasses import asdict

        from src.services.risk_service import run_risk_and_planning

        engine = get_engine()
        report = run_risk_and_planning(
            engine,
            as_of=to_local_naive(data_interval_end),
            run_id=run_id or "manual",
            calendar=get_calendar(engine),
            block_minutes=OVERBOOK_BLOCK_MINUTES,
        )
        return asdict(report)

    [wait_admissions, load_holidays_f5(), load_weather_f6()] >> compute_risk_and_plan()


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


risk_calculation()
