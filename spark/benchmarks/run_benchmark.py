"""Benchmark build_gold: baseline vs optimized on the same synthetic input.

Generates a deterministic synthetic Silver zone at production scale
(400k patients, 22 branches, configurable appointments), then runs
``build_gold`` in both modes ``--runs`` times and writes a JSON report.

Usage::

    PYTHONPATH=spark/jobs spark-submit spark/benchmarks/run_benchmark.py \
        --lake-uri /tmp/clinicared-bench --appointments 2500000 --runs 3 \
        --output docs/performance/results/spark_benchmark.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from datetime import date

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from build_gold import run as build_gold_run
from clinicared_spark.config import JobArgs
from clinicared_spark.session import apply_mode, build_session

SEED = 42
BRANCHES = 22
SPECIALTIES = 18
PROFESSIONALS = 900


def _write(df: DataFrame, path: str) -> None:
    df.write.mode("overwrite").parquet(path)


def generate_silver(spark: SparkSession, lake: str, patients: int, appointments: int) -> None:
    """Create deterministic synthetic silver tables."""
    silver = f"{lake}/silver"
    p = spark.range(1, patients + 1).withColumnRenamed("id", "patient_id")
    _write(
        p.select(
            F.col("patient_id").cast("int"),
            F.format_string("%010d", F.col("patient_id")).alias("national_id"),
            F.expr("date_sub(date'2026-11-04', cast(rand(1) * 30000 as int))").alias("date_of_birth"),
            F.when(F.rand(2) < 0.5, "F").otherwise("M").alias("sex"),
        ),
        f"{silver}/patients_db",
    )
    _write(
        p.select(
            F.col("patient_id").cast("int"),
            F.format_string("%010d", F.col("patient_id")).alias("national_id_norm"),
            F.lit(None).cast("date").alias("birth_date"),
            (F.rand(3) * 90).cast("int").alias("age"),
            F.lit(None).cast("string").alias("sex"),
            F.element_at(F.array(*[F.lit(c) for c in ("Barranquilla", "Malambo", "Soledad", "Cartagena")]), (F.rand(4) * 4 + 1).cast("int")).alias("city"),
            (F.rand(5) < 0.22).alias("has_chronic_condition"),
            F.lit(None).cast("string").alias("dq_flags"),
            F.current_timestamp().alias("updated_at"),
        ),
        f"{silver}/patients_f1",
    )
    a = spark.range(1, appointments + 1).select(
        F.col("id").cast("int"),
        (F.rand(6) * patients + 1).cast("int").alias("patient_id"),
        (F.rand(7) * PROFESSIONALS + 1).cast("int").alias("professional_id"),
        (F.rand(8) * BRANCHES + 1).cast("int").alias("location_id"),
        (F.rand(9) * SPECIALTIES + 1).cast("int").alias("specialty_id"),
        F.expr("timestamp'2025-10-01 07:00:00' + make_interval(0,0,0, cast(rand(10)*400 as int), cast(rand(11)*11 as int), cast(rand(12)*3 as int)*20, 0)").alias("scheduled_datetime"),
        F.when(F.rand(13) < 0.08, "CANCELLED").otherwise("ATTENDED").alias("status"),
        F.lit(None).cast("timestamp").alias("created_at"),
        F.lit(None).cast("timestamp").alias("confirmed_at"),
        F.lit(None).cast("timestamp").alias("cancelled_at"),
        (F.rand(14) < 0.02).alias("is_overbook"),
        F.lit("SELF_SERVICE").alias("booking_channel"),
    ).withColumn("created_at", F.expr("scheduled_datetime - make_interval(0,0,0, cast(rand(15)*30 as int), 0, 0, 0)"))
    _write(a.withColumn("scheduled_month", F.date_format("scheduled_datetime", "yyyy-MM")), f"{silver}/appointments")
    _write(
        a.select(
            F.col("id").alias("appointment_id"),
            F.when(F.rand(16) < 0.18, "NO_SHOW").otherwise("ATTENDED").alias("status"),
            F.col("scheduled_datetime").alias("check_in_time"),
        ),
        f"{silver}/attendances",
    )
    _write(
        a.select(
            F.col("id").alias("appointment_id"),
            F.element_at(F.array(F.lit("HIGH"), F.lit("MEDIUM"), F.lit("LOW")), (F.rand(17) * 3 + 1).cast("int")).alias("risk_level"),
            (F.rand(18) * 30).cast("int").alias("lead_time_days"),
        ),
        f"{silver}/risk_scores",
    )
    _write(
        a.where(F.rand(19) < 0.6).select(
            F.col("id").alias("id"),
            F.col("id").alias("appointment_id"),
            F.lit("H24").alias("message_type"),
            F.lit("DELIVERED").alias("status"),
            F.lit("SMS").alias("channel"),
            F.lit("PATIENT").alias("recipient_type"),
            F.lit("MEDIUM").alias("risk_level"),
            F.expr("scheduled_datetime - interval 24 hours").alias("send_at"),
            F.expr("scheduled_datetime - interval 24 hours").alias("sent_at"),
            F.lit(45).cast("decimal(10,2)").alias("cost_cop"),
        ),
        f"{silver}/messages",
    )
    _write(
        a.where(F.rand(20) < 0.8).select(
            F.concat(F.lit("ADM-"), F.col("id")).alias("admission_id"),
            F.col("id").alias("appointment_id"),
            F.expr("scheduled_datetime + make_interval(0,0,0,0,0, cast(rand(21)*40 as int) - 10, 0)").alias("admitted_at"),
        ),
        f"{silver}/admissions",
    )
    cities = ["Barranquilla", "Malambo", "Soledad", "Cartagena"]
    _write(
        spark.range(1, BRANCHES + 1).select(
            F.col("id").cast("int"),
            F.concat(F.lit("Sede "), F.col("id")).alias("name"),
            F.element_at(F.array(*[F.lit(c) for c in cities]), (F.col("id") % 4 + 1).cast("int")).alias("city"),
            F.concat(F.lit("S"), F.col("id")).alias("code"),
        ),
        f"{silver}/locations",
    )
    _write(
        spark.range(1, SPECIALTIES + 1).select(
            F.col("id").cast("int"), F.concat(F.lit("Esp "), F.col("id")).alias("name"), (F.col("id") % 5 != 0).alias("is_overbookable")
        ),
        f"{silver}/specialties",
    )
    _write(spark.range(1, PROFESSIONALS + 1).select(F.col("id").cast("int"), F.lit(True).alias("is_active")), f"{silver}/professionals")
    _write(
        spark.range(0, 400).crossJoin(spark.range(1, PROFESSIONALS + 1).withColumnRenamed("id", "professional_id")).select(
            F.col("professional_id").cast("int"),
            (F.col("professional_id") % BRANCHES + 1).cast("int").alias("location_id"),
            (F.col("professional_id") % SPECIALTIES + 1).cast("int").alias("specialty_id"),
            F.expr("date_add(date'2025-10-01', cast(id as int))").alias("agenda_date"),
            F.lit(18).alias("regular_slots"),
        ),
        f"{silver}/agenda_capacity",
    )
    _write(spark.createDataFrame([(date(2026, 1, 1), "Año Nuevo")], "holiday_date date, name string"), f"{silver}/holidays")
    _write(
        spark.range(0, 400).crossJoin(spark.createDataFrame([(c,) for c in cities], "city string")).select(
            "city",
            F.expr("date_add(date'2025-10-01', cast(id as int))").alias("weather_date"),
            (F.rand(22) * 50).cast("decimal(6,1)").alias("precipitation_mm"),
            (F.rand(23) * 10 + 25).cast("decimal(4,1)").alias("max_temp_c"),
            F.lit("RAIN").alias("condition"),
            (F.rand(24) < 0.05).alias("is_severe"),
        ),
        f"{silver}/weather",
    )


def main() -> None:
    """Run the benchmark and write the JSON report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake-uri", required=True)
    parser.add_argument("--patients", type=int, default=400_000)
    parser.add_argument("--appointments", type=int, default=2_500_000)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--output", default="spark_benchmark.json")
    ns = parser.parse_args()

    spark = build_session("clinicared-benchmark", "optimized")
    if not ns.skip_generate:
        generate_silver(spark, ns.lake_uri, ns.patients, ns.appointments)
    pepper = os.getenv("PSEUDONYMIZATION_PEPPER", "benchmark-only-pepper-not-a-secret-000000")

    report: dict[str, object] = {
        "date": date.today().isoformat(),
        "spark_version": spark.version,
        "python": platform.python_version(),
        "host_cpus": os.cpu_count(),
        "patients": ns.patients,
        "appointments": ns.appointments,
        "results": {},
    }
    for mode in ("baseline", "optimized"):
        apply_mode(spark, mode)  # type: ignore[arg-type]
        totals: list[float] = []
        for _ in range(ns.runs):
            spark.catalog.clearCache()
            started = time.perf_counter()
            tables = build_gold_run(spark, JobArgs("2026-11-04", ns.lake_uri, "", mode, True), pepper)
            totals.append(round(time.perf_counter() - started, 3))
        report["results"][mode] = {  # type: ignore[index]
            "runs_seconds": totals,
            "median_seconds": statistics.median(totals),
            "last_run_tables": tables,
        }
    base = report["results"]["baseline"]["median_seconds"]  # type: ignore[index]
    opt = report["results"]["optimized"]["median_seconds"]  # type: ignore[index]
    report["speedup"] = round(base / opt, 2) if opt else None

    with open(ns.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
