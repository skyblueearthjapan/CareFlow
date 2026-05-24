"""Auto-schedule v2.0 (Wave 41) Pydantic schemas.

設計仕様書: ``docs/plans/auto-schedule-v2.md`` (v0.2)

エンドポイント:
  - POST /api/v1/schedule/v2/diff-add        (機能 A: 差分追加)
  - POST /api/v1/schedule/v2/full-optimize   (機能 B: 全面最適化)
  - POST /api/v1/schedule/v2/apply-individual (機能 A/B 共通: 1 件採用)
  - POST /api/v1/schedule/v2/reset-to-fixed  (機能 D: 固定枠に戻す)

実装方針:
  - auto_allocator_v2 の戻り値 dict と 1:1 対応する shape にする.
  - フロントエンド (frontend/lib/schemas/v2/auto_schedule_v2.ts; 後続作成) と
    完全一致させる。値を変える場合は両側同時に更新.
  - 採用フローは Q2 確定で **1 件ずつ** (一括採用なし).
"""

from __future__ import annotations

import uuid
from datetime import time as time_cls
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared sub-schemas
# ---------------------------------------------------------------------------

AmPmV2 = Literal["am", "pm", "any"]

# W41 v2 拡張 (構造化警告): UI で曜日タブ振り分け + 「⏰ 固定時間を変更」アクション
# を出すために warning を type / patient_id / suggested_time 等で構造化する.
V2WarningTypeOut = Literal[
    "same_address_consolidation",
    "course_capacity",
    "course_long_distance",
    "course_count",
    "acceptance_blocked",
    # W41 v2 拡張 (警告 type の分離): UI 分類のため travel_time_shortage /
    # two_staff_shortage を course_long_distance / course_count から切り出す.
    "travel_time_shortage",
    "two_staff_shortage",
    # W41 v2 (クロスレビュー修正): diff_add の衝突回避警告
    # (既存 PFV × pool 重複 + pool 内同患者同曜日重複).
    "diff_add_conflict",
    # CareFlow Wave Next 2 cross-review [H2]: staff_shifts 未投入の data-health 警告.
    "data_health_staff_shifts_missing",
    # Fix E (CareFlow): 同コース異住所同時刻 2 名以上が発生した場合に
    # 後者の時刻を自動シフトする際の通知. severity 的には "info" 相当.
    "auto_time_shift_for_conflict",
    # Wave 4 (Phase C): 固定/時間帯 patient の希望時刻からの大乖離 warning.
    # 30 分超で emit (60 分超は unassigned + UnassignedReason=care_alarm_exceeded).
    "care_alarm_deviation",
    # Phase G-45: 拠点稼働曜日外で visit をスケジュールできなかった patient.
    # offices.operating_weekdays に当該 weekday が含まれない場合に emit.
    "office_closed",
    "general",
]


# Wave 4 (Phase C): V2WarningOut の集約カテゴリ (11 種 type → 6 カテゴリ).
# Backend ``auto_allocator_v2.V2WarningCategory`` と 1:1 対応.
V2WarningCategoryOut = Literal[
    "time_deviation",  # travel_time_shortage + care_alarm_deviation
    "capacity",  # course_capacity + course_count + course_long_distance + two_staff_shortage
    "acceptance",  # acceptance_blocked
    "data_quality",  # data_health_staff_shifts_missing
    "placement_info",  # same_address_consolidation + auto_time_shift_for_conflict
    "conflict",  # diff_add_conflict + general
]


