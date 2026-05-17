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
