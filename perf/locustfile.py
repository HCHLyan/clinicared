"""API load test (US 05 T5): p95 <= 400 ms.

Run (headless, 5 min, 100 users)::

    locust -f perf/locustfile.py --host https://api.clinicared.example --headless \
        -u 100 -r 10 -t 5m --csv docs/performance/results/api_$(date +%F)

The ``*_stats.csv`` file contains the 95 % column per endpoint; copy it to
``docs/performance/benchmarks.md``. Patient ids are sampled from the seeded
range; use a non-production environment with synthetic data only.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

MAX_PATIENT_ID = int(os.getenv("PERF_MAX_PATIENT_ID", "50"))
ACTOR = {"X-Actor-Id": "perf-test"}


class FrontDeskUser(HttpUser):
    """Front-desk traffic mix: mostly reads, some confirmations."""

    wait_time = between(0.5, 2.0)

    @task(6)
    def get_patient(self) -> None:
        self.client.get(f"/patients/{random.randint(1, MAX_PATIENT_ID)}", headers=ACTOR, name="GET /patients/{id}")  # noqa: S311

    @task(3)
    def list_patients(self) -> None:
        self.client.get("/patients/?limit=20", headers=ACTOR, name="GET /patients")

    @task(1)
    def readiness(self) -> None:
        self.client.get("/health/ready", name="GET /health/ready")
