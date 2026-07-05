"""K-1d: csv_builder 純関数のテスト (18列生成・cp932・実データ整合)."""

from __future__ import annotations

import csv
import io
from datetime import date, time

from app.services.diff.engine import _parse_kaipoke_rows
from app.services.kaipoke.csv_builder import (
    HEADER,
    BuildOptions,
    KaipokeCsvRow,
    StaffCell,
    build_csv,
    business_type_from_insurance,
    row_to_cells,
)


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
    assert opts.default_service_content == "精神基本療養費Ⅰ・正看"
    assert opts.mentor_as_companion is True
