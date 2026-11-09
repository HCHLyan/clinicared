"""US 06 T3: historical simulation."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.analytics.historical_simulation import HistoricalAppointment, SimulationParams, point_in_time_risk, simulate
from src.domain.enums import RiskLevel


def _appt(i: int, patient: int, day: int, outcome: str, lead: int = 3) -> HistoricalAppointment:
    scheduled = datetime(2026, 1, 1, 9) + timedelta(days=day)
    return HistoricalAppointment(i, patient, scheduled, scheduled - timedelta(days=lead), False, outcome)


def test_point_in_time_risk_has_no_look_ahead() -> None:
    history = [
        _appt(1, 1, 0, "NO_SHOW"),
        _appt(2, 1, 10, "NO_SHOW"),
        _appt(3, 1, 20, "ATTENDED"),  # 2 prior no-shows -> HIGH
        _appt(4, 2, 20, "NO_SHOW"),  # its own no-show must not count -> first visit, LOW
    ]
    risk = point_in_time_risk(history)
    assert risk[1] == (RiskLevel.LOW, True)
    assert risk[2][0] is RiskLevel.MEDIUM
    assert risk[3][0] is RiskLevel.HIGH
    assert risk[4] == (RiskLevel.LOW, True)


def test_strategies_ordering_and_costs() -> None:
    history = [_appt(i, i % 40, i % 200, "NO_SHOW" if i % 5 == 0 else "ATTENDED") for i in range(1, 801)]
    params = SimulationParams(0.2, 0.1, 45.0, 60_000.0)
    res = simulate(history, params)
    assert res["none"].messages == 0
    assert res["blanket_24h"].messages == len(history)
    assert res["blanket_72_24"].messages == 2 * len(history)
    assert res["risk_based"].messages < res["blanket_72_24"].messages
    assert res["none"].no_show_rate >= res["blanket_24h"].no_show_rate >= res["blanket_72_24"].no_show_rate
    assert res["blanket_24h"].no_show_rate == pytest.approx(res["none"].no_show_rate * 0.8, abs=1e-4)
    assert res["risk_based"].messaging_cost_cop == res["risk_based"].messages * 45.0


def test_cancelled_on_time_excluded() -> None:
    res = simulate([_appt(1, 1, 0, "CANCELLED_ON_TIME")], SimulationParams())
    assert res["blanket_24h"].appointments == 0


def test_invalid_params() -> None:
    with pytest.raises(ValueError):
        SimulationParams(reduction_first_reminder=1.0)
