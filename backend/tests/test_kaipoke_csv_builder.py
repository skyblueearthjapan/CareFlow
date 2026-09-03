"""K-1d: csv_builder 純関数のテスト (18列生成・cp932・実データ整合)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, time

import pytest

from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import Visit
from app.services.diff.engine import _parse_kaipoke_rows
from app.services.kaipoke.csv_builder import (
    HEADER,
    BuildOptions,
    KaipokeCsvRow,
    StaffCell,
    build_csv,
    business_type_from_insurance,
    resolve_month_rows,
    resolve_service_content,
    row_to_cells,
)

# DB 経由テストの対象日 (2026-07-07 火)。BuildOptions(year=2026, month=7) と対応。
TUE = date(2026, 7, 7)


def _single_row() -> KaipokeCsvRow:
    # 実CSVの先頭行を再現: 宇田川　優莉(看護師) → 朝倉　美夢 / 医療保険 / 精神基本療養費Ⅰ・正看
    return KaipokeCsvRow(
        patient_name="朝倉　美夢",
        visit_date=date(2026, 7, 1),  # 水曜
        start_time=time(10, 0),
        end_time=time(10, 35),
        office_name="訪問看護ステーションよりより",
        business_type="医療保険",
        service_content="精神基本療養費Ⅰ・正看",
        primary=StaffCell(name="宇田川　優莉", qualification="看護師"),
    )


def test_header_is_18_columns() -> None:
    assert len(HEADER) == 18
    assert HEADER[0] == "職員名１"
    assert HEADER[13] == "サービス内容"


def test_row_to_cells_matches_real_format() -> None:
    cells = row_to_cells(_single_row())
    assert len(cells) == 18
    assert cells[0] == "宇田川　優莉"  # 職員名1
    assert cells[1] == "看護師"  # 職種1
    assert cells[2] == "" and cells[4] == ""  # 職員名2/同行2 空
    assert cells[8] == "訪問看護ステーションよりより"  # 事業所名
    assert cells[9] == "1"  # 日付
    assert cells[10] == "水"  # 曜日 (2026-07-01 は水)
    assert cells[11] == "朝倉　美夢"  # 利用者
    assert cells[12] == "医療保険"  # 業務種別
    assert cells[13] == "精神基本療養費Ⅰ・正看"  # サービス内容
    assert cells[14] == "10:00" and cells[15] == "10:35"  # 時間
    assert cells[16] == "35"  # 提供時間（分）
    assert cells[17] == ""  # 備考


def test_two_staff_companion_mark() -> None:
    row = _single_row()
    row.secondary = StaffCell(name="川名　千恵", qualification="看護師", companion=True)
    cells = row_to_cells(row)
    assert cells[2] == "川名　千恵"  # 職員名2
    assert cells[3] == "看護師"  # 職種2
    assert cells[4] == "○"  # 同行2


def test_business_type_from_insurance() -> None:
    assert business_type_from_insurance("medical") == "医療保険"
    assert business_type_from_insurance("care") == "介護保険"
    assert business_type_from_insurance(None) == "医療保険"  # 既定
    assert business_type_from_insurance("unknown") == "医療保険"


def test_build_csv_cp932_roundtrip() -> None:
    data = build_csv([_single_row()], encoding="cp932")
    text = data.decode("cp932")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == HEADER
    assert len(rows) == 2  # header + 1


def test_generated_csv_parses_back_via_diff_engine() -> None:
    """生成CSV → diff/engine のパーサ → ScheduleEntry で往復整合 (ゴールデン)."""
    row = _single_row()
    row.secondary = StaffCell(name="川名　千恵", qualification="看護師", companion=True)
    data = build_csv([row], encoding="utf-8-sig")
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    entries = _parse_kaipoke_rows(parsed)
    assert len(entries) == 1
    e = entries[0]
    assert e.user_name == "朝倉　美夢"
    assert e.date == "1"
    assert e.business_type == "医療保険"
    assert e.service_type == "精神基本療養費Ⅰ・正看"
    assert e.start_time == "10:00"
    assert e.end_time == "10:35"
    assert e.staff1_name == "宇田川　優莉"
    assert e.staff1_type == "看護師"
    assert e.staff2_name == "川名　千恵"


def test_build_options_defaults() -> None:
    opts = BuildOptions(year=2026, month=7)
    # 後方互換のため残っているだけのフィールド (行生成には効かない)。
    assert opts.default_service_content == "精神基本療養費Ⅰ・正看"
    assert opts.mentor_as_companion is True


# ---------------------------------------------------------------------------
# S2: resolve_service_content — 患者の訪問看護区分 × 職員1の資格
# (設計 docs/plans/kaipoke-service-content-design.md §2)
# ---------------------------------------------------------------------------


def _patient(visit_category: str = "psychiatric", override: str | None = None) -> Patient:
    return Patient(
        code="P-SC-1",
        name="サービス内容 太郎",
        visit_category=visit_category,
        kaipoke_service_content=override,
    )


def _staff(qualification: str | None) -> Staff:
    return Staff(name="テスト 職員", qualification=qualification)


def test_resolve_service_content_four_quadrants() -> None:
    """4 象限: 精神科/一般 × 正看/准看。"""
    assert (
        resolve_service_content(_patient("psychiatric"), _staff("看護師"))
        == "精神基本療養費Ⅰ・正看"
    )
    assert (
        resolve_service_content(_patient("psychiatric"), _staff("准看護師"))
        == "精神基本療養費Ⅰ・准看"
    )
    assert resolve_service_content(_patient("general"), _staff("看護師")) == "基本療養費Ⅰ・正看"
    assert resolve_service_content(_patient("general"), _staff("准看護師")) == "基本療養費Ⅰ・准看"


def test_resolve_service_content_override_wins() -> None:
    """患者上書きがあれば分岐を完全に無視する (例外運用)。"""
    patient = _patient("general", override="精神基本療養費Ⅲ・准看")
    assert resolve_service_content(patient, _staff("看護師")) == "精神基本療養費Ⅲ・准看"
    assert resolve_service_content(patient, None) == "精神基本療養費Ⅲ・准看"


def test_resolve_service_content_unassigned_is_nurse_grade() -> None:
    """職員1 未割当 ('-' 行) は患者ベース + 正看。"""
    assert resolve_service_content(_patient("psychiatric"), None) == "精神基本療養費Ⅰ・正看"
    assert resolve_service_content(_patient("general"), None) == "基本療養費Ⅰ・正看"


def test_resolve_service_content_non_nurse_qualifications_are_nurse_grade() -> None:
    """准看護師以外 (PT/OT/ST・未設定) は「正看」に寄せる (§1-2 今回は対象外)。"""
    for qualification in ("理学療法士", "作業療法士", "言語聴覚士", None):
        assert (
            resolve_service_content(_patient("psychiatric"), _staff(qualification))
            == "精神基本療養費Ⅰ・正看"
        )


def test_resolve_service_content_defaults_to_psychiatric() -> None:
    """visit_category 未設定 (旧データ) は精神科扱い (既定)。"""
    patient = Patient(code="P-SC-2", name="旧データ")
    assert resolve_service_content(patient, _staff("看護師")) == "精神基本療養費Ⅰ・正看"


def test_resolve_service_content_visit_override_beats_everything() -> None:
    """訪問上書き (mig 0078) が最優先 — 患者上書きも区分×資格も無視する。"""
    visit = Visit(kaipoke_service_override="基本療養費Ⅰ・准看")
    assert (
        resolve_service_content(_patient("psychiatric"), _staff("看護師"), visit)
        == "基本療養費Ⅰ・准看"
    )
    with_patient_override = _patient("general", override="精神基本療養費Ⅲ・正看")
    assert (
        resolve_service_content(with_patient_override, _staff("准看護師"), visit)
        == "基本療養費Ⅰ・准看"
    )


def test_resolve_service_content_empty_visit_override_falls_through() -> None:
    """訪問上書きが None / 空なら従来どおり (患者上書き > 区分×資格)。"""
    for override in (None, ""):
        visit = Visit(kaipoke_service_override=override)
        assert (
            resolve_service_content(_patient("general"), _staff("准看護師"), visit)
            == "基本療養費Ⅰ・准看"
        )
    # visit を渡さない旧呼び出しも従来どおり動く (後方互換)。
    assert resolve_service_content(_patient("general"), _staff("准看護師")) == "基本療養費Ⅰ・准看"


# ---------------------------------------------------------------------------
# S2: CSV 行生成への結線 (DB 経由) — 職員1 基準であることを実データ経路で固定する
# ---------------------------------------------------------------------------


async def _seed_office(db, name: str = "稲毛") -> Office:
    o = Office(name=name)
    db.add(o)
    await db.flush()
    return o


async def _seed_staff(db, name: str, office: Office, *, qualification: str = "看護師") -> Staff:
    s = Staff(
        name=name,
        role="staff",
        primary_office_id=office.id,
        qualification=qualification,
    )
    db.add(s)
    await db.flush()
    return s


async def _seed_patient(
    db, name: str, office: Office, *, visit_category: str = "psychiatric"
) -> Patient:
    p = Patient(
        code=f"P-{uuid.uuid4().hex[:8]}",
        name=name,
        status="active",
        insurance="medical",
        primary_office_id=office.id,
        visit_category=visit_category,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_visit(
    db,
    patient: Patient,
    primary: Staff,
    *,
    start: time = time(10, 0),
    end: time = time(10, 35),
    secondary_staff_id=None,
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
        primary_staff_id=primary.id,
        secondary_staff_id=secondary_staff_id,
    )
    db.add(v)
    await db.flush()
    return v


@pytest.mark.asyncio
async def test_row_service_content_uses_primary_staff_qualification(db) -> None:
    """職員1 が准看なら「准看」・患者区分が一般なら基本療養費Ⅰ。"""
    office = await _seed_office(db)
    nurse = await _seed_staff(db, "看護太郎", office)
    assistant = await _seed_staff(db, "准看花子", office, qualification="准看護師")
    psychiatric = await _seed_patient(db, "精神様", office)
    general = await _seed_patient(db, "一般様", office, visit_category="general")
    await _seed_visit(db, psychiatric, assistant, start=time(9, 0), end=time(9, 35))
    await _seed_visit(db, general, nurse, start=time(11, 0), end=time(11, 35))
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    by_patient = {r.patient_name: r.service_content for r in rows}
    assert by_patient["精神様"] == "精神基本療養費Ⅰ・准看"
    assert by_patient["一般様"] == "基本療養費Ⅰ・正看"


@pytest.mark.asyncio
async def test_row_service_content_ignores_companion_qualification(db) -> None:
    """同行 (職員2) が准看でも職員1 基準 = 正看のまま (カイポケの実態どおり)。"""
    office = await _seed_office(db)
    nurse = await _seed_staff(db, "看護太郎", office)
    assistant = await _seed_staff(db, "准看花子", office, qualification="准看護師")
    patient = await _seed_patient(db, "山田様", office)
    await _seed_visit(db, patient, nurse, secondary_staff_id=assistant.id)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert len(rows) == 1
    assert rows[0].secondary is not None and rows[0].secondary.name == "准看花子"
    assert rows[0].service_content == "精神基本療養費Ⅰ・正看"


@pytest.mark.asyncio
async def test_row_service_content_unassigned_primary(db) -> None:
    """職員1 未割当 ('-' 行) は患者ベース + 正看 (include_unassigned 経路)。"""
    office = await _seed_office(db)
    patient = await _seed_patient(db, "未割当様", office, visit_category="general")
    db.add(
        Visit(
            patient_id=patient.id,
            visit_date=TUE,
            start_time=time(10, 0),
            end_time=time(10, 35),
            type="regular",
            status="planned",
            source="auto",
            required_staff_count=1,
        )
    )
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7, include_unassigned=True))
    assert len(rows) == 1
    assert rows[0].primary.name == "-"
    assert rows[0].service_content == "基本療養費Ⅰ・正看"


@pytest.mark.asyncio
async def test_row_service_content_override_wins(db) -> None:
    """患者上書きがあれば職員1 の資格を無視してそのまま出力する。"""
    office = await _seed_office(db)
    assistant = await _seed_staff(db, "准看花子", office, qualification="准看護師")
    patient = await _seed_patient(db, "例外様", office)
    patient.kaipoke_service_content = "精神基本療養費Ⅲ・正看"
    await _seed_visit(db, patient, assistant)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert rows[0].service_content == "精神基本療養費Ⅲ・正看"


@pytest.mark.asyncio
async def test_row_service_content_visit_override_wins(db) -> None:
    """訪問上書き (mig 0078) は患者上書きより強い — 1 件だけカイポケに合わせる経路。"""
    office = await _seed_office(db)
    nurse = await _seed_staff(db, "看護太郎", office)
    patient = await _seed_patient(db, "例外様", office)
    patient.kaipoke_service_content = "精神基本療養費Ⅲ・正看"
    visit = await _seed_visit(db, patient, nurse)
    visit.kaipoke_service_override = "基本療養費Ⅰ・准看"
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert [r.service_content for r in rows] == ["基本療養費Ⅰ・准看"]


# ---------------------------------------------------------------------------
# 2026-09-03 W37: 職員1 のコース担当フォールバック (安全網)
# ---------------------------------------------------------------------------


async def _seed_course(db, office: Office, staff: Staff | None, *, code: str = "A") -> Course:
    """TUE (2026-07-07 = 2026-W28 火) のコース。"""
    c = Course(
        iso_year=2026,
        iso_week=28,
        weekday=1,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(c)
    await db.flush()
    return c


async def _seed_unassigned_visit(
    db, patient: Patient, *, course: Course | None = None, manual_override: bool = False
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=TUE,
        start_time=time(10, 0),
        end_time=time(10, 35),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
        course_id=course.id if course is not None else None,
        manual_staff_override=manual_override,
    )
    db.add(v)
    await db.flush()
    return v


@pytest.mark.asyncio
async def test_row_falls_back_to_course_staff_when_primary_null(db) -> None:
    """primary_staff_id が NULL でもコース担当が居れば職員名1 はコース担当。

    2026-09-03 W37 の実害 (プール一括投入がコース担当だけ書き、既存訪問の
    primary_staff_id を NULL のまま残した → CSV が '-' で出てカイポケの担当を消した)
    の安全網。資格 (サービス内容) もコース担当基準になる。
    """
    office = await _seed_office(db)
    assistant = await _seed_staff(db, "准看花子", office, qualification="准看護師")
    course = await _seed_course(db, office, assistant)
    patient = await _seed_patient(db, "宙ぶらりん様", office)
    await _seed_unassigned_visit(db, patient, course=course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7, include_unassigned=True))
    assert len(rows) == 1
    assert rows[0].primary.name == "准看花子"
    assert rows[0].service_content == "精神基本療養費Ⅰ・准看"


@pytest.mark.asyncio
async def test_row_course_fallback_skipped_on_manual_override(db) -> None:
    """manual_staff_override=True は「この訪問だけ担当を外した」意思なのでフォールバックしない。"""
    office = await _seed_office(db)
    nurse = await _seed_staff(db, "看護太郎", office)
    course = await _seed_course(db, office, nurse)
    patient = await _seed_patient(db, "手動様", office)
    await _seed_unassigned_visit(db, patient, course=course, manual_override=True)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7, include_unassigned=True))
    assert len(rows) == 1
    assert rows[0].primary.name == "-"


@pytest.mark.asyncio
async def test_row_course_fallback_skips_inactive_staff(db) -> None:
    """コース担当が退職・削除済みならフォールバックしない ('-' のまま)。

    退職者をカイポケへ押し込まないための絞り込み (JOIN staff で status='active')。
    """
    office = await _seed_office(db)
    retired = await _seed_staff(db, "退職太郎", office)
    retired.status = "inactive"
    course = await _seed_course(db, office, retired)
    patient = await _seed_patient(db, "退職担当様", office)
    await _seed_unassigned_visit(db, patient, course=course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7, include_unassigned=True))
    assert len(rows) == 1
    assert rows[0].primary.name == "-"

    # include_unassigned=False なら従来どおり行ごと落ちる (カイポケは職員必須)。
    rows_strict = await resolve_month_rows(db, BuildOptions(year=2026, month=7))
    assert rows_strict == []


@pytest.mark.asyncio
async def test_row_unassigned_when_course_has_no_staff(db) -> None:
    """コースにも担当が居なければ従来どおり '-' (include_unassigned=True)。"""
    office = await _seed_office(db)
    course = await _seed_course(db, office, None)
    patient = await _seed_patient(db, "担当なし様", office, visit_category="general")
    await _seed_unassigned_visit(db, patient, course=course)
    await db.commit()

    rows = await resolve_month_rows(db, BuildOptions(year=2026, month=7, include_unassigned=True))
    assert len(rows) == 1
    assert rows[0].primary.name == "-"
    assert rows[0].service_content == "基本療養費Ⅰ・正看"
