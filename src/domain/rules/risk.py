"""RN-02: rule-based no-show risk classification.

* HIGH:   >= 2 no-shows in the last 6 months, OR lead time > 20 days and the
          appointment is not confirmed.
* MEDIUM: exactly 1 no-show in the last 6 months, OR lead time > 10 days.
* LOW:    otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.domain.enums import RiskLevel

HIGH_NO_SHOW_THRESHOLD: int = 2
HIGH_LEAD_TIME_DAYS: int = 20
MEDIUM_LEAD_TIME_DAYS: int = 10


@dataclass(frozen=True, slots=True)
class RiskInput:
    """Point-in-time features required by RN-02."""

    scheduled_at: datetime
    booked_at: datetime
    is_confirmed: bool
    no_shows_last_6m: int


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Risk level with the rule codes that triggered it."""

    level: RiskLevel
    reasons: tuple[str, ...]
    lead_time_days: int


def lead_time_days(booked_at: datetime, scheduled_at: datetime) -> int:
    """Return calendar days between booking and appointment (never negative)."""
    return max(0, (scheduled_at.date() - booked_at.date()).days)


def classify_risk(features: RiskInput) -> RiskAssessment:
    """Apply RN-02.

    Args:
        features: Point-in-time features of the appointment.

    Returns:
        The risk assessment. All triggered conditions of the winning level
        are reported so the decision is explainable.

    Raises:
        ValueError: If the no-show count is negative.
    """
    if features.no_shows_last_6m < 0:
        raise ValueError("no_shows_last_6m cannot be negative")
    lead = lead_time_days(features.booked_at, features.scheduled_at)

    high: list[str] = []
    if features.no_shows_last_6m >= HIGH_NO_SHOW_THRESHOLD:
        high.append("NO_SHOWS_GE_2_IN_6M")
    if lead > HIGH_LEAD_TIME_DAYS and not features.is_confirmed:
        high.append("LEAD_TIME_GT_20D_UNCONFIRMED")
    if high:
        return RiskAssessment(RiskLevel.HIGH, tuple(high), lead)

    medium: list[str] = []
    if features.no_shows_last_6m == 1:
        medium.append("NO_SHOW_EQ_1_IN_6M")
    if lead > MEDIUM_LEAD_TIME_DAYS:
        medium.append("LEAD_TIME_GT_10D")
    if medium:
        return RiskAssessment(RiskLevel.MEDIUM, tuple(medium), lead)

    return RiskAssessment(RiskLevel.LOW, ("DEFAULT_LOW",), lead)
