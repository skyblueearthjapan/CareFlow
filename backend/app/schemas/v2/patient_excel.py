"""Pydantic schemas for /api/v1/patients/import-export.

Frontend と契約する Response 型. dry_run / apply で同じ shape を返す.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ImportOperation = Literal["new", "update", "delete", "error", "noop"]


class PatientExcelChange(BaseModel):
    """1 フィールドの差分."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="変更フィールド名 (例: address, name)")
    old_value: Any = Field(default=None, description="既存値. 新規行では null.")
    new_value: Any = Field(default=None, description="新しい値. delete 行では null.")


class PatientExcelImportRow(BaseModel):
    """患者マスタシートの 1 行の処理結果."""

    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(
        description="Excel の行番号 (1-indexed, ヘッダーが 1 行目, データは 2 行目から)"
    )
    patient_id: UUID | None = Field(default=None)
    patient_code: str | None = Field(default=None)
    operation: ImportOperation
    changes: list[PatientExcelChange] = Field(default_factory=list)
    error_message: str | None = Field(default=None)


class PfvExcelImportRow(BaseModel):
    """固定訪問スケジュールシートの 1 行の処理結果."""

    model_config = ConfigDict(extra="forbid")

    row_number: int
    patient_id: UUID | None = Field(default=None)
    patient_code: str | None = Field(default=None)
    weekday: int | None = Field(default=None, ge=0, le=6)
    slot_index: int | None = Field(default=None, ge=0, le=1)
    operation: ImportOperation
    changes: list[PatientExcelChange] = Field(default_factory=list)
    error_message: str | None = Field(default=None)


class PatientExcelImportSummary(BaseModel):
    """集計 (UI で件数バッジ表示用)."""

    model_config = ConfigDict(extra="forbid")

    patients_new: int = 0
    patients_update: int = 0
    patients_delete: int = 0
    patients_error: int = 0
    patients_noop: int = 0
    pfv_new: int = 0
    pfv_update: int = 0
    pfv_delete: int = 0
    pfv_error: int = 0
    pfv_noop: int = 0


class PatientExcelImportResponse(BaseModel):
    """POST /api/v1/patients/import-export/import のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    summary: PatientExcelImportSummary
    patient_rows: list[PatientExcelImportRow]
    pfv_rows: list[PfvExcelImportRow]
    transaction_applied: bool = Field(
        description=(
            "True なら DB に反映済 (partial commit). "
            "dry_run=True のときは常に False. "
            "dry_run=False で有効な op が 1 件以上あれば True "
            "(error 行は skip され、それ以外が 1 transaction で commit される). "
            "dry_run=False で有効な op が 0 件 (全 error or 全 noop) なら False."
        )
    )


# ---------------------------------------------------------------------------
# 完全置換 (バックアップ復元) 用 schema
# ---------------------------------------------------------------------------


class PatientExcelReplaceAllSummary(BaseModel):
    """完全置換インポートの集計 (UI で件数バッジ表示用).

    通常 import の summary とは別 schema. PFV は全件再投入する仕様なので
    update 概念は無く、"replace" として件数を返す.
    """

    model_config = ConfigDict(extra="forbid")

    patients_to_create: int = 0
    patients_to_update: int = 0
    patients_to_soft_delete: int = 0  # Excel に無い既存 (alive) 患者
    patients_error: int = 0
    pfv_to_replace: int = 0  # 全件物理削除する既存 PFV の件数
    pfv_to_create: int = 0  # Excel から再投入する PFV の件数
    pfv_error: int = 0


class PatientExcelReplaceAllResponse(BaseModel):
    """POST /api/v1/patients/import-export/replace-all のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    summary: PatientExcelReplaceAllSummary
    patient_rows: list[PatientExcelImportRow]
    pfv_rows: list[PfvExcelImportRow]
    transaction_applied: bool = Field(
        description=(
            "True なら DB に反映済 (atomic). "
            "dry_run=True のときは常に False. "
            "dry_run=False で error が 1 件もなく成功した場合のみ True. "
            "通常 import と違い、error 1 件でも全 rollback されるため True/False "
            "は all-or-nothing."
        )
    )
    patients_to_soft_delete_preview: list[dict] = Field(
        default_factory=list,
        description=(
            "Excel に無い既存 (alive) 患者の一覧 (UI プレビュー表示用). "
            "各要素は {patient_id, patient_code, name} を含む."
        ),
    )