class V2WarningOut(BaseModel):
    """構造化警告 (Backend ``auto_allocator_v2.V2Warning`` と 1:1 対応).

    UI 側 (``FullOptimizeDialog``):
        - ``weekday`` でタブを振り分け (None → 「曜日不問」タブ).
        - ``actionable=True`` の警告に「⏰ 固定時間を変更」ボタンを出す.
        - ``patient_id`` / ``current_time`` / ``suggested_time`` / ``time_type``
          をモーダル初期値に流す.

    P2 (構造化照合): ``affected_patient_ids`` で「この警告に影響を受ける患者」
    を構造化リストで持つ. ``unassigned_patients[].reason`` の判定に text 含み
    でなく patient_id 照合を使うためのフィールド.
    """

    model_config = ConfigDict(extra="forbid")

    type: V2WarningTypeOut
    message: str
    weekday: int | None = None
    actionable: bool = False
    patient_id: uuid.UUID | None = None
    patient_name: str | None = None
    visit_id: uuid.UUID | None = None
    current_time: str | None = None
    suggested_time: str | None = None
    time_type: str | None = None
    preferred_start: str | None = None
    preferred_end: str | None = None
    # P2: この警告が影響を与える patient_id 一覧 (例: 容量超過コース内の全患者,
    # マネージャー不足で未割当の全患者). UI で patient ハイライト等に使う.
    affected_patient_ids: list[uuid.UUID] = Field(default_factory=list)
    # Wave 4 (Phase C): 警告 type 集約カテゴリ (11 種 type を 6 カテゴリに集約).
    # UI 側でフィルタ/集計を簡略化するための add-on フィールド. ``type`` は後方互換維持.
    category: V2WarningCategoryOut = "conflict"


class V2VisitPlan(BaseModel):
    """提案の単位 (1 件の訪問予定; weekday × start_time × course)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    start_time: time_cls
    end_time: time_cls
    duration_min: int = Field(ge=1, le=480)
    course_code: str = Field(min_length=1, max_length=2)
    office_id: uuid.UUID
    am_pm: AmPmV2
    assigned_staff_id: uuid.UUID | None = None


class V2VisitForUI(BaseModel):
    """V2CourseSummary.visits 要素 — UI 表示用フィールド.

    Frontend ``frontend/lib/schemas/v2/autoScheduleV2.ts`` の
    ``v2VisitForUiSchema`` と完全一致させる.

    W41 v2 (Mode 2 UI 拡張): ``address`` + ``area_label`` を追加.
    住所文字列と「○○」(町レベル) のエリアラベルを Before/After カードに
    表示してエリア偏在を見える化する.

    W41 v2 (Mode 2 Before/After 表示拡張):
    ``time_type`` (例: "午前"/"午後"/"終日"/"固定"/"時間帯") と
    ``sex_restriction`` (例: "female_only"/"male_only"/None) を UI バッジ用に追加.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    patient_name: str
    patient_code: str | None = None
    start_time: str  # "HH:MM" or "HH:MM:SS"
    end_time: str
    duration_min: int = Field(ge=1, le=480)
    am_pm: AmPmV2
    address: str | None = None
    area_label: str | None = None  # 例: "宮野木", "幕張本郷"
    time_type: str | None = None  # 例: "午前", "午後", "終日", "固定", "時間帯"
    sex_restriction: str | None = None  # "female_only", "male_only", None
    # W41 v2 (H2 視覚化): 同住所グループ id. UI で「📍 同住所 (N 名)」連結表示用.
    # 同 (office, weekday, start_time, address_bucket) で 2 名以上なら共通 id.
    same_address_group_id: str | None = None
    # W41 v2 (UI 時間詳細表示): 患者の希望時間帯 (HH:MM 文字列).
    # time_type='時間帯' のとき範囲 (start-end), '固定' のとき開始時刻を保持.
    preferred_start: str | None = None
    preferred_end: str | None = None
    # W41 v2 拡張 (訪問間距離): 同コース内で次の patient までの直線距離 (km).
    # コース内 start_time 昇順で隣接ペアの Haversine 距離を計算. 最後の visit は null.
    # UI で「↘ 1.2km」のような曲線矢印 + 距離ラベル表示に使う.
    distance_to_next_km: float | None = None
    # W41 v2 拡張 (週限定変更): 提案中 visit の ID. apply-week-only 後に DB に
    # 入った visit を「今週限定」変更する際の識別子. 提案算出直後は null.
    visit_id: uuid.UUID | None = None
    # CareFlow #101 FE: この visit に紐づく warning (主に travel_time_shortage).
    # UI 側で赤枠ハイライト + ツールチップ表示に使う.
    # BE 側 _v2visit_to_ui で affected_patient_ids に self.patient_id を含む +
    # weekday 一致 + travel_time_shortage type のものを抽出して渡す.
    warnings: list[V2WarningOut] = Field(default_factory=list)


