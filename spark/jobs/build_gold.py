"""Silver -> Gold: pseudonymized star schema (US 06 T1) for Power BI.

Optimizations applied in ``optimized`` mode (US 05 T4), each measured by
``spark/benchmarks/run_benchmark.py``:

1. Broadcast joins for dimensions (locations, specialties, weather, patients
   key map, ~400k rows/~30 MB) -> no shuffle of the large fact side.
2. ``fact_appointment`` is cached once and reused by the occupancy aggregate
   and the date dimension instead of being recomputed three times.
3. Facts are repartitioned by ``year_month`` before a partitioned Parquet
   write: one file set per month, partition pruning for Power BI/Athena.
4. AQE coalesces small shuffle partitions and splits skewed ones.

``baseline`` writes unpartitioned CSV without caching or broadcast (the
original job) so the gain can be measured on the same input.
"""

from __future__ import annotations

import json
import sys
import time

from pyspark import StorageLevel
from pyspark.sql import DataFrame, SparkSession

from clinicared_spark.config import JobArgs, parse_args, resolve_pepper
from clinicared_spark.lake_io import write_parquet
from clinicared_spark.session import build_session
from clinicared_spark.transforms import (
    appointment_outcome,
    build_agg_occupancy_daily,
    build_dim_date,
    build_dim_patient,
    build_fact_admission,
    build_fact_appointment,
    build_fact_reminder,
)

FACT_PARTITION = "year_month"


def _write(df: DataFrame, path: str, optimized: bool, partitioned: bool) -> None:
    if not optimized:
        df.write.mode("overwrite").option("header", "true").csv(path)
        return
    if partitioned:
        write_parquet(df.repartition(FACT_PARTITION), path, [FACT_PARTITION])
    else:
        write_parquet(df.coalesce(1), path)


def run(spark: SparkSession, args: JobArgs, pepper: str) -> dict[str, float]:
    """Build every Gold table; returns per-table timings (seconds)."""
    optimized = args.mode == "optimized"
    silver = lambda name: spark.read.parquet(args.path("silver", name))  # noqa: E731
    timings: dict[str, float] = {}

    appointments = silver("appointments")
    attendances = silver("attendances")
    messages = silver("messages")
    risk = silver("risk_scores")
    admissions = silver("admissions")
    locations = silver("locations")
    specialties = silver("specialties")
    professionals = silver("professionals")
    capacity = silver("agenda_capacity")
    holidays = silver("holidays")
    weather = silver("weather")

    def timed(name: str, df: DataFrame, partitioned: bool) -> None:
        start = time.perf_counter()
        _write(df, args.path("gold", name), optimized, partitioned)
        timings[name] = round(time.perf_counter() - start, 3)

    dim_patient = build_dim_patient(silver("patients_db"), silver("patients_f1"), pepper, args.run_date, optimized)
    if optimized:
        dim_patient = dim_patient.persist(StorageLevel.MEMORY_AND_DISK)
    # patient_id is an internal surrogate needed for keying; it is not published.
    timed("dim_patient", dim_patient.drop("patient_id"), partitioned=False)
    timed("dim_branch", locations, partitioned=False)
    timed("dim_specialty", specialties, partitioned=False)
    timed("dim_professional", professionals, partitioned=False)

    with_outcome = appointment_outcome(appointments, attendances)
    fact_appointment = build_fact_appointment(with_outcome, risk, messages, dim_patient, locations, weather, optimized)
    if optimized:
        fact_appointment = fact_appointment.persist(StorageLevel.MEMORY_AND_DISK)
    timed("fact_appointment", fact_appointment, partitioned=True)
    timed("fact_reminder", build_fact_reminder(messages, appointments, dim_patient, optimized), partitioned=True)
    timed("fact_admission", build_fact_admission(admissions, appointments, dim_patient, optimized), partitioned=True)
    timed("agg_occupancy_daily", build_agg_occupancy_daily(fact_appointment, capacity), partitioned=True)

    from pyspark.sql import functions as F

    dates = fact_appointment.select(F.to_date(F.col("date_key").cast("string"), "yyyyMMdd").alias("d"))
    timed("dim_date", build_dim_date(dates, holidays, "d"), partitioned=False)

    if optimized:
        fact_appointment.unpersist()
        dim_patient.unpersist()
    return timings


def main(argv: list[str]) -> None:
    """CLI entrypoint."""
    args = parse_args(argv)
    spark = build_session("clinicared-build-gold", args.mode)  # type: ignore[arg-type]
    started = time.perf_counter()
    timings = run(spark, args, resolve_pepper())
    print(json.dumps({"job": "build_gold", "mode": args.mode, "total_seconds": round(time.perf_counter() - started, 3), "tables": timings}))


if __name__ == "__main__":
    main(sys.argv[1:])
