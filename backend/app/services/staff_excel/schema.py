"""Excel sheet definitions for staff master import / export.

シート構造 / 列定義 / dropdown 値リストを一元管理する.

# 注意: DB スキーマとの整合

仕様書には ``自宅住所`` / ``自宅緯度`` / ``自宅経度`` / ``同行可能`` の 4 列が
含まれているが、これらは W1-BE2 (v2 staff master cleanup, migration 0010) で
``staff`` テーブルから物理的に削除済み (``home_address``/``home_lat``/
``home_lng``/``can_double_team``)。

本実装では:
  * これらの列を Excel テンプレート / export には **含めない**
    (export しても常に空で、import しても保存先が無く誤解を招くため)。
  * 仕様書の他の必須項目 (id, code, name, kana, sex, status, role,
    office_code, is_trainee, note, delete_flag) は全て保持する。
  * patient_excel の同等パターンに揃える。
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
# W1-BE2 (§4.2): 在籍 / 休職 / 退職 の 3 値.
STATUS_VALUES: Final[tuple[str, ...]] = ("active", "on_leave", "retired")
ROLE_VALUES: Final[tuple[str, ...]] = ("admin", "manager", "staff")
OFFICE_CODE_VALUES: Final[tuple[str, ...]] = ("INAGE", "TSUGA")
BOOL_VALUES: Final[tuple[str, ...]] = ("TRUE", "FALSE")

WEEKDAY_LABELS: Final[tuple[str, ...]] = ("月", "火", "水", "木", "金", "土", "日")
WEEKDAY_LABEL_TO_INT: Final[dict[str, int]] = {label: i for i, label in enumerate(WEEKDAY_LABELS)}
WEEKDAY_INT_TO_LABEL: Final[dict[int, str]] = {
    i: label for label, i in WEEKDAY_LABEL_TO_INT.items()
}

DELETE_FLAG_VALUES: Final[tuple[str, ...]] = (MAGIC_DELETE,)


# ---------------------------------------------------------------------------
# シート 1: スタッフマスタ
# ---------------------------------------------------------------------------

SHEET_STAFF: Final = "スタッフマスタ"
SHEET_SHIFT: Final = "勤務シフト"

# 列定義: (key, header, width, dropdown_values).
# key は内部識別子. header は実際の Excel ヘッダー文字列.
# dropdown_values が None でない場合、その列に DataValidation (list) を設定.
STAFF_COLUMNS: Final[list[dict[str, object]]] = [
    {"key": "staff_id", "header": "staff_id", "width": 38, "dropdown": None},
    {"key": "staff_code", "header": "staff_code", "width": 14, "dropdown": None},
    {"key": "name", "header": "スタッフ名", "width": 18, "dropdown": None},
    {"key": "kana", "header": "フリガナ", "width": 18, "dropdown": None},
    {"key": "sex", "header": "性別", "width": 10, "dropdown": SEX_VALUES},
    {"key": "status", "header": "ステータス", "width": 12, "dropdown": STATUS_VALUES},
    {"key": "role", "header": "ロール", "width": 12, "dropdown": ROLE_VALUES},
    {"key": "office_code", "header": "拠点コード", "width": 12, "dropdown": OFFICE_CODE_VALUES},
    {"key": "is_trainee", "header": "新人フラグ", "width": 12, "dropdown": BOOL_VALUES},
    {"key": "note", "header": "備考", "width": 30, "dropdown": None},
    {"key": "delete_flag", "header": "(削除フラグ)", "width": 14, "dropdown": DELETE_FLAG_VALUES},
]

STAFF_COL_INDEX: Final[dict[str, int]] = {str(col["key"]): i for i, col in enumerate(STAFF_COLUMNS)}

# 必須項目 (新規作成時): 仕様書 §1
STAFF_REQUIRED_ON_NEW: Final[tuple[str, ...]] = (
    "staff_code",
    "name",
    "status",
    "role",
)


# ---------------------------------------------------------------------------
# シート 2: 勤務シフト
# ---------------------------------------------------------------------------

SHIFT_COLUMNS: Final[list[dict[str, object]]] = [
    {"key": "staff_id", "header": "staff_id", "width": 38, "dropdown": None},
    {"key": "staff_code", "header": "staff_code", "width": 14, "dropdown": None},
    {"key": "staff_name", "header": "staff_name", "width": 18, "dropdown": None},
    {"key": "weekday", "header": "曜日", "width": 8, "dropdown": WEEKDAY_LABELS},
    {"key": "is_on", "header": "勤務", "width": 10, "dropdown": BOOL_VALUES},
    {"key": "start_time", "header": "開始時刻", "width": 12, "dropdown": None},
    {"key": "end_time", "header": "終了時刻", "width": 12, "dropdown": None},
    {"key": "delete_flag", "header": "(削除フラグ)", "width": 14, "dropdown": DELETE_FLAG_VALUES},
]

SHIFT_COL_INDEX: Final[dict[str, int]] = {str(col["key"]): i for i, col in enumerate(SHIFT_COLUMNS)}


# ---------------------------------------------------------------------------
# Styling constants
# ---------------------------------------------------------------------------

HEADER_FILL_COLOR: Final = "FF4472C4"  # 濃い青
HEADER_FONT_COLOR: Final = "FFFFFFFF"  # 白
ID_COLUMN_FILL_COLOR: Final = "FFEEEEEE"  # 薄いグレー (参照用 staff_id 列)
