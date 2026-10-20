"""休み申請と「休み・勤務表」の表 (mig 0099) と月の API (``app/api/v1/leave.py``) のスキーマ.

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §2-2・§2-5・§5・§7。
モデルは ``app/models/leave.py``。
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

#: 休みの種類の英語の値 (``app/core/leave.py`` の ``LEAVE_TYPES``)。
LeaveTypeCode = Literal["off", "am_off", "pm_off", "paid_off"]
#: 件名で種類が分からないカイポケの予定に管理者が選ぶ種類。
JudgementKind = Literal["not_leave", "off", "am_off", "pm_off", "paid_off"]
KinmuSheetKind = Literal["plan", "final"]
DriveStatus = Literal["connected", "needs_reconnect"]


def month_start(value: date) -> date:
    """月の列 (leave_months など・DB は月の 1 日だけ) へ渡す日付を月の 1 日にそろえる."""
    return value.replace(day=1)


#: 月の 1 日にそろえた日付 (``2026-10-15`` → ``2026-10-01``)。読み出しのスキーマでは表示だけ。
MonthStart = Annotated[date, AfterValidator(month_start)]

_YEAR_MONTH = re.compile(r"^\d{4}-\d{2}$")


def _month_text(value: Any) -> Any:
    """``YYYY-MM`` を ``YYYY-MM-01`` として読む (それ以外はそのまま日付として読む)."""
    if isinstance(value, str) and _YEAR_MONTH.match(value):
        return f"{value}-01"
    return value


def _first_day_only(value: date) -> date:
    """月を受け取るパスと入力: 月の 1 日でない日付は 422 (別の月を黙って読まない)."""
    if value.day != 1:
        raise ValueError("月は YYYY-MM か月の 1 日 (YYYY-MM-01) で指定してください")
    return month_start(value)


#: 月を受け取るパスと入力 (``2026-11`` / ``2026-11-01``)。月の 1 日でない日付は 422。
MonthParam = Annotated[date, BeforeValidator(_month_text), AfterValidator(_first_day_only)]


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
    subject: str = Field(min_length=1)
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
    month: MonthStart
    kaipoke_job_id: UUID | None = None
    fetched_at: dict[str, Any] | None = None
    diffs: list[dict[str, Any]] = Field(default_factory=list)
    auto_updates: list[dict[str, Any]] | None = None
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

    month: MonthStart
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
    month: MonthStart
    kind: KinmuSheetKind
    version: int
    drive_file_id: str | None = None
    url: str | None = None
    reason: str | None = None
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


# ---------------------------------------------------------------------------
# 「休み・勤務表」の月の API (#8・``app/api/v1/leave.py``・設計 §5)
# ---------------------------------------------------------------------------

#: 休みの出どころ (``staff_weekly_overrides.source``)。表の「足」「カ」の印。
LeaveSourceCode = Literal["request", "admin", "kaipoke"]
#: 申請の種類 (変更のお願いだけ ``cancel`` = 休みを取り消してほしい)。
LeaveRequestType = Literal["off", "am_off", "pm_off", "paid_off", "cancel"]
#: ``normal`` = 締め切り前の申請／``change`` = 変更のお願い (締め切り後・確定後)。
LeaveRequestKind = Literal["normal", "change"]
#: 休みの送信の状態 (§2-5): 照合前／未送信／送信済み／カイポケで要対応。
LeaveSendStatus = Literal["baseline", "unsent", "sent", "kaipoke_action"]
EmploymentType = Literal["full_time", "part_time"]


class HolidayRead(BaseModel):
    """祝日 (振替休日・国民の休日を含む) と事業所の休みの日付と名前。どちらも勤務日のまま."""

    date: date
    name: str


class LeaveMonthDay(BaseModel):
    """表の見出しと下の行 (日ごと)."""

    date: date
    #: 0=月〜6=日。
    weekday: int
    holiday_name: str | None = None
    #: 事業所の休み (灰色の帯・名前)。
    closed_label: str | None = None
    #: その週を作り済み (コースがある) か。作り済みの日だけ訪問の件数を出す。
    week_generated: bool
    #: その日休む人の数 = 承認済みの公休・半休・有給 (半休も 1 人)。型で勤務しない曜日と
    #: 新人は数えない。
    off_count: int
    #: ``off_count`` が ``LeaveMonthView.off_warn_threshold`` (2) 以上 = 黄色。
    off_warn: bool
    #: その日の申請中の件数 (見出しの「申N」)。
    pending_count: int


class LeaveMonthStaff(BaseModel):
    """表の行 (職員) と右端の集計。並びは登録ナンバー → 登録順 (``order``)."""

    id: UUID
    order: int
    name: str
    code: str | None = None
    employment_type: EmploymentType
    #: 非常勤は休日数の「対象外」。
    is_full_time: bool
    #: 新人 (その日休む人に数えない)。
    is_trainee: bool
    #: active / on_leave / retired (退職者はその月に休みがあるときだけ載る)。
    status: str
    #: 型 (``staff_shifts.is_on``) で勤務しない曜日 (0=月〜6=日・灰色)。
    off_weekdays: list[int]
    #: 休日数 (型で勤務しない曜日＋表の休み・半休 0.5・有給は数えない)。
    holidays: float
    #: 必要休日数 (常勤だけ・非常勤は None =「対象外」)。
    required_holidays: int | None = None
    #: あと N 日 (常勤だけ・足りていれば 0・非常勤は None)。
    shortfall: float | None = None
    #: 有給の日数。
    paid_days: int
    #: 申請中の件数。
    pending_count: int
    #: 申請中がすべて通ったら増える休日数 (「通れば あと N」の材料)。
    pending_gain: float
    #: この月の確定 (``staff_shift_confirmations``) の時刻。未確定は None。
    confirmed_at: datetime | None = None


class LeavePendingBrief(BaseModel):
    """マスに出す申請中 (枠＋「?」)."""

    request_id: UUID
    override_type: LeaveRequestType
    kind: LeaveRequestKind


class LeaveMonthCell(BaseModel):
    """職員×日のマス。何もないマス (休みも時間変更も申請も訪問も無い) は返さない.

    型で勤務しない曜日は ``LeaveMonthStaff.off_weekdays`` から、週を作り済みかは
    ``LeaveMonthDay.week_generated`` から決める (返さないマスの訪問は 0 件)。
    """

    staff_id: UUID
    date: date
    #: 休みの種類 (登録が無い・時間変更だけなら None)。
    leave_type: LeaveTypeCode | None = None
    source: LeaveSourceCode | None = None
    #: 休みの理由 (メモ)。
    note: str | None = None
    #: 承認元の申請。
    approved_request_id: UUID | None = None
    #: 時間変更 (``custom_time``) の時刻。休みを入れると置き換わる (小窓で「消えます」)。
    time_change_start: time | None = None
    time_change_end: time | None = None
    #: 申請中。
    pending: LeavePendingBrief | None = None
    #: 承認後に取り消し (斜線)。申請は承認済みのままで、いま休みの登録が無い。
    cancelled_after_approval: bool = False
    #: 本人が担当の訪問の件数 (取消は除く・週を作り済みの日だけ)。
    visit_count: int = 0
    #: 休みの送信の状態 (休みの登録か ``sent_type`` があるときだけ)。
    send_status: LeaveSendStatus | None = None


class LeavePendingItem(BaseModel):
    """右の一覧の申請中 1 件 (日付順／職員順に並べる材料つき)."""

    id: UUID
    staff_id: UUID
    staff_name: str
    #: 職員順に並べるときの順番 (``LeaveMonthStaff.order``)。
    staff_order: int
    date: date
    override_type: LeaveRequestType
    kind: LeaveRequestKind
    note: str | None = None
    created_at: datetime
    #: いまの休みの登録 (変更のお願いの相手)。
    current_leave_type: LeaveTypeCode | None = None
    #: 今日より前の日 (承認しても休みの記録だけ・訪問は動かさない)。
    past_day: bool
    #: 承認すると担当なしへ移す訪問の件数 (今日以降・休みの時間帯の本人の planned)。
    move_visit_count: int
    #: その日休む人の数 (いま) と、この申請を承認したあと。
    off_count_now: int
    off_count_if_approved: int


class LeaveCompareStatus(BaseModel):
    """カイポケとの照合の状態 (照合の担当 #9 が書いた ``leave_compare_runs`` を読むだけ)."""

    last_compared_at: datetime | None = None
    last_compare_run_id: UUID | None = None
    #: 最新の照合の差分の件数・送る予定の件数・未確認の日の数 (照合が無ければ None)。
    diff_count: int | None = None
    planned_send_count: int | None = None
    unconfirmed_days: int | None = None
    #: その月の休みを最後に変えた時刻。
    last_leave_changed_at: datetime | None = None
    #: 照合の後に休みを変えた (比べ直しが要る)。照合が無ければ False。
    changed_since_compare: bool
    #: 照合前の休みの件数 (その月の照合が一度済むまで送らない)。
    baseline_pending_count: int


class LeaveMonthConfirmation(BaseModel):
    """月の確定の状態 (``staff_shift_confirmations`` を表の職員で集めたもの)."""

    #: 表の職員の全員が確定済み。
    confirmed: bool
    confirmed_staff_count: int
    staff_count: int
    #: いちばん新しい確定の時刻。
    last_confirmed_at: datetime | None = None
    #: 確定の後に休みを変えた (再確定が要る)。
    changed_since_confirm: bool


class LeaveMonthView(BaseModel):
    """``GET /leave/month/{month}`` — 「休み・勤務表」の月を 1 回で返す (管理者)."""

    month: date
    #: 今日 (JST)。これより前の日は休みの記録だけ。
    today: date
    required_holidays: int
    #: 常勤の従業者が勤務すべき時間数 (未入力は None)。
    required_hours: float | None = None
    #: 前の月の勤務すべき時間数 (確定のダイアログの初期値)。
    previous_required_hours: float | None = None
    #: その日休む人がこの数以上で黄色。
    off_warn_threshold: int
    days: list[LeaveMonthDay]
    staff: list[LeaveMonthStaff]
    cells: list[LeaveMonthCell]
    pending: list[LeavePendingItem]
    holidays: list[HolidayRead]
    closed_days: list[OfficeClosedDayRead]
    confirmation: LeaveMonthConfirmation
    compare: LeaveCompareStatus
    #: 休みの時間帯に担当が付いている訪問の件数 (今日以降・その月)。
    visits_on_leave_count: int
    #: 「実績を記録する」を押せるか (押せない理由の一覧)。
    record_actuals: LeaveRecordReadiness | None = None


class LeaveMonthSummary(BaseModel):
    """``GET /leave/month/{month}/summary`` — 職員も読める軽い月の情報.

    休みの中身・理由は返さない (申請履歴の帯とスマホの祝日用)。
    """

    month: date
    holidays: list[HolidayRead]
    #: 事業所の休み (日付と名前だけ)。
    closed_days: list[HolidayRead]
    #: 申請の締め切り (前の月の 20 日 23:59 JST の終わり = 前の月の 21 日 0:00 JST)。
    request_deadline: datetime
    #: 管理者: 表の職員の全員が確定済み／職員: 本人が確定済み。
    confirmed: bool
    #: 管理者: いちばん新しい確定の時刻／職員: 本人の確定の時刻。
    confirmed_at: datetime | None = None
    #: 管理者だけ (職員には None): 確定済みの人数と表の人数・申請中の件数
    #: (メニューの未処理件数)・必要休日数に足りない常勤の人数・カイポケで要対応の件数。
    confirmed_staff_count: int | None = None
    staff_count: int | None = None
    pending_count: int | None = None
    short_staff_count: int | None = None
    kaipoke_action_count: int | None = None
    #: 管理者だけ: 月をまたいだ休みの申請中の件数 (メニューの未処理件数)。
    pending_total: int | None = None


class LeaveDayInput(BaseModel):
    """``POST /leave/day`` — 休みを入れる・種類を変える (管理者)."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    date: date
    leave_type: LeaveTypeCode
    note: str | None = Field(default=None, max_length=500)


class LeaveDayResult(BaseModel):
    """休みを入れた・種類を変えた・承認した結果 (トーストの材料)."""

    staff_id: UUID
    date: date
    leave_type: LeaveTypeCode
    #: 今日より前の日 = 休みの記録だけ。
    past_day: bool
    #: 担当なしへ移した訪問・その患者の数。
    moved_visit_count: int
    moved_patient_count: int
    #: 打刻済み・実施中・完了などで動かさなかった訪問の数。
    skipped_visit_count: int
    #: 取消の印を付けた固定イベントの数。
    cancelled_event_count: int
    #: 本人が同行者に入っている休みの時間帯の訪問の数 (同行はそのまま)。
    accompaniment_count: int


class LeaveCancelResult(BaseModel):
    """``DELETE /leave/day`` — 取り消した休み (担当なしへ移した訪問は戻さない)."""

    staff_id: UUID
    date: date
    cancelled_type: LeaveTypeCode
    #: この休みで担当なしへ移した訪問の件数 (自動では戻らない・トーストの「N 件」)。
    moved_visit_count: int = 0


class LeaveDayBulkInput(BaseModel):
    """``POST /leave/day/bulk`` — 「この日の全員に公休を入れる」(1 トランザクション).

    ``staff_ids`` が無ければ、非常勤・型で勤務しない曜日の人・在籍でない人・その日に申請中の人・
    休みが入っている人を除く全員 (新人は含む)。
    """

    model_config = ConfigDict(extra="forbid")

    date: date
    override_type: LeaveTypeCode = "off"
    staff_ids: list[UUID] | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)


class LeaveBulkSkip(BaseModel):
    """まとめて入れるときに飛ばした人 (``already_on_leave`` = すでに休みがある・上書きしない)."""

    staff_id: UUID
    reason: Literal["already_on_leave"]
    #: いまの休みの種類。
    leave_type: LeaveTypeCode | None = None


class LeaveDayBulkResult(BaseModel):
    date: date
    override_type: LeaveTypeCode
    applied: list[LeaveDayResult]
    #: ``staff_ids`` を渡したときに飛ばした人 (省いたときは初めから対象にしないので空)。
    skipped: list[LeaveBulkSkip] = Field(default_factory=list)
    #: 担当なしへ移した訪問の合計 (管理者への通知は 1 通)。
    moved_visit_count: int


class LeaveApproveInput(BaseModel):
    """``POST /leave/requests/{id}/approve`` — ``override_type`` を渡すと種類を変えて承認."""

    model_config = ConfigDict(extra="forbid")

    override_type: LeaveTypeCode | None = None


class LeaveApproveResult(BaseModel):
    """申請 1 件の承認の結果."""

    request_id: UUID
    staff_id: UUID
    date: date
    #: 承認した中身 (``cancel`` = 休みを取り消した)。
    override_type: LeaveRequestType
    #: 休みを入れたときの結果 (``cancel`` のときは None)。
    result: LeaveDayResult | None = None


class LeaveApproveBatchInput(BaseModel):
    """``POST /leave/requests/approve-batch`` — まとめて承認 (1 トランザクション)."""

    model_config = ConfigDict(extra="forbid")

    request_ids: list[UUID] = Field(min_length=1, max_length=200)


class LeaveApproveBatchResult(BaseModel):
    approved: list[LeaveApproveResult]
    #: 担当なしへ移した訪問の合計 (管理者への通知は 1 通)。
    moved_visit_count: int


class LeaveRejectInput(BaseModel):
    """``POST /leave/requests/{id}/reject`` — 理由は必須 (本人へ通知)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class LeaveRejectResult(BaseModel):
    request_id: UUID
    target_staff_id: UUID | None = None
    target_date: date | None = None
    reason: str


class LeaveShortStaff(BaseModel):
    """休日数が足りない常勤の職員 (確定の結果)."""

    staff_id: UUID
    name: str
    holidays: float
    required_holidays: int
    shortfall: float


class LeaveConfirmInput(BaseModel):
    """``POST /leave/month/{month}/confirm`` の本文 (任意)。``staff_ids`` が無ければ表の全員."""

    model_config = ConfigDict(extra="forbid")

    staff_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=200)


