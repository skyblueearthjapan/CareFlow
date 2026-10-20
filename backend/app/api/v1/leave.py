"""「休み・勤務表」の月の API (休み設計 §5・§9 #8).

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §2・§5・§11。

    GET    /leave/month/{month}                 — 月×全職員の表を 1 回で返す (管理者)
    GET    /leave/month/{month}/summary         — 月の確定の状態と祝日 (職員も可・休みの中身は返さない)
    POST   /leave/day                           — 休みを入れる・種類を変える (管理者)
    DELETE /leave/day?staff_id=&date=           — 休みを取り消す (管理者)
    POST   /leave/requests/approve-batch        — 申請をまとめて承認 (1 トランザクション・通知は 1 通)
    POST   /leave/requests/{id}/approve         — 承認 (``override_type`` で種類を変えて承認)
    POST   /leave/requests/{id}/reject          — 却下 (理由は必須)
    GET    /leave/closed-days?month=            — 事業所の休みの一覧
    PUT    /leave/closed-days                   — 事業所の休みを入れる・名前を変える
    DELETE /leave/closed-days/{day}             — 事業所の休みを消す
    PUT    /leave/months/{month}                — 勤務すべき時間数
    POST   /leave/month/{month}/confirm         — 月の確定 (記録と本人への通知だけ)

休みを書くのは共通の処理 ``apply_staff_leave`` / ``cancel_staff_leave`` だけ (§3-1)。
承認は申請一覧と同じ行ロック＋CAS (``applied_at IS NULL AND status='pending'``) で 1 回だけ。
カイポケへの送信と勤務表の予定版は #13 で ``_confirm_month`` の後に足す (ここでは行わない)。
照合・送信の API は ``leave_kaipoke.py`` (別の担当)。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Final, cast, get_args
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.pending_requests import _claim_pending_for_approve
from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.core.leave import LEAVE_TYPES, visit_hits_leave
from app.models.course import Course
from app.models.leave import (
    KinmuSheet,
    LeaveCompareRun,
    LeaveMonth,
    OfficeClosedDay,
    StaffLeaveKaipoke,
)
from app.models.pending_request import PendingRequest
from app.models.schedule_op_log import ScheduleOpLog
from app.models.staff import Staff, StaffShiftConfirmation, StaffWeeklyOverride
from app.models.user import User, normalize_user_role
from app.models.visit import VISIT_STATUS_CANCELLED, VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.leave import (
    HolidayRead,
    KinmuSheetRead,
    LeaveApproveBatchInput,
    LeaveApproveBatchResult,
    LeaveApproveInput,
    LeaveApproveResult,
    LeaveBulkSkip,
    LeaveCancelResult,
    LeaveCompareStatus,
    LeaveConfirmInput,
    LeaveConfirmResult,
    LeaveDayBulkInput,
    LeaveDayBulkResult,
    LeaveDayInput,
    LeaveDayResult,
    LeaveMonthCell,
    LeaveMonthConfirmation,
    LeaveMonthDay,
    LeaveMonthRead,
    LeaveMonthStaff,
    LeaveMonthSummary,
    LeaveMonthUpdate,
    LeaveMonthView,
    LeavePendingBrief,
    LeavePendingItem,
    LeaveRejectInput,
    LeaveRejectResult,
    LeaveRequestType,
    LeaveSendStatus,
    LeaveShortStaff,
    MonthParam,
    OfficeClosedDayCreate,
    OfficeClosedDayRead,
)
from app.schemas.v2.enums import RequestStatus, RequestType
from app.services.leave_calendar import (
    day_holiday_value,
    iso_key,
    load_closed_days,
    load_overrides_between,
    load_working_weekdays,
    month_days,
    month_holidays,
    required_holidays,
)
from app.services.leave_notify import (
    notify_leave_approved,
    notify_leave_rejected,
    notify_shift_confirmed,
)
from app.services.staff_leave import (
    LeaveApplyResult,
    StaffLeaveError,
    apply_staff_leave,
    cancel_staff_leave,
    leave_request_deadline,
    notify_leave_results,
    today_jst,
)

router = APIRouter()

AdminUser = Annotated[User, Depends(require_role("admin"))]

#: その日休む人がこの数以上で黄色 (PO 2026-10-20)。
OFF_WARN_THRESHOLD: Final = 2
#: 申請の種類として受ける値 (変更のお願いの ``cancel`` を含む)。
_REQUEST_TYPES: Final = frozenset(get_args(LeaveRequestType))
_ORIGIN: Final = "leave_page"
#: 申請中の日に入れる・取り消すとき (申請を先に決めてもらう)。
_PENDING_DAY_DETAIL: Final = "この日は申請中です。申請を承認か却下で決めてください"


def _raise_leave_error(exc: StaffLeaveError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# ---------------------------------------------------------------------------
# 月の材料 (どれもクエリの回数は職員・週の数によらず一定)
# ---------------------------------------------------------------------------


@dataclass
class _RequestRow:
    """staff_off の申請 1 件 (payload を読んだもの)."""

    row: PendingRequest
    staff_id: UUID
    day: date
    override_type: str
    kind: str
    note: str | None


@dataclass
class _MonthData:
    month: date
    days: list[date]
    staff: list[Staff]
    working: dict[UUID, set[int]]
    #: {職員: {日: override の行}} (時間変更も含む)。
    overrides: dict[UUID, dict[date, StaffWeeklyOverride]]
    closed: dict[date, str]
    holidays: dict[date, str]
    pending: list[_RequestRow] = field(default_factory=list)
    approved: list[_RequestRow] = field(default_factory=list)


def _request_fields(row: PendingRequest) -> tuple[UUID | None, date | None, str, str, str | None]:
    """申請の (職員, 日, 種類, kind, メモ)。``edited_payload`` があればそちらを読む."""
    payload: dict[str, Any] = dict(row.edited_payload or row.payload or {})
    raw_staff = payload.get("staff_id")
    staff_id = row.target_staff_id
    if raw_staff:
        try:
            staff_id = UUID(str(raw_staff))
        except ValueError:
            staff_id = row.target_staff_id
    day = row.target_date
    raw_date = payload.get("date")
    if raw_date:
        try:
            day = date.fromisoformat(str(raw_date))
        except ValueError:
            day = row.target_date
    override_type = str(payload.get("override_type") or payload.get("type") or "off")
    kind = "change" if payload.get("kind") == "change" else "normal"
    note = payload.get("note") or payload.get("reason")
    return staff_id, day, override_type, kind, str(note) if note else None


async def _load_staff_off_requests(
    db: AsyncSession, days: list[date], statuses: tuple[str, ...]
) -> list[_RequestRow]:
    """その月の staff_off の申請 (``statuses``)。種類が読めない行は表に出さない."""
    rows = (
        await db.scalars(
            select(PendingRequest)
            .where(
                PendingRequest.request_type == RequestType.STAFF_OFF.value,
                PendingRequest.status.in_(statuses),
                PendingRequest.target_date >= days[0],
                PendingRequest.target_date <= days[-1],
            )
            .order_by(PendingRequest.target_date, PendingRequest.created_at, PendingRequest.id)
        )
    ).all()
    out: list[_RequestRow] = []
    for r in rows:
        staff_id, day, override_type, kind, note = _request_fields(r)
        if staff_id is None or day is None or not (days[0] <= day <= days[-1]):
            continue
        if override_type not in _REQUEST_TYPES:
            continue
        out.append(_RequestRow(r, staff_id, day, override_type, kind, note))
    return out


async def _load_month(db: AsyncSession, month: date, *, with_requests: bool) -> _MonthData:
    """表に載せる職員 (消していない・退職者はその月に休みがあるときだけ) と休み・型・申請."""
    days = month_days(month)
    all_staff = list(
        (
            await db.scalars(
                select(Staff)
                .where(Staff.deleted_at.is_(None))
                .order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc(), Staff.id)
            )
        ).all()
    )
    # 全職員ぶんを 1 回で読む (職員ごとに読まない・日付の範囲から ISO 週の組を作る L2)。
    overrides = await load_overrides_between(db, days[0], days[-1])
    staff = [
        s
        for s in all_staff
        if s.status != "retired"
        or any(r.override_type in LEAVE_TYPES for r in overrides.get(s.id, {}).values())
    ]
    working = await load_working_weekdays(db, [s.id for s in staff])
    data = _MonthData(
        month=days[0],
        days=days,
        staff=staff,
        working=working,
        overrides=overrides,
        closed=await load_closed_days(db, month),
        holidays=month_holidays(month),
    )
    if with_requests:
        rows = await _load_staff_off_requests(
            db, days, (RequestStatus.PENDING.value, RequestStatus.APPROVED.value)
        )
        listed = {s.id for s in staff}
        data.pending = [
            r for r in rows if r.row.status == RequestStatus.PENDING.value and r.staff_id in listed
        ]
        data.approved = [
            r for r in rows if r.row.status == RequestStatus.APPROVED.value and r.staff_id in listed
        ]
    return data


@dataclass(frozen=True)
class _VisitLite:
    id: UUID
    day: date
    start: Any
    end: Any
    planned: bool


async def _load_month_visits(
    db: AsyncSession, days: list[date]
) -> dict[tuple[UUID, date], list[_VisitLite]]:
    """{(職員, 日): 本人が担当の訪問 (取消・削除を除く)}.

    「本人の訪問」は共通の処理 (``staff_leave._owns_primary``・§3-3) と同じ: primary が本人／
    primary が空・明示の担当なしでない・コース担当が本人／2 名体制の VSA に本人。
    """
    rows = (
        await db.execute(
            select(
                Visit.id,
                Visit.visit_date,
                Visit.start_time,
                Visit.end_time,
                Visit.status,
                Visit.primary_staff_id,
                Visit.manual_staff_override,
                Course.assigned_staff_id,
            )
            .outerjoin(Course, (Course.id == Visit.course_id) & (Course.deleted_at.is_(None)))
            .where(
                Visit.visit_date >= days[0],
                Visit.visit_date <= days[-1],
                Visit.deleted_at.is_(None),
                Visit.status != VISIT_STATUS_CANCELLED,
            )
        )
    ).all()
    vsa: dict[UUID, set[UUID]] = defaultdict(set)
    for visit_id, staff_id in (
        await db.execute(
            select(VisitStaffAssignment.visit_id, VisitStaffAssignment.staff_id)
            .join(Visit, Visit.id == VisitStaffAssignment.visit_id)
            .where(
                Visit.visit_date >= days[0],
                Visit.visit_date <= days[-1],
                Visit.deleted_at.is_(None),
            )
        )
    ).all():
        vsa[visit_id].add(staff_id)
    out: dict[tuple[UUID, date], list[_VisitLite]] = defaultdict(list)
    for vid, vday, start, end, vstatus, primary, manual, course_staff in rows:
        owners = set(vsa.get(vid, set()))
        if primary is not None:
            owners.add(primary)
        elif not manual and course_staff is not None:
            owners.add(course_staff)
        lite = _VisitLite(vid, vday, start, end, vstatus == VISIT_STATUS_PLANNED)
        for sid in owners:
            out[(sid, vday)].append(lite)
    return out


async def _generated_weeks(db: AsyncSession, days: list[date]) -> set[tuple[int, int]]:
    """その月にかかる週のうち、作り済み (コースがある) の (ISO 年, ISO 週)."""
    pairs = sorted({(d.isocalendar().year, d.isocalendar().week) for d in days})
    rows = (
        await db.execute(
            select(Course.iso_year, Course.iso_week)
            .where(
                Course.deleted_at.is_(None),
                or_(*(and_(Course.iso_year == y, Course.iso_week == w) for y, w in pairs)),
            )
            .distinct()
        )
    ).all()
    return {(int(y), int(w)) for y, w in rows}


def _leave_type(data: _MonthData, staff_id: UUID, day: date) -> str | None:
    row = data.overrides.get(staff_id, {}).get(day)
    return row.override_type if row is not None and row.override_type in LEAVE_TYPES else None


def _counts_as_off(data: _MonthData, s: Staff, day: date, leave_type: str | None) -> bool:
    """その日休む人に数えるか: 休みの登録があり・在籍 (active)・新人でなく・型で勤務する曜日.

    退職・休職 (status が active でない) の人の休みは数えない (ディレクター決定)。
    """
    return (
        leave_type in LEAVE_TYPES
        and s.status == "active"
        and not s.is_trainee
        and day.weekday() in data.working.get(s.id, set())
    )


def _moves(visits: list[_VisitLite], leave_type: str) -> int:
    """休みを入れたら担当なしへ移る訪問の数 (planned で休みの時間帯にかかるもの)."""
    return sum(1 for v in visits if v.planned and visit_hits_leave(leave_type, v.start, v.end))


def _send_status(
    leave_type: str | None, kaipoke: StaffLeaveKaipoke | None
) -> LeaveSendStatus | None:
    """休みの送信の状態 (§2-5 の表)."""
    sent = kaipoke.sent_type if kaipoke is not None else None
    if leave_type is None:
        return "kaipoke_action" if sent is not None else None
    if sent is None:
        return "baseline" if kaipoke is not None and kaipoke.baseline_pending else "unsent"
    return "sent" if sent == leave_type else "kaipoke_action"


async def _confirmations(
    db: AsyncSession, month: date, staff_ids: list[UUID]
) -> dict[UUID, datetime]:
    if not staff_ids:
        return {}
    rows = (
        await db.execute(
            select(StaffShiftConfirmation.staff_id, StaffShiftConfirmation.confirmed_at).where(
                StaffShiftConfirmation.month == month,
                StaffShiftConfirmation.staff_id.in_(staff_ids),
            )
        )
    ).all()
    return {sid: at for sid, at in rows}


def _aware(value: datetime | None) -> datetime | None:
    """SQLite の試験では tz が落ちるので UTC として比べる."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _after(a: datetime | None, b: datetime | None) -> bool:
    a2, b2 = _aware(a), _aware(b)
    return a2 is not None and b2 is not None and a2 > b2


