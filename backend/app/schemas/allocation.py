"""Allocation Engine schemas — request/response payloads for /api/v1/allocate.

Phase W1-D (continuation). Pydantic v2 strict; pure DTOs that mirror
:class:`app.services.allocation.models.AssignmentResult` for JSON transport.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AllocateOptions(BaseModel):
    """Optional engine knobs. Empty for the W1-D minimal slice."""

    model_config = ConfigDict(extra="forbid")


class AllocateRequest(BaseModel):
    """Run the allocation engine on the visit requests of a single ISO week."""

    model_config = ConfigDict(extra="forbid")

    week_start: date = Field(
        ..., description="対象週の月曜日 (ISO date, yyyy-mm-dd)"
    )
    options: AllocateOptions | None = Field(
        default=None,
        description="将来の拡張用オプション (現状未使用)",
    )


class AssignmentItem(BaseModel):
    """1件分の割当結果。`AssignmentResult` dataclass のシリアライズ形式。"""

    model_config = ConfigDict(from_attributes=True)

    visit_id: str = ""
    date_str: str = ""
    weekday: str = ""
    staff_id: str = ""
    staff_name: str = ""
    pid: str = ""
    pname: str = ""
    area: str = ""
    start_min: int | None = None
    end_min: int | None = None
    service_min: int = 60
    time_type: str = ""
    earliest_min: int | None = None
    latest_min: int | None = None
    note: str = ""
    is_event: bool = False
    is_coupled: bool = False
    movement_km: float | None = None


class AllocateSummary(BaseModel):
    """件数サマリー (assigned/unassigned/total)。

    ``mapping_phase`` は ORM→engine 入力マッピングの完成度を caller に通知する。
    現状 ``"minimal"`` (Patient/Staff/VisitRequest の最小フィールドのみ) で
    動いており、area filter / soft_cap / work_days / lat-lng 距離スコア /
    2-opt 等の高度判定は無効化されている。W1-G で full mapping
    (lat/lng, shifts, areas, weekly_patterns, NG staff, 等) に切り替える。
    """

    total: int
    assigned: int
    unassigned: int
    mapping_phase: Literal["minimal", "full"] = "minimal"


class AllocateResponse(BaseModel):
    """Allocation 結果。`assignments` の順序は engine の発行順を保つ。"""

    model_config = ConfigDict(extra="forbid")

    week_start: date
    summary: AllocateSummary
    assignments: list[AssignmentItem]
