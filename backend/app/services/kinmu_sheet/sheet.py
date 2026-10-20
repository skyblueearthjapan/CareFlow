"""勤務表の値 → スプレッドシートへ書く形 (行×列の値・結合・列幅) (設計 §7-1・§7-3).

様式はお客様の勤務表の「原本」シートの並び:

* 1 行目: A=「　M月」・右上に勤務すべき時間数と「時間」。2 行目: 表の名前と「（YYYY 年　M 月分 ）」。
* 4 行目: 「勤務形態」・週の帯・「暦月の合計」「常勤換算後の人数」「備　考」(4〜7 行目を結合)。
* 6 行目: 「職　　種」「氏　　名」と日付。7 行目: 曜日。
* 8 行目から職員ごと: 予定 → (管理者だけ) 管理時間 → 実績 → 番号の行。B 列 (勤務形態) は職員の
  行を通して結合、A 列 (職種)・C 列 (氏名) は 2 行目から結合。
* 下に「合計」の予定・実績、その下に勤務形態の区分と常勤換算の注記。

ここで決める ``SheetTable`` は勤務表の担当の中間の形 (Google ドライブの担当の ``SheetData``
に合わせるのは統合のとき)。行・列は 0 始まり。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from app.services.kinmu_sheet.build import CellValue, HolidaySummary, KinmuMonth

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

#: 列幅 (ピクセル)。日付の列は ``DAY_WIDTH``。
_WIDTHS: Final = {COL_JOB: 85, COL_FORM: 40, COL_NAME: 100, COL_KIND: 75}
DAY_WIDTH: Final = 36
_TOTAL_WIDTH: Final = 60


@dataclass
class SheetTable:
    """1 枚のシート: 値の 2 次元の表と、結合・列幅・固定する行と列の最小限."""

    title: str
    values: list[list[CellValue | int]]
    #: (行の始め, 列の始め, 行の終わり, 列の終わり)・両端を含む。
    merges: list[tuple[int, int, int, int]] = field(default_factory=list)
    column_widths: dict[int, int] = field(default_factory=dict)
    frozen_rows: int = 0
    frozen_cols: int = 0


class _Grid:
    def __init__(self, width: int) -> None:
        self.width = width
        self.rows: list[list[CellValue | int]] = []

    def row(self, index: int) -> list[CellValue | int]:
        while len(self.rows) <= index:
            self.rows.append([None] * self.width)
        return self.rows[index]

    def put(self, r: int, c: int, value: CellValue | int) -> None:
        self.row(r)[c] = value


def _mark_value(mark: str | None) -> CellValue | int:
    """番号の行: 「1」「2」は数で書く (見本どおり)。ほかは文字."""
    if mark is not None and mark.isdigit():
        return int(mark)
    return mark


def to_sheet_data(km: KinmuMonth, summary: HolidaySummary | None = None) -> list[SheetTable]:
    """勤務表の値 → シート (「勤務表」と、``summary`` があれば「集計」)."""
    ndays = len(km.days)
    col_total = COL_FIRST_DAY + ndays
    col_fte = col_total + 1
    col_note = col_total + 2
    g = _Grid(col_note + 1)
    merges: list[tuple[int, int, int, int]] = []

    # 見出し
    g.put(0, COL_JOB, f"　{km.month.month}月")
    g.put(0, col_fte - 10, REQUIRED_HOURS_LABEL)
    g.put(0, col_fte, km.required_hours)
    g.put(0, col_note, "時間")
    g.put(1, COL_JOB, TABLE_TITLE)
    g.put(1, 13, f"（{km.month.year} 年　{km.month.month} 月分 ）")
    merges.append((1, 13, 1, 18))
    g.put(ROW_WEEKS, COL_FORM, "勤務形態")
    for w in km.weeks:
        c0 = COL_FIRST_DAY + w.first.day - 1
        c1 = COL_FIRST_DAY + w.last.day - 1
        g.put(ROW_WEEKS, c0, w.label)
        if c1 > c0:
            merges.append((ROW_WEEKS, c0, ROW_WEEKS, c1))
    g.put(ROW_WEEKS, col_total, "暦月の合計")
    g.put(ROW_WEEKS, col_fte, "常勤換算後の人数")
    g.put(ROW_WEEKS, col_note, "備　 考")
    for c in (COL_FORM, col_total, col_fte, col_note):
        merges.append((ROW_WEEKS, c, ROW_WEEKDAYS, c))
    g.put(ROW_DATES, COL_JOB, "職　　種")
    g.put(ROW_DATES, COL_NAME, "氏　　名")
    for i, h in enumerate(km.days):
        g.put(ROW_DATES, COL_FIRST_DAY + i, h.day.day)
        g.put(ROW_WEEKDAYS, COL_FIRST_DAY + i, h.weekday_label)

    def put_cells(r: int, cells: Sequence[CellValue]) -> None:
        for i, v in enumerate(cells):
            g.put(r, COL_FIRST_DAY + i, v)

    # 職員
    r = ROW_FIRST_STAFF
    for row in km.staff:
        s = row.staff
        top = r
        g.put(r, COL_JOB, s.title or s.job)
        g.put(r, COL_FORM, s.work_form)
        g.put(r, COL_NAME, s.office_label)
        g.put(r, COL_KIND, "予定")
        put_cells(r, row.plan)
        g.put(r, col_total, row.plan_total)
        g.put(r, col_fte, row.plan_fte)
        r += 1
        g.put(r, COL_JOB, s.job)
        g.put(r, COL_NAME, s.name)
        if row.admin is not None:
            g.put(r, COL_KIND, "管理時間")
            put_cells(r, row.admin)
            g.put(r, col_total, row.admin_total)
            g.put(r, col_fte, row.admin_fte)
            r += 1
        g.put(r, COL_KIND, "実績")
        if row.actual is not None:
            put_cells(r, row.actual)
            g.put(r, col_total, row.actual_total)
        r += 1
        for i, m in enumerate(row.marks):
            g.put(r, COL_FIRST_DAY + i, _mark_value(m))
        merges.append((top, COL_FORM, r, COL_FORM))
        merges.append((top + 1, COL_JOB, r, COL_JOB))
        merges.append((top + 1, COL_NAME, r, COL_NAME))
        r += 1

    # 合計
    total_top = r
    g.put(r, COL_JOB, "合計")
    g.put(r, COL_KIND, "予定")
    put_cells(r, km.plan_day_totals)
    g.put(r, col_total, km.plan_total)
    g.put(r, col_fte, km.plan_fte)
    r += 1
    g.put(r, COL_KIND, "実績")
    if km.actual_day_totals is not None:
        put_cells(r, km.actual_day_totals)
        g.put(r, col_total, km.actual_total)
    for c in (COL_JOB, COL_FORM, COL_NAME):
        merges.append((total_top, c, r, c))
    merges.append((ROW_FIRST_STAFF, col_note, r, col_note))

    # 注記
    r += 2
    g.put(r, COL_NAME, WORK_FORM_NOTE)
    for note in FTE_NOTES:
        r += 1
        g.put(r, COL_JOB, note)

    widths = dict(_WIDTHS)
    widths.update({COL_FIRST_DAY + i: DAY_WIDTH for i in range(ndays)})
    widths.update({col_total: _TOTAL_WIDTH, col_fte: _TOTAL_WIDTH, col_note: _TOTAL_WIDTH})
    sheets = [
        SheetTable(
            title=KINMU_SHEET_TITLE,
            values=g.rows,
            merges=merges,
            column_widths=widths,
            frozen_rows=ROW_WEEKDAYS + 1,
            frozen_cols=COL_FIRST_DAY,
        )
    ]
    if summary is not None:
        sheets.append(summary_to_sheet_data(summary))
    return sheets


def summary_to_sheet_data(summary: HolidaySummary) -> SheetTable:
    """「集計」シート: A1「休日取得数」・3 行目に「■YYYY年」と 1〜12 月・「合計」、職員の行・合計の行."""
    width = 1 + len(summary.months) + 1
    g = _Grid(width)
    g.put(0, 0, "休日取得数")
    g.put(2, 0, f"■{summary.year}年")
    for i, m in enumerate(summary.months):
        g.put(2, 1 + i, f"{m.month}月")
    g.put(2, width - 1, "合計")
    r = 3
    for name, values, total in summary.rows:
        g.put(r, 0, name)
        for i, v in enumerate(values):
            g.put(r, 1 + i, v)
        g.put(r, width - 1, total)
        r += 1
    g.put(r, 0, "合計")
    for i, v in enumerate(summary.month_totals):
        g.put(r, 1 + i, v)
    g.put(r, width - 1, summary.grand_total)
    return SheetTable(
        title=SUMMARY_SHEET_TITLE,
        values=g.rows,
        column_widths={0: 110},
        frozen_rows=3,
        frozen_cols=1,
    )


__all__ = [
    "KINMU_SHEET_TITLE",
    "SUMMARY_SHEET_TITLE",
    "SheetTable",
    "summary_to_sheet_data",
    "to_sheet_data",
]
