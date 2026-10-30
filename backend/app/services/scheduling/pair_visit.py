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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.layer1_expander import _is_special_week_active

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
    """1 行の 2 名で訪問 → その週・その曜日の 2 人目 (slot_index=1) のコース.

    2 人目のコースが引けない訪問 (固定訪問に slot 1 が無い・ひな形が無い・その週の
    コースが無い・1 人目と同じコース) は結果に入れない。
    """
    targets = [v for v in visits if is_single_row_pair(v) and v.visit_date is not None]
    if not targets:
        return {}
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
    if not patients:
        return {}
    slot1: dict[tuple[UUID, str, int], UUID] = {}
    for row in (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id.in_(list(patients)),
                PatientFixedVisit.slot_index == 1,
                PatientFixedVisit.course_template_id.is_not(None),
            )
        )
    ).all():
        slot1[(row.patient_id, row.mode, row.weekday)] = row.course_template_id

    wanted: dict[UUID, tuple[UUID, int, int, int]] = {}
    for v in targets:
        patient = patients.get(v.patient_id)
        if patient is None:
            continue
        iso = v.visit_date.isocalendar()
        wd = v.visit_date.weekday()
        modes = (
            ("special", "normal")
            if _is_special_week_active(patient, iso.year, iso.week)
            else ("normal",)
        )
        tpl = next((slot1[k] for m in modes if (k := (v.patient_id, m, wd)) in slot1), None)
        if tpl is not None:
            wanted[v.id] = (tpl, iso.year, iso.week, wd)
    if not wanted:
        return {}
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
    out: dict[UUID, Course] = {}
    for v in targets:
        key = wanted.get(v.id)
        course = by_key.get(key) if key is not None else None
        if course is not None and course.id != v.course_id:
            out[v.id] = course
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
    """
    by_tpl_key: dict[tuple[UUID, int, int, int], Course] = {}
    for c in courses:
        if c.template_id is not None and c.deleted_at is None:
            by_tpl_key[(c.template_id, c.iso_year, c.iso_week, c.weekday)] = c
    if not by_tpl_key:
        return {}
    templates = {k[0] for k in by_tpl_key}
    pfv_rows = (
        await db.execute(
            select(PatientFixedVisit.patient_id, PatientFixedVisit.weekday).where(
                PatientFixedVisit.slot_index == 1,
                PatientFixedVisit.course_template_id.in_(list(templates)),
            )
        )
    ).all()
    if not pfv_rows:
        return {}
    patient_ids = {pid for pid, _ in pfv_rows}
    dates: set[date] = set()
    for _tpl, y, w, wd in by_tpl_key:
        try:
            dates.add(date.fromisocalendar(y, w, 1) + timedelta(days=wd))
        except ValueError:
            continue
    if not dates:
        return {}
    excluded = set(exclude_visit_ids)
    candidates = [
        v
        for v in (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id.in_(list(patient_ids)),
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
    wanted_ids = {c.id for c in by_tpl_key.values()}
    for v in candidates:
        partner = partners.get(v.id)
        if partner is not None and partner.id in wanted_ids:
            out[partner.id].append(v)
    return dict(out)
