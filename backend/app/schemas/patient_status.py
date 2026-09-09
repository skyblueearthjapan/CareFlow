"""患者ステータス連動 (docs/plans/patient-status-schedule-design-2026-09-09.md §7-3) の pydantic.

- ``GET  /api/v1/patients/{id}/status-impact``  → :class:`StatusImpact`
- ``POST /api/v1/patients/{id}/status-change``  → :class:`StatusChangeRequest` / :class:`StatusChangeResult`

契約は設計書 §7-3 (a)(b) を正とし、この module の型がそのまま FE の
``frontend/lib/schemas/patientStatus.ts`` と対になる (キー名を変えない)。
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.v2.patient import PatientStatusV2, PatientV2Read

StatusDirection = Literal["deactivate", "reactivate", "none"]
SpecialPeriodAction = Literal["keep", "end"]


class WeekCount(BaseModel):
    """ISO 週ごとの件数 (表示用ラベル付き)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    count: int = Field(ge=0)
    label: str = Field(description="例: '9/14週' (週の月曜日)")


class ImpactVisits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0, description="取消対象 (planned・from_date 以降・除外分を引いた数)")
    by_week: list[WeekCount] = []
    by_source: dict[str, int] = Field(default_factory=dict)
    pair_groups: int = Field(ge=0, default=0, description="2 名体制 (visit_group_id) のグループ数")
    excluded: dict[str, int] = Field(
        default_factory=dict,
        description="対象外にした理由別件数: checked_in / in_progress / week_pinned",
    )


class ImpactSpecialPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    start_date: date
    end_date: date
    pool_marks: int = Field(ge=0)
    placed_marks: int = Field(ge=0)
    placed_future_visits: int = Field(
        ge=0, description="配置済み ● のうち from_date 以降の planned"
    )


class ImpactRegenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weeks: list[WeekCount] = Field(
        default_factory=list, description="expected = 型から作られる見込み件数"
    )
    total: int = Field(ge=0, default=0)


class StatusImpact(BaseModel):
    """``GET /patients/{id}/status-impact`` の応答."""

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    current_status: str
    to_status: PatientStatusV2
    from_date: date
    direction: StatusDirection
    visits: ImpactVisits
    special_period: ImpactSpecialPeriod | None = None
    fixed_visit_rows: int = Field(ge=0, default=0)
    pending_requests: int = Field(ge=0, default=0)
    kaipoke_weeks: int = Field(ge=0, default=0, description="送信差分が出る見込みの週数")
    regenerate: ImpactRegenerate | None = None


class StatusChangeRequest(BaseModel):
    """``POST /patients/{id}/status-change`` の要求."""

    model_config = ConfigDict(extra="forbid")

    status: PatientStatusV2
    from_date: date | None = Field(
        default=None, description="取消/再生成の起点 (JST)。省略時は今日。過去日は 422"
    )
    special_period_action: SpecialPeriodAction = Field(
        default="keep",
        description="deactivate 時のみ有効。keep=特別訪問週間に触らない (既定・PO 決定) / end=終了して ○ を取消",
    )
    regenerate: bool = Field(
        default=True, description="reactivate 時のみ有効。型から生成済み週に予定を作り直す"
    )
    note: str | None = Field(default=None, max_length=200)


class OpGroupRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    op_group_id: UUID


class RegeneratedSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: int = Field(ge=0)
    weeks: list[WeekCount] = Field(default_factory=list)


class SpecialPeriodResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    action: SpecialPeriodAction
    cancelled_pool_marks: int = Field(ge=0, default=0)


class StatusChangeResult(BaseModel):
    """``POST /patients/{id}/status-change`` の応答."""

    model_config = ConfigDict(extra="forbid")

    patient: PatientV2Read
    direction: StatusDirection
    cancelled_visit_ids: list[UUID] = Field(default_factory=list)
    cancelled_count: int = Field(ge=0, default=0)
    special_period: SpecialPeriodResult | None = None
    rejected_requests: int = Field(ge=0, default=0)
    op_groups: list[OpGroupRef] = Field(default_factory=list)
    regenerated: RegeneratedSummary | None = None
    notification_count: int = Field(ge=0, default=0)
