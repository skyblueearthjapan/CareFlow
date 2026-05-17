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


class StaffExcelReplaceAllSummary(BaseModel):
    """完全置換インポートの集計 (UI で件数バッジ表示用)."""

    model_config = ConfigDict(extra="forbid")

    staff_to_create: int = 0
    staff_to_update: int = 0
    staff_to_soft_delete: int = 0  # Excel に無い既存 (alive) スタッフ
    staff_error: int = 0
    shift_to_replace: int = 0  # 全件物理削除する既存 shift の件数
    shift_to_create: int = 0  # Excel から再投入する shift の件数
    shift_error: int = 0


class StaffExcelReplaceAllResponse(BaseModel):
    """POST /api/v1/staff/import-export/replace-all のレスポンス."""

    model_config = ConfigDict(extra="forbid")

    summary: StaffExcelReplaceAllSummary
    staff_rows: list[StaffExcelImportRow]
    shift_rows: list[ShiftExcelImportRow]
    transaction_applied: bool = Field(
        description=(
            "True なら DB に反映済 (atomic). "
            "dry_run=True のときは常に False. "
            "dry_run=False で error が 1 件もなく成功した場合のみ True. "
            "通常 import と違い、error 1 件でも全 rollback されるため True/False "
            "は all-or-nothing."
        )
    )
    staff_to_soft_delete_preview: list[dict] = Field(
        default_factory=list,
        description=(
            "Excel に無い既存 (alive) スタッフの一覧 (UI プレビュー表示用). "
            "各要素は {staff_id, staff_code, name} を含む."
        ),
    )
