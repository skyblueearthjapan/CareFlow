"""操作ジャーナル (schedule_op_log) のリクエスト/レスポンス スキーマ (Wave U-3)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OpLogStateResponse(BaseModel):
    """GET /v2/op-log/state レスポンス.

    自分のその週のスタック状態。D-4: 自分の操作のみ対象。
    """

    model_config = ConfigDict(extra="forbid")

    can_undo: bool
    can_redo: bool
    undo_label: str | None = None  # 次の undo 対象グループのラベル（cannot undo → None）
    redo_label: str | None = None  # 次の redo 対象グループのラベル（cannot redo → None）


class UndoRedoRequest(BaseModel):
    """POST /v2/op-log/undo and /v2/op-log/redo のリクエスト."""

    model_config = ConfigDict(extra="forbid")

    iso_year: int = Field(ge=2000, le=2100)
    iso_week: int = Field(ge=1, le=53)


class UndoRedoResponse(BaseModel):
    """POST /v2/op-log/undo and /v2/op-log/redo のレスポンス.

    実行後の state を含める（FE の再フェッチ削減）。
    """

    model_config = ConfigDict(extra="forbid")

    state: OpLogStateResponse
