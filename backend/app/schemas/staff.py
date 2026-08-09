"""Staff schemas — v2 (W1-BE2: スタッフマスタを §4.2 の 9 項目に整理).

設計仕様書 v0.9 §4.2 / 実装手順書 v0.2 §2 W1-BE2 / API 契約 §2 に準拠。

削除済み (v1 → v2): can_double_team / home_address / home_lat / home_lng /
areas / max_per_day / skill_level / assignment_volume の 6 + 3 項目。

状態 (`status`): 在籍 (active) / 休職 (on_leave) / 退職 (retired) の **3 値 enum**
で厳密化 (DB は文字列のまま、Pydantic で Literal バリデート)。

W10-BE1: mentor_id 廃止、is_trainee 追加。
Phase 2 (新人同行 v1.1 §3): 旧 staff_companion_assignments 機構は撤去済み。
同行は trainee_accompaniments (スケジュール画面の同行モード) が正典。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 列挙値 (§4.2 / Wave 0-C `app.schemas.v2.staff` と同一定義)
# ---------------------------------------------------------------------------

# 業務ロール (二軸分離・2026-08-09): 実運用は staff(スタッフ) / manager(マネージャー) の 2 値。
# manager は自動割当の対象外 (埋まらないコースの救済にのみ入る)。
# 'admin' は旧データ読み取り互換のためだけに残す (新規入力の選択肢には出さない)。
# ログインのアカウント権限 (users.role: admin/staff) とは **無関係**。
RoleV2 = Literal["admin", "manager", "staff"]

# 状態: 在籍 (active) / 休職 (on_leave) / 退職 (retired) の 3 値
#   保存値は英語、UI 表示は ja-JP に変換する
StaffStatusV2 = Literal["active", "on_leave", "retired"]

# 性別: 患者の性別制限とマッチング用
SexV2 = Literal["male", "female", "unknown"]


class StaffBase(BaseModel):
    """スタッフマスタ v2 基底スキーマ (§4.2 残す 9 項目)."""

    model_config = ConfigDict(extra="forbid")

    # 基本情報
    code: str | None = Field(default=None, max_length=64, description="スタッフコード")
    name: str = Field(min_length=1, max_length=120)
    kana: str | None = Field(default=None, max_length=120)
    sex: SexV2 | None = Field(default=None, description="性別 (患者の性別制限と突合)")
    role: RoleV2 = Field(
        default="staff",
        description="業務ロール。manager は自動割当の対象外 (救済のみ)。アカウント権限とは無関係",
    )
    status: StaffStatusV2 = Field(
        default="active",
        description="在籍 (active) 以外 (on_leave / retired) は割当除外",
    )
    primary_office_id: UUID | None = Field(
        default=None,
        description="主拠点 (§4.3). プルダウン: 稲毛 / 都賀。距離計算の起点",
    )
    note: str | None = Field(default=None, description="自由記述")

    # 詳細セクション (§4.2)
    is_trainee: bool = Field(
        default=False,
        description="新人フラグ。True の場合は同行スタッフ (companion) と一緒に訪問",
    )


class StaffCreate(StaffBase):
    """POST /api/v1/staff リクエスト (W1-BE2)."""


class StaffUpdate(BaseModel):
    """PATCH /api/v1/staff/{id} リクエスト (W1-BE2). 全フィールド optional."""

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


class StaffRead(StaffBase):
    """GET /api/v1/staff{,/{id}} レスポンス (W1-BE2)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