class LeaveOutboundStarted(BaseModel):
    """確定から始めたカイポケへの休みの送信 (非同期のジョブ)."""

    #: 送信のジョブを始めた (画面は ``/leave/outbound/{job_id}`` を数秒おきに読む)。
    started: bool
    job_id: UUID | None = None
    #: 送る件数。
    count: int = 0
    #: 始めなかった理由 (照合がまだ・送る休みが無い・ほかのジョブが動いている など)。
    reason: str | None = None


class LeaveKinmuSheetOutcome(BaseModel):
    """作った勤務表 (予定版・確定版)。作れなくても行は「未作成」(``sheet.url`` が空) で残る."""

    created: bool
    kind: KinmuSheetKind
    sheet: KinmuSheetRead | None = None
    #: 作れなかった理由 (ドライブ未接続・繋ぎ直しが要る・勤務すべき時間数が未入力 など)。
    reason: str | None = None


#: 「実績を記録する」を押せない理由の種類 (``app/services/leave_compare.py`` の RECORD_*)。
RecordBlockerCode = Literal[
    "not_compared",
    "diffs_remaining",
    "unconfirmed_days",
    "planned_sends",
    "changed_since_compare",
    "stale_material",
    "recompare_after_choose",
    "already_recorded",
    "month_not_ended",
]