class V2CourseSummary(BaseModel):
    """Before/After で表示する 1 コース (=1 曜日 × 1 スタッフ枠) のサマリ.

    Note: weekday は V2WeekdayBeforeAfter が保持するので重複を避けて削除.

    W41 v2 (Mode 2 Before/After 表示拡張): ``office_name`` を追加.
    UI ヘッダーで「本店(稲毛) A コース」のように拠点名 + コース名のフル表記.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    office_id: uuid.UUID
    office_name: str | None = None
    assigned_staff_id: uuid.UUID | None = None
    visits: list[V2VisitForUI] = Field(default_factory=list)
    distance_km: float = 0.0
    visits_count: int = 0


class V2CourseContainer(BaseModel):
    """``V2WeekdayBeforeAfter.before/after`` の型付きコンテナ.

    W41 v2 final cross-review (M-Codex-2): 旧実装は ``dict`` (untyped) だったため、
    Pydantic が shape を検証できず、UI 側で ``courses`` キーの有無が常時 zod 検証任せだった.
    型付きにして API レスポンス段階で検証する.
    """

    model_config = ConfigDict(extra="forbid")

    courses: list[V2CourseSummary] = Field(default_factory=list)


class V2WeekdayBeforeAfter(BaseModel):
    """機能 B (full-optimize) で 1 曜日の Before/After を並べた構造."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    before: V2CourseContainer = Field(default_factory=V2CourseContainer)
    after: V2CourseContainer = Field(default_factory=V2CourseContainer)


class V2KpiOverall(BaseModel):
    """全週まとめての KPI 比較値 (機能 A/B 共通)."""

    model_config = ConfigDict(extra="forbid")

    total_distance_km_before: float = 0.0
    total_distance_km_after: float = 0.0
    distance_reduction_pct: float = 0.0
    courses_count_before: int = 0
    courses_count_after: int = 0
    capacity_overflows: int = 0
    h_violations: dict[str, int] = Field(default_factory=dict)


class V2ProposalDelta(BaseModel):
    """採用前後の差分サマリ (UI ポップアップで表示)."""

    model_config = ConfigDict(extra="forbid")

    distance_km: float = 0.0
    capacity: str | None = Field(default=None, description="例 '4→5'")
    course_visits_count_before: int = 0
    course_visits_count_after: int = 0


class V2BeforeAfterSummary(BaseModel):
    """1 患者の採用前後サマリ (機能 A の各 proposal で使用)."""

    model_config = ConfigDict(extra="forbid")

    course_visits_count: int = 0
    distance_km: float = 0.0


# ---------------------------------------------------------------------------
# PendingFixedTimeEdit (W41 v2 拡張: 今週限定オーバーレイ)
#
# /full-optimize や /apply-week-only に渡すと、その算出/適用処理だけで
# PFV を一時的に上書きしたかのように振る舞う. マスター (PFV / weekly_pattern)
# は変更しない. FE 側「保留変更リスト」に対応.
# ---------------------------------------------------------------------------


