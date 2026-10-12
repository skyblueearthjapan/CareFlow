"""らく助 専門エージェント向けの読み取り専用 API (admin のみ).

docs/plans/agent-skills/api-additions-2026-10-12.md が正典。

* ``GET /agent/week-context`` — 週のならし道具 (``docs/tools/week-leveling/extract.py``)
  が psql で集めている材料を 1 回で返す。``_week_sql`` / ``_history_sql`` と同じ
  意味 (deleted_at・cancelled・JST の壁時計・イベントは「壁時計を UTC として保存」)
  に揃えてあり、道具はこの応答へ乗り換えられる。追加で、固定訪問の枠
  (可動域・赤ピン)・同住所リンク・コース・同行・特別訪問週間・保留プール・担当歴の
  集計も載せる。
* ``GET /agent/staff-week`` — 職員ごとの 1 週 (勤務・休み/時間変更・予定・訪問件数)。

どちらも DB を変更しない。クエリ回数は件数に依らず一定 (N+1 なし)。
応答は Pydantic モデルを通さない素の dict (フィールド名は上記の docs に一覧)。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select

from app.core.deps import DbDep, require_role
from app.models.accompaniment import Accompaniment
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient, PatientAllowedOffice
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.patient_ng_staff import PatientNgStaff
from app.models.patient_same_address_link import PatientSameAddressLink
from app.models.scheduling_settings import SchedulingSettings
from app.models.special_visit import SpecialVisitMark, SpecialVisitPeriod
from app.models.staff import (
    Staff,
    StaffEvent,
    StaffSecondaryOffice,
    StaffShift,
    StaffWeeklyOverride,
)
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

router = APIRouter()

AdminUser = Annotated[User, Depends(require_role("admin"))]
IsoYear = Annotated[int, Query(ge=2000, le=2100)]
IsoWeek = Annotated[int, Query(ge=1, le=53)]

HISTORY_DAYS = 28  # extract.py _history_sql と同じ (直前 4 週)


# ---------------------------------------------------------------------------
# 共通の小道具
# ---------------------------------------------------------------------------


def _monday(iso_year: int, iso_week: int) -> date:
    try:
        return date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={iso_year} week={iso_week}",
        ) from exc


def _s(v: Any) -> Any:
    """UUID / date / time / Decimal を JSON の素の値へ (row_to_json と同じ見た目)."""
    if v is None:
        return None
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _wall(dt: datetime) -> datetime:
    """staff_events の時刻を壁時計 (naive) に戻す.

    予定の時刻は「JST の壁時計を UTC として保存」(extract.py の
    ``starts_at at time zone 'UTC'``)。PG では aware(UTC) で返るので UTC に
    揃えて tz を外す。SQLite では naive のまま返る。
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _desired_per_week(weekly_pattern: Any) -> int:
    """frontend getDesiredWeeklyVisitCount と同じ (0 = 希望未設定 → プール対象外)."""
    if not isinstance(weekly_pattern, dict):
        return 0
    raw = weekly_pattern.get("frequency_per_week")
    if isinstance(raw, bool):
        return 0
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return 0
    if n != n or n <= 0:  # NaN / 0 以下
        return 0
    return int(min(7, max(1, round(n))))


def _service_minutes(weekly_pattern: Any) -> int | None:
    if not isinstance(weekly_pattern, dict):
        return None
    raw = weekly_pattern.get("service_minutes")
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _visit_dict(v: Visit, course: Course | None, office_label: str | None, pname: str | None):
    return {
        "id": _s(v.id),
        "patient_id": _s(v.patient_id),
        "patient_name": pname,
        "visit_date": _s(v.visit_date),
        "weekday": v.visit_date.weekday(),
        "start_time": _s(v.start_time),
        "end_time": _s(v.end_time),
        "type": v.type,
        "required_staff_count": v.required_staff_count,
        "visit_group_id": _s(v.visit_group_id),
        "source": v.source,
        "status": v.status,
        "is_unplanned": bool(v.is_unplanned),
        "manual_staff_override": bool(v.manual_staff_override),
        "primary_staff_id": _s(v.primary_staff_id),
        "secondary_staff_id": _s(v.secondary_staff_id),
        "mentor_staff_id": _s(v.mentor_staff_id),
        "course_id": _s(v.course_id),
        "course_code": course.code if course else None,
        "course_office": office_label,
        # 表示の正典はコースの担当 (週を生成した直後は主担当が空でコースにだけ担当がいる)
        "course_staff_id": _s(course.assigned_staff_id) if course else None,
        "effective_staff_id": _s(
            v.primary_staff_id or (course.assigned_staff_id if course else None)
        ),
        "kaipoke_id": v.kaipoke_id,
    }


