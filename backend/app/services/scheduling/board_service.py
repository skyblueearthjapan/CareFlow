"""週ボード read API (Phase2-3a) サービス層.

``GET /api/v1/schedule/v2/board`` の本体ロジック:
    1. 対象週 × 拠点の実 Visit を 1 回ロードし、(office, weekday, course) 単位の
       実訪問列・担当スタッフ・コース id を構築する (N+1 回避).
       読み方は ``propose_slots_service.load_week_course_buckets`` と同手法
       (``Visit JOIN Course OUTER JOIN Staff``, planned かつ未削除) だが、
       ボードは visit_id / 保険 / kana / 住所も必要なので Patient を 1 回 join して
       それらも保持する (= 本モジュール専用ローダ).
    2. 同住所グループ id を ``schedule_v2._assign_same_address_groups`` と同等の
       ロジック (``_address_bucket`` ベース) で (office, weekday) 単位に算出する.
    3. (office × weekday) のスタッフ数を ``count_active_staff_per_weekday`` /
       ``count_active_managers_per_weekday`` で集計し、休講 (staff 0) 判定に使う.

read-only: DB は読むだけ. 実 Visit 由来の **実時刻** をそのまま返す.
モックの SLOT_WINDOWS は使わない (空き枠時間帯は propose-slots が扱う).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_CANCELLED, VISIT_STATUS_PLANNED, Visit
from app.services.patient_excel.schema import OFFICE_CODE_TO_SHORT
from app.services.scheduling.auto_allocator_v2 import (
    MAX_PATIENTS_PER_COURSE,
    _address_bucket,
    count_active_managers_per_weekday,
    count_active_staff_per_weekday,
)

# 保険 (患者マスタ) → ボード表示用短縮.
_INSURANCE_TO_SHORT: dict[str, str] = {"medical": "med", "care": "care"}


def _fmt_hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _time_to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def _normalize_insurance(value: str | None) -> str | None:
    """患者マスタ insurance ("medical"/"care") をボード短縮 (med/care) に正規化.

    未知値 / None は None (UI 側で非表示).
    """
    if value is None:
        return None
    return _INSURANCE_TO_SHORT.get(value)


def _office_short(office_code: str | None) -> str:
    """拠点短縮 (course_label 用). office_code 基準の正準マッピング.

    INAGE→稲 / TSUGA→津 (``patient_excel.schema.OFFICE_CODE_TO_SHORT``). 患者 Excel・
    モバイルモック・グリッド集計と同一の短縮に統一する. マップに無い拠点コードは
    コードそのものを返す (先頭 1 字ヒューリスティックで「都」等を出さない). office_code
    が無い場合は空文字 (= ラベルは course_code のみ)."""
    if not office_code:
        return ""
    return OFFICE_CODE_TO_SHORT.get(office_code, office_code)


@dataclass
class BoardVisitData:
    """ボード 1 訪問 (実 Visit 由来)."""

    visit_id: UUID
    patient_id: UUID
    patient_name: str
    patient_kana: str | None
    insurance: str | None  # 正規化済 (med/care/None)
    service_minutes: int
    start_time: time
    end_time: time
    address: str | None
    lat: float | None
    lng: float | None
    mode: str  # normal/special
    status: str = VISIT_STATUS_PLANNED  # planned / cancelled
    same_address_group_id: str | None = None
    slot_index: int = 0


@dataclass
class BoardCourseData:
    """(office, weekday, course_code) 1 コース."""

    office_id: UUID
    office_code: str | None
    weekday: int
    course_code: str
    course_id: UUID | None
    staff_name: str | None
    visits: list[BoardVisitData] = field(default_factory=list)


async def load_board_buckets(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
    include_cancelled: bool = False,
) -> tuple[dict[tuple[UUID, int, str], BoardCourseData], dict[UUID, str], dict[UUID, str | None]]:
    """対象週 × 拠点の実 Visit を 1 回ロードし、コース単位に集計する.

    ``propose_slots_service.load_week_course_buckets`` と同じ読み方
    (planned かつ未削除 visit を Course/Staff 経由でグルーピング) に加え、
    ボード描画に必要な Patient (name/kana/insurance/address/座標) を 1 回で join する.

    Returns:
        ``({(office_id, weekday, course_code): BoardCourseData}, {office_id: name},
        {office_id: code})``. ``code`` は course_label 用の正準短縮を引くために使う
        (office_code 基準. name 先頭 1 字ヒューリスティックは廃止).
    """
    try:
        week_monday = date.fromisocalendar(iso_year, iso_week, 1)
        week_sunday = date.fromisocalendar(iso_year, iso_week, 7)
    except ValueError as exc:
        raise ValueError(f"無効な ISO 週: {iso_year}-W{iso_week}") from exc
    week_upper = week_sunday + timedelta(days=1)

    allowed_statuses = [VISIT_STATUS_PLANNED]
    if include_cancelled:
        allowed_statuses.append(VISIT_STATUS_CANCELLED)

    stmt = (
        select(Visit, Course, Staff)
        .join(Course, Course.id == Visit.course_id)
        .outerjoin(Staff, Staff.id == Course.assigned_staff_id)
        .where(
            Visit.deleted_at.is_(None),
            Visit.status.in_(allowed_statuses),
            Visit.visit_date >= week_monday,
            Visit.visit_date < week_upper,
            Course.deleted_at.is_(None),
            Course.iso_year == iso_year,
            Course.iso_week == iso_week,
        )
    )
    if office_ids:
        stmt = stmt.where(Course.office_id.in_(office_ids))

    rows = (await db.execute(stmt)).all()

    # patient を 1 回でまとめてロード (N+1 回避).
    patient_ids = {v.patient_id for (v, _c, _s) in rows}
    patients_by_id: dict[UUID, Patient] = {}
    if patient_ids:
        prows = await db.scalars(
            select(Patient).where(
                Patient.id.in_(patient_ids),
                Patient.deleted_at.is_(None),
            )
        )
        patients_by_id = {p.id: p for p in prows.all()}

    # 拠点 name / code map (対象 office + バケットに出た office) を先に 1 回ロード.
    # code は course_label の正準短縮 (OFFICE_CODE_TO_SHORT) を引くために course/cell へ持たせる.
    office_ids_in_use: set[UUID] = {course.office_id for (_v, course, _s) in rows} | set(office_ids)
    office_name_by_id: dict[UUID, str] = {}
    office_code_by_id: dict[UUID, str | None] = {}
    if office_ids_in_use:
        orows = await db.scalars(select(Office).where(Office.id.in_(office_ids_in_use)))
        for o in orows.all():
            office_name_by_id[o.id] = o.name
            office_code_by_id[o.id] = o.code

    buckets: dict[tuple[UUID, int, str], BoardCourseData] = {}
    for v, course, staff in rows:
        patient = patients_by_id.get(v.patient_id)
        if patient is None:
            continue
        code = course.code or "M"
        key = (course.office_id, course.weekday, code)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = BoardCourseData(
                office_id=course.office_id,
                office_code=office_code_by_id.get(course.office_id),
                weekday=course.weekday,
                course_code=code,
                course_id=course.id,
                staff_name=staff.name if staff is not None else None,
            )
            buckets[key] = bucket
        start_min = _time_to_min(v.start_time)
        end_min = _time_to_min(v.end_time)
        duration = end_min - start_min if end_min > start_min else 0
        bucket.visits.append(
            BoardVisitData(
                visit_id=v.id,
                patient_id=patient.id,
                patient_name=patient.name,
                patient_kana=patient.kana,
                insurance=_normalize_insurance(patient.insurance),
                service_minutes=duration,
                start_time=v.start_time,
                end_time=v.end_time,
                address=patient.address,
                lat=float(patient.lat) if patient.lat is not None else None,
                lng=float(patient.lng) if patient.lng is not None else None,
                mode="normal" if v.type == "regular" else "special",
                status=v.status,
            )
        )

    # 各コース内を start 昇順に整列 + slot_index を付与.
    # cancelled は planned の後ろに寄せる (0=planned, 1=cancelled).
    for bucket in buckets.values():
        bucket.visits.sort(
            key=lambda x: (
                0 if x.status == VISIT_STATUS_PLANNED else 1,
                _time_to_min(x.start_time),
                str(x.visit_id),
            )
        )
        for idx, bv in enumerate(bucket.visits):
            bv.slot_index = idx

    # 同住所 group_id を (office, weekday) 単位で算出して各 visit に付与する.
    _assign_same_address_groups(buckets)

    return buckets, office_name_by_id, office_code_by_id


def _assign_same_address_groups(
    buckets: dict[tuple[UUID, int, str], BoardCourseData],
) -> None:
    """同 (office, weekday, address_bucket) で 2 名以上の visit に group_id を付与.

    ``schedule_v2._assign_same_address_groups`` と同等のロジック
    (``_address_bucket`` で 100m 角に丸め, 2 名以上で group_id 採番). ボードは
    コース越境を許容して (office, weekday) 単位で判定する (= 同住所で別コースに
    分かれていても連結対象). group_id 形式は schedule_v2 と揃える.
    """
    bucket_to_visits: dict[tuple[UUID, int, tuple[float, float]], list[BoardVisitData]] = (
        defaultdict(list)
    )
    for course in buckets.values():
        for bv in course.visits:
            if bv.lat is None or bv.lng is None:
                continue
            addr_bucket = _address_bucket(bv.lat, bv.lng)
            key = (course.office_id, course.weekday, addr_bucket)
            bucket_to_visits[key].append(bv)

    for key, vs in bucket_to_visits.items():
        if len(vs) >= 2:
            office_id, wd, (lat_b, lng_b) = key
            gid = f"sa_{office_id}_{wd}_{lat_b:.4f}_{lng_b:.4f}"
            for bv in vs:
                bv.same_address_group_id = gid


async def load_weekday_staff_counts(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_ids: list[UUID],
) -> tuple[dict[tuple[UUID, int], int], dict[tuple[UUID, int], int]]:
    """(office_id, weekday) ごとの稼働 staff 数 / manager 数を返す.

    休講判定 (staff 0 = courses 空 / closed) とヘッダー集計に使う.
    ``count_active_staff_per_weekday`` / ``count_active_managers_per_weekday``
    (weekday-staff-capacity と同じ) を再利用する.
    """
    staff_counts = await count_active_staff_per_weekday(
        db, office_ids=office_ids, iso_year=iso_year, iso_week=iso_week
    )
    manager_counts = await count_active_managers_per_weekday(
        db, office_ids=office_ids, iso_year=iso_year, iso_week=iso_week
    )
    return staff_counts, manager_counts


__all__ = [
    "MAX_PATIENTS_PER_COURSE",
    "BoardCourseData",
    "BoardVisitData",
    "load_board_buckets",
    "load_weekday_staff_counts",
    "_fmt_hhmm",
    "_office_short",
]