class PendingFixedTimeEdit(BaseModel):
    """提案段階での一時的な固定時間上書き (今週限定変更).

    /full-optimize や /apply-week-only に渡すと、その算出/適用処理だけで
    PFV を一時的に上書きしたかのように振る舞う. マスター (PFV) は変更しない.

    Fields:
        patient_id: 対象患者.
        weekday: 0=月..6=日.
        new_start: "HH:MM" 形式の開始時刻 (オーバーレイ後の preferred_start / PFV.start_time).
        new_end: "HH:MM" 形式の終了時刻. None なら既存 duration_min を保持.
        new_time_type: "固定" | "時間帯" | "午前" | "午後" | "終日" | None.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    weekday: int = Field(ge=0, le=6)
    # W41 v2 cross-review (M-Codex-3): pattern で "HH:MM" のみを許容し、API 境界で reject.
    # _parse_hhmm が len != 2 (HH:MM:SS など) を silent reject していた問題を防ぐ.
    new_start: str = Field(..., pattern=r"^\d{1,2}:\d{2}$")
    new_end: str | None = Field(default=None, pattern=r"^\d{1,2}:\d{2}$")
    new_time_type: Literal["固定", "時間帯", "午前", "午後", "終日"] | None = None


# ---------------------------------------------------------------------------
# 1) /diff-add (機能 A)
# ---------------------------------------------------------------------------


class AutoScheduleV2DiffAddRequest(BaseModel):
    """``POST /api/v1/schedule/v2/diff-add`` request."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class V2DiffAddProposal(BaseModel):
    """機能 A: プール患者 1 件の提案候補."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    patient_code: str | None = None
    suggested: V2VisitPlan
    suggested_visits: list[V2VisitPlan] = Field(
        default_factory=list,
        description="同一患者が複数曜日に展開される場合の各 visit. suggested は代表 1 件.",
    )
    before_summary: V2BeforeAfterSummary = Field(default_factory=V2BeforeAfterSummary)
    after_summary: V2BeforeAfterSummary = Field(default_factory=V2BeforeAfterSummary)
    delta: V2ProposalDelta = Field(default_factory=V2ProposalDelta)
    warnings: list[V2WarningOut] = Field(default_factory=list)


class AutoScheduleV2DiffAddResponse(BaseModel):
    """``POST /api/v1/schedule/v2/diff-add`` response."""

    model_config = ConfigDict(extra="forbid")

    proposal_batch_id: uuid.UUID
    proposals: list[V2DiffAddProposal] = Field(default_factory=list)
    kpi_overall: V2KpiOverall = Field(default_factory=V2KpiOverall)
    warnings: list[V2WarningOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2) /full-optimize (機能 B)
# ---------------------------------------------------------------------------


class AutoScheduleV2FullOptimizeRequest(BaseModel):
    """``POST /api/v1/schedule/v2/full-optimize`` request."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    # W41 v2 拡張 (今週限定オーバーレイ): PFV / weekly_pattern を一時的に上書きして再算出.
    # マスターは変更しないため、本フィールドは本算出の中でのみ作用する.
    pending_edits: list[PendingFixedTimeEdit] = Field(default_factory=list, max_length=500)


class V2IndividualProposal(BaseModel):
    """機能 B: 1 患者の Before/After 差分 (個別ポップアップ用)."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    patient_code: str | None = None
    current_pfv: list[V2VisitPlan] = Field(default_factory=list)
    proposed_pfv: list[V2VisitPlan] = Field(default_factory=list)
    delta: V2ProposalDelta = Field(default_factory=V2ProposalDelta)
    warnings: list[V2WarningOut] = Field(default_factory=list)


# P2: 未割当理由の構造化 enum. Backend ``auto_allocator_v2.UnassignedReason`` と 1:1.
# UI 分類タブ / ハイライト用. text 含み判定 (旧 "原因不明 (...のいずれか)") は撤去.
UnassignedReasonOut = Literal[
    "no_coordinates",
    "no_primary_office",
    "no_weekly_pattern",
    "acceptance_calendar",
    "course_capacity",
    "course_overflow",
    "manager_short",
    "same_address_split",
    # Phase E-3 改修 (4): 同住所 3 名以上で 3 名目以降を別コース化対象として unassigned
    # に流す (auto_allocator_v2.UnassignedReason と同期).
    "same_address_three_or_more",
    "fixed_time_conflict",
    "lunch_break",
    # Wave 4 (Phase C): 希望時刻から CARE_ALARM_UNASSIGNED_THRESHOLD_MIN (=60) 分超
    # で配置された固定/時間帯 patient. ケアアラーム閾値超過のため未割当扱い.
    "care_alarm_exceeded",
    "unknown",
]

# P2: stage = 未割当が確定した処理段階. UI で「stage4 容量超過」「stage5 マネージャー不足」
# のような表示に使う.
UnassignedStageOut = Literal[
    "stage3_set",
    "stage4_capacity",
    "stage5_course",
    "apply",
    "general",
]


class UnassignedPatient(BaseModel):
    """W41 v2 (Mode 2 UI 拡張): 全面最適化で割当不可だった患者 1 件分.

    Mode 2 (``full_optimize``) で pool に入れたが after_visits に出てこなかった
    患者を抽出して、UI で「未割当」セクションに表示する.

    P2 (構造化照合): ``reason`` を enum (``UnassignedReasonOut``) に切替え、
    ``dropped_at_stage`` で stage を明示し、``reason_detail`` に message を残す.
    旧 ``reason`` は自由記述だったため UI フィルタが効かなかった.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    patient_name: str
    patient_code: str | None = None
    # P2: enum に変更 (旧: 自由記述 str). 旧 reason の主要パターンを 10 種に集約.
    reason: UnassignedReasonOut
    # P2: 原因 message (text). reason だけで足りない詳細を UI で表示するため.
    reason_detail: str | None = None
    # P2: どの stage で未割当が確定したか.
    dropped_at_stage: UnassignedStageOut | None = None