# ---------------------------------------------------------------------------
# GET /leave/month/{month}
# ---------------------------------------------------------------------------


@router.get("/month/{month}", response_model=LeaveMonthView)
async def get_leave_month(month: MonthParam, db: DbDep, _user: AdminUser) -> LeaveMonthView:
    """「休み・勤務表」の月を 1 回で返す (職員×日の表・集計・申請中・確定・照合・検知)."""
    data = await _load_month(db, month, with_requests=True)
    days = data.days
    today = today_jst()
    visits = await _load_month_visits(db, days)
    generated = await _generated_weeks(db, days)
    staff_ids = [s.id for s in data.staff]
    confirmations = await _confirmations(db, data.month, staff_ids)
    kaipoke_rows = (
        await db.scalars(
            select(StaffLeaveKaipoke).where(
                StaffLeaveKaipoke.leave_date >= days[0], StaffLeaveKaipoke.leave_date <= days[-1]
            )
        )
    ).all()
    kaipoke = {(k.staff_id, k.leave_date): k for k in kaipoke_rows}
    prev_month = (days[0] - timedelta(days=1)).replace(day=1)
    months = {
        m.month: m
        for m in (
            await db.scalars(select(LeaveMonth).where(LeaveMonth.month.in_([days[0], prev_month])))
        ).all()
    }
    leave_month = months.get(days[0])
    run: LeaveCompareRun | None = None
    if leave_month is not None and leave_month.last_compare_run_id is not None:
        run = await db.get(LeaveCompareRun, leave_month.last_compare_run_id)

    required = required_holidays(data.month, data.closed.keys())
    staff_by_id = {s.id: s for s in data.staff}
    order = {s.id: i for i, s in enumerate(data.staff)}
    # 同じマスに申請中が 2 件以上あるとき (作成時の 409 をすり抜けた古いデータなど):
    # マスの印と「通れば あと N」(pending_gain) は **いちばん先に出された申請** を使う
    # (data.pending は作成順)。件数 (見出しの「申N」・職員の pending_count) と右の一覧は
    # すべての申請を数える。
    pending_by_cell: dict[tuple[UUID, date], _RequestRow] = {}
    pending_per_staff: dict[UUID, int] = defaultdict(int)
    for r in data.pending:
        pending_by_cell.setdefault((r.staff_id, r.day), r)
        pending_per_staff[r.staff_id] += 1
    approved_cells = {(r.staff_id, r.day) for r in data.approved if r.override_type in LEAVE_TYPES}

    # 日ごとのその日休む人
    off_count: dict[date, int] = dict.fromkeys(days, 0)
    for s in data.staff:
        for d in days:
            if _counts_as_off(data, s, d, _leave_type(data, s.id, d)):
                off_count[d] += 1
    pending_per_day: dict[date, int] = defaultdict(int)
    for r in data.pending:
        pending_per_day[r.day] += 1

    out_days = [
        LeaveMonthDay(
            date=d,
            weekday=d.weekday(),
            holiday_name=data.holidays.get(d),
            closed_label=data.closed.get(d),
            week_generated=(d.isocalendar().year, d.isocalendar().week) in generated,
            off_count=off_count[d],
            off_warn=off_count[d] >= OFF_WARN_THRESHOLD,
            pending_count=pending_per_day.get(d, 0),
        )
        for d in days
    ]

    # マス・職員の集計・検知
    cells: list[LeaveMonthCell] = []
    out_staff: list[LeaveMonthStaff] = []
    visits_on_leave = 0
    for s in data.staff:
        work = data.working.get(s.id, set())
        holidays_total = 0.0
        paid = 0
        gain = 0.0
        for d in days:
            row = data.overrides.get(s.id, {}).get(d)
            leave = (
                row.override_type if row is not None and row.override_type in LEAVE_TYPES else None
            )
            h, p = day_holiday_value(leave, d.weekday() in work)
            holidays_total += h
            paid += p
            day_visits = visits.get((s.id, d), [])
            if leave is not None and d >= today:
                visits_on_leave += _moves(day_visits, leave)
            req = pending_by_cell.get((s.id, d))
            if req is not None:
                new_type = None if req.override_type == "cancel" else req.override_type
                gain += day_holiday_value(new_type, d.weekday() in work)[0] - h
            k = kaipoke.get((s.id, d))
            send = _send_status(leave, k)
            cancelled = leave is None and (s.id, d) in approved_cells
            custom = row is not None and row.override_type == "custom_time"
            if not (leave or custom or req or cancelled or day_visits or send):
                continue
            cells.append(
                LeaveMonthCell(
                    staff_id=s.id,
                    date=d,
                    leave_type=cast(Any, leave),
                    source=cast(Any, row.source) if leave is not None and row is not None else None,
                    note=row.reason if leave is not None and row is not None else None,
                    approved_request_id=(
                        row.pending_request_id if leave is not None and row is not None else None
                    ),
                    time_change_start=row.start_time if custom and row is not None else None,
                    time_change_end=row.end_time if custom and row is not None else None,
                    pending=(
                        LeavePendingBrief(
                            request_id=req.row.id,
                            override_type=cast(Any, req.override_type),
                            kind=cast(Any, req.kind),
                        )
                        if req is not None
                        else None
                    ),
                    cancelled_after_approval=cancelled,
                    visit_count=len(day_visits),
                    send_status=send,
                )
            )
        full = s.employment_type == "full_time"
        out_staff.append(
            LeaveMonthStaff(
                id=s.id,
                order=order[s.id],
                name=s.name,
                code=s.code,
                employment_type=cast(Any, s.employment_type),
                is_full_time=full,
                is_trainee=bool(s.is_trainee),
                status=s.status,
                off_weekdays=[wd for wd in range(7) if wd not in work],
                holidays=holidays_total,
                required_holidays=required if full else None,
                shortfall=max(0.0, required - holidays_total) if full else None,
                paid_days=paid,
                pending_count=pending_per_staff.get(s.id, 0),
                pending_gain=gain,
                confirmed_at=confirmations.get(s.id),
            )
        )

    pending_items: list[LeavePendingItem] = []
    for r in data.pending:
        s = staff_by_id[r.staff_id]
        current = _leave_type(data, s.id, r.day)
        now_n = off_count[r.day]
        counted_now = _counts_as_off(data, s, r.day, current)
        new_type = None if r.override_type == "cancel" else r.override_type
        counted_after = _counts_as_off(data, s, r.day, new_type)
        past = r.day < today
        pending_items.append(
            LeavePendingItem(
                id=r.row.id,
                staff_id=s.id,
                staff_name=s.name,
                staff_order=order[s.id],
                date=r.day,
                override_type=cast(Any, r.override_type),
                kind=cast(Any, r.kind),
                note=r.note,
                created_at=r.row.created_at,
                current_leave_type=cast(Any, current),
                past_day=past,
                move_visit_count=(
                    0
                    if past or new_type is None
                    else _moves(visits.get((s.id, r.day), []), new_type)
                ),
                off_count_now=now_n,
                off_count_if_approved=now_n - int(counted_now) + int(counted_after),
            )
        )

    last_changed = leave_month.last_leave_changed_at if leave_month is not None else None
    last_compared = leave_month.last_compared_at if leave_month is not None else None
    confirmed_times = [confirmations[sid] for sid in staff_ids if sid in confirmations]
    last_confirmed = max(confirmed_times, key=lambda t: _aware(t) or t) if confirmed_times else None
    prev = months.get(prev_month)
    return LeaveMonthView(
        month=data.month,
        today=today,
        required_holidays=required,
        required_hours=leave_month.required_hours if leave_month is not None else None,
        previous_required_hours=prev.required_hours if prev is not None else None,
        off_warn_threshold=OFF_WARN_THRESHOLD,
        days=out_days,
        staff=out_staff,
        cells=cells,
        pending=pending_items,
        holidays=[HolidayRead(date=d, name=n) for d, n in sorted(data.holidays.items())],
        closed_days=[
            OfficeClosedDayRead.model_validate(r)
            for r in (
                await db.scalars(
                    select(OfficeClosedDay)
                    .where(OfficeClosedDay.date >= days[0], OfficeClosedDay.date <= days[-1])
                    .order_by(OfficeClosedDay.date)
                )
            ).all()
        ],
        confirmation=LeaveMonthConfirmation(
            confirmed=bool(staff_ids) and len(confirmed_times) == len(staff_ids),
            confirmed_staff_count=len(confirmed_times),
            staff_count=len(staff_ids),
            last_confirmed_at=last_confirmed,
            changed_since_confirm=_after(last_changed, last_confirmed),
        ),
        compare=LeaveCompareStatus(
            last_compared_at=last_compared,
            last_compare_run_id=leave_month.last_compare_run_id if leave_month else None,
            diff_count=run.diff_count if run is not None else None,
            planned_send_count=run.planned_send_count if run is not None else None,
            unconfirmed_days=run.unconfirmed_days if run is not None else None,
            last_leave_changed_at=last_changed,
            changed_since_compare=_after(last_changed, last_compared),
            baseline_pending_count=sum(1 for c in cells if c.send_status == "baseline"),
        ),
        visits_on_leave_count=visits_on_leave,
    )


