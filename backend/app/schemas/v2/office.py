"""OfficeV2 schemas (Wave 0-C).

設計仕様書 v0.9 §4.3 に基づく拠点マスタ v2。
v1 から大きな変更はないが、v2 では:
  - スタッフの自宅起点を廃止 → 距離計算の起点は **常に拠点住所**
  - 患者は住所から自動で拠点に紐付く (`OfficeAssigner`、W1-BE3)
  - 拠点マスタの編集は **AI 経由不可**（地理データを含むため Web フォームから）

`allowed_cities` は M2M (`office_cities`) の id list を schema に直接含める方針。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OfficeV2Base(BaseModel):
    """拠点マスタ v2 (§4.3).

    距離計算の起点として使う `address / lat / lng` を必須情報として扱う
    (実装上は nullable だが、稲毛 / 都賀の運用では必ず値が入る)。
    """

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, max_length=64, description="拠点コード (一意識別補助)")
    name: str = Field(min_length=1, max_length=120, description="例: 稲毛 / 都賀")
    prefecture: str | None = Field(default=None, max_length=8, description="都道府県")
    address: str | None = Field(
        default=None,
        description="距離計算の起点として使用 (§4.3)",
    )
    lat: float | None = Field(default=None, ge=-90, le=90, description="距離計算の起点緯度")
    lng: float | None = Field(default=None, ge=-180, le=180, description="距離計算の起点経度")
    kaipoke_name: str | None = Field(
        default=None,
        max_length=120,
        description="K-1b カイポケ「事業所名」列の正式名 (例: 訪問看護ステーションよりより)。NULL時は name",
    )
    note: str | None = Field(default=None, description="自由記述")


class OfficeV2Create(OfficeV2Base):
    """POST /api/v1/offices リクエスト (W1-BE3).

    `allowed_cities` は cities テーブルの id 配列。M2M テーブル `office_cities` に永続化される。
    1 拠点が複数市区町村を担当可能 (§4.3)。
    """

    allowed_cities: list[UUID] = Field(
        default_factory=list,
        description="担当市区町村 id リスト (M2M `office_cities`)",
    )


class OfficeV2Update(BaseModel):
    """PATCH /api/v1/offices/{id} リクエスト."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prefecture: str | None = Field(default=None, max_length=8)
    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    kaipoke_name: str | None = Field(default=None, max_length=120)
    note: str | None = None
    allowed_cities: list[UUID] | None = None


class OfficeV2Read(OfficeV2Base):
    """GET /api/v1/offices/{id} レスポンス."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    allowed_cities: list[UUID] = Field(default_factory=list)
