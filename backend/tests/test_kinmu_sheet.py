"""休みの段 #11: 勤務表の生成 (純粋な関数・DB からの材料・シートの形).

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §7-1・§11-1「勤務表」。

10 月の正解 (``tests/fixtures/kinmu_sheet``) はお客様の勤務表の見本 (2026.10 と 9/27〜9/30)
から職員名を「職員A〜G」に置き換えて作った。見本の値のうち、設計の規則と違う所は規則を正とし、
``expected.json`` の ``corrections`` と ``test_october_matches_sample`` のコメントに書いた。
正解の作り直し: 見本 (git 管理外) を openpyxl で読むスクリプト (リポジトリの外・make_fixture.py) に
見本のパスと ``backend/tests/fixtures/kinmu_sheet`` を渡して materials.json・expected.json を書き直す。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (
    KinmuSheet,
    LeaveMonth,
    Office,
    OfficeClosedDay,
    Patient,
    Staff,
    StaffShift,
    StaffWeeklyOverride,
    Visit,
)
from app.models.staff import StaffSecondaryOffice
from app.services.google_drive import Formula
from app.services.google_drive.sheets import CellRange
from app.services.kinmu_sheet import (
    DayOverride,
    KinmuRules,
    KinmuStaffInput,
    build_holiday_summary,
    build_kinmu_month,
    load_holiday_counts_by_month,
    load_kinmu_inputs,
    plan_from_snapshot,
    snapshot_plan,
    summary_to_sheet_data,
    to_sheet_data,
)
from app.services.kinmu_sheet.build import fte, holiday_marks, week_bands
from app.services.kinmu_sheet.sheet import col_letter
from app.services.leave_calendar import HolidayCount, iso_key

FIXTURES = Path(__file__).parent / "fixtures" / "kinmu_sheet"

MON_FRI = (0, 1, 2, 3, 4)


def t(hhmm: str) -> time:
    hh, mm = hhmm.split(":")
    return time(int(hh), int(mm))


def staff_input(
    key: str = "S",
    *,
    start: str = "09:00",
    end: str = "18:00",
    weekdays: tuple[int, ...] = MON_FRI,
    overrides: dict[date, DayOverride] | None = None,
    actual: dict[date, DayOverride] | None = None,
    admin: float | None = None,
    title: str | None = None,
    full_time: bool = True,
) -> KinmuStaffInput:
    return KinmuStaffInput(
        key=key,
        name=f"職員{key}",
        job="看護師",
        title=title,
        work_form="A" if full_time else "C",
        office_label="拠点1",
        full_time=full_time,
        admin_hours_per_day=admin,
        shifts={wd: (t(start), t(end)) for wd in weekdays},
        overrides=overrides or {},
        actual_overrides=actual,
    )


def off(*days: date, kind: str = "off") -> dict[date, DayOverride]:
    return {d: DayOverride(kind) for d in days}


OCT = date(2026, 10, 1)


def day_index(d: date) -> int:
    return d.day - 1


# ---------------------------------------------------------------------------
# 10 月の正解 (見本)
# ---------------------------------------------------------------------------


def _load_october() -> tuple[list[KinmuStaffInput], dict, float]:
    materials = json.loads((FIXTURES / "materials.json").read_text(encoding="utf-8"))
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    inputs = []
    for s in materials["staff"]:
        shift = (t(s["shift"]["start"]), t(s["shift"]["end"]))
        overrides = {
            date.fromisoformat(d): DayOverride(
                o["type"],
                t(o["start"]) if "start" in o else None,
                t(o["end"]) if "end" in o else None,
            )
            for d, o in s["overrides"].items()
        }
        inputs.append(
            KinmuStaffInput(
                key=s["key"],
                name=s["name"],
                job=s["job"],
                title=s["title"],
                work_form=s["work_form"],
                office_label=s["office_label"],
                full_time=s["full_time"],
                admin_hours_per_day=s["admin_hours_per_day"],
                shifts={wd: shift for wd in s["shift"]["weekdays"]},
                overrides=overrides,
            )
        )
    return inputs, expected, materials["required_hours"]


def test_october_matches_sample() -> None:
    """見本の 2026 年 10 月 (9/27〜9/30 の休みを含む) と同じ値になる.

    見本と違う所 (規則を正とした・expected.json の corrections と同じ):
    1. 職員A 10/12・10/17 の番号: 見本は 祝1・2。週 (10/11〜10/17) の中で日付の順に 1・2、
       3 つ目が祝n なので、規則では 10/12 = 2・10/17 = 祝1。
    2. 職員A 10/26: 見本は手で入れた例外 (予定 8.0・管理時間 0)。らく助は作らない (設計 §7-1)
       ので、ほかの日と同じ 予定 4.0 (勤務 8.0 − 管理 4.0)・管理時間 4.0。
    3. 職員E 10/6・10/21 の午後休: 見本は文字「PM休」で合計に入らない。らく助は始業〜12:30 の
       時間を入れる (PO 決定 10-20)。型を 8:30〜17:30 にして 4.0。
    4. 合計の予定: 見本の式 (AJ30) は職員E の行が抜けている。らく助は全員の行を足す。
    5. 常勤換算: 見本の式は割るだけ。様式の注記どおり小数第 2 位で切り捨てる。
    見本の 8.0 (固定値) は型 9:00〜18:00 − 休憩 60 分で合わせ、土日に勤務した日は時間変更
    (custom_time 9:00〜18:00)、平日の「休」は公休の登録として入力にした。
    """
    inputs, expected, required = _load_october()
    km = build_kinmu_month(OCT, inputs, required)

    assert len(expected["corrections"]) == 6
    assert [r.staff.key for r in km.staff] == [s["key"] for s in expected["staff"]]
    for row, exp in zip(km.staff, expected["staff"], strict=True):
        assert row.plan == exp["plan"], row.staff.key
        assert row.marks == exp["marks"], row.staff.key
        assert row.admin == exp["admin"], row.staff.key
        assert row.plan_total == exp["plan_total"], row.staff.key
        assert row.admin_total == exp["admin_total"], row.staff.key
        assert row.holidays == HolidayCount(exp["holidays"], 0), row.staff.key
        # 予定版なので実績の行は空
        assert row.actual is None and row.actual_total is None
    assert km.plan_day_totals == expected["day_totals"]
    assert km.plan_total == expected["grand_total"] == 1124.0
    assert km.required_hours == 160.0

    # 常勤換算 (小数第 2 位で切り捨て)。職員D・E は 168 ÷ 160 = 1.05 → 1.0。
    ftes = {r.staff.key: r.plan_fte for r in km.staff}
    assert ftes == {"A": 0.5, "B": 1.1, "C": 1.1, "D": 1.0, "E": 1.0, "F": 1.1, "G": 1.1}
    assert km.staff[0].admin_fte == 0.5  # 管理時間 84 ÷ 160 = 0.525 → 0.5
    assert km.plan_fte == 7.0  # 1124 ÷ 160 = 7.025 → 7.0

    # 週の帯 (10 月: 第1週 1〜3 日・第2週 4〜10 日 … 第5週 25〜31 日)
    assert [(w.label, w.first.day, w.last.day) for w in km.weeks] == [
        ("第1週", 1, 3),
        ("第2週", 4, 10),
        ("第3週", 11, 17),
        ("第4週", 18, 24),
        ("第5週", 25, 31),
    ]
    # 10/12 は祝日 (見出しに名前)。祝日に休みの登録が無い人は出勤 (職員B は 8.0)。
    assert km.days[11].holiday is not None
    assert km.staff[1].plan[11] == 8.0


def _cell_ref(ref: str) -> tuple[int, int]:
    ref = ref.replace("$", "")
    letters = "".join(ch for ch in ref if ch.isalpha())
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return int(ref[len(letters) :]) - 1, col - 1


def _evaluate(rows: list[list], value):
    """試験用の小さな式の計算 (=SUM の範囲と並び・=ROUNDDOWN(x/y,1) だけ)."""
    if not isinstance(value, Formula):
        return value

    def get(ref: str):
        r, c = _cell_ref(ref)
        v = rows[r][c] if r < len(rows) and c < len(rows[r]) else None
        return _evaluate(rows, v)

    body = value.expr[1:]
    if body.startswith("SUM(") and body.endswith(")"):
        total = 0.0
        for part in body[4:-1].split(","):
            if ":" in part:
                (r0, c0), (r1, c1) = (_cell_ref(x) for x in part.split(":"))
                cells = [
                    get(f"{col_letter(c)}{r + 1}")
                    for r in range(r0, r1 + 1)
                    for c in range(c0, c1 + 1)
                ]
            else:
                cells = [get(part)]
            total += sum(v for v in cells if isinstance(v, int | float))
        return round(total, 2)
    if body.startswith("ROUNDDOWN(") and body.endswith(",1)"):
        num, den = body[len("ROUNDDOWN(") : -3].split("/")
        return fte(get(num), get(den))
    raise AssertionError(f"知らない式: {value}")


def test_october_sheet_layout_matches_template() -> None:
    """シートの並びが見本 (2026.10) と同じ行・列に来る。合計・常勤換算は式で、値は計算と同じ."""
    inputs, _, required = _load_october()
    km = build_kinmu_month(OCT, inputs, required)
    sheets = to_sheet_data(km)
    assert [s.title for s in sheets] == ["勤務表"]
    v = sheets[0].rows
    # 列: E=1 日 (4)・AJ=暦月の合計 (35)・AK=常勤換算 (36)・AL=備考 (37)
    assert v[0][0] == "　10月"
    assert v[0][26] == "常勤の従業者が当該月勤務すべき時間数（合計）"
    assert v[0][36] == 160.0 and v[0][37] == "時間"
    assert v[1][0] == "従業者の勤務の体制及び勤務形態一覧表"
    assert v[1][13] == "（2026 年　10 月分 ）"
    assert v[3][1] == "勤務形態" and v[3][4] == "第1週" and v[3][7] == "第2週"
    assert v[3][35] == "暦月の合計" and v[3][36] == "常勤換算後の人数"
    assert v[5][4] == 1 and v[5][34] == 31
    assert v[6][4] == "木" and v[6][34] == "土"
    # 職員A (管理者): 8〜11 行目 = 予定・管理時間・実績・番号
    assert v[7][:4] == ["管理責任者", "A", "稲毛・都賀", "予定"]
    assert v[7][4] == 4.0 and v[7][6] == "休"
    assert v[7][35] == Formula("=SUM(E8:AI8)")
    assert v[7][36] == Formula("=ROUNDDOWN(AJ8/$AK$1,1)")
    assert v[8][0] == "看護師" and v[8][2] == "職員A" and v[8][3] == "管理時間"
    assert v[8][35] == Formula("=SUM(E9:AI9)")
    assert v[9][3] == "実績" and v[9][4] is None and v[9][35] is None
    assert v[10][6] == 2 and v[10][7] == 1  # 10/3 = 2・10/4 = 1 (数で書く)
    # 職員B: 12〜14 行目 (役職があるので 1 行目は「課長」)
    assert v[11][:4] == ["課長", "A", "稲毛", "予定"]
    assert v[12][:4] == ["看護師", None, "職員B", "実績"]
    # 職員C: 役職が無いので 1 行目は職種
    assert v[14][0] == "看護師" and v[15][2] == "職員C"
    # 職員E の番号の行: 半休は「0,5」
    assert v[22][9] == "0,5"
    # 合計: 30・31 行目。対象の行 (全員の予定) を並べた SUM
    assert v[29][0] == "合計" and v[29][3] == "予定"
    assert v[29][4] == Formula("=SUM(E8,E12,E15,E18,E21,E24,E27)")
    assert v[29][35] == Formula("=SUM(AJ8,AJ12,AJ15,AJ18,AJ21,AJ24,AJ27)")
    assert v[29][36] == Formula("=ROUNDDOWN(AJ30/$AK$1,1)")
    assert v[30][3] == "実績" and v[30][4] is None
    # 注記: 見本と同じ 34〜36 行目
    assert v[33][2].startswith("勤務形態の区分")
    assert v[34][0].startswith("　　　㊟")
    assert "小数点以下第２位を切り捨て" in v[35][0]

    # 式の値 = Python の計算 (KinmuMonth)
    for row, r in zip(km.staff, (7, 11, 14, 17, 20, 23, 26), strict=True):
        assert _evaluate(v, v[r][35]) == row.plan_total
        assert _evaluate(v, v[r][36]) == row.plan_fte
    assert _evaluate(v, v[8][35]) == km.staff[0].admin_total
    assert _evaluate(v, v[8][36]) == km.staff[0].admin_fte
    assert [_evaluate(v, v[29][4 + i]) for i in range(31)] == km.plan_day_totals
    assert _evaluate(v, v[29][35]) == km.plan_total == 1124.0
    assert _evaluate(v, v[29][36]) == km.plan_fte == 7.0

    sheet = sheets[0]
    merges = sheet.merges
    assert CellRange(3, 4, 4, 7) in merges  # 第1週 E4:G4
    assert CellRange(7, 1, 11, 2) in merges  # 職員A の勤務形態 B8:B11
    assert CellRange(8, 0, 11, 1) in merges and CellRange(8, 2, 11, 3) in merges  # A9:A11・C9:C11
    assert CellRange(12, 0, 14, 1) in merges  # 職員B A13:A14
    assert CellRange(7, 37, 31, 38) in merges  # 備考の列
    assert sheet.frozen_rows == 7 and sheet.frozen_cols == 4
    assert sheet.column_widths[4] == 36 and sheet.column_widths[2] == 100
    hour_rows = {f.range.start_row for f in sheet.formats if f.format.number_format == "0.0"}
    assert {7, 8, 11, 29} <= hour_rows
    assert 10 not in hour_rows  # 番号の行には数の書式をかけない (1 が 1.0 にならない)
    assert any(f.borders and f.range == CellRange(3, 0, 31, 38) for f in sheet.formats)
    assert any(f.format.background and f.range == CellRange(3, 0, 7, 38) for f in sheet.formats)


# ---------------------------------------------------------------------------
# 休みの番号
# ---------------------------------------------------------------------------


def test_marks_third_off_in_week_is_shuku_and_counts_through_month() -> None:
    s = staff_input(
        overrides=off(
            date(2026, 10, 6),  # 火 (週 10/4〜10/10: 日 4 が 1・火 6 が 2)
            date(2026, 10, 7),  # 水 → 3 つ目 = 祝1
            date(2026, 10, 13),  # 火 (週 10/11〜: 日 11 が 1・火 13 が 2)
            date(2026, 10, 14),  # 水 → 祝2 (月の通し番号)
        )
    )
    marks = holiday_marks(s.shifts, s.overrides, OCT)
    assert marks[day_index(date(2026, 10, 4))] == "1"
    assert marks[day_index(date(2026, 10, 6))] == "2"
    assert marks[day_index(date(2026, 10, 7))] == "祝1"
    assert marks[day_index(date(2026, 10, 10))] == "祝2"  # 土 (型の休み) は 4 つ目
    assert marks[day_index(date(2026, 10, 11))] == "1"
    assert marks[day_index(date(2026, 10, 13))] == "2"
    assert marks[day_index(date(2026, 10, 14))] == "祝3"
    assert marks[day_index(date(2026, 10, 17))] == "祝4"
    assert marks[day_index(date(2026, 10, 5))] is None  # 出勤の日


def test_marks_holiday_week_follows_date_order() -> None:
    """祝日の週も日付の順 (祝日だから祝n になるわけではない)."""
    s = staff_input(overrides=off(date(2026, 10, 12)))  # 月・スポーツの日
    marks = holiday_marks(s.shifts, s.overrides, OCT)
    assert marks[day_index(date(2026, 10, 11))] == "1"
    assert marks[day_index(date(2026, 10, 12))] == "2"
    assert marks[day_index(date(2026, 10, 17))] == "祝1"
    # 祝日に休みの登録が無ければ番号は無い (出勤)
    s2 = staff_input()
    assert holiday_marks(s2.shifts, s2.overrides, OCT)[day_index(date(2026, 10, 12))] is None


def test_marks_week_crossing_month_counts_previous_month_days() -> None:
    """週 9/27〜10/3: 9/27 (日・型の休み)・9/28 (月・公休) を数えるので 10/3 (土) は 祝1."""
    s = staff_input(overrides=off(date(2026, 9, 28)))
    marks = holiday_marks(s.shifts, s.overrides, OCT)
    assert marks[day_index(date(2026, 10, 3))] == "祝1"
    assert marks[day_index(date(2026, 10, 4))] == "1"
    # 前の月の休みが 1 つだけなら 2
    s2 = staff_input()
    assert holiday_marks(s2.shifts, s2.overrides, OCT)[day_index(date(2026, 10, 3))] == "2"
    # 前の月の日で 3 つ目以降になっても、その月の 祝n は 1 から
    s3 = staff_input(overrides=off(date(2026, 9, 28), date(2026, 9, 29), date(2026, 10, 1)))
    m3 = holiday_marks(s3.shifts, s3.overrides, OCT)
    assert m3[0] == "祝1" and m3[day_index(date(2026, 10, 3))] == "祝2"


def test_half_day_and_paid_do_not_take_numbers() -> None:
    s = staff_input(
        overrides={
            date(2026, 10, 5): DayOverride("am_off"),
            date(2026, 10, 6): DayOverride("pm_off"),
            date(2026, 10, 7): DayOverride("paid_off"),
            date(2026, 10, 8): DayOverride("off"),
        }
    )
    km = build_kinmu_month(OCT, [s], 160)
    row = km.staff[0]
    i = day_index
    assert row.marks[i(date(2026, 10, 4))] == "1"
    assert row.marks[i(date(2026, 10, 5))] == "0,5"
    assert row.marks[i(date(2026, 10, 6))] == "0,5"
    assert row.marks[i(date(2026, 10, 7))] == "有給"
    assert row.marks[i(date(2026, 10, 8))] == "2"
    assert row.marks[i(date(2026, 10, 10))] == "祝1"
    # 有給の日は「休」・時間に入れない。休日数は数えず、有給の日数で数える。
    assert row.plan[i(date(2026, 10, 7))] == "休"
    # 型の休み 9 ＋ 公休 1 ＋ 半休 0.5×2 = 11.0・有給 1
    assert row.holidays == HolidayCount(11.0, 1)


# ---------------------------------------------------------------------------
# 勤務時間
# ---------------------------------------------------------------------------


def test_half_day_hours_from_shift_without_break() -> None:
    a = staff_input(
        "A",
        overrides={
            date(2026, 10, 5): DayOverride("am_off"),
            date(2026, 10, 6): DayOverride("pm_off"),
        },
    )
    b = staff_input(
        "B", start="08:30", end="17:30", overrides={date(2026, 10, 6): DayOverride("pm_off")}
    )
    km = build_kinmu_month(OCT, [a, b], 160)
    i = day_index
    assert km.staff[0].plan[i(date(2026, 10, 5))] == 5.0  # 午前休 13:00〜18:00
    assert km.staff[0].plan[i(date(2026, 10, 6))] == 3.5  # 午後休 9:00〜12:30
    assert km.staff[1].plan[i(date(2026, 10, 6))] == 4.0  # 午後休 8:30〜12:30
    assert km.staff[1].plan[i(date(2026, 10, 7))] == 8.0  # 8:30〜17:30 − 休憩


def test_break_only_when_longer_than_six_hours() -> None:
    six = staff_input("A", start="09:00", end="15:00")
    over = staff_input("B", start="09:00", end="15:30")
    custom = staff_input(
        "C", overrides={date(2026, 10, 5): DayOverride("custom_time", t("10:00"), t("14:00"))}
    )
    km = build_kinmu_month(OCT, [six, over, custom], 160)
    i = day_index(date(2026, 10, 5))
    assert km.staff[0].plan[i] == 6.0  # ちょうど 6 時間は休憩なし
    assert km.staff[1].plan[i] == 5.5  # 6.5 時間 − 60 分
    assert km.staff[2].plan[i] == 4.0  # 時間変更もその時刻で同じ計算
    # 土曜 (型の休み) の時間変更 = 出勤
    sat = staff_input(
        "D", overrides={date(2026, 10, 10): DayOverride("custom_time", t("09:00"), t("18:00"))}
    )
    row = build_kinmu_month(OCT, [sat], 160).staff[0]
    assert row.plan[day_index(date(2026, 10, 10))] == 8.0
    assert row.marks[day_index(date(2026, 10, 10))] is None


def test_closed_day_is_work_unless_registered() -> None:
    closed = {date(2026, 10, 15): "創立記念日", date(2026, 10, 16): "創立記念日"}
    s = staff_input(overrides=off(date(2026, 10, 16)))
    km = build_kinmu_month(OCT, [s], 160, closed)
    assert km.days[14].closed == "創立記念日"
    assert km.staff[0].plan[14] == 8.0  # 登録が無い事業所の休みは出勤
    assert km.staff[0].plan[15] == "休"  # 登録がある日だけ「休」


def test_fte_truncates_at_second_decimal() -> None:
    assert fte(168, 160) == 1.0
    assert fte(175.9, 160) == 1.0
    assert fte(176, 160) == 1.1
    assert fte(84, 160) == 0.5
    assert fte(159.99, 160) == 0.9
    assert fte(0, 160) == 0.0


def test_required_hours_missing_raises() -> None:
    with pytest.raises(ValueError):
        build_kinmu_month(OCT, [staff_input()], None)
    with pytest.raises(ValueError):
        build_kinmu_month(OCT, [staff_input()], 0)


def test_manager_admin_hours_and_half_day() -> None:
    m = staff_input(
        "M",
        admin=4.0,
        title="管理責任者",
        overrides={
            date(2026, 10, 6): DayOverride("pm_off"),
            date(2026, 10, 7): DayOverride("paid_off"),
        },
    )
    row = build_kinmu_month(OCT, [m], 160).staff[0]
    i = day_index
    assert row.plan[i(date(2026, 10, 5))] == 4.0  # 8.0 − 管理 4.0
    assert row.admin is not None
    assert row.admin[i(date(2026, 10, 5))] == 4.0
    # 半休の日の管理時間は 0 (仮決め)・勤務時間はすべて看護へ
    assert row.plan[i(date(2026, 10, 6))] == 3.5
    assert row.admin[i(date(2026, 10, 6))] == 0.0
    # 休みの日は管理時間の行も空
    assert row.admin[i(date(2026, 10, 7))] is None
    assert row.admin[i(date(2026, 10, 3))] is None
    # 平日 22 日 − 半休 1 − 有給 1 = 20 日 × 4.0 ＋ 3.5
    assert row.plan_total == 83.5
    assert row.admin_total == 80.0


def test_order_manager_title_full_time_part_time() -> None:
    part = staff_input("P", full_time=False, start="09:00", end="13:00")
    plain = staff_input("F")
    titled = staff_input("T", title="課長")
    manager = staff_input("M", admin=4.0)
    km = build_kinmu_month(OCT, [part, plain, titled, manager], 160)
    assert [r.staff.key for r in km.staff] == ["M", "T", "F", "P"]
    # 非常勤 (9:00〜13:00) は 4.0 の日が 22 日 = 88.0・常勤換算 0.55 → 0.5
    p = km.staff[3]
    assert p.plan_total == 88.0 and p.plan_fte == 0.5


def test_actual_rows_use_reconciled_leave() -> None:
    plan = off(date(2026, 10, 5))
    actual = {date(2026, 10, 6): DayOverride("am_off")}
    s = staff_input(overrides=plan, actual=actual)
    km = build_kinmu_month(OCT, [s], 160)
    row = km.staff[0]
    i = day_index
    assert row.plan[i(date(2026, 10, 5))] == "休"
    assert row.actual is not None
    assert row.actual[i(date(2026, 10, 5))] == 8.0
    assert row.actual[i(date(2026, 10, 6))] == 5.0
    # 番号と休日数は実績 (照合を済ませた休み) で付ける
    assert row.marks[i(date(2026, 10, 5))] is None
    assert row.marks[i(date(2026, 10, 6))] == "0,5"
    assert row.holidays == HolidayCount(9.5, 0)
    assert row.plan_total == 168.0 and row.actual_total == 173.0
    assert km.actual_total == 173.0 and km.actual_fte == 1.0
    assert km.actual_day_totals is not None and km.actual_day_totals[4] == 8.0
    sheet = to_sheet_data(km)[0].rows
    assert sheet[8][3] == "実績" and sheet[8][4 + 4] == 8.0
    assert sheet[8][35] == Formula("=SUM(E9:AI9)") and _evaluate(sheet, sheet[8][35]) == 173.0
    assert sheet[11][4] == Formula("=SUM(E9)")  # 合計の実績の行


# ---------------------------------------------------------------------------
# 週の帯・年をまたぐ月 (ISO 53 週)
# ---------------------------------------------------------------------------


def test_week_bands_year_end_months() -> None:
    assert [(w.first.day, w.last.day) for w in week_bands(date(2026, 12, 1))] == [
        (1, 5),
        (6, 12),
        (13, 19),
        (20, 26),
        (27, 31),
    ]
    jan = week_bands(date(2027, 1, 1))
    assert [(w.first.day, w.last.day) for w in jan] == [
        (1, 2),
        (3, 9),
        (10, 16),
        (17, 23),
        (24, 30),
        (31, 31),
    ]
    assert jan[-1].label == "第6週"


def _add_staff(db, name: str, **kw) -> Staff:
    s = Staff(name=name, **kw)
    db.add(s)
    return s


def _add_off(db, staff: Staff, d: date, kind: str = "off", **kw) -> None:
    y, w, wd = iso_key(d)
    db.add(
        StaffWeeklyOverride(
            staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type=kind, **kw
        )
    )


@pytest.mark.asyncio
async def test_load_kinmu_inputs_across_iso_53_week(db) -> None:
    """2027 年 1 月: 1/1 は ISO 2026-W53。前の月 12/27 からの週の休みを読み、番号に数える."""
    staff = _add_staff(db, "年末 花子", code="001")
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=wd < 5))
    _add_off(db, staff, date(2026, 12, 28))  # 月 (ISO 2026-W53)
    _add_off(db, staff, date(2027, 1, 1))  # 金 (ISO 2026-W53)
    _add_off(db, staff, date(2027, 1, 4), "paid_off")  # 月 (ISO 2027-W01)
    db.add(LeaveMonth(month=date(2027, 1, 1), required_hours=152))
    db.add(OfficeClosedDay(date=date(2027, 1, 2), label="年末年始"))
    await db.flush()

    inputs = await load_kinmu_inputs(db, date(2027, 1, 15))
    assert inputs.month == date(2027, 1, 1)
    assert inputs.required_hours == 152
    assert inputs.closed_days == {date(2027, 1, 2): "年末年始"}
    s = inputs.staff[0]
    assert set(s.overrides) == {date(2026, 12, 28), date(2027, 1, 1), date(2027, 1, 4)}
    assert s.actual_overrides is None
    # 時刻が空の型は営業時間の既定 (9:30〜18:00)
    assert s.shifts[0] == (time(9, 30), time(18, 0))

    km = build_kinmu_month(inputs.month, inputs.staff, inputs.required_hours, inputs.closed_days)
    row = km.staff[0]
    # 週 12/27〜1/2: 12/27 (日) 1・12/28 (月) 2・1/1 (金) 祝1・1/2 (土) 祝2
    assert row.marks[0] == "祝1" and row.marks[1] == "祝2"
    assert row.marks[2] == "1"  # 1/3 (日) から新しい週
    assert row.marks[3] == "有給"
    assert row.plan[0] == "休" and row.plan[3] == "休"
    assert row.plan[4] == 7.5  # 9:30〜18:00 − 休憩
    assert km.days[1].closed == "年末年始"
    assert km.days[0].holiday is not None  # 元日
    # 1 月: 型の休み (土 5・日 5) ＋ 公休 1/1 = 11・有給 1
    assert row.holidays == HolidayCount(11.0, 1)


@pytest.mark.asyncio
async def test_load_kinmu_inputs_december_and_fields(db) -> None:
    """2026 年 12 月: 12/28〜12/31 は ISO 2026-W53。職員の項目と並び・実績の渡し方."""
    inage = Office(name="稲毛", sort_order=1)
    tsuga = Office(name="都賀", sort_order=2)
    db.add_all([inage, tsuga])
    await db.flush()
    nurse = _add_staff(
        db, "看護 一郎", code="002", qualification="看護師", primary_office_id=inage.id
    )
    boss = _add_staff(
        db,
        "管理 花子",
        code="003",
        qualification="看護師",
        primary_office_id=inage.id,
        admin_hours_per_day=4.0,
        kinmu_title="管理責任者",
        work_form="A",
    )
    part = _add_staff(
        db, "非常 次郎", code="001", employment_type="part_time", primary_office_id=tsuga.id
    )
    _add_staff(db, "退職 三郎", code="004", status="retired")
    await db.flush()
    db.add(StaffSecondaryOffice(staff_id=boss.id, office_id=tsuga.id))
    for s in (nurse, boss, part):
        for wd in range(5):
            db.add(
                StaffShift(
                    staff_id=s.id,
                    weekday=wd,
                    is_on=True,
                    start_time=t("09:00"),
                    end_time=t("18:00"),
                )
            )
    _add_off(db, nurse, date(2026, 12, 29))
    _add_off(
        db, nurse, date(2026, 12, 30), "custom_time", start_time=t("09:00"), end_time=t("12:00")
    )
    await db.flush()

    inputs = await load_kinmu_inputs(db, date(2026, 12, 1), with_actual=True)
    assert inputs.required_hours is None  # 勤務すべき時間数が未入力
    by_name = {s.name: s for s in inputs.staff}
    assert set(by_name) == {"看護 一郎", "管理 花子", "非常 次郎"}
    assert [s.name for s in inputs.staff] == ["非常 次郎", "看護 一郎", "管理 花子"]
    b = by_name["管理 花子"]
    assert b.office_label == "稲毛・都賀"
    assert b.title == "管理責任者" and b.job == "看護師" and b.work_form == "A"
    assert b.admin_hours_per_day == 4.0 and b.full_time
    assert not by_name["非常 次郎"].full_time
    n = by_name["看護 一郎"]
    assert n.overrides[date(2026, 12, 30)] == DayOverride("custom_time", t("09:00"), t("12:00"))
    assert n.actual_overrides == n.overrides

    with pytest.raises(ValueError):
        build_kinmu_month(inputs.month, inputs.staff, inputs.required_hours)
    km = build_kinmu_month(inputs.month, inputs.staff, 160)
    assert [r.staff.name for r in km.staff] == ["管理 花子", "看護 一郎", "非常 次郎"]
    nrow = km.staff[1]
    assert nrow.plan[28] == "休" and nrow.plan[29] == 3.0
    assert nrow.actual is not None and nrow.actual[29] == 3.0


@pytest.mark.asyncio
async def test_holiday_summary_from_db(db) -> None:
    staff = _add_staff(db, "集計 花子", code="001")
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=wd < 5))
    _add_off(db, staff, date(2026, 2, 2))
    _add_off(db, staff, date(2026, 3, 3), "am_off")
    _add_off(db, staff, date(2026, 3, 4), "paid_off")
    await db.flush()

    rows = await load_holiday_counts_by_month(db, date(2026, 3, 1))
    summary = build_holiday_summary(date(2026, 3, 1), rows)
    name, values, total = summary.rows[0]
    assert name == "集計 花子"
    # 1 月: 土 5・日 4 = 9／2 月: 土 4・日 4 ＋ 公休 1 = 9／3 月: 土 4・日 5 ＋ 半休 0.5 (有給は数えない)
    assert values[:3] == [9.0, 9.0, 9.5]
    assert values[3:] == [None] * 9
    assert total == 27.5
    assert summary.month_totals[:3] == [9.0, 9.0, 9.5] and summary.grand_total == 27.5

    sheet = summary_to_sheet_data(summary)
    v = sheet.rows
    assert sheet.title == "集計"
    assert v[0][0] == "休日取得数"
    assert v[2][0] == "■2026年" and v[2][1] == "1月"
    assert v[2][13] == "合計"
    assert v[3][:4] == ["集計 花子", 9.0, 9.0, 9.5]
    assert v[3][13] == Formula("=SUM(B4:M4)") and _evaluate(v, v[3][13]) == 27.5
    assert v[4][0] == "合計" and v[4][1] == Formula("=SUM(B4:B4)") and v[4][4] is None
    assert _evaluate(v, v[4][13]) == summary.grand_total


@pytest.mark.asyncio
async def test_holiday_summary_part_time_is_not_applicable(db) -> None:
    """非常勤は集計も「対象外」(合計に入れない)。1 月 1 日〜月末を 1 回で読む."""
    full = _add_staff(db, "常勤 花子", code="001")
    part = _add_staff(db, "非常 次郎", code="002", employment_type="part_time")
    await db.flush()
    for s in (full, part):
        for wd in range(7):
            db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=wd < 5))
    _add_off(db, full, date(2026, 1, 1))  # 木 (ISO 2026-W01)
    await db.flush()
    rows = await load_holiday_counts_by_month(db, date(2026, 2, 10))
    assert rows[1] == ("非常 次郎", None)
    summary = build_holiday_summary(date(2026, 2, 1), rows)
    # 1 月: 土 5・日 4 ＋ 公休 1 = 10／2 月: 土 4・日 4 = 8
    assert summary.rows[0][1][:2] == [10.0, 8.0]
    assert summary.rows[1][1][:3] == ["対象外", "対象外", None]
    assert summary.rows[1][2] == "対象外"
    assert summary.month_totals[:2] == [10.0, 8.0] and summary.grand_total == 18.0
    v = summary_to_sheet_data(summary).rows
    assert v[4][1] == "対象外"
    assert _evaluate(v, v[5][13]) == 18.0


def test_to_sheet_data_includes_summary_sheet() -> None:
    km = build_kinmu_month(OCT, [staff_input()], 160)
    summary = build_holiday_summary(OCT, [("職員S", {OCT: 9.0})])
    assert [s.title for s in to_sheet_data(km, summary)] == ["勤務表", "集計"]
    # 30 日の月は合計の列が 1 つ左 (9 月: E〜AH が日付・AI が合計)
    sep = build_kinmu_month(date(2026, 9, 1), [staff_input()], 160)
    v = to_sheet_data(sep)[0].rows
    assert v[3][34] == "暦月の合計"
    assert v[7][34] == Formula("=SUM(E8:AH8)")
    assert v[7][35] == Formula("=ROUNDDOWN(AI8/$AJ$1,1)")
    assert len(v[0]) == 4 + 30 + 3
    assert all(d.day.month == 9 for d in sep.days)
    assert sep.days[-1].day == date(2026, 9, 1) + timedelta(days=29)


# ---------------------------------------------------------------------------
# 休職・非常勤・管理者・働ける時間の無い半休 (ディレクター決定・KinmuRules で切り替え)
# ---------------------------------------------------------------------------


def test_on_leave_staff_all_off_and_not_in_fte() -> None:
    s = replace(staff_input("L"), on_leave=True)
    km = build_kinmu_month(OCT, [s, staff_input("F")], 160)
    row = next(r for r in km.staff if r.staff.key == "L")
    assert row.plan == ["休"] * 31
    assert row.plan_total == 0.0 and row.plan_fte is None
    assert row.marks is None and row.marks_note == "休職" and row.holidays is None
    v = to_sheet_data(km)[0].rows
    assert v[7][36] is None  # 常勤換算を書かない
    assert v[9][4] == "休職"
    # 切り替え: 休職でもふつうに計算する
    normal = build_kinmu_month(OCT, [s], 160, rules=KinmuRules(on_leave_all_off=False))
    assert normal.staff[0].plan_total == 176.0 and normal.staff[0].plan_fte == 1.1


def test_part_time_marks_not_applicable() -> None:
    p = staff_input("P", full_time=False)
    km = build_kinmu_month(OCT, [p], 160)
    row = km.staff[0]
    assert row.marks is None and row.marks_note == "対象外" and row.holidays is None
    assert row.plan_fte == 1.1  # 常勤換算には入れる
    assert to_sheet_data(km)[0].rows[9][4] == "対象外"
    with_marks = build_kinmu_month(OCT, [p], 160, rules=KinmuRules(part_time_marks=True))
    assert with_marks.staff[0].marks is not None
    assert with_marks.staff[0].holidays == HolidayCount(9.0, 0)


def test_admin_hours_zero_is_not_manager() -> None:
    z = staff_input("Z", admin=0.0)
    assert not z.is_manager
    row = build_kinmu_month(OCT, [z], 160).staff[0]
    assert row.admin is None and row.plan[4] == 8.0


def test_half_day_without_working_time_is_full_off() -> None:
    """勤務が 13:00 までの人の午前休は働ける時間が残らない → 終日の休み (番号を取る)."""
    s = staff_input(end="13:00", overrides={date(2026, 10, 6): DayOverride("am_off")})
    row = build_kinmu_month(OCT, [s], 160).staff[0]
    assert row.plan[5] == "休"
    assert row.marks is not None and row.marks[5] == "2"


def test_staff_name_starting_with_equals_stays_text() -> None:
    """職員名・職種・拠点名が = で始まっても数式にしない (数式は Formula だけ)."""
    s = replace(staff_input("X"), name="=HYPERLINK(1)", job="=1+1", office_label="=A1")
    v = to_sheet_data(build_kinmu_month(OCT, [s], 160))[0].rows
    assert v[7][0] == "=1+1" and v[7][2] == "=A1" and v[8][2] == "=HYPERLINK(1)"
    for cell in (v[7][0], v[7][2], v[8][0], v[8][2]):
        assert type(cell) is str
    assert isinstance(v[7][35], Formula)
    summary = summary_to_sheet_data(build_holiday_summary(OCT, [("=1+1", {OCT: 9.0})]))
    assert type(summary.rows[3][0]) is str


# ---------------------------------------------------------------------------
# 予定版の写し (kinmu_sheets.plan_snapshot)・退職者・休職
# ---------------------------------------------------------------------------


def test_snapshot_round_trip() -> None:
    s = staff_input(
        overrides={
            date(2026, 9, 28): DayOverride("off"),
            date(2026, 10, 5): DayOverride("custom_time", t("10:00"), t("15:00")),
        }
    )
    snap = json.loads(json.dumps(snapshot_plan([s])))
    got = plan_from_snapshot(snap, "S")
    assert got is not None
    assert got[0] == dict(s.shifts) and got[1] == dict(s.overrides)
    assert plan_from_snapshot(snap, "missing") is None


@pytest.mark.asyncio
async def test_final_version_plan_from_snapshot_actual_from_current(db) -> None:
    staff = _add_staff(db, "写し 花子", code="001")
    await db.flush()
    for wd in range(5):
        db.add(
            StaffShift(
                staff_id=staff.id,
                weekday=wd,
                is_on=True,
                start_time=t("09:00"),
                end_time=t("18:00"),
            )
        )
    _add_off(db, staff, date(2026, 10, 5))
    db.add(LeaveMonth(month=OCT, required_hours=160))
    await db.flush()

    # 写しが無い月: いまの予定を使い、印で知らせる
    first = await load_kinmu_inputs(db, OCT, with_actual=True)
    assert first.plan_source == "current"
    assert first.staff[0].overrides == first.staff[0].actual_overrides

    # 予定版を作ったときの写し (10/5 が休み) を置き、そのあと休みを 10/6 へ変える
    plan_inputs = await load_kinmu_inputs(db, OCT)
    assert plan_inputs.staff[0].actual_overrides is None
    db.add(
        KinmuSheet(
            month=OCT, kind="plan", version=1, plan_snapshot=snapshot_plan(plan_inputs.staff)
        )
    )
    await db.flush()
    row = await db.scalar(select(StaffWeeklyOverride))
    row.weekday = iso_key(date(2026, 10, 6))[2]
    await db.flush()

    final = await load_kinmu_inputs(db, OCT, with_actual=True)
    assert final.plan_source == "snapshot"
    km = build_kinmu_month(final.month, final.staff, final.required_hours)
    r = km.staff[0]
    assert r.plan[4] == "休" and r.plan[5] == 8.0  # 予定 = 写し
    assert r.actual is not None and r.actual[4] == 8.0 and r.actual[5] == "休"  # 実績 = いま
    assert r.marks is not None and r.marks[5] == "2"  # 番号は実績で付ける


@pytest.mark.asyncio
async def test_retired_staff_listed_only_with_activity(db) -> None:
    office = Office(name="拠点1")
    db.add(office)
    await db.flush()
    patient = Patient(code="P-1", name="利用者 一")
    db.add(patient)
    visited = _add_staff(db, "退職 訪問", code="001", status="retired")
    leave = _add_staff(db, "退職 休み", code="002", status="retired")
    _add_staff(db, "退職 なし", code="003", status="retired")
    _add_staff(db, "休職 花子", code="004", status="on_leave")
    await db.flush()
    db.add(
        Visit(
            patient_id=patient.id,
            primary_staff_id=visited.id,
            visit_date=date(2026, 10, 2),
            start_time=t("09:00"),
            end_time=t("10:00"),
            type="regular",
        )
    )
    _add_off(db, leave, date(2026, 10, 8))
    await db.flush()

    inputs = await load_kinmu_inputs(db, OCT)
    assert [s.name for s in inputs.staff] == ["退職 訪問", "退職 休み", "休職 花子"]
    assert inputs.staff[2].on_leave and not inputs.staff[0].on_leave
    # 訪問も休みも無い月 (11 月) の退職者は載せない
    nov = await load_kinmu_inputs(db, date(2026, 11, 1))
    assert [s.name for s in nov.staff] == ["休職 花子"]
    excluded = await load_kinmu_inputs(
        db, OCT, rules=KinmuRules(include_retired_with_activity=False)
    )
    assert [s.name for s in excluded.staff] == ["休職 花子"]