# ---------------------------------------------------------------------------
# GET /leave/month/{month}/summary (職員も可)
# ---------------------------------------------------------------------------


@router.get("/month/{month}/summary", response_model=LeaveMonthSummary)
async def get_leave_month_summary(
    month: MonthParam, db: DbDep, user: CurrentActiveUser
) -> LeaveMonthSummary:
    """月の確定の状態と祝日・事業所の休み (申請履歴の帯とスマホの祝日用)。休みの中身は返さない."""
    role = normalize_user_role(user.role)
    if role not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    days = month_days(month)
    holidays = [HolidayRead(date=d, name=n) for d, n in sorted(month_holidays(month).items())]
    closed = [HolidayRead(date=d, name=n) for d, n in (await load_closed_days(db, month)).items()]
    deadline = leave_request_deadline(days[0])
    if role != "admin":
        mine = None
        if user.staff_id is not None:
            mine = (await _confirmations(db, days[0], [user.staff_id])).get(user.staff_id)
        return LeaveMonthSummary(
            month=days[0],
            holidays=holidays,
            closed_days=closed,
            request_deadline=deadline,
            confirmed=mine is not None,
            confirmed_at=mine,
        )
    # 訪問は読まない (月の表より軽い): 職員・休み・型・事業所の休み・確定・申請・送信の記録だけ。
    data = await _load_month(db, month, with_requests=False)
    staff_ids = [s.id for s in data.staff]
    confirmations = await _confirmations(db, days[0], staff_ids)
    pending = await _load_staff_off_requests(db, days, (RequestStatus.PENDING.value,))
    listed = set(staff_ids)
    kaipoke_rows = (
        await db.scalars(
            select(StaffLeaveKaipoke).where(
                StaffLeaveKaipoke.leave_date >= days[0], StaffLeaveKaipoke.leave_date <= days[-1]
            )
        )
    ).all()
    # 「カイポケで要対応」は sent_type がある行からしか生まれない (§2-5) ので、送信の記録だけを見る。
    kaipoke_action = sum(
        1
        for k in kaipoke_rows
        if k.staff_id in listed
        and _send_status(_leave_type(data, k.staff_id, k.leave_date), k) == "kaipoke_action"
    )
    times = list(confirmations.values())
    return LeaveMonthSummary(
        month=days[0],
        holidays=holidays,
        closed_days=closed,
        request_deadline=deadline,
        confirmed=bool(staff_ids) and len(times) == len(staff_ids),
        confirmed_at=max(times, key=lambda t: _aware(t) or t) if times else None,
        confirmed_staff_count=len(times),
        staff_count=len(staff_ids),
        pending_count=sum(1 for r in pending if r.staff_id in listed),
        short_staff_count=len(
            _short_staff(data, required_holidays(data.month, data.closed.keys()))
        ),
        kaipoke_action_count=kaipoke_action,
    )


