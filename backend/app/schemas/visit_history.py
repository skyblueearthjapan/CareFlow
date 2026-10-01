"""打刻履歴 API (``/api/v1/visit-history``) のスキーマ.

正典設計書: ``docs/plans/visit-history-design-2026-09-30.md`` §2 / §3 と
``docs/plans/actual-time-adjust-design-2026-09-30.md`` §6-3 (実績の時刻を合わせる)。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.visit_monitor import MonitorAdjustment

VisitHistoryState = Literal["done", "in_progress", "no_departure", "none", "future"]


class VisitHistoryItem(BaseModel):
    """1 行 = 1 訪問 (``services/checkin/history.HistoryRow`` から組む)。"""

    model_config = ConfigDict(from_attributes=True)

    visit_id: UUID
    visit_date: date
    # 患者の主担当拠点。
    office_id: UUID | None = None
    office_name: str | None = None
    patient_id: UUID
    patient_name: str | None = None
    # 予定 ("HH:MM")。予定外の訪問 (``is_unplanned``) は null。
    start_time: str | None = None
    end_time: str | None = None
    # 予定の担当 = 主担当。主担当が空 (かつ手動の付け替えでない) の訪問はコース担当
    # (``VisitRead.staff_name`` と同じ規則)。予定外の訪問は null。
    planned_staff_id: UUID | None = None
    planned_staff_name: str | None = None
    # 実際に訪問した人 = 最新の到着の打刻者 (無ければ最新の退出の打刻者)。
    actual_staff_id: UUID | None = None
    actual_staff_name: str | None = None
    # 到着・退出の実績時刻 (UTC)。調整があれば調整後、無ければ読取時刻。無ければ null。
    arrival_at: datetime | None = None
    departure_at: datetime | None = None
    # 読取時刻 (QR を読んだ時刻・UTC)。読み取りが無ければ null。
    arrival_read_at: datetime | None = None
    departure_read_at: datetime | None = None
    arrival_adjusted: bool = False
    departure_adjusted: bool = False
    # 読み取りの無い退出 (手で入れた時刻)。
    departure_manual: bool = False
    # 圏外で退避して遅れて届いた打刻なら、その受信時刻 (UTC)。遅れていなければ null。
    arrival_late_received_at: datetime | None = None
    departure_late_received_at: datetime | None = None
    # 効いている調整 (訪問モニターと同じ形)。無ければ空配列。
    adjustments: list[MonitorAdjustment] = Field(default_factory=list)
    # 今のユーザーがこの訪問の実績を合わせられるか (権限はサーバが判定)。
    # 到着の読み取りが無い訪問は、合わせる対象が無いので false。
    adjust_allowed: bool = False
    # 到着・退出を JST の分に切り捨ててからの差。片方でも無ければ null。
    stay_minutes: int | None = None
    # 最新の到着の 'qr' / 'manual' と位置判定。到着が無ければ null。
    checkin_source: str | None = None
    match_status: str | None = None
    is_substitute: bool
    is_unplanned: bool
    # 取消または削除済み (打刻があるので載せている行)。
    is_cancelled: bool
    state: VisitHistoryState
    # 備考ラベル (語彙と順序は設計 §2)。
    remarks: list[str]


class VisitHistorySummary(BaseModel):
    """集計帯。ページングする前の絞り込み結果全体から、``future`` を除いて数える。"""

    model_config = ConfigDict(extra="forbid")

    visits: int
    with_arrival: int
    with_departure: int
    no_departure: int
    none: int
    # 実績の時刻を合わせてある訪問 (到着か退出のどちらか)。
    adjusted: int


class VisitHistoryGroup(BaseModel):
    """見出し行の件数 (``sort=staff`` / ``patient``)。ページングする前の全体から数える。"""

    model_config = ConfigDict(extra="forbid")

    # 看護師名 (実際の打刻者 ?? 予定の担当) または患者名。
    # 名前が無ければ「（担当なし）」/「（患者名なし）」。
    label: str
    count: int
    with_arrival: int


class VisitHistoryList(BaseModel):
    """一覧レスポンス。``total`` は絞り込み後の総件数 (``future`` を含む)。"""

    model_config = ConfigDict(extra="forbid")

    items: list[VisitHistoryItem]
    total: int
    summary: VisitHistorySummary
    # ``sort=date`` は空配列。
    groups: list[VisitHistoryGroup] = Field(default_factory=list)
