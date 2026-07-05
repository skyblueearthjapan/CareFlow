"""Schemas for the 連携センター (integrations) endpoints — Phase 5-1.

Covers:
  - KaipokeJob / KaipokeJobItem (fetch/push background jobs)
  - GeocodingCache (admin read-only listing)
  - AiInterpretLog (admin read-only listing)
  - Wave 4-A: kaipoke status + relay (expand/export/diff/apply/stop)
              + correction sheets / items (差分プレビュー)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# --- Kaipoke jobs ----------------------------------------------------------

KaipokeJobType = Literal["fetch", "push"]
KaipokeJobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
KaipokeJobItemStatus = Literal["pending", "running", "completed", "failed", "skipped"]


class KaipokeJobBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_type: KaipokeJobType
    week_start: date
    params: dict[str, Any] = Field(default_factory=dict)


class KaipokeJobCreate(KaipokeJobBase):
    pass


class KaipokeJobItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    job_id: UUID
    seq: int
    status: KaipokeJobItemStatus
    content: dict[str, Any] = Field(default_factory=dict)
    error_msg: str | None = None
    created_at: datetime
    updated_at: datetime


class KaipokeJobRead(KaipokeJobBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    status: KaipokeJobStatus
    result_summary: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    items: list[KaipokeJobItemRead] = Field(default_factory=list)


# --- Geocoding cache -------------------------------------------------------


class GeocodingCacheRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    address_hash: str
    address: str
    lat: float
    lng: float
    provider: str
    looked_up_at: datetime
    created_at: datetime
    updated_at: datetime


# --- AI interpret logs -----------------------------------------------------


class AiInterpretLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    prompt: str
    response: dict[str, Any] = Field(default_factory=dict)
    model: str
    latency_ms: int
    user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


# --- Wave 4-A: kaipoke status + relay -------------------------------------


class KaipokeStatusRead(BaseModel):
    """Combined: live kaipoke /status + the most recent DB-backed job row."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kaipoke: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw kaipoke /api/status response (or {} if unreachable)",
    )
    login_remain_sec: int | None = Field(default=None, alias="loginRemainSec")
    last_sync_at: datetime | None = Field(default=None, alias="lastSyncAt")
    running_job: KaipokeJobRead | None = Field(default=None, alias="runningJob")
    reachable: bool = True
    error: str | None = None


class LiveSnapshotRead(BaseModel):
    """Merged live view of the single-slot kaipoke worker for the monitor UI.

    Combines kaipoke `/api/status` with the running command's progress
    (`/api/apply/result` while applying) and the tail of the ring-buffer log,
    plus the noVNC monitor URL. Polled by the ジョブセンター画面.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    reachable: bool = True
    running: bool = False
    command: str | None = Field(
        default=None, description="Running op: expand|export|apply|diff, or null when idle"
    )
    phase: str | None = None
    processed: int | None = None
    total: int | None = None
    current_name: str | None = Field(default=None, alias="currentName")
    success: int | None = None
    failed: int | None = None
    skipped: int | None = None
    logs: list[str] = Field(default_factory=list)
    monitor_url: str | None = Field(default=None, alias="monitorUrl")
    latest_job: KaipokeJobRead | None = Field(default=None, alias="latestJob")
    error: str | None = None


class WeekScheduleRow(BaseModel):
    """週スケジュール表示用の1訪問 (CareFlow 確定 visits 由来・K-2 UI)。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    visit_date: str = Field(alias="visitDate")  # YYYY-MM-DD
    weekday: int = 0  # 0=月..6=日
    start_time: str = Field(alias="startTime")  # HH:MM
    end_time: str = Field(alias="endTime")  # HH:MM
    patient_name: str = Field(alias="patientName")
    staff1: str = ""
    staff2: str = ""
    course_code: str = Field(default="", alias="courseCode")  # A/B/C/D..
    office_name: str = Field(default="", alias="officeName")


class ExpandStatusRead(BaseModel):
    """対象月の展開状況 (展開=月1回・上書きのため2回目ブロック判定に使う)。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    month: str
    expanded: bool
    expanded_at: datetime | None = Field(default=None, alias="expandedAt")
    job_id: UUID | None = Field(default=None, alias="jobId")


class WeekScheduleRead(BaseModel):
    """対象週の CareFlow スケジュール (週ビュー表示用)。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    week_start: str = Field(alias="weekStart")
    week_end: str = Field(alias="weekEnd")
    rows: list[WeekScheduleRow] = Field(default_factory=list)


class GeneratedCsvRead(BaseModel):
    """CareFlow visits から生成したカイポケ18列CSV (K-2a)。

    差分適用の「最適化CSV」側を CareFlow が供給するための出力。UI プレビュー
    および将来のローカル差分 (compare_schedules_from_content) の入力になる。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    month: str
    row_count: int = Field(alias="rowCount")
    csv_content: str = Field(alias="csvContent")


class IntegrationExpandRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    dry_run: bool = Field(default=False, alias="dryRun")


class IntegrationExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    format: Literal["csv", "xlsx"] = "csv"


class IntegrationDiffRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    # K-2b ローカル差分で対象拠点を絞る (None=全拠点)。従来の /diff では未使用。
    office_id: UUID | None = Field(default=None, alias="officeId")
    # K-2c 週スコープ (毎週運用): 対象週の開始日 (通常は月曜)。指定時は現況・最適化の
    # 両方をこの週レンジに絞り、対象週外のカイポケ既存予定を delete 差分にしない。
    # week_end 省略時は week_start + 6日 (月曜〜日曜の7日)。未指定なら月全体 (要注意)。
    week_start: date | None = Field(default=None, alias="weekStart")
    week_end: date | None = Field(default=None, alias="weekEnd")


class IntegrationApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    sheet_id: UUID = Field(alias="sheetId")
    # 不可逆な外部書込のため既定は dry_run=True (安全側)。実書込は明示的に
    # dryRun:false を送った時だけ。フィールド送り忘れで実書込が走らないようにする。
    dry_run: bool = Field(default=True, alias="dryRun")


class JobAccepted(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    job_id: UUID = Field(alias="jobId")
    kaipoke_job_id: str | None = Field(default=None, alias="kaipokeJobId")
    status: KaipokeJobStatus


class DiffAccepted(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    job_id: UUID = Field(alias="jobId")
    sheet_id: UUID = Field(alias="sheetId")
    summary: dict[str, int] = Field(default_factory=dict)


class JobItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    manually_handled: bool | None = Field(default=None, alias="manuallyHandled")
    comment: str | None = None


# --- Correction sheets / items (Phase C) ----------------------------------

CorrectionAction = Literal[
    "add",
    "delete",
    "update",
    "companion_change",
]


class CorrectionItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    sheet_id: UUID
    patient_id: UUID | None = None
    visit_id: UUID | None = None
    action: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    include: bool
    comment: str | None = None
    created_at: datetime
    updated_at: datetime


class CorrectionSheetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    target_month: str
    status: str
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    items: list[CorrectionItemRead] = Field(default_factory=list)


class CorrectionItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    include: bool | None = None
    comment: str | None = None


class CorrectionBulkSelect(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    ids: list[UUID]
    patch: CorrectionItemUpdate
