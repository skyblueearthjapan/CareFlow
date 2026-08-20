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
