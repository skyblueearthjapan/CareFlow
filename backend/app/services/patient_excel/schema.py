"""Excel sheet definitions for patient master import / export.

シート構造 / 列定義 / dropdown 値リストを一元管理する.

# 注意: DB スキーマとの整合

仕様書には ``age`` / ``required_staff_count`` / ``ng_time_start`` / ``ng_time_end``
/ ``area`` の 5 列が含まれているが、これらは W1-BE1 (v2 patient master cleanup) で
``patients`` テーブルから物理的に削除済み。

本実装では:
  * これらの列を Excel テンプレート / export には **含めない**
    (export しても常に空で、import しても保存先が無く誤解を招くため)。
  * 仕様書の他の必須項目は全て保持する。
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Magic words
# ---------------------------------------------------------------------------

# 「変更しない」(空セル) は値として表現せずに「セルが空」で判断する.
# 以下 2 つは入力値として現れたときに特殊扱いされるマーカー.

MAGIC_DELETE: Final = "<DELETE>"
MAGIC_CLEAR: Final = "<CLEAR>"


def is_magic_delete(value: object) -> bool:
    return isinstance(value, str) and value.strip().upper() == MAGIC_DELETE


def is_magic_clear(value: object) -> bool:
    return isinstance(value, str) and value.strip().upper() == MAGIC_CLEAR


# ---------------------------------------------------------------------------
# Dropdown 値リスト
# ---------------------------------------------------------------------------

SEX_VALUES: Final[tuple[str, ...]] = ("male", "female", "unknown")
STATUS_VALUES: Final[tuple[str, ...]] = (
    "active",
    "suspended",
    "admitted",
    "pending",
    "cancelled",
)
INSURANCE_VALUES: Final[tuple[str, ...]] = ("medical", "care")
SEX_RESTRICTION_VALUES: Final[tuple[str, ...]] = ("female_only", "male_only")
OFFICE_CODE_VALUES: Final[tuple[str, ...]] = ("INAGE", "TSUGA")
# Phase E-7: requires_multiple_staff (Patient W18 Phase A-1 列) を Excel で扱うため.
BOOL_VALUES: Final[tuple[str, ...]] = ("TRUE", "FALSE")
# 仕様書: 1〜3 だが現状の DB スキーマには列が無い (W1-BE1 で削除済み).
# 互換のため Excel 上の dropdown 値のみ定義 (実 import では列が無いので未使用).

WEEKDAY_LABELS: Final[tuple[str, ...]] = ("月", "火", "水", "木", "金", "土", "日")
WEEKDAY_LABEL_TO_INT: Final[dict[str, int]] = {label: i for i, label in enumerate(WEEKDAY_LABELS)}
WEEKDAY_INT_TO_LABEL: Final[dict[int, str]] = {
    i: label for label, i in WEEKDAY_LABEL_TO_INT.items()
}

TIME_TYPE_VALUES: Final[tuple[str, ...]] = ("固定", "時間帯", "午前", "午後", "終日")
# Export / import 時に time_type を解決できない (patient.weekly_pattern にエントリ無し /
# 空セル) の場合に fallback として使うデフォルト値。
# "時間帯" は一般的な訪問形態を表す中立な値で、後から UI で個別に変更できる。
# round-trip 運用 (export → そのまま import) で「time_type が空です」エラーを
# 出さないために必要 (E-4 改修).
DEFAULT_TIME_TYPE: Final[str] = "時間帯"
COURSE_TEMPLATE_CODES: Final[tuple[str, ...]] = ("A", "B", "C", "D", "E", "M")
PFV_MODE_VALUES: Final[tuple[str, ...]] = ("normal", "special")

DELETE_FLAG_VALUES: Final[tuple[str, ...]] = (MAGIC_DELETE,)


# ---------------------------------------------------------------------------
# シート 1: 患者マスタ
# ---------------------------------------------------------------------------

SHEET_PATIENTS: Final = "患者マスタ"
SHEET_PFV: Final = "固定訪問スケジュール"

# 列定義: (key, header, width, dropdown_values).
# key は内部識別子. header は実際の Excel ヘッダー文字列.
# dropdown_values が None でない場合、その列に DataValidation (list) を設定.
PATIENT_COLUMNS: Final[list[dict[str, object]]] = [
    {"key": "patient_id", "header": "patient_id (※新規時は空欄)", "width": 38, "dropdown": None},
    {"key": "patient_code", "header": "patient_code", "width": 14, "dropdown": None},
    {"key": "name", "header": "患者名", "width": 18, "dropdown": None},
    {"key": "kana", "header": "フリガナ", "width": 18, "dropdown": None},
    {"key": "sex", "header": "性別", "width": 10, "dropdown": SEX_VALUES},
    {"key": "status", "header": "ステータス", "width": 12, "dropdown": STATUS_VALUES},
    {"key": "insurance", "header": "保険区分", "width": 10, "dropdown": INSURANCE_VALUES},
    {"key": "address", "header": "住所", "width": 32, "dropdown": None},
    {"key": "lat", "header": "緯度", "width": 12, "dropdown": None},
    {"key": "lng", "header": "経度", "width": 12, "dropdown": None},
    {"key": "office_code", "header": "拠点コード", "width": 12, "dropdown": OFFICE_CODE_VALUES},
    {
        "key": "sex_restriction",
        "header": "性別制限",
        "width": 12,
        "dropdown": SEX_RESTRICTION_VALUES,
    },
    # Phase E-7 (gap P0-1): W18 Phase A-1 で追加された Patient.requires_multiple_staff
    # を Excel 入出力で扱えるようにする. 空セル = 維持 (既存) / False (新規) の
    # 慣習は importer / replace_all 側で実装.
    {
        "key": "requires_multiple_staff",
        "header": "複数スタッフ必須",
        "width": 16,
        "dropdown": BOOL_VALUES,
    },
    {"key": "note", "header": "備考", "width": 30, "dropdown": None},
    {"key": "delete_flag", "header": "(削除フラグ)", "width": 14, "dropdown": DELETE_FLAG_VALUES},
]

# index lookup
PATIENT_COL_INDEX: Final[dict[str, int]] = {
    str(col["key"]): i for i, col in enumerate(PATIENT_COLUMNS)
}

# 必須項目 (新規作成時): 仕様書 §1
PATIENT_REQUIRED_ON_NEW: Final[tuple[str, ...]] = (
    "patient_code",
    "name",
    "sex",
    "status",
    "address",
)


# ---------------------------------------------------------------------------
# シート 2: 固定訪問スケジュール (PFV)
# ---------------------------------------------------------------------------

PFV_COLUMNS: Final[list[dict[str, object]]] = [
    {
        "key": "patient_id",
        "header": "patient_id (※新規時は空欄、code 入力可)",
        "width": 42,
        "dropdown": None,
    },
    {"key": "patient_code", "header": "patient_code", "width": 14, "dropdown": None},
    {"key": "patient_name", "header": "患者名", "width": 18, "dropdown": None},
    {"key": "weekday", "header": "曜日", "width": 8, "dropdown": WEEKDAY_LABELS},
    {"key": "slot_index", "header": "slot_index", "width": 10, "dropdown": None},
    {"key": "mode", "header": "モード", "width": 10, "dropdown": PFV_MODE_VALUES},
    {"key": "time_type", "header": "時間タイプ", "width": 12, "dropdown": TIME_TYPE_VALUES},
    {"key": "start_time", "header": "開始時刻", "width": 12, "dropdown": None},
    {"key": "end_time", "header": "終了時刻", "width": 12, "dropdown": None},
    {"key": "duration_min", "header": "duration_min", "width": 14, "dropdown": None},
    {
        "key": "course_template_code",
        "header": "course_template_code",
        "width": 22,
        "dropdown": COURSE_TEMPLATE_CODES,
    },
    # Phase E-5 (項目 ⑥B): サブ拠点コード (任意).
    # 主担当拠点 (患者マスタの office_code) と別に、フォロー時の配置先 PFV を
    # 1 行単位で指定するためのコード. 空欄なら従来どおり主担当のみで動作.
    {
        "key": "sub_office_code",
        "header": "sub_office_code",
        "width": 16,
        "dropdown": OFFICE_CODE_VALUES,
    },
    {"key": "delete_flag", "header": "(削除フラグ)", "width": 14, "dropdown": DELETE_FLAG_VALUES},
]

PFV_COL_INDEX: Final[dict[str, int]] = {str(col["key"]): i for i, col in enumerate(PFV_COLUMNS)}

# 必須項目: patient_id, weekday, slot_index, time_type, start_time
PFV_REQUIRED: Final[tuple[str, ...]] = (
    "patient_id",
    "weekday",
    "slot_index",
    "time_type",
    "start_time",
)


# ---------------------------------------------------------------------------
# Styling constants
# ---------------------------------------------------------------------------

HEADER_FILL_COLOR: Final = "FF4472C4"  # 濃い青
HEADER_FONT_COLOR: Final = "FFFFFFFF"  # 白
ID_COLUMN_FILL_COLOR: Final = "FFEEEEEE"  # 薄いグレー (参照用 patient_id 列)
ID_COLUMN_FONT_COLOR: Final = "FF666666"  # 中間グレー (新規時に触らない雰囲気)

# patient_id 列のヘッダーセルに添えるコメント (新規登録時のガイダンス)。
PATIENT_ID_COMMENT_TEXT: Final = (
    "新規登録時はこの列を空欄のままにしてください。\nシステムが自動的に UUID を発番します。"
)
PFV_PATIENT_ID_COMMENT_TEXT: Final = (
    "新規患者の PFV を登録する場合: この列を空欄にし、"
    "patient_code 列に患者コードを入力してください。\n"
    "同 import 内の新規患者と自動で紐付きます。"
)
COMMENT_AUTHOR: Final = "システム"
