"""Visit monitor (PC 訪問モニター) 集計 API スキーマ — QR チェックイン Phase 3.

``GET /api/v1/monitor`` のレスポンス契約。その日の visits を visit_checkins と
突き合わせ、職員ごとに予定 / 到着 / 退出 / 滞在 / 次距離 / 実効状態
(phase + alert_level) を返す。判定アルゴリズムは ``app.services.checkin.monitor``。

GPS 座標 (``MonitorCheckin.lat/lng``) は **位置違いの地図表示** (自宅↔実 GPS の赤破線)
のために返す。本 API は admin / manager 限定 (require_role) なので、設計 §8 の
「座標を返す監査用途は admin 限定」方針と整合する (CheckinRead が座標を返さないのは
staff 向け契約のため。モニターは管理者専用)。
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MonitorThresholds(BaseModel):
    """判定に用いたしきい値 (UI のしきい値円・遅延表示用)."""

    match_m: int
    review_m: int
    accuracy_m: int
    no_show_grace_min: int
    late_min: int
    # 退出忘れ (長時間 inprogress) しきい値 (分)。Phase 4 で設定化。
    max_inprogress_min: int


class MonitorCheckin(BaseModel):
    """1 打刻 (到着 / 退出 / 未訪問) の射影."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    scanned_at: datetime
    device_time: datetime | None = None
    lat: float | None = None
    lng: float | None = None
    distance_m: float | None = None
    accuracy_m: float | None = None
    match_status: str
    reason: str | None = None
    is_override: bool = False


class MonitorAdjustment(BaseModel):
    """効いている調整 1 件 (実績の時刻を誰がいつ・なぜ合わせたか).

    訪問モニターと打刻履歴 (``VisitHistoryItem.adjustments``) で同じ形。
    """

    # 'arrival' | 'departure'
    kind: str
    # 理由コード (intercom_wait / read_later / no_read / other) と、その表示名
    # (インターホン待ち / 読み取りが後になった / 読み取りなし / その他)。理由なしは None。
    reason_code: str | None = None
    reason_label: str | None = None
    reason_text: str | None = None
    # 合わせた人 (スタッフ名。無ければ、admin への応答では利用者の email / username、
    # staff への応答では「管理者」)。
    by_name: str | None = None
    # 合わせた日時 (UTC)。
    created_at: datetime | None = None


