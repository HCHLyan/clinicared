"""Readers and writers for the lake and PostgreSQL."""

from __future__ import annotations

import os

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StructType

from clinicared_spark.schemas import CORRUPT_COLUMN, JDBC_EXTRACTS, JDBC_PARTITION_COLUMN


def read_raw_csv(spark: SparkSession, path: str, schema: StructType) -> DataFrame:
    """Read CSV with an explicit raw schema; malformed lines land in ``_corrupt_record``."""
    return (
        spark.read.option("header", "true")
        .option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", CORRUPT_COLUMN)
        .option("encoding", "UTF-8")
        .schema(schema)
        .csv(path)
    )


def _db_credentials() -> tuple[str, str]:
    """``(user, password)`` from Secrets Manager (AWS) or env vars (local)."""
    secret_id = os.getenv("ANALYTICS_DB_SECRET_ID")
    if secret_id:
        import json

        import boto3

        secret = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"])
        return secret["username"], secret["password"]
    return os.environ["ANALYTICS_DB_USER"], os.environ["ANALYTICS_DB_PASSWORD"]


def jdbc_options(jdbc_url: str) -> dict[str, str]:
    """JDBC options for the column-restricted ``clinicared_etl`` role."""
    user, password = _db_credentials()
    return {
        "url": jdbc_url,
        "user": user,
        "password": password,
        "driver": "org.postgresql.Driver",
        "fetchsize": "10000",
    }


def read_jdbc_extract(spark: SparkSession, jdbc_url: str, name: str, window_start: str, num_partitions: int = 8) -> DataFrame:
    """Extract one table with its explicit schema.

    Large tables are read in parallel by ``id`` ranges; ``dbtable`` is used
    with a sub-query because Spark forbids ``query`` + ``partitionColumn``.
    """
    sql, schema = JDBC_EXTRACTS[name]
    subquery = f"({sql.format(window_start=window_start)}) AS src"
    options = jdbc_options(jdbc_url)
    partition_column = JDBC_PARTITION_COLUMN.get(name)
    if partition_column:
        bounds = (
            spark.read.format("jdbc")
            .options(**options)
            .option("dbtable", f"(SELECT min({partition_column}) AS lo, max({partition_column}) AS hi FROM {subquery}) AS b")
            .load()
            .first()
        )
        if bounds is not None and bounds["lo"] is not None:
            options.update(
                {
                    "partitionColumn": partition_column,
                    "lowerBound": str(bounds["lo"]),
                    "upperBound": str(bounds["hi"] + 1),
                    "numPartitions": str(num_partitions),
                }
            )
    df = spark.read.format("jdbc").options(**options).option("dbtable", subquery).load()
    return df.select([F.col(field.name).cast(field.dataType).alias(field.name) for field in schema.fields])


def write_parquet(df: DataFrame, path: str, partition_by: list[str] | None = None) -> None:
    """Overwrite (dynamic partition overwrite when partitioned => idempotent)."""
    writer = df.write.mode("overwrite").format("parquet")
    if partition_by:
        writer = writer.partitionBy(*partition_by)
    writer.save(path)
