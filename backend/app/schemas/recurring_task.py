"""毎月のやること (繰り返しの設定) の入出力 — 正典 ``docs/plans/patient-tasks-recurring-design-2026-11-02.md``."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.patient_task import TaskVisitBrief

VisitCategory = Literal["psychiatric", "general"]
PreviewResult = Literal["create", "exists", "manual", "excluded", "not_active"]


def _strip_memo(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    return s or None


def _categories(v: list[VisitCategory] | None) -> list[VisitCategory] | None:
    if v is None:
        return None
    out = list(dict.fromkeys(v))
    if not out:
        raise ValueError("訪問の区分を 1 つ以上選んでください")
    return out


def _first_of_month(v: date | None) -> date | None:
    return v.replace(day=1) if v is not None else None


class PatientRef(BaseModel):
    patient_id: UUID
    name: str | None


class RecurringTaskRuleRead(BaseModel):
    id: UUID
    item_type_id: UUID | None
    item_type_name: str | None
    #: 項目を「使わない」にした設定は作らない (画面で知らせる)。伝言だけの設定は true。
    item_type_active: bool
    memo: str | None
    day_of_month: int
    #: 毎月何日まで (任意)。1〜28 日、0 は末日。null は期日なし。
    due_day: int | None = None
    target_categories: list[str]
    start_month: date
    is_active: bool
    excluded_patients: list[PatientRef]
    #: いま稼働中で対象の方の人数。
    target_count: int
    #: この設定から作った未完了の件数 (まとめて取り消すの数)。
    open_count: int
    created_at: datetime
    #: 作る・変えるの応答だけ: 保存したときに今月の分を作った件数。
    created_now: int = 0


class RecurringTaskRuleCreate(BaseModel):
    item_type_id: UUID | None = None
    memo: str | None = Field(default=None, max_length=2000)
    day_of_month: int = Field(default=1, ge=1, le=28)
    #: 毎月何日まで (任意)。1〜28 日、0 は末日。毎月何日からより前は 422。
    due_day: int | None = Field(default=None, ge=0, le=28)
    target_categories: list[VisitCategory] = Field(..., max_length=2)
    #: 最初に作る月 (日は 1 日に丸める)。
    start_month: date
    is_active: bool = True
    excluded_patient_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    _memo = field_validator("memo")(_strip_memo)
    _cats = field_validator("target_categories")(_categories)
    _month = field_validator("start_month")(_first_of_month)


class RecurringTaskRuleUpdate(BaseModel):
    """送った欄だけ変える (``memo``・``item_type_id`` は null で空にできる)."""

    item_type_id: UUID | None = None
    memo: str | None = Field(default=None, max_length=2000)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    #: 送ると変える (null で期日なし)。
    due_day: int | None = Field(default=None, ge=0, le=28)
    target_categories: list[VisitCategory] | None = Field(default=None, max_length=2)
    start_month: date | None = None
    is_active: bool | None = None
    #: 送ると外す方をこの一覧に置き換える。
    excluded_patient_ids: list[UUID] | None = Field(default=None, max_length=1000)

    _memo = field_validator("memo")(_strip_memo)
    _cats = field_validator("target_categories")(_categories)
    _month = field_validator("start_month")(_first_of_month)


class RecurringCandidate(BaseModel):
    patient_id: UUID
    name: str
    kana: str | None
    visit_category: str
    status: str


class RecurringRunResult(BaseModel):
    created_count: int


class RecurringRunDueResult(BaseModel):
    today: date
    month: date
    created_total: int
    by_rule: dict[str, int]


class RecurringCancelResult(BaseModel):
    cancelled_count: int


class RecurringPreviewRow(BaseModel):
    patient_id: UUID
    patient_name: str | None
    status: str
    visit_category: str
    result: PreviewResult
    #: 出る訪問 (作る・作り済みの方だけ)。無ければ「訪問が入ったら出ます」。
    visit: TaskVisitBrief | None
    #: この設定の前の月の分が未完了で残っている (同じ訪問に並べて出る)。
    prev_open: bool


class RecurringPreview(BaseModel):
    rule_id: UUID
    month: date
    #: 作るやることの日付 (この日から出す)。
    start_date: date
    #: 作るやることの期日 (設定に「毎月何日まで」が無ければ null)。
    due_date: date | None = None
    is_active: bool
    item_is_active: bool
    counts: dict[str, int]
    prev_open_count: int
    rows: list[RecurringPreviewRow]
