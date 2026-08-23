"""Visit schemas — Phase 2 CRUD payloads (W2-BE4 拡張済み).

W2-BE4 (`docs/plans/v2-allocation-redesign.md` v0.9 §3.3 / §4.5):
  - ``course_id`` (FK NULL) — Layer 2 で決定するコース所属
  - ``required_staff_count`` (1 or 2) — 2 名体制対応
  - ``visit_group_id`` (UUID NULL) — 2 名体制の訪問グルーピング

CRUD レスポンスは ``visit_staff_assignments`` 経由で割り当てられたスタッフ全員
(``staff_assignments``) を含める。1 visit あたり 1 or 2 行
(``required_staff_count`` に応じる)。

正本の v2 schema (``extra='forbid'``) は ``app.schemas.v2.visit`` にある。
本ファイルは router (旧クライアント互換のため ``extra='ignore'``) 用の薄い拡張。
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.accompaniment import AccompanimentRef
from app.schemas.v2.visit import VisitStaffAssignmentV2Read
from app.schemas.visit_checkin import CheckinRead

# v2 visit_staff_assignments の Read 型 (re-export)
VisitStaffAssignmentRead = VisitStaffAssignmentV2Read


class VisitBase(BaseModel):
    """Visit の共通フィールド (v1 互換 + v2 追加)."""

    model_config = ConfigDict(extra="ignore")

    patient_id: UUID
    primary_staff_id: UUID | None = None
    secondary_staff_id: UUID | None = None
    mentor_staff_id: UUID | None = None
    visit_date: date
    start_time: time
    end_time: time
    type: str
    status: str = "planned"
    source: str = "manual"
    # 週のピン (青ピン / PO 決定 2026-08-09)。source とは独立の軸。
    week_pinned: bool = False
    note: str | None = None
    kaipoke_id: str | None = None

    # ---- W2-BE4 v2 additions (§3.3 / §4.5) ----------------------------------
    course_id: UUID | None = Field(
        default=None,
        description="所属コース (Layer 2 で決定; CRUD では NULL のまま運用しても可)",
    )
    required_staff_count: int = Field(
        default=1,
        ge=1,
        le=2,
        description="必要スタッフ数. 1 = 通常 / 2 = 2 名体制 (§3.3)",
    )
    visit_group_id: UUID | None = Field(
        default=None,
        description=(
            "2 名体制の訪問グルーピングキー (§3.3). "
            "通常 (required_staff_count=1) は NULL, "
            "2 名体制 (required_staff_count=2) では同じ UUID を持つ visit が 2 行存在する"
        ),
    )


class VisitCreate(VisitBase):
    """POST /api/v1/visits リクエスト."""


class VisitUpdate(BaseModel):
    """PATCH /api/v1/visits/{id} リクエスト."""

    model_config = ConfigDict(extra="ignore")

    patient_id: UUID | None = None
    primary_staff_id: UUID | None = None
    secondary_staff_id: UUID | None = None
    mentor_staff_id: UUID | None = None
    visit_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    type: str | None = None
    status: str | None = None
    source: str | None = None
    note: str | None = None
    kaipoke_id: str | None = None

    # W2-BE4 v2 additions
    course_id: UUID | None = None
    required_staff_count: int | None = Field(default=None, ge=1, le=2)
    visit_group_id: UUID | None = None


class VisitCancelWeekRequest(BaseModel):
    """POST /api/v1/schedule/v2/visit-cancel-week (week-cockpit-design.md §2-2).

    「今週だけ取消」= ``visits.status`` を planned ↔ cancelled で往復させる
    (取込の delete と同一表現・行は残るので履歴が追える。csv_builder が
    cancelled を除外するため、カイポケへの送信差分は delete になる)。
    """

    model_config = ConfigDict(extra="forbid")

    visit_id: UUID
    cancel: bool
    reason: str | None = Field(default=None, max_length=200)
    # 操作ジャーナルのグルーピング (ツールバー「戻る」で 1 手として扱う)。
    op_group_id: UUID | None = None


class VisitServiceOverrideRequest(BaseModel):
    """POST /api/v1/schedule/v2/visit-service-override (設計 §2).

    「この訪問だけカイポケのサービス内容に合わせる」= ``visits
    .kaipoke_service_override`` を書き換えるだけの操作。マスタ (患者の区分 /
    スタッフの資格) には一切触れない (憲法1: 盤面操作はマスタを変えない)。

    対象の指定は **どちらか一方**:
      * ``visit_id`` — 盤面のカード (VisitActionMenu) から直接指定する経路
      * ``item_id`` — 同期バーの差分行 (CorrectionSheetItem) から指定する経路。
        BE 側で item → visit を解決する (visit_id 列があればそれ、無ければ
        日付 + 開始時刻 + 患者名で当該週の visits から引く)

    ``service_content`` が None / 空文字なら **解除** (自動判定へ戻す)。
    """

    model_config = ConfigDict(extra="forbid")

    visit_id: UUID | None = None
    item_id: UUID | None = None
    service_content: str | None = Field(default=None, max_length=64)
    # 操作ジャーナルのグルーピング (ツールバー「戻る」で 1 手として扱う)。
    op_group_id: UUID | None = None


class VisitRead(VisitBase):
    """GET /api/v1/visits/{id} レスポンス (v2 拡張済み)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    # Denormalized display names — populated by selectinload() in the router.
    # Frontend (`schedule/page.tsx`) renders these directly without a join.
    patient_name: str | None = None
    staff_name: str | None = None
    # 患者ジオコード (非破壊追加). モバイル QR チェックインのクライアント側
    # 距離プレビュー (記録前の確認) に使う. 未ジオコードの患者は None
    # (フロントは no_gps 相当としてプレビュー無しで POST 可能).
    patient_lat: float | None = None
    patient_lng: float | None = None
    # モバイル訪問カードの性別ウォッシュ/📍住所用 (R-9・非破壊追加)。
    patient_sex: str | None = None
    patient_address: str | None = None
    # visit_staff_assignments 経由の割当スタッフ一覧 (§4.5)
    # 1 visit あたり 1 or 2 行 (required_staff_count による)
    staff_assignments: list[VisitStaffAssignmentRead] = Field(default_factory=list)
    # QR チェックイン (Phase 1) の最新打刻 (非破壊追加). 未打刻なら None.
    # 既存クライアント (me.ts の MyVisit) は本フィールドを無視できる.
    latest_checkin: CheckinRead | None = None
    # 同行 (非破壊追加・R-9 の patient_sex と同じ流儀). 同行リンクは accompaniments が
    # 唯一の正典で、読み出し時に JOIN 解決する (visits.*_staff_id には書かない)。
    #
    # ``accompaniments`` = **全件** (決定的順序: support 優先 → 名前昇順)。
    # 一般化 決定#5 で 1 訪問に複数同行者を認めたため、こちらが正となる。
    # ``accompaniment`` (単数) は**後方互換**として先頭要素を載せ続ける
    # (FE 移行後に deprecate)。同行が無ければ空配列 / None。
    accompaniment: AccompanimentRef | None = None
    accompaniments: list[AccompanimentRef] = Field(default_factory=list)
    # 予定外訪問 (visits.is_unplanned / 設計 qr-open-checkin-design.md §3)。
    # ``adhoc-checkin`` がその場で生成した実績行なら true。**読み取り専用**として
    # ここ (VisitRead) にだけ置く: VisitBase に置くと POST /visits の入力になり、
    # 予定外を手で作れてしまう (生成経路は adhoc-checkin だけに閉じる)。
    is_unplanned: bool = False
    # 訪問単位のサービス内容上書き (migration 0078 / 設計 §2)。非 NULL なら
    # カイポケへ送るサービス内容がこの文字列で確定する。``is_unplanned`` と
    # 同じ理由で **VisitRead にだけ** 置く: VisitBase に置くと POST/PATCH
    # /visits の入力になり、専用 API (visit-service-override) への 1 本化
    # (admin 限定・op_log で undo 可) が崩れる。
    kaipoke_service_override: str | None = None


__all__ = [
    "AccompanimentRef",
    "CheckinRead",
    "VisitBase",
    "VisitCancelWeekRequest",
    "VisitCreate",
    "VisitRead",
    "VisitServiceOverrideRequest",
    "VisitStaffAssignmentRead",
    "VisitUpdate",
]
