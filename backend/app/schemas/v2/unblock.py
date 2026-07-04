"""詰まり解消相談 API (W-12d BE) Pydantic v2 schemas.

エンドポイント:
  - POST /api/v1/schedule/v2/propose-unblock         (admin/manager, read-only)
  - POST /api/v1/schedule/v2/propose-unblock/apply   (admin/manager, 1 TX 書込)

設計書: docs/plans/unblock-consult-design.md §2.2.

方針:
  - 候補情報は propose-slots と同形 (address/lat/lng + 希望 + 拠点 + 週) を踏襲する。
  - ``time`` は HH:MM 文字列でやり取りする (propose_slots と同一作法)。
  - 全モデル ``extra="forbid"`` (既存 v2 作法)。後方互換のため追加フィールドは default 付き。
"""

from __future__ import annotations

import uuid
from datetime import time as time_cls

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.v2.propose_slots import (
    ProposeTimeType,
    ProposeVisitFrequency,
    WeekdayCode,
    _parse_hhmm,
)

# ---------------------------------------------------------------------------
# 候補 (propose-slots と同形の入力)
# ---------------------------------------------------------------------------


class _UnblockCandidateBase(BaseModel):
    """対象患者の希望 (propose-slots と同形) + 対象週 / 拠点.

    ``existing_patient_id`` は apply で対象患者を特定するのに使う (適用には必須)。
    propose-unblock は「個別提案 (プール患者クリック) の候補 0 件」から呼ぶため、
    通常は既存患者 (プール患者) の id が入る。
    """

    model_config = ConfigDict(extra="forbid")

    # --- 候補の座標確定 (address 優先 → lat/lng) ---
    address: str | None = Field(default=None, description="候補患者の住所 (geocode 対象)")
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lng: float | None = Field(default=None, ge=-180.0, le=180.0)

    # --- 候補の希望 (患者マスタ項目に準拠) ---
    service_minutes: int = Field(..., ge=1, le=480, description="訪問サービス時間 (分)")
    time_type: ProposeTimeType | None = Field(default=None)
    preferred_start: str | None = Field(default=None, description="HH:MM")
    preferred_end: str | None = Field(default=None, description="HH:MM")
    preferred_weekdays: list[WeekdayCode] = Field(default_factory=list)
    # visit_frequency / frequency_per_week は探索には未使用だが propose-slots と同形にするため受ける
    # (FE が同じ candidate 情報を送るため。extra=forbid で 422 にしない).
    visit_frequency: ProposeVisitFrequency | None = Field(default=None)
    frequency_per_week: int | None = Field(default=None, ge=1, le=7)
    requires_multiple_staff: bool = Field(default=False)
    sex_restriction: str | None = Field(default=None)
    existing_patient_id: uuid.UUID | None = Field(
        default=None, description="対象患者 (プール患者) の id。propose 時に指定する。"
    )

    # --- 対象週 / 拠点 ---
    iso_year: int = Field(..., ge=2020, le=2100)
    iso_week: int = Field(..., ge=1, le=53)
    office_id: uuid.UUID = Field(..., description="詰まり解消の対象拠点 (office スコープ)")

    @field_validator("preferred_start", "preferred_end")
    @classmethod
    def _validate_hhmm(cls, v: str | None) -> str | None:
        _parse_hhmm(v)  # 形式チェックのみ (実体変換は endpoint 側).
        return v


class ProposeUnblockRequest(_UnblockCandidateBase):
    """``POST /v2/propose-unblock`` リクエスト (read-only)."""

    limit: int = Field(default=5, ge=1, le=20, description="返却プラン数の上限 (上位 N 件)")


# ---------------------------------------------------------------------------
# プラン (レスポンス sub-models)
# ---------------------------------------------------------------------------


class UnblockSlotRef(BaseModel):
    """プラン内の枠参照 (曜日 / コード / 開始時刻).

    apply では simulate レスポンスの plan をそのままエコーバックするため、FE が持たせうる
    追加フィールド (course_label 等) を ``extra="ignore"`` で受け流す (契約の後方互換)。
    """

    model_config = ConfigDict(extra="ignore")

    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    course_code: str = Field(..., description="A..E / M..M9")
    course_label: str | None = Field(default=None, description="拠点+コード (BE 未送出なら null)")
    start_time: str = Field(..., description="HH:MM")


class UnblockMoveItem(BaseModel):
    """既存訪問を 1 手ずらす (退避)."""

    # ``from`` は予約語のため ``from_`` を alias で受ける (populate_by_name で内部名も許容).
    # plan は apply でエコーバックされるため extra=ignore で FE 追加フィールドを受け流す.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    patient_id: uuid.UUID
    patient_name: str
    from_: UnblockSlotRef = Field(..., alias="from")
    to: UnblockSlotRef
    delta_minutes: int | None = Field(
        default=None, description="退避で増える移動 (分/週). 正 = 乱れ増 / 負 = 改善."
    )
    within_preference: bool = Field(
        default=False, description="退避先が患者の希望訪問スケジュール範囲内か"
    )


