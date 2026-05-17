"""Pydantic schemas for patient sync endpoints (週 visits → 固定枠 反映).

POST /api/v1/patients/{patient_id}/sync-week-visits-to-fixed
    今週の visits (visit_date が iso_year × iso_week に含まれる active 行) を
    PFV (patient_fixed_visits, mode='normal', slot_index=0) に upsert する。

設計判断:
    - 既存 PFV のうち、今週に対応する weekday が無いものは **触らない**
      (一括削除しない; W41 v2 apply_individual_proposal とは挙動が異なる).
    - 1 患者単位の atomic commit (この患者だけ 1 transaction で確定).
    - dry_run=true (default) ではコミットせず diff のみを返す。

Wave Next 1 cross-review (Codex/Opus) 対応:
    - ``operation`` に ``"skipped"`` を追加 (multi-staff 等で当該 weekday を
      触らない判断をしたケースを差分に明示).
    - ``SyncChangeEntry.reason`` (Optional) を追加し ``skipped`` の理由を伝える.
    - レスポンスに ``untouched_existing`` を追加し、今週 visit が無い既存 PFV
      (zombie 候補) を返す.
"""

from __future__ import annotations

import uuid
from datetime import time as time_cls
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SyncChangeOperation = Literal["insert", "update", "unchanged", "skipped"]


class SyncWeekToFixedRequest(BaseModel):
    """POST /patients/{patient_id}/sync-week-visits-to-fixed のリクエスト body."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100, description="ISO 年 (2000-2100)")
    iso_week: int = Field(ge=1, le=53, description="ISO 週 (1-53)")
    dry_run: bool = Field(
        default=True,
        description="True なら diff のみ計算し DB 変更しない。False で 1 TX commit.",
    )


class SyncPfvSnapshot(BaseModel):
    """1 件の PFV の値スナップショット (old / new 比較用)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    start_time: time_cls
    duration_min: int = Field(ge=1, le=480)
    course_template_id: uuid.UUID | None = None


class SyncChangeEntry(BaseModel):
    """1 件の変更 (insert / update / unchanged / skipped)."""

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    operation: SyncChangeOperation
    old: SyncPfvSnapshot | None = Field(
        default=None,
        description="insert の場合は None。update / unchanged では既存 PFV のスナップショット.",
    )
    new: SyncPfvSnapshot | None = Field(
        default=None,
        description=(
            "今週 visit から導出された新 PFV のスナップショット. "
            "``skipped`` の場合は None (反映候補が存在しない / 反映を見送ったケース)."
        ),
    )
    reason: str | None = Field(
        default=None,
        description="``skipped`` の理由を人間可読で伝える (例: 'multi_staff_not_supported').",
    )


class SyncWeekToFixedSummary(BaseModel):
    """件数サマリ."""

    model_config = ConfigDict(extra="forbid")

    pfv_inserted: int = Field(ge=0)
    pfv_updated: int = Field(ge=0)
    pfv_unchanged: int = Field(ge=0)
    pfv_skipped: int = Field(ge=0, default=0)


class SyncWeekToFixedResponse(BaseModel):
    """POST /patients/{patient_id}/sync-week-visits-to-fixed のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    summary: SyncWeekToFixedSummary
    changes: list[SyncChangeEntry] = Field(
        default_factory=list,
        description="今週 visits の各 weekday に対応する変更. 触らない既存 PFV は含まない.",
    )
    untouched_existing: list[SyncPfvSnapshot] = Field(
        default_factory=list,
        description=(
            "今週 visit が無く、apply 後も保持される既存 PFV (zombie 候補) の "
            "スナップショット. FE 側で「今週の visit に対応が無い固定枠」を "
            "可視化したい用途を想定."
        ),
    )
    transaction_applied: bool = Field(
        description="True なら DB に commit 済み. False なら dry_run (diff のみ).",
    )


__all__ = [
    "SyncChangeEntry",
    "SyncChangeOperation",
    "SyncPfvSnapshot",
    "SyncWeekToFixedRequest",
    "SyncWeekToFixedResponse",
    "SyncWeekToFixedSummary",
]
