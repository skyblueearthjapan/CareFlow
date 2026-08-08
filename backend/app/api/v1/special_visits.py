"""特別訪問週間 (special visit week) endpoints — 設計 §4.

`docs/plans/special-visit-week-design.md`:
基本の固定訪問はそのまま生かしたまま、○ (extra) で追加枠を週ごとにプールへ積み、
毎週その週だけ配置する「上乗せ型」機能。固定訪問を日単位で退避 (displaced) する
トグルも本ルーターが扱う。

**恒久パターン (``patient_fixed_visits``) には一切書き込まない** (設計 §2)。
退避 = ``visits.deleted_at`` を立てる soft-delete、復元 = ``deleted_at`` を NULL に戻す
(行が消えている場合のみ snapshot から再作成)。

すべて require_role("admin", "manager")。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, tuple_

from app.core.deps import DbDep, require_role
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.special_visit import (
    MARK_KIND_DISPLACED,
    MARK_KIND_EXTRA,
    MARK_STATUS_CANCELLED,
    MARK_STATUS_PLACED,
    MARK_STATUS_POOL,
    PERIOD_STATUS_ACTIVE,
    SpecialVisitMark,
    SpecialVisitPeriod,
)
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import VISIT_SOURCE_MANUAL_WEEK, VISIT_STATUS_PLANNED, Visit
from app.schemas.special_visit import (
    CalendarDay,
    CalendarRead,
    CalendarWeek,
    FixedVisitRead,
    LastPlacement,
    MarkCreate,
    MarkRead,
    PeriodCreate,
    PeriodRead,
    PeriodUpdate,
    PlacedSummary,
    PlaceRequest,
    PlaceResponse,
    PoolPatient,
    PoolPeriod,
    PoolTicketRead,
    PreferredSlot,
)
from app.services.patient_excel.schema import OFFICE_CODE_TO_SHORT
from app.services.scheduling.auto_allocator_v2 import _extract_weekly_entries, _parse_hhmm

router = APIRouter()

AdminManager = Annotated[User, Depends(require_role("admin", "manager"))]

# Layer1 が生成する固定訪問と同じ type / source (復元時の再作成で使う).
_FIXED_VISIT_TYPE = "regular"
_FIXED_VISIT_SOURCE = "auto"
# 配置 (place) 時に duration が全く解決できない場合の最終フォールバック (分).
_DEFAULT_SERVICE_MINUTES = 30


# ---------------------------------------------------------------------------
# 小さなヘルパ
# ---------------------------------------------------------------------------


def _hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def _add_minutes(start: time, minutes: int) -> time:
    """``start`` に ``minutes`` を足した時刻 (日跨ぎは 23:59 に丸める)."""
    total = start.hour * 60 + start.minute + max(minutes, 0)
    if total >= 24 * 60:
        return time(23, 59)
    return time(total // 60, total % 60)


def _week_monday(iso_year: int, iso_week: int) -> date:
    return date.fromisocalendar(iso_year, iso_week, 1)


def _iso_weeks_between(start: date, end: date) -> list[tuple[int, int]]:
    """``start``〜``end`` を含む全 ISO 週 (iso_year, iso_week) を昇順で返す."""
    cursor = start - timedelta(days=start.weekday())
    last = end - timedelta(days=end.weekday())
    weeks: list[tuple[int, int]] = []
    while cursor <= last:
        iy, iw, _ = cursor.isocalendar()
        weeks.append((iy, iw))
        cursor += timedelta(days=7)
    return weeks


def _course_label(office: Office | None, course_code: str | None) -> str | None:
    """UI 表示用ラベル (拠点短縮 + コード, 例: 稲A).

    0059 の拠点マスタ駆動 (``offices.short_label``) を優先し、未設定なら legacy 既定
    (``patient_excel.schema.OFFICE_CODE_TO_SHORT``) にフォールバックする。
    """
    if not course_code:
        return None
    if office is None:
        return course_code
    code = office.code or ""
    short = office.short_label or OFFICE_CODE_TO_SHORT.get(code, code)
    return f"{short}{course_code}" if short else course_code


def _mark_read(mark: SpecialVisitMark, placed_summary: PlacedSummary | None = None) -> MarkRead:
    return MarkRead(
        id=mark.id,
        period_id=mark.period_id,
        patient_id=mark.patient_id,
        iso_year=mark.iso_year,
        iso_week=mark.iso_week,
        weekday=mark.weekday,
        kind=mark.kind,
        status=mark.status,
        placed_visit_id=mark.placed_visit_id,
        placed_summary=placed_summary,
    )


async def _get_period(db, period_id: UUID) -> SpecialVisitPeriod:
    period = await db.scalar(select(SpecialVisitPeriod).where(SpecialVisitPeriod.id == period_id))
    if period is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return period


async def _get_mark(db, mark_id: UUID) -> SpecialVisitMark:
    mark = await db.scalar(select(SpecialVisitMark).where(SpecialVisitMark.id == mark_id))
    if mark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return mark


async def _week_is_generated(db, iso_year: int, iso_week: int) -> bool:
    """当該週が「生成済み」か = その週の Course 行が存在するか (設計の判定基準).

    患者ごとの visit 有無ではなく **週生成そのもの** を見る (訪問ゼロの患者でも
    生成済み週なら PFV 投影を出さない)。
    """
    row = await db.scalar(
        select(Course.id)
        .where(
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.deleted_at.is_(None),
        )
        .limit(1)
    )
    return row is not None


async def _staff_names(db, staff_ids: set[UUID]) -> dict[UUID, str]:
    if not staff_ids:
        return {}
    rows = await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids)))
    return {r.id: r.name for r in rows}


async def _resolve_service_minutes(db, patient: Patient) -> int:
    """配置 (place) 時の所要時間 (分) を解決する.

    ``place`` の入力は ``start_time`` のみなので、①患者の固定枠 (PFV) の
    ``duration_min`` → ②希望パターン (weekly_pattern) の service_minutes →
    ③既定 30 分 の順にフォールバックする。
    """
    duration = await db.scalar(
        select(PatientFixedVisit.duration_min)
        .where(
            PatientFixedVisit.patient_id == patient.id,
            PatientFixedVisit.mode == "normal",
        )
        .order_by(PatientFixedVisit.weekday, PatientFixedVisit.slot_index)
        .limit(1)
    )
    if duration:
        return int(duration)
    for _wd, _st, service_minutes, _tt, _ps, _pe in _extract_weekly_entries(patient):
        if service_minutes and service_minutes > 0:
            return int(service_minutes)
    return _DEFAULT_SERVICE_MINUTES


async def _visit_is_alive(db, visit_id: UUID | None) -> Visit | None:
    """``visit_id`` の訪問が生きていれば返す (自己回復判定に使う)."""
    if visit_id is None:
        return None
    return await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))


async def _placed_summary_for(db, visit_id: UUID | None) -> PlacedSummary | None:
    """配置先訪問から ``placed_summary`` (● のツールチップ用) を組み立てる."""
    if visit_id is None:
        return None
    row = (
        await db.execute(
            select(Visit, Course, Office)
            .outerjoin(Course, Visit.course_id == Course.id)
            .outerjoin(Office, Course.office_id == Office.id)
            .where(Visit.id == visit_id, Visit.deleted_at.is_(None))
        )
    ).first()
    if row is None:
        return None
    visit, course, office = row
    return PlacedSummary(
        start_time=_hhmm(visit.start_time),
        course_label=_course_label(office, course.code if course is not None else None),
    )


# ---------------------------------------------------------------------------
# 期間 (special_visit_periods)
# ---------------------------------------------------------------------------


@router.post(
    "/special-visit-periods",
    response_model=PeriodRead,
    status_code=status.HTTP_201_CREATED,
    summary="特別訪問週間の期間を作成 (admin/manager)",
)
async def create_period(payload: PeriodCreate, db: DbDep, _user: AdminManager) -> PeriodRead:
    patient = await db.scalar(
        select(Patient).where(Patient.id == payload.patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    # 同一患者で active な期間は同時に 1 本のみ (設計 §1・アプリ層で担保).
    existing = await db.scalar(
        select(SpecialVisitPeriod.id).where(
            SpecialVisitPeriod.patient_id == payload.patient_id,
            SpecialVisitPeriod.status == PERIOD_STATUS_ACTIVE,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="この患者には既に有効な特別訪問週間があります",
        )

    period = SpecialVisitPeriod(
        patient_id=payload.patient_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        weekly_target=payload.weekly_target,
        note=payload.note,
        status=PERIOD_STATUS_ACTIVE,
    )
    db.add(period)
    await db.commit()
    await db.refresh(period)
    return PeriodRead.model_validate(period)


@router.get(
    "/special-visit-periods",
    response_model=list[PeriodRead],
    summary="特別訪問週間の期間一覧 (admin/manager)",
)
async def list_periods(
    db: DbDep,
    _user: AdminManager,
    patient_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[PeriodRead]:
    stmt = select(SpecialVisitPeriod)
    if patient_id is not None:
        stmt = stmt.where(SpecialVisitPeriod.patient_id == patient_id)
    if not include_inactive:
        stmt = stmt.where(SpecialVisitPeriod.status == PERIOD_STATUS_ACTIVE)
    stmt = stmt.order_by(SpecialVisitPeriod.start_date.desc())
    rows = (await db.scalars(stmt)).all()
    return [PeriodRead.model_validate(r) for r in rows]


@router.patch(
    "/special-visit-periods/{period_id}",
    response_model=PeriodRead,
    summary="期間の延長 / 目標変更 / 終了 (admin/manager)",
)
async def update_period(
    period_id: UUID, payload: PeriodUpdate, db: DbDep, _user: AdminManager
) -> PeriodRead:
    period = await _get_period(db, period_id)
    data = payload.model_dump(exclude_unset=True)

    # status / end_date / weekly_target は NOT NULL — 明示 null は「変更なし」として落とす.
    for key in ("end_date", "weekly_target", "status"):
        if key in data and data[key] is None:
            data.pop(key)

    for key, value in data.items():
        setattr(period, key, value)

    if period.end_date < period.start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be >= start_date",
        )

    # 再 active 化は「active は 1 本のみ」を壊しうるので重複チェックする.
    if period.status == PERIOD_STATUS_ACTIVE:
        dup = await db.scalar(
            select(SpecialVisitPeriod.id).where(
                SpecialVisitPeriod.patient_id == period.patient_id,
                SpecialVisitPeriod.status == PERIOD_STATUS_ACTIVE,
                SpecialVisitPeriod.id != period.id,
            )
        )
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="この患者には既に有効な特別訪問週間があります",
            )

    await db.commit()
    await db.refresh(period)
    return PeriodRead.model_validate(period)


# ---------------------------------------------------------------------------
# カレンダー
# ---------------------------------------------------------------------------


@router.get(
    "/special-visit-periods/{period_id}/calendar",
    response_model=CalendarRead,
    summary="期間内の週 × 曜日カレンダー (admin/manager)",
)
async def get_calendar(period_id: UUID, db: DbDep, _user: AdminManager) -> CalendarRead:
    period = await _get_period(db, period_id)
    week_keys = _iso_weeks_between(period.start_date, period.end_date)

    # ---- マーク (取消済みは除外) ----------------------------------------
    marks = (
        await db.scalars(
            select(SpecialVisitMark).where(
                SpecialVisitMark.period_id == period.id,
                SpecialVisitMark.status != MARK_STATUS_CANCELLED,
            )
        )
    ).all()
    extra_by_cell: dict[tuple[int, int, int], SpecialVisitMark] = {}
    displaced_by_cell: dict[tuple[int, int, int], SpecialVisitMark] = {}
    placed_visit_ids: set[UUID] = set()
    for mark in marks:
        cell = (mark.iso_year, mark.iso_week, mark.weekday)
        if mark.kind == MARK_KIND_EXTRA:
            extra_by_cell[cell] = mark
        else:
            displaced_by_cell[cell] = mark
        if mark.placed_visit_id is not None:
            placed_visit_ids.add(mark.placed_visit_id)

    # ---- 生成済み週 (= その週の Course 行が存在するか) --------------------
    generated: set[tuple[int, int]] = set()
    if week_keys:
        rows = await db.execute(
            select(Course.iso_year, Course.iso_week)
            .where(
                tuple_(Course.iso_year, Course.iso_week).in_(week_keys),
                Course.deleted_at.is_(None),
            )
            .distinct()
        )
        generated = {(r.iso_year, r.iso_week) for r in rows}

    # ---- 生成済み週の実 visit ------------------------------------------
    visits_by_date: dict[date, list[tuple[Visit, Course | None, Office | None]]] = {}
    staff_ids: set[UUID] = set()
    if week_keys:
        span_start = _week_monday(*week_keys[0])
        span_end = _week_monday(*week_keys[-1]) + timedelta(days=5)
        visit_rows = await db.execute(
            select(Visit, Course, Office)
            .outerjoin(Course, Visit.course_id == Course.id)
            .outerjoin(Office, Course.office_id == Office.id)
            .where(
                Visit.patient_id == period.patient_id,
                Visit.deleted_at.is_(None),
                Visit.status == VISIT_STATUS_PLANNED,
                Visit.visit_date >= span_start,
                Visit.visit_date <= span_end,
            )
            .order_by(Visit.visit_date, Visit.start_time)
        )
        for visit, course, office in visit_rows:
            visits_by_date.setdefault(visit.visit_date, []).append((visit, course, office))
            resolved = (course.assigned_staff_id if course is not None else None) or (
                visit.primary_staff_id
            )
            if resolved is not None:
                staff_ids.add(resolved)
    staff_name_by_id = await _staff_names(db, staff_ids)

    # ---- 未生成週の PFV 投影 (mode='normal') ------------------------------
    pfv_by_weekday: dict[int, list[tuple[PatientFixedVisit, CourseTemplate | None, Office | None]]]
    pfv_by_weekday = {}
    pfv_rows = await db.execute(
        select(PatientFixedVisit, CourseTemplate, Office)
        .outerjoin(CourseTemplate, PatientFixedVisit.course_template_id == CourseTemplate.id)
        .outerjoin(Office, CourseTemplate.office_id == Office.id)
        .where(
            PatientFixedVisit.patient_id == period.patient_id,
            PatientFixedVisit.mode == "normal",
        )
        .order_by(PatientFixedVisit.weekday, PatientFixedVisit.start_time)
    )
    for pfv, template, office in pfv_rows:
        pfv_by_weekday.setdefault(pfv.weekday, []).append((pfv, template, office))

    # ---- 希望訪問カレンダー (patients.weekly_pattern) ---------------------
    patient = await db.get(Patient, period.patient_id)
    preferred_by_weekday: dict[int, list[PreferredSlot]] = {}
    if patient is not None:
        for wd, start_t, service_minutes, _tt, ps_raw, pe_raw in _extract_weekly_entries(patient):
            # ps_raw が無い entry は「希望時刻の指定なし」(既定値で埋められただけ) なので出さない.
            if ps_raw is None:
                continue
            end_t = _parse_hhmm(pe_raw) or _add_minutes(start_t, service_minutes)
            preferred_by_weekday.setdefault(wd, []).append(
                PreferredSlot(start=_hhmm(start_t), end=_hhmm(end_t))
            )

    # ---- 配置済み ○ の placed_summary ------------------------------------
    placed_summary_by_visit: dict[UUID, PlacedSummary] = {}
    for visit_id in placed_visit_ids:
        summary = await _placed_summary_for(db, visit_id)
        if summary is not None:
            placed_summary_by_visit[visit_id] = summary

    # ---- 組み立て --------------------------------------------------------
    weeks_out: list[CalendarWeek] = []
    for iso_year, iso_week in week_keys:
        monday = _week_monday(iso_year, iso_week)
        is_generated = (iso_year, iso_week) in generated
        days: list[CalendarDay] = []
        total = 0
        # 列は月〜土 (日曜は対象外).
        for weekday in range(6):
            day_date = monday + timedelta(days=weekday)
            cell = (iso_year, iso_week, weekday)
            extra_mark = extra_by_cell.get(cell)
            displaced_mark = displaced_by_cell.get(cell)

            fixed_visits: list[FixedVisitRead] = []
            if is_generated:
                for visit, course, office in visits_by_date.get(day_date, []):
                    # 配置済み ○ の訪問は「固定訪問の残数」ではないので除外 (二重計上防止).
                    if visit.id in placed_visit_ids:
                        continue
                    resolved = (course.assigned_staff_id if course is not None else None) or (
                        visit.primary_staff_id
                    )
                    fixed_visits.append(
                        FixedVisitRead(
                            visit_id=visit.id,
                            start_time=_hhmm(visit.start_time),
                            end_time=_hhmm(visit.end_time),
                            course_label=_course_label(
                                office, course.code if course is not None else None
                            ),
                            staff_name=staff_name_by_id.get(resolved) if resolved else None,
                            generated=True,
                        )
                    )
            elif displaced_mark is None:
                # 未生成週は PFV の投影. 退避マークがある曜日は週生成で skip されるため出さない.
                for pfv, template, office in pfv_by_weekday.get(weekday, []):
                    fixed_visits.append(
                        FixedVisitRead(
                            visit_id=None,
                            start_time=_hhmm(pfv.start_time),
                            end_time=_hhmm(_add_minutes(pfv.start_time, pfv.duration_min)),
                            course_label=_course_label(
                                office, template.label if template is not None else None
                            ),
                            staff_name=None,
                            generated=False,
                        )
                    )

            days.append(
                CalendarDay(
                    weekday=weekday,
                    date=day_date,
                    fixed_visits=fixed_visits,
                    extra_mark=(
                        _mark_read(
                            extra_mark,
                            placed_summary_by_visit.get(extra_mark.placed_visit_id)
                            if extra_mark.placed_visit_id
                            else None,
                        )
                        if extra_mark is not None
                        else None
                    ),
                    displaced_mark=(
                        _mark_read(
                            displaced_mark,
                            placed_summary_by_visit.get(displaced_mark.placed_visit_id)
                            if displaced_mark.placed_visit_id
                            else None,
                        )
                        if displaced_mark is not None
                        else None
                    ),
                    preferred=preferred_by_weekday.get(weekday, []),
                )
            )
            # 週合計 = 固定訪問の残数 + extra ○ (pool/placed 両方) + displaced チケット数 (§3).
            total += len(fixed_visits)
            total += 1 if extra_mark is not None else 0
            total += 1 if displaced_mark is not None else 0

        weeks_out.append(
            CalendarWeek(
                iso_year=iso_year,
                iso_week=iso_week,
                week_monday=monday,
                days=days,
                total=total,
                target_met=total >= period.weekly_target,
            )
        )

    return CalendarRead(period=PeriodRead.model_validate(period), weeks=weeks_out)


# ---------------------------------------------------------------------------
# マーク: ○ 追加 / 取消
# ---------------------------------------------------------------------------


@router.post(
    "/special-visit-periods/{period_id}/marks",
    response_model=MarkRead,
    status_code=status.HTTP_201_CREATED,
    summary="○ (追加枠) をセルに立てる (admin/manager)",
)
async def create_extra_mark(
    period_id: UUID, payload: MarkCreate, db: DbDep, _user: AdminManager
) -> MarkRead:
    period = await _get_period(db, period_id)

    # 期間範囲ガード (レビュー補強): FE はグレーアウトで防ぐが、API 直叩きで
    # 期間外の週に○を立てると週合計が水増しされるため 422 で防ぐ。
    if (payload.iso_year, payload.iso_week) not in set(
        _iso_weeks_between(period.start_date, period.end_date)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="指定週は期間の範囲外です",
        )

    dup = await db.scalar(
        select(SpecialVisitMark.id).where(
            SpecialVisitMark.period_id == period.id,
            SpecialVisitMark.iso_year == payload.iso_year,
            SpecialVisitMark.iso_week == payload.iso_week,
            SpecialVisitMark.weekday == payload.weekday,
            SpecialVisitMark.kind == MARK_KIND_EXTRA,
            SpecialVisitMark.status != MARK_STATUS_CANCELLED,
        )
    )
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このセルには既に追加枠があります",
        )

    mark = SpecialVisitMark(
        period_id=period.id,
        patient_id=period.patient_id,
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        weekday=payload.weekday,
        kind=MARK_KIND_EXTRA,
        status=MARK_STATUS_POOL,
    )
    db.add(mark)
    await db.commit()
    await db.refresh(mark)
    return _mark_read(mark)


@router.delete(
    "/special-visit-marks/{mark_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="○ の取消 (配置済みは force=true で配置先訪問も削除)",
)
async def delete_mark(
    mark_id: UUID,
    db: DbDep,
    _user: AdminManager,
    force: Annotated[bool, Query()] = False,
) -> None:
    mark = await _get_mark(db, mark_id)
    if mark.kind == MARK_KIND_DISPLACED:
        # 退避マークの解除は restore が正 (設計 §4).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="displaced マークの解除は restore を使ってください",
        )
    if mark.status == MARK_STATUS_CANCELLED:
        return None

    if mark.status == MARK_STATUS_PLACED:
        placed = await _visit_is_alive(db, mark.placed_visit_id)
        if placed is not None:
            if not force:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="配置済みです。force=true で配置先訪問も削除します",
                )
            placed.deleted_at = datetime.now(UTC)

    mark.status = MARK_STATUS_CANCELLED
    mark.placed_visit_id = None
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# マーク: 固定訪問の退避 / 復元 (設計 §2)
# ---------------------------------------------------------------------------


@router.post(
    "/special-visit-periods/{period_id}/displace",
    response_model=MarkRead,
    status_code=status.HTTP_201_CREATED,
    summary="固定訪問をこの日だけプールへ退避 (admin/manager)",
)
async def displace_fixed_visit(
    period_id: UUID, payload: MarkCreate, db: DbDep, _user: AdminManager
) -> MarkRead:
    period = await _get_period(db, period_id)

    dup = await db.scalar(
        select(SpecialVisitMark.id).where(
            SpecialVisitMark.period_id == period.id,
            SpecialVisitMark.iso_year == payload.iso_year,
            SpecialVisitMark.iso_week == payload.iso_week,
            SpecialVisitMark.weekday == payload.weekday,
            SpecialVisitMark.kind == MARK_KIND_DISPLACED,
            SpecialVisitMark.status != MARK_STATUS_CANCELLED,
        )
    )
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="この日は既に退避済みです",
        )

    is_generated = await _week_is_generated(db, payload.iso_year, payload.iso_week)
    snapshot: dict

    if is_generated:
        day_date = _week_monday(payload.iso_year, payload.iso_week) + timedelta(
            days=payload.weekday
        )
        # 同期間の配置済み ○ の訪問は「固定訪問」ではないので退避対象から除く.
        placed_ids = set(
            (
                await db.scalars(
                    select(SpecialVisitMark.placed_visit_id).where(
                        SpecialVisitMark.period_id == period.id,
                        SpecialVisitMark.status == MARK_STATUS_PLACED,
                        SpecialVisitMark.placed_visit_id.is_not(None),
                    )
                )
            ).all()
        )
        rows = (
            await db.execute(
                select(Visit, Course, Office)
                .outerjoin(Course, Visit.course_id == Course.id)
                .outerjoin(Office, Course.office_id == Office.id)
                .where(
                    Visit.patient_id == period.patient_id,
                    Visit.visit_date == day_date,
                    Visit.deleted_at.is_(None),
                    Visit.status == VISIT_STATUS_PLANNED,
                )
                .order_by(Visit.start_time)
            )
        ).all()
        targets = [(v, c, o) for (v, c, o) in rows if v.id not in placed_ids]
        if not targets:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="この日に退避できる固定訪問がありません",
            )
        now = datetime.now(UTC)
        snapshot = {
            "visits": [
                {
                    "visit_id": str(visit.id),
                    "start_time": _hhmm(visit.start_time),
                    "end_time": _hhmm(visit.end_time),
                    "course_id": str(visit.course_id) if visit.course_id else None,
                    "course_label": _course_label(
                        office, course.code if course is not None else None
                    ),
                    "primary_staff_id": (
                        str(visit.primary_staff_id) if visit.primary_staff_id else None
                    ),
                }
                for visit, course, office in targets
            ]
        }
        for visit, _course, _office in targets:
            visit.deleted_at = now
    else:
        # 未生成週はマークのみ。週生成 (Layer1) が当該曜日の PFV 展開を skip する。
        has_pfv = await db.scalar(
            select(PatientFixedVisit.id)
            .where(
                PatientFixedVisit.patient_id == period.patient_id,
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.weekday == payload.weekday,
            )
            .limit(1)
        )
        if has_pfv is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="この日に退避できる固定訪問がありません",
            )
        snapshot = {"pfv": True}

    mark = SpecialVisitMark(
        period_id=period.id,
        patient_id=period.patient_id,
        iso_year=payload.iso_year,
        iso_week=payload.iso_week,
        weekday=payload.weekday,
        kind=MARK_KIND_DISPLACED,
        status=MARK_STATUS_POOL,
        displaced_snapshot=snapshot,
    )
    db.add(mark)
    await db.commit()
    await db.refresh(mark)
    return _mark_read(mark)


@router.post(
    "/special-visit-marks/{mark_id}/restore",
    response_model=MarkRead,
    summary="退避の解除 (配置済みは force=true が必須)",
)
async def restore_mark(
    mark_id: UUID,
    db: DbDep,
    _user: AdminManager,
    force: Annotated[bool, Query()] = False,
) -> MarkRead:
    mark = await _get_mark(db, mark_id)
    if mark.kind != MARK_KIND_DISPLACED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="restore は displaced マーク専用です",
        )
    if mark.status == MARK_STATUS_CANCELLED:
        # 既に解除済み (トグルの二度押し) は冪等に成功扱い.
        return _mark_read(mark)

    if mark.status == MARK_STATUS_PLACED:
        placed = await _visit_is_alive(db, mark.placed_visit_id)
        if placed is not None:
            if not force:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="配置済みです。force=true で配置先訪問を削除してから復元します",
                )
            placed.deleted_at = datetime.now(UTC)

    snapshot = mark.displaced_snapshot or {}
    visits_snapshot = snapshot.get("visits")
    if isinstance(visits_snapshot, list):
        day_date = _week_monday(mark.iso_year, mark.iso_week) + timedelta(days=mark.weekday)
        for entry in visits_snapshot:
            if not isinstance(entry, dict):
                continue
            await _restore_one_visit(db, mark=mark, entry=entry, day_date=day_date)

    mark.status = MARK_STATUS_CANCELLED
    mark.placed_visit_id = None
    await db.commit()
    await db.refresh(mark)
    return _mark_read(mark)


async def _restore_one_visit(db, *, mark: SpecialVisitMark, entry: dict, day_date: date) -> None:
    """snapshot 1 件を復元する (deleted_at 解除、行が消えていれば再作成)."""
    raw_id = entry.get("visit_id")
    visit: Visit | None = None
    if raw_id:
        try:
            visit = await db.scalar(select(Visit).where(Visit.id == UUID(str(raw_id))))
        except ValueError:
            visit = None
    if visit is not None:
        visit.deleted_at = None
        return

    start_t = _parse_hhmm(entry.get("start_time"))
    end_t = _parse_hhmm(entry.get("end_time"))
    if start_t is None or end_t is None:
        return
    course_id = entry.get("course_id")
    staff_id = entry.get("primary_staff_id")
    db.add(
        Visit(
            patient_id=mark.patient_id,
            visit_date=day_date,
            start_time=start_t,
            end_time=end_t,
            type=_FIXED_VISIT_TYPE,
            status=VISIT_STATUS_PLANNED,
            source=_FIXED_VISIT_SOURCE,
            required_staff_count=1,
            course_id=UUID(str(course_id)) if course_id else None,
            primary_staff_id=UUID(str(staff_id)) if staff_id else None,
            note="特別訪問週間: 退避の復元",
        )
    )


# ---------------------------------------------------------------------------
# プール / 配置
# ---------------------------------------------------------------------------


@router.get(
    "/special-visit-marks/pool",
    response_model=list[PoolTicketRead],
    summary="指定週の未配置チケット一覧 (admin/manager)",
)
async def list_pool(
    db: DbDep,
    _user: AdminManager,
    iso_year: Annotated[int, Query(ge=2000, le=2100)],
    iso_week: Annotated[int, Query(ge=1, le=53)],
    office_id: Annotated[UUID | None, Query()] = None,
) -> list[PoolTicketRead]:
    stmt = (
        select(SpecialVisitMark, SpecialVisitPeriod, Patient)
        .join(SpecialVisitPeriod, SpecialVisitMark.period_id == SpecialVisitPeriod.id)
        .join(Patient, SpecialVisitMark.patient_id == Patient.id)
        .where(
            SpecialVisitMark.iso_year == iso_year,
            SpecialVisitMark.iso_week == iso_week,
            SpecialVisitMark.status != MARK_STATUS_CANCELLED,
            SpecialVisitPeriod.status == PERIOD_STATUS_ACTIVE,
            Patient.deleted_at.is_(None),
        )
        .order_by(SpecialVisitMark.weekday, Patient.code)
    )
    if office_id is not None:
        stmt = stmt.where(Patient.primary_office_id == office_id)
    rows = (await db.execute(stmt)).all()

    tickets: list[PoolTicketRead] = []
    last_placement_cache: dict[UUID, LastPlacement | None] = {}
    service_minutes_cache: dict[UUID, int] = {}
    for mark, period, patient in rows:
        if mark.status == MARK_STATUS_PLACED:
            # 自己回復: 配置済みでも訪問が消えた / soft-delete 済みなら pool 扱いで返す.
            if await _visit_is_alive(db, mark.placed_visit_id) is not None:
                continue
        if period.id not in last_placement_cache:
            last_placement_cache[period.id] = await _last_placement(db, period_id=period.id)
        if patient.id not in service_minutes_cache:
            # place と同一計算 (PFV 優先) — 提案と実配置の枠長を一致させる.
            service_minutes_cache[patient.id] = await _resolve_service_minutes(db, patient)
        tickets.append(
            PoolTicketRead(
                mark=_mark_read(mark),
                patient=PoolPatient.model_validate(patient),
                period=PoolPeriod(
                    id=period.id,
                    weekly_target=period.weekly_target,
                    end_date=period.end_date,
                ),
                last_placement=last_placement_cache[period.id],
                service_minutes=service_minutes_cache[patient.id],
            )
        )
    return tickets


async def _last_placement(db, *, period_id: UUID) -> LastPlacement | None:
    """同一期間内の直近の placed マークの配置先 (参考ヒント・強制しない)."""
    row = (
        await db.execute(
            select(SpecialVisitMark, Visit, Course, Office, Staff)
            .join(Visit, SpecialVisitMark.placed_visit_id == Visit.id)
            .outerjoin(Course, Visit.course_id == Course.id)
            .outerjoin(Office, Course.office_id == Office.id)
            .outerjoin(Staff, Visit.primary_staff_id == Staff.id)
            .where(
                SpecialVisitMark.period_id == period_id,
                SpecialVisitMark.status == MARK_STATUS_PLACED,
                Visit.deleted_at.is_(None),
            )
            .order_by(Visit.visit_date.desc(), Visit.start_time.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    mark, visit, course, office, staff = row
    return LastPlacement(
        weekday=mark.weekday,
        start_time=_hhmm(visit.start_time),
        course_label=_course_label(office, course.code if course is not None else None),
        staff_name=staff.name if staff is not None else None,
    )


@router.post(
    "/special-visit-marks/{mark_id}/place",
    response_model=PlaceResponse,
    summary="チケットをその週のコースへ配置 (admin/manager)",
)
async def place_mark(
    mark_id: UUID, payload: PlaceRequest, db: DbDep, _user: AdminManager
) -> PlaceResponse:
    mark = await _get_mark(db, mark_id)
    if mark.status == MARK_STATUS_CANCELLED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="取消済みのチケットです")
    if mark.status == MARK_STATUS_PLACED:
        # 自己回復: 訪問が生きている場合のみ二重配置として弾く.
        if await _visit_is_alive(db, mark.placed_visit_id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="既に配置済みです")

    if payload.course_id is not None:
        course = await db.scalar(
            select(Course).where(Course.id == payload.course_id, Course.deleted_at.is_(None))
        )
    else:
        # propose-slots の候補は course_id を持たないため (office_id, course_code) と
        # mark 側の週・曜日で当該週のコース実体を解決する。
        course = await db.scalar(
            select(Course).where(
                Course.office_id == payload.office_id,
                Course.code == payload.course_code,
                Course.iso_year == mark.iso_year,
                Course.iso_week == mark.iso_week,
                Course.weekday == mark.weekday,
                Course.deleted_at.is_(None),
            )
        )
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    # 対象週・曜日の妥当性 (設計 §4).
    if (
        course.iso_year != mark.iso_year
        or course.iso_week != mark.iso_week
        or course.weekday != mark.weekday
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="course が対象週・曜日と一致しません",
        )

    patient = await db.get(Patient, mark.patient_id)
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    start_t = _parse_hhmm(payload.start_time)
    if start_t is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid start_time"
        )
    service_minutes = await _resolve_service_minutes(db, patient)
    end_t = _add_minutes(start_t, service_minutes)
    visit_date = _week_monday(mark.iso_year, mark.iso_week) + timedelta(days=mark.weekday)

    visit = Visit(
        patient_id=mark.patient_id,
        visit_date=visit_date,
        start_time=start_t,
        end_time=end_t,
        type=_FIXED_VISIT_TYPE,
        status=VISIT_STATUS_PLANNED,
        # PFV は作らない = この週だけの決定 (週生成・固定枠戻の両方から保護される source).
        source=VISIT_SOURCE_MANUAL_WEEK,
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
        note="特別訪問週間: 追加枠の配置",
    )
    db.add(visit)
    await db.flush()

    mark.status = MARK_STATUS_PLACED
    mark.placed_visit_id = visit.id
    await db.commit()
    await db.refresh(mark)

    summary = await _placed_summary_for(db, mark.placed_visit_id)
    return PlaceResponse(mark=_mark_read(mark, summary), visit_id=visit.id)
