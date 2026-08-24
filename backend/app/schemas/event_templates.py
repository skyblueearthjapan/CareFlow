"""Schemas for `event_templates` (イベントひな形・共通 + 個人).

正典 = docs/plans/staff-event-history-design.md §2 Phase 2。

時刻は HH:MM 文字列 (staff_events / staff_event_defaults と同じ流儀)。
``start_time`` / ``end_time`` は **両方指定 or 両方 None** (=「時間はその場で
入力」)。片方だけの指定は 422。時間を持つ場合は ``end > start`` (ゼロ長は不可 —
ひな形はダイアログの初期値であり、長さ 0 の初期値は事故のもと)。
"""

from __future__ import annotations

from datetime import date, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EventTypeLiteral = Literal["event", "training"]

_HHMM = r"^(?:[01]\d|2[0-3]):[0-5]\d$"


def parse_hhmm(v: str) -> time:
    h, m = v.split(":")[:2]
    return time(int(h), int(m))


def format_hhmm(v: time | None) -> str | None:
    return None if v is None else f"{v.hour:02d}:{v.minute:02d}"


def _validate_time_pair(start: str | None, end: str | None) -> None:
    """両方 None か、両方指定 (かつ end > start) のみ許す。"""
    if (start is None) != (end is None):
        raise ValueError("開始時刻と終了時刻は両方指定するか、両方空にしてください")
    if start is not None and end is not None and parse_hhmm(end) <= parse_hhmm(start):
        raise ValueError("終了時刻は開始時刻より後にしてください")


class EventTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # NULL = 事業所共通 / 値あり = そのスタッフ個人。
    staff_id: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    event_type: EventTypeLiteral = "event"
    start_time: str | None = Field(default=None, pattern=_HHMM)
    end_time: str | None = Field(default=None, pattern=_HHMM)
    blocking: bool = False
    note: str | None = Field(default=None, max_length=500)
    # 未指定なら同スコープ内の max(sort_order) + 1 が入る。
    sort_order: int | None = None
    is_active: bool = True

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title は必須です")
        return v

    @model_validator(mode="after")
    def _times(self) -> EventTemplateCreate:
        _validate_time_pair(self.start_time, self.end_time)
        return self


class EventTemplateUpdate(BaseModel):
    """部分更新。時刻は「両方送る」か「両方送らない」のどちらか。

    時刻を消して「その場で入力」に戻すときは ``start_time=null`` と
    ``end_time=null`` を **両方明示的に** 送る。
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    event_type: EventTypeLiteral | None = None
    start_time: str | None = Field(default=None, pattern=_HHMM)
    end_time: str | None = Field(default=None, pattern=_HHMM)
    blocking: bool | None = None
    note: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None
    is_active: bool | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("title は必須です")
        return v

    @model_validator(mode="after")
    def _times(self) -> EventTemplateUpdate:
        fields = self.model_fields_set
        touches_start = "start_time" in fields
        touches_end = "end_time" in fields
        if touches_start != touches_end:
            raise ValueError("開始時刻と終了時刻は両方指定するか、両方空にしてください")
        if touches_start:
            _validate_time_pair(self.start_time, self.end_time)
        return self


class EventTemplateRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    staff_id: UUID | None = None
    title: str
    event_type: str
    start_time: str | None = None
    end_time: str | None = None
    blocking: bool
    note: str | None = None
    sort_order: int
    is_active: bool
    # 便宜フラグ: FE の「共通」/「個人」セクション分けに使う。
    is_shared: bool


class ReorderRequest(BaseModel):
    """1 スコープ (共通 or 1 スタッフ) の並びを一括更新する。"""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID | None = None
    ordered_ids: list[UUID] = Field(min_length=1)


class HistorySuggestionItem(BaseModel):
    """過去 staff_events をタイトルで集約した「ひな形にできる候補」1 件。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    count: int
    last_date: date
    last_start_time: str | None = None
    last_end_time: str | None = None
    event_type: str
