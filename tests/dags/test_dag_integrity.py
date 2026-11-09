"""DAG validation (US 05): every DAG imports, has no cycles, and follows the
team conventions. Runs in the CI ``dag-validation`` job where Airflow is
installed; skipped elsewhere."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("airflow")

from airflow.models import DagBag  # noqa: E402

DAGS_FOLDER = Path(__file__).resolve().parents[2] / "dags"
EXPECTED = {
    "clinicared_admissions_consolidation",
    "clinicared_risk_calculation",
    "clinicared_reminder_orchestration",
    "clinicared_analytics_spark_etl",
}


@pytest.fixture(scope="module")
def dagbag() -> DagBag:
    return DagBag(dag_folder=str(DAGS_FOLDER), include_examples=False)


def test_no_import_errors(dagbag: DagBag) -> None:
    assert dagbag.import_errors == {}


def test_expected_dags_present(dagbag: DagBag) -> None:
    assert EXPECTED <= set(dagbag.dag_ids)


@pytest.mark.parametrize("dag_id", sorted(EXPECTED))
def test_conventions(dagbag: DagBag, dag_id: str) -> None:
    dag = dagbag.get_dag(dag_id)
    assert dag.catchup is False
    assert dag.max_active_runs == 1
    assert "clinicared" in dag.tags
    for task in dag.tasks:
        assert task.retries >= 1, f"{dag_id}.{task.task_id} must retry"
        assert task.on_failure_callback is not None, f"{dag_id}.{task.task_id} must alert on failure"


def test_risk_dag_waits_for_admissions(dagbag: DagBag) -> None:
    dag = dagbag.get_dag("clinicared_risk_calculation")
    compute = dag.get_task("compute_risk_and_plan")
    assert "wait_admissions_consolidation" in compute.upstream_task_ids
