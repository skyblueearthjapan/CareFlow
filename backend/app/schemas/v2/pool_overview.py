"""Stage P-2 (プール投入の個別化) Pydantic v2 schemas.

エンドポイント:
  - POST /api/v1/schedule/v2/pool-overview (admin/manager)

保留プールの患者それぞれについて「最良候補 (delta 最小) 1 件」を軽量計算して返す
read-only API. FE は「効果順」一覧を出し、一括プール投入の俯瞰価値を個別フローで代替する
(設計書 ``docs/plans/pool-unification-design.md`` Stage P-2).

実装方針:
  - 候補生成は propose-slots と同一 (``compute_all_proposed_slots`` を再利用) し、
    delta (``marginal_cost_minutes``) の物差しも診断/改善提案/範囲最適化と揃える.
  - ``patient_ids`` は FE が保留プール表示から抽出して送る (プール導出ロジックの二重実装をしない).
  - サイズガード: ``len(patient_ids) > POOL_OVERVIEW_MAX_PATIENTS`` は 422.
  - DB 書込なし (最良候補算出のみ).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

# サイズガード: 1 リクエストで扱えるプール患者数の上限.
# 患者ごとに全コース × 希望曜日でソルバを回すため、暴発 (患者数 × コース数 × フルロード)
# を避ける. 20 名で数秒以内が目安 (設計書 Stage P-2).
POOL_OVERVIEW_MAX_PATIENTS: int = 50


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class PoolOverviewRequest(BaseModel):
    """``POST /v2/pool-overview`` リクエスト (対象週 × 拠点 × プール患者 id 列)."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(..., ge=2020, le=2100)
    iso_week: int = Field(..., ge=1, le=53)
    # 対象拠点 (単一). None = 全拠点 (propose-slots の office_ids=[] と同じ挙動).
    office_id: uuid.UUID | None = Field(
        default=None, description="対象拠点. None なら全拠点を対象にする"
    )
    # 保留プール患者 id 列 (FE が抽出して送る). 空なら items=[] を返す.
    patient_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("patient_ids")
    @classmethod
    def _guard_size(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(v) > POOL_OVERVIEW_MAX_PATIENTS:
            raise ValueError(
                f"patient_ids は最大 {POOL_OVERVIEW_MAX_PATIENTS} 件までです "
                f"(received: {len(v)} 件)"
            )
        return v


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class PoolOverviewBestSlot(BaseModel):
    """患者の最良候補 (delta 最小) 1 件の要約 (FE の効果順表示 / 展開導線用)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(..., ge=0, le=6, description="0=Mon..6=Sun")
    course_code: str = Field(..., description="A..E / M..M9")
    office_id: uuid.UUID
    start_time: str = Field(..., description="HH:MM:SS")
    end_time: str = Field(..., description="HH:MM:SS")


class PoolOverviewItem(BaseModel):
    """プール患者 1 名分の最良候補サマリ."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    best_slot: PoolOverviewBestSlot | None = Field(
        default=None, description="delta 最小の候補. 入れる場所が無ければ null"
    )
    best_delta_minutes: float | None = Field(
        default=None,
        description="best_slot の挿入厳密限界コスト (分/週). 未計算 (下位候補のみ) なら null",
    )
    candidate_count: int = Field(..., ge=0, description="提案可能な候補総数. 0 = 入れる場所なし")
    top_excluded_reason: str | None = Field(
        default=None,
        description="candidate_count=0 のとき除外理由集約の最多 reason. それ以外は null",
    )


class PoolOverviewResponse(BaseModel):
    """``POST /v2/pool-overview`` レスポンス (患者ごとの最良候補一覧)."""

    model_config = ConfigDict(extra="forbid")

    items: list[PoolOverviewItem] = Field(default_factory=list)


__all__ = [
    "POOL_OVERVIEW_MAX_PATIENTS",
    "PoolOverviewBestSlot",
    "PoolOverviewItem",
    "PoolOverviewRequest",
    "PoolOverviewResponse",
]
