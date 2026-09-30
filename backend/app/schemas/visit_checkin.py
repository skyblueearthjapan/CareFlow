"""Checkin schemas (QR 訪問チェックイン Phase 1).

``CheckinCreate``: checkin / checkout / no-show リクエスト。全フィールド任意
(設計 R1: 既存 ``me.ts`` の ``{lat,lng,at}`` をそのまま受理し、422 で弾かない)。
``at`` は ``device_time`` のエイリアスとして受理する。

``CheckinRead``: ``VisitRead.latest_checkin`` に非破壊で載せる最新打刻の射影。
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


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
    # その場で合わせた時刻の同梱 (設計 actual-time-adjust §6-2)。圏外で退避した打刻に、
    # 到着直後のカードで合わせた時刻を載せて再送するための項目。
    # **ここでは形を検証しない** (型も長さも縛らない): 退避キューの再送は 4xx で破棄
    # されるため、調整の不備で打刻そのものを 422 にしない。検証は打刻を記録した後に
    # 行い、通らなければ調整だけを黙って無視する。
    adjusted_time: str | None = Field(default=None, description="合わせた時刻 (JST の HH:MM)")
    adjust_reason_code: str | None = Field(default=None, description="合わせた理由のコード")

    @field_validator("adjusted_time", "adjust_reason_code", mode="before")
    @classmethod
    def _ignore_non_string(cls, value: object) -> str | None:
        return value if isinstance(value, str) else None


#: 実績のどちらの時刻か / 時刻を合わせた理由 (設計 actual-time-adjust §4)。
ActualTimeKind = Literal["arrival", "departure"]
AdjustReasonCode = Literal["intercom_wait", "read_later", "no_read", "other"]


class ActualTimeAdjustRequest(BaseModel):
    """PUT /visits/{id}/actual-time リクエスト (設計 actual-time-adjust §6-1).

    ``time`` は JST の ``HH:MM``。サーバが ``visit_date`` と組み合わせる (端末側で
    タイムゾーン計算をさせない)。

    **スキーマでは値を縛らない** (全項目が任意の文字列): 画面は 4xx の ``detail`` を
    そのままトーストに出すので、Pydantic の配列形式の ``detail`` を返さない。値の
    検証 (``kind`` / ``reason_code`` の語彙・``time`` の形と範囲) はサーバ側
    (``services/checkin/adjust``) で行い、日本語の文字列の ``detail`` を返す。
    語彙は ``ActualTimeKind`` / ``AdjustReasonCode``。
    """

    model_config = ConfigDict(extra="ignore")

    kind: str | None = None
    time: str | None = None
    reason_code: str | None = None
    reason_text: str | None = None

    @field_validator("kind", "time", "reason_code", "reason_text", mode="before")
    @classmethod
    def _ignore_non_string(cls, value: object) -> str | None:
        return value if isinstance(value, str) else None


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
    """GET /visits/resolve-qr/{token} の候補 1 件 (v2 = 凍結コントラクト 2026-08-16).

    正典設計書 ``docs/plans/qr-open-checkin-design.md`` §4-1。第 1 弾は自分の担当
    visit だけを返していたが、代行・予定外の記録を開放したため **その患者の当日
    (JST) visit 全件**を返す。担当かどうかは ``is_mine`` で表す。

    予定スタッフ名を担当外へ開示するのは「QR 所持 = 現地に居る」前提 (決定#1)。
    住所等の患者属性はここで増やさない。
    """

    model_config = ConfigDict(from_attributes=True)

    visit_id: UUID
    start_time: time
    end_time: time
    status: str
    # 予定担当 (visits.primary_staff) 名。未割当は None。代行の確認表示用。
    planned_staff_name: str | None = None
    # 自分の担当集合 (primary/secondary/mentor/assignments/新人同行) に入っているか。
    is_mine: bool = False
    # 既存の予定外訪問 (adhoc-checkin 生成)。二重生成の抑止・退出導線に使う。
    is_unplanned: bool = False


class QrResolveRead(BaseModel):
    """resolve-qr レスポンス (v2)。

    ``patient_name`` は「誤った利用者に記録しない」ための確認表示に必須のため
    v2 で追加した (氏名のみ・住所等は返さない)。候補ゼロ (当日予定なし) は
    200 + 空配列で、FE は予定外訪問の導線へ進む。
    """

    patient_name: str
    candidates: list[QrResolveCandidate]


__all__ = [
    "ActualTimeAdjustRequest",
    "ActualTimeKind",
    "AdjustReasonCode",
    "CheckinCreate",
    "CheckinRead",
    "QrResolveCandidate",
    "QrResolveRead",
]