class AutoScheduleV2FullOptimizeResponse(BaseModel):
    """``POST /api/v1/schedule/v2/full-optimize`` response."""

    model_config = ConfigDict(extra="forbid")

    proposal_batch_id: uuid.UUID
    week_proposals: list[V2WeekdayBeforeAfter] = Field(default_factory=list)
    individual_proposals: list[V2IndividualProposal] = Field(default_factory=list)
    kpi_overall: V2KpiOverall = Field(default_factory=V2KpiOverall)
    warnings: list[V2WarningOut] = Field(default_factory=list)
    # W41 v2 (Mode 2 UI 拡張): pool に入れたが after_visits に出てこなかった患者.
    # Mode 2 (full_optimize) のときのみ非空, Mode 1 (diff_add) では参照しない.
    unassigned_patients: list[UnassignedPatient] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3) /apply-individual (機能 A/B 共通; 1 件採用)
# ---------------------------------------------------------------------------


class AutoScheduleV2ApplyIndividualRequest(BaseModel):
    """``POST /api/v1/schedule/v2/apply-individual`` request.

    proposal_id (差分追加モード) または (proposal_batch_id + patient_id) の
    どちらかを指定する. confirm=true 必須.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_id: uuid.UUID | None = None
    proposal_batch_id: uuid.UUID | None = None
    patient_id: uuid.UUID | None = None
    confirm: bool = Field(default=False, description="必ず true を指定すること")
    iso_year: int | None = Field(default=None, ge=2020, le=2100)
    iso_week: int | None = Field(default=None, ge=1, le=53)
    # クライアントが提案内容を直接送る経路 (proposal_id ベースだと
    # in-memory cache が必要だが、stateless 設計のため visit_plans を送る).
    visit_plans: list[V2VisitPlan] = Field(
        default_factory=list,
        description=(
            "採用する visit 配置. ``patient_id`` を持つ全 visit を本リストで上書き. "
            "weekday × start_time が同じ既存 PFV は更新, 違うものは追加, 古いものは削除."
        ),
    )


class AutoScheduleV2ApplyIndividualResponse(BaseModel):
    """``POST /api/v1/schedule/v2/apply-individual`` response."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    applied: bool
    fixed_visit_ids: list[uuid.UUID] = Field(default_factory=list)
    # 既に同等の固定枠が存在し、no-op だった場合は ``idempotent=true`` を返す.
    idempotent: bool = False
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4) /reset-to-fixed (機能 D)
# ---------------------------------------------------------------------------


