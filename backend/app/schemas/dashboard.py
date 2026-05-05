"""Dashboard aggregation schemas — Phase 7 (W2-D)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class DashboardKpiResponse(BaseModel):
    """Today + this-week roll-up for the dashboard KPI cards.

    No `from_attributes=True` because the route hand-builds this from
    aggregate scalars, never from an ORM row.
    """

    today_visits: int = Field(..., ge=0, description="Total visits scheduled today.")
    today_completed: int = Field(..., ge=0, description="Visits with status='completed' today.")
    today_unassigned: int = Field(
        ..., ge=0, description="Visits today with no primary_staff assigned."
    )
    today_overlapping: int = Field(
        ...,
        ge=0,
        description=(
            "Number of visits today that overlap (in time) with another visit "
            "sharing the same primary_staff. Counts each side of the overlap."
        ),
    )
    this_week_visits: int = Field(
        ..., ge=0, description="Total visits this ISO week (Mon–Sun)."
    )
    this_week_completion_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="completed / total for the current ISO week (0.0 when no visits).",
    )


class DashboardTrendItem(BaseModel):
    """One day's bucket in a trend series."""

    model_config = ConfigDict(from_attributes=True)

    date: date
    total: int = Field(..., ge=0)
    completed: int = Field(..., ge=0)
    unassigned: int = Field(..., ge=0)


class DashboardTrendResponse(BaseModel):
    """`days`-long trend series (oldest → newest)."""

    model_config = ConfigDict(from_attributes=True)

    items: list[DashboardTrendItem]
    days: int = Field(..., ge=1, le=90)
    start_date: date
    end_date: date
