"""Schemas for `staff_event_defaults` (毎週の固定イベント既定・朝会など).

正典 = docs/plans/kaipoke-event-two-way-design.md §3-②。
時刻は HH:MM 文字列 (staff_overrides と同じ流儀)。weekday は 0=月〜5=土。
start == end はメモ系 (ゼロ長) として許容する。
"""

from __future__ import annotations

from datetime import time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_WEEKDAY_LABELS = ("月", "火", "水", "木", "金", "土")


def _parse_hhmm(v: str | time) -> time:
    if isinstance(v, time):
        return v
    h, m = str(v).split(":")
    return time(int(h), int(m))


class EventDefaultCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=5, description="0=月〜5=土 (日曜は定義不可)")
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    title: str = Field(min_length=1, max_length=255)
    blocking: bool = False
    note: str | None = Field(default=None, max_length=500)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title は必須です")
        return v

    @model_validator(mode="after")
    def _time_order(self) -> EventDefaultCreate:
        if _parse_hhmm(self.start_time) > _parse_hhmm(self.end_time):
            raise ValueError("終了時刻は開始時刻以降にしてください")
        return self


class EventDefaultUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int | None = Field(default=None, ge=0, le=5)
    start_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    title: str | None = Field(default=None, min_length=1, max_length=255)
    blocking: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class EventDefaultBulkCreate(BaseModel):
    """``POST /api/v1/staff-event-defaults/bulk`` request (staff-event-history-design.md §2 Phase 3).

    スタッフ × 曜日の **全組** を 1 トランザクションで作成する汎用一括登録。
    朝会もカンファレンスもこの 1 本で登録する (朝会をコードで特別扱いしない・PO Q5)。
    """

    model_config = ConfigDict(extra="forbid")

    staff_ids: list[UUID] = Field(min_length=1, description="対象スタッフ (1 件以上)")
    weekdays: list[int] = Field(min_length=1, description="0=月〜5=土 (1 件以上・重複は除去)")
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    title: str = Field(min_length=1, max_length=255)
    blocking: bool = False
    note: str | None = Field(default=None, max_length=500)

    @field_validator("staff_ids")
    @classmethod
    def _dedupe_staff_ids(cls, v: list[UUID]) -> list[UUID]:
        # 並びは指定順のまま (プレビュー件数と作成順を一致させる)。
        return list(dict.fromkeys(v))

    @field_validator("weekdays")
    @classmethod
    def _dedupe_weekdays(cls, v: list[int]) -> list[int]:
        for wd in v:
            if not 0 <= wd <= 5:
                raise ValueError("weekday は 0=月〜5=土 で指定してください (日曜は定義不可)")
        return sorted(dict.fromkeys(v))

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title は必須です")
        return v

    @model_validator(mode="after")
    def _time_order(self) -> EventDefaultBulkCreate:
        # 単票 (EventDefaultCreate) は start == end のゼロ長メモを許容するが、
        # 一括登録は「N名 × N曜日」を一気に作るため取り違えの影響が大きい。
        # ここでは end > start を必須にする (仕様: end <= start は 422)。
        if _parse_hhmm(self.start_time) >= _parse_hhmm(self.end_time):
            raise ValueError("終了時刻は開始時刻より後にしてください")
        return self


class EventDefaultBulkResult(BaseModel):
    """``POST /api/v1/staff-event-defaults/bulk`` response."""

    model_config = ConfigDict(extra="forbid")

    created: int = Field(ge=0, description="新規作成した既定の件数")
    skipped: int = Field(ge=0, description="同一内容の既定が既にあり作成しなかった件数")


class EventDefaultRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    staff_id: UUID
    weekday: int
    weekday_label: str
    start_time: str
    end_time: str
    title: str
    blocking: bool
    note: str | None = None

    @staticmethod
    def weekday_to_label(weekday: int) -> str:
        return _WEEKDAY_LABELS[weekday] if 0 <= weekday <= 5 else "?"