# ---------------------------------------------------------------------------
# GET /leave/month/{month}/kinmu-sheets (作った勤務表の一覧)
# ---------------------------------------------------------------------------


@router.get("/month/{month}/kinmu-sheets", response_model=list[KinmuSheetRead])
async def list_kinmu_sheets(month: MonthParam, db: DbDep, _user: AdminUser) -> list[KinmuSheetRead]:
    """その月に作った勤務表 (予定版・確定版の全部の版)。予定版→確定版、版の新しい順."""
    rows = (
        await db.scalars(
            select(KinmuSheet)
            .where(KinmuSheet.month == month)
            .order_by(KinmuSheet.kind.desc(), KinmuSheet.version.desc())
        )
    ).all()
    return [KinmuSheetRead.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# 入れる・取り消す
# ---------------------------------------------------------------------------


def _day_result(r: LeaveApplyResult) -> LeaveDayResult:
    return LeaveDayResult(
        staff_id=r.staff_id,
        date=r.day,
        leave_type=cast(Any, r.leave_type),
        past_day=r.past_day,
        moved_visit_count=len(r.moved_visit_ids),
        moved_patient_count=r.moved_patient_count,
        skipped_visit_count=len(r.skipped_visit_ids),
        cancelled_event_count=len(r.cancelled_event_ids),
        accompaniment_count=r.accompaniment_count,
    )


async def _pending_on(db: AsyncSession, staff_id: UUID, day: date) -> PendingRequest | None:
    rows = await _load_staff_off_requests(db, [day], (RequestStatus.PENDING.value,))
    return next((r.row for r in rows if r.staff_id == staff_id and r.day == day), None)


@router.post("/day", response_model=LeaveDayResult)
async def put_leave_day(payload: LeaveDayInput, db: DbDep, user: AdminUser) -> LeaveDayResult:
    """休みを入れる・種類を変える (管理者が入れた休み = source 'admin')."""
    if await _pending_on(db, payload.staff_id, payload.date) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_PENDING_DAY_DETAIL)
    try:
        result = await apply_staff_leave(
            db,
            staff_id=payload.staff_id,
            day=payload.date,
            leave_type=payload.leave_type,
            source="admin",
            note=payload.note,
            actor=user,
            op_group_id=None,
            origin=_ORIGIN,
        )
    except StaffLeaveError as exc:
        await db.rollback()
        raise _raise_leave_error(exc) from exc
    await db.commit()
    return _day_result(result)


