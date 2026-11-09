"""RN-08: effective occupancy."""

from __future__ import annotations

import pytest

from src.domain.rules.occupancy import OccupancyInput, compute_occupancy


def test_effective_occupancy_counts_overbook_attendance() -> None:
    result = compute_occupancy(OccupancyInput(regular_capacity=20, booked_regular=20, attended_regular=16, attended_overbook=2))
    assert result.effective_occupancy == 0.9
    assert result.nominal_occupancy == 1.0
    assert result.lost_capacity == 4


def test_zero_capacity() -> None:
    assert compute_occupancy(OccupancyInput(0, 0, 0, 0)).effective_occupancy is None


def test_invalid_counts() -> None:
    with pytest.raises(ValueError):
        compute_occupancy(OccupancyInput(10, 5, 6, 0))
    with pytest.raises(ValueError):
        compute_occupancy(OccupancyInput(-1, 0, 0, 0))