async def _week_visits(db, monday: date, sunday: date) -> list[Visit]:
    """週の訪問 (deleted_at IS NULL・取消を含む)。患者の論理削除は見ない (extract と同じ)."""
    return list(
        (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.visit_date >= monday,
                    Visit.visit_date <= sunday,
                    Visit.deleted_at.is_(None),
                )
                .order_by(Visit.visit_date, Visit.start_time)
            )
        ).all()
    )


async def _courses_by_id(db, ids: set[UUID]) -> dict[UUID, Course]:
    if not ids:
        return {}
    rows = (await db.scalars(select(Course).where(Course.id.in_(ids)))).all()
    return {c.id: c for c in rows}


async def _week_events(db, monday: date, sunday: date) -> list[StaffEvent]:
    # extract.py: cancelled_at is null and (starts_at UTC 壁時計) < 日曜の翌日
    #             and (ends_at UTC 壁時計) > 月曜 0:00
    # 比較値は naive (staff_events.py の from/to と同じ作法)。
    return list(
        (
            await db.scalars(
                select(StaffEvent)
                .where(
                    StaffEvent.cancelled_at.is_(None),
                    StaffEvent.starts_at < datetime.combine(sunday + timedelta(days=1), time.min),
                    StaffEvent.ends_at > datetime.combine(monday, time.min),
                )
                .order_by(StaffEvent.starts_at)
            )
        ).all()
    )


def _event_dict(e: StaffEvent) -> dict:
    s, en = _wall(e.starts_at), _wall(e.ends_at)
    return {
        "id": _s(e.id),
        "staff_id": _s(e.staff_id),
        "date": s.strftime("%Y-%m-%d"),
        "start": s.strftime("%H:%M"),
        "end": en.strftime("%H:%M"),
        "end_date": en.strftime("%Y-%m-%d"),
        "event_type": e.event_type,
        "title": e.title,
        "source": e.source,
        "blocking": bool(e.blocking),
    }


def _override_dict(o: StaffWeeklyOverride) -> dict:
    return {
        "staff_id": _s(o.staff_id),
        "weekday": o.weekday,
        "date": _s(date.fromisocalendar(o.iso_year, o.iso_week, o.weekday + 1)),
        "override_type": o.override_type,
        "start_time": _s(o.start_time),
        "end_time": _s(o.end_time),
        "reason": o.reason,
    }


# ---------------------------------------------------------------------------
# GET /agent/week-context
# ---------------------------------------------------------------------------