@router.delete("/day", response_model=LeaveCancelResult)
async def delete_leave_day(
    db: DbDep,
    user: AdminUser,
    staff_id: Annotated[UUID, Query()],
    day: Annotated[date, Query(alias="date")],
) -> LeaveCancelResult:
    """休みを取り消す。担当なしへ移した訪問・申請の承認は戻さない (§5-3)."""
    if await _pending_on(db, staff_id, day) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_PENDING_DAY_DETAIL)
    moved = await _moved_to_unassigned(db, staff_id, day)
    try:
        cancelled = await cancel_staff_leave(
            db, staff_id=staff_id, day=day, actor=user, op_group_id=None, origin=_ORIGIN
        )
    except StaffLeaveError as exc:
        await db.rollback()
        raise _raise_leave_error(exc) from exc
    await db.commit()
    return LeaveCancelResult(
        staff_id=staff_id, date=day, cancelled_type=cast(Any, cancelled), moved_visit_count=moved
    )


@router.post("/day/bulk", response_model=LeaveDayBulkResult)
async def put_leave_day_bulk(
    payload: LeaveDayBulkInput, db: DbDep, user: AdminUser
) -> LeaveDayBulkResult:
    """「この日の全員に公休を入れる」(事業所の休みの日など)。1 トランザクション.

    ``staff_ids`` が無ければ、常勤・在籍・型でその曜日に勤務・その日に申請中でない・
    まだ休みが入っていない人の全員 (新人は含む)。渡したときは、表に載っていない人がいれば
    422・申請中の人がいれば 409。どちらのときも、すでに休みが入っている人は上書きせず
    ``skipped`` に返す (ディレクター決定)。1 人でも入れられなければ全部やめる。
    管理者への通知は 1 通にまとめる。
    """
    day = payload.date
    data = await _load_month(db, day, with_requests=True)
    pending_staff = {r.staff_id for r in data.pending if r.day == day}
    skipped: list[LeaveBulkSkip] = []
    if payload.staff_ids is not None:
        wanted = list(dict.fromkeys(payload.staff_ids))
        if set(wanted) - {s.id for s in data.staff}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="この月の表に載っていない職員が含まれています",
            )
        if pending_staff & set(wanted):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_PENDING_DAY_DETAIL)
        targets = []
        for sid in wanted:
            existing = _leave_type(data, sid, day)
            if existing is not None:
                skipped.append(
                    LeaveBulkSkip(staff_id=sid, reason="already_on_leave", leave_type=existing)
                )
            else:
                targets.append(sid)
    else:
        targets = [
            s.id
            for s in data.staff
            if s.employment_type == "full_time"
            and s.status == "active"
            and day.weekday() in data.working.get(s.id, set())
            and s.id not in pending_staff
            and _leave_type(data, s.id, day) is None
        ]
    results: list[LeaveApplyResult] = []
    for sid in targets:
        try:
            results.append(
                await apply_staff_leave(
                    db,
                    staff_id=sid,
                    day=day,
                    leave_type=payload.override_type,
                    source="admin",
                    note=payload.note,
                    actor=user,
                    op_group_id=None,
                    origin=_ORIGIN,
                    notify=False,
                )
            )
        except StaffLeaveError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=exc.status_code,
                detail={"staff_id": str(sid), "message": exc.detail},
            ) from exc
    await notify_leave_results(db, list(results))
    await db.commit()
    return LeaveDayBulkResult(
        date=day,
        override_type=payload.override_type,
        applied=[_day_result(r) for r in results],
        skipped=skipped,
        moved_visit_count=sum(len(r.moved_visit_ids) for r in results),
    )