class MonitorVisit(BaseModel):
    """1 訪問の予定 + 実績 + 実効状態."""

    visit_id: UUID
    # 訪問の担当スタッフ (= visits.primary_staff_id)。モバイル「今日の訪問」と
    # 同一ソースのため、モニターに出る担当とスタッフ端末の表示は常に一致する。
    staff_id: UUID | None = None
    staff_name: str | None = None
    # 2 名体制 (required_staff_count=2) のグルーピングキー。同一値の visit が 2 行。
    # 通常訪問は None。KPI / アラートはこの単位で 1 論理訪問に重複排除する。
    visit_group_id: UUID | None = None
    # 同行 (非破壊追加)。行ヘッダ/詳細パネルの「＋◯◯（同行）」表示用。
    # 同行リンクは JOIN 解決する (visits には書かない)。
    #
    # ``accompaniment_staff_names`` = **全件** (決定的順序: support 優先 → 名前昇順)。
    # 一般化 決定#5 で 1 訪問に複数同行者を認めたため、こちらが正。
    # ``accompaniment_staff_name`` (単数) は後方互換で先頭要素を載せ続ける。
    accompaniment_staff_name: str | None = None
    accompaniment_staff_names: list[str] = Field(default_factory=list)
    # 実績 (最新 arrival 打刻の staff)。予定担当 (staff_id) と食い違う = 代行。
    # 設計 ``docs/plans/qr-open-checkin-design.md`` §6: 予定側の担当は書き換えず、
    # 「予定した人 / 実際に行った人」を並記する。未到着は None。
    actual_staff_id: UUID | None = None
    actual_staff_name: str | None = None
    # 代行した人 = arrival 打刻者のうち担当集合の外だった最新の 1 名。
    # ``actual_staff_*`` は「最新の打刻者」なので、代行の後に担当本人が打ち直すと
    # 実績名は担当本人になる。「代行バッジ + 担当本人名」という自己矛盾表示を防ぐ
    # ため、UI はバッジの根拠 (誰が代行したか) をこちらから取る。
    # ``is_substitute`` が false のときは常に None。
    substitute_staff_id: UUID | None = None
    substitute_staff_name: str | None = None
    # 代行 = arrival 打刻者の**いずれか**が visit の担当集合 (primary/secondary/
    # mentor/assignments/新人同行) の外。UI はバーに「代行」バッジ + 代行者名を出す。
    is_substitute: bool = False
    # 予定外訪問 (visits.is_unplanned)。読み取った本人 (= 主担当) の行に「予定外」の
    # 札つきで入る (専用行は 2026-10-01 廃止・monitor-staff-rows-design §2)。
    is_unplanned: bool = False
    # 訪問のコースと札 (行 = 職員単位にしたので、コースは訪問ごとの札になった)。
    # 札 = 拠点名の 1 文字目 + コースコード (例「稲D」「都臨2」)。コース無し・予定外は None。
    course_id: UUID | None = None
    course_tag: str | None = None
    # コースの拠点 (拠点の絞り込みと札の色に使う)。コース無しは患者の主担当拠点。
    course_office_id: UUID | None = None
    course_office_name: str | None = None
    # コースの担当 (courses.assigned_staff_id) とこの訪問の担当が違う
    # (手動の付け替え manual_staff_override を除く)。札に ⚠ を出す。
    course_staff_mismatch: bool = False
    patient_id: UUID
    patient_name: str | None = None
    patient_code: str | None = None
    patient_lat: float | None = None
    patient_lng: float | None = None
    # 患者ステータス連動 Phase 3 §3-4「表示の保険」(非破壊追加・既定 None)。
    # モニターは cancelled を最初から落とすので ``source`` は基本 planned の出所だが、
    # 残骸点検のため visits.source をそのまま載せる。``patient_status`` が active
    # 以外なら不整合 (バッジ表示)。
    source: str | None = None
    patient_status: str | None = None
    # 患者ステータスが今の値になった日 (JST)。FE はこの日以降の予定にだけバッジを
    # 出す (PO フィードバック 2026-09-10)。未記録 (mig 0082 以前) は None。
    patient_status_since: date | None = None
    # 予定 (JST 壁時計の "HH:MM").
    start_time: str
    end_time: str
    # 実効状態 (集計時に合成). UI が色とバー形を決める。
    phase: str  # future | awaiting | inprogress | done | missing
    alert_level: str  # none | review | mismatch | missing
    # 同住所・同時刻ペアの後攻が相方の完了を待っている間 (予定 + grace は過ぎたが
    # ペア補正で awaiting に留まっている)。UI が「ペア待ち」バッジを出す。phase は
    # awaiting のまま (列挙は増やさない)。
    pair_waiting: bool = False
    # 生の打刻 (位置判定・座標・理由)。実績の時刻を合わせても書き換わらない。
    # 読み取りの無い退出 (手で入れた時刻) は ``departure`` が None のまま
    # ``departure_at`` だけが入る。
    arrival: MonitorCheckin | None = None
    departure: MonitorCheckin | None = None
    no_show: MonitorCheckin | None = None
    # 実績時刻 (調整があれば調整後、無ければ読取時刻。設計
    # ``actual-time-adjust-design-2026-09-30.md`` §6-3)。バーの位置・時刻の表示は
    # こちらを使う。``phase`` / ``alert_level`` / ``stay_minutes`` /
    # ``arrival_delay_min`` もこの時刻が基準。
    arrival_at: datetime | None = None
    departure_at: datetime | None = None
    # 読取時刻 (QR を読んだ時刻)。読み取りが無ければ None。
    arrival_read_at: datetime | None = None
    departure_read_at: datetime | None = None
    arrival_adjusted: bool = False
    departure_adjusted: bool = False
    # 読み取りの無い退出 (手で入れた時刻)。
    departure_manual: bool = False
    # 効いている調整 (到着 → 退出の順)。無ければ空配列。
    adjustments: list[MonitorAdjustment] = Field(default_factory=list)
    # 到着〜退出 (進行中は now 迄)。実績時刻の差。
    stay_minutes: int | None = None
    # 到着ズレ (実績の到着 - 予定開始, 分。早着は負)。
    arrival_delay_min: int | None = None
    # 同スタッフ同日の次訪問までの直線距離 (m)。
    distance_to_next_m: float | None = None
    # 表示用の理由 (未訪問の理由 ?? 到着の理由)。
    reason: str | None = None
    # 「確認済み」(visit 単位の review)。reviewed なら要対応トレイから外れ
    # (alert_level を抑制)、タイムラインに「確認済」印が付く (Phase 5-3)。
    reviewed: bool = False
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None = None
    review_comment: str | None = None


