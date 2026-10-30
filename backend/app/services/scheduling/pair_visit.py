"""2 名で訪問 (required_staff_count=2) を 1 行で持つための共通の処理.

本番の形 (2026-10-30 確認・カイポケ取込と同じ):

    2 名で訪問は **visits の 1 行**。``required_staff_count=2``・``primary_staff_id`` =
    1 人目 (固定訪問 slot_index=0 のコースの担当)・``secondary_staff_id`` = 2 人目
    (slot_index=1 のコースのその日の担当)・``visit_group_id`` は NULL。``course_id`` は
    1 人目のコース。

    本番の partial UNIQUE ``uq_visits_pds_group_active`` (patient_id, visit_date,
    start_time, COALESCE(visit_group_id, NIL)) は、同じ患者・日・時刻・組 id の 2 行を
    許さない。2 行 (同じ visit_group_id) で作ると本番では INSERT が落ちる。

固定訪問 (patient_fixed_visits) は従来どおり slot_index 0/1 の 2 行を型として持つ。
slot_index=1 の行の ``course_template_id`` が「2 人目のコース」を覚えている。
ここでは、その週のその曜日の 2 人目のコース (courses) を引く。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.layer1_expander import _is_special_week_active
from app.services.scheduling.visit_frequency import pfv_visits_in_week

__all__ = [
    "is_single_row_pair",
    "load_pair_partner_courses",
    "load_pair_visits_by_partner_course",
    "pair_secondary_staff",
]


def is_single_row_pair(visit: Visit) -> bool:
    """1 行で持つ 2 名で訪問か (required_staff_count>=2 かつ visit_group_id なし)."""
    return int(visit.required_staff_count or 1) >= 2 and visit.visit_group_id is None


def pair_secondary_staff(primary: UUID | None, partner: UUID | None) -> UUID | None:
    """2 人目の担当。1 人目と同じ人・未定なら None (2 人目が足りない扱い)."""
    if partner is None or partner == primary:
        return None
    return partner


async def load_pair_partner_courses(
    db: AsyncSession, visits: Iterable[Visit]
) -> dict[UUID, Course]:
    """1 行の 2 名で訪問 → その日の 2 人目のコース.

    引き方 (盤面の ``pairPartnerCourseOf`` と同じ):

    1. 固定訪問の slot_index=1 の行のひな形から、その週・その曜日のコース。
       slot 0 と同じく、特別週の枠があればその週は特別週の枠だけを見て、通常の枠は
       訪問の週 (隔週の A/B・月の第何週) に当たるときだけ使う。
    2. 1 で引けない訪問 (カイポケ取込・今週だけの配置など slot 1 が無い) は、その日の
       コースのうち担当が ``secondary_staff_id`` の人のコース。1 人目のコースと同じ
       拠点を先に、同じ拠点の中ではコースの記号・id の順で 1 つに決める。

    どちらでも引けない訪問 (2 人目がいない・2 人目のコースがその日に無い・1 人目と
    同じコース) は結果に入れない。2 人目のコースが無い人には提案・一括投入の置き場
    (コース) も無いので、ふさぐ時間も無い。
    """
    targets = [v for v in visits if is_single_row_pair(v) and v.visit_date is not None]
    if not targets:
        return {}
    out: dict[UUID, Course] = {}

    # ---- 1. 固定訪問の slot 1 ----
    patient_ids = {v.patient_id for v in targets}
    patients = {
        p.id: p
        for p in (
            await db.scalars(
                select(Patient).where(
                    Patient.id.in_(list(patient_ids)),
                    Patient.requires_multiple_staff.is_(True),
                )
            )
        ).all()
    }
    wanted: dict[UUID, tuple[UUID, int, int, int]] = {}
    if patients:
        pfv_rows = (
            await db.scalars(
                select(PatientFixedVisit).where(PatientFixedVisit.patient_id.in_(list(patients)))
            )
        ).all()
        has_special = {r.patient_id for r in pfv_rows if r.mode == "special"}
        slot1 = {
            (r.patient_id, r.mode, r.weekday): r
            for r in pfv_rows
            if r.slot_index == 1 and r.course_template_id is not None
        }
        for v in targets:
            patient = patients.get(v.patient_id)
            if patient is None:
                continue
            iso = v.visit_date.isocalendar()
            # slot 0 (Layer1) と同じ: 特別週で特別週の枠がある方は特別週の枠だけ.
            mode = (
                "special"
                if v.patient_id in has_special
                and _is_special_week_active(patient, iso.year, iso.week)
                else "normal"
            )
            row = slot1.get((v.patient_id, mode, v.visit_date.weekday()))
            if row is None or not pfv_visits_in_week(patient.weekly_pattern, row, v.visit_date):
                continue
            wanted[v.id] = (row.course_template_id, iso.year, iso.week, v.visit_date.weekday())
    if wanted:
        courses = (
            await db.scalars(
                select(Course).where(
                    Course.template_id.in_({k[0] for k in wanted.values()}),
                    Course.iso_year.in_({k[1] for k in wanted.values()}),
                    Course.iso_week.in_({k[2] for k in wanted.values()}),
                    Course.deleted_at.is_(None),
                )
            )
        ).all()
        by_key = {(c.template_id, c.iso_year, c.iso_week, c.weekday): c for c in courses}
        for v in targets:
            key = wanted.get(v.id)
            course = by_key.get(key) if key is not None else None
            if course is not None and course.id != v.course_id:
                out[v.id] = course

    # ---- 2. 2 人目の担当のその日のコース ----
    rest = [v for v in targets if v.id not in out and v.secondary_staff_id is not None]
    if rest:
        isos = {v.visit_date.isocalendar() for v in rest}
        staff_courses = (
            await db.scalars(
                select(Course).where(
                    Course.assigned_staff_id.in_({v.secondary_staff_id for v in rest}),
                    Course.iso_year.in_({i.year for i in isos}),
                    Course.iso_week.in_({i.week for i in isos}),
                    Course.deleted_at.is_(None),
                )
            )
        ).all()
        by_staff_day: dict[tuple[UUID, int, int, int], list[Course]] = defaultdict(list)
        for c in staff_courses:
            if c.assigned_staff_id is not None:
                by_staff_day[(c.assigned_staff_id, c.iso_year, c.iso_week, c.weekday)].append(c)
        primary_office: dict[UUID, UUID] = {}
        primary_ids = {v.course_id for v in rest if v.course_id is not None}
        if primary_ids:
            primary_office = dict(
                (
                    await db.execute(
                        select(Course.id, Course.office_id).where(Course.id.in_(primary_ids))
                    )
                ).all()
            )
        for v in rest:
            iso = v.visit_date.isocalendar()
            cands = [
                c
                for c in by_staff_day.get(
                    (v.secondary_staff_id, iso.year, iso.week, v.visit_date.weekday()), []
                )
                if c.id != v.course_id
            ]
            if not cands:
                continue
            office = primary_office.get(v.course_id) if v.course_id is not None else None
            cands.sort(key=lambda c: (c.office_id != office, c.code or "", str(c.id)))
            out[v.id] = cands[0]
    return out


async def load_pair_visits_by_partner_course(
    db: AsyncSession,
    courses: Iterable[Course],
    *,
    exclude_visit_ids: Iterable[UUID] = (),
) -> dict[UUID, list[Visit]]:
    """コース → そのコースを 2 人目のコースとする 1 行の 2 名で訪問 (予定のみ).

    2 行で持っていたときは 2 人目のコースにも訪問が 1 行あった。1 行にした後も、
    2 人目のコースの担当を決める・数えるときに、その訪問を 2 人目のコースにも入れる。
    2 人目のコースの引き方は ``load_pair_partner_courses`` と同じ (slot 1 のひな形、
    無ければ 2 人目の担当のその日のコース)。
    """
    live = [c for c in courses if c.deleted_at is None]
    if not live:
        return {}
    dates: set[date] = set()
    for c in live:
        try:
            dates.add(date.fromisocalendar(c.iso_year, c.iso_week, 1) + timedelta(days=c.weekday))
        except ValueError:
            continue
    if not dates:
        return {}
    templates = {c.template_id for c in live if c.template_id is not None}
    staff_ids = {c.assigned_staff_id for c in live if c.assigned_staff_id is not None}
    patient_ids: set[UUID] = set()
    if templates:
        patient_ids = set(
            (
                await db.scalars(
                    select(PatientFixedVisit.patient_id).where(
                        PatientFixedVisit.slot_index == 1,
                        PatientFixedVisit.course_template_id.in_(list(templates)),
                    )
                )
            ).all()
        )
    who = []
    if patient_ids:
        who.append(Visit.patient_id.in_(list(patient_ids)))
    if staff_ids:
        who.append(Visit.secondary_staff_id.in_(list(staff_ids)))
    if not who:
        return {}
    excluded = set(exclude_visit_ids)
    candidates = [
        v
        for v in (
            await db.scalars(
                select(Visit).where(
                    or_(*who),
                    Visit.visit_date.in_(list(dates)),
                    Visit.required_staff_count >= 2,
                    Visit.visit_group_id.is_(None),
                    Visit.status == VISIT_STATUS_PLANNED,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
        if v.id not in excluded
    ]
    partners = await load_pair_partner_courses(db, candidates)
    out: dict[UUID, list[Visit]] = defaultdict(list)
    wanted_ids = {c.id for c in live}
    for v in candidates:
        partner = partners.get(v.id)
        if partner is not None and partner.id in wanted_ids:
            out[partner.id].append(v)
    return dict(out)