@router.get(
    "/week-context",
    summary="エージェント用: 1 週のならしに要る材料をまとめて返す (read-only)",
)
async def week_context(
    db: DbDep,
    _user: AdminUser,
    iso_year: IsoYear,
    iso_week: IsoWeek,
) -> dict:
    monday = _monday(iso_year, iso_week)
    sunday = monday + timedelta(days=6)

    # --- 訪問 (週) ---------------------------------------------------------
    all_visits = await _week_visits(db, monday, sunday)
    live_visits = [v for v in all_visits if v.status != "cancelled"]
    cancelled_visits = [v for v in all_visits if v.status == "cancelled"]

    # --- コース (週) とテンプレート ---------------------------------------
    week_courses = list(
        (
            await db.scalars(
                select(Course)
                .where(
                    Course.iso_year == iso_year,
                    Course.iso_week == iso_week,
                    Course.deleted_at.is_(None),
                )
                .order_by(Course.weekday, Course.code)
            )
        ).all()
    )
    course_by_id = {c.id: c for c in week_courses}
    # 訪問が週外/削除済みコースを指している場合も落とさない (extract は left join)
    missing = {v.course_id for v in all_visits if v.course_id and v.course_id not in course_by_id}
    course_by_id.update(await _courses_by_id(db, missing))

    offices = list(
        (
            await db.scalars(
                select(Office).where(Office.deleted_at.is_(None)).order_by(Office.sort_order)
            )
        ).all()
    )
    # 訪問のコースの拠点は削除済み拠点でも引けるように全件から作る
    office_label = {o.id: o.short_label for o in (await db.scalars(select(Office))).all()}

    templates = list(
        (
            await db.scalars(
                select(CourseTemplate)
                .where(CourseTemplate.deleted_at.is_(None))
                .order_by(CourseTemplate.office_id, CourseTemplate.label)
            )
        ).all()
    )

    # --- 特別訪問週間 -------------------------------------------------------
    periods = list(
        (
            await db.scalars(
                select(SpecialVisitPeriod).where(
                    SpecialVisitPeriod.start_date <= sunday,
                    SpecialVisitPeriod.end_date >= monday,
                )
            )
        ).all()
    )
    period_by_id = {p.id: p for p in periods}
    week_marks = list(
        (
            await db.scalars(
                select(SpecialVisitMark).where(
                    SpecialVisitMark.iso_year == iso_year,
                    SpecialVisitMark.iso_week == iso_week,
                )
            )
        ).all()
    )
    # 週と重なる期間の外にあるマークの期間も引いておく (status 判定のため)
    extra_period_ids = {m.period_id for m in week_marks if m.period_id not in period_by_id}
    if extra_period_ids:
        for p in (
            await db.scalars(
                select(SpecialVisitPeriod).where(SpecialVisitPeriod.id.in_(extra_period_ids))
            )
        ).all():
            period_by_id[p.id] = p

    # --- 患者 ---------------------------------------------------------------
    # extract.py: 週の訪問 (取消含む・deleted_at null) の患者 ∪ 週のマークの患者
    week_patient_ids = {v.patient_id for v in all_visits} | {m.patient_id for m in week_marks}

    # 保留プール (frontend CourseDayTablePanel の poolPatients と同じ判定):
    #   status='active'・論理削除なし で、複数名体制 or 希望回数 > 週の訪問数 (取消も数える)
    count_by_patient: dict[UUID, int] = defaultdict(int)
    for v in all_visits:
        count_by_patient[v.patient_id] += 1
    active_patients = (
        await db.scalars(
            select(Patient).where(Patient.deleted_at.is_(None), Patient.status == "active")
        )
    ).all()
    pool: list[dict] = []
    pool_ids: set[UUID] = set()
    for p in active_patients:
        desired = _desired_per_week(p.weekly_pattern)
        actual = count_by_patient.get(p.id, 0)
        multi = bool(p.requires_multiple_staff)
        if not multi and (desired == 0 or actual >= desired):
            continue
        pool_ids.add(p.id)
        pool.append(
            {
                "patient_id": _s(p.id),
                "name": p.name,
                "desired_per_week": desired,
                "actual_in_week": actual,
                "shortage": max(0, desired - actual),
                "requires_multiple_staff": multi,
                "reason": "multi_staff" if multi and desired <= actual else "shortage",
            }
        )

    patient_ids = week_patient_ids | pool_ids
    patients = (
        list((await db.scalars(select(Patient).where(Patient.id.in_(patient_ids)))).all())
        if patient_ids
        else []
    )
    patient_by_id = {p.id: p for p in patients}

    allowed_offices: dict[UUID, list[str]] = defaultdict(list)
    fixed_visits: list[PatientFixedVisit] = []
    links: list[PatientSameAddressLink] = []
    if patient_ids:
        for row in (
            await db.scalars(
                select(PatientAllowedOffice).where(PatientAllowedOffice.patient_id.in_(patient_ids))
            )
        ).all():
            allowed_offices[row.patient_id].append(_s(row.office_id))
        fixed_visits = list(
            (
                await db.scalars(
                    select(PatientFixedVisit)
                    .where(PatientFixedVisit.patient_id.in_(patient_ids))
                    .order_by(
                        PatientFixedVisit.patient_id,
                        PatientFixedVisit.mode,
                        PatientFixedVisit.weekday,
                        PatientFixedVisit.slot_index,
                    )
                )
            ).all()
        )
        links = list(
            (
                await db.scalars(
                    select(PatientSameAddressLink).where(
                        or_(
                            PatientSameAddressLink.patient_a_id.in_(patient_ids),
                            PatientSameAddressLink.patient_b_id.in_(patient_ids),
                        )
                    )
                )
            ).all()
        )

    # 長さ: 固定枠 (normal・weekday, slot_index 順の先頭) → 希望の service_minutes → 30
    first_normal_duration: dict[UUID, int] = {}
    for f in sorted(
        (f for f in fixed_visits if f.mode == "normal"),
        key=lambda f: (f.weekday, f.slot_index),
    ):
        first_normal_duration.setdefault(f.patient_id, f.duration_min)

    # --- 未配置の○ (extract.py special_marks と同じ) -------------------------
    placed_ids = {m.placed_visit_id for m in week_marks if m.placed_visit_id is not None}
    alive_placed: set[UUID] = set()
    if placed_ids:
        alive_placed = set(
            (
                await db.scalars(
                    select(Visit.id).where(
                        Visit.id.in_(placed_ids),
                        Visit.deleted_at.is_(None),
                        Visit.status != "cancelled",
                    )
                )
            ).all()
        )
    special_marks: list[dict] = []
    for m in week_marks:
        per = period_by_id.get(m.period_id)
        p = patient_by_id.get(m.patient_id)
        if m.kind != "extra" or per is None or per.status != "active":
            continue
        if p is None or p.deleted_at is not None or p.status != "active":
            continue
        unplaced = m.status == "pool" or (
            m.status == "placed"
            and (m.placed_visit_id is None or m.placed_visit_id not in alive_placed)
        )
        if not unplaced:
            continue
        duration = first_normal_duration.get(m.patient_id)
        if duration is None:
            duration = _service_minutes(p.weekly_pattern) or 30
        special_marks.append(
            {
                "id": _s(m.id),
                "patient_id": _s(m.patient_id),
                "weekday": m.weekday,
                "duration_min": duration,
            }
        )

    # --- 職員・勤務・休み・予定・NG ------------------------------------------
    staff_rows = list(
        (
            await db.scalars(
                select(Staff)
                .where(Staff.deleted_at.is_(None))
                .order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc())
            )
        ).all()
    )
    secondary: dict[UUID, list[str]] = defaultdict(list)
    for row in (await db.scalars(select(StaffSecondaryOffice))).all():
        secondary[row.staff_id].append(_s(row.office_id))

    def _staff_dict(s: Staff) -> dict:
        return {
            "id": _s(s.id),
            "code": s.code,
            "name": s.name,
            "role": s.role,
            "sex": s.sex,
            "is_trainee": bool(s.is_trainee),
            "qualification": s.qualification,
            "status": s.status,
            "primary_office_id": _s(s.primary_office_id),
            "secondary_office_ids": secondary.get(s.id, []),
        }

    shifts = (
        await db.scalars(select(StaffShift).order_by(StaffShift.staff_id, StaffShift.weekday))
    ).all()
    overrides = (
        await db.scalars(
            select(StaffWeeklyOverride)
            .where(
                StaffWeeklyOverride.iso_year == iso_year,
                StaffWeeklyOverride.iso_week == iso_week,
            )
            .order_by(StaffWeeklyOverride.staff_id, StaffWeeklyOverride.weekday)
        )
    ).all()
    events = await _week_events(db, monday, sunday)
    ng_rows = (await db.scalars(select(PatientNgStaff))).all()

    settings_row = await db.scalar(
        select(SchedulingSettings).where(SchedulingSettings.is_singleton.is_(True)).limit(1)
    )

    # --- 2 名体制・同行 --------------------------------------------------------
    visit_ids = [v.id for v in all_visits]
    vsa_by_visit: dict[UUID, list[str]] = defaultdict(list)
    accompaniments: list[Accompaniment] = []
    if visit_ids:
        for a in (
            await db.scalars(
                select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
            )
        ).all():
            vsa_by_visit[a.visit_id].append(_s(a.staff_id))
    conds = []
    if visit_ids:
        conds.append(Accompaniment.visit_id.in_(visit_ids))
    week_course_ids = [c.id for c in week_courses]
    if week_course_ids:
        conds.append(Accompaniment.course_id.in_(week_course_ids))
    if conds:
        accompaniments = list((await db.scalars(select(Accompaniment).where(or_(*conds)))).all())

    # --- 直前 4 週の担当歴 (extract.py _history_sql と同じ) -------------------
    hist_from = monday - timedelta(days=HISTORY_DAYS)
    hist_to = monday - timedelta(days=1)
    hist_rows = (
        await db.execute(
            select(Visit.patient_id, Visit.visit_date, Visit.start_time, Visit.primary_staff_id)
            .where(
                Visit.visit_date >= hist_from,
                Visit.visit_date <= hist_to,
                Visit.deleted_at.is_(None),
                Visit.status != "cancelled",
                Visit.primary_staff_id.is_not(None),
            )
            .order_by(Visit.visit_date, Visit.start_time)
        )
    ).all()
    history = [
        {
            "patient_id": _s(r.patient_id),
            "visit_date": _s(r.visit_date),
            "start_time": _s(r.start_time),
            "primary_staff_id": _s(r.primary_staff_id),
        }
        for r in hist_rows
    ]
    agg: dict[tuple[UUID, UUID], dict] = {}
    for r in hist_rows:
        key = (r.patient_id, r.primary_staff_id)
        cur = agg.get(key)
        if cur is None:
            agg[key] = {
                "patient_id": _s(r.patient_id),
                "staff_id": _s(r.primary_staff_id),
                "count": 1,
                "last_date": _s(r.visit_date),
            }
        else:
            cur["count"] += 1
            cur["last_date"] = _s(r.visit_date)  # 日付昇順なので最後が最新
    history_counts = sorted(
        agg.values(), key=lambda x: (x["patient_id"], -x["count"], x["staff_id"])
    )

    # --- 組み立て --------------------------------------------------------------
    def _vd(v: Visit) -> dict:
        c = course_by_id.get(v.course_id) if v.course_id else None
        p = patient_by_id.get(v.patient_id)
        d = _visit_dict(v, c, office_label.get(c.office_id) if c else None, p.name if p else None)
        d["assigned_staff_ids"] = vsa_by_visit.get(v.id, [])
        return d

    return {
        "extracted_at": datetime.now(UTC).isoformat(),
        "iso_year": iso_year,
        "iso_week": iso_week,
        "week_start": _s(monday),
        "week_end": _s(sunday),
        "visits": [_vd(v) for v in live_visits],
        "cancelled_visits": [_vd(v) for v in cancelled_visits],
        "patients": [
            {
                "id": _s(p.id),
                "code": p.code,
                "name": p.name,
                "sex": p.sex,
                "sex_restriction": p.sex_restriction,
                "lat": _s(p.lat),
                "lng": _s(p.lng),
                "primary_office_id": _s(p.primary_office_id),
                "allowed_office_ids": allowed_offices.get(p.id, []),
                "weekly_pattern": p.weekly_pattern,
                "special_weekly_pattern": p.special_weekly_pattern,
                "requires_multiple_staff": bool(p.requires_multiple_staff),
                "status": p.status,
                "deleted": p.deleted_at is not None,
                "in_week": p.id in week_patient_ids,
                "in_pool": p.id in pool_ids,
            }
            for p in patients
        ],
        "fixed_visits": [
            {
                "id": _s(f.id),
                "patient_id": _s(f.patient_id),
                "mode": f.mode,
                "weekday": f.weekday,
                "slot_index": f.slot_index,
                "start_time": _s(f.start_time),
                "duration_min": f.duration_min,
                "course_template_id": _s(f.course_template_id),
                "sub_office_id": _s(f.sub_office_id),
                "is_pinned": bool(f.is_pinned),
                "movability": f.movability,
            }
            for f in fixed_visits
        ],
        "same_address_links": [
            {
                "patient_a_id": _s(lk.patient_a_id),
                "patient_b_id": _s(lk.patient_b_id),
                "pair_mode": lk.pair_mode,
            }
            for lk in links
        ],
        "special_periods": [
            {
                "id": _s(p.id),
                "patient_id": _s(p.patient_id),
                "start_date": _s(p.start_date),
                "end_date": _s(p.end_date),
                "weekly_target": p.weekly_target,
                "status": p.status,
            }
            for p in periods
        ],
        "special_marks": special_marks,
        "special_marks_all": [
            {
                "id": _s(m.id),
                "period_id": _s(m.period_id),
                "patient_id": _s(m.patient_id),
                "weekday": m.weekday,
                "kind": m.kind,
                "status": m.status,
                "placed_visit_id": _s(m.placed_visit_id),
            }
            for m in week_marks
        ],
        "pool": pool,
        "ng": [{"patient_id": _s(r.patient_id), "staff_id": _s(r.staff_id)} for r in ng_rows],
        "scheduling_settings": (
            {
                "visit_buffer_min": settings_row.visit_buffer_min,
                "travel_speed_kmh": _s(settings_row.travel_speed_kmh),
                "lunch_duration_min": settings_row.lunch_duration_min,
                "lunch_window_start": _s(settings_row.lunch_window_start),
                "lunch_window_end": _s(settings_row.lunch_window_end),
                "business_start": _s(settings_row.business_start),
                "business_end": _s(settings_row.business_end),
                "max_patients_per_course": settings_row.max_patients_per_course,
            }
            if settings_row is not None
            else None
        ),
        "offices": [
            {
                "id": _s(o.id),
                "name": o.name,
                "short_label": o.short_label,
                "lat": _s(o.lat),
                "lng": _s(o.lng),
                "operating_weekdays": o.operating_weekdays,
            }
            for o in offices
        ],
        "courses": [
            {
                "id": _s(c.id),
                "weekday": c.weekday,
                "date": _s(monday + timedelta(days=c.weekday)),
                "code": c.code,
                "office_id": _s(c.office_id),
                "office_short_label": office_label.get(c.office_id),
                "assigned_staff_id": _s(c.assigned_staff_id),
                "template_id": _s(c.template_id),
                "course_status": c.course_status,
            }
            for c in week_courses
        ],
        "course_templates": [
            {
                "id": _s(t.id),
                "office_id": _s(t.office_id),
                "label": t.label,
                "capacity": [
                    t.capacity_mon,
                    t.capacity_tue,
                    t.capacity_wed,
                    t.capacity_thu,
                    t.capacity_fri,
                    t.capacity_sat,
                    t.capacity_sun,
                ],
            }
            for t in templates
        ],
        "accompaniments": [
            {
                "id": _s(a.id),
                "accompanying_staff_id": _s(a.accompanying_staff_id),
                "target_type": a.target_type,
                "course_id": _s(a.course_id),
                "visit_id": _s(a.visit_id),
                "kind": a.kind,
                "source": a.source,
            }
            for a in accompaniments
        ],
        # extract.py と同じく staff は active のみ。休職・退職 (論理削除前) は staff_inactive。
        "staff": [_staff_dict(s) for s in staff_rows if s.status == "active"],
        "staff_inactive": [_staff_dict(s) for s in staff_rows if s.status != "active"],
        "shifts": [
            {
                "staff_id": _s(sh.staff_id),
                "weekday": sh.weekday,
                "is_on": bool(sh.is_on),
                "start_time": _s(sh.start_time),
                "end_time": _s(sh.end_time),
            }
            for sh in shifts
        ],
        "weekly_overrides": [_override_dict(o) for o in overrides],
        "events": [_event_dict(e) for e in events],
        "history_from": _s(hist_from),
        "history_to": _s(hist_to),
        "history": history,
        "history_counts": history_counts,
    }