class MonitorCourseTag(BaseModel):
    """行ヘッダに並べるコースの札 1 つ (その人がこの日持つコース)."""

    # 拠点名の 1 文字目 + コースコード (例「稲D」)。
    label: str
    course_id: UUID
    office_id: UUID | None = None
    office_name: str | None = None


class MonitorDayOverride(BaseModel):
    """その日の休み・時間変更 (staff_weekly_overrides) — 行の帯と見出しに使う."""

    # 'off' (休み) | 'custom_time' (時間変更)
    kind: str
    start_time: str | None = None
    end_time: str | None = None
    reason: str | None = None


class MonitorStaffRow(BaseModel):
    """行 = 職員単位 (2026-10-01・monitor-staff-rows-design-2026-09-30.md).

    行キー = 訪問の担当 (``visits.primary_staff_id``。空ならコース担当へフォールバック)。
    どちらも無い訪問は「担当なし」行 (``staff_id`` = None) に集まる。訪問が無くても、
    その日にイベント・休み・時間変更がある在籍中の職員は行になる (``visits`` = [])。

    名前は互換のため据え置き。``course_id`` / ``course_label`` / ``course_staff_id`` /
    ``course_staff_name`` は項目だけ残して**常に None** (コースは訪問ごとの札になった)。
    """

    # 互換のため残す (常に None)。
    course_id: UUID | None = None
    course_staff_id: UUID | None = None
    course_staff_name: str | None = None
    # 行の職員。「担当なし」行は None。
    staff_id: UUID | None = None
    staff_name: str | None = None
    # ``[staff_id]`` (互換。イベント帯の取得に使う)。「担当なし」行は []。
    staff_ids: list[UUID] = []
    # 職員の所属拠点 (staff.primary_office_id)。
    office_id: UUID | None = None
    office_name: str | None = None
    # 互換のため残す (常に None)。
    course_label: str | None = None
    # その人がこの日持つコースの札 (重複なし・初出順)。
    course_tags: list[MonitorCourseTag] = Field(default_factory=list)
    # 主担当の訪問 (時刻順)。
    visits: list[MonitorVisit]
    # 同行・副担当として関わる訪問の id (主担当の行にある訪問)。画面は薄く描く。
    companion_visit_ids: list[UUID] = Field(default_factory=list)
    # その日の休み・時間変更 (無ければ None)。
    day_override: MonitorDayOverride | None = None


class MonitorOffice(BaseModel):
    """フィルタチップ用の拠点 (当日の訪問のコース拠点 ∪ 行の職員の所属拠点)."""

    id: UUID
    name: str
    # 拠点の略称 (札・凡例の 1 文字目)。offices.short_label、未設定なら拠点名の 1 文字目。
    short_label: str | None = None


class MonitorResponse(BaseModel):
    """``GET /api/v1/monitor`` レスポンス."""

    date: date
    # サーバ現在時刻 (UTC, ISO)。UI の「今」ライン・相対表示の基準。
    now: datetime
    thresholds: MonitorThresholds
    offices: list[MonitorOffice]
    # 札の色の基準 = 拠点マスタの安定した順 (sort_order → 名前 → id)。その日の
    # ``offices`` の並びだと、都賀しか出ない日に都賀が普段の稲毛の色になるため別に持つ。
    office_order: list[UUID] = []
    staff: list[MonitorStaffRow]


class NearbyPatient(BaseModel):
    """近隣患者宅候補 (場所違いの「〇〇様宅？」表示用)."""

    patient_id: UUID
    name: str
    code: str | None = None
    lat: float
    lng: float
    distance_m: float


class NearbyResponse(BaseModel):
    """``GET /api/v1/monitor/nearby`` レスポンス."""

    items: list[NearbyPatient]


__all__ = [
    "MonitorAdjustment",
    "MonitorCheckin",
    "MonitorCourseTag",
    "MonitorDayOverride",
    "MonitorOffice",
    "MonitorResponse",
    "MonitorStaffRow",
    "MonitorThresholds",
    "MonitorVisit",
    "NearbyPatient",
    "NearbyResponse",
]
