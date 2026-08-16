"""Checkin schemas (QR 訪問チェックイン Phase 1).

``CheckinCreate``: checkin / checkout / no-show リクエスト。全フィールド任意
(設計 R1: 既存 ``me.ts`` の ``{lat,lng,at}`` をそのまま受理し、422 で弾かない)。
``at`` は ``device_time`` のエイリアスとして受理する。

``CheckinRead``: ``VisitRead.latest_checkin`` に非破壊で載せる最新打刻の射影。
"""

from __future__ import annotations

from datetime import datetime, time
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class CheckinCreate(BaseModel):
    """POST /visits/{id}/checkin · /checkout · /no-show リクエスト (全任意)."""

    # 既存モバイルの未知フィールドを 422 にしないため extra='ignore'.
    # populate_by_name=True で ``device_time`` / ``at`` の双方を受理する.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    qr_token: str | None = Field(
        default=None,
        description="患者宅 QR のトークン. 無し = manual (visit.patient_id を採用)",
    )
    # 緯度経度は地理座標の有効域に制約する (範囲外は 422 で弾く)。
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    # accuracy は非負のみ許容。負値を渡して精度ガード (accuracy_m > tol) を
    # バイパスし mismatch を握り潰す穴を塞ぐ (security)。
    accuracy: float | None = Field(default=None, ge=0, description="GPS 精度 (m)")
    device_time: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("device_time", "at"),
        description="端末時刻 (ISO 8601). 既存 ``at`` を写像",
    )
    reason: str | None = Field(default=None, max_length=1000, description="場所違い / 未訪問の理由")
    is_override: bool = Field(
        default=False,
        description="不一致でも強行記録した旗",
    )


class CheckinRead(BaseModel):
    """最新チェックインの射影 (VisitRead.latest_checkin)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    kind: str
    match_status: str
    distance_m: float | None = None
    accuracy_m: float | None = None
    scanned_at: datetime
    checkin_source: str
    reason: str | None = None
    is_override: bool


class QrResolveCandidate(BaseModel):
    """GET /visits/resolve-qr/{token} の候補 1 件.

    汎用カメラのディープリンクで「本日の自分の担当 visit」へ遷移するための最小
    情報のみ。**患者名・住所は載せない** (担当 visit の存在自体は担当者が既に
    知り得る情報だが、患者属性はここで増やさない)。
    """

    model_config = ConfigDict(from_attributes=True)

    visit_id: UUID
    start_time: time
    end_time: time
    status: str


class QrResolveRead(BaseModel):
    """resolve-qr レスポンス。

    候補ゼロ (担当外患者のトークン含む) は 200 + 空配列 — 404 と区別させず
    「その患者を担当しているか」を漏らさない (存在秘匿)。
    """

    candidates: list[QrResolveCandidate]


__all__ = ["CheckinCreate", "CheckinRead", "QrResolveCandidate", "QrResolveRead"]
