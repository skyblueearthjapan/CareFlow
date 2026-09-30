"""前の週をコピーして週を作る (docs/plans/copy-week-design-2026-09-30.md).

写すのは **患者の予定** (患者・曜日・予定の開始/終了時刻・2 名体制の組・サービス内容の
上書き) だけ。担当 (primary / secondary / mentor / VSA / コース担当 /
``manual_staff_override``) は写さない (PO 決定 #1・ローテーションが基本)。
時刻は ``visits.start_time`` / ``end_time`` (予定として確定した時刻) で、QR の実績では
ない (#2)。固定訪問 (``patient_fixed_visits``) には書き込まない (#3)。読むのは
「固定訪問に無い」目印と、任意の「固定訪問から補う」だけ。

確認画面 (preview) と実行は **同じ ``build_copy_plan``** を通る。表示と実行の件数が
ズレないようにするため (patient_status_sync と同じ方針)。

session は commit しない (呼び出し側 = API がトランザクション境界を持つ)。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import (
    COURSE_STATUS_COURSE_FIXED,
    COURSE_STATUS_STAFF_ASSIGNED,
    Course,
)
from app.models.course_template import CourseTemplate
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.special_visit import MARK_KIND_EXTRA, MARK_STATUS_CANCELLED, SpecialVisitMark
from app.models.visit import (
    VISIT_SOURCE_MANUAL_WEEK,
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.patient_status_sync import is_schedulable_status, today_jst
from app.services.scheduling.jp_holidays import holidays_between
from app.services.scheduling.layer1_expander import (
    Layer1Expander,
    Layer1Result,
    _displaced_weekdays,
    _expand_patient_fixed_visits,
    _get_or_create_course_for_template_week_l1,
    _is_special_week_active,
)

#: 写す元として選べる範囲 (写す先の週から何週前まで)。§4-2 の「過去 8 週」。
MAX_WEEKS_BACK = 8

#: 臨時コース (カイポケ取込由来・その日だけの回り)。配下の訪問はコースなしで入れる。
_TEMP_COURSE_PREFIX = "臨"

#: 取込 (カイポケが正) の出所。写す先で残す。
_SOURCE_IMPORT = "import"


class CopyWeekError(Exception):
    """業務上の入力エラー (API で 422 にする)."""


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def validate_weeks(source_monday: date, target_monday: date, *, today: date) -> None:
    """写す元・写す先の週の妥当性 (§4-2 / §4-6: 写す先は今週以降のみ)."""
    if source_monday.weekday() != 0 or target_monday.weekday() != 0:
        raise CopyWeekError("週の始まり (月曜日) を指定してください")
    if target_monday < monday_of(today):
        raise CopyWeekError("写す先は今週以降の週だけを選べます")
    diff = (target_monday - source_monday).days // 7
    if diff < 1 or diff > MAX_WEEKS_BACK:
        raise CopyWeekError(f"写す元は写す先より前の {MAX_WEEKS_BACK} 週の中から選んでください")


# ---------------------------------------------------------------------------
# 計画 (preview と実行で共通)
# ---------------------------------------------------------------------------


@dataclass
class CopyItem:
    """写す訪問 1 行 (写す元の visit 1 行に対応)."""

    source_visit_id: UUID
    patient_id: UUID
    patient_name: str
    target_date: date
    start_time: time
    end_time: time
    type: str
    required_staff_count: int
    old_group_id: UUID | None
    kaipoke_service_override: str | None
    # 写す先で入れるコースのテンプレート (None = コースなし)
    template_id: UUID | None
    weekday: int
    temp_course: bool
    not_in_fixed: bool


@dataclass
class MissingFixed:
    patient: Patient
    weekday: int
    target_date: date
    start_time: time
    end_time: time
    visits: int
    entries: list[dict]
    special: bool


@dataclass
class CopyPlan:
    source_monday: date
    target_monday: date
    mode: str  # 'replace' | 'add_only'
    items: list[CopyItem] = field(default_factory=list)
    # 利用者の除外前の「固定訪問に無い」訪問 (一覧表示用)
    not_in_fixed: list[CopyItem] = field(default_factory=list)
    excluded_ids: set[UUID] = field(default_factory=set)
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    replace_visits: list[Visit] = field(default_factory=list)
    existing: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    missing_fixed: list[MissingFixed] = field(default_factory=list)
    missing_patients_without_visits: int = 0
    fill_from_fixed: bool = False

    @property
    def fill_count(self) -> int:
        return sum(m.visits for m in self.missing_fixed) if self.fill_from_fixed else 0


def _week_range(monday: date) -> tuple[date, date]:
    return monday, monday + timedelta(days=6)


async def _live_week_visits(db: AsyncSession, monday: date) -> list[Visit]:
    start, end = _week_range(monday)
    return list(
        (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.visit_date >= start,
                    Visit.visit_date <= end,
                    Visit.deleted_at.is_(None),
                )
                .order_by(Visit.visit_date, Visit.start_time, Visit.id)
            )
        ).all()
    )


async def _checked_in_ids(db: AsyncSession, visit_ids: list[UUID]) -> set[UUID]:
    if not visit_ids:
        return set()
    rows = await db.scalars(
        select(VisitCheckin.visit_id).where(VisitCheckin.visit_id.in_(visit_ids)).distinct()
    )
    return set(rows.all())


async def _fixed_weekdays_by_patient(
    db: AsyncSession, patients: dict[UUID, Patient], monday: date
) -> dict[UUID, set[int]]:
    """患者ごとの固定訪問の曜日 (その週に効く mode = 特別週なら special)."""
    if not patients:
        return {}
    iso = monday.isocalendar()
    rows = (
        await db.execute(
            select(
                PatientFixedVisit.patient_id, PatientFixedVisit.mode, PatientFixedVisit.weekday
            ).where(PatientFixedVisit.patient_id.in_(list(patients.keys())))
        )
    ).all()
    out: dict[UUID, set[int]] = defaultdict(set)
    for pid, mode, weekday in rows:
        want = "special" if _is_special_week_active(patients[pid], iso.year, iso.week) else "normal"
        if mode == want:
            out[pid].add(weekday)
    return out


def _classify_existing(v: Visit, *, checked_in: set[UUID], today: date) -> str | None:
    """写す先の既存訪問のうち **残す** ものの区分 (None = 置き換える) — §4-5."""
    if (
        v.id in checked_in
        or v.is_unplanned
        or v.status
        not in (
            VISIT_STATUS_PLANNED,
            VISIT_STATUS_CANCELLED,
        )
    ):
        return "keep_checked_in"
    if v.status == VISIT_STATUS_CANCELLED:
        return "keep_cancelled"
    if v.source == _SOURCE_IMPORT:
        return "keep_import"
    if v.week_pinned:
        return "keep_pinned"
    if v.visit_date < today:
        return "keep_past"
    return None


async def build_copy_plan(
    db: AsyncSession,
    *,
    source_monday: date,
    target_monday: date,
    exclude_visit_ids: set[UUID],
    fill_from_fixed: bool,
    today: date | None = None,
) -> CopyPlan:
    """写す内容を決める (DB は変えない)."""
    today = today or today_jst()
    validate_weeks(source_monday, target_monday, today=today)
    offset = target_monday - source_monday

    # ----- 写す先の既存訪問 (§4-5) -----
    target_visits = await _live_week_visits(db, target_monday)
    target_checked = await _checked_in_ids(db, [v.id for v in target_visits])
    mode = "add_only" if target_checked else "replace"
    plan = CopyPlan(
        source_monday=source_monday,
        target_monday=target_monday,
        mode=mode,
        fill_from_fixed=fill_from_fixed,
    )
    kept: list[Visit] = []
    plan.existing["total"] = len(target_visits)
    for v in target_visits:
        if mode == "add_only":
            kept.append(v)
            continue
        kind = _classify_existing(v, checked_in=target_checked, today=today)
        if kind is None:
            plan.replace_visits.append(v)
        else:
            plan.existing[kind] += 1
            kept.append(v)
    plan.existing["replace"] = len(plan.replace_visits)
    if mode == "add_only":
        # 足すだけの週は全件残す。内訳は区分どおりに数えて見せる。
        for v in target_visits:
            plan.existing[
                _classify_existing(v, checked_in=target_checked, today=today) or "keep_other"
            ] += 1
    kept_keys = {(v.patient_id, v.visit_date, v.start_time) for v in kept}
    occupied_days = {(v.patient_id, v.visit_date) for v in kept}

    # ----- 写す元の訪問 -----
    source_visits = await _live_week_visits(db, source_monday)
    patient_ids = {v.patient_id for v in source_visits}
    patients = (
        {
            p.id: p
            for p in (
                await db.scalars(select(Patient).where(Patient.id.in_(list(patient_ids))))
            ).all()
        }
        if patient_ids
        else {}
    )
    extra_ids: set[UUID] = set()
    if source_visits:
        extra_ids = set(
            (
                await db.scalars(
                    select(SpecialVisitMark.placed_visit_id).where(
                        SpecialVisitMark.kind == MARK_KIND_EXTRA,
                        SpecialVisitMark.status != MARK_STATUS_CANCELLED,
                        SpecialVisitMark.placed_visit_id.in_([v.id for v in source_visits]),
                    )
                )
            ).all()
        )
    course_ids = {v.course_id for v in source_visits if v.course_id is not None}
    courses = (
        {
            c.id: c
            for c in (await db.scalars(select(Course).where(Course.id.in_(list(course_ids))))).all()
        }
        if course_ids
        else {}
    )
    template_ids = {c.template_id for c in courses.values() if c.template_id is not None}
    live_templates = (
        set(
            (
                await db.scalars(
                    select(CourseTemplate.id).where(
                        CourseTemplate.id.in_(list(template_ids)),
                        CourseTemplate.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        if template_ids
        else set()
    )
    fixed_weekdays = await _fixed_weekdays_by_patient(db, patients, source_monday)

    # 2 名体制は組で扱う (片方だけ写すと組が壊れる)。利用者の除外も組へ広げる。
    group_members: dict[UUID, list[Visit]] = defaultdict(list)
    for v in source_visits:
        if v.visit_group_id is not None:
            group_members[v.visit_group_id].append(v)
    excluded = set(exclude_visit_ids)
    for members in group_members.values():
        if any(m.id in excluded for m in members):
            excluded.update(m.id for m in members)
    plan.excluded_ids = excluded

    candidates: list[CopyItem] = []
    for v in source_visits:
        patient = patients.get(v.patient_id)
        if v.status == VISIT_STATUS_CANCELLED:
            plan.skipped["cancelled"] += 1
            continue
        if v.is_unplanned:
            plan.skipped["unplanned"] += 1
            continue
        if v.id in extra_ids:
            plan.skipped["special_extra"] += 1
            continue
        if (
            patient is None
            or patient.deleted_at is not None
            or not is_schedulable_status(patient.status)
        ):
            plan.skipped["inactive_patient"] += 1
            continue
        course = courses.get(v.course_id) if v.course_id is not None else None
        temp = course is not None and (course.code or "").startswith(_TEMP_COURSE_PREFIX)
        template_id = (
            course.template_id
            if course is not None and not temp and course.template_id in live_templates
            else None
        )
        weekday = v.visit_date.weekday()
        candidates.append(
            CopyItem(
                source_visit_id=v.id,
                patient_id=v.patient_id,
                patient_name=patient.name,
                target_date=v.visit_date + offset,
                start_time=v.start_time,
                end_time=v.end_time,
                type=v.type,
                required_staff_count=v.required_staff_count,
                old_group_id=v.visit_group_id,
                kaipoke_service_override=v.kaipoke_service_override,
                template_id=template_id,
                weekday=weekday,
                temp_course=temp,
                not_in_fixed=weekday not in fixed_weekdays.get(v.patient_id, set()),
            )
        )

    # 組の片方が写せない (取消など) ときも、残りは組のまま写す (新しい組 ID を振る)。
    blocked_groups: set[UUID] = set()
    reason_of: dict[UUID, str] = {}
    for it in candidates:
        reason = None
        if it.source_visit_id in excluded:
            reason = "user_excluded"
        elif it.target_date < today:
            reason = "past_day"
        elif mode == "add_only" and (it.patient_id, it.target_date) in occupied_days:
            reason = "occupied_day"
        elif (it.patient_id, it.target_date, it.start_time) in kept_keys:
            reason = "kept_conflict"
        if reason is not None:
            reason_of[it.source_visit_id] = reason
            if it.old_group_id is not None:
                blocked_groups.add(it.old_group_id)
    for it in candidates:
        reason = reason_of.get(it.source_visit_id)
        if reason is None and it.old_group_id in blocked_groups:
            # 組の相方が写せない → こちらも写さない (組を壊さない)
            reason = next(
                reason_of[m.id] for m in group_members[it.old_group_id] if m.id in reason_of
            )
        # 一覧に出すのは「写す」か「利用者が外した」ものだけ (外せない理由のものは出さない)
        if it.not_in_fixed and reason in (None, "user_excluded"):
            plan.not_in_fixed.append(it)
        if reason is not None:
            plan.skipped[reason] += 1
            continue
        plan.items.append(it)

    # ----- 写す元の週に無かった固定訪問 (補う候補) -----
    await _plan_missing_fixed(db, plan, today=today)
    return plan


async def _plan_missing_fixed(db: AsyncSession, plan: CopyPlan, *, today: date) -> None:
    """固定訪問のうち、写した後の週でその (患者・日) に訪問が 1 件も無いもの."""
    iso = plan.target_monday.isocalendar()
    after_copy_days = {(it.patient_id, it.target_date) for it in plan.items}
    replaced = {v.id for v in plan.replace_visits}
    for v in await _live_week_visits(db, plan.target_monday):
        if v.id not in replaced:
            after_copy_days.add((v.patient_id, v.visit_date))

    active = list(
        (
            await db.scalars(
                select(Patient)
                .where(Patient.status == "active", Patient.deleted_at.is_(None))
                .order_by(Patient.code)
            )
        ).all()
    )
    copied_patients = {it.patient_id for it in plan.items}
    without_visits: set[UUID] = set()
    for patient in active:
        special = _is_special_week_active(patient, iso.year, iso.week)
        entries = await _expand_patient_fixed_visits(
            db,
            patient=patient,
            mode="special" if special else "normal",
            week_monday=plan.target_monday,
        )
        if not entries:
            continue
        displaced = await _displaced_weekdays(
            db, patient_id=patient.id, iso_year=iso.year, iso_week=iso.week
        )
        multi = bool(patient.requires_multiple_staff)
        by_weekday: dict[int, dict[int, dict]] = defaultdict(dict)
        for fe in entries:
            by_weekday[fe["weekday"]].setdefault(fe.get("slot_index", 0) or 0, fe)
        for weekday in sorted(by_weekday):
            slots = by_weekday[weekday]
            if weekday in displaced or 0 not in slots or (multi and 1 not in slots):
                continue  # 週生成と同じく作らない枠 (退避・2 名体制の片側欠け)
            target_date = plan.target_monday + timedelta(days=weekday)
            if target_date < today or (patient.id, target_date) in after_copy_days:
                continue
            fe = slots[0]
            start = fe["_start_time"]
            end_min = start.hour * 60 + start.minute + int(fe["service_minutes"])
            if end_min >= 24 * 60:
                continue
            chosen = [slots[0], slots[1]] if multi else [slots[0]]
            plan.missing_fixed.append(
                MissingFixed(
                    patient=patient,
                    weekday=weekday,
                    target_date=target_date,
                    start_time=start,
                    end_time=time(end_min // 60, end_min % 60),
                    visits=len(chosen),
                    entries=chosen,
                    special=special,
                )
            )
            if patient.id not in copied_patients:
                without_visits.add(patient.id)
    plan.missing_patients_without_visits = len(without_visits)


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


@dataclass
class CopyResult:
    created: int = 0
    filled: int = 0
    replaced: int = 0
    courses_created: int = 0
    courses_cleared: int = 0
    staff_mirrored: int = 0


async def apply_copy_plan(db: AsyncSession, plan: CopyPlan) -> CopyResult:
    """計画を書き込む (commit しない)。スナップショットは呼び出し側で先に取る."""
    result = CopyResult()
    iso = plan.target_monday.isocalendar()
    now = datetime.now(UTC)

    # 1) 置き換える訪問を論理削除 (週生成と同じ soft delete)。
    touched_course_ids: set[UUID] = set()
    for v in plan.replace_visits:
        v.deleted_at = now
        if v.course_id is not None:
            touched_course_ids.add(v.course_id)
    result.replaced = len(plan.replace_visits)
    await db.flush()

    # 2) 置き換えで訪問 (取消以外) が 1 件も無くなったコースは担当を外す。
    #    前の担当を残すと、盤面 (コース担当が正典) には担当が見えるのに写した訪問の
    #    primary / VSA は空、という 3 か所の不一致になる (カイポケ担当消失事故の型)。
    if touched_course_ids:
        still_live = set(
            (
                await db.scalars(
                    select(Visit.course_id)
                    .where(
                        Visit.course_id.in_(list(touched_course_ids)),
                        Visit.deleted_at.is_(None),
                        Visit.status != VISIT_STATUS_CANCELLED,
                    )
                    .distinct()
                )
            ).all()
        )
        for c in (
            await db.scalars(select(Course).where(Course.id.in_(list(touched_course_ids))))
        ).all():
            if c.id in still_live or c.assigned_staff_id is None:
                continue
            c.assigned_staff_id = None
            c.staff_assigned_at = None
            if c.course_status == COURSE_STATUS_STAFF_ASSIGNED:
                c.course_status = COURSE_STATUS_COURSE_FIXED
            result.courses_cleared += 1
        await db.flush()

    # 3) 写す。コースは写す先の週の同じテンプレートのコース (無ければ作る)。
    templates: dict[UUID, CourseTemplate] = {}
    tpl_ids = {it.template_id for it in plan.items if it.template_id is not None}
    if tpl_ids:
        templates = {
            t.id: t
            for t in (
                await db.scalars(select(CourseTemplate).where(CourseTemplate.id.in_(list(tpl_ids))))
            ).all()
        }
    before_courses = await _count_week_courses(db, iso.year, iso.week)
    new_group: dict[UUID, UUID] = {}
    course_cache: dict[tuple[UUID, int], Course] = {}
    created: list[tuple[Visit, Course | None]] = []
    for it in plan.items:
        course: Course | None = None
        tpl = templates.get(it.template_id) if it.template_id is not None else None
        if tpl is not None:
            key = (tpl.id, it.weekday)
            course = course_cache.get(key)
            if course is None:
                course = await _get_or_create_course_for_template_week_l1(
                    db, template=tpl, iso_year=iso.year, iso_week=iso.week, weekday=it.weekday
                )
                course_cache[key] = course
        group_id = None
        if it.old_group_id is not None:
            group_id = new_group.setdefault(it.old_group_id, uuid.uuid4())
        v = Visit(
            patient_id=it.patient_id,
            visit_date=it.target_date,
            start_time=it.start_time,
            end_time=it.end_time,
            type=it.type,
            status=VISIT_STATUS_PLANNED,
            source=VISIT_SOURCE_MANUAL_WEEK,
            required_staff_count=it.required_staff_count,
            visit_group_id=group_id,
            kaipoke_service_override=it.kaipoke_service_override,
            course_id=course.id if course is not None else None,
        )
        db.add(v)
        created.append((v, course))
    await db.flush()
    result.created = len(created)
    result.courses_created = await _count_week_courses(db, iso.year, iso.week) - before_courses

    # 4) 残す訪問があって担当の決まっているコースへ入れた訪問は、そのコースの担当を
    #    3 か所 (コース・primary・VSA) で揃える。写す元の担当ではない (写さない)。
    result.staff_mirrored = await _mirror_course_staff(db, created)

    # 5) 写す元の週に無かった固定訪問を補う (任意)。週生成と同じ組み立てで作る。
    if plan.fill_from_fixed and plan.missing_fixed:
        expander = Layer1Expander()
        l1 = Layer1Result(iso_year=iso.year, iso_week=iso.week)
        template_cache: dict[UUID, CourseTemplate | None] = {}
        by_patient: dict[UUID, list[MissingFixed]] = defaultdict(list)
        for m in plan.missing_fixed:
            by_patient[m.patient.id].append(m)
        for items in by_patient.values():
            patient = items[0].patient
            template = await expander._resolve_template_for_patient(
                db, patient=patient, template_cache=template_cache
            )
            made = await expander._expand_fixed_visits_to_visits(
                db,
                patient=patient,
                fixed_entries=[fe for m in items for fe in m.entries],
                week_monday=plan.target_monday,
                special_applied=items[0].special,
                template=template,
                iso_year=iso.year,
                iso_week=iso.week,
                result=l1,
            )
            result.filled += len(made)
        await db.flush()
    return result


async def _count_week_courses(db: AsyncSession, iso_year: int, iso_week: int) -> int:
    return int(
        await db.scalar(
            select(func.count(Course.id)).where(
                Course.iso_year == iso_year,
                Course.iso_week == iso_week,
                Course.deleted_at.is_(None),
            )
        )
        or 0
    )


async def _mirror_course_staff(db: AsyncSession, created: list[tuple[Visit, Course | None]]) -> int:
    """担当の決まっているコースへ入れた訪問に、そのコースの担当を揃えて書く."""
    staff_of_course = {
        c.id: c.assigned_staff_id for _, c in created if c is not None and c.assigned_staff_id
    }
    if not staff_of_course:
        return 0
    by_group: dict[UUID, list[Visit]] = defaultdict(list)
    for v, _ in created:
        if v.visit_group_id is not None:
            by_group[v.visit_group_id].append(v)
    n = 0
    for v, c in created:
        if c is None or c.id not in staff_of_course:
            continue
        primary = staff_of_course[c.id]
        secondary = None
        for partner in by_group.get(v.visit_group_id, []) if v.visit_group_id else []:
            if partner.id != v.id and partner.course_id in staff_of_course:
                cand = staff_of_course[partner.course_id]
                if cand != primary:
                    secondary = cand
                    break
        v.primary_staff_id = primary
        v.secondary_staff_id = secondary
        db.add(VisitStaffAssignment(visit_id=v.id, staff_id=primary))
        if secondary is not None:
            db.add(VisitStaffAssignment(visit_id=v.id, staff_id=secondary))
        n += 1
    await db.flush()
    return n


# ---------------------------------------------------------------------------
# 写す元の候補週 (一覧)
# ---------------------------------------------------------------------------


@dataclass
class SourceWeekStat:
    week_start: date
    visits: int
    patients: int
    cancelled: int
    unplanned: int
    qr_arrivals: int
    holidays: list[tuple[date, str]]


async def list_source_weeks(db: AsyncSession, target_monday: date) -> list[SourceWeekStat]:
    """写す先の週の前 8 週のうち、訪問のある週 (新しい順)."""
    first = target_monday - timedelta(weeks=MAX_WEEKS_BACK)
    last = target_monday - timedelta(days=1)
    rows = (
        await db.execute(
            select(
                Visit.id,
                Visit.patient_id,
                Visit.visit_date,
                Visit.status,
                Visit.is_unplanned,
            ).where(
                Visit.visit_date >= first,
                Visit.visit_date <= last,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    arrived = (
        set(
            (
                await db.scalars(
                    select(VisitCheckin.visit_id)
                    .where(
                        VisitCheckin.kind == "arrival",
                        VisitCheckin.visit_id.in_([r.id for r in rows]),
                    )
                    .distinct()
                )
            ).all()
        )
        if rows
        else set()
    )
    by_week: dict[date, list] = defaultdict(list)
    for r in rows:
        by_week[monday_of(r.visit_date)].append(r)
    out: list[SourceWeekStat] = []
    for monday in sorted(by_week, reverse=True):
        rs = by_week[monday]
        live = [r for r in rs if r.status != VISIT_STATUS_CANCELLED and not r.is_unplanned]
        out.append(
            SourceWeekStat(
                week_start=monday,
                visits=len(live),
                patients=len({r.patient_id for r in live}),
                cancelled=sum(1 for r in rs if r.status == VISIT_STATUS_CANCELLED),
                unplanned=sum(
                    1 for r in rs if r.is_unplanned and r.status != VISIT_STATUS_CANCELLED
                ),
                qr_arrivals=sum(1 for r in rs if r.id in arrived),
                holidays=holidays_between(monday, monday + timedelta(days=6)),
            )
        )
    return out
