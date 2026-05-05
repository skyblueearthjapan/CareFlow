"""Diff Engine schemas — request/response payloads for /api/v1/diff."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DiffRequest(BaseModel):
    """Compare two CSV schedules and emit a correction list.

    `current_csv` is the kaipoke export, `optimized_csv` is the optimizer
    output. Week range is given as day-of-month (1..31) and supports
    month-spanning weeks via wrap (start > end ⇒ accept either tail).
    """

    model_config = ConfigDict(extra="forbid")

    current_csv: str = Field(
        ...,
        max_length=10_000_000,
        description="Kaipoke現状CSVのテキスト (10MB上限)",
    )
    optimized_csv: str = Field(
        ...,
        max_length=10_000_000,
        description="最適化後CSVのテキスト (10MB上限)",
    )
    target_week_start: int = Field(..., ge=1, le=31, description="対象週の開始日 (1-31)")
    target_week_end: int = Field(..., ge=1, le=31, description="対象週の終了日 (1-31)")
    target_users: list[str] | None = Field(
        default=None,
        description="絞り込み対象の利用者名リスト（Noneで全利用者）",
    )


DiffAction = Literal["edit", "delete", "add", "date_change"]


class DiffEdit(BaseModel):
    """1件分の修正エントリ。Correction dataclass と等価のシリアライズ形式。"""

    model_config = ConfigDict(from_attributes=True)

    user_name: str
    date_from: str
    date_to: str
    start_time_from: str
    start_time_to: str
    end_time_from: str
    end_time_to: str
    staff1_from: str
    staff1_to: str
    staff2_from: str
    staff2_to: str
    service_type: str
    action: DiffAction
    business_type: str = ""
    remarks: str = ""


class DiffSummary(BaseModel):
    """件数サマリー（フロント側で集計表示するための値）。"""

    total: int
    time_changes: int
    staff_changes: int
    date_changes: int
    additions: int
    deletions: int


class DiffResponse(BaseModel):
    """Compare 結果。`edits` の順序は engine の発行順を保つ。"""

    model_config = ConfigDict(extra="forbid")

    summary: DiffSummary
    edits: list[DiffEdit]
