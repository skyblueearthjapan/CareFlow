"""DB → 型の作業場の材料 (``raw``)。agent/tools/courseplan/extract.py の SQL と同じ中身を ORM で読む.

SQLite (テスト) でも動くよう、PG 専用 SQL (json_build_object) は使わない。読むだけ。
固定訪問の枠の ``id`` は ``<患者ID>:<曜日>:<slot>`` の合成キー (下書きの枠のキーと同じ形)。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff, StaffEventDefault, StaffShift

TEST_PREFIX = "【検証】"
CAP_KEYS = ("capacity_mon", "capacity_tue", "capacity_wed", "capacity_thu", "capacity_fri", "capacity_sat", "capacity_sun")


def slot_key(patient_id, weekday: int, slot_index: int) -> str:
    return f"{patient_id}:{int(weekday)}:{int(slot_index)}"


def new_slot_key(patient_id, weekday: int, slot_index: int) -> str:
    return f"new:{patient_id}:{int(weekday)}:{int(slot_index)}"


def _hm(t) -> str | None:
    return t.strftime("%H:%M") if t is not None else None


def _f(x) -> float | None:
    return float(x) if x is not None else None


def office_label(o: Office) -> str:
    return (o.short_label or o.name or "")[:8]


async def build_raw(db: AsyncSession, today: date | None = None) -> dict:
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    since = monday - timedelta(days=35)
    until = monday + timedelta(days=13)

    offices = (await db.scalars(select(Office).where(Office.deleted_at.is_(None)))).all()
    tmpls = (
        await db.scalars(select(CourseTemplate).where(CourseTemplate.deleted_at.is_(None)))
    ).all()
    office_ids = {o.id for o in offices}
    tmpls = [t for t in tmpls if t.office_id in office_ids]

    pfv_rows = (
        await db.execute(
            select(PatientFixedVisit, Patient)
            .join(Patient, Patient.id == PatientFixedVisit.patient_id)
            .where(
                PatientFixedVisit.mode == "normal",
                Patient.deleted_at.is_(None),
                Patient.status == "active",
            )
            .order_by(PatientFixedVisit.patient_id, PatientFixedVisit.weekday, PatientFixedVisit.slot_index)
        )
    ).all()
    pfv, pats = [], {}
    for f, p in pfv_rows:
        if (p.name or "").startswith(TEST_PREFIX):
            continue
        pats[p.id] = p
        pfv.append(
            {
                "id": slot_key(f.patient_id, f.weekday, f.slot_index),
                "real_id": str(f.id),
                "patient_id": str(f.patient_id),
                "weekday": int(f.weekday),
                "start_time": _hm(f.start_time),
                "duration_min": int(f.duration_min),
                "course_template_id": str(f.course_template_id) if f.course_template_id else None,
                "sub_office_id": str(f.sub_office_id) if f.sub_office_id else None,
                "slot_index": int(f.slot_index),
                "movability": f.movability,
                "is_pinned": bool(f.is_pinned),
            }
        )

    def pat_row(p: Patient, with_status: bool = False) -> dict:
        d = {
            "id": str(p.id),
            "code": p.code,
            "name": p.name,
            "sex_restriction": p.sex_restriction,
            "lat": _f(p.lat),
            "lng": _f(p.lng),
            "primary_office_id": str(p.primary_office_id) if p.primary_office_id else None,
            "weekly_pattern": p.weekly_pattern or None,
            "requires_multiple_staff": bool(p.requires_multiple_staff),
        }
        if with_status:
            d["status"] = p.status
        return d

    have = {p.id for p in pats.values()}
    cand_rows = (
        await db.scalars(
            select(Patient).where(Patient.deleted_at.is_(None), Patient.status.in_(("active", "pending")))
        )
    ).all()
    candidates = [
        pat_row(p, True) for p in cand_rows if p.id not in have and not (p.name or "").startswith(TEST_PREFIX)
    ]

    usual = {}
    for c in (
        await db.scalars(
            select(Course).where(
                Course.deleted_at.is_(None),
                Course.course_status != "proposed",
                Course.template_id.is_not(None),
                Course.assigned_staff_id.is_not(None),
            )
        )
    ).all():
        try:
            wk = date.fromisocalendar(int(c.iso_year), int(c.iso_week), 1)
        except ValueError:
            continue
        if not (since <= wk <= until):
            continue
        k = (str(c.template_id), int(c.weekday), str(c.assigned_staff_id))
        usual[k] = usual.get(k, 0) + 1

    staff = (
        await db.scalars(select(Staff).where(Staff.status == "active", Staff.deleted_at.is_(None)))
    ).all()
    shifts = (await db.scalars(select(StaffShift))).all()
    evs = (await db.scalars(select(StaffEventDefault))).all()
    ngs = (await db.scalars(select(PatientNgStaff))).all()

    return {
        "extracted_at": today.isoformat(),
        "offices": [
            {
                "id": str(o.id),
                "short_label": office_label(o),
                "lat": _f(o.lat),
                "lng": _f(o.lng),
                "operating_weekdays": list(o.operating_weekdays or [0, 1, 2, 3, 4, 5]),
            }
            for o in offices
        ],
        "templates": [
            {
                "id": str(t.id),
                "office_id": str(t.office_id),
                "label": t.label,
                "capacity": [getattr(t, k) for k in CAP_KEYS],
            }
            for t in tmpls
        ],
        "pfv": pfv,
        "patients": [pat_row(p) for p in pats.values()],
        "candidates": candidates,
        "usual": [{"template_id": k[0], "weekday": k[1], "staff_id": k[2], "n": n} for k, n in usual.items()],
        "staff": [
            {
                "id": str(s.id),
                "code": s.code,
                "name": s.name,
                "sex": s.sex,
                "role": s.role,
                "is_trainee": bool(s.is_trainee),
                "primary_office_id": str(s.primary_office_id) if s.primary_office_id else None,
            }
            for s in staff
        ],
        "shifts": [
            {
                "staff_id": str(r.staff_id),
                "weekday": int(r.weekday),
                "is_on": bool(r.is_on),
                "start_time": _hm(r.start_time),
                "end_time": _hm(r.end_time),
            }
            for r in shifts
        ],
        "event_defaults": [
            {
                "staff_id": str(r.staff_id),
                "weekday": int(r.weekday),
                "start_time": _hm(r.start_time),
                "end_time": _hm(r.end_time),
                "title": r.title,
            }
            for r in evs
        ],
        "ng": [{"patient_id": str(r.patient_id), "staff_id": str(r.staff_id)} for r in ngs],
    }


def row_hash(r: dict) -> tuple:
    mv = r.get("movability") or ("locked" if r.get("is_pinned") else "unknown")
    return (
        int(r["weekday"]),
        str(r.get("start_time"))[:5],
        int(r.get("duration_min") or 35),
        r.get("course_template_id") or "",
        r.get("sub_office_id") or "",
        int(r.get("slot_index") or 0),
        mv,
    )


def master_hashes(pfv_rows: list[dict], patient_ids) -> dict[str, str]:
    """患者ごとの固定訪問の枠 (normal) の指紋。枠が無い方は空の指紋 (agent の draft.master_hashes と同じ)."""
    by: dict[str, list] = {str(pid): [] for pid in patient_ids}
    for r in pfv_rows:
        if r["patient_id"] in by:
            by[r["patient_id"]].append(row_hash(r))
    return {
        pid: hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        for pid, rows in by.items()
    }


def combined_hash(hashes: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(sorted(hashes.items())).encode("utf-8")).hexdigest()[:16]


async def current_master_hashes(db: AsyncSession, patient_ids: list[str]) -> dict[str, str]:
    """今のマスター (DB) のその方々の枠の指紋。"""
    ids = [UUID(p) for p in patient_ids]
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.mode == "normal", PatientFixedVisit.patient_id.in_(ids)
            )
        )
    ).all()
    pfv = [
        {
            "patient_id": str(f.patient_id),
            "weekday": int(f.weekday),
            "start_time": _hm(f.start_time),
            "duration_min": int(f.duration_min),
            "course_template_id": str(f.course_template_id) if f.course_template_id else None,
            "sub_office_id": str(f.sub_office_id) if f.sub_office_id else None,
            "slot_index": int(f.slot_index),
            "movability": f.movability,
            "is_pinned": bool(f.is_pinned),
        }
        for f in rows
    ]
    return master_hashes(pfv, patient_ids)
