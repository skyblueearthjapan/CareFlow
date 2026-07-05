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


class IntegrationApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    sheet_id: UUID = Field(alias="sheetId")
    dry_run: bool = Field(default=False, alias="dryRun")


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
