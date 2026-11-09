"""SparkSession factory with the two benchmarked configurations (US 05 T4)."""

from __future__ import annotations

from typing import Literal

from pyspark.sql import SparkSession

Mode = Literal["baseline", "optimized"]

_MODE_CONF: dict[str, dict[str, str]] = {
    # Pre-optimization job: no AQE, no automatic broadcast, default 200 partitions.
    "baseline": {
        "spark.sql.adaptive.enabled": "false",
        "spark.sql.autoBroadcastJoinThreshold": "-1",
        "spark.sql.shuffle.partitions": "200",
    },
    # AQE (coalescing + skew join), broadcast of small dims, partitions sized
    # for ~2-3 M appointments/year.
    "optimized": {
        "spark.sql.adaptive.enabled": "true",
        "spark.sql.adaptive.coalescePartitions.enabled": "true",
        "spark.sql.adaptive.skewJoin.enabled": "true",
        "spark.sql.autoBroadcastJoinThreshold": str(32 * 1024 * 1024),
        "spark.sql.shuffle.partitions": "64",
        "spark.sql.files.maxPartitionBytes": str(128 * 1024 * 1024),
    },
}


def apply_mode(spark: SparkSession, mode: Mode) -> None:
    """Switch runtime SQL configuration between profiles (used by benchmarks)."""
    for key, value in _MODE_CONF[mode].items():
        spark.conf.set(key, value)


def build_session(app_name: str, mode: Mode = "optimized") -> SparkSession:
    """Create (or reuse) a SparkSession configured for ``mode``.

    Args:
        app_name: Spark application name.
        mode: Configuration profile.

    Returns:
        The SparkSession.
    """
    spark = (
        SparkSession.builder.appName(app_name)
        # Spark 4 enables ANSI mode by default; cleansing relies on invalid casts and
        # unparseable dates yielding NULL (Spark 3.x semantics), so keep it off.
        .config("spark.sql.ansi.enabled", "false")
        .config("spark.sql.session.timeZone", "America/Bogota")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .getOrCreate()
    )
    apply_mode(spark, mode)
    return spark
