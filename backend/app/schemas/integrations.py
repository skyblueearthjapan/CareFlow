"""Schemas for the 連携センター (integrations) endpoints — Phase 5-1.

Covers:
  - KaipokeJob / KaipokeJobItem (fetch/push background jobs)
  - GeocodingCache (admin read-only listing)
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
    # 患者性別 (male/female/unknown/None)。週ビューのカードを本体スケジュールと同じ
    # 性別ウォッシュ意匠で塗るための additive フィールド (既存挙動は不変)。
    patient_sex: str | None = Field(default=None, alias="patientSex")
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


# --- 接続設定: カイポケ ログイン情報 (C-1・汎用化) ---------------------------


class KaipokeCredentialsRead(BaseModel):
    """設定状態の読み出し。パスワードは絶対に返さない (書き込み専用)。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    configured: bool
    corp_id: str | None = Field(default=None, alias="corpId")
    user_id: str | None = Field(default=None, alias="userId")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


class KaipokeCredentialsUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    corp_id: str = Field(alias="corpId", min_length=1, max_length=32)
    user_id: str = Field(alias="userId", min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class KaipokeLoginTestResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    ok: bool
    message: str = ""


# --- 逆反映 (カイポケ→CareFlow・R-1/R-2) -----------------------------------


class InboundEligibilityRead(BaseModel):
    """apply実績ゲートの判定結果 (週単位)。eligible=false の週は取り込み不可。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    week_start: date = Field(alias="weekStart")
    eligible: bool
    # 直近の実apply 完了日時 (eligible=true のとき)。UI の説明表示用。
    last_applied_at: datetime | None = Field(default=None, alias="lastAppliedAt")


class InboundApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    sheet_id: UUID = Field(alias="sheetId")
    # 逆反映も既定は dry_run=True (安全側)。実適用は明示的に dryRun:false。
    dry_run: bool = Field(default=True, alias="dryRun")
    # 取り込む日付 (曜日チップの複数選択)。None/空 = シートの週全体。
    days: list[date] | None = None


class InboundItemResultRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    item_id: str = Field(alias="itemId")
    action: str
    outcome: Literal["cancelled", "updated", "added", "skipped", "failed"]
    detail: str = ""
    patient_name: str = Field(default="", alias="patientName")
    date: str = ""


class InboundApplyResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    job_id: UUID | None = Field(default=None, alias="jobId")
    dry_run: bool = Field(alias="dryRun")
    cancelled: int = 0
    updated: int = 0
    added: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[InboundItemResultRead] = Field(default_factory=list)


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
    direction: str = "outbound"
    week_start: date | None = None
    week_end: date | None = None
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


# --- イベント取り込み (個別業務・kaipoke-event-inbound-design.md E-1) --------


class EventsInboundPreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    week_start: date = Field(alias="weekStart")


class EventsInboundChange(BaseModel):
    """プレビュー→適用でエコーバックされる1変更。

    apply は upsert 意味論 (add=既存なら update / update=消えていれば add /
    delete=消えていれば skip) のため、プレビュー後にカイポケ側が動いても安全。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    action: Literal["add", "update", "delete"]
    external_id: str = Field(alias="externalId", max_length=40)
    staff_id: UUID = Field(alias="staffId")
    staff_name: str = Field(default="", alias="staffName")
    target_date: date = Field(alias="date")
    # 実在時刻のみ許可 (25:99 等を弾く — apply 側 time() の ValueError 500 防止)
    start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    title: str = Field(default="", max_length=255)
    is_memo: bool = Field(default=False, alias="isMemo")
    before_start: str | None = Field(default=None, alias="beforeStart")
    before_end: str | None = Field(default=None, alias="beforeEnd")
    before_title: str | None = Field(default=None, alias="beforeTitle")


class EventsInboundUnmatchedRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    staff_name: str = Field(alias="staffName")
    count: int


class EventsInboundPreviewRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    week_start: date = Field(alias="weekStart")
    week_end: date = Field(alias="weekEnd")
    fetched_total: int = Field(alias="fetchedTotal")
    sunday_skipped: int = Field(alias="sundaySkipped")
    memo_count: int = Field(alias="memoCount")
    adds: int = 0
    updates: int = 0
    deletes: int = 0
    changes: list[EventsInboundChange] = Field(default_factory=list)
    unmatched: list[EventsInboundUnmatchedRead] = Field(default_factory=list)


class EventsInboundApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    week_start: date = Field(alias="weekStart")
    # 既定は dry_run=True (安全側)。実適用は明示的に dryRun:false。
    dry_run: bool = Field(default=True, alias="dryRun")
    changes: list[EventsInboundChange]


class EventsInboundApplyItemRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    action: str
    external_id: str = Field(alias="externalId")
    staff_name: str = Field(alias="staffName")
    target_date: str = Field(alias="date")
    title: str = ""
    outcome: Literal["added", "updated", "deleted", "skipped", "failed"]
    detail: str = ""


class EventsInboundApplyResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    job_id: UUID | None = Field(default=None, alias="jobId")
    dry_run: bool = Field(alias="dryRun")
    added: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[EventsInboundApplyItemRead] = Field(default_factory=list)


# --- 置換取り込み (週白紙化→カイポケ全挿入・2026-07-26 PO確定) --------------


class ReplaceInboundRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    week_start: date = Field(alias="weekStart")
    # 既定は dry_run=True (安全側)。実適用は明示的に dryRun:false。
    dry_run: bool = Field(default=True, alias="dryRun")


class ReplaceInboundSkipRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    reason: str
    user_name: str = Field(alias="userName")
    staff_name: str = Field(alias="staffName")
    target_date: str = Field(alias="date")
    start: str = ""


class ReplaceInboundTraineeSoloRead(BaseModel):
    """⚠新人の単独訪問 (取り込み済み・新人フラグ見直しの判断材料)。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    staff_name: str = Field(alias="staffName")
    count: int


class ReplaceInboundResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    job_id: UUID | None = Field(default=None, alias="jobId")
    week_start: date = Field(alias="weekStart")
    week_end: date = Field(alias="weekEnd")
    dry_run: bool = Field(alias="dryRun")
    wiped: int = 0
    inserted: int = 0
    sunday_skipped: int = Field(default=0, alias="sundaySkipped")
    temp_courses: int = Field(default=0, alias="tempCourses")
    # コース担当をカイポケの現実へ付け替えた数 (臨時コース乱立の根治・2026-07-26)
    courses_reassigned: int = Field(default=0, alias="coursesReassigned")
    # 未使用テンプレートから新設したコース行の数 (例: 稲毛E・2026-07-26)
    courses_created: int = Field(default=0, alias="coursesCreated")
    skipped: list[ReplaceInboundSkipRead] = Field(default_factory=list)
    trainee_solo: list[ReplaceInboundTraineeSoloRead] = Field(
        default_factory=list, alias="traineeSolo"
    )


# --- smart-inbound (日単位ハイブリッド自動判別・2026-07-26 PO確定) ------------


class SmartInboundPreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    week_start: date = Field(alias="weekStart")


class SmartInboundPreviewRead(BaseModel):
    """統合プレビュー: 打刻あり日=差分 (🔒実績保護)・なし日=置換、をシステムが自動判別。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    week_start: date = Field(alias="weekStart")
    week_end: date = Field(alias="weekEnd")
    # 打刻実績のある日 (差分モード担当・行を残して直す)
    protected_days: list[date] = Field(default_factory=list, alias="protectedDays")
    # 打刻の無い日 (置換モード担当・白紙化して書き直す)
    replace_days: list[date] = Field(default_factory=list, alias="replaceDays")
    sheet_id: UUID | None = Field(default=None, alias="sheetId")
    diff_summary: dict[str, int] = Field(default_factory=dict, alias="diffSummary")
    replace: ReplaceInboundResult | None = None


class SmartInboundApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    week_start: date = Field(alias="weekStart")
    sheet_id: UUID | None = Field(default=None, alias="sheetId")
    dry_run: bool = Field(default=True, alias="dryRun")


class SmartInboundApplyResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    week_start: date = Field(alias="weekStart")
    protected_days: list[date] = Field(default_factory=list, alias="protectedDays")
    replace_days: list[date] = Field(default_factory=list, alias="replaceDays")
    dry_run: bool = Field(alias="dryRun")
    diff: InboundApplyResult | None = None
    replace: ReplaceInboundResult | None = None
