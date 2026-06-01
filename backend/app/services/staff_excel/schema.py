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
# Phase E-7 (gap P1): StaffShift / StaffWeeklyOverride を扱う旧 per-row シート名.
# Phase G-56 で「編集用」(1 スタッフ 1 行) シートへ移行したが、旧 export ファイル
# (per-row 形式) を import できるよう後方互換のためシート名・列定義を温存する.
SHEET_SHIFT: Final = "勤務シフト"
SHEET_OVERRIDE: Final = "勤務例外"

# Phase G-56: 「1 スタッフ 1 行」入力性向上シート.
#   * SHEET_SHIFT_EDIT     : 週次シフト (1 スタッフ 1 行, 曜日 7 × 開始/終了 2 列).
#   * SHEET_OVERRIDE_EDIT  : 勤務例外 (1 スタッフ × 1 週 = 1 行, 曜日 7 セル).
# 患者マスタ (Phase G-48) / 固定訪問パターン (Phase G-55) の患者 1 行化と同じ思想.
SHEET_SHIFT_EDIT: Final = "勤務シフト（編集用）"
SHEET_OVERRIDE_EDIT: Final = "勤務例外（編集用）"

# Phase E-7 (gap P1): StaffWeeklyOverride.override_type の dropdown 値.
OVERRIDE_TYPE_VALUES: Final[tuple[str, ...]] = ("off", "custom_time")

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
    # Phase E-7 (gap P0-2): staff.secondary_offices (StaffSecondaryOffice relationship)
    # を Excel で扱えるようにする. カンマ区切りの office.code 列挙
    # (例: "INAGE,TSUGA"). 空セル = 関連解除 (= 完全置換). dropdown は値が複数
    # 入る可能性があるため設定しない.
    {
        "key": "secondary_office_codes",
        "header": "サブ拠点コード (カンマ区切り, 解除は <CLEAR>)",
        "width": 32,
        "dropdown": None,
    },
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
# シート 3: 勤務例外 (StaffWeeklyOverride) — Phase E-7 (gap P1)
# ---------------------------------------------------------------------------
#
# StaffWeeklyOverride モデルの 1 行 1 レコード対応. UNIQUE key は
# (staff_id, iso_year, iso_week, weekday). UI で「特定週の指定日 = 休み or 時間変更」
# を表現する. Excel から往復させる事でバックアップ運用品質を担保する.

OVERRIDE_COLUMNS: Final[list[dict[str, object]]] = [
    {"key": "staff_id", "header": "staff_id", "width": 38, "dropdown": None},
    {"key": "staff_code", "header": "staff_code", "width": 14, "dropdown": None},
    {"key": "staff_name", "header": "staff_name", "width": 18, "dropdown": None},
    {"key": "iso_year", "header": "iso_year", "width": 10, "dropdown": None},
    {"key": "iso_week", "header": "iso_week", "width": 10, "dropdown": None},
    {"key": "weekday", "header": "曜日", "width": 8, "dropdown": WEEKDAY_LABELS},
    {
        "key": "override_type",
        "header": "種別",
        "width": 14,
        "dropdown": OVERRIDE_TYPE_VALUES,
    },
    {"key": "start_time", "header": "開始時刻", "width": 12, "dropdown": None},
    {"key": "end_time", "header": "終了時刻", "width": 12, "dropdown": None},
    {"key": "reason", "header": "理由", "width": 30, "dropdown": None},
    {"key": "delete_flag", "header": "(削除フラグ)", "width": 14, "dropdown": DELETE_FLAG_VALUES},
]

OVERRIDE_COL_INDEX: Final[dict[str, int]] = {
    str(col["key"]): i for i, col in enumerate(OVERRIDE_COLUMNS)
}


# ---------------------------------------------------------------------------
# Phase G-56: 「勤務シフト（編集用）」(1 スタッフ 1 行) — SHEET_SHIFT_EDIT
# ---------------------------------------------------------------------------
#
# 1 スタッフ = 1 行. 各曜日 (月..日) に開始/終了 2 列を持つ.
#   * 開始 & 終了 両方あり → is_on=True + その時刻.
#   * 両方空欄         → is_on=False (休).
# 行 1 つで「その staff の週次シフトの正本」を表す. importer は per-staff replace で
# 7 曜日すべてを行に一致させる (行に出ない staff は不変).
#
# 曜日キー (0=月 .. 6=日). 各キーに _start / _end 2 列を生成する.

SHIFT_EDIT_WEEKDAY_KEYS: Final[tuple[str, ...]] = (
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
)
# 曜日キー → DB weekday int (0=月..6=日). WEEKDAY_LABELS と同順.
SHIFT_EDIT_WEEKDAY_KEY_TO_INT: Final[dict[str, int]] = {
    key: i for i, key in enumerate(SHIFT_EDIT_WEEKDAY_KEYS)
}
SHIFT_EDIT_WEEKDAY_KEY_TO_LABEL: Final[dict[str, str]] = dict(
    zip(SHIFT_EDIT_WEEKDAY_KEYS, WEEKDAY_LABELS, strict=True)
)


def _shift_edit_columns() -> list[dict[str, object]]:
    cols: list[dict[str, object]] = [
        {
            "key": "staff_id",
            "header": "staff_id (※システム用・編集不要)",
            "width": 38,
            "dropdown": None,
        },
        {"key": "staff_code", "header": "staff_code", "width": 14, "dropdown": None},
        {"key": "staff_name", "header": "スタッフ名", "width": 18, "dropdown": None},
    ]
    for key, label in zip(SHIFT_EDIT_WEEKDAY_KEYS, WEEKDAY_LABELS, strict=True):
        cols.append(
            {"key": f"{key}_start", "header": f"{label}_開始", "width": 11, "dropdown": None}
        )
        cols.append({"key": f"{key}_end", "header": f"{label}_終了", "width": 11, "dropdown": None})
    cols.append(
        {
            "key": "delete_flag",
            "header": "(削除フラグ)",
            "width": 14,
            "dropdown": DELETE_FLAG_VALUES,
        }
    )
    return cols


