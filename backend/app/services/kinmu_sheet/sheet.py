"""勤務表の値 → Google スプレッドシートへ書く形 (``SheetData``) (設計 §7-1・§7-3).

様式はお客様の勤務表の 2026.10 シートの並びに、「原本」シートの見出し (2 行目の「（YYYY 年　M 月分 ）」・
1 行目の「常勤の従業者が当該月勤務すべき時間数（合計）」) を足したもの:

* 1 行目: A=「　M月」・右上に勤務すべき時間数と「時間」。2 行目: 表の名前と「（YYYY 年　M 月分 ）」。
* 4 行目: 「勤務形態」・週の帯・「暦月の合計」「常勤換算後の人数」「備　考」(4〜7 行目を結合)。
* 6 行目: 「職　　種」「氏　　名」と日付。7 行目: 曜日。
* 8 行目から職員ごと: 予定 → (管理者だけ) 管理時間 → 実績 → 番号の行。B 列 (勤務形態) は職員の
  行を通して結合、A 列 (職種)・C 列 (氏名) は 2 行目から結合。
* 下に「合計」の予定・実績、合計の 2 行下から勤務形態の区分と常勤換算の注記 (見本の 34〜36 行目)。

合計・常勤換算・日ごとの合計は**式** (``Formula``) で書く (行の合計 ``=SUM(E8:AI8)``・常勤換算
``=ROUNDDOWN(AJ8/$AK$1,1)``・合計の行は対象の行を並べた ``=SUM(E8,E12,…)``)。``KinmuMonth`` の
値は試験の比べる相手。職員名などの文字は str のまま (= で始まっても文字)。行・列は 0 始まり、
``CellRange`` の終わりは含まない。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.services.google_drive.sheets import (
    CellFormat,
    CellRange,
    CellValue,
    FormatRange,
    Formula,
    SheetData,
)
from app.services.kinmu_sheet.build import HolidaySummary, KinmuMonth

KINMU_SHEET_TITLE: Final = "勤務表"
SUMMARY_SHEET_TITLE: Final = "集計"

TABLE_TITLE: Final = "従業者の勤務の体制及び勤務形態一覧表"
REQUIRED_HOURS_LABEL: Final = "常勤の従業者が当該月勤務すべき時間数（合計）"
WORK_FORM_NOTE: Final = (
    "勤務形態の区分　Ａ：常勤で専従　Ｂ：常勤で兼務　Ｃ：非常勤で専従　Ｄ：非常勤で兼務"
)
FTE_NOTES: Final = (
    "　　　㊟　常勤換算が必要な職種は、Ａ～Ｄの「平均の勤務時間」をすべて足し、"
    "常勤の従業者が暦月に勤務すべき時間数で割って、「常勤換算後の人数」を算出してください。",
    "　　　　　　算出にあたっては、小数点以下第２位を切り捨ててください。",
)

#: 列 (0 始まり)。A=職種・B=勤務形態・C=氏名・D=予定/実績・E から日付。
COL_JOB: Final = 0
COL_FORM: Final = 1
COL_NAME: Final = 2
COL_KIND: Final = 3
COL_FIRST_DAY: Final = 4
#: 見出しの行 (0 始まり)。
ROW_WEEKS: Final = 3
ROW_DATES: Final = 5
ROW_WEEKDAYS: Final = 6
ROW_FIRST_STAFF: Final = 7
#: 合計の行 (2 行目) から注記までの行数 (見本: 31 行目 → 34 行目)。
_NOTE_GAP: Final = 3

#: 列幅 (ピクセル)。
_WIDTHS: Final = {COL_JOB: 85, COL_FORM: 40, COL_NAME: 100, COL_KIND: 75}
DAY_WIDTH: Final = 36
_TOTAL_WIDTH: Final = 60

HOURS_FORMAT: Final = "0.0"
_HEADER = CellFormat(background="#EFEFEF", bold=True, horizontal_align="CENTER")
_HOURS = CellFormat(number_format=HOURS_FORMAT)
_TITLE = CellFormat(bold=True, font_size=12)


def col_letter(col: int) -> str:
    """0 始まりの列 → A1 形式の列名 (0 → A・26 → AA)."""
    out = ""
    n = col + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def a1(row: int, col: int, *, absolute: bool = False) -> str:
    """0 始まりの (行, 列) → A1 形式 (``absolute`` で ``$AK$1``)."""
    if absolute:
        return f"${col_letter(col)}${row + 1}"
    return f"{col_letter(col)}{row + 1}"


def _range(r0: int, c0: int, r1: int, c1: int) -> CellRange:
    """両端を含む (行, 列) → 終わりを含まない ``CellRange``."""
    return CellRange(r0, c0, r1 + 1, c1 + 1)


class _Grid:
    def __init__(self, width: int) -> None:
        self.width = width
        self.rows: list[list[CellValue]] = []

    def put(self, r: int, c: int, value: CellValue) -> None:
        while len(self.rows) <= r:
            self.rows.append([None] * self.width)
        self.rows[r][c] = value


def _mark_value(mark: str | None) -> CellValue:
    """番号の行: 「1」「2」は数で書く (見本どおり)。ほかは文字."""
    if mark is not None and mark.isdigit():
        return int(mark)
    return mark


def to_sheet_data(km: KinmuMonth, summary: HolidaySummary | None = None) -> list[SheetData]:
    """勤務表の値 → シート (「勤務表」と、``summary`` があれば「集計」)."""
    ndays = len(km.days)
    col_last_day = COL_FIRST_DAY + ndays - 1
    col_total = col_last_day + 1
    col_fte = col_total + 1
    col_note = col_total + 2
    required = a1(0, col_fte, absolute=True)
    g = _Grid(col_note + 1)
    merges: list[CellRange] = []
    formats: list[FormatRange] = []

    def row_sum(r: int) -> Formula:
        return Formula(f"=SUM({a1(r, COL_FIRST_DAY)}:{a1(r, col_last_day)})")

    def fte_formula(r: int) -> Formula:
        return Formula(f"=ROUNDDOWN({a1(r, col_total)}/{required},1)")

    def put_cells(r: int, cells: Sequence[CellValue]) -> None:
        for i, v in enumerate(cells):
            g.put(r, COL_FIRST_DAY + i, v)
        formats.append(FormatRange(_range(r, COL_FIRST_DAY, r, col_fte), _HOURS))

    # 見出し
    g.put(0, COL_JOB, f"　{km.month.month}月")
    g.put(0, col_fte - 10, REQUIRED_HOURS_LABEL)
    g.put(0, col_fte, km.required_hours)
    g.put(0, col_note, "時間")
    g.put(1, COL_JOB, TABLE_TITLE)
    g.put(1, 13, f"（{km.month.year} 年　{km.month.month} 月分 ）")
    merges.append(_range(1, 13, 1, 18))
    formats.append(FormatRange(_range(0, 0, 1, 0), _TITLE))
    g.put(ROW_WEEKS, COL_FORM, "勤務形態")
    for w in km.weeks:
        c0 = COL_FIRST_DAY + w.first.day - 1
        c1 = COL_FIRST_DAY + w.last.day - 1
        g.put(ROW_WEEKS, c0, w.label)
        if c1 > c0:
            merges.append(_range(ROW_WEEKS, c0, ROW_WEEKS, c1))
    g.put(ROW_WEEKS, col_total, "暦月の合計")
    g.put(ROW_WEEKS, col_fte, "常勤換算後の人数")
    g.put(ROW_WEEKS, col_note, "備　 考")
    for c in (COL_FORM, col_total, col_fte, col_note):
        merges.append(_range(ROW_WEEKS, c, ROW_WEEKDAYS, c))
    g.put(ROW_DATES, COL_JOB, "職　　種")
    g.put(ROW_DATES, COL_NAME, "氏　　名")
    for i, h in enumerate(km.days):
        g.put(ROW_DATES, COL_FIRST_DAY + i, h.day.day)
        g.put(ROW_WEEKDAYS, COL_FIRST_DAY + i, h.weekday_label)

    # 職員
    plan_rows: list[int] = []
    actual_rows: list[int] = []
    r = ROW_FIRST_STAFF
    for row in km.staff:
        s = row.staff
        top = r
        g.put(r, COL_JOB, s.title or s.job)
        g.put(r, COL_FORM, s.work_form)
        g.put(r, COL_NAME, s.office_label)
        g.put(r, COL_KIND, "予定")
        put_cells(r, row.plan)
        g.put(r, col_total, row_sum(r))
        if row.plan_fte is not None:
            g.put(r, col_fte, fte_formula(r))
        plan_rows.append(r)
        r += 1
        g.put(r, COL_JOB, s.job)
        g.put(r, COL_NAME, s.name)
        if row.admin is not None:
            g.put(r, COL_KIND, "管理時間")
            put_cells(r, row.admin)
            g.put(r, col_total, row_sum(r))
            if row.admin_fte is not None:
                g.put(r, col_fte, fte_formula(r))
            r += 1
        g.put(r, COL_KIND, "実績")
        if row.actual is not None:
            put_cells(r, row.actual)
            g.put(r, col_total, row_sum(r))
            actual_rows.append(r)
        r += 1
        if row.marks is not None:
            for i, m in enumerate(row.marks):
                g.put(r, COL_FIRST_DAY + i, _mark_value(m))
        else:
            g.put(r, COL_FIRST_DAY, row.marks_note)
        merges.append(_range(top, COL_FORM, r, COL_FORM))
        merges.append(_range(top + 1, COL_JOB, r, COL_JOB))
        merges.append(_range(top + 1, COL_NAME, r, COL_NAME))
        r += 1

    def list_sum(rows: list[int], c: int) -> Formula:
        return Formula("=SUM(" + ",".join(a1(x, c) for x in rows) + ")" if rows else "=0")

    # 合計
    total_top = r
    g.put(r, COL_JOB, "合計")
    g.put(r, COL_KIND, "予定")
    put_cells(r, [list_sum(plan_rows, COL_FIRST_DAY + i) for i in range(ndays)])
    g.put(r, col_total, list_sum(plan_rows, col_total))
    g.put(r, col_fte, fte_formula(r))
    r += 1
    g.put(r, COL_KIND, "実績")
    if km.actual_day_totals is not None:
        put_cells(r, [list_sum(actual_rows, COL_FIRST_DAY + i) for i in range(ndays)])
        g.put(r, col_total, list_sum(actual_rows, col_total))
    for c in (COL_JOB, COL_FORM, COL_NAME):
        merges.append(_range(total_top, c, r, c))
    merges.append(_range(ROW_FIRST_STAFF, col_note, r, col_note))
    formats.append(FormatRange(_range(ROW_WEEKS, 0, r, col_note), borders=True))
    formats.insert(0, FormatRange(_range(ROW_WEEKS, 0, ROW_WEEKDAYS, col_note), _HEADER))

    # 注記
    r += _NOTE_GAP
    g.put(r, COL_NAME, WORK_FORM_NOTE)
    for note in FTE_NOTES:
        r += 1
        g.put(r, COL_JOB, note)

    widths = dict(_WIDTHS)
    widths.update({COL_FIRST_DAY + i: DAY_WIDTH for i in range(ndays)})
    widths.update({col_total: _TOTAL_WIDTH, col_fte: _TOTAL_WIDTH, col_note: _TOTAL_WIDTH})
    sheets = [
        SheetData(
            title=KINMU_SHEET_TITLE,
            rows=g.rows,
            formats=formats,
            merges=merges,
            column_widths=widths,
            frozen_rows=ROW_WEEKDAYS + 1,
            frozen_cols=COL_FIRST_DAY,
        )
    ]
    if summary is not None:
        sheets.append(summary_to_sheet_data(summary))
    return sheets


def summary_to_sheet_data(summary: HolidaySummary) -> SheetData:
    """「集計」シート: A1「休日取得数」・3 行目に「■YYYY年」と 1〜12 月・「合計」、職員の行・合計の行.

    職員の行の合計・合計の行は式 (「対象外」の文字は SUM が数えない)。
    """
    width = 1 + len(summary.months) + 1
    last = width - 1
    g = _Grid(width)
    g.put(0, 0, "休日取得数")
    g.put(2, 0, f"■{summary.year}年")
    for i, m in enumerate(summary.months):
        g.put(2, 1 + i, f"{m.month}月")
    g.put(2, last, "合計")
    r = 3
    for name, values, _ in summary.rows:
        g.put(r, 0, name)
        for i, v in enumerate(values):
            g.put(r, 1 + i, v)
        g.put(r, last, Formula(f"=SUM({a1(r, 1)}:{a1(r, last - 1)})"))
        r += 1
    g.put(r, 0, "合計")
    for c in range(1, width):
        if c == last or summary.month_totals[c - 1] is not None:
            g.put(r, c, Formula(f"=SUM({a1(3, c)}:{a1(r - 1, c)})") if r > 3 else 0)
    return SheetData(
        title=SUMMARY_SHEET_TITLE,
        rows=g.rows,
        formats=[
            FormatRange(_range(2, 0, 2, last), _HEADER),
            FormatRange(_range(3, 1, r, last), _HOURS),
            FormatRange(_range(2, 0, r, last), borders=True),
            FormatRange(_range(0, 0, 0, 0), _TITLE),
        ],
        column_widths={0: 110},
        frozen_rows=3,
        frozen_cols=1,
    )


__all__ = [
    "HOURS_FORMAT",
    "KINMU_SHEET_TITLE",
    "SUMMARY_SHEET_TITLE",
    "a1",
    "col_letter",
    "summary_to_sheet_data",
    "to_sheet_data",
]
