"""新人同行 Phase 3 (カイポケ職員名2への同行反映) — 設計 §9 のテスト.

観点:
- csv_builder.resolve_month_rows: 同行→職員名2 / secondary優先(3人ケース職員名3) /
  同行なし=従来どおり
- diff: 同行由来 staff2 が optimized CSV に載り Correction(staff2_to) 化する
- inbound(apply_inbound_items): 同行由来 staff2 は secondary へ書かず要2名化しない
  (edit/add 経路)。非同行 staff2 は従来どおり required_staff_count=2。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.models.correction_sheet import CorrectionSheetItem
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.trainee_accompaniment import TraineeAccompaniment
from app.models.visit import Visit
from app.services.diff.engine import compare_schedules_from_content
from app.services.kaipoke.csv_builder import (
    BuildOptions,
    KaipokeCsvRow,
    StaffCell,
    build_csv,
    build_month_csv,
    resolve_month_rows,
)
from app.services.kaipoke.inbound import apply_inbound_items

JST = ZoneInfo("Asia/Tokyo")

# 対象週: 2026-07-06(月) 〜 2026-07-12(日)。火曜 = 2026-07-07 (weekday=1)。
WEEK_START = date(2026, 7, 6)
WEEK_END = date(2026, 7, 12)
TUE = date(2026, 7, 7)
MONTH = "2026-07"
SERVICE = "精神基本療養費Ⅰ・正看"


# --- seeding helpers ---------------------------------------------------------


async def _office(db, name: str = "稲毛") -> Office:
    o = Office(name=name)
    db.add(o)
    await db.flush()
    return o


async def _staff(db, name: str, office: Office, *, is_trainee: bool = False) -> Staff:
    s = Staff(name=name, role="staff", primary_office_id=office.id, is_trainee=is_trainee)
    s.qualification = "看護師"
    db.add(s)
    await db.flush()
    return s


async def _patient(db, name: str, office: Office) -> Patient:
    p = Patient(
        code=f"P-{uuid.uuid4().hex[:8]}",
        name=name,
        status="active",
        insurance="medical",
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    return p


async def _course(db, office: Office, staff: Staff, *, weekday: int, code: str) -> Course:
    iso = WEEK_START.isocalendar()
    c = Course(
        iso_year=iso[0],
        iso_week=iso[1],
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(c)
    await db.flush()
    return c


async def _visit(
    db,
    patient: Patient,
    mentor: Staff,
    *,
    course: Course | None = None,
    start: time = time(10, 0),
    end: time = time(10, 35),
    secondary_staff_id=None,
    mentor_staff_id=None,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=TUE,
        start_time=start,
        end_time=end,
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=2 if secondary_staff_id is not None else 1,
        primary_staff_id=mentor.id,
        secondary_staff_id=secondary_staff_id,
        mentor_staff_id=mentor_staff_id,
        course_id=course.id if course else None,
    )
    db.add(v)
    await db.flush()
    return v


def _link_course(db, trainee: Staff, course: Course) -> None:
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id,
            target_type="course",
            course_id=course.id,
            source="manual",
        )
    )


# --- A. csv_builder.resolve_month_rows ---------------------------------------


@pytest.mark.asyncio
async def test_accompaniment_fills_staff2(db) -> None:
    """コースリンクの同行新人が職員名2 (secondary 未設定) に載る・同行○は付かない."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    await _visit(db, patient, mentor, course=course)
    _link_course(db, trainee, course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert len(rows) == 1
    r = rows[0]
    assert r.secondary is not None
    assert r.secondary.name == "新人花子"
    assert r.secondary.companion is False  # 決定事項#4: 正規スタッフ扱い (同行○なし)。
    assert r.tertiary is None


@pytest.mark.asyncio
async def test_secondary_takes_priority_three_person(db) -> None:
    """secondary(要2名) が居れば職員名2=secondary・職員名3=同行 (3人ケース)."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    second = await _staff(db, "看護次郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    await _visit(db, patient, mentor, course=course, secondary_staff_id=second.id)
    _link_course(db, trainee, course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert len(rows) == 1
    r = rows[0]
    assert r.secondary is not None and r.secondary.name == "看護次郎"  # 職員名2
    assert r.tertiary is not None and r.tertiary.name == "新人花子"  # 職員名3=同行


@pytest.mark.asyncio
async def test_no_accompaniment_unchanged(db) -> None:
    """同行が無ければ従来どおり (secondary 未設定なら職員名2 空)."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    await _visit(db, patient, mentor, course=course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert len(rows) == 1
    assert rows[0].secondary is None
    assert rows[0].tertiary is None


# --- B. diff: 同行由来 staff2 の Correction 化 ---------------------------------


@pytest.mark.asyncio
async def test_accompaniment_creates_staff2_correction(db) -> None:
    """optimized CSV に同行 staff2 が載り、staff2 追加が edit Correction になる."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    await _visit(db, patient, mentor, course=course)
    _link_course(db, trainee, course)
    await db.commit()

    optimized = (
        await build_month_csv(db, BuildOptions(year=2026, month=7), encoding="utf-8-sig")
    ).decode("utf-8-sig")

    # カイポケ現況 (staff2 未設定) を組み立てる — キーは同一 (patient|day|保険|service)。
    current = build_csv(
        [
            KaipokeCsvRow(
                patient_name="山田様",
                visit_date=TUE,
                start_time=time(10, 0),
                end_time=time(10, 35),
                office_name=office.name,
                business_type="医療保険",
                service_content=SERVICE,
                primary=StaffCell(name="先輩太郎", qualification="看護師"),
            )
        ],
        encoding="utf-8-sig",
    ).decode("utf-8-sig")

    corrections = compare_schedules_from_content(current, optimized)
    staff2_adds = [c for c in corrections if c.staff2_to == "新人花子"]
    assert len(staff2_adds) == 1
    assert staff2_adds[0].staff2_from == ""


# --- C. inbound: 同行由来 staff2 の突合 (edit / add) --------------------------


def _now() -> datetime:
    return datetime(2026, 7, 8, 12, 0, tzinfo=JST)


def _edit_item(patient: Patient, visit: Visit, *, staff1: str, staff2: str) -> CorrectionSheetItem:
    common = {"user_name": patient.name, "date": "7", "start_time": "10:00", "end_time": "10:35"}
    return CorrectionSheetItem(
        sheet_id=uuid.uuid4(),
        patient_id=patient.id,
        visit_id=visit.id,
        action="edit",
        before={**common, "staff1": staff1, "staff2": ""},
        after={**common, "staff1": staff1, "staff2": staff2},
        include=True,
    )


@pytest.mark.asyncio
async def test_inbound_edit_accompaniment_staff2_skipped(db) -> None:
    """カイポケ staff2 が同行新人と一致 → secondary へ書かず要2名化しない (edit)."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    _link_course(db, trainee, course)
    await db.commit()

    item = _edit_item(patient, visit, staff1="先輩太郎", staff2="新人花子")
    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )
    await db.commit()
    await db.refresh(visit)

    assert visit.secondary_staff_id is None  # 同行は secondary へ書かない。
    assert visit.required_staff_count == 1  # 要2名化しない。
    assert summary.skipped == 1  # 同行以外に変更点なし → skipped。