class LeaveKinmuRebuildInput(BaseModel):
    """``POST /leave/month/{month}/kinmu-sheets/rebuild`` — 勤務表を作り直す (理由は必須).

    予定版はドライブにつないだ後の「作る」にも使う (最新の版が「未作成」ならその行で作る)。
    確定版は実績を記録した月だけ (記録の時刻は変えない)。
    """

    model_config = ConfigDict(extra="forbid")

    kind: KinmuSheetKind
    reason: str = Field(min_length=1, max_length=500)


class LeaveRecordBlocker(BaseModel):
    code: RecordBlockerCode
    message: str


class LeaveRecordReadiness(BaseModel):
    """「実績を記録する」を押せるか (API の 409 と同じ判定)。"""

    ready: bool
    blockers: list[LeaveRecordBlocker] = Field(default_factory=list)
    #: 最後に実績を記録した時刻 (未記録は None)。
    actual_recorded_at: datetime | None = None


class LeaveRecordActualsResult(BaseModel):
    """``POST /leave/month/{month}/record-actuals`` — 実績を記録し、勤務表の確定版を作る."""

    month: date
    actual_recorded_at: datetime
    kinmu_sheet: LeaveKinmuSheetOutcome


class LeaveConfirmResult(BaseModel):
    """``POST /leave/month/{month}/confirm`` — 確定の記録と本人への通知.

    記録を commit したあと、別のトランザクションでカイポケへの送信のジョブを始め
    (``outbound``)、勤務表の予定版を作る (``kinmu_sheet``)。どちらが失敗しても確定は残る。
    """

    month: date
    confirmed_at: datetime
    #: 確定を記録した職員の数・通知した人数 (アカウントの無い職員には通知しない)。
    confirmed_staff_count: int
    notified_count: int
    #: その月に残っている申請中の件数。
    pending_remaining: int
    #: 休日数が足りない常勤の職員。
    short_staff: list[LeaveShortStaff]
    required_hours: float | None = None
    outbound: LeaveOutboundStarted | None = None
    kinmu_sheet: LeaveKinmuSheetOutcome | None = None
