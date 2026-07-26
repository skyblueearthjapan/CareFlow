"""Schemas for `staff_events` (研修 / イベント / 会議 etc.).

DB storage (unchanged): `event_type` (≤16 char string), `starts_at` /
`ends_at` (`DateTime(timezone=True)`), `title`, `note`. The HTTP API
contract however speaks the *Frontend* language:

  - `date`        (YYYY-MM-DD, derived from `starts_at`)
  - `start_time`  (HH:MM, from `starts_at.time()`)
  - `end_time`    (HH:MM, from `ends_at.time()`)
  - `type`        ('研修' or 'イベント' — anything that is not 'training'
                    in the DB is collapsed to 'イベント' on output so the
                    Frontend zod's strict 2-value enum still validates)

Writes additionally accept legacy English `event_type` codes via the
`_CANONICAL_EVENT` table so import scripts continue to work.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from datetime import date as _Date  # noqa: N812 (project-local alias)
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Public canonical event types accepted on input.
_CANONICAL_EVENT = {
    "会議": "meeting",
    "打合せ": "meeting",
    "meeting": "meeting",
    "研修": "training",
    "training": "training",
    "イベント": "event",
    "event": "event",
    "役所同行": "errand",
    "errand": "errand",
    "初回面談": "interview",
    "interview": "interview",
    "事務": "office",
    "office": "office",
    "その他": "other",
    "other": "other",
}


def _normalise_event_type(value: str) -> str:
    if not value:
        raise ValueError("event_type must not be empty")
    canon = _CANONICAL_EVENT.get(value, value)
    if len(canon) > 16:
        raise ValueError("event_type must be ≤16 characters")
    return canon


def _db_type_to_label(value: str) -> str:
    """Collapse the rich DB enum to the Frontend's binary 研修 / イベント."""
    return "研修" if value == "training" else "イベント"


# ---------------------------------------------------------------------------
# Frontend-shaped helpers
# ---------------------------------------------------------------------------


_HHMM_RE_TIME = "%H:%M"


def _validate_hhmm(value: str) -> str:
    """Lightweight HH:MM validator (Frontend already guarantees the format)."""
    try:
        time.fromisoformat(value if len(value) > 5 else value + ":00")
    except ValueError as exc:
        raise ValueError(f"invalid HH:MM time string: {value!r}") from exc
    # Normalise to exactly HH:MM (drop seconds if Frontend ever sends them).
    parts = value.split(":")
    if len(parts) < 2:
        raise ValueError(f"invalid HH:MM time string: {value!r}")
    return f"{int(parts[0]):02d}:{int(parts[1]):02d}"


# ---------------------------------------------------------------------------
# Request schemas (Frontend shape)
# ---------------------------------------------------------------------------


class EventCreate(BaseModel):
    """Frontend-shaped create payload (no `starts_at` / `ends_at`)."""

    model_config = ConfigDict(extra="forbid")

    date: _Date
    title: Annotated[str, Field(min_length=1, max_length=100)]
    start_time: str
    end_time: str
    type: Annotated[str, Field(min_length=1, max_length=32)]
    note: str | None = Field(default=None, max_length=500)

    @field_validator("type")
    @classmethod
    def _norm_type(cls, v: str) -> str:
        return _normalise_event_type(v)

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_hhmm(cls, v: str) -> str:
        return _validate_hhmm(v)

    @model_validator(mode="after")
    def _check_range(self) -> EventCreate:
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be < end_time")
        return self


class EventUpdate(BaseModel):
    """Frontend-shaped partial update — every field optional.

    Wave 39: ``new_staff_id`` を追加. 指定された場合は event を別 staff に
    付け替える (= D&D で担当者を変更する). URL の ``staff_id`` は「現在の
    staff_id」, body の ``new_staff_id`` で移動先を表す. 同一トランザクション
    内で start_time/end_time/date と一緒に更新される.
    """

    model_config = ConfigDict(extra="forbid")

    date: _Date | None = None
    title: str | None = Field(default=None, min_length=1, max_length=100)
    start_time: str | None = None
    end_time: str | None = None
    type: str | None = Field(default=None, min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=500)
    # Wave 39: D&D で event を別スタッフに付け替えるための optional フィールド.
    new_staff_id: UUID | None = None
    # 🔒絶対に潰せないイベント (2段階提案・mig 0063): 提案エンジンのフォール
    # バックでも占有として扱う。ユーザーがイベント編集で ON/OFF する。
    blocking: bool | None = None

    @field_validator("type")
    @classmethod
    def _norm_type(cls, v: str | None) -> str | None:
        return None if v is None else _normalise_event_type(v)

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_hhmm(cls, v: str | None) -> str | None:
        return None if v is None else _validate_hhmm(v)


# ---------------------------------------------------------------------------
# Response schema (Frontend contract)
# ---------------------------------------------------------------------------


class EventRead(BaseModel):
    """Response contract aligned with Frontend zod `eventReadSchema`.

    Wave 39: ``staff_id`` を追加. FE で D&D 経由の付け替え時に「元の staff_id」を
    PATCH URL に組み立てるために必要. 既存の Frontend zod は `staff_id` を
    optional として扱うため互換.
    """

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    # Wave 39: 担当スタッフ ID (D&D 移動時の URL 構築 + 衝突チェックに使用).
    staff_id: UUID
    date: _Date
    title: str
    start_time: str
    end_time: str
    type: Literal["研修", "イベント"]
    note: str | None = None
    # 🔒絶対に潰せないイベント (2段階提案). 既定 False.
    blocking: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_orm(cls, data: Any) -> Any:
        """Translate ORM `StaffEvent` → Frontend-shaped dict."""
        if isinstance(data, dict):
            return data

        starts_at = getattr(data, "starts_at", None)
        ends_at = getattr(data, "ends_at", None)
        if starts_at is None or ends_at is None:
            return data

        # If timezone-aware, convert to local naive for date/time extraction.
        # We treat the stored timestamp as the canonical wall-clock for the
        # event (Frontend doesn't ship a tz offset). For tz-aware values we
        # convert to UTC then drop the tz so the components are stable.
        if isinstance(starts_at, datetime) and starts_at.tzinfo is not None:
            starts_at = starts_at.astimezone(UTC).replace(tzinfo=None)
        if isinstance(ends_at, datetime) and ends_at.tzinfo is not None:
            ends_at = ends_at.astimezone(UTC).replace(tzinfo=None)

        title = getattr(data, "title", None)
        # Frontend zod requires `title: string` (not nullable). Coerce
        # historical NULL rows to empty string so the response still
        # validates client-side.
        if title is None:
            title = ""

        return {
            "id": data.id,
            "staff_id": data.staff_id,
            "date": starts_at.date(),
            "title": title,
            "start_time": starts_at.strftime(_HHMM_RE_TIME),
            "end_time": ends_at.strftime(_HHMM_RE_TIME),
            "type": _db_type_to_label(getattr(data, "event_type", "")),
            "note": getattr(data, "note", None),
            "blocking": bool(getattr(data, "blocking", False)),
        }
