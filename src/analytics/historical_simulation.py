"""US 06 T3: historical simulation of reminder strategies.

Compares, on the same historical appointments, the expected no-show rate and
messaging cost of:

* ``none``        - no reminders (counterfactual floor).
* ``blanket_24h`` - one 24 h reminder to every appointment.
* ``blanket_72_24`` - 72 h + 24 h reminders to every appointment.
* ``risk_based``  - RN-02 + RN-03 targeting (the implemented policy).

Method (counterfactual re-weighting)
------------------------------------
Each appointment has an observed outcome ``y`` (1 = no-show) obtained under
``k_obs`` historical reminders. The effect of reminders is modelled as a
multiplicative reduction of the no-show probability:

    m(0) = 1, m(1) = 1 - r1, m(k>=2) = (1 - r1) * (1 - r2)

and the expected no-show under a policy that sends ``k`` reminders is
``min(1, y * m(k) / m(k_obs))``. ``r1``/``r2`` are **assumptions** that must be
calibrated with a ClínicaRed A/B test; the report includes a sensitivity
grid so decisions do not hinge on a single value.

Risk is computed point-in-time (only outcomes of appointments scheduled
before the evaluation instant are visible), so there is no look-ahead leak.

Usage::

    python -m src.analytics.historical_simulation --csv history.csv --out report.json
    python -m src.analytics.historical_simulation --from-db --since 2025-11-01 --out report.json
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from src.domain.enums import RiskLevel
from src.domain.rules.reminders import required_kinds
from src.domain.rules.risk import RiskInput, classify_risk
from src.domain.timeutils import subtract_months

STRATEGIES: tuple[str, ...] = ("none", "blanket_24h", "blanket_72_24", "risk_based")
#: Risk is refreshed daily; the last computation before the visit is ~1 day earlier.
RISK_EVALUATION_LEAD = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class HistoricalAppointment:
    """One past appointment with its RN-01 outcome."""

    appointment_id: int
    patient_id: int
    scheduled_at: datetime
    booked_at: datetime
    confirmed: bool
    outcome: str  # ATTENDED | NO_SHOW | CANCELLED_ON_TIME
    reminders_received: int = 0


@dataclass(frozen=True, slots=True)
class SimulationParams:
    """Model assumptions (all configurable from the CLI)."""

    reduction_first_reminder: float = 0.20
    reduction_additional_reminder: float = 0.10
    cost_per_message_cop: float = 45.0
    cost_per_no_show_cop: float = 60_000.0

    def __post_init__(self) -> None:
        for name in ("reduction_first_reminder", "reduction_additional_reminder"):
            value = getattr(self, name)
            if not 0.0 <= value < 1.0:
                raise ValueError(f"{name} must be in [0, 1)")
        if self.cost_per_message_cop < 0 or self.cost_per_no_show_cop < 0:
            raise ValueError("costs cannot be negative")

    def multiplier(self, reminders: int) -> float:
        """No-show probability multiplier for ``reminders`` reminders."""
        if reminders <= 0:
            return 1.0
        value = 1.0 - self.reduction_first_reminder
        if reminders >= 2:
            value *= 1.0 - self.reduction_additional_reminder
        return value


@dataclass(slots=True)
class StrategyResult:
    """Aggregated outcome of one strategy."""

    strategy: str
    appointments: int = 0
    messages: int = 0
    messaging_cost_cop: float = 0.0
    expected_no_shows: float = 0.0
    no_show_rate: float = 0.0
    no_shows_avoided_vs_none: float = 0.0
    cost_per_avoided_no_show_cop: float | None = None
    net_benefit_cop: float = 0.0
    roi: float | None = None
    messages_by_risk: dict[str, int] = field(default_factory=dict)


def point_in_time_risk(appointments: Sequence[HistoricalAppointment]) -> dict[int, tuple[RiskLevel, bool]]:
    """Compute RN-02 risk and first-visit flag without look-ahead.

    Returns:
        ``{appointment_id: (risk_level, is_first_appointment)}``.
    """
    no_shows: dict[int, list[datetime]] = defaultdict(list)
    visits: dict[int, list[datetime]] = defaultdict(list)
    for appt in appointments:
        if appt.outcome == "NO_SHOW":
            no_shows[appt.patient_id].append(appt.scheduled_at)
        if appt.outcome != "CANCELLED_ON_TIME":
            visits[appt.patient_id].append(appt.scheduled_at)
    for series in (*no_shows.values(), *visits.values()):
        series.sort()

    result: dict[int, tuple[RiskLevel, bool]] = {}
    for appt in appointments:
        as_of = appt.scheduled_at - RISK_EVALUATION_LEAD
        history = no_shows.get(appt.patient_id, [])
        window_start = subtract_months(as_of, 6)
        count = bisect.bisect_left(history, as_of) - bisect.bisect_left(history, window_start)
        level = classify_risk(RiskInput(appt.scheduled_at, appt.booked_at, appt.confirmed, count)).level
        prior_visits = bisect.bisect_left(visits.get(appt.patient_id, []), appt.scheduled_at)
        result[appt.appointment_id] = (level, prior_visits == 0)
    return result


def reminders_for(strategy: str, level: RiskLevel, is_first: bool) -> int:
    """Number of reminders a strategy sends to one appointment."""
    if strategy == "none":
        return 0
    if strategy == "blanket_24h":
        return 1
    if strategy == "blanket_72_24":
        return 2
    if strategy == "risk_based":
        return len(required_kinds(level, is_first))
    raise ValueError(f"unknown strategy {strategy}")


def simulate(appointments: Sequence[HistoricalAppointment], params: SimulationParams) -> dict[str, StrategyResult]:
    """Run every strategy over the same appointments.

    Appointments cancelled on time are excluded: they are not no-show
    candidates and would not receive reminders after cancelling.
    """
    risk = point_in_time_risk(appointments)
    eligible = [a for a in appointments if a.outcome in ("ATTENDED", "NO_SHOW")]
    results = {name: StrategyResult(strategy=name) for name in STRATEGIES}

    for appt in eligible:
        observed = 1.0 if appt.outcome == "NO_SHOW" else 0.0
        level, is_first = risk[appt.appointment_id]
        base_multiplier = params.multiplier(appt.reminders_received)
        for name, res in results.items():
            k = reminders_for(name, level, is_first)
            res.appointments += 1
            res.messages += k
            res.messages_by_risk[level.value] = res.messages_by_risk.get(level.value, 0) + k
            res.expected_no_shows += min(1.0, observed * params.multiplier(k) / base_multiplier)

    floor = results["none"].expected_no_shows
    for res in results.values():
        res.messaging_cost_cop = round(res.messages * params.cost_per_message_cop, 2)
        res.no_show_rate = round(res.expected_no_shows / res.appointments, 4) if res.appointments else 0.0
        res.no_shows_avoided_vs_none = round(floor - res.expected_no_shows, 2)
        res.expected_no_shows = round(res.expected_no_shows, 2)
        if res.no_shows_avoided_vs_none > 0:
            res.cost_per_avoided_no_show_cop = round(res.messaging_cost_cop / res.no_shows_avoided_vs_none, 2)
        benefit = res.no_shows_avoided_vs_none * params.cost_per_no_show_cop
        res.net_benefit_cop = round(benefit - res.messaging_cost_cop, 2)
        res.roi = round(res.net_benefit_cop / res.messaging_cost_cop, 2) if res.messaging_cost_cop else None
    return results


def sensitivity(appointments: Sequence[HistoricalAppointment], base: SimulationParams, grid: Iterable[float]) -> list[dict[str, object]]:
    """Re-run the comparison for several first-reminder effect sizes."""
    rows: list[dict[str, object]] = []
    for r1 in grid:
        params = SimulationParams(r1, base.reduction_additional_reminder, base.cost_per_message_cop, base.cost_per_no_show_cop)
        res = simulate(appointments, params)
        rows.append(
            {
                "reduction_first_reminder": r1,
                "risk_based_no_show_rate": res["risk_based"].no_show_rate,
                "blanket_24h_no_show_rate": res["blanket_24h"].no_show_rate,
                "risk_based_cost_cop": res["risk_based"].messaging_cost_cop,
                "blanket_24h_cost_cop": res["blanket_24h"].messaging_cost_cop,
                "risk_based_net_benefit_cop": res["risk_based"].net_benefit_cop,
                "blanket_24h_net_benefit_cop": res["blanket_24h"].net_benefit_cop,
            }
        )
    return rows


def load_csv(path: str) -> list[HistoricalAppointment]:
    """Load ``appointment_id,patient_id,scheduled_at,booked_at,confirmed,outcome[,reminders_received]``."""
    with open(path, encoding="utf-8", newline="") as handle:
        return [
            HistoricalAppointment(
                appointment_id=int(row["appointment_id"]),
                patient_id=int(row["patient_id"]),
                scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
                booked_at=datetime.fromisoformat(row["booked_at"]),
                confirmed=row["confirmed"].strip().lower() in ("1", "true", "t", "yes"),
                outcome=row["outcome"].strip().upper(),
                reminders_received=int(row.get("reminders_received") or 0),
            )
            for row in csv.DictReader(handle)
        ]


def load_from_db(since: str) -> list[HistoricalAppointment]:
    """Load evaluated appointments from PostgreSQL (uses the API settings)."""
    from sqlalchemy import text

    from src.core.database import engine

    sql = text(
        """
        SELECT ap.id, ap.patient_id, ap.scheduled_datetime, COALESCE(ap.created_at, ap.scheduled_datetime) AS booked_at,
               ap.confirmed_at IS NOT NULL AS confirmed, at.status AS outcome,
               (SELECT count(*) FROM messages m WHERE m.appointment_id = ap.id
                  AND m.message_type IN ('H72','H24') AND m.status IN ('SENT','DELIVERED','READ')) AS reminders
          FROM appointments ap JOIN attendances at ON at.appointment_id = ap.id
         WHERE ap.scheduled_datetime >= :since AND at.status IN ('ATTENDED','NO_SHOW','CANCELLED_ON_TIME')
         ORDER BY ap.scheduled_datetime
        """
    )
    with engine.connect() as conn:
        return [HistoricalAppointment(*row[:6], reminders_received=int(row[6])) for row in conn.execute(sql, {"since": since})]


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="ClínicaRed reminder strategy simulation")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv")
    source.add_argument("--from-db", action="store_true")
    parser.add_argument("--since", default="2025-11-01")
    parser.add_argument("--r1", type=float, default=0.20, help="Relative no-show reduction of the first reminder (assumption)")
    parser.add_argument("--r2", type=float, default=0.10, help="Additional reduction of the second reminder (assumption)")
    parser.add_argument("--message-cost", type=float, default=45.0)
    parser.add_argument("--no-show-cost", type=float, default=60_000.0)
    parser.add_argument("--out", default="simulation_report.json")
    ns = parser.parse_args()

    appointments = load_csv(ns.csv) if ns.csv else load_from_db(ns.since)
    params = SimulationParams(ns.r1, ns.r2, ns.message_cost, ns.no_show_cost)
    results = simulate(appointments, params)
    report = {
        "params": asdict(params),
        "appointments_loaded": len(appointments),
        "strategies": {name: asdict(res) for name, res in results.items()},
        "sensitivity_r1": sensitivity(appointments, params, (0.10, 0.15, 0.20, 0.25, 0.30)),
    }
    with open(ns.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print(json.dumps(report["strategies"], indent=2, default=str))


if __name__ == "__main__":
    main()
