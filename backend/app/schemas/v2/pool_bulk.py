"""プール一括投入 (W-1: simulate) Pydantic v2 schemas.

エンドポイント:
  - POST /api/v1/schedule/v2/pool-bulk-simulate (admin/manager, read-only)

保留プールの患者列を **逐次シミュレーション** で 1 人ずつ決定論的に積み、投入先
(placements) / 部分投入 (partial) / 投入不能 (unplaced) / 前後 KPI / before-after 週ビュー /
state_token を返す read-only API. 物差し (実現可能性 + delta 分/週) は個別提案
(propose-slots) と完全に同一 (``compute_all_proposed_slots`` を再利用).

設計書 ``docs/plans/pool-bulk-insert-design.md`` §3 (エンジン) / §4 (API 契約).

実装方針:
  - simulate は read-only (DB 書込みなし). apply (W-2) と完全分離.
  - サイズガード: ``len(patient_ids) > POOL_BULK_MAX_PATIENTS`` は 422
    (pool-overview の ``POOL_OVERVIEW_MAX_PATIENTS`` と同値).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.v2.auto_schedule_v2 import V2WeekdayBeforeAfter

# 単一ソースはエンジン. スキーマは再エクスポートのみ (二重定義排除).
from app.services.scheduling.pool_bulk_inserter import POOL_BULK_MAX_PATIENTS

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class PoolBulkSimulateRequest(BaseModel):
    """``POST /v2/pool-bulk-simulate`` リクエスト (対象週 × 拠点 × プール患者 id 列)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(..., ge=2020, le=2100)
    iso_week: int = Field(..., ge=1, le=53)
    # 対象拠点 (単一必須). 一括投入は単一拠点内で完結する (設計書 §7).
    # state_token が拠点単位の指紋のため office_id は必須.
    office_id: uuid.UUID = Field(..., description="対象拠点 (単一)")
    # 保留プール患者 id 列 (FE が抽出して送る. pool-overview と同方式).
    patient_ids: list[uuid.UUID] = Field(default_factory=list)
    # 投入順序. v1 は hybrid のみ (D-1). 将来拡張用に enum.
    ordering: Literal["hybrid"] = "hybrid"

    @field_validator("patient_ids")
    @classmethod
    def _guard_size(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(v) > POOL_BULK_MAX_PATIENTS:
            raise ValueError(
                f"patient_ids は最大 {POOL_BULK_MAX_PATIENTS} 件までです (received: {len(v)} 件)"
            )
        return v


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class PoolBulkPlacement(BaseModel):
    """投入成功した 1 枠 (患者 × 曜日 × コース). seq は投入順の通し番号."""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(..., ge=1, description="投入順の通し番号 (1..)")
    patient_id: uuid.UUID
    patient_name: str
    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    course_code: str
    office_id: uuid.UUID
    start_time: str = Field(..., description="HH:MM:SS (pool-overview と同形式)")
    service_minutes: int = Field(..., ge=1, le=480)
    delta_minutes: float | None = Field(
        default=None, description="挿入の厳密限界コスト (分/週). 個別提案と同一の物差し"
    )
    warnings: list[str] = Field(
        default_factory=list, description="propose-slots と同一語彙の警告コード (staff_absent 等)"
    )


class PoolBulkPartial(BaseModel):
    """部分投入 (不足曜日の一部だけ入った) 患者 1 名の要約."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    patient_name: str
    placed_days: int = Field(..., ge=1, description="投入できた枠数")
    missing_days: int = Field(..., ge=1, description="投入できなかった不足枠数")
    unplaced_reasons: dict[str, str] = Field(
        default_factory=dict,
        description="投入できなかった希望曜日 (str) → 理由コード (excluded_summary と同語彙)",
    )
    # A 案 (2026-07-04 PO 承認): 超過は一括に組み込まず個別の相談フロー (方式b) へ橋渡しする.
    overcapacity_available_count: int = Field(
        default=0,
        ge=0,
        description="定員起因 (capacity_full) の未充足曜日に「定員 +1 なら入る候補」がある件数 "
        "(>=1 なら方式b の相談で採用可能)",
    )


class PoolBulkUnplaced(BaseModel):
    """投入不能 (1 枠も入らなかった) 患者 1 名の要約."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    patient_name: str
    reason: str = Field(
        ...,
        description="理由コード (capacity_full/travel_shortage/lunch_window/no_gap/"
        "course_closed/no_coordinates/no_primary_office/office_mismatch/two_staff_pending)",
    )
    # A 案 (2026-07-04 PO 承認): capacity_full のとき「定員 +1 なら入る候補」件数を載せ、個別フローへ橋渡し.
    overcapacity_available_count: int = Field(
        default=0,
        ge=0,
        description="capacity_full で投入不能な患者に「定員 +1 なら入る候補」がある件数 "
        "(>=1 なら方式b の相談で採用可能)",
    )


class PoolBulkKpi(BaseModel):
    """プレビュー用サマリ KPI (投入件数 + 週全体の移動時間/距離 before→after)."""

    model_config = ConfigDict(extra="forbid")

    placed_patients: int = Field(..., ge=0, description="1 枠以上投入できた患者数")
    placed_slots: int = Field(..., ge=0, description="投入枠の合計数")
    travel_minutes_before: int = Field(..., ge=0)
    travel_minutes_after: int = Field(..., ge=0)
    travel_km_before: float = Field(..., ge=0.0)
    travel_km_after: float = Field(..., ge=0.0)


class PoolBulkSimulateResponse(BaseModel):
    """``POST /v2/pool-bulk-simulate`` レスポンス (read-only プレビュー)."""

    model_config = ConfigDict(extra="forbid")

    placements: list[PoolBulkPlacement] = Field(default_factory=list)
    partial: list[PoolBulkPartial] = Field(default_factory=list)
    unplaced: list[PoolBulkUnplaced] = Field(default_factory=list)
    # ProposalWeekCalendar / FullOptimizeDialog 互換の before/after 週ビュー.
    week_before_after: list[V2WeekdayBeforeAfter] = Field(default_factory=list)
    kpi: PoolBulkKpi
    # 楽観ロック用指紋 (apply=W-2 で再計算し不一致なら 409).
    state_token: str


# ---------------------------------------------------------------------------
# Apply (W-2)
# ---------------------------------------------------------------------------


class PoolBulkApplyRequest(BaseModel):
    """``POST /v2/pool-bulk-apply`` リクエスト (設計書 §4).

    simulate の ``placements`` をそのまま送る. **change_scope フィールドは持たせない**:
    一括投入は 1 件ずつ聞けないため反映先は pattern_and_week 固定 (D-2). state_token を
    サーバで再計算し、不一致なら 409 (simulate 後にスケジュールが変わったら必ずやり直し).
    """

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(..., ge=2020, le=2100)
    iso_week: int = Field(..., ge=1, le=53)
    office_id: uuid.UUID = Field(..., description="対象拠点 (単一・state_token の単位)")
    # simulate の placements をそのまま (プレフィックス選択は v1 なし = 全件).
    placements: list[PoolBulkPlacement] = Field(
        default_factory=list,
        max_length=350,
        description="投入対象の配置リスト (上限 350 = 最大 50 患者 × 7 曜日).",
    )
    # 楽観ロック用指紋 (simulate が返したもの). サーバ再計算と不一致なら 409.
    state_token: str
    # NG スタッフ / 性別制限の確認フロー (docs/plans/patient-ng-staff-design.md §7-2)。
    # simulate は NG 枠を提案しないが、simulate 後に NG が登録されると古い placements の
    # apply が素通りするため、**患者ループの前に全 placements をまとめて事前検査** する。
    acknowledge_constraint_warnings: bool = Field(
        default=False,
        description="NGスタッフ/性別制限の警告を確認済みとして続行する (422 の再送用)。",
    )


class PoolBulkApplyResponse(BaseModel):
    """``POST /v2/pool-bulk-apply`` レスポンス (適用サマリ)."""

    model_config = ConfigDict(extra="forbid")

    applied_patients: int = Field(..., ge=0, description="固定枠を登録できた患者数")
    applied_slots: int = Field(..., ge=0, description="登録した固定枠 (placements) の合計数")
    warnings: list[str] = Field(
        default_factory=list,
        description="pfv_validator V3-V5 (衝突/昼休み/容量) の warning 文言 (ブロックしない)",
    )


__all__ = [
    "POOL_BULK_MAX_PATIENTS",
    "PoolBulkApplyRequest",
    "PoolBulkApplyResponse",
    "PoolBulkKpi",
    "PoolBulkPartial",
    "PoolBulkPlacement",
    "PoolBulkSimulateRequest",
    "PoolBulkSimulateResponse",
    "PoolBulkUnplaced",
]