class UnblockInsertItem(BaseModel):
    """対象患者を配置する枠 (2 名体制なら partner_course_code に相方コース)."""

    model_config = ConfigDict(extra="ignore")

    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    course_code: str = Field(..., description="A..E / M..M9")
    course_label: str | None = Field(default=None, description="拠点+コード (BE 未送出なら null)")
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    partner_course_code: str | None = Field(
        default=None, description="2 名体制ペアの相方 (slot1) コース。通常配置は null。"
    )
    partner_course_label: str | None = Field(default=None, description="相方コースの表示ラベル")


class UnblockPlanItem(BaseModel):
    """開通プラン 1 件 (moves を適用 → insert に対象を配置).

    ``plan_id`` は ``{対象患者id}:{内容ハッシュ}`` 形式の決定的文字列。FE はこれを不透明な
    キーとして round-trip し、apply は先頭の対象患者 id を取り出して配置対象を復元する
    (apply 契約が candidate 情報を持たないため。§2.2 の plan エコーバックだけで自足させる)。
    """

    model_config = ConfigDict(extra="ignore")

    plan_id: str = Field(..., description="{対象患者id}:{内容ハッシュ} の決定的文字列")
    moves: list[UnblockMoveItem] = Field(default_factory=list)
    insert: UnblockInsertItem
    total_delta_minutes: int = Field(default=0, description="全手の delta 合計 (分/週)")
    moved_count: int = Field(default=0, ge=0, description="動かす既存訪問の数 (= len(moves))")


class UnblockUnmovableSummary(BaseModel):
    """動かせない事情の内訳 (N-6「黙って諦めない」). real-blocker 単位のカウント."""

    model_config = ConfigDict(extra="forbid")

    pinned: int = Field(default=0, ge=0, description="ピン留めで動かせない real-blocker 数")
    locked: int = Field(default=0, ge=0, description="可動域=完全固定で動かせない数")
    two_staff: int = Field(default=0, ge=0, description="2 名体制で v1 は動かさない数")
    pair: int = Field(default=0, ge=0, description="同住所ペアの片割れで動かさない数")
    dismissed: int = Field(default=0, ge=0, description="却下記憶で退避を出さなかった数")
    confirmation_required: int = Field(
        default=0,
        ge=0,
        description="退避先が希望外+movability不明で患者確認が要るため出さなかった数",
    )


class ProposeUnblockResponse(BaseModel):
    """``POST /v2/propose-unblock`` レスポンス (read-only)."""

    model_config = ConfigDict(extra="forbid")

    plans: list[UnblockPlanItem] = Field(default_factory=list)
    unmovable_summary: UnblockUnmovableSummary = Field(default_factory=UnblockUnmovableSummary)
    state_token: str = Field(..., description="apply 用の楽観ロック指紋 (sha256, office スコープ)")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class ProposeUnblockApplyRequest(BaseModel):
    """``POST /v2/propose-unblock/apply`` リクエスト (1 TX / all-or-nothing).

    ``plan`` は simulate レスポンスの 1 件をそのままエコーバックする。
    ``target_patient_id`` は明示フィールドとして必須 (plan_id 先頭の UUID と照合し、
    さらにサーバが plan 内容から plan_id を再導出してハッシュ指紋の一致を検証する)。
    ``state_token`` はサーバが再計算し不一致なら 409 (詰まり状況が変わった = 再探索が必要)。
    """

    model_config = ConfigDict(extra="forbid")

    office_id: uuid.UUID
    iso_year: int = Field(..., ge=2020, le=2100)
    iso_week: int = Field(..., ge=1, le=53)
    plan: UnblockPlanItem
    state_token: str = Field(..., min_length=1)
    target_patient_id: uuid.UUID = Field(
        ...,
        description="配置対象患者の UUID (plan_id 先頭との一致 + ハッシュ再導出で改竄を検出).",
    )


class ProposeUnblockApplyResponse(BaseModel):
    """``POST /v2/propose-unblock/apply`` レスポンス."""

    model_config = ConfigDict(extra="forbid")

    applied_moves: int = Field(..., ge=0, description="適用した退避手数 (= len(plan.moves))")
    inserted: bool = Field(..., description="対象患者を配置できたか")
    warnings: list[str] = Field(
        default_factory=list,
        description="N-4 再検証で検出した非致命の警告 (現場向け日本語). ブロックしない.",
    )


def parse_hhmm_or_none(value: str | None) -> time_cls | None:
    """HH:MM を time に変換する薄いラッパ (endpoint から再利用)."""
    return _parse_hhmm(value)


__all__ = [
    "ProposeUnblockApplyRequest",
    "ProposeUnblockApplyResponse",
    "ProposeUnblockRequest",
    "ProposeUnblockResponse",
    "UnblockInsertItem",
    "UnblockMoveItem",
    "UnblockPlanItem",
    "UnblockSlotRef",
    "UnblockUnmovableSummary",
]
