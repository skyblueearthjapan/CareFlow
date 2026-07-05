"""StaffV2 schemas (Wave 0-C / W10-BE1).

設計仕様書 v0.9 §4.2 に基づき、v2 で残す項目のみを保持する。
削除する 6 項目 (can_double_team / 自宅住所 + lat/lng / 得意エリア /
1日最大訪問数 / スキル / 割付ボリューム) は v2 schema には含めない。

W10-BE1: mentor_id 廃止 → is_trainee 追加。
同行スタッフ管理は staff_companion_assignments テーブルへ移行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 役割 (§4.2):
#   admin = 全権 / manager (= M1) = 管理者枠 (割当対象外) / staff = 通常スタッフ
RoleV2 = Literal["admin", "manager", "staff"]

# 状態 (§4.2 確定: 在籍 / 休職 / 退職 の 3 値):
#   active = 在籍 / on_leave = 休職 / retired = 退職
#   保存値は英語、UI 表示は ja-JP に変換する。
StaffStatusV2 = Literal["active", "on_leave", "retired"]

# 性別 (§4.2):
SexV2 = Literal["male", "female", "unknown"]

# K-1b カイポケ「職種」列の看護/リハ資格。role (システム権限) とは独立。
QualificationV2 = Literal["看護師", "准看護師", "理学療法士", "作業療法士", "言語聴覚士"]


class StaffV2Base(BaseModel):
    """スタッフマスタ v2 (§4.2 残す項目).

    削除済み (v2 schema に含めない): can_double_team, home_address,
    home_lat, home_lng, areas, max_per_day, skill_level, assignment_volume。
    W10-BE1: mentor_id 廃止 → is_trainee 追加。
    """

    model_config = ConfigDict(extra="forbid")

    # 基本情報
    code: str | None = Field(default=None, max_length=64, description="スタッフコード (一意識別)")
    name: str = Field(min_length=1, max_length=120, description="必須")
    kana: str | None = Field(default=None, max_length=120, description="必須・並び順")
    sex: SexV2 | None = Field(
        default=None,
        description="患者の性別制限とマッチング (§4.2 / §5.4 ハード制約)",
    )
    role: RoleV2 = Field(
        default="staff",
        description="manager (= M1) は割当対象外 (§3.6.5)",
    )
    status: StaffStatusV2 = Field(
        default="active",
        description="在籍 (active) 以外 (on_leave / retired) は割当除外",
    )
    primary_office_id: UUID | None = Field(
        default=None,
        description=(
            "主拠点 (§4.3). プルダウン選択 (稲毛 / 都賀)。"
            "距離計算の起点はスタッフが選択した主拠点の住所"
        ),
    )

    note: str | None = Field(default=None, description="自由記述")

    # 詳細セクション (§4.2)
    is_trainee: bool = Field(
        default=False,
        description="新人フラグ。True の場合は同行スタッフ (companion) と一緒に訪問",
    )
    qualification: QualificationV2 | None = Field(
        default=None,
        description="K-1b カイポケ「職種」列 (看護師/准看護師…)。カイポケ転記専用",
    )


class StaffV2Create(StaffV2Base):
    """POST /api/v1/staff リクエスト (W1-BE2)."""


class StaffV2Update(BaseModel):
    """PATCH /api/v1/staff/{id} リクエスト (W1-BE2).

    全フィールド optional.
    """

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kana: str | None = Field(default=None, max_length=120)
    sex: SexV2 | None = None
    role: RoleV2 | None = None
    status: StaffStatusV2 | None = None
    primary_office_id: UUID | None = None
    note: str | None = None
    is_trainee: bool | None = None
    qualification: QualificationV2 | None = None


class StaffV2Read(StaffV2Base):
    """GET /api/v1/staff/{id} レスポンス (W1-BE2)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
