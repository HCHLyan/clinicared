"""Job arguments, lake paths and secret resolution."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta

#: Facts are re-extracted for this rolling window (13+ months) and their
#: partitions overwritten, which keeps the Parquet lake idempotent without a
#: MERGE-capable table format (see ADR-008).
EXTRACT_WINDOW_DAYS = 400
MIN_PEPPER_LENGTH = 32


@dataclass(frozen=True)
class JobArgs:
    """Common CLI arguments."""

    run_date: str
    lake_uri: str
    jdbc_url: str
    mode: str
    skip_jdbc: bool

    @property
    def window_start(self) -> str:
        """First scheduled date included in fact extracts."""
        return (date.fromisoformat(self.run_date) - timedelta(days=EXTRACT_WINDOW_DAYS)).isoformat()

    def path(self, zone: str, table: str) -> str:
        """``<lake>/<zone>/<table>``."""
        return f"{self.lake_uri.rstrip('/')}/{zone}/{table}"


def parse_args(argv: list[str] | None = None) -> JobArgs:
    """Parse arguments shared by every job."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--lake-uri", required=True)
    parser.add_argument("--jdbc-url", default="")
    parser.add_argument("--job", default="")
    parser.add_argument("--mode", choices=["baseline", "optimized"], default="optimized")
    parser.add_argument("--skip-jdbc", action="store_true", help="Use existing silver extracts (benchmarks)")
    ns = parser.parse_args(argv)
    date.fromisoformat(ns.run_date)  # validate
    return JobArgs(ns.run_date, ns.lake_uri, ns.jdbc_url, ns.mode, ns.skip_jdbc)


def resolve_pepper() -> str:
    """Return the RN-09 pepper from the env or AWS Secrets Manager.

    Raises:
        RuntimeError: If no pepper is configured or it is too short.
    """
    pepper = os.getenv("PSEUDONYMIZATION_PEPPER", "")
    secret_id = os.getenv("PSEUDONYMIZATION_SECRET_ID")
    if not pepper and secret_id:
        import boto3

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"]
        try:
            pepper = json.loads(raw).get("pepper", "")
        except json.JSONDecodeError:
            pepper = raw
    if len(pepper) < MIN_PEPPER_LENGTH:
        raise RuntimeError("PSEUDONYMIZATION_PEPPER missing or shorter than 32 characters")
    return pepper