@pytest.mark.asyncio
async def test_inbound_edit_real_staff2_applied(db) -> None:
    """同行でない実 staff2 は従来どおり secondary へ反映し required_staff_count=2."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    second = await _staff(db, "看護次郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    _link_course(db, trainee, course)  # 同行は別人 (新人花子) — 看護次郎とは一致しない。
    await db.commit()

    item = _edit_item(patient, visit, staff1="先輩太郎", staff2="看護次郎")
    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )
    await db.commit()
    await db.refresh(visit)

    assert visit.secondary_staff_id == second.id
    assert visit.required_staff_count == 2
    assert summary.updated == 1


@pytest.mark.asyncio
async def test_inbound_add_accompaniment_staff2_skipped(db) -> None:
    """add: 新設 visit が張り付くコースの同行新人と staff2 一致 → 要2名化しない."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    # 火曜に mentor のコースを用意 (add の course 解決先) + 同行リンク。
    course = await _course(db, office, mentor, weekday=1, code="A")
    _link_course(db, trainee, course)
    await db.commit()

    item = CorrectionSheetItem(
        sheet_id=uuid.uuid4(),
        patient_id=patient.id,
        action="add",
        before=None,
        after={
            "user_name": patient.name,
            "date": "7",
            "start_time": "15:00",
            "end_time": "15:35",
            "staff1": "先輩太郎",
            "staff2": "新人花子",
        },
        include=True,
    )
    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )
    await db.commit()

    from sqlalchemy import select

    new_visit = await db.scalar(
        select(Visit).where(Visit.patient_id == patient.id, Visit.start_time == time(15, 0))
    )
    assert summary.added == 1
    assert new_visit is not None
    assert new_visit.secondary_staff_id is None  # 同行は secondary へ書かない。
    assert new_visit.required_staff_count == 1