async def _moved_to_unassigned(db: AsyncSession, staff_id: UUID, day: date) -> int:
    """この人のこの日の休みで担当なしへ移した訪問の数 (op_log から・戻した操作は除く).

    **その日の最後の取り消しの後に入れた休み** (いまの override の行) の操作 (``set_staff_off``)
    と同じ op_group の ``set_visit_staff_slot`` のうち、担当なしへ移したもの
    (``primary_staff_id`` が空) の訪問を重ねずに数える。

    「最後の取り消しの後」は override の行の id で決める: 取り消すと行が消え、次に入れると
    新しい id になる (種類を変えるだけなら同じ id のまま)。運転席の「戻る」で取り消しを
    戻すと元の id の行が戻るので、元の休みの移し替えもまた数える。op_log の時刻の並びに
    頼らない (同じ秒の操作でも決まる)。週を作るときの後始末など op_log を書かない経路で
    移した訪問は数えられない。
    """
    current = (await load_overrides_between(db, day, day, [staff_id])).get(staff_id, {}).get(day)
    if current is None or current.override_type not in LEAVE_TYPES:
        return 0
    y, w, wd = iso_key(day)
    rows = (
        await db.scalars(
            select(ScheduleOpLog).where(
                ScheduleOpLog.iso_year == y,
                ScheduleOpLog.iso_week == w,
                ScheduleOpLog.undone.is_(False),
                ScheduleOpLog.op_kind.in_(("set_staff_off", "set_visit_staff_slot")),
            )
        )
    ).all()
    groups = {
        r.op_group_id
        for r in rows
        if r.op_kind == "set_staff_off"
        and str(r.forward_payload.get("staff_id")) == str(staff_id)
        and r.forward_payload.get("weekday") == wd
        and r.forward_payload.get("type") in LEAVE_TYPES
        and str(r.forward_payload.get("override_id")) == str(current.id)
    }
    return len(
        {
            str(r.forward_payload.get("visit_id"))
            for r in rows
            if r.op_kind == "set_visit_staff_slot"
            and r.op_group_id in groups
            and r.forward_payload.get("primary_staff_id") is None
        }
    )


# ---------------------------------------------------------------------------
# 申請の承認・却下
# ---------------------------------------------------------------------------


