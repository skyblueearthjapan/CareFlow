"""Pydantic schemas for /api/v1/staff/import-export.

Frontend と契約する Response 型. dry_run / apply で同じ shape を返す.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ImportOperation = Literal["new", "update", "delete", "error", "noop"]


class StaffExcelChange(BaseModel):
    """1 フィールドの差分."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="変更フィールド名 (例: name, role)")
    old_value: Any = Field(default=None, description="既存値. 新規行では null.")
    new_value: Any = Field(default=None, description="新しい値. delete 行では null.")


class StaffExcelImportRow(BaseModel):
    """スタッフマスタシートの 1 行の処理結果."""

    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(
        description="Excel の行番号 (1-indexed, ヘッダーが 1 行目, データは 2 行目から)"
    )
    staff_id: UUID | None = Field(default=None)
    staff_code: str | None = Field(default=None)
    operation: ImportOperation
    changes: list[StaffExcelChange] = Field(default_factory=list)
    error_message: str | None = Field(default=None)


class ShiftExcelImportRow(BaseModel):
    """勤務シフトシートの 1 行の処理結果."""

    model_config = ConfigDict(extra="forbid")

    row_number: int
    staff_id: UUID | None = Field(default=None)
    staff_code: str | None = Field(default=None)
    weekday: int | None = Field(default=None, ge=0, le=6)
    operation: ImportOperation
    changes: list[StaffExcelChange] = Field(default_factory=list)
    error_message: str | None = Field(default=None)


class StaffExcelImportSummary(BaseModel):
    """集計 (UI で件数バッジ表示用)."""

    model_config = ConfigDict(extra="forbid")

    staff_new: int = 0
    staff_update: int = 0
    staff_delete: int = 0
    staff_error: int = 0
    staff_noop: int = 0
    shift_new: int = 0
    shift_update: int = 0
    shift_delete: int = 0
    shift_error: int = 0
    shift_noop: int = 0


class StaffExcelImportResponse(BaseModel):
    """POST /api/v1/staff/import-export/import のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    summary: StaffExcelImportSummary
    staff_rows: list[StaffExcelImportRow]
    shift_rows: list[ShiftExcelImportRow]
    transaction_applied: bool = Field(
        description="True なら DB に反映済 (dry_run=False かつ error 0 件)."
    )
