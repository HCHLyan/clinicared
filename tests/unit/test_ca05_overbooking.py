"""CA-05 (RN-05): controlled overbooking."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.rules.overbooking import allowed_extra_slots, block_start, evaluate_overbook_request


@pytest.mark.parametrize(("high", "expected"), [(0, 0), (2, 0), (3, 1), (5, 1), (6, 2), (9, 2), (30, 2)])
def test_one_extra_per_three_high_risk_capped_at_two(high: int, expected: int) -> None:
    assert allowed_extra_slots(high, specialty_overbookable=True) == expected


def test_never_on_non_overbookable_specialty_f7() -> None:
    assert allowed_extra_slots(9, specialty_overbookable=False) == 0
    decision = evaluate_overbook_request(high_risk_regular_count=9, used_extra_slots=0, specialty_overbookable=False)
    assert (decision.allowed, decision.reason) == (False, "SPECIALTY_NOT_OVERBOOKABLE")


def test_capacity_is_consumed() -> None:
    assert evaluate_overbook_request(high_risk_regular_count=3, used_extra_slots=0, specialty_overbookable=True).allowed
    exhausted = evaluate_overbook_request(high_risk_regular_count=3, used_extra_slots=1, specialty_overbookable=True)
    assert (exhausted.allowed, exhausted.reason) == (False, "OVERBOOK_CAPACITY_EXHAUSTED")
    none = evaluate_overbook_request(high_risk_regular_count=2, used_extra_slots=0, specialty_overbookable=True)
    assert none.reason == "NO_HIGH_RISK_QUOTA"


def test_block_start() -> None:
    assert block_start(datetime(2026, 11, 10, 10, 47), 60) == datetime(2026, 11, 10, 10, 0)
    assert block_start(datetime(2026, 11, 10, 10, 47), 30) == datetime(2026, 11, 10, 10, 30)
    with pytest.raises(ValueError):
        block_start(datetime(2026, 11, 10, 10, 47), 7)


def test_negative_count_rejected() -> None:
    with pytest.raises(ValueError):
        allowed_extra_slots(-1, True)
