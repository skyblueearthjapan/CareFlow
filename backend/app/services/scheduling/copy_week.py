"""前の週をコピーして週を作る (docs/plans/copy-week-design-2026-09-30.md).

写すのは **患者の予定** (患者・曜日・予定の開始/終了時刻・2 名体制の組・サービス内容の
上書き) だけ。担当 (primary / secondary / mentor / VSA / コース担当 /
``manual_staff_override``) は写さない (PO 決定 #1・ローテーションが基本)。
時刻は ``visits.start_time`` / ``end_time`` (予定として確定した時刻) で、QR の実績では
ない (#2)。固定訪問 (``patient_fixed_visits``) には書き込まない (#3)。読むのは
「固定訪問に無い」目印と、任意の「固定訪問から補う」だけ。

確認画面 (preview) と実行は **同じ ``build_copy_plan``** を通る。どのコースへ入れるか
(担当の決まったコースへ入れてよいかの検査を含む) も計画の段階で決めるので、表示と
実行の件数がズレない (patient_status_sync と同じ方針)。

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
from app.models.special_visit import (
    MARK_KIND_DISPLACED,
    MARK_KIND_EXTRA,
    MARK_STATUS_CANCELLED,
    SpecialVisitMark,
)
from app.models.staff import Staff
from app.models.visit import (
    VISIT_SOURCE_MANUAL_WEEK,
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.constraint_override_notify import collect_constraint_warnings_for_staff
from app.services.kaipoke.inbound import TEMP_COURSE_CODES
from app.services.patient_status_sync import is_schedulable_status, today_jst
from app.services.scheduling.jp_holidays import holidays_between
from app.services.scheduling.layer1_expander import (
    LAYER1_VISIT_SOURCE,
    LAYER1_VISIT_TYPE,
    Layer1Expander,
    _get_or_create_course_for_template_week_l1,
    _is_special_week_active,
)

#: 写す元として選べる範囲 (写す先の週から何週前まで)。§4-2 の「過去 8 週」。
MAX_WEEKS_BACK = 8

#: 取込 (カイポケが正) の出所。写す先で残す。
_SOURCE_IMPORT = "import"

#: 写す先で「残す」訪問のうち、その (患者・日) への写しを丸ごと止める区分 (§8-3 M-4)。
_KEEP_BLOCKS_DAY = frozenset({"keep_checked_in", "keep_import", "keep_pinned"})


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
    """写す先に作る訪問 1 行 (写す元の visit 1 行、または補う固定訪問の 1 枠)."""

    key: UUID  # 写す元の visit id (補う固定訪問は採番した id)
    origin: str  # 'copy' | 'fill'
    patient: Patient
    target_date: date
    start_time: time
    end_time: time
    type: str
    required_staff_count: int
    group_key: UUID | None  # 組 (写す元の visit_group_id / 補う 2 名体制は採番)
    kaipoke_service_override: str | None
    # 1st 候補のテンプレート (写す元のコース / 固定訪問の指定)。臨時・無しは None
    template_id: UUID | None
    weekday: int
    temp_course: bool
    not_in_fixed: bool
    # 写す元の担当 (primary / secondary / VSA)。同じ人が続かないための検査に使う
    source_staff_ids: frozenset[UUID] = frozenset()
    # ---- 置き場所 (``_place_items`` が決める) ----
    course_template_id: UUID | None = None
    staff_after: UUID | None = None
    manual_reason: str | None = None

    @property
    def patient_id(self) -> UUID:
        return self.patient.id

    @property
    def patient_name(self) -> str:
        return self.patient.name


@dataclass
class MissingFixed:
    patient: Patient
    weekday: int
    target_date: date
    start_time: time
    end_time: time
    visits: int
    entries: list[PatientFixedVisit]


@dataclass
class CopyPlan:
    source_monday: date
    target_monday: date
    mode: str  # 'replace' | 'add_only'
    items: list[CopyItem] = field(default_factory=list)
    fill_items: list[CopyItem] = field(default_factory=list)
    # 利用者の除外前の「固定訪問に無い」訪問 (一覧表示用)
    not_in_fixed: list[CopyItem] = field(default_factory=list)
    excluded_ids: set[UUID] = field(default_factory=set)
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    replace_visits: list[Visit] = field(default_factory=list)
    # 置き換えの週で、残す訪問 (取消以外) の無いコース = 担当を外す (M-1)
    cleared_course_ids: set[UUID] = field(default_factory=set)
    existing: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    missing_fixed: list[MissingFixed] = field(default_factory=list)
    missing_patients_without_visits: int = 0
    fill_from_fixed: bool = False

    @property
    def fill_count(self) -> int:
        return len(self.fill_items)

    @property
    def needs_manual_staff(self) -> list[CopyItem]:
        return [it for it in [*self.items, *self.fill_items] if it.manual_reason]


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
    """写す内容と置き場所を決める (DB は変えない)."""
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
    kept_kind: dict[UUID, str] = {}
    plan.existing["total"] = len(target_visits)
    for v in target_visits:
        kind = _classify_existing(v, checked_in=target_checked, today=today)
        if mode == "replace" and kind is None:
            plan.replace_visits.append(v)
            continue
        kept.append(v)
        kept_kind[v.id] = kind or "keep_other"
        plan.existing[kind or "keep_other"] += 1
    plan.existing["replace"] = len(plan.replace_visits)
    # 取消行と同じ枠には写さない (同じキーは本番の部分 UNIQUE にも当たる)。
    kept_keys = {(v.patient_id, v.visit_date, v.start_time) for v in kept}
    # 打刻・取込・青ピンの訪問がある (患者・日) は時刻を問わず写さない (M-4)。
    blocked_days = {
        (v.patient_id, v.visit_date) for v in kept if kept_kind[v.id] in _KEEP_BLOCKS_DAY
    }
    occupied_days = {(v.patient_id, v.visit_date) for v in kept}

    if mode == "replace":
        # 残す訪問 (取消以外) の無いコースは担当を外す (M-1)。前の担当が残ると、
        # 盤面 (コース担当が正典) と写した訪問の primary / VSA が食い違う。
        live_kept_courses = {
            v.course_id
            for v in kept
            if v.course_id is not None and v.status != VISIT_STATUS_CANCELLED
        }
        iso_t = target_monday.isocalendar()
        plan.cleared_course_ids = {
            c.id
            for c in (
                await db.scalars(
                    select(Course).where(
                        Course.iso_year == iso_t.year,
                        Course.iso_week == iso_t.week,
                        Course.deleted_at.is_(None),
                        Course.assigned_staff_id.is_not(None),
                    )
                )
            ).all()
            if c.id not in live_kept_courses
        }

    # ----- 写す元の訪問 -----
    source_visits = await _live_week_visits(db, source_monday)
    patient_ids = {v.patient_id for v in source_visits}
    patients: dict[UUID, Patient] = {}
    if patient_ids:
        patients = {
            p.id: p
            for p in (
                await db.scalars(select(Patient).where(Patient.id.in_(list(patient_ids))))
            ).all()
        }
    source_ids = [v.id for v in source_visits]
    extra_ids: set[UUID] = set()
    vsa_by_visit: dict[UUID, set[UUID]] = defaultdict(set)
    if source_ids:
        extra_ids = set(
            (
                await db.scalars(
                    select(SpecialVisitMark.placed_visit_id).where(
                        SpecialVisitMark.kind == MARK_KIND_EXTRA,
                        SpecialVisitMark.status != MARK_STATUS_CANCELLED,
                        SpecialVisitMark.placed_visit_id.in_(source_ids),
                    )
                )
            ).all()
        )
        for vid, sid in (
            await db.execute(
                select(VisitStaffAssignment.visit_id, VisitStaffAssignment.staff_id).where(
                    VisitStaffAssignment.visit_id.in_(source_ids)
                )
            )
        ).all():
            vsa_by_visit[vid].add(sid)
    course_ids = {v.course_id for v in source_visits if v.course_id is not None}
    courses: dict[UUID, Course] = {}
    if course_ids:
        courses = {
            c.id: c
            for c in (await db.scalars(select(Course).where(Course.id.in_(list(course_ids))))).all()
        }
    live_templates = await _live_template_ids(
        db, {c.template_id for c in courses.values() if c.template_id is not None}
    )
    fixed_weekdays = await _fixed_weekdays_by_patient(db, patients, source_monday)

    # 2 名体制は組で扱う。利用者が 1 行外すと組ごと外す。
    group_members: dict[UUID, list[Visit]] = defaultdict(list)
    for v in source_visits:
        if v.visit_group_id is not None:
            group_members[v.visit_group_id].append(v)
    excluded = set(exclude_visit_ids)
    for members in group_members.values():
        if any(m.id in excluded for m in members):
            excluded.update(m.id for m in members)
    plan.excluded_ids = excluded

    # 1) 1 行ずつの理由
    own_reason: dict[UUID, str | None] = {}
    candidates: dict[UUID, CopyItem] = {}
    for v in source_visits:
        patient = patients.get(v.patient_id)
        target_date = v.visit_date + offset
        reason: str | None = None
        if v.status == VISIT_STATUS_CANCELLED:
            reason = "cancelled"
        elif v.is_unplanned:
            reason = "unplanned"
        elif v.id in extra_ids:
            reason = "special_extra"
        elif (
            patient is None
            or patient.deleted_at is not None
            or not is_schedulable_status(patient.status)
        ):
            reason = "inactive_patient"
        elif v.id in excluded:
            reason = "user_excluded"
        elif target_date < today:
            reason = "past_day"
        elif mode == "add_only" and (v.patient_id, target_date) in occupied_days:
            reason = "occupied_day"
        elif (v.patient_id, target_date) in blocked_days:
            reason = "kept_same_day"
        elif (v.patient_id, target_date, v.start_time) in kept_keys:
            reason = "kept_conflict"
        own_reason[v.id] = reason
        if patient is None:
            continue
        course = courses.get(v.course_id) if v.course_id is not None else None
        temp = course is not None and course.code in TEMP_COURSE_CODES
        weekday = v.visit_date.weekday()
        candidates[v.id] = CopyItem(
            key=v.id,
            origin="copy",
            patient=patient,
            target_date=target_date,
            start_time=v.start_time,
            end_time=v.end_time,
            type=v.type,
            required_staff_count=v.required_staff_count,
            group_key=v.visit_group_id,
            kaipoke_service_override=v.kaipoke_service_override,
            template_id=(
                course.template_id
                if course is not None and not temp and course.template_id in live_templates
                else None
            ),
            weekday=weekday,
            temp_course=temp,
            not_in_fixed=weekday not in fixed_weekdays.get(v.patient_id, set()),
            source_staff_ids=frozenset(
                {sid for sid in (v.primary_staff_id, v.secondary_staff_id) if sid is not None}
                | vsa_by_visit.get(v.id, set())
            ),
        )

    # 2) 組の誰か 1 人でも写せないなら組ごと写さない (M-3)
    for v in source_visits:
        reason = own_reason[v.id]
        group = group_members.get(v.visit_group_id, []) if v.visit_group_id else []
        if reason is None and any(own_reason[m.id] for m in group):
            reason = "pair_partner"
        it = candidates.get(v.id)
        # 一覧に出すのは「写す」か「利用者が外した」ものだけ (外せない理由のものは出さない)
        if (
            it is not None
            and it.not_in_fixed
            and all(own_reason[m.id] in (None, "user_excluded") for m in (group or [v]))
        ):
            plan.not_in_fixed.append(it)
        if reason is not None:
            plan.skipped[reason] += 1
            continue
        if it is not None:
            plan.items.append(it)

    # ----- 写す元の週に無かった固定訪問 (補う候補) -----
    await _plan_missing_fixed(db, plan, today=today, live_after=kept)
    if fill_from_fixed:
        plan.fill_items = _fill_items(plan.missing_fixed)

    # ----- 置き場所 (コース) と担当の検査 (H-1 / M-6 / M-2) -----
    await _place_items(db, plan, kept=kept)
    return plan


async def _live_template_ids(db: AsyncSession, ids: set[UUID]) -> set[UUID]:
    if not ids:
        return set()
    return set(
        (
            await db.scalars(
                select(CourseTemplate.id).where(
                    CourseTemplate.id.in_(list(ids)), CourseTemplate.deleted_at.is_(None)
                )
            )
        ).all()
    )


async def _plan_missing_fixed(
    db: AsyncSession, plan: CopyPlan, *, today: date, live_after: list[Visit]
) -> None:
    """固定訪問のうち、写した後の週でその (患者・日) に訪問が 1 件も無いもの.

    固定訪問と特別訪問週間の退避日はまとめて読む (患者ごとの N+1 を避ける)。
    """
    iso = plan.target_monday.isocalendar()
    after_copy_days = {(it.patient_id, it.target_date) for it in plan.items}
    after_copy_days |= {(v.patient_id, v.visit_date) for v in live_after}

    active = list(
        (
            await db.scalars(
                select(Patient)
                .where(Patient.status == "active", Patient.deleted_at.is_(None))
                .order_by(Patient.code)
            )
        ).all()
    )
    if not active:
        return
    active_ids = [p.id for p in active]
    pfv_by_patient: dict[UUID, list[PatientFixedVisit]] = defaultdict(list)
    for fv in (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id.in_(active_ids))
        )
    ).all():
        pfv_by_patient[fv.patient_id].append(fv)
    displaced: dict[UUID, set[int]] = defaultdict(set)
    for pid, wd in (
        await db.execute(
            select(SpecialVisitMark.patient_id, SpecialVisitMark.weekday).where(
                SpecialVisitMark.iso_year == iso.year,
                SpecialVisitMark.iso_week == iso.week,
                SpecialVisitMark.kind == MARK_KIND_DISPLACED,
                SpecialVisitMark.status != MARK_STATUS_CANCELLED,
                SpecialVisitMark.patient_id.in_(active_ids),
            )
        )
    ).all():
        displaced[pid].add(wd)

    copied_patients = {it.patient_id for it in plan.items}
    without_visits: set[UUID] = set()
    for patient in active:
        mode = "special" if _is_special_week_active(patient, iso.year, iso.week) else "normal"
        rows = [fv for fv in pfv_by_patient.get(patient.id, []) if fv.mode == mode]
        if not rows:
            continue
        multi = bool(patient.requires_multiple_staff)
        by_weekday: dict[int, dict[int, PatientFixedVisit]] = defaultdict(dict)
        for fv in rows:
            by_weekday[fv.weekday].setdefault(fv.slot_index or 0, fv)
        for weekday in sorted(by_weekday):
            slots = by_weekday[weekday]
            if weekday in displaced[patient.id] or 0 not in slots or (multi and 1 not in slots):
                continue  # 週生成と同じく作らない枠 (退避・2 名体制の片側欠け)
            target_date = plan.target_monday + timedelta(days=weekday)
            if target_date < today or (patient.id, target_date) in after_copy_days:
                continue
            start = slots[0].start_time
            end_min = start.hour * 60 + start.minute + int(slots[0].duration_min)
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
                )
            )
            if patient.id not in copied_patients:
                without_visits.add(patient.id)
    plan.missing_patients_without_visits = len(without_visits)


def _fill_items(missing: list[MissingFixed]) -> list[CopyItem]:
    """補う固定訪問を、写す訪問と同じ置き場所の処理に乗せるための行にする (M-2)."""
    out: list[CopyItem] = []
    for m in missing:
        group = uuid.uuid4() if m.visits == 2 else None
        for fv in m.entries:
            out.append(
                CopyItem(
                    key=uuid.uuid4(),
                    origin="fill",
                    patient=m.patient,
                    target_date=m.target_date,
                    start_time=m.start_time,
                    end_time=m.end_time,
                    type=LAYER1_VISIT_TYPE,
                    required_staff_count=m.visits,
                    group_key=group,
                    kaipoke_service_override=None,
                    template_id=fv.course_template_id,
                    weekday=m.weekday,
                    temp_course=False,
                    not_in_fixed=False,
                )
            )
    return out


async def _course_for(
    db: AsyncSession, template_id: UUID, iso_year: int, iso_week: int, weekday: int
) -> Course | None:
    """``_get_or_create_course_for_template_week_l1`` と同じ条件で既存コースを引く (作らない)."""
    return await db.scalar(
        select(Course).where(
            Course.template_id == template_id,
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
            Course.weekday == weekday,
            Course.deleted_at.is_(None),
        )
    )


def _overlaps(a: tuple[time, time], b: tuple[time, time]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


async def _place_items(db: AsyncSession, plan: CopyPlan, *, kept: list[Visit]) -> None:
    """各行を入れるコースを決める (H-1 / M-6 / M-2).

    候補の順: (1) 写す元のコースと同じテンプレート (補う固定訪問は固定訪問の指定)、
    (2) 患者の拠点の既定テンプレート (週生成と同じ ``_resolve_template_for_patient``)。
    候補のコースに担当が居なければそのまま入れる (自動割当がローテーションで付ける)。
    担当が居るコースへは、ほかの経路と同じ検査を通ったときだけ入れて担当を揃える:
      - NG スタッフ・性別 (``collect_constraint_warnings_for_staff``)
      - その担当の写す先の週の別の訪問と時間が重ならない
      - ローテーション: 写す元の訪問の担当 (primary / secondary / VSA) と同じ人ではない
    どれにも入れられなければコースなしで入れ、「担当を手で付ける」に数える。
    """
    items = [*plan.items, *plan.fill_items]
    if not items:
        return
    iso = plan.target_monday.isocalendar()
    expander = Layer1Expander()
    home_cache: dict[UUID, CourseTemplate | None] = {}
    live_templates = await _live_template_ids(
        db, {it.template_id for it in items if it.template_id is not None}
    )
    course_cache: dict[tuple[UUID, int], Course | None] = {}

    # 担当ごとの写す先の週の予定 (残す訪問・取消以外)。
    kept_live = [v for v in kept if v.status != VISIT_STATUS_CANCELLED]
    busy: dict[UUID, list[tuple[date, time, time]]] = defaultdict(list)
    vsa_rows: list[tuple[UUID, UUID]] = []
    if kept_live:
        vsa_rows = list(
            (
                await db.execute(
                    select(VisitStaffAssignment.visit_id, VisitStaffAssignment.staff_id).where(
                        VisitStaffAssignment.visit_id.in_([v.id for v in kept_live])
                    )
                )
            ).all()
        )
    by_id = {v.id: v for v in kept_live}
    seen: set[tuple[UUID, UUID]] = set()
    for v in kept_live:
        if v.primary_staff_id is not None:
            seen.add((v.id, v.primary_staff_id))
            busy[v.primary_staff_id].append((v.visit_date, v.start_time, v.end_time))
    for vid, sid in vsa_rows:
        if (vid, sid) not in seen:
            seen.add((vid, sid))
            v = by_id[vid]
            busy[sid].append((v.visit_date, v.start_time, v.end_time))

    names: dict[UUID, str] = {}

    async def staff_name(sid: UUID) -> str:
        if sid not in names:
            names[sid] = (await db.scalar(select(Staff.name).where(Staff.id == sid))) or "担当"
        return names[sid]

    placed_in_group: dict[UUID, set[tuple[UUID, int]]] = defaultdict(set)
    for it in items:
        cands: list[UUID] = []
        if it.template_id is not None and it.template_id in live_templates:
            cands.append(it.template_id)
        home = await expander._resolve_template_for_patient(
            db, patient=it.patient, template_cache=home_cache
        )
        if home is not None and home.id not in cands:
            cands.append(home.id)
        reasons: list[str] = []
        for tpl_id in cands:
            ckey = (tpl_id, it.weekday)
            if it.group_key is not None and ckey in placed_in_group[it.group_key]:
                reasons.append("2 名体制の相方と同じコースになるため")
                continue
            if ckey not in course_cache:
                course_cache[ckey] = await _course_for(db, tpl_id, iso.year, iso.week, it.weekday)
            course = course_cache[ckey]
            staff = None
            if course is not None and course.id not in plan.cleared_course_ids:
                staff = course.assigned_staff_id
            if staff is not None:
                name = await staff_name(staff)
                if staff in it.source_staff_ids:
                    reasons.append(f"前の週と同じ担当（{name}）のコースになるため")
                    continue
                warns = await collect_constraint_warnings_for_staff(
                    db, staff_id=staff, patient_ids=[it.patient_id]
                )
                if warns:
                    kinds = {w.kind for w in warns}
                    label = "・".join(
                        (["NG スタッフ"] if "ng_staff" in kinds else [])
                        + (["性別の希望"] if "gender" in kinds else [])
                    )
                    reasons.append(f"コースの担当（{name}）が{label}に当たるため")
                    continue
                me = (it.start_time, it.end_time)
                if any(d == it.target_date and _overlaps(me, (s, e)) for d, s, e in busy[staff]):
                    reasons.append(f"コースの担当（{name}）の別の訪問と時間が重なるため")
                    continue
                busy[staff].append((it.target_date, it.start_time, it.end_time))
            it.course_template_id = tpl_id
            it.staff_after = staff
            if it.group_key is not None:
                placed_in_group[it.group_key].add(ckey)
            break
        else:
            it.manual_reason = (
                "・".join(dict.fromkeys(reasons))
                if reasons
                else "入れられるコースがありません（拠点の既定コースがありません）"
            )


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
    for v in plan.replace_visits:
        v.deleted_at = now
    result.replaced = len(plan.replace_visits)
    await db.flush()

    # 2) 残す訪問の無いコースの担当を外す (M-1)。
    if plan.cleared_course_ids:
        for c in (
            await db.scalars(select(Course).where(Course.id.in_(list(plan.cleared_course_ids))))
        ).all():
            c.assigned_staff_id = None
            c.staff_assigned_at = None
            if c.course_status == COURSE_STATUS_STAFF_ASSIGNED:
                c.course_status = COURSE_STATUS_COURSE_FIXED
            result.courses_cleared += 1
        await db.flush()

    # 3) 写す / 補う。コースは計画で決めたテンプレートの同じ曜日のコース (無ければ作る)。
    items = [*plan.items, *plan.fill_items]
    tpl_ids = {it.course_template_id for it in items if it.course_template_id is not None}
    templates: dict[UUID, CourseTemplate] = {}
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
    created: list[tuple[CopyItem, Visit]] = []
    for it in items:
        course: Course | None = None
        tpl = templates.get(it.course_template_id) if it.course_template_id else None
        if tpl is not None:
            key = (tpl.id, it.weekday)
            course = course_cache.get(key)
            if course is None:
                course = await _get_or_create_course_for_template_week_l1(
                    db, template=tpl, iso_year=iso.year, iso_week=iso.week, weekday=it.weekday
                )
                course_cache[key] = course
        group_id = None
        if it.group_key is not None:
            group_id = new_group.setdefault(it.group_key, uuid.uuid4())
        v = Visit(
            patient_id=it.patient_id,
            visit_date=it.target_date,
            start_time=it.start_time,
            end_time=it.end_time,
            type=it.type,
            status=VISIT_STATUS_PLANNED,
            source=VISIT_SOURCE_MANUAL_WEEK if it.origin == "copy" else LAYER1_VISIT_SOURCE,
            required_staff_count=it.required_staff_count,
            visit_group_id=group_id,
            kaipoke_service_override=it.kaipoke_service_override,
            course_id=course.id if course is not None else None,
        )
        db.add(v)
        created.append((it, v))
    await db.flush()
    result.created = sum(1 for it, _ in created if it.origin == "copy")
    result.filled = sum(1 for it, _ in created if it.origin == "fill")
    result.courses_created = await _count_week_courses(db, iso.year, iso.week) - before_courses

    # 4) 検査を通って担当の決まったコースへ入れた訪問は、コース・primary・VSA の
    #    3 か所を揃える。写す元の担当ではない (写さない)。
    by_group: dict[UUID, list[tuple[CopyItem, Visit]]] = defaultdict(list)
    for it, v in created:
        if it.group_key is not None:
            by_group[it.group_key].append((it, v))
    for it, v in created:
        if it.staff_after is None:
            continue
        secondary = None
        for p_it, _ in by_group.get(it.group_key, []) if it.group_key else []:
            if p_it is not it and p_it.staff_after not in (None, it.staff_after):
                secondary = p_it.staff_after
                break
        v.primary_staff_id = it.staff_after
        v.secondary_staff_id = secondary
        db.add(VisitStaffAssignment(visit_id=v.id, staff_id=it.staff_after))
        if secondary is not None:
            db.add(VisitStaffAssignment(visit_id=v.id, staff_id=secondary))
        result.staff_mirrored += 1
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
