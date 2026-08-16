"""Visit monitor (PC 訪問モニター) 集計 API スキーマ — QR チェックイン Phase 3.

``GET /api/v1/monitor`` のレスポンス契約。その日の visits を visit_checkins と
突き合わせ、スタッフ (= コース) ごとに予定 / 到着 / 退出 / 滞在 / 次距離 / 実効状態
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
    # 予定外訪問 (visits.is_unplanned)。専用行「📌予定外訪問」に集約される。
    is_unplanned: bool = False
    patient_id: UUID
    patient_name: str | None = None
    patient_code: str | None = None
    patient_lat: float | None = None
    patient_lng: float | None = None
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
    arrival: MonitorCheckin | None = None
    departure: MonitorCheckin | None = None
    no_show: MonitorCheckin | None = None
    # 到着〜退出 (進行中は now 迄)。device_time 優先で逆転対策。
    stay_minutes: int | None = None
    # 到着ズレ (到着 - 予定開始, 分。早着は負)。
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


class MonitorStaffRow(BaseModel):
    """行 = コース単位 (2026-07-10 PO要望でスタッフ単位から変更).

    フィールド名は互換のため据え置き。``staff_id`` は行内の担当が 1 名のときのみ
    その id (複数名の掛け持ち行は None)。``staff_name`` は「・」連結の表示用。
    コース無し visit の行は従来どおり担当スタッフ単位で作る (course_id=None)。
    """

    course_id: UUID | None = None
    # スケジュール側のコース担当 (= courses.assigned_staff_id。スケジュール画面と同一ソース)。
    # 訪問側の担当 (staff_ids) と食い違う場合は UI が ⚠ を出す (原則③ ズレは隠さない)。
    course_staff_id: UUID | None = None
    course_staff_name: str | None = None
    staff_id: UUID | None = None
    staff_name: str | None = None
    # 行内の担当スタッフ id 集合 (イベント帯・性別バッジ用)。
    staff_ids: list[UUID] = []
    office_id: UUID | None = None
    office_name: str | None = None
    course_label: str | None = None
    visits: list[MonitorVisit]


class MonitorOffice(BaseModel):
    """フィルタチップ用の拠点 (当日 visits に登場する拠点のみ)."""

    id: UUID
    name: str


class MonitorResponse(BaseModel):
    """``GET /api/v1/monitor`` レスポンス."""

    date: date
    # サーバ現在時刻 (UTC, ISO)。UI の「今」ライン・相対表示の基準。
    now: datetime
    thresholds: MonitorThresholds
    offices: list[MonitorOffice]
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
    "MonitorCheckin",
    "MonitorOffice",
    "MonitorResponse",
    "MonitorStaffRow",
    "MonitorThresholds",
    "MonitorVisit",
    "NearbyPatient",
    "NearbyResponse",
]