class AutoScheduleV2ResetToFixedRequest(BaseModel):
    """``POST /api/v1/schedule/v2/reset-to-fixed`` request.

    W41 v2 final cross-review (M-Codex-1): ``confirm=True`` を必須化し、UI 側で
    確認ダイアログを経由せずに直接 API 叩く誤操作を防ぐ.

    Phase G-21 T3-5: ``mode`` / ``dry_run`` を追加. デフォルトは後方互換の
    ``mode='legacy'`` / ``dry_run=False``.
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    confirm: Literal[True] = Field(
        default=True,
        description="必ず true を指定すること (UI 側で確認ダイアログ後に True 固定送信)",
    )
    mode: Literal["legacy", "auto"] = Field(
        default="legacy",
        description=(
            "Phase G-21 T3-5: reset の挙動モード. "
            "'legacy' (既定, 後方互換) = 全 PFV を pinned 扱い + apply_travel_corrections "
            "完全スキップ. "
            "'auto' = pinned PFV のみ厳守 + 非 pinned は weekly_pattern 配置経路."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Phase G-21 T3-5: True の場合は DB を変更せず件数のみ返却する. "
            "レスポンスには visits_to_insert / visits_to_skip_protected / "
            "visits_to_skip_conflict が含まれる."
        ),
    )


class AutoScheduleV2ResetToFixedResponse(BaseModel):
    """``POST /api/v1/schedule/v2/reset-to-fixed`` response."""

    model_config = ConfigDict(extra="forbid")

    visits_regenerated: int = Field(ge=0)
    visits_soft_deleted: int = Field(ge=0)
    courses_used: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    # Phase G-21 T3-5: dry_run=True の場合のみ返す件数 (mode 後方互換のため Optional).
    dry_run: bool = Field(
        default=False,
        description="True の場合は DB 未変更 + visits_to_* フィールドで件数返却",
    )
    visits_to_insert: int = Field(default=0, ge=0)
    visits_to_skip_protected: int = Field(default=0, ge=0)
    visits_to_skip_conflict: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# 5) /apply-week-only (この週だけ反映)
#
# 全面最適化の結果を **その週の visits だけ** に反映する慎重モード.
# patient_fixed_visits は更新しないので、来週からは元の固定枠ベースに戻る.
# ---------------------------------------------------------------------------


class V2PatientVisitPlans(BaseModel):
    """``apply-week-only`` で 1 患者分の採用 visit 計画."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    visit_plans: list[V2VisitPlan] = Field(default_factory=list)


class AutoScheduleV2ApplyWeekOnlyRequest(BaseModel):
    """``POST /api/v1/schedule/v2/apply-week-only`` request.

    全面最適化の結果を visits のみに反映する.
    patient_fixed_visits は更新しない (来週からは元の固定枠に戻る).
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    visit_plans_per_patient: list[V2PatientVisitPlans] = Field(default_factory=list)
    # W41 v2 拡張 (今週限定オーバーレイ): 一括反映時に保留変更を visit_plans に
    # 反映するためのオーバーレイ. 通常は visit_plans_per_patient 自体に既に
    # 反映済みだが、再算出時のメタ情報として保持できる.
    pending_edits: list[PendingFixedTimeEdit] = Field(default_factory=list, max_length=500)
    confirm: Literal[True] = Field(
        default=True,
        description="必ず true を指定すること (UI 側で確認ダイアログ後に True 固定送信)",
    )


class AutoScheduleV2ApplyWeekOnlyResponse(BaseModel):
    """``POST /api/v1/schedule/v2/apply-week-only`` response."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    visits_created: int = Field(ge=0)
    visits_soft_deleted: int = Field(ge=0)
    courses_created: int = Field(ge=0)
    visit_staff_assignments_created: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 6) /update-fixed-time-master  (W41 v2 拡張: 警告アクション)
# ---------------------------------------------------------------------------


