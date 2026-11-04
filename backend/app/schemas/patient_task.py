"""事務所からのやること (看護師へのタスク) の入出力 — 正典 ``docs/plans/patient-tasks-design-2026-11-01.md``."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

TaskStatus = Literal["open", "done", "cancelled"]
#: waiting2plus = 未完了で 2 回目以降の訪問待ち (visit_round >= 2)。
TaskListStatus = Literal["open", "overdue", "waiting2plus", "done", "all"]


# ---------------------------------------------------------------------------
# 項目 (task_item_types)
# ---------------------------------------------------------------------------


class TaskItemTypeRead(BaseModel):
    id: UUID
    name: str
    sort_order: int
    is_active: bool


def _strip_name(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    if not s:
        raise ValueError("名前を入れてください")
    return s


class TaskItemTypeCreate(BaseModel):
    name: str = Field(..., max_length=100)
    #: 省略すると最後に並べる。
    sort_order: int | None = None

    _name = field_validator("name")(_strip_name)


class TaskItemTypeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    sort_order: int | None = None
    is_active: bool | None = None

    _name = field_validator("name")(_strip_name)


# ---------------------------------------------------------------------------
# やること (patient_tasks)
# ---------------------------------------------------------------------------


class TaskStaffRef(BaseModel):
    staff_id: UUID
    name: str | None
    #: primary = 主担当 (コース担当で決まる場合も含む) / secondary = 2 名訪問の 2 人目 /
    #: accompaniment = 同行
    role: Literal["primary", "secondary", "accompaniment"]


class TaskVisitBrief(BaseModel):
    """やることを出す訪問 (または完了した訪問) の要点."""

    visit_id: UUID
    visit_date: date
    start_time: str
    end_time: str
    staff: list[TaskStaffRef]


class PatientTaskRead(BaseModel):
    id: UUID
    patient_id: UUID
    patient_name: str | None
    start_date: date
    #: いつまでに (任意)。null は期日なし。
    due_date: date | None = None
    #: 何回目の訪問待ち (1 以上)。一覧・作成・1 件の読み出しは「いまの時刻から見て」
    #: (日付以降で予定の終わりが過ぎた訪問の数 + 1)、訪問ごとの読み出しは「その訪問から見て」
    #: (日付以降でその訪問より前の訪問の数 + 1)。
    visit_round: int = Field(default=1, ge=1)
    item_type_id: UUID | None
    item_type_name: str | None
    #: 伝言 (item_type_id が NULL) なら true。
    is_message: bool
    memo: str | None
    created_by: UUID | None
    created_by_name: str | None
    created_at: datetime
    status: TaskStatus
    #: 期日を過ぎた = 未完了で、期日が今日 (JST) より前。期日がないものは常に false。
    overdue: bool
    #: 訪問ごとの読み出しだけ: この訪問より前に出す訪問があった (= 持ち越し)。
    carried_over: bool = False
    done_at: datetime | None
    done_by_user_id: UUID | None
    done_by_staff_id: UUID | None
    done_by_name: str | None
    done_visit_id: UUID | None
    cancelled_at: datetime | None
    #: 毎月の設定から作ったときだけ: どの設定から・何月分か (その月の 1 日)。手で付けたものは null。
    recurring_rule_id: UUID | None = None
    recurring_month: date | None = None
    #: 一覧・作成の読み出しだけ: 未完了なら出す訪問 (今日以降・日付以降の最初の訪問)。
    next_visit: TaskVisitBrief | None = None
    #: 一覧の読み出しだけ: 完了した訪問。
    done_visit: TaskVisitBrief | None = None
    #: 呼んだ人がこの完了を戻せるか (管理者 = 完了なら常に / 職員 = 自分が押した完了だけ)。
    can_reopen: bool = False


class PatientTaskCreate(BaseModel):
    patient_id: UUID
    start_date: date
    #: 期日 (任意)。日付より前は 422。
    due_date: date | None = None
    #: 複数なら 1 件ずつ作る。空なら伝言だけの 1 件。
    item_type_ids: list[UUID] = Field(default_factory=list, max_length=50)
    #: 伝言 (任意)。項目と一緒なら最初の 1 件に付ける。
    memo: str | None = Field(default=None, max_length=2000)

    @field_validator("memo")
    @classmethod
    def _strip_memo(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None


class PatientTaskCreateResult(BaseModel):
    tasks: list[PatientTaskRead]
    next_visit: TaskVisitBrief | None
    notified_user_count: int


class PatientTaskDueUpdate(BaseModel):
    """期日を変える。null で期日なしに戻す (欄は省けない)."""

    due_date: date | None


class PatientTaskComplete(BaseModel):
    visit_id: UUID | None = None


class VisitTaskCount(BaseModel):
    open_count: int
    total_count: int
    has_carried_over: bool
    #: その訪問に出る未完了のうち、期日を過ぎたものがある。
    has_overdue: bool
    all_done: bool


class VisitTaskCounts(BaseModel):
    date: date
    #: 訪問 id → 件数。やることが 1 件も出ない訪問は含めない。
    counts: dict[UUID, VisitTaskCount]