async def _approve_one(
    db: AsyncSession, request_id: UUID, user: User, override_type: str | None
) -> tuple[LeaveApproveResult, LeaveApplyResult | None]:
    """申請 1 件を承認する (行ロック＋CAS・通知は呼び出し側でまとめる・commit しない).

    Raises:
        HTTPException: 404 / 409 (承認済み・却下済み) / 422 (休みの申請でない・種類が不正)。
    """
    row = await _claim_pending_for_approve(db, request_id)
    if row.request_type != RequestType.STAFF_OFF.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="休みの申請ではありません"
        )
    staff_id, day, requested, _kind, note = _request_fields(row)
    if staff_id is None or day is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="申請の職員か日付がありません"
        )
    if requested not in _REQUEST_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"申請の休みの種類が読めません: {requested}",
        )
    if override_type is not None and requested == "cancel":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="休みの取り消しのお願いは種類を変えて承認できません",
        )
    final_type = override_type or requested
    if override_type is not None and override_type != requested:
        # 先に編集された内容 (edited_payload) があればそれを保ち、種類だけ変える。
        base = dict(row.edited_payload or row.payload or {})
        row.edited_payload = {**base, "override_type": override_type}

    now = datetime.now(UTC)
    cas = await db.execute(
        update(PendingRequest)
        .where(
            PendingRequest.id == request_id,
            PendingRequest.applied_at.is_(None),
            PendingRequest.status == RequestStatus.PENDING.value,
        )
        .values(
            status=RequestStatus.APPROVED.value,
            approved_by=user.id,
            approved_at=now,
            applied_at=now,
        )
        .execution_options(synchronize_session="fetch")
    )
    if (getattr(cas, "rowcount", 0) or 0) == 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="この申請は承認済みです")

    try:
        if final_type == "cancel":
            await cancel_staff_leave(
                db, staff_id=staff_id, day=day, actor=user, op_group_id=None, origin=_ORIGIN
            )
            applied = None
        else:
            applied = await apply_staff_leave(
                db,
                staff_id=staff_id,
                day=day,
                leave_type=final_type,
                source="request",
                note=note,
                actor=user,
                op_group_id=None,
                origin=_ORIGIN,
                request=row,
                notify=False,
            )
            await notify_leave_approved(db, staff_id=staff_id, target=day, leave_type=final_type)
    except StaffLeaveError as exc:
        raise _raise_leave_error(exc) from exc
    return (
        LeaveApproveResult(
            request_id=row.id,
            staff_id=staff_id,
            date=day,
            override_type=cast(Any, final_type),
            result=_day_result(applied) if applied is not None else None,
        ),
        applied,
    )


@router.post("/requests/approve-batch", response_model=LeaveApproveBatchResult)
async def approve_leave_requests_batch(
    payload: LeaveApproveBatchInput, db: DbDep, user: AdminUser
) -> LeaveApproveBatchResult:
    """申請をまとめて承認する (1 トランザクション・1 件でも失敗したら全部やめる).

    管理者への「未割り当てとしました」は 1 通にまとめ、本人へは 1 件ずつ承認を知らせる。
    行ロックは id を並べた順で取る (同時に 2 つのまとめて承認が走っても互いに待ち合わない)。
    """
    ids = sorted(set(payload.request_ids), key=str)
    approved: list[LeaveApproveResult] = []
    applied: list[LeaveApplyResult | None] = []
    for rid in ids:
        try:
            res, app_res = await _approve_one(db, rid, user, None)
        except HTTPException as exc:
            await db.rollback()
            raise HTTPException(
                status_code=exc.status_code,
                detail={"request_id": str(rid), "message": exc.detail},
            ) from exc
        approved.append(res)
        applied.append(app_res)
    await notify_leave_results(db, applied)
    await db.commit()
    return LeaveApproveBatchResult(
        approved=approved,
        moved_visit_count=sum(len(a.moved_visit_ids) for a in applied if a is not None),
    )


@router.post("/requests/{request_id}/approve", response_model=LeaveApproveResult)
async def approve_leave_request(
    request_id: UUID, payload: LeaveApproveInput, db: DbDep, user: AdminUser
) -> LeaveApproveResult:
    """申請 1 件を承認する (``override_type`` を渡すと種類を変えて承認)."""
    try:
        res, app_res = await _approve_one(db, request_id, user, payload.override_type)
    except HTTPException:
        await db.rollback()
        raise
    await notify_leave_results(db, [app_res])
    await db.commit()
    return res


@router.post("/requests/{request_id}/reject", response_model=LeaveRejectResult)
async def reject_leave_request(
    request_id: UUID, payload: LeaveRejectInput, db: DbDep, user: AdminUser
) -> LeaveRejectResult:
    """申請を却下する (理由は必須・本人へ通知)."""
    locked = await db.scalar(
        select(PendingRequest).where(PendingRequest.id == request_id).with_for_update()
    )
    if locked is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if locked.request_type != RequestType.STAFF_OFF.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="休みの申請ではありません"
        )
    if locked.status != RequestStatus.PENDING.value or locked.applied_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="この申請は承認済みか却下済みです"
        )
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="却下の理由を入れてください"
        )
    locked.status = RequestStatus.REJECTED.value
    locked.rejected_by = user.id
    locked.rejected_at = datetime.now(UTC)
    locked.rejection_reason = reason
    await notify_leave_rejected(db, request=locked)
    staff_id, day, _t, _k, _n = _request_fields(locked)
    await db.commit()
    return LeaveRejectResult(
        request_id=request_id, target_staff_id=staff_id, target_date=day, reason=reason
    )


# ---------------------------------------------------------------------------
# 事業所の休み・勤務すべき時間数
# ---------------------------------------------------------------------------


