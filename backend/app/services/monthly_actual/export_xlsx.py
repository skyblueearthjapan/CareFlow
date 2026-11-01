"""月の一覧 (Excel) — ``docs/tools/monthly-actual/build_final.py`` と同じ 4 シート.

1. 「{M}月の実績」: カイポケの実績の 1 行ずつ (日付・曜日・利用者・サービス内容・職員・開始・
   終了・提供時間(分)・QR 到着〜退出・QR と同じ・備考)。要確認の行は色を付ける。
2. 「要確認」: 人が見る・カイポケに無い・カイポケだけの行 (日付・利用者・職員・カイポケの実績・
   QR・確認すること)。
3. 「看護師別」: らく助の看護師ごとの月の実績 (``staff_report``・件数・稼働日数・患者数・
   週の平均患者数・2 名訪問・同行・提供時間・平均滞在・到着＋35 分・移動距離) と、
   カイポケの職員ごとの件数・提供時間 (名前の照合キーで突き合わせる)。
4. 「読み方」: この一覧の説明。

事業所の名前はコードに書かず、事業所の情報 (``business_profile.station_name``) から入れる。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from io import BytesIO
from typing import Final

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

from app.models.monthly_actual import MonthlyActualRow
from app.services.monthly_actual import judge as rules
from app.services.monthly_actual.staff_report import MonthStaffReport

_WD: Final = "月火水木金土日"
_THIN: Final = Side(style="thin", color="D9CFCA")
_BORDER: Final = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEAD_FILL: Final = PatternFill("solid", fgColor="FCE4EC")
_ASK_FILL: Final = PatternFill("solid", fgColor="FFF3E0")

MAIN_HEADERS: Final = (
    "日付",
    "曜日",
    "利用者",
    "サービス内容",
    "職員",
    "開始",
    "終了",
    "提供時間(分)",
    "QR 到着〜退出",
    "QR と同じ",
    "備考",
)
ASK_HEADERS: Final = ("日付", "利用者", "職員", "カイポケの実績", "QR", "確認すること")
STAFF_HEADERS: Final = (
    "看護師",
    "拠点",
    "訪問の件数",
    "稼働日数",
    "1日あたりの件数",
    "担当した患者様の数",
    "週の平均患者数",
    "2名訪問・同行の件数",
    "提供時間の合計(分)",
    "1件あたりの平均滞在(分)",
    "30分未満→到着＋35分",
    "退出なし→到着＋35分",
    "移動距離の合計(km)",
    "移動距離 1日あたり(km)",
    "移動距離 1件あたり(km)",
    "カイポケの件数",
    "カイポケの提供時間(分)",
)
#: 要確認に出す判定。
ASK_STATUSES: Final = (rules.STATUS_REVIEW, rules.STATUS_MISSING, rules.STATUS_KAIPOKE_ONLY)


def sheet_names(month: str) -> list[str]:
    return [f"{int(month.split('-')[1])}月の実績", "要確認", "看護師別", "読み方"]


def _head(ws: Worksheet, cols: Sequence[str], widths: Sequence[int]) -> None:
    ws.append(list(cols))
    for cell, width in zip(ws[ws.max_row], widths, strict=False):
        cell.font = Font(bold=True)
        cell.fill = _HEAD_FILL
        cell.border = _BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[cell.column_letter].width = width


def _qr(row: MonthlyActualRow) -> str:
    if not row.rk_arrival and not row.rk_departure:
        return ""
    return f"{row.rk_arrival or '—'}〜{row.rk_departure or '—'}"


def _kp(row: MonthlyActualRow) -> str:
    return f"{row.kp_start}〜{row.kp_end}" if row.kp_start else ""


def _note(row: MonthlyActualRow) -> str:
    """備考 = 要確認なら先頭に「要確認：」・印・後から合わせた時刻。"""
    parts: list[str] = []
    flags = list(row.flags or [])
    review = [f["label"] for f in flags if f.get("review")]
    info = [f["label"] for f in flags if not f.get("review")]
    if row.status == rules.STATUS_REVIEW:
        parts.append("要確認：" + "・".join(review or ["人が見る"]))
    elif row.status == rules.STATUS_MISSING:
        parts.append("要確認：カイポケに無い（足す候補）")
    elif row.status == rules.STATUS_KAIPOKE_ONLY:
        parts.append("要確認：らく助に無い（カイポケだけ）")
    parts.extend(info)
    if row.rk_adjusted:
        parts.append(f"QR は後から合わせた（{row.rk_adjusted}）")
    return "／".join(parts)


def _ask_text(row: MonthlyActualRow) -> str:
    note = _note(row)
    return note.removeprefix("要確認：")


def build_monthly_actual_xlsx(
    rows: Sequence[MonthlyActualRow],
    *,
    month: str,
    station_name: str | None,
    kaipoke_fetched_at: datetime | None,
    generated_on: date | None = None,
    staff_report: MonthStaffReport | None = None,
) -> bytes:
    """判定の行から月の一覧 (4 シート) を作る。

    ``staff_report`` = らく助の看護師別の月の実績 (看護師別のシート)。無ければカイポケの
    列だけを埋める。
    """
    names = sheet_names(month)
    year, mon = (int(x) for x in month.split("-"))
    made = generated_on or date.today()
    # 1 行目はカイポケの行 (反映後のカイポケの実績の一覧)。カイポケに無い行は要確認へ。
    kp_rows = sorted(
        (r for r in rows if r.kp_start),
        key=lambda r: (r.visit_date, r.kp_start or "", rules.name_key(r.kp_patient or r.patient)),
    )
    asks = sorted(
        (r for r in rows if r.status in ASK_STATUSES),
        key=lambda r: (r.visit_date, r.new_start or r.kp_start or "", rules.name_key(r.patient)),
    )
    qr_eq = sum(1 for r in kp_rows if r.status == rules.STATUS_MATCH)

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = names[0]
    _head(ws, MAIN_HEADERS, (11, 5, 14, 24, 22, 7, 7, 9, 14, 8, 46))
    for r in kp_rows:
        ws.append(
            [
                r.visit_date,
                _WD[r.visit_date.weekday()],
                r.kp_patient or r.patient,
                r.kp_service or "",
                r.kp_staff or "",
                r.kp_start,
                r.kp_end,
                r.kp_minutes,
                _qr(r),
                "○" if r.status == rules.STATUS_MATCH else "",
                _note(r),
            ]
        )
        ask = r.status in ASK_STATUSES
        for cell in ws[ws.max_row]:
            cell.border = _BORDER
            if ask:
                cell.fill = _ASK_FILL
        ws.cell(ws.max_row, 1).number_format = "m/d"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    w2 = wb.create_sheet(names[1])
    _head(w2, ASK_HEADERS, (11, 14, 22, 14, 14, 60))
    for r in asks:
        w2.append(
            [
                r.visit_date,
                r.patient,
                r.kp_staff or r.rk_staff or "",
                _kp(r),
                _qr(r),
                _ask_text(r),
            ]
        )
        for cell in w2[w2.max_row]:
            cell.border = _BORDER
        w2.cell(w2.max_row, 1).number_format = "m/d"

    w3 = wb.create_sheet(names[2])
    _head(w3, STAFF_HEADERS, (18, 8, 9, 9, 10, 10, 10, 11, 11, 11, 11, 11, 11, 11, 11, 10, 11))
    by_staff: dict[str, Counter[str]] = defaultdict(Counter)
    kp_names: dict[str, str] = {}
    for r in kp_rows:
        for staff in (r.kp_staff or "").split("・"):
            if not staff:
                continue
            key = rules.name_key(staff)
            kp_names.setdefault(key, staff)
            by_staff[key]["件数"] += 1
            by_staff[key]["分"] += r.kp_minutes or 0
    for s in staff_report.staff if staff_report is not None else []:
        kp = by_staff.pop(rules.name_key(s.name), Counter())
        w3.append(
            [
                s.name,
                s.office_short or "",
                s.visits,
                s.days,
                s.per_day,
                s.patients,
                s.weekly_patients_avg,
                s.accompany_visits,
                s.provided_min_total,
                s.avg_stay_min,
                s.short_stay_count,
                s.no_departure_count,
                s.km_total,
                s.km_per_day,
                s.km_per_visit,
                kp["件数"],
                kp["分"],
            ]
        )
        for cell in w3[w3.max_row]:
            cell.border = _BORDER
    # らく助に無いカイポケの職員 (名前の字体違いなど) はカイポケの列だけ。
    for key, c in sorted(by_staff.items(), key=lambda kv: (-kv[1]["件数"], kv[0])):
        w3.append([kp_names[key], *([None] * (len(STAFF_HEADERS) - 3)), c["件数"], c["分"]])
        for cell in w3[w3.max_row]:
            cell.border = _BORDER
    w3.freeze_panes = "B2"

    w4 = wb.create_sheet(names[3])
    station = f"　{station_name}" if station_name else ""
    fetched = (
        f"（カイポケの実績の読み取り {kaipoke_fetched_at:%Y/%m/%d %H:%M} UTC）"
        if kaipoke_fetched_at
        else ""
    )
    missing = sum(1 for r in rows if r.status == rules.STATUS_MISSING)
    for line in (
        f"訪問時間の記録 {year} 年 {mon} 月{station}（{made:%Y/%m/%d} 作成）{fetched}",
        f"カイポケの {mon} 月の実績 {len(kp_rows)} 件の一覧です。らく助の QR の記録"
        "（看護師が後から合わせた時刻を含む）をもとに、カイポケの実績を合わせました。",
        f"QR と同じ：カイポケの実績の時刻が、らく助の QR の時刻と同じ訪問です（{qr_eq} 件）。",
        f"{rules.SHORT_STAY_MINUTES} 分に満たない訪問、退出の QR が無い訪問は、決まりどおり到着から "
        f"{rules.SHORT_STAY_END_MINUTES} 分で入れています。",
        f"カイポケに無い訪問（{missing} 件）は、らく助の記録をもとに足す候補です（要確認のシート）。",
        f"要確認（{len(asks)} 件）：らく助の記録どうしが食い違う、担当や人数が違う、"
        "片方にしか無い訪問です。「要確認」のシートをご覧ください。",
        "提供時間（分）はカイポケが開始と終了から計算した値です。",
        "「看護師別」のシートの数は、らく助の実績（主担当として受け持った訪問・取消と削除を除く）"
        "から数えています。2 名訪問・同行は 2 人目として入った件数です。提供時間は到着〜退出で、"
        f"{rules.SHORT_STAY_MINUTES} 分未満と退出の記録が無い訪問は到着＋"
        f"{rules.SHORT_STAY_END_MINUTES} 分にした後の値です。移動距離は拠点と訪問先を直線で"
        "つないだ目安です。右の 2 列はカイポケの実績の件数と提供時間です。",
    ):
        w4.append([line])
    w4.column_dimensions["A"].width = 120

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = [
    "ASK_HEADERS",
    "MAIN_HEADERS",
    "STAFF_HEADERS",
    "build_monthly_actual_xlsx",
    "sheet_names",
]
