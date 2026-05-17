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


# ---------------------------------------------------------------------------
# Bulk (一括) sync schemas — FullOptimizeDialog の「一括 固定時間変更」用.
#
# 2 種類のモードを 2 つの bulk endpoint で表現する:
#   1. 永続 (PFV を更新; 個別 sync-week-visits-to-fixed の bulk 版)
#   2. 週限定 (今週の visit のみ更新; PFV は変更しない)
# ---------------------------------------------------------------------------


class BulkSyncWeekToFixedRequest(BaseModel):
    """POST /patients/bulk-sync-week-to-fixed のリクエスト body.

    既存 ``POST /patients/{patient_id}/sync-week-visits-to-fixed`` を複数
    patient に対して 1 transaction で実行する.
    """

    model_config = ConfigDict(extra="forbid")

    patient_ids: list[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="対象患者の UUID リスト (空不可 / 上限 500 名).",
    )
    iso_year: int = Field(ge=2000, le=2100, description="ISO 年 (2000-2100)")
    iso_week: int = Field(ge=1, le=53, description="ISO 週 (1-53)")
    dry_run: bool = Field(
        default=True,
        description="True なら diff のみ計算 (DB 変更なし) / False なら 1 TX commit.",
    )


class BulkSyncWeekToFixedItem(BaseModel):
    """1 patient 分の bulk sync 結果."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    summary: SyncWeekToFixedSummary
    changes: list[SyncChangeEntry] = Field(default_factory=list)
    untouched_existing: list[SyncPfvSnapshot] = Field(default_factory=list)
    transaction_applied: bool = Field(
        description="True なら DB に commit 済み. False なら dry_run.",
    )
    error: str | None = Field(
        default=None,
        description="patient 個別の失敗理由 (404 など). 成功時は None.",
    )


class BulkSyncWeekToFixedResponse(BaseModel):
    """POST /patients/bulk-sync-week-to-fixed のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    results: list[BulkSyncWeekToFixedItem] = Field(default_factory=list)
    total_inserted: int = Field(ge=0, default=0)
    total_updated: int = Field(ge=0, default=0)
    total_unchanged: int = Field(ge=0, default=0)
    total_skipped: int = Field(ge=0, default=0)
    errors: list[str] = Field(
        default_factory=list,
        description="bulk 全体の errors (個別 error は results[].error).",
    )
    transaction_applied: bool = Field(
        description="True なら 1 TX で commit 済み. False なら dry_run.",
    )


# --- Bulk: week-only visit changes (PFV 不変, 今週 visit のみ update) ---


class BulkWeekOnlyPatientVisitChange(BaseModel):
    """1 件の (patient, weekday) に対する週限定変更."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    weekday: int = Field(ge=0, le=6, description="0=月..6=日")
    start_time: time_cls
    duration_min: int = Field(ge=1, le=480)
    course_template_id: uuid.UUID | None = Field(
        default=None,
        description="任意. None なら既存 visit の course を維持する.",
    )


class BulkApplyWeekOnlyVisitChangesRequest(BaseModel):
    """POST /patients/bulk-apply-week-only-visit-changes のリクエスト body.

    指定週の visit を (patient, weekday) 単位で update する. 該当 visit が無ければ
    INSERT (defensive). PFV は変更しない (= イレギュラー対応モード).
    """

    model_config = ConfigDict(extra="forbid")

    patient_visit_changes: list[BulkWeekOnlyPatientVisitChange] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="(patient, weekday) ごとの変更. 空不可 / 上限 1000 件.",
    )
    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)
    dry_run: bool = Field(
        default=True,
        description="True なら diff のみ / False なら 1 TX commit.",
    )


class BulkWeekOnlyChangeOutcome(BaseModel):
    """1 件の (patient, weekday) 適用結果."""

    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    weekday: int = Field(ge=0, le=6)
    operation: Literal["insert", "update", "unchanged", "skipped"]
    old_start_time: time_cls | None = None
    new_start_time: time_cls | None = None
    old_duration_min: int | None = None
    new_duration_min: int | None = None
    visit_id: uuid.UUID | None = None
    reason: str | None = Field(
        default=None,
        description="skipped の理由 (例: 'no_active_visit_and_no_pfv_anchor').",
    )


class BulkApplyWeekOnlyVisitChangesResponse(BaseModel):
    """POST /patients/bulk-apply-week-only-visit-changes のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int
    iso_week: int
    outcomes: list[BulkWeekOnlyChangeOutcome] = Field(default_factory=list)
    total_inserted: int = Field(ge=0, default=0)
    total_updated: int = Field(ge=0, default=0)
    total_unchanged: int = Field(ge=0, default=0)
    total_skipped: int = Field(ge=0, default=0)
    errors: list[str] = Field(default_factory=list)
    transaction_applied: bool = Field(
        description="True なら 1 TX で commit 済み. False なら dry_run.",
    )


__all__ = [
    "BulkApplyWeekOnlyVisitChangesRequest",
    "BulkApplyWeekOnlyVisitChangesResponse",
    "BulkSyncWeekToFixedItem",
    "BulkSyncWeekToFixedRequest",
    "BulkSyncWeekToFixedResponse",
    "BulkWeekOnlyChangeOutcome",
    "BulkWeekOnlyPatientVisitChange",
    "SyncChangeEntry",
    "SyncChangeOperation",
    "SyncPfvSnapshot",
    "SyncWeekToFixedRequest",
    "SyncWeekToFixedResponse",
    "SyncWeekToFixedSummary",
]
