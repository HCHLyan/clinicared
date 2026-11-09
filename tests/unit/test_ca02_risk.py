"""CA-02 (RN-02): rule-based risk classification at every boundary."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.domain.enums import RiskLevel
from src.domain.rules.risk import RiskInput, classify_risk, lead_time_days

APPOINTMENT = datetime(2026, 12, 1, 10, 0)


def _risk(no_shows: int, lead_days: int, confirmed: bool = False) -> RiskLevel:
    booked = APPOINTMENT - timedelta(days=lead_days)
    return classify_risk(RiskInput(APPOINTMENT, booked, confirmed, no_shows)).level


@pytest.mark.parametrize(
    ("no_shows", "lead", "confirmed", "expected"),
    [
        (2, 0, True, RiskLevel.HIGH),
        (3, 5, False, RiskLevel.HIGH),
        (0, 21, False, RiskLevel.HIGH),
        (0, 21, True, RiskLevel.MEDIUM),  # confirmed: only the >10 d rule applies
        (0, 20, False, RiskLevel.MEDIUM),  # boundary: > 20, not >= 20
        (0, 11, True, RiskLevel.MEDIUM),
        (1, 0, False, RiskLevel.MEDIUM),
        (0, 10, False, RiskLevel.LOW),  # boundary: > 10, not >= 10
        (0, 0, False, RiskLevel.LOW),
    ],
)
def test_risk_matrix(no_shows: int, lead: int, confirmed: bool, expected: RiskLevel) -> None:
    assert _risk(no_shows, lead, confirmed) is expected


def test_reasons_are_explainable() -> None:
    booked = APPOINTMENT - timedelta(days=25)
    assessment = classify_risk(RiskInput(APPOINTMENT, booked, False, 2))
    assert assessment.reasons == ("NO_SHOWS_GE_2_IN_6M", "LEAD_TIME_GT_20D_UNCONFIRMED")
    assert assessment.lead_time_days == 25


def test_lead_time_uses_calendar_days_and_never_negative() -> None:
    assert lead_time_days(datetime(2026, 11, 1, 23, 59), datetime(2026, 11, 2, 0, 1)) == 1
    assert lead_time_days(datetime(2026, 11, 5), datetime(2026, 11, 1)) == 0


def test_negative_no_shows_rejected() -> None:
    with pytest.raises(ValueError):
        classify_risk(RiskInput(APPOINTMENT, APPOINTMENT, False, -1))