class UpdateFixedTimeMasterRequest(BaseModel):
    """``POST /api/v1/schedule/v2/update-fixed-time-master`` request.

    patient_fixed_visits (永続マスター) と patient.weekly_pattern の preferred_*
    を一括更新する. 同住所集約警告の「マスター更新」アクションから呼ぶ.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    weekday: int = Field(ge=0, le=6)
    # W41 v2 cross-review (M-Codex-3): pattern で "HH:MM" のみを許容し、API 境界で reject.
    new_start: str = Field(..., pattern=r"^\d{1,2}:\d{2}$")
    new_end: str | None = Field(default=None, pattern=r"^\d{1,2}:\d{2}$")
    new_time_type: Literal["固定", "時間帯", "午前", "午後", "終日"] | None = None


class UpdateFixedTimeMasterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated: bool
    patient_id: uuid.UUID
    weekday: int


class UpdateFixedTimeWeekOnlyRequest(BaseModel):
    """``POST /api/v1/schedule/v2/update-fixed-time-week-only`` request.

    apply-week-only で DB に入った visit 1 件の start_time / end_time を上書き.
    マスター (PFV / weekly_pattern) は変更しない. 対象 visit は ``source`` が
    ``auto_alloc_v2*`` 系 (自動算出由来) のみ許可する.
    """

    model_config = ConfigDict(extra="forbid")

    visit_id: uuid.UUID
    # W41 v2 cross-review (M-Codex-3): pattern で "HH:MM" のみを許容し、API 境界で reject.
    new_start: str = Field(..., pattern=r"^\d{1,2}:\d{2}$")
    new_end: str | None = Field(default=None, pattern=r"^\d{1,2}:\d{2}$")


class UpdateFixedTimeWeekOnlyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated: bool
    visit_id: uuid.UUID


# ---------------------------------------------------------------------------
# 8) /unassign-all-staff (Phase G-17: 一斉未割当)
#
# 表示中の週に対して:
#   - courses.assigned_staff_id を NULL にする (course 自体は残す)
#   - visit_staff_assignments を物理 delete (visit 自体は残す)
# admin / manager のみ.
# ---------------------------------------------------------------------------


class AutoScheduleV2UnassignAllRequest(BaseModel):
    """``POST /api/v1/schedule/v2/unassign-all-staff`` request.

    Phase G-17: 表示中の週の全 Course 担当 + visit_staff_assignments を
    一括解除する. UI 側で確認ダイアログを経由してから呼び出す.
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    office_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class AutoScheduleV2UnassignAllResponse(BaseModel):
    """``POST /api/v1/schedule/v2/unassign-all-staff`` response."""

    model_config = ConfigDict(extra="forbid")

    courses_unassigned: int = Field(ge=0)
    visit_assignments_removed: int = Field(ge=0)


__all__ = [
    "AmPmV2",
    "AutoScheduleV2ApplyIndividualRequest",
    "AutoScheduleV2ApplyIndividualResponse",
    "AutoScheduleV2ApplyWeekOnlyRequest",
    "AutoScheduleV2ApplyWeekOnlyResponse",
    "AutoScheduleV2DiffAddRequest",
    "AutoScheduleV2DiffAddResponse",
    "AutoScheduleV2FullOptimizeRequest",
    "AutoScheduleV2FullOptimizeResponse",
    "AutoScheduleV2ResetToFixedRequest",
    "AutoScheduleV2ResetToFixedResponse",
    "AutoScheduleV2UnassignAllRequest",
    "AutoScheduleV2UnassignAllResponse",
    "UnassignedPatient",
    "UnassignedReasonOut",
    "UnassignedStageOut",
    "UpdateFixedTimeMasterRequest",
    "UpdateFixedTimeMasterResponse",
    "UpdateFixedTimeWeekOnlyRequest",
    "UpdateFixedTimeWeekOnlyResponse",
    "PendingFixedTimeEdit",
    "V2BeforeAfterSummary",
    "V2CourseContainer",
    "V2CourseSummary",
    "V2DiffAddProposal",
    "V2IndividualProposal",
    "V2KpiOverall",
    "V2PatientVisitPlans",
    "V2ProposalDelta",
    "V2VisitForUI",
    "V2VisitPlan",
    "V2WarningCategoryOut",
    "V2WarningOut",
    "V2WarningTypeOut",
    "V2WeekdayBeforeAfter",
]
