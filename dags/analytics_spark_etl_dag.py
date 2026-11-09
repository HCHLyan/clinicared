"""Daily Spark ETL: Bronze (extracts + F1/F3/F5/F6/F7) -> Silver (cleansed)
-> Gold (pseudonymized star schema for Power BI).

Runtime is chosen at parse time from ``SPARK_RUNTIME``:

* ``local`` (default, docker-compose): ``SparkSubmitOperator`` against the
  ``spark_default`` connection.
* ``emr_serverless`` (AWS): ``EmrServerlessStartJobOperator``; the job role and
  application id come from environment variables managed by Terraform.

Both jobs overwrite their output partitions dynamically, so reruns are
idempotent.
"""

from __future__ import annotations

import os

from airflow.sdk import Asset, dag

from clinicared_common import DEFAULT_ARGS, START_DATE

SPARK_RUNTIME = os.getenv("SPARK_RUNTIME", "local")
LAKE_URI = os.getenv("LAKE_URI", "/opt/airflow/lake")
JOBS_URI = os.getenv("SPARK_JOBS_URI", "/opt/airflow/spark/jobs")
JDBC_URL = os.getenv("ANALYTICS_JDBC_URL", "jdbc:postgresql://postgres:5432/clinicared_db")
GOLD_DATASET = Asset(f"{LAKE_URI}/gold")


def _args(job: str) -> list[str]:
    return [
        "--run-date", "{{ data_interval_end.in_timezone('America/Bogota').to_date_string() }}",
        "--lake-uri", LAKE_URI,
        "--jdbc-url", JDBC_URL,
        "--job", job,
    ]


@dag(
    dag_id="clinicared_analytics_spark_etl",
    schedule="0 4 * * *",
    start_date=START_DATE,
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["clinicared", "us04", "us05", "us06", "spark", "rn-09"],
    doc_md=__doc__,
)
def analytics_spark_etl() -> None:
    """cleanse -> build_gold."""
    if SPARK_RUNTIME == "emr_serverless":
        from airflow.providers.amazon.aws.operators.emr import EmrServerlessStartJobOperator

        def emr(task_id: str, script: str, outlets: list[Asset] | None = None) -> EmrServerlessStartJobOperator:
            return EmrServerlessStartJobOperator(
                task_id=task_id,
                application_id=os.environ["EMR_SERVERLESS_APP_ID"],
                execution_role_arn=os.environ["EMR_SERVERLESS_JOB_ROLE_ARN"],
                job_driver={
                    "sparkSubmit": {
                        "entryPoint": f"{JOBS_URI}/{script}",
                        "entryPointArguments": _args(task_id),
                        "sparkSubmitParameters": (
                            f"--py-files {JOBS_URI}/jobs.zip "
                            "--conf spark.sql.sources.partitionOverwriteMode=dynamic "
                            "--conf spark.emr-serverless.driverEnv.PSEUDONYMIZATION_SECRET_ID=clinicared/pseudonymization-pepper "
                            "--conf spark.emr-serverless.driverEnv.ANALYTICS_DB_SECRET_ID=clinicared/etl-db"
                        ),
                    }
                },
                configuration_overrides={
                    "monitoringConfiguration": {"cloudWatchLoggingConfiguration": {"enabled": True, "logGroupName": "/clinicared/spark"}}
                },
                wait_for_completion=True,
                outlets=outlets or [],
            )

        emr("cleanse", "cleanse.py") >> emr("build_gold", "build_gold.py", [GOLD_DATASET])
    else:
        from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

        def local(task_id: str, script: str, outlets: list[Asset] | None = None) -> SparkSubmitOperator:
            return SparkSubmitOperator(
                task_id=task_id,
                conn_id="spark_default",
                application=f"{JOBS_URI}/{script}",
                # Locally the package is on PYTHONPATH; on clusters ship the zip built in CI.
                py_files=os.getenv("SPARK_JOBS_ZIP"),
                application_args=_args(task_id),
                packages="org.postgresql:postgresql:42.7.4",
                conf={"spark.sql.sources.partitionOverwriteMode": "dynamic"},
                env_vars={
                    "ANALYTICS_DB_USER": "{{ conn.clinicared_db.login }}",
                    "ANALYTICS_DB_PASSWORD": "{{ conn.clinicared_db.password }}",
                    "PSEUDONYMIZATION_PEPPER": "{{ var.value.pseudonymization_pepper }}",
                },
                outlets=outlets or [],
            )

        local("cleanse", "cleanse.py") >> local("build_gold", "build_gold.py", [GOLD_DATASET])


analytics_spark_etl()
