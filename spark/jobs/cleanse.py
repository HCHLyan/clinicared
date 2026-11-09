"""Bronze -> Silver: extract operational tables and cleanse F1/F5/F6/F7.

Usage::

    spark-submit --py-files jobs.zip cleanse.py --run-date 2026-11-04 \
        --lake-uri s3://clinicared-lake --jdbc-url jdbc:postgresql://host/db
"""

from __future__ import annotations

import json
import sys
import time

from pyspark.errors import AnalysisException
from pyspark.sql import functions as F

from clinicared_spark.config import JobArgs, parse_args
from clinicared_spark.lake_io import read_jdbc_extract, read_raw_csv, write_parquet
from clinicared_spark.schemas import (
    F1_PATIENTS_RAW,
    F5_HOLIDAYS_RAW,
    F6_WEATHER_RAW,
    F7_SPECIALTY_RULES_RAW,
    JDBC_EXTRACTS,
)
from clinicared_spark.session import build_session
from clinicared_spark.transforms import apply_f7_rules, cleanse_holidays_f5, cleanse_patients_f1, cleanse_weather_f6


def run(args: JobArgs) -> dict[str, object]:
    """Execute the cleansing job and return data-quality metrics."""
    spark = build_session("clinicared-cleanse", args.mode)  # type: ignore[arg-type]
    started = time.perf_counter()
    metrics: dict[str, object] = {"run_date": args.run_date}

    if not args.skip_jdbc:
        for name in JDBC_EXTRACTS:
            df = read_jdbc_extract(spark, args.jdbc_url, name, args.window_start)
            if name == "specialties":
                try:
                    f7 = read_raw_csv(spark, args.path("landing", "f7_specialty_rules"), F7_SPECIALTY_RULES_RAW)
                    df = apply_f7_rules(df, f7)
                except AnalysisException:
                    metrics["f7_missing"] = True  # keep DB flags (already loaded from F7 by the seeder)
            partition = ["scheduled_month"] if name == "appointments" else None
            if partition:
                df = df.withColumn("scheduled_month", F.date_format("scheduled_datetime", "yyyy-MM"))
            write_parquet(df, args.path("silver", name), partition)

    f1_raw = read_raw_csv(spark, args.path("landing", f"f1_patients/dt={args.run_date}"), F1_PATIENTS_RAW).cache()
    f1_clean, f1_quarantine = cleanse_patients_f1(f1_raw, args.run_date)
    write_parquet(f1_clean, args.path("silver", "patients_f1"))
    write_parquet(f1_quarantine.withColumn("run_date_p", F.lit(args.run_date)), args.path("quarantine", "f1_patients"), ["run_date_p"])

    f1_clean_written = spark.read.parquet(args.path("silver", "patients_f1"))
    metrics["f1_read"] = f1_raw.count()
    metrics["f1_clean"] = f1_clean_written.count()
    metrics["f1_quarantined"] = spark.read.parquet(args.path("quarantine", "f1_patients")).where(
        F.col("run_date_p") == args.run_date
    ).count()
    flag_counts = (
        f1_clean_written.where(F.col("dq_flags").isNotNull())
        .select(F.explode(F.split("dq_flags", ",")).alias("flag"))
        .groupBy("flag")
        .count()
        .collect()
    )
    metrics["f1_dq_flags"] = {row["flag"]: row["count"] for row in flag_counts}
    f1_raw.unpersist()

    write_parquet(cleanse_holidays_f5(read_raw_csv(spark, args.path("landing", "f5_holidays"), F5_HOLIDAYS_RAW)), args.path("silver", "holidays"))
    write_parquet(cleanse_weather_f6(read_raw_csv(spark, args.path("landing", "f6_weather"), F6_WEATHER_RAW)), args.path("silver", "weather"))

    metrics["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    return metrics


if __name__ == "__main__":
    result = run(parse_args(sys.argv[1:]))
    print(json.dumps({"job": "cleanse", **result}, default=str))
