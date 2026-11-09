"""CA-10 (RN-09, RN-10): pseudonymization/minimization and fail-closed audit."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import AuditMiddleware
from src.domain.privacy import Pseudonymizer, age_band, has_chronic_condition, normalize_national_id

PEPPER = "x" * 40


def test_pseudonym_is_deterministic_and_keyed() -> None:
    a = Pseudonymizer(PEPPER)
    b = Pseudonymizer("y" * 40)
    assert a.patient_key("1.020.304-5") == a.patient_key("10203045")
    assert a.patient_key("10203045") != b.patient_key("10203045")
    assert len(a.patient_key("10203045")) == 64
    assert "10203045" not in a.patient_key("10203045")


def test_short_pepper_and_empty_id_rejected() -> None:
    with pytest.raises(ValueError):
        Pseudonymizer("short")
    with pytest.raises(ValueError):
        Pseudonymizer(PEPPER).patient_key(" - ")


def test_normalization() -> None:
    assert normalize_national_id("cc 1.020-33") == "CC102033"


@pytest.mark.parametrize(("age", "band"), [(None, "UNKNOWN"), (-3, "UNKNOWN"), (0, "0-17"), (17, "0-17"), (18, "18-29"), (74, "60-74"), (75, "75+")])
def test_age_bands(age: int | None, band: str) -> None:
    assert age_band(age) == band


@pytest.mark.parametrize(("raw", "flag"), [(None, False), ("", False), ("Ninguna", False), ("N/A", False), ("Diabetes tipo 2", True)])
def test_chronic_conditions_reduced_to_flag(raw: str | None, flag: bool) -> None:
    assert has_chronic_condition(raw) is flag


# ------------------------------------------------------------------ RN-10
class RecordingEngine:
    """Minimal stand-in for ``Engine.begin()``."""

    def __init__(self, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = fail

    @contextmanager
    def begin(self):  # noqa: ANN201
        if self.fail:
            raise RuntimeError("database down")
        outer = self

        class _Conn:
            def execute(self, _sql: Any, params: dict[str, Any]) -> None:
                outer.rows.append(params)

        yield _Conn()


def _app(engine: RecordingEngine) -> TestClient:
    app = FastAPI()
    app.add_middleware(AuditMiddleware, engine=engine)

    @app.get("/patients/{patient_id}")
    def read(patient_id: int) -> dict[str, int]:
        return {"id": patient_id}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def test_patient_access_is_audited_with_actor() -> None:
    engine = RecordingEngine()
    response = _app(engine).get("/patients/42", headers={"X-Actor-Id": "dr.perez"})
    assert response.status_code == 200
    assert engine.rows[0]["actor"] == "dr.perez"
    assert (engine.rows[0]["action"], engine.rows[0]["resource_type"], engine.rows[0]["resource_id"]) == ("READ", "patients", "42")


def test_non_patient_routes_are_not_audited() -> None:
    engine = RecordingEngine()
    _app(engine).get("/health")
    assert engine.rows == []


def test_audit_failure_blocks_the_response() -> None:
    response = _app(RecordingEngine(fail=True)).get("/patients/42")
    assert response.status_code == 503
    assert "id" not in response.json()
