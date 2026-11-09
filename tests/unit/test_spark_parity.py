"""Spark transforms must reproduce the domain rules (RN-09 keys, F1 cleansing).

Skipped automatically when PySpark/Java are not available (the CI job
``spark-tests`` installs them).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "spark" / "jobs"))

from pyspark.sql import SparkSession  # noqa: E402

from clinicared_spark.schemas import F1_PATIENTS_RAW  # noqa: E402
from clinicared_spark.transforms import cleanse_patients_f1, normalize_national_id, patient_key  # noqa: E402
from src.domain.privacy import Pseudonymizer  # noqa: E402

PEPPER = "p" * 40


@pytest.fixture(scope="module")
def spark() -> SparkSession:
    session = (
        SparkSession.builder.master("local[1]")
        .appName("parity")
        .config("spark.ui.enabled", "false")
        # Same non-ANSI semantics as clinicared_spark.session.build_session (Spark 4 default is ANSI).
        .config("spark.sql.ansi.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


def test_patient_key_matches_python(spark: SparkSession) -> None:
    from pyspark.sql import functions as F

    df = spark.createDataFrame([("1.020.304-5",), ("cc 99",)], "nid string")
    keys = [r[0] for r in df.select(patient_key(normalize_national_id(F.col("nid")), PEPPER)).collect()]
    python = Pseudonymizer(PEPPER)
    assert keys == [python.patient_key("1.020.304-5"), python.patient_key("cc 99")]


def test_f1_cleansing_handles_negative_ages_and_dedup(spark: SparkSession) -> None:
    rows = [
        ("1", "100", "A", "B", "2000-01-01", "-5", "m", None, None, "Diabetes", "barranquilla", "2026-01-01 00:00:00", None),
        ("2", "100", "A", "B", "2000-01-01", "26", "M", None, None, "Ninguna", "barranquilla", "2026-06-01 00:00:00", None),
        ("3", "200", "C", "D", "2090-01-01", "300", "X", None, None, None, "soledad", "2026-01-01 00:00:00", None),
        ("4", "", "E", "F", "1990-01-01", "36", "F", None, None, None, "malambo", "2026-01-01 00:00:00", None),
    ]
    raw = spark.createDataFrame(rows, F1_PATIENTS_RAW)
    clean, quarantine = cleanse_patients_f1(raw, "2026-11-04")
    result = {r["national_id_norm"]: r for r in clean.collect()}

    assert set(result) == {"100", "200"}
    assert result["100"]["patient_id"] == 2  # latest updated_at wins
    assert result["100"]["age"] == 26 and result["100"]["has_chronic_condition"] is False
    assert result["200"]["birth_date"] is None and result["200"]["age"] is None
    assert set(result["200"]["dq_flags"].split(",")) == {"BIRTH_DATE_INVALID", "AGE_OUT_OF_RANGE"}
    assert result["200"]["sex"] is None
    assert [r["reason"] for r in quarantine.collect()] == ["MISSING_NATIONAL_ID"]
    assert "chronic_conditions" not in clean.columns