# ---------------------------------------------------------------------------
# GET /agent/staff-week
# ---------------------------------------------------------------------------


@router.get(
    "/staff-week",
    summary="エージェント用: 職員ごとの 1 週 (勤務・休み・予定・訪問件数) (read-only)",
)
async def staff_week(
    db: DbDep,
    _user: AdminUser,
    iso_year: IsoYear,
    iso_week: IsoWeek,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict:
    monday = _monday(iso_year, iso_week)
    sunday = monday + timedelta(days=6)
    days = [monday + timedelta(days=i) for i in range(7)]

    stmt = select(Staff).where(Staff.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(Staff.status == "active")
    staff_rows = list(
        (await db.scalars(stmt.order_by(Staff.code.asc().nulls_last(), Staff.created_at))).all()
    )
    staff_ids = [s.id for s in staff_rows]

    shift_map: dict[tuple[UUID, int], StaffShift] = {}
    ov_map: dict[tuple[UUID, int], StaffWeeklyOverride] = {}
    if staff_ids:
        for sh in (
            await db.scalars(select(StaffShift).where(StaffShift.staff_id.in_(staff_ids)))
        ).all():
            shift_map[(sh.staff_id, sh.weekday)] = sh
        for o in (
            await db.scalars(
                select(StaffWeeklyOverride).where(
                    StaffWeeklyOverride.iso_year == iso_year,
                    StaffWeeklyOverride.iso_week == iso_week,
                    StaffWeeklyOverride.staff_id.in_(staff_ids),
                )
            )
        ).all():
            ov_map[(o.staff_id, o.weekday)] = o

    # 予定 (取消を除く・日をまたぐものは重なる日すべてに載せる)
    events_by: dict[tuple[UUID, date], list[dict]] = defaultdict(list)
    for e in await _week_events(db, monday, sunday):
        s, en = _wall(e.starts_at), _wall(e.ends_at)
        for d in days:
            day_start = datetime.combine(d, time.min)
            if s < day_start + timedelta(days=1) and en > day_start:
                events_by[(e.staff_id, d)].append(_event_dict(e))

    # 訪問 (取消を除く)。担当 = 主担当、無ければコースの担当 (extract.py / 表示の正典)
    visits = [v for v in await _week_visits(db, monday, sunday) if v.status != "cancelled"]
    courses = await _courses_by_id(db, {v.course_id for v in visits if v.course_id})
    vsa: dict[UUID, set[UUID]] = defaultdict(set)
    if visits:
        for a in (
            await db.scalars(
                select(VisitStaffAssignment).where(
                    VisitStaffAssignment.visit_id.in_([v.id for v in visits])
                )
            )
        ).all():
            vsa[a.visit_id].add(a.staff_id)

    def _minutes(v: Visit) -> int:
        return (v.end_time.hour * 60 + v.end_time.minute) - (
            v.start_time.hour * 60 + v.start_time.minute
        )

    primary_cnt: dict[tuple[UUID, date], int] = defaultdict(int)
    primary_min: dict[tuple[UUID, date], int] = defaultdict(int)
    first_start: dict[tuple[UUID, date], time] = {}
    last_end: dict[tuple[UUID, date], time] = {}
    second_cnt: dict[tuple[UUID, date], int] = defaultdict(int)
    for v in visits:
        c = courses.get(v.course_id) if v.course_id else None
        eff = v.primary_staff_id or (c.assigned_staff_id if c else None)
        if eff is not None:
            k = (eff, v.visit_date)
            primary_cnt[k] += 1
            primary_min[k] += _minutes(v)
            if k not in first_start or v.start_time < first_start[k]:
                first_start[k] = v.start_time
            if k not in last_end or v.end_time > last_end[k]:
                last_end[k] = v.end_time
        others = set(vsa.get(v.id, set()))
        if v.secondary_staff_id:
            others.add(v.secondary_staff_id)
        others.discard(eff)
        for sid in others:
            second_cnt[(sid, v.visit_date)] += 1

    # 同行 (週のコース・訪問へのリンク)
    acc_cnt: dict[tuple[UUID, date], int] = defaultdict(int)
    week_courses = (
        await db.scalars(
            select(Course).where(
                Course.iso_year == iso_year,
                Course.iso_week == iso_week,
                Course.deleted_at.is_(None),
            )
        )
    ).all()
    course_date = {c.id: monday + timedelta(days=c.weekday) for c in week_courses}
    visit_date_by_id = {v.id: v.visit_date for v in visits}
    conds = []
    if visit_date_by_id:
        conds.append(Accompaniment.visit_id.in_(list(visit_date_by_id)))
    if course_date:
        conds.append(Accompaniment.course_id.in_(list(course_date)))
    if conds:
        for a in (await db.scalars(select(Accompaniment).where(or_(*conds)))).all():
            d = (
                course_date.get(a.course_id)
                if a.target_type == "course"
                else visit_date_by_id.get(a.visit_id)
            )
            if d is not None:
                acc_cnt[(a.accompanying_staff_id, d)] += 1

    out_staff = []
    for s in staff_rows:
        day_rows = []
        week_total = 0
        for i, d in enumerate(days):
            sh = shift_map.get((s.id, i))
            ov = ov_map.get((s.id, i))
            base_on = bool(sh.is_on) if sh else False
            start = sh.start_time if sh and sh.is_on else None
            end = sh.end_time if sh and sh.is_on else None
            is_on = base_on
            half_off = None
            if ov is not None:
                if ov.override_type == "off":
                    is_on, start, end = False, None, None
                elif ov.override_type == "custom_time":
                    # layer3 と同じく時間変更は「その日は出勤」とみなす
                    is_on = True
                    start = ov.start_time or (sh.start_time if sh else None)
                    end = ov.end_time or (sh.end_time if sh else None)
                elif ov.override_type in ("am_off", "pm_off"):
                    half_off = "am" if ov.override_type == "am_off" else "pm"
            k = (s.id, d)
            week_total += primary_cnt.get(k, 0)
            day_rows.append(
                {
                    "date": _s(d),
                    "weekday": i,
                    "shift": (
                        {
                            "is_on": bool(sh.is_on),
                            "start_time": _s(sh.start_time),
                            "end_time": _s(sh.end_time),
                        }
                        if sh
                        else None
                    ),
                    "override": (
                        {
                            "override_type": ov.override_type,
                            "start_time": _s(ov.start_time),
                            "end_time": _s(ov.end_time),
                            "reason": ov.reason,
                        }
                        if ov
                        else None
                    ),
                    "is_on": is_on,
                    "start_time": _s(start),
                    "end_time": _s(end),
                    "half_off": half_off,
                    "events": events_by.get(k, []),
                    "visits_count": primary_cnt.get(k, 0),
                    "visit_minutes": primary_min.get(k, 0),
                    "first_visit_start": _s(first_start.get(k)),
                    "last_visit_end": _s(last_end.get(k)),
                    "second_staff_count": second_cnt.get(k, 0),
                    "accompaniment_count": acc_cnt.get(k, 0),
                }
            )
        out_staff.append(
            {
                "id": _s(s.id),
                "code": s.code,
                "name": s.name,
                "role": s.role,
                "sex": s.sex,
                "is_trainee": bool(s.is_trainee),
                "status": s.status,
                "primary_office_id": _s(s.primary_office_id),
                "week_visits_count": week_total,
                "days": day_rows,
            }
        )

    return {
        "iso_year": iso_year,
        "iso_week": iso_week,
        "week_start": _s(monday),
        "week_end": _s(sunday),
        "staff": out_staff,
    }
