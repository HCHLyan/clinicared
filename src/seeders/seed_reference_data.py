"""Idempotent loader for reference data used by US 04 (F5, F7, guardians demo).

Usage::

    python -m src.seeders.seed_reference_data --years 2026 2027 \
        [--f5 landing/holidays/holidays.csv] [--f7 landing/f7_specialty_rules/rules.csv]

* F5: if a file is given it is loaded; the statutory Colombian calendar is
  always upserted as a fallback (``source = 'STATUTORY'``).
* F7: ``specialty_id,specialty_name,is_overbookable``. Without the file the
  current flags are left untouched (F7 is the only source of truth).
"""

from __future__ import annotations

import argparse
import csv
import logging

from sqlalchemy import text

from src.core.database import engine
from src.domain.holidays_co import colombian_holidays

logger = logging.getLogger(__name__)


def seed_holidays(years: list[int], f5_path: str | None) -> int:
    """Upsert statutory holidays, then F5 rows (F5 wins on name)."""
    rows = [{"d": d, "n": "Festivo nacional", "s": "STATUTORY"} for y in years for d in sorted(colombian_holidays(y))]
    if f5_path:
        with open(f5_path, encoding="utf-8", newline="") as handle:
            rows += [{"d": r["holiday_date"], "n": r["name"], "s": "F5"} for r in csv.DictReader(handle)]
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO holidays (holiday_date, name, source) VALUES (:d, :n, :s)
                ON CONFLICT (holiday_date) DO UPDATE SET
                    name = CASE WHEN EXCLUDED.source = 'F5' THEN EXCLUDED.name ELSE holidays.name END,
                    source = CASE WHEN EXCLUDED.source = 'F5' THEN 'F5' ELSE holidays.source END
                """
            ),
            rows,
        )
    return len(rows)


def seed_f7(f7_path: str) -> int:
    """Apply F7 overbooking flags by specialty id."""
    with open(f7_path, encoding="utf-8", newline="") as handle:
        rows = [
            {"id": int(r["specialty_id"]), "flag": r["is_overbookable"].strip().lower() in ("true", "1", "si", "sí", "yes")}
            for r in csv.DictReader(handle)
        ]
    with engine.begin() as conn:
        conn.execute(text("UPDATE specialties SET is_overbookable = :flag WHERE id = :id"), rows)
    return len(rows)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", required=True)
    parser.add_argument("--f5")
    parser.add_argument("--f7")
    ns = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logger.info("holidays upserted: %s", seed_holidays(ns.years, ns.f5))
    if ns.f7:
        logger.info("F7 rules applied: %s", seed_f7(ns.f7))
    else:
        logger.warning("No F7 file given: specialty overbooking flags left unchanged")


if __name__ == "__main__":
    main()