@pytest.mark.asyncio
async def test_inbound_add_real_staff2_promotes(db) -> None:
    """add: 同行でない実 staff2 は従来どおり required_staff_count=2 に昇格."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    second = await _staff(db, "看護次郎", office)
    patient = await _patient(db, "山田様", office)
    await _course(db, office, mentor, weekday=1, code="A")  # 同行リンク無し。
    await db.commit()

    item = CorrectionSheetItem(
        sheet_id=uuid.uuid4(),
        patient_id=patient.id,
        action="add",
        before=None,
        after={
            "user_name": patient.name,
            "date": "7",
            "start_time": "15:00",
            "end_time": "15:35",
            "staff1": "先輩太郎",
            "staff2": "看護次郎",
        },
        include=True,
    )
    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )
    await db.commit()

    from sqlalchemy import select

    new_visit = await db.scalar(
        select(Visit).where(Visit.patient_id == patient.id, Visit.start_time == time(15, 0))
    )
    assert summary.added == 1
    assert new_visit is not None
    assert new_visit.secondary_staff_id == second.id
    assert new_visit.required_staff_count == 2


# --- C-2. 遷移・境界の回帰 (Phase 3 レビュー NIT-3 / MINOR-2) ------------------


@pytest.mark.asyncio
async def test_inbound_edit_trainee_staff2_auto_accompaniment(db) -> None:
    """案B①: リンク未作成でも新人 staff2 は同行リンクを自動作成し要2名化しない (edit).

    旧仕様 (Phase 3 初版) では未リンク新人の staff2 は secondary へ昇格していたが、
    盤面に2人目コース列の無い不整合を生む (PO指摘) ため案B (PO承認 2026-07-12) で
    「未リンク新人は同行リンクを自動作成・要2名化しない」へ転換した。他に変更点が
    無くてもリンク作成を実変更とみなし updated として集計する。
    """
    from sqlalchemy import select

    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    # リンクを張らない (= 未作成状態)。案B ではここで自動作成される。
    await db.commit()

    item = _edit_item(patient, visit, staff1="先輩太郎", staff2="新人花子")
    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )
    await db.commit()
    await db.refresh(visit)

    assert visit.secondary_staff_id is None  # 同行は secondary へ書かない。
    assert visit.required_staff_count == 1  # 要2名化しない。
    assert summary.updated == 1  # リンク作成を実変更として updated 集計。

    link = await db.scalar(
        select(TraineeAccompaniment).where(
            TraineeAccompaniment.trainee_staff_id == trainee.id,
            TraineeAccompaniment.visit_id == visit.id,
        )
    )
    assert link is not None
    assert link.target_type == "visit"
    assert link.source == "manual"  # 'inbound' 値は追加しない (CHECK 制約 migration 回避)。


@pytest.mark.asyncio
async def test_inbound_edit_staff2_clear_unchanged(db) -> None:
    """staff2 クリア (空文字) の既存挙動が Phase 3 で壊れていない (NIT-3 c)."""
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    second = await _staff(db, "看護次郎", office)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course, secondary_staff_id=second.id)
    await db.commit()

    common = {"user_name": patient.name, "date": "7", "start_time": "10:00", "end_time": "10:35"}
    item = CorrectionSheetItem(
        sheet_id=uuid.uuid4(),
        patient_id=patient.id,
        visit_id=visit.id,
        action="edit",
        before={**common, "staff1": "先輩太郎", "staff2": "看護次郎"},
        after={**common, "staff1": "先輩太郎", "staff2": ""},
        include=True,
    )
    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )
    await db.commit()
    await db.refresh(visit)

    assert visit.secondary_staff_id is None
    assert visit.required_staff_count == 1
    assert summary.updated == 1


@pytest.mark.asyncio
async def test_inbound_edit_multiple_trainees_membership(db) -> None:
    """1コースに複数新人リンクがあっても membership 判定で両名とも同行由来 (MINOR-2).

    単一値 last-wins だと片方の新人が「要2名の正規2人目」として誤って書き戻される
    非決定性があった — 集合判定でどちらの新人が staff2 で来てもスキップされること。
    """
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee_a = await _staff(db, "新人花子", office, is_trainee=True)
    trainee_b = await _staff(db, "新人月子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    _link_course(db, trainee_a, course)
    _link_course(db, trainee_b, course)
    await db.commit()

    for trainee_name in ("新人花子", "新人月子"):
        item = _edit_item(patient, visit, staff1="先輩太郎", staff2=trainee_name)
        summary = await apply_inbound_items(
            db,
            items=[item],
            week_start=WEEK_START,
            week_end=WEEK_END,
            days=None,
            dry_run=False,
            now=_now(),
        )
        await db.commit()
        await db.refresh(visit)
        assert visit.secondary_staff_id is None, trainee_name
        assert visit.required_staff_count == 1, trainee_name
        assert summary.skipped == 1, trainee_name


# --- D. 案B: 未リンク新人 staff2 の自動同行化 (edit / add / dry_run / 冪等) --------


def _add_item(
    patient: Patient, *, staff1: str, staff2: str, start: str = "15:00"
) -> CorrectionSheetItem:
    return CorrectionSheetItem(
        sheet_id=uuid.uuid4(),
        patient_id=patient.id,
        action="add",
        before=None,
        after={
            "user_name": patient.name,
            "date": "7",
            "start_time": start,
            "end_time": "15:35",
            "staff1": staff1,
            "staff2": staff2,
        },
        include=True,
    )


@pytest.mark.asyncio
async def test_inbound_add_trainee_staff2_auto_accompaniment(db) -> None:
    """案B②: add で未リンク新人 staff2 → visit 新設 + 同行リンク自動作成 (要2名化しない)."""
    from sqlalchemy import select

    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    await _course(db, office, mentor, weekday=1, code="A")  # 同行リンクは張らない。
    await db.commit()

    item = _add_item(patient, staff1="先輩太郎", staff2="新人花子")
    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )
    await db.commit()

    new_visit = await db.scalar(
        select(Visit).where(Visit.patient_id == patient.id, Visit.start_time == time(15, 0))
    )
    assert summary.added == 1
    assert new_visit is not None
    assert new_visit.secondary_staff_id is None  # 同行は secondary へ書かない。
    assert new_visit.required_staff_count == 1  # 要2名化しない。

    link = await db.scalar(
        select(TraineeAccompaniment).where(
            TraineeAccompaniment.trainee_staff_id == trainee.id,
            TraineeAccompaniment.visit_id == new_visit.id,
        )
    )
    assert link is not None
    assert link.target_type == "visit"
    assert link.source == "manual"


@pytest.mark.asyncio
async def test_inbound_dry_run_no_accompaniment_created(db) -> None:
    """案B: dry_run では同行リンクを作成しない (edit / add とも)."""
    from sqlalchemy import func, select

    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    patient2 = await _patient(db, "佐藤様", office)
    await db.commit()

    edit = _edit_item(patient, visit, staff1="先輩太郎", staff2="新人花子")
    add = _add_item(patient2, staff1="先輩太郎", staff2="新人花子")
    summary = await apply_inbound_items(
        db,
        items=[edit, add],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=True,
        now=_now(),
    )
    await db.commit()

    count = await db.scalar(select(func.count()).select_from(TraineeAccompaniment))
    assert count == 0  # dry_run では一切書き込まない。
    # dry_run でも判定は実書き込み時と同じ (edit=updated / add=added)。
    assert summary.updated == 1
    assert summary.added == 1


@pytest.mark.asyncio
async def test_inbound_edit_trainee_staff2_idempotent_reapply(db) -> None:
    """案B: 同じ item を再 apply しても 2 回目は判定①でスキップ・リンクは 1 件のまま."""
    from sqlalchemy import func, select

    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    await db.commit()

    async def _apply() -> object:
        item = _edit_item(patient, visit, staff1="先輩太郎", staff2="新人花子")
        s = await apply_inbound_items(
            db,
            items=[item],
            week_start=WEEK_START,
            week_end=WEEK_END,
            days=None,
            dry_run=False,
            now=_now(),
        )
        await db.commit()
        return s

    first = await _apply()
    assert first.updated == 1  # 1 回目: リンク作成 → updated。

    second = await _apply()
    assert second.skipped == 1  # 2 回目: 既存リンクと一致 → 判定① スキップ。

    count = await db.scalar(
        select(func.count())
        .select_from(TraineeAccompaniment)
        .where(
            TraineeAccompaniment.trainee_staff_id == trainee.id,
            TraineeAccompaniment.visit_id == visit.id,
        )
    )
    assert count == 1  # 冪等: リンクは 1 件のまま。


# --- E. 穴②: 担当1 (staff1) が新人のときの反映抑止 (設計 §8) -------------------


@pytest.mark.asyncio
async def test_inbound_edit_staff1_trainee_applied_with_warning(db) -> None:
    """方針転換 (PO確定 2026-07-26): 担当1が新人へ変更 → 適用する + ⚠警告注記.

    カイポケは最終的な「正」のため、新人が担当1の現実も取り込む。⚠警告で
    新人フラグ見直しの判断材料にする。らく助発の割当経路の封鎖は不変。
    """
    office = await _office(db)
    mentor = await _staff(db, "先輩太郎", office)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    course = await _course(db, office, mentor, weekday=1, code="A")
    visit = await _visit(db, patient, mentor, course=course)
    await db.commit()
    mentor_id = mentor.id
    trainee_id = trainee.id

    common = {"user_name": patient.name, "date": "7", "start_time": "10:00", "end_time": "10:35"}
    item = CorrectionSheetItem(
        sheet_id=uuid.uuid4(),
        patient_id=patient.id,
        visit_id=visit.id,
        action="edit",
        before={**common, "staff1": "先輩太郎", "staff2": ""},
        after={**common, "staff1": "新人花子", "staff2": ""},  # 担当1を新人へ変更。
        include=True,
    )
    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )
    await db.commit()
    await db.refresh(visit)

    # 担当1変更は適用される (カイポケの現実を受け入れる) + ⚠警告注記。
    assert visit.primary_staff_id == trainee_id
    assert mentor_id is not None  # (旧担当の存在確認のみ)
    assert summary.updated == 1
    assert "⚠" in summary.results[0].detail
    assert "新人" in summary.results[0].detail


@pytest.mark.asyncio
async def test_inbound_add_staff1_trainee_added_with_warning(db) -> None:
    """方針転換 (PO確定 2026-07-26): 担当1が新人の追加予定も取り込む + ⚠警告注記.

    コースは新人がその日持たないため臨時コースへ配置される。
    """
    from sqlalchemy import select

    office = await _office(db)
    trainee = await _staff(db, "新人花子", office, is_trainee=True)
    patient = await _patient(db, "山田様", office)
    await db.commit()
    trainee_id = trainee.id

    item = _add_item(patient, staff1="新人花子", staff2="")
    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=WEEK_START,
        week_end=WEEK_END,
        days=None,
        dry_run=False,
        now=_now(),
    )
    await db.commit()

    # visit が作られ、新人が primary・臨時コースへ配置される。
    visit = await db.scalar(select(Visit).where(Visit.patient_id == patient.id))
    assert visit is not None
    assert visit.primary_staff_id == trainee_id
    assert visit.course_id is not None
    assert summary.added == 1
    assert summary.failed == 0
    assert "⚠" in summary.results[0].detail
    assert "新人" in summary.results[0].detail
