"""Excel 出力 (テンプレート / 全件エクスポート).

openpyxl で 2 シート構成のワークブックを組み立てる:

  1. スタッフマスタシート
  2. 勤務シフトシート

呼び出し側 (API endpoint) は ``build_workbook`` の戻り値である ``Workbook``
を ``save`` するか ``BytesIO`` にダンプしてレスポンスする.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import time
from io import BytesIO
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from app.models.office import Office
from app.models.staff import Staff, StaffShift, StaffWeeklyOverride
from app.services.staff_excel.schema import (
    HEADER_FILL_COLOR,
    HEADER_FONT_COLOR,
    ID_COLUMN_FILL_COLOR,
    OVERRIDE_COL_INDEX,
    OVERRIDE_COLUMNS,
    SHEET_OVERRIDE,
    SHEET_SHIFT,
    SHEET_STAFF,
    SHIFT_COL_INDEX,
    SHIFT_COLUMNS,
    STAFF_COL_INDEX,
    STAFF_COLUMNS,
    WEEKDAY_INT_TO_LABEL,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _set_header_row(ws: Worksheet, columns: list[dict[str, object]]) -> None:
    """1 行目にヘッダーを書き、太字 + 背景色 + freeze を設定."""
    header_font = Font(bold=True, color=HEADER_FONT_COLOR)
    header_fill = PatternFill("solid", fgColor=HEADER_FILL_COLOR)
    center = Alignment(horizontal="center", vertical="center")
    for col_idx, col_def in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=str(col_def["header"]))
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
        width = col_def.get("width", 14)
        ws.column_dimensions[get_column_letter(col_idx)].width = int(width)  # type: ignore[arg-type]
    ws.freeze_panes = "A2"


def _attach_dropdowns(
    ws: Worksheet,
    columns: list[dict[str, object]],
    *,
    max_data_rows: int = 1000,
) -> None:
    """各列の dropdown を ``max_data_rows`` 行分まで設定."""
    last_row = 1 + max_data_rows  # ヘッダー行を除外
    for col_idx, col_def in enumerate(columns, start=1):
        dropdown = col_def.get("dropdown")
        if dropdown is None:
            continue
        # openpyxl の DataValidation list 値は "値1,値2,..." をダブルクォートで囲む.
        # 値内にカンマが含まれない前提 (今回は全て安全な値).
        formula = '"' + ",".join(str(v) for v in dropdown) + '"'  # type: ignore[arg-type]
        dv = DataValidation(type="list", formula1=formula, allow_blank=True)
        dv.error = "リストにない値です"
        dv.errorTitle = "入力エラー"
        col_letter = get_column_letter(col_idx)
        dv.add(f"{col_letter}2:{col_letter}{last_row}")
        ws.add_data_validation(dv)


def _shade_id_column_data_rows(
    ws: Worksheet,
    col_key: str,
    columns: list[dict[str, object]],
    *,
    data_row_count: int,
) -> None:
    """staff_id 列の **データ行のみ** を薄いグレー背景でハイライト.

    無条件で固定行数 (例: 1000) を塗ると ``ws.max_row`` が 1000 を返してしまい、
    テンプレート判定 (空 = max_row==1) ができなくなる。データ行が 0 件のときは
    何もしない。
    """
    if data_row_count <= 0:
        return
    target_index = None
    for i, col_def in enumerate(columns, start=1):
        if col_def["key"] == col_key:
            target_index = i
            break
    if target_index is None:
        return
    fill = PatternFill("solid", fgColor=ID_COLUMN_FILL_COLOR)
    col_letter = get_column_letter(target_index)
    for row in range(2, 2 + data_row_count):
        ws[f"{col_letter}{row}"].fill = fill


def _write_staff_row(
    ws: Worksheet,
    row_idx: int,
    staff: Staff,
    *,
    office_code_by_id: dict[UUID, str],
    secondary_office_ids_by_staff: dict[UUID, list[UUID]] | None = None,
) -> None:
    """1 行分のスタッフデータを書き込む."""
    code = ""
    if staff.primary_office_id is not None:
        code = office_code_by_id.get(staff.primary_office_id, "")
    # Phase E-7 (gap P0-2): secondary_offices → comma-separated コード列.
    # 解決できない id は silent skip (export 時点で alive offices のみ load される前提).
    # Phase E-7 (gap LOW#5): コード辞書順に sort して deterministic にする.
    sec_codes: list[str] = []
    if secondary_office_ids_by_staff is not None:
        for off_id in secondary_office_ids_by_staff.get(staff.id, []):
            oc = office_code_by_id.get(off_id)
            if oc:
                sec_codes.append(oc)
    sec_codes.sort()
    sec_codes_str = ",".join(sec_codes) if sec_codes else None
    values: dict[str, object | None] = {
        "staff_id": str(staff.id),
        "staff_code": staff.code,
        "name": staff.name,
        "kana": staff.kana,
        "sex": staff.sex,
        "status": staff.status,
        "role": staff.role,
        "office_code": code or None,
        "secondary_office_codes": sec_codes_str,
        "is_trainee": "TRUE" if staff.is_trainee else "FALSE",
        "note": staff.note,
        "delete_flag": None,
    }
    for col_key, col_idx in STAFF_COL_INDEX.items():
        ws.cell(row=row_idx, column=col_idx + 1, value=values.get(col_key))


def _hhmm(t: time | None) -> str | None:
    if t is None:
        return None
    return f"{t.hour:02d}:{t.minute:02d}"


def _write_shift_row(
    ws: Worksheet,
    row_idx: int,
    shift: StaffShift,
    *,
    staff_lookup: dict[UUID, Staff],
) -> None:
    s = staff_lookup.get(shift.staff_id)
    values: dict[str, object | None] = {
        "staff_id": str(shift.staff_id),
        "staff_code": s.code if s else None,
        "staff_name": s.name if s else None,
        "weekday": WEEKDAY_INT_TO_LABEL.get(shift.weekday),
        "is_on": "TRUE" if shift.is_on else "FALSE",
        "start_time": _hhmm(shift.start_time),
        "end_time": _hhmm(shift.end_time),
        "delete_flag": None,
    }
    for col_key, col_idx in SHIFT_COL_INDEX.items():
        ws.cell(row=row_idx, column=col_idx + 1, value=values.get(col_key))


def _write_override_row(
    ws: Worksheet,
    row_idx: int,
    override: StaffWeeklyOverride,
    *,
    staff_lookup: dict[UUID, Staff],
) -> None:
    """Phase E-7 (gap P1): StaffWeeklyOverride を 1 行書き込む."""
    s = staff_lookup.get(override.staff_id)
    values: dict[str, object | None] = {
        "staff_id": str(override.staff_id),
        "staff_code": s.code if s else None,
        "staff_name": s.name if s else None,
        "iso_year": override.iso_year,
        "iso_week": override.iso_week,
        "weekday": WEEKDAY_INT_TO_LABEL.get(override.weekday),
        "override_type": override.override_type,
        "start_time": _hhmm(override.start_time),
        "end_time": _hhmm(override.end_time),
        "reason": override.reason,
        "delete_flag": None,
    }
    for col_key, col_idx in OVERRIDE_COL_INDEX.items():
        ws.cell(row=row_idx, column=col_idx + 1, value=values.get(col_key))


# ---------------------------------------------------------------------------
# public entrypoints
# ---------------------------------------------------------------------------


def build_workbook(
    *,
    staff_list: Sequence[Staff],
    shifts: Sequence[StaffShift],
    offices: Sequence[Office],
    secondary_office_ids_by_staff: dict[UUID, list[UUID]] | None = None,
    overrides: Sequence[StaffWeeklyOverride] | None = None,
) -> Workbook:
    """テンプレート + データ込みのワークブックを構築する.

    ``staff_list`` を空 list で呼べばテンプレート (ヘッダー + dropdown のみ) になる.

    Phase E-7 (gap P0-2, P1):
      * ``secondary_office_ids_by_staff`` : Staff.id → [Office.id, ...] のマップ.
        staff.secondary_offices relationship を呼び出し側で precompute して渡す
        (lazy-load を async session で叩けないため).
      * ``overrides`` : StaffWeeklyOverride 一覧. 「勤務例外」シートに書き出す.
        None もしくは空 list の場合はヘッダーのみの空シートが出力される.
    """
    wb = Workbook()
    # default のシートを 1 枚目として使う
    ws_s: Worksheet = wb.active  # type: ignore[assignment]
    ws_s.title = SHEET_STAFF

    _set_header_row(ws_s, STAFF_COLUMNS)
    _attach_dropdowns(ws_s, STAFF_COLUMNS)

    office_code_by_id: dict[UUID, str] = {
        office.id: (office.code or "")
        for office in offices
        if office.code  # コード未設定の拠点はスキップ
    }
    for i, staff in enumerate(staff_list, start=2):
        _write_staff_row(
            ws_s,
            i,
            staff,
            office_code_by_id=office_code_by_id,
            secondary_office_ids_by_staff=secondary_office_ids_by_staff,
        )
    _shade_id_column_data_rows(ws_s, "staff_id", STAFF_COLUMNS, data_row_count=len(staff_list))

    # 勤務シフトシート
    ws_f: Worksheet = wb.create_sheet(title=SHEET_SHIFT)
    _set_header_row(ws_f, SHIFT_COLUMNS)
    _attach_dropdowns(ws_f, SHIFT_COLUMNS)

    staff_lookup: dict[UUID, Staff] = {s.id: s for s in staff_list}
    for i, shift in enumerate(shifts, start=2):
        _write_shift_row(ws_f, i, shift, staff_lookup=staff_lookup)
    _shade_id_column_data_rows(ws_f, "staff_id", SHIFT_COLUMNS, data_row_count=len(shifts))

    # Phase E-7 (gap P1): 勤務例外シート (StaffWeeklyOverride).
    ws_o: Worksheet = wb.create_sheet(title=SHEET_OVERRIDE)
    _set_header_row(ws_o, OVERRIDE_COLUMNS)
    _attach_dropdowns(ws_o, OVERRIDE_COLUMNS)
    override_list = list(overrides or [])
    for i, ov in enumerate(override_list, start=2):
        _write_override_row(ws_o, i, ov, staff_lookup=staff_lookup)
    _shade_id_column_data_rows(
        ws_o, "staff_id", OVERRIDE_COLUMNS, data_row_count=len(override_list)
    )

    return wb


def workbook_to_bytes(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
