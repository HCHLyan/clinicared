"""RN-08: effective occupancy.

Definitions (also used in the Gold layer and Power BI measures):

* ``regular_capacity``: regular slots offered in the agenda (overbook slots
  excluded).
* ``effective_occupancy = attended / regular_capacity``. Attended includes
  overbooked patients that showed up, so the value may exceed 1.0; that is
  precisely the benefit RN-05 aims for.
* ``nominal_occupancy = booked_not_cancelled_regular / regular_capacity``.
* ``lost_capacity = regular_capacity - attended_regular``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OccupancyInput:
    """Counts for a branch/specialty/day (or any aggregation grain)."""

    regular_capacity: int
    booked_regular: int
    attended_regular: int
    attended_overbook: int


@dataclass(frozen=True, slots=True)
class OccupancyResult:
    """Occupancy KPIs. Ratios are ``None`` when capacity is zero."""

    effective_occupancy: float | None
    nominal_occupancy: float | None
    lost_capacity: int


def compute_occupancy(counts: OccupancyInput) -> OccupancyResult:
    """Compute RN-08 KPIs.

    Raises:
        ValueError: If any count is negative or attended exceeds booked.
    """
    values = (counts.regular_capacity, counts.booked_regular, counts.attended_regular, counts.attended_overbook)
    if any(v < 0 for v in values):
        raise ValueError("counts cannot be negative")
    if counts.attended_regular > counts.booked_regular:
        raise ValueError("attended_regular cannot exceed booked_regular")
    if counts.regular_capacity == 0:
        return OccupancyResult(None, None, 0)
    attended = counts.attended_regular + counts.attended_overbook
    return OccupancyResult(
        effective_occupancy=round(attended / counts.regular_capacity, 4),
        nominal_occupancy=round(counts.booked_regular / counts.regular_capacity, 4),
        lost_capacity=max(0, counts.regular_capacity - counts.attended_regular),
    )