SHIFT_EDIT_COLUMNS: Final[list[dict[str, object]]] = _shift_edit_columns()
SHIFT_EDIT_COL_INDEX: Final[dict[str, int]] = {
    str(col["key"]): i for i, col in enumerate(SHIFT_EDIT_COLUMNS)
}


# ---------------------------------------------------------------------------
# Phase G-56: 「勤務例外（編集用）」(1 スタッフ × 1 週 = 1 行) — SHEET_OVERRIDE_EDIT
# ---------------------------------------------------------------------------
#
# 1 (staff, iso_year, iso_week) = 1 行. 各曜日 (月..日) に 1 セルで例外内容を表記:
#   * "休"          → その曜日休 (override_type='off', 時刻なし).
#   * "HH:MM-HH:MM" → 時刻変更 (override_type='custom_time', start/end).
#   * 空欄          → 例外なし.
# importer は per-(staff, iso_year, iso_week) replace でその週の例外を行に一致させる.

OVERRIDE_OFF_TOKEN: Final = "休"

OVERRIDE_EDIT_WEEKDAY_KEYS: Final[tuple[str, ...]] = (
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
)
OVERRIDE_EDIT_WEEKDAY_KEY_TO_INT: Final[dict[str, int]] = {
    key: i for i, key in enumerate(OVERRIDE_EDIT_WEEKDAY_KEYS)
}
OVERRIDE_EDIT_WEEKDAY_KEY_TO_LABEL: Final[dict[str, str]] = dict(
    zip(OVERRIDE_EDIT_WEEKDAY_KEYS, WEEKDAY_LABELS, strict=True)
)


def _override_edit_columns() -> list[dict[str, object]]:
    cols: list[dict[str, object]] = [
        {
            "key": "staff_id",
            "header": "staff_id (※システム用・編集不要)",
            "width": 38,
            "dropdown": None,
        },
        {"key": "staff_code", "header": "staff_code", "width": 14, "dropdown": None},
        {"key": "staff_name", "header": "スタッフ名", "width": 18, "dropdown": None},
        {"key": "iso_year", "header": "iso_year", "width": 10, "dropdown": None},
        {"key": "iso_week", "header": "iso_week", "width": 10, "dropdown": None},
    ]
    for key, label in zip(OVERRIDE_EDIT_WEEKDAY_KEYS, WEEKDAY_LABELS, strict=True):
        cols.append({"key": key, "header": label, "width": 12, "dropdown": None})
    cols.append({"key": "reason", "header": "理由", "width": 30, "dropdown": None})
    cols.append(
        {
            "key": "delete_flag",
            "header": "(削除フラグ)",
            "width": 14,
            "dropdown": DELETE_FLAG_VALUES,
        }
    )
    return cols


OVERRIDE_EDIT_COLUMNS: Final[list[dict[str, object]]] = _override_edit_columns()
OVERRIDE_EDIT_COL_INDEX: Final[dict[str, int]] = {
    str(col["key"]): i for i, col in enumerate(OVERRIDE_EDIT_COLUMNS)
}


def format_override_cell(
    override_type: str | None,
    start_hhmm: str | None,
    end_hhmm: str | None,
) -> str | None:
    """(override_type, start, end) → 例外セル表記 ("休" / "HH:MM-HH:MM").

    解決できない場合 (override_type 不明 / custom_time なのに時刻欠落) は None.
    """
    if override_type == "off":
        return OVERRIDE_OFF_TOKEN
    if override_type == "custom_time":
        if start_hhmm and end_hhmm:
            return f"{start_hhmm}-{end_hhmm}"
        return None
    return None


def parse_override_cell(value: object) -> tuple[str, str | None, str | None] | None:
    """例外セル表記 → (override_type, start_hhmm, end_hhmm).

    * "休"          → ("off", None, None)
    * "HH:MM-HH:MM" → ("custom_time", "HH:MM", "HH:MM")
    解析できない / 空欄は None (= 例外なし). 不正表記は ValueError.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s == OVERRIDE_OFF_TOKEN or s.lower() == "off":
        return ("off", None, None)
    # "HH:MM-HH:MM" を許容. ハイフン (半角/全角/波ダッシュ) で分割.
    sep = None
    for ch in ("-", "−", "〜", "~", "ー"):
        if ch in s:
            sep = ch
            break
    if sep is None:
        raise ValueError(f"例外セルの表記が不正です (休 または HH:MM-HH:MM): {s!r}")
    parts = s.split(sep)
    if len(parts) != 2:
        raise ValueError(f"例外セルの表記が不正です (休 または HH:MM-HH:MM): {s!r}")
    start = _normalize_hhmm(parts[0].strip())
    end = _normalize_hhmm(parts[1].strip())
    return ("custom_time", start, end)


def _normalize_hhmm(s: str) -> str:
    parts = s.split(":")
    if len(parts) < 2:
        raise ValueError(f"HH:MM 形式ではありません: {s!r}")
    h = int(parts[0])
    m = int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"時刻の範囲外: {s!r}")
    return f"{h:02d}:{m:02d}"


# ---------------------------------------------------------------------------
# Styling constants
# ---------------------------------------------------------------------------

HEADER_FILL_COLOR: Final = "FF4472C4"  # 濃い青
HEADER_FONT_COLOR: Final = "FFFFFFFF"  # 白
ID_COLUMN_FILL_COLOR: Final = "FFEEEEEE"  # 薄いグレー (参照用 staff_id 列)