@router.get("/closed-days", response_model=list[OfficeClosedDayRead])
async def list_closed_days(
    db: DbDep, _user: AdminUser, month: Annotated[MonthParam, Query()]
) -> list[OfficeClosedDayRead]:
    days = month_days(month)
    rows = (
        await db.scalars(
            select(OfficeClosedDay)
            .where(OfficeClosedDay.date >= days[0], OfficeClosedDay.date <= days[-1])
            .order_by(OfficeClosedDay.date)
        )
    ).all()
    return [OfficeClosedDayRead.model_validate(r) for r in rows]


@router.put("/closed-days", response_model=OfficeClosedDayRead)
async def put_closed_day(
    payload: OfficeClosedDayCreate, db: DbDep, user: AdminUser
) -> OfficeClosedDayRead:
    """事業所の休みを入れる (同じ日があれば名前を変える)。誰の休みにもならない (§2-2)."""
    row = await db.get(OfficeClosedDay, payload.date)
    if row is None:
        row = OfficeClosedDay(date=payload.date, label=payload.label, created_by=user.id)
        db.add(row)
    else:
        row.label = payload.label
    await _commit_or_409(db)
    await db.refresh(row)
    return OfficeClosedDayRead.model_validate(row)


async def _commit_or_409(db: AsyncSession) -> None:
    """同じ行を同時に新しく作ったとき (主キーの重なり) は 409 (もう一度押してもらう)."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ほかの方が同時に登録しました。表を読み直してからもう一度押してください",
        ) from exc


@router.delete("/closed-days/{day}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_closed_day(day: date, db: DbDep, _user: AdminUser) -> Response:
    row = await db.get(OfficeClosedDay, day)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/months/{month}", response_model=LeaveMonthRead)
async def put_leave_month(
    month: MonthParam, payload: LeaveMonthUpdate, db: DbDep, _user: AdminUser
) -> LeaveMonthRead:
    """常勤の従業者が勤務すべき時間数 (勤務表の右上)."""
    row = await db.get(LeaveMonth, month)
    if row is None:
        row = LeaveMonth(month=month, required_hours=payload.required_hours)
        db.add(row)
    else:
        row.required_hours = payload.required_hours
    await _commit_or_409(db)
    await db.refresh(row)
    return LeaveMonthRead.model_validate(row)


# ---------------------------------------------------------------------------
# 月の確定 (記録と本人への通知だけ・送信と予定版は #13)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ConfirmRecord:
    confirmed_at: datetime
    staff_ids: list[UUID]
    notified: int


async def _confirm_month(db: AsyncSession, data: _MonthData, user: User) -> _ConfirmRecord:
    """``data.staff`` の職員ぶん ``staff_shift_confirmations`` を upsert し本人へ通知する (commit しない).

    #13 はこの後に送信のジョブ (照合が済んだ月だけ) と予定版を足す。
    """
    now = datetime.now(UTC)
    staff_ids = [s.id for s in data.staff]
    existing = (
        {
            r.staff_id: r
            for r in (
                await db.scalars(
                    select(StaffShiftConfirmation).where(
                        StaffShiftConfirmation.month == data.month,
                        StaffShiftConfirmation.staff_id.in_(staff_ids),
                    )
                )
            ).all()
        }
        if staff_ids
        else {}
    )
    notified = 0
    for sid in staff_ids:
        row = existing.get(sid)
        if row is None:
            db.add(
                StaffShiftConfirmation(
                    staff_id=sid, month=data.month, confirmed_by=user.id, confirmed_at=now
                )
            )
        else:
            row.confirmed_by = user.id
            row.confirmed_at = now
        notified += await notify_shift_confirmed(db, staff_id=sid, month=data.month)
    await db.flush()
    return _ConfirmRecord(confirmed_at=now, staff_ids=staff_ids, notified=notified)


def _short_staff(data: _MonthData, required: int) -> list[LeaveShortStaff]:
    out: list[LeaveShortStaff] = []
    for s in data.staff:
        if s.employment_type != "full_time":
            continue
        work = data.working.get(s.id, set())
        total = sum(
            day_holiday_value(_leave_type(data, s.id, d), d.weekday() in work)[0] for d in data.days
        )
        if total < required:
            out.append(
                LeaveShortStaff(
                    staff_id=s.id,
                    name=s.name,
                    holidays=total,
                    required_holidays=required,
                    shortfall=required - total,
                )
            )
    return out


@router.post("/month/{month}/confirm", response_model=LeaveConfirmResult)
async def confirm_leave_month(
    month: MonthParam,
    db: DbDep,
    user: AdminUser,
    payload: Annotated[LeaveConfirmInput | None, Body()] = None,
) -> LeaveConfirmResult:
    """この月を確定して通知する (記録と本人への通知だけ・確定後も表で変えられる).

    ``staff_ids`` を渡せばその人だけ (表に載っている人に限る)、無ければ表の全員。
    申請中の残り・足りない人も、確定した人の分だけを返す。
    """
    data = await _load_month(db, month, with_requests=True)
    if payload is not None and payload.staff_ids is not None:
        wanted = set(payload.staff_ids)
        unknown = wanted - {s.id for s in data.staff}
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="この月の表に載っていない職員が含まれています",
            )
        data.staff = [s for s in data.staff if s.id in wanted]
        data.pending = [r for r in data.pending if r.staff_id in wanted]
    record = await _confirm_month(db, data, user)
    leave_month = await db.get(LeaveMonth, data.month)
    result = LeaveConfirmResult(
        month=data.month,
        confirmed_at=record.confirmed_at,
        confirmed_staff_count=len(record.staff_ids),
        notified_count=record.notified,
        pending_remaining=len(data.pending),
        short_staff=_short_staff(data, required_holidays(data.month, data.closed.keys())),
        required_hours=leave_month.required_hours if leave_month is not None else None,
    )
    await db.commit()
    return result


__all__ = ["OFF_WARN_THRESHOLD", "router"]
