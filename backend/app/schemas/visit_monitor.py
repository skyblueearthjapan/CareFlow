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

from pydantic import BaseModel, ConfigDict


class MonitorThresholds(BaseModel):
    """判定に用いたしきい値 (UI のしきい値円・遅延表示用)."""

    match_m: int
    review_m: int
    accuracy_m: int
    no_show_grace_min: int
    late_min: int


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
    # 2 名体制 (required_staff_count=2) のグルーピングキー。同一値の visit が 2 行。
    # 通常訪問は None。KPI / アラートはこの単位で 1 論理訪問に重複排除する。
    visit_group_id: UUID | None = None
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


class MonitorStaffRow(BaseModel):
    """スタッフ (= 1 日 1 コース) 単位の行."""

    staff_id: UUID | None = None
    staff_name: str | None = None
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
