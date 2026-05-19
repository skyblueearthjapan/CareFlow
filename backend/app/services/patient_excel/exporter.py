"""Excel 出力 (テンプレート / 全件エクスポート).

openpyxl で 2 シート構成のワークブックを組み立てる:

  1. 患者マスタシート
  2. 固定訪問スケジュールシート (PFV)

呼び出し側 (API endpoint) は ``build_workbook`` の戻り値である ``Workbook``
を ``save`` するか ``BytesIO`` にダンプしてレスポンスする.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import time
from io import BytesIO
from uuid import UUID

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.patient_excel.schema import (
    COMMENT_AUTHOR,
    DEFAULT_TIME_TYPE,
    HEADER_FILL_COLOR,
    HEADER_FONT_COLOR,
    ID_COLUMN_FILL_COLOR,
    ID_COLUMN_FONT_COLOR,
    PATIENT_COL_INDEX,
    PATIENT_COLUMNS,
    PATIENT_ID_COMMENT_TEXT,
    PFV_COL_INDEX,
    PFV_COLUMNS,
    PFV_PATIENT_ID_COMMENT_TEXT,
    SHEET_PATIENTS,
    SHEET_PFV,
    SHEET_WEEKLY,
    WEEKDAY_INT_TO_LABEL,
    WEEKLY_COLUMNS,
)

logger = logging.getLogger(__name__)

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


def _column_index(col_key: str, columns: list[dict[str, object]]) -> int | None:
    """1-indexed のカラム位置を返す (見つからなければ None)."""
    for i, col_def in enumerate(columns, start=1):
        if col_def["key"] == col_key:
            return i
    return None


def _shade_id_column_data_rows(
    ws: Worksheet,
    col_key: str,
    columns: list[dict[str, object]],
    *,
    data_row_count: int,
) -> None:
    """patient_id 列の **データ行のみ** に「触らない雰囲気」装飾を適用.

    薄いグレー背景 + イタリック + やや薄い色のフォントで、ユーザーに
    「ここは編集不要 (新規登録時は空欄)」を視覚的に伝える。

    無条件で固定行数 (例: 1000) を塗ると ``ws.max_row`` が 1000 を返してしまい、
    テンプレート判定 (空 = max_row==1) ができなくなる。データ行が 0 件のときは
    何もしない。
    """
    if data_row_count <= 0:
        return
    target_index = _column_index(col_key, columns)
    if target_index is None:
        return
    fill = PatternFill("solid", fgColor=ID_COLUMN_FILL_COLOR)
    font = Font(italic=True, color=ID_COLUMN_FONT_COLOR)
    col_letter = get_column_letter(target_index)
    for row in range(2, 2 + data_row_count):
        cell = ws[f"{col_letter}{row}"]
        cell.fill = fill
        cell.font = font


def _attach_header_comment(
    ws: Worksheet,
    col_key: str,
    columns: list[dict[str, object]],
    *,
    text: str,
) -> None:
    """ヘッダー (1 行目) の指定カラムにコメント (ホバーで表示) を付ける."""
    target_index = _column_index(col_key, columns)
    if target_index is None:
        return
    col_letter = get_column_letter(target_index)
    cell = ws[f"{col_letter}1"]
    # openpyxl の Comment(text, author) は幅/高さを明示しないとデフォルトが小さい
    comment = Comment(text, COMMENT_AUTHOR)
    comment.width = 280
    comment.height = 80
    cell.comment = comment


def _write_patient_row(
    ws: Worksheet,
    row_idx: int,
    patient: Patient,
    *,
    office_code_by_id: dict[UUID, str],
) -> None:
    """1 行分の患者データを書き込む.

    primary_office_id → 拠点コード (INAGE/TSUGA) の lookup は呼び出し側で構築済みの
    ``office_code_by_id`` を使う.
    """
    code = ""
    if patient.primary_office_id is not None:
        code = office_code_by_id.get(patient.primary_office_id, "")
    values: dict[str, object | None] = {
        "patient_id": str(patient.id),
        "patient_code": patient.code,
        "name": patient.name,
        "kana": patient.kana,
        "sex": patient.sex,
        "status": patient.status,
        "insurance": patient.insurance,
        "address": patient.address,
        # Numeric → 文字列化を避けて数値のまま書く. None はそのまま空セル.
        "lat": float(patient.lat) if patient.lat is not None else None,
        "lng": float(patient.lng) if patient.lng is not None else None,
        "office_code": code or None,
        "sex_restriction": patient.sex_restriction,
        # Phase E-7 (gap P0-1): requires_multiple_staff を TRUE/FALSE で書き出す.
        # NOT NULL bool 列なのでデフォルト False で必ず値が入る.
        "requires_multiple_staff": "TRUE" if patient.requires_multiple_staff else "FALSE",
        "note": patient.note,
        "delete_flag": None,
    }
    for col_key, col_idx in PATIENT_COL_INDEX.items():
        ws.cell(row=row_idx, column=col_idx + 1, value=values.get(col_key))


def _hhmm(t: time | None) -> str | None:
    if t is None:
        return None
    return f"{t.hour:02d}:{t.minute:02d}"


def _end_time(start: time, duration_min: int) -> str:
    total = start.hour * 60 + start.minute + duration_min
    h = (total // 60) % 24
    m = total % 60
    return f"{h:02d}:{m:02d}"


def _write_pfv_row(
    ws: Worksheet,
    row_idx: int,
    pfv: PatientFixedVisit,
    *,
    patient_lookup: dict[UUID, Patient],
    course_template_by_id: dict[UUID, CourseTemplate],
    office_code_by_id: dict[UUID, str] | None = None,
    crossoffice_warnings: list[str] | None = None,
) -> None:
    p = patient_lookup.get(pfv.patient_id)
    # course_template_code は患者の primary_office に存在する CourseTemplate のラベルのみ
    # 書き出す。クロスオフィス参照 (PFV.course_template が患者拠点に存在しない
    # template を指している場合) は round-trip import で
    # 「course_template_code が患者拠点に存在しません」エラーになるため、
    # その場合は空欄として書き出す (バックアップ運用での再 import を 0 エラーで通すため).
    code_label: str | None = None
    if pfv.course_template_id is not None:
        ct = course_template_by_id.get(pfv.course_template_id)
        if ct is not None and ct.label:
            # 患者拠点と template 拠点が一致 → ラベルを書き出す.
            # 不一致 (データ不整合) → 空欄. import 時に course_template_id=None で
            # 取り込まれる (PFV 自体は保持される).
            if p is not None and p.primary_office_id == ct.office_id:
                code_label = ct.label
            else:
                # Phase E-7 (gap P2): クロスオフィス参照を warning として収集.
                # サイレントに空欄化すると import で course_template binding が
                # 喪失することに気付けないため、export 時点で warning を残す.
                msg = (
                    f"PFV クロスオフィス course_template 参照 (row={row_idx}): "
                    f"patient_id={pfv.patient_id} primary_office={p.primary_office_id if p else None}, "
                    f"template_office={ct.office_id} ({ct.label}) — Excel は空欄化, "
                    "import で binding が喪失します"
                )
                logger.warning(msg)
                if crossoffice_warnings is not None:
                    crossoffice_warnings.append(msg)
    start_hhmm = _hhmm(pfv.start_time) or ""
    end_hhmm = _end_time(pfv.start_time, pfv.duration_min) if pfv.start_time else ""
    # time_type は patient.weekly_pattern から導く。該当エントリが無い場合は
    # default "時間帯" を書き出す (空セルだと import 時に「time_type が空です」
    # エラーになるため. round-trip 運用で 0 エラーを担保).
    resolved_tt = _resolve_time_type(p, pfv.weekday) if p else None
    # Phase E-5: sub_office_id → コードに変換 (lookup が無ければ空欄).
    sub_office_code: str | None = None
    if pfv.sub_office_id is not None and office_code_by_id is not None:
        sub_office_code = office_code_by_id.get(pfv.sub_office_id) or None
    values: dict[str, object | None] = {
        "patient_id": str(pfv.patient_id),
        "patient_code": p.code if p else None,
        "patient_name": p.name if p else None,
        "weekday": WEEKDAY_INT_TO_LABEL.get(pfv.weekday),
        "slot_index": pfv.slot_index,
        "mode": pfv.mode,
        "time_type": resolved_tt or DEFAULT_TIME_TYPE,
        "start_time": start_hhmm,
        "end_time": end_hhmm,
        "duration_min": pfv.duration_min,
        "course_template_code": code_label,
        "sub_office_code": sub_office_code,
        "delete_flag": None,
    }
    for col_key, col_idx in PFV_COL_INDEX.items():
        ws.cell(row=row_idx, column=col_idx + 1, value=values.get(col_key))


def _write_weekly_row(ws: Worksheet, row_idx: int, patient: Patient) -> None:
    """Phase E-8: patient.weekly_pattern を 「希望訪問パターン」シートの 1 行に展開.

    patient.weekly_pattern は JSONB で、WeeklyPatternV2 schema の dict (or None).
    None の場合は patient_id / patient_code / patient_name のみ書き出し、他は空欄.
    """
    wp = patient.weekly_pattern or {}
    if not isinstance(wp, dict):
        wp = {}

    # 希望曜日: list[str] | None → 7 bool に展開
    pref_weekdays = wp.get("preferred_weekdays") or []
    if not isinstance(pref_weekdays, list):
        pref_weekdays = []
    pref_set = {str(w) for w in pref_weekdays}

    def _wd_flag(wd_en: str) -> str:
        return "TRUE" if wd_en in pref_set else "FALSE"

    ws.cell(row=row_idx, column=1, value=str(patient.id))
    ws.cell(row=row_idx, column=2, value=patient.code or "")
    ws.cell(row=row_idx, column=3, value=patient.name or "")
    ws.cell(row=row_idx, column=4, value=wp.get("frequency_per_week"))
    ws.cell(row=row_idx, column=5, value=wp.get("visit_frequency"))
    ws.cell(row=row_idx, column=6, value=wp.get("visit_weeks"))
    # 希望曜日 月-日 (7 列, col 7-13)
    for i, wd_en in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
        ws.cell(row=row_idx, column=7 + i, value=_wd_flag(wd_en))
    ws.cell(row=row_idx, column=14, value=wp.get("service_minutes"))
    ws.cell(row=row_idx, column=15, value=wp.get("time_type"))
    ws.cell(row=row_idx, column=16, value=wp.get("preferred_start"))
    ws.cell(row=row_idx, column=17, value=wp.get("preferred_end"))
    # delete_flag: col 18 (空)


def _resolve_time_type(patient: Patient, weekday: int) -> str | None:
    """patient.weekly_pattern.entries[].weekday と一致するエントリの time_type を返す.

    weekly_pattern が辞書で entries が無い場合は patient.weekly_pattern.time_type を返す.
    どちらも無い場合は ``None``.
    """
    wp = patient.weekly_pattern
    if not isinstance(wp, dict):
        return None
    short = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[weekday]
    entries = wp.get("entries") or []
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("weekday") == short:
                tt = entry.get("time_type")
                if isinstance(tt, str):
                    return tt
    tt_root = wp.get("time_type")
    return tt_root if isinstance(tt_root, str) else None


# ---------------------------------------------------------------------------
# public entrypoints
# ---------------------------------------------------------------------------


def build_workbook(
    *,
    patients: Sequence[Patient],
    pfvs: Sequence[PatientFixedVisit],
    offices: Sequence[Office],
    course_templates: Sequence[CourseTemplate],
    crossoffice_warnings_out: list[str] | None = None,
) -> Workbook:
    """テンプレート + データ込みのワークブックを構築する.

    ``patients`` を空 list で呼べばテンプレート (ヘッダー + dropdown のみ) になる.

    Phase E-7 (gap P2): ``crossoffice_warnings_out`` が渡された場合、PFV のクロス
    オフィス course_template 参照 (患者拠点と template 拠点が一致しない参照) の
    warning メッセージを append する. 呼び出し側 (API endpoint) は件数を response
    header (X-Excel-Crossoffice-Warnings-Count) として返し、operator が export 結果
    から気付けるようにする.
    """
    wb = Workbook()
    # default のシートを 1 枚目として使う
    ws_p: Worksheet = wb.active  # type: ignore[assignment]
    ws_p.title = SHEET_PATIENTS

    _set_header_row(ws_p, PATIENT_COLUMNS)
    _attach_dropdowns(ws_p, PATIENT_COLUMNS)
    _attach_header_comment(ws_p, "patient_id", PATIENT_COLUMNS, text=PATIENT_ID_COMMENT_TEXT)

    office_code_by_id: dict[UUID, str] = {
        office.id: (office.code or "")
        for office in offices
        if office.code  # コード未設定の拠点はスキップ
    }
    for i, patient in enumerate(patients, start=2):
        _write_patient_row(ws_p, i, patient, office_code_by_id=office_code_by_id)
    _shade_id_column_data_rows(ws_p, "patient_id", PATIENT_COLUMNS, data_row_count=len(patients))

    # PFV シート
    ws_f: Worksheet = wb.create_sheet(title=SHEET_PFV)
    _set_header_row(ws_f, PFV_COLUMNS)
    _attach_dropdowns(ws_f, PFV_COLUMNS)
    _attach_header_comment(ws_f, "patient_id", PFV_COLUMNS, text=PFV_PATIENT_ID_COMMENT_TEXT)

    patient_lookup: dict[UUID, Patient] = {p.id: p for p in patients}
    # PFV の course_template_id → CourseTemplate (office_id 付き) の lookup。
    # _write_pfv_row 内で「patient.primary_office と template.office_id が一致するか」を
    # 確認し、不一致 (クロスオフィス参照) なら label を書き出さない。
    course_template_by_id: dict[UUID, CourseTemplate] = {ct.id: ct for ct in course_templates}
    # Phase E-7 (gap P2): クロスオフィス参照 warning 収集用. build_workbook 自身は
    # warning を返さないが、logger.warning で必ず出力される. ``crossoffice_warnings_out``
    # を呼び出し側が渡した場合はそこにも append し、API endpoint から response header に
    # 件数を出して operator が気付けるようにする.
    crossoffice_warnings: list[str] = (
        crossoffice_warnings_out if crossoffice_warnings_out is not None else []
    )
    for i, pfv in enumerate(pfvs, start=2):
        _write_pfv_row(
            ws_f,
            i,
            pfv,
            patient_lookup=patient_lookup,
            course_template_by_id=course_template_by_id,
            # Phase E-5: sub_office_id → コード解決用 (患者シートと共用).
            office_code_by_id=office_code_by_id,
            crossoffice_warnings=crossoffice_warnings,
        )
    _shade_id_column_data_rows(ws_f, "patient_id", PFV_COLUMNS, data_row_count=len(pfvs))

    if crossoffice_warnings:
        logger.warning(
            "patient_excel export: クロスオフィス course_template 参照を %d 件 "
            "空欄化しました. 再 import 時に course_template binding が喪失します.",
            len(crossoffice_warnings),
        )

    # Phase E-8: 希望訪問パターン シート (1 patient = 1 行).
    # patient.weekly_pattern を読み出して書き出す.
    ws_w: Worksheet = wb.create_sheet(title=SHEET_WEEKLY)
    _set_header_row(ws_w, WEEKLY_COLUMNS)
    _attach_dropdowns(ws_w, WEEKLY_COLUMNS)
    _attach_header_comment(ws_w, "patient_id", WEEKLY_COLUMNS, text=PATIENT_ID_COMMENT_TEXT)
    for i, patient in enumerate(patients, start=2):
        _write_weekly_row(ws_w, i, patient)
    _shade_id_column_data_rows(ws_w, "patient_id", WEEKLY_COLUMNS, data_row_count=len(patients))

    return wb


def workbook_to_bytes(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
