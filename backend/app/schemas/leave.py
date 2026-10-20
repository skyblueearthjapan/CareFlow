"""休み申請と「休み・勤務表」の表 (mig 0099) の読み書きのスキーマ.

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §2-2・§2-5・§7。
モデルは ``app/models/leave.py``。API (``app/api/v1/leave.py`` など) は後の段で作る。
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: 休みの種類の英語の値 (``app/core/leave.py`` の ``LEAVE_TYPES``)。
LeaveTypeCode = Literal["off", "am_off", "pm_off", "paid_off"]
#: 件名で種類が分からないカイポケの予定に管理者が選ぶ種類。
JudgementKind = Literal["not_leave", "off", "am_off", "pm_off", "paid_off"]
KinmuSheetKind = Literal["plan", "final"]
DriveStatus = Literal["connected", "needs_reconnect"]


# ---------------------------------------------------------------------------
# 事業所の休み (office_closed_days)
# ---------------------------------------------------------------------------


class OfficeClosedDayCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    label: str = Field(min_length=1, max_length=64, description="例「年末年始」")


class OfficeClosedDayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    label: str
    created_by: UUID | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# 休みのカイポケへの送信の記録 (staff_leave_kaipoke)
# ---------------------------------------------------------------------------


class StaffLeaveKaipokeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    staff_id: UUID
    leave_date: date
    sent_type: LeaveTypeCode | None = None
    baseline_pending: bool
    sent_at: datetime | None = None
    sent_by: UUID | None = None
    kaipoke_external_id: str | None = None
    updated_at: datetime


# ---------------------------------------------------------------------------
# 管理者が選んだ種類 (kaipoke_leave_judgements)
# ---------------------------------------------------------------------------


class KaipokeLeaveJudgementCreate(BaseModel):
    """照合の差分「『〇〇』の種類を選んでください」への答え (§7-4-3)."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    event_date: date
    subject: str = Field(min_length=1, max_length=255)
    starts: time | None = None
    ends: time | None = None
    chosen_kind: JudgementKind


class KaipokeLeaveJudgementRead(KaipokeLeaveJudgementCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content_hash: str
    decided_by: UUID | None = None
    decided_at: datetime


# ---------------------------------------------------------------------------
# 照合の結果 (leave_compare_runs)・月 (leave_months)
# ---------------------------------------------------------------------------


class LeaveCompareRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    month: date
    kaipoke_job_id: UUID | None = None
    fetched_at: dict[str, Any] | None = None
    diffs: list[dict[str, Any]] = Field(default_factory=list)
    diff_count: int
    planned_send_count: int
    unconfirmed_days: int
    created_by: UUID | None = None
    created_at: datetime


class LeaveMonthUpdate(BaseModel):
    """``PUT /leave/months/{month}`` (勤務すべき時間数)."""

    model_config = ConfigDict(extra="forbid")

    required_hours: float | None = Field(default=None, ge=0, le=744)


class LeaveMonthRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    month: date
    required_hours: float | None = None
    last_leave_changed_at: datetime | None = None
    last_compared_at: datetime | None = None
    last_compare_run_id: UUID | None = None
    actual_recorded_at: datetime | None = None
    actual_recorded_by: UUID | None = None


# ---------------------------------------------------------------------------
# 勤務表 (kinmu_sheets)・Google ドライブの接続 (google_drive_connections)
# ---------------------------------------------------------------------------


class KinmuSheetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    month: date
    kind: KinmuSheetKind
    version: int
    drive_file_id: str | None = None
    url: str | None = None
    created_by: UUID | None = None
    created_at: datetime


class GoogleDriveConnectionRead(BaseModel):
    """接続の状態。リフレッシュトークンは返さない."""

    model_config = ConfigDict(from_attributes=True)

    status: DriveStatus
    folder_id: str | None = None
    shared_drive_id: str | None = None
    connected_by: UUID | None = None
    last_error: str | None = None
    updated_at: datetime
