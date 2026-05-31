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
#
# Phase G-48: 患者マスタの enum 値を「人が手編集しやすい」日本語表記へ作り替える.
# DB には従来どおり英語 enum を格納し、exporter は日本語で書き出し、importer は
# 日本語→英語へ変換して DB へ反映する. 後方互換のため importer は英語値も受理する.
#
# 各 enum について以下を定義する:
#   * ``*_VALUES``        : DB に格納される英語の正準値 (後方互換受理用).
#   * ``*_JA_VALUES``     : Excel の dropdown に出す日本語ラベル (export 出力値).
#   * ``*_EN_TO_JA``      : DB 英語値 → 日本語ラベル (export 時).
#   * ``*_JA_TO_EN``      : 日本語ラベル → DB 英語値 (import 時. 英語値も自己写像で受理).

SEX_VALUES: Final[tuple[str, ...]] = ("male", "female", "unknown")
SEX_EN_TO_JA: Final[dict[str, str]] = {
    "female": "女性",
    "male": "男性",
    "unknown": "不明",
}
SEX_JA_VALUES: Final[tuple[str, ...]] = ("女性", "男性", "不明")
SEX_JA_TO_EN: Final[dict[str, str]] = {ja: en for en, ja in SEX_EN_TO_JA.items()}

STATUS_VALUES: Final[tuple[str, ...]] = (
    "active",
    "suspended",
    "admitted",
    "pending",
    "cancelled",
)
STATUS_EN_TO_JA: Final[dict[str, str]] = {
    "active": "稼働",
    "suspended": "休止",
    "admitted": "入院",
    "pending": "未開始",
    "cancelled": "解約",
}
STATUS_JA_VALUES: Final[tuple[str, ...]] = ("稼働", "休止", "入院", "未開始", "解約")
STATUS_JA_TO_EN: Final[dict[str, str]] = {ja: en for en, ja in STATUS_EN_TO_JA.items()}

INSURANCE_VALUES: Final[tuple[str, ...]] = ("medical", "care")
INSURANCE_EN_TO_JA: Final[dict[str, str]] = {
    "medical": "医療保険",
    "care": "介護保険",
}
INSURANCE_JA_VALUES: Final[tuple[str, ...]] = ("医療保険", "介護保険")
INSURANCE_JA_TO_EN: Final[dict[str, str]] = {ja: en for en, ja in INSURANCE_EN_TO_JA.items()}

SEX_RESTRICTION_VALUES: Final[tuple[str, ...]] = ("female_only", "male_only")
# 性別制限の「なし」は DB では NULL (空). Excel dropdown では「なし」を明示表示する.
SEX_RESTRICTION_NONE_JA: Final = "なし"
SEX_RESTRICTION_EN_TO_JA: Final[dict[str, str]] = {
    "female_only": "女性のみ",
    "male_only": "男性のみ",
}
SEX_RESTRICTION_JA_VALUES: Final[tuple[str, ...]] = ("なし", "女性のみ", "男性のみ")
SEX_RESTRICTION_JA_TO_EN: Final[dict[str, str]] = {
    ja: en for en, ja in SEX_RESTRICTION_EN_TO_JA.items()
}

OFFICE_CODE_VALUES: Final[tuple[str, ...]] = ("INAGE", "TSUGA")
# Phase E-7: requires_multiple_staff (Patient W18 Phase A-1 列) を Excel で扱うため.
BOOL_VALUES: Final[tuple[str, ...]] = ("TRUE", "FALSE")
# Phase G-48: 複数スタッフ必須を「はい/いいえ」で表示. TRUE/FALSE も受理 (後方互換).
BOOL_JA_VALUES: Final[tuple[str, ...]] = ("はい", "いいえ")
BOOL_JA_TO_BOOL: Final[dict[str, bool]] = {"はい": True, "いいえ": False}

# Phase G-50: 希望曜日 7 列の選択肢を「〇/×」に (はい/いいえ より一覧で視認しやすい).
# 〇 = 選択, × = 非選択. import 側は 〇/はい/TRUE/1/yes を選択扱い (後方互換).
WEEKDAY_MARK_VALUES: Final[tuple[str, ...]] = ("〇", "×")

# ---------------------------------------------------------------------------
# Phase G-49: 入力性向上のための dropdown 値レンジ
# ---------------------------------------------------------------------------
#
# 週訪問回数: 1〜7 の整数 dropdown.
FREQUENCY_PER_WEEK_VALUES: Final[tuple[str, ...]] = tuple(str(n) for n in range(1, 8))
# サービス時間(分): 15〜180 を 5 分刻み. import は int として解釈される.
SERVICE_MINUTES_VALUES: Final[tuple[str, ...]] = tuple(str(n) for n in range(15, 181, 5))
# 希望開始/終了時刻: 06:00〜20:00 を 30 分刻みの "HH:MM" 文字列 (_read_hhmm が解釈可能).
HHMM_VALUES: Final[tuple[str, ...]] = tuple(
    f"{h:02d}:{m:02d}" for h in range(6, 21) for m in (0, 30)
)

WEEKDAY_LABELS: Final[tuple[str, ...]] = ("月", "火", "水", "木", "金", "土", "日")
WEEKDAY_LABEL_TO_INT: Final[dict[str, int]] = {label: i for i, label in enumerate(WEEKDAY_LABELS)}
WEEKDAY_INT_TO_LABEL: Final[dict[int, str]] = {
    i: label for label, i in WEEKDAY_LABEL_TO_INT.items()
}

TIME_TYPE_VALUES: Final[tuple[str, ...]] = ("固定", "時間帯", "午前", "午後", "終日")
# Phase G-49: これらの時間タイプは時刻 (開始/終了) を使わない. exporter は
# 希望開始/終了時刻セルを条件付き書式でグレーアウトして「不要」を視覚的に示す.
TIME_TYPE_GREYOUT_VALUES: Final[tuple[str, ...]] = ("午前", "午後", "終日")
# Export / import 時に time_type を解決できない (patient.weekly_pattern にエントリ無し /
# 空セル) の場合に fallback として使うデフォルト値。
# "時間帯" は一般的な訪問形態を表す中立な値で、後から UI で個別に変更できる。
# round-trip 運用 (export → そのまま import) で「time_type が空です」エラーを
# 出さないために必要 (E-4 改修).
DEFAULT_TIME_TYPE: Final[str] = "時間帯"
COURSE_TEMPLATE_CODES: Final[tuple[str, ...]] = ("A", "B", "C", "D", "E", "M")
PFV_MODE_VALUES: Final[tuple[str, ...]] = ("normal", "special")
# Phase G-48: PFV モードを日本語化 (通常/特別). 英語値も受理 (後方互換).
PFV_MODE_EN_TO_JA: Final[dict[str, str]] = {"normal": "通常", "special": "特別"}
PFV_MODE_JA_VALUES: Final[tuple[str, ...]] = ("通常", "特別")
PFV_MODE_JA_TO_EN: Final[dict[str, str]] = {ja: en for en, ja in PFV_MODE_EN_TO_JA.items()}

DELETE_FLAG_VALUES: Final[tuple[str, ...]] = (MAGIC_DELETE,)


# ---------------------------------------------------------------------------
# 訪問頻度 (weekly_pattern.visit_frequency) — VisitFrequencyV2 正準値との対応
# ---------------------------------------------------------------------------
#
# Phase G-48: schemas/v2/patient.py の ``VisitFrequencyV2 = Literal["every",
# "biweekly", "monthly"]`` が DB 格納の正準値. UI の「毎週/隔週/月次」と双方向
# マッピングする. exporter は DB 値 (every 等) → 日本語、importer は日本語 →
# DB 値へ変換する. 旧 export では日本語が直接格納されていた可能性があるため、
# importer は日本語ラベル / 英語正準値 / (旧バグで保存され得る) 日本語値の
# いずれも受理する.

VISIT_FREQUENCY_VALUES: Final[tuple[str, ...]] = ("every", "biweekly", "monthly")
VISIT_FREQUENCY_EN_TO_JA: Final[dict[str, str]] = {
    "every": "毎週",
    "biweekly": "隔週",
    "monthly": "月次",
}
VISIT_FREQUENCY_JA_VALUES: Final[tuple[str, ...]] = ("毎週", "隔週", "月次")
VISIT_FREQUENCY_JA_TO_EN: Final[dict[str, str]] = {
    ja: en for en, ja in VISIT_FREQUENCY_EN_TO_JA.items()
}


# ---------------------------------------------------------------------------
# 希望曜日 (weekly_pattern.preferred_weekdays) — 1 セルカンマ区切りの双方向変換
# ---------------------------------------------------------------------------
#
# 内部 (JSONB) は英短縮形 Mon..Sun の list. Excel では 1 セルに日本語カンマ区切り
# ("月,水,金") で表現する. 区切りは半角/全角カンマ・読点いずれも許容.

WEEKDAY_EN_LIST: Final[tuple[str, ...]] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_EN_TO_JA: Final[dict[str, str]] = dict(zip(WEEKDAY_EN_LIST, WEEKDAY_LABELS, strict=True))
WEEKDAY_JA_TO_EN: Final[dict[str, str]] = {ja: en for en, ja in WEEKDAY_EN_TO_JA.items()}
# import 時に許容する区切り文字 (半角カンマ / 全角カンマ / 読点).
WEEKDAY_SPLIT_CHARS: Final = (",", "，", "、")


def weekdays_en_to_cell(weekdays: list[str] | None) -> str:
    """preferred_weekdays (英 list) → "月,水,金" 形式の 1 セル文字列.

    Mon..Sun の正準順で並べ替えて出力する (round-trip 安定性のため).
    None / 空 list は空文字を返す.
    """
    if not weekdays:
        return ""
    present = {str(w) for w in weekdays}
    return ",".join(WEEKDAY_EN_TO_JA[en] for en in WEEKDAY_EN_LIST if en in present)


def weekdays_cell_to_en(value: str | None) -> list[str]:
    """ "月,水,金" / "Mon,Wed,Fri" 形式の 1 セル → preferred_weekdays (英 list).

    日本語ラベル・英短縮形いずれも受理 (後方互換). Mon..Sun の正準順で返す.
    """
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    # 全角/半角カンマ・読点を統一区切りに正規化.
    for ch in WEEKDAY_SPLIT_CHARS:
        text = text.replace(ch, ",")
    tokens = [t.strip() for t in text.split(",") if t.strip()]
    found: set[str] = set()
    for tok in tokens:
        if tok in WEEKDAY_JA_TO_EN:
            found.add(WEEKDAY_JA_TO_EN[tok])
        elif tok in WEEKDAY_EN_LIST:
            found.add(tok)
        # 候補外トークンは無視 (壊さない方針).
    return [en for en in WEEKDAY_EN_LIST if en in found]


def weekdays_en_to_yesno_cells(weekdays: list[str] | None) -> dict[str, str]:
    """Phase G-49/G-50: preferred_weekdays (英 list) → 7 列 (pref_wd_mon..) の 〇/×.

    希望されている曜日の列に「〇」、それ以外に「×」を入れた dict を返す.
    None / 空 list でも全列「×」を返す (round-trip 安定のため空欄にしない).
    """
    present = {str(w) for w in (weekdays or [])}
    return {key: ("〇" if en in present else "×") for key, en in PATIENT_WEEKDAY_KEY_TO_EN.items()}


def weekday_yesno_cells_to_en(cells: dict[str, object]) -> list[str]:
    """Phase G-49/G-50: 7 列 (pref_wd_mon..) の 〇/× → preferred_weekdays (英 list).

    「〇」(= 選択 / True 相当) の曜日のみを Mon..Sun の正準順で返す. 値の解釈は
    `_is_yes` に準拠し「〇/はい/TRUE/1/yes」を選択扱い (後方互換). ×/空/候補外は
    非選択扱い (壊さない方針).
    """
    found: set[str] = set()
    for key, en in PATIENT_WEEKDAY_KEY_TO_EN.items():
        if _is_yes(cells.get(key)):
            found.add(en)
    return [en for en in WEEKDAY_EN_LIST if en in found]


def _is_yes(value: object) -> bool:
    """セル値が「選択」(True) かを判定. 〇/はい/TRUE/1/yes を True とする.
    空/×/いいえ/候補外は False (後方互換のため はい も受理)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    s = str(value).strip()
    if s == "":
        return False
    if s in ("〇", "○", "はい"):  # 〇(U+3007) / ○(U+25CB) 両方許容 + 旧 はい
        return True
    return s.upper() in ("TRUE", "1", "YES", "Y")


# ---------------------------------------------------------------------------
# 希望曜日 7 列クリック式 (Phase G-49) — 月..日 を「はい/いいえ」dropdown に分割
# ---------------------------------------------------------------------------
#
# Phase G-48 までは 1 セルカンマ区切り ("月,水,金") だったが、入力性向上のため
# 月/火/水/木/金/土/日 の 7 列に分割し、各列を BOOL_JA (はい/いいえ) dropdown とする.
# 内部 (JSONB) の preferred_weekdays は従来どおり英短縮形 Mon..Sun の list.
#
# patient sheet 上の列キー: pref_wd_mon .. pref_wd_sun. weekly_pattern dict 内では
# preferred_weekdays (英 list) にマップする.

PATIENT_WEEKDAY_COL_KEYS: Final[tuple[str, ...]] = (
    "pref_wd_mon",
    "pref_wd_tue",
    "pref_wd_wed",
    "pref_wd_thu",
    "pref_wd_fri",
    "pref_wd_sat",
    "pref_wd_sun",
)
# 列キー → 英短縮形 (Mon..Sun). WEEKDAY_EN_LIST と同順.
PATIENT_WEEKDAY_KEY_TO_EN: Final[dict[str, str]] = dict(
    zip(PATIENT_WEEKDAY_COL_KEYS, WEEKDAY_EN_LIST, strict=True)
)
# 列キー → ヘッダー日本語ラベル (希望曜日_月 等).
PATIENT_WEEKDAY_KEY_TO_HEADER: Final[dict[str, str]] = {
    key: f"希望曜日_{label}"
    for key, label in zip(PATIENT_WEEKDAY_COL_KEYS, WEEKDAY_LABELS, strict=True)
}


# ---------------------------------------------------------------------------
# シート 1: 患者マスタ (Phase G-48 で希望訪問パターンを統合)
# ---------------------------------------------------------------------------

SHEET_PATIENTS: Final = "患者マスタ"
SHEET_PFV: Final = "固定訪問パターン"
# 旧シート名 (= Phase E-7 以前の export ファイルが持っている名前).
# importer / replace_all で wb[SHEET_PFV] が無い場合のフォールバックとして
# wb[LEGACY_SHEET_PFV] を読みに行く. 新規 export は常に SHEET_PFV で書き出す.
LEGACY_SHEET_PFV: Final = "固定訪問スケジュール"

# Phase G-48: 患者マスタシートに weekly_pattern (希望訪問パターン) を横一列で統合.
# 1 患者 = 1 行. 固定訪問パターン (PFV) は 1 患者複数行のため別シート維持.
#
# 列定義: (key, header, width, dropdown_values).
# key は内部識別子. header は実際の Excel ヘッダー文字列.
# dropdown_values が None でない場合、その列に DataValidation (list) を設定.
PATIENT_COLUMNS: Final[list[dict[str, object]]] = [
    {
        "key": "patient_id",
        "header": "patient_id (※システム用・編集不要)",
        "width": 38,
        "dropdown": None,
    },
    {"key": "patient_code", "header": "patient_code", "width": 14, "dropdown": None},
    {"key": "name", "header": "患者名", "width": 18, "dropdown": None},
    {"key": "kana", "header": "フリガナ", "width": 18, "dropdown": None},
    {"key": "sex", "header": "性別", "width": 10, "dropdown": SEX_JA_VALUES},
    {"key": "status", "header": "ステータス", "width": 12, "dropdown": STATUS_JA_VALUES},
    {"key": "insurance", "header": "保険区分", "width": 12, "dropdown": INSURANCE_JA_VALUES},
    {"key": "address", "header": "住所", "width": 32, "dropdown": None},
    {"key": "lat", "header": "緯度", "width": 12, "dropdown": None},
    {"key": "lng", "header": "経度", "width": 12, "dropdown": None},
    {"key": "office_code", "header": "拠点コード", "width": 12, "dropdown": OFFICE_CODE_VALUES},
    {
        "key": "sex_restriction",
        "header": "性別制限",
        "width": 12,
        "dropdown": SEX_RESTRICTION_JA_VALUES,
    },
    # Phase E-7 (gap P0-1): W18 Phase A-1 で追加された Patient.requires_multiple_staff
    # を Excel 入出力で扱えるようにする. 空セル = 維持 (既存) / False (新規) の
    # 慣習は importer / replace_all 側で実装.
    {
        "key": "requires_multiple_staff",
        "header": "複数スタッフ必須",
        "width": 16,
        "dropdown": BOOL_JA_VALUES,
    },
    # ---- Phase G-48: weekly_pattern を統合 (希望訪問パターン) ----
    # Phase G-49: 週訪問回数を 1〜7 dropdown 化.
    {
        "key": "frequency_per_week",
        "header": "週訪問回数",
        "width": 12,
        "dropdown": FREQUENCY_PER_WEEK_VALUES,
    },
    {
        "key": "visit_frequency",
        "header": "訪問頻度",
        "width": 12,
        "dropdown": VISIT_FREQUENCY_JA_VALUES,
    },
    {"key": "visit_weeks", "header": "訪問週 (例 '1,3')", "width": 14, "dropdown": None},
    # Phase G-49/G-50: 希望曜日を 7 列クリック式 (月..日 / 〇・×) に分割.
    # 旧 1 セル "希望曜日 (例 '月,水,金')" 列は廃止 (旧 export ファイルは importer の
    # 後方互換経路で読む).
    {
        "key": "pref_wd_mon",
        "header": PATIENT_WEEKDAY_KEY_TO_HEADER["pref_wd_mon"],
        "width": 11,
        "dropdown": WEEKDAY_MARK_VALUES,
    },
    {
        "key": "pref_wd_tue",
        "header": PATIENT_WEEKDAY_KEY_TO_HEADER["pref_wd_tue"],
        "width": 11,
        "dropdown": WEEKDAY_MARK_VALUES,
    },
    {
        "key": "pref_wd_wed",
        "header": PATIENT_WEEKDAY_KEY_TO_HEADER["pref_wd_wed"],
        "width": 11,
        "dropdown": WEEKDAY_MARK_VALUES,
    },
    {
        "key": "pref_wd_thu",
        "header": PATIENT_WEEKDAY_KEY_TO_HEADER["pref_wd_thu"],
        "width": 11,
        "dropdown": WEEKDAY_MARK_VALUES,
    },
    {
        "key": "pref_wd_fri",
        "header": PATIENT_WEEKDAY_KEY_TO_HEADER["pref_wd_fri"],
        "width": 11,
        "dropdown": WEEKDAY_MARK_VALUES,
    },
    {
        "key": "pref_wd_sat",
        "header": PATIENT_WEEKDAY_KEY_TO_HEADER["pref_wd_sat"],
        "width": 11,
        "dropdown": WEEKDAY_MARK_VALUES,
    },
    {
        "key": "pref_wd_sun",
        "header": PATIENT_WEEKDAY_KEY_TO_HEADER["pref_wd_sun"],
        "width": 11,
        "dropdown": WEEKDAY_MARK_VALUES,
    },
    # Phase G-49: サービス時間を 5 分刻み dropdown 化.
    {
        "key": "service_minutes",
        "header": "サービス時間 (分)",
        "width": 16,
        "dropdown": SERVICE_MINUTES_VALUES,
    },
    {"key": "weekly_time_type", "header": "時間タイプ", "width": 12, "dropdown": TIME_TYPE_VALUES},
    # Phase G-49: 希望開始/終了時刻を HH:MM dropdown 化.
    {"key": "preferred_start", "header": "希望開始時刻", "width": 14, "dropdown": HHMM_VALUES},
    {"key": "preferred_end", "header": "希望終了時刻", "width": 14, "dropdown": HHMM_VALUES},
    {"key": "note", "header": "備考", "width": 30, "dropdown": None},
    {"key": "delete_flag", "header": "(削除フラグ)", "width": 14, "dropdown": DELETE_FLAG_VALUES},
]

# index lookup
PATIENT_COL_INDEX: Final[dict[str, int]] = {
    str(col["key"]): i for i, col in enumerate(PATIENT_COLUMNS)
}

# Phase G-48: weekly_pattern を構成する統合シート上の列キー (importer が patient 行
# から weekly を読むときに使う). weekly_time_type は patient sheet 上のキー名で、
# weekly_pattern dict 内では "time_type" にマップする.
PATIENT_WEEKLY_KEYS: Final[tuple[str, ...]] = (
    "frequency_per_week",
    "visit_frequency",
    "visit_weeks",
    # Phase G-49: 希望曜日は 7 列 (pref_wd_mon..pref_wd_sun) に分割.
    *PATIENT_WEEKDAY_COL_KEYS,
    "service_minutes",
    "weekly_time_type",
    "preferred_start",
    "preferred_end",
)

# 派生列 (システム自動算出 / システム用). exporter でグレー塗り + コメントを付与.
# Phase G-49: 拠点コードは住所から自動割当する派生列扱いに (緯度/経度と同様).
DERIVED_PATIENT_COLUMNS: Final[tuple[str, ...]] = ("patient_id", "lat", "lng", "office_code")

# 必須項目 (新規作成時): 仕様書 §1
PATIENT_REQUIRED_ON_NEW: Final[tuple[str, ...]] = (
    "patient_code",
    "name",
    "sex",
    "status",
    "address",
)


# ---------------------------------------------------------------------------
# シート 2: 固定訪問パターン (PFV) — 旧名「固定訪問スケジュール」
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
    {"key": "mode", "header": "モード", "width": 10, "dropdown": PFV_MODE_JA_VALUES},
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
# Phase G-51: 拠点付きコース表記 (office_code × course_label → 表示トークン)
# ---------------------------------------------------------------------------
#
# 新「固定訪問パターン（編集用）」シート (患者 1 行) と「グリッド集計」シートで、
# コースを **拠点短縮 + ラベル** の 1 トークンで表記する (例 "稲A" / "津M").
# クロス拠点 (例 月=稲B・木=津A) を 1 患者行で表現できるよう、(office, label) を
# 一意な文字列にエンコードし、import 時にパースして (office_code, label) へ戻す.
#
# office_code → 短縮名 (1 文字). INAGE→稲 (稲毛), TSUGA→津 (都賀).

OFFICE_CODE_TO_SHORT: Final[dict[str, str]] = {
    "INAGE": "稲",
    "TSUGA": "津",
}
OFFICE_SHORT_TO_CODE: Final[dict[str, str]] = {
    short: code for code, short in OFFICE_CODE_TO_SHORT.items()
}


def course_token(office_code: str | None, label: str | None) -> str | None:
    """(office_code, label) → 拠点付きコーストークン (例 "稲A").

    office_code が短縮名マップに無い / label が空の場合は None (= 書き出さない).
    短縮名が無い拠点コードはそのままコードを使う (例 "FOOA"; 解析時も後方互換で対応).
    """
    if not label:
        return None
    if not office_code:
        return None
    short = OFFICE_CODE_TO_SHORT.get(office_code, office_code)
    return f"{short}{label}"


def parse_course_token(token: str | None) -> tuple[str, str] | None:
    """拠点付きコーストークン (例 "稲A") → (office_code, label).

    先頭の短縮名 (稲/津) を office_code に、残りを label に分解する.
    解析できない場合は None (= コース未解決として best-effort で扱う).
    後方互換: 短縮名でなく office_code そのもの始まり ("INAGEA") も受理する.
    """
    if token is None:
        return None
    s = str(token).strip()
    if not s:
        return None
    # 1 文字短縮名 (稲/津) 始まり.
    head = s[0]
    if head in OFFICE_SHORT_TO_CODE:
        label = s[1:].strip()
        if label:
            return OFFICE_SHORT_TO_CODE[head], label
        return None
    # 後方互換: office_code そのもの始まり (例 "INAGEA").
    for code in OFFICE_CODE_VALUES:
        if s.startswith(code):
            label = s[len(code) :].strip()
            if label:
                return code, label
    return None


# 「固定訪問パターン（編集用）」シート (患者 1 行) の曜日キー. 月..土 (slot_index=0).
# 各曜日 2 列: time (HH:MM) と course (拠点付きトークン).
PFV_EDIT_WEEKDAY_KEYS: Final[tuple[str, ...]] = ("mon", "tue", "wed", "thu", "fri", "sat")
PFV_EDIT_WEEKDAY_LABELS: Final[tuple[str, ...]] = ("月", "火", "水", "木", "金", "土")
# 曜日キー → DB weekday int (0=月..5=土).
PFV_EDIT_WEEKDAY_KEY_TO_INT: Final[dict[str, int]] = {
    key: i for i, key in enumerate(PFV_EDIT_WEEKDAY_KEYS)
}

SHEET_PFV_EDIT: Final = "固定訪問パターン（編集用）"
SHEET_PFV_GRID: Final = "グリッド集計（静的・閲覧専用）"

# 静的シート最上部の閲覧専用バナー文言.
PFV_GRID_BANNER_TEXT: Final = (
    "※このシートは閲覧専用の静的スナップショット (export 時点) です。"
    "編集しても取り込まれません。編集は『固定訪問パターン（編集用）』で行ってください。"
)


def _pfv_edit_columns() -> list[dict[str, object]]:
    """「固定訪問パターン（編集用）」シートの列定義を組み立てる.

    course 列の dropdown は build_workbook 内で (office, label) から動的生成するため、
    ここでは None (= dropdown 無し) のプレースホルダ. exporter が差し替える.
    """
    cols: list[dict[str, object]] = [
        {
            "key": "patient_id",
            "header": "patient_id (※システム用・編集不要)",
            "width": 38,
            "dropdown": None,
        },
        {"key": "patient_code", "header": "patient_code", "width": 14, "dropdown": None},
        {"key": "patient_name", "header": "患者名", "width": 18, "dropdown": None},
        {
            "key": "service_minutes",
            "header": "サービス時間 (分)",
            "width": 16,
            "dropdown": SERVICE_MINUTES_VALUES,
        },
        {"key": "time_type", "header": "時間タイプ", "width": 12, "dropdown": TIME_TYPE_VALUES},
    ]
    for key, label in zip(PFV_EDIT_WEEKDAY_KEYS, PFV_EDIT_WEEKDAY_LABELS, strict=True):
        cols.append(
            {
                "key": f"{key}_time",
                "header": f"{label}_時刻",
                "width": 12,
                "dropdown": HHMM_VALUES,
            }
        )
        # course 列の dropdown は exporter が course_templates から差し替える.
        cols.append(
            {
                "key": f"{key}_course",
                "header": f"{label}_コース",
                "width": 12,
                "dropdown": None,
            }
        )
    cols.append(
        {
            "key": "delete_flag",
            "header": "(削除フラグ)",
            "width": 14,
            "dropdown": DELETE_FLAG_VALUES,
        }
    )
    return cols


PFV_EDIT_COLUMNS: Final[list[dict[str, object]]] = _pfv_edit_columns()
PFV_EDIT_COL_INDEX: Final[dict[str, int]] = {
    str(col["key"]): i for i, col in enumerate(PFV_EDIT_COLUMNS)
}

# patient_id 列ヘッダーのガイダンスコメント (新「編集用」シート用).
PFV_EDIT_PATIENT_ID_COMMENT_TEXT: Final = (
    "システム用・編集不要。\n"
    "新規患者の固定枠を登録する場合はこの列を空欄にし、"
    "patient_code に患者コードを入力してください。\n"
    "各曜日の『時刻』を空欄にすると、その曜日の固定枠は削除されます。"
)


# ---------------------------------------------------------------------------
# 【後方互換専用】旧シート 3: 希望訪問パターン (patient.weekly_pattern JSONB)
# ---------------------------------------------------------------------------
#
# Phase E-8 で導入した独立シート「希望訪問パターン」(1 patient = 1 行、曜日 7 列の
# TRUE/FALSE). Phase G-48 でこの内容を「患者マスタ」シートに統合したため、
# 新規 export では **このシートを書き出さない**. ただし旧 export ファイル
# (3 シート構成・曜日 7 列) を import できるよう、シート名・列定義・lookup は
# 後方互換のため温存する. importer / replace_all は SHEET_WEEKLY が存在する場合
# のみ旧ロジックでこのシートを読みに行く.

SHEET_WEEKLY: Final = "希望訪問パターン"

WEEKLY_COLUMNS: Final[list[dict[str, object]]] = [
    {"key": "patient_id", "header": "patient_id (※新規時は空欄)", "width": 38, "dropdown": None},
    {"key": "patient_code", "header": "patient_code", "width": 14, "dropdown": None},
    {"key": "patient_name", "header": "患者名", "width": 18, "dropdown": None},
    {"key": "frequency_per_week", "header": "週訪問回数", "width": 10, "dropdown": None},
    {
        "key": "visit_frequency",
        "header": "訪問頻度",
        "width": 10,
        "dropdown": VISIT_FREQUENCY_VALUES,
    },
    {"key": "visit_weeks", "header": "訪問週 (例 '1,3')", "width": 12, "dropdown": None},
    # 希望曜日 月-日 (TRUE/FALSE) — patient.weekly_pattern.preferred_weekdays から展開
    {"key": "wd_mon", "header": "希望曜日_月", "width": 12, "dropdown": BOOL_VALUES},
    {"key": "wd_tue", "header": "希望曜日_火", "width": 12, "dropdown": BOOL_VALUES},
    {"key": "wd_wed", "header": "希望曜日_水", "width": 12, "dropdown": BOOL_VALUES},
    {"key": "wd_thu", "header": "希望曜日_木", "width": 12, "dropdown": BOOL_VALUES},
    {"key": "wd_fri", "header": "希望曜日_金", "width": 12, "dropdown": BOOL_VALUES},
    {"key": "wd_sat", "header": "希望曜日_土", "width": 12, "dropdown": BOOL_VALUES},
    {"key": "wd_sun", "header": "希望曜日_日", "width": 12, "dropdown": BOOL_VALUES},
    {"key": "service_minutes", "header": "サービス時間 (分)", "width": 14, "dropdown": None},
    {"key": "time_type", "header": "時間タイプ", "width": 12, "dropdown": TIME_TYPE_VALUES},
    {"key": "preferred_start", "header": "希望開始時刻", "width": 12, "dropdown": None},
    {"key": "preferred_end", "header": "希望終了時刻", "width": 12, "dropdown": None},
    {"key": "delete_flag", "header": "(削除フラグ)", "width": 14, "dropdown": DELETE_FLAG_VALUES},
]

WEEKLY_COL_INDEX: Final[dict[str, int]] = {
    str(col["key"]): i for i, col in enumerate(WEEKLY_COLUMNS)
}

# 必須項目: patient_id (or patient_code) — 残りは optional (空セル = 維持)
WEEKLY_REQUIRED: Final[tuple[str, ...]] = ("patient_id",)

# 旧 7 列 (TRUE/FALSE) Excel キー → 英短縮形. 後方互換読み込み専用.
# (WEEKDAY_EN_LIST は上部で定義済み.)
WEEKDAY_EXCEL_TO_EN: Final[dict[str, str]] = {
    "wd_mon": "Mon",
    "wd_tue": "Tue",
    "wd_wed": "Wed",
    "wd_thu": "Thu",
    "wd_fri": "Fri",
    "wd_sat": "Sat",
    "wd_sun": "Sun",
}
WEEKDAY_EN_TO_EXCEL: Final[dict[str, str]] = {v: k for k, v in WEEKDAY_EXCEL_TO_EN.items()}


# ---------------------------------------------------------------------------
# Styling constants
# ---------------------------------------------------------------------------

HEADER_FILL_COLOR: Final = "FF4472C4"  # 濃い青
HEADER_FONT_COLOR: Final = "FFFFFFFF"  # 白
ID_COLUMN_FILL_COLOR: Final = "FFEEEEEE"  # 薄いグレー (参照用 patient_id 列)
ID_COLUMN_FONT_COLOR: Final = "FF666666"  # 中間グレー (新規時に触らない雰囲気)

# patient_id 列のヘッダーセルに添えるコメント (新規登録時のガイダンス)。
# Phase G-48: patient_id 空欄でも patient_code で既存患者を突合できる旨を明記.
PATIENT_ID_COMMENT_TEXT: Final = (
    "システム用・編集不要。\n"
    "新規登録時はこの列を空欄のままにしてください。\n"
    "patient_code を入力しておけば、この列が空でも既存患者を更新できます。"
)
# Phase G-48: 緯度/経度はシステムが住所から自動算出する派生列. ユーザー編集不要.
LATLNG_COMMENT_TEXT: Final = (
    "自動算出・編集不要。\nシステムが住所から緯度経度を計算します。\n空欄のままにしてください。"
)
# Phase G-49: 拠点コードは住所から自動割当する派生列. 空欄なら住所から自動解決.
OFFICE_CODE_COMMENT_TEXT: Final = (
    "住所から自動割当・編集不要。\n"
    "新規患者は空欄のままにすると、システムが住所 (市区町村) から拠点を自動判定します。\n"
    "既存患者は現在の拠点が表示されます (空欄にしても変更されません)。\n"
    "手動で拠点を指定/変更したい場合のみコードを入力してください。"
)
PFV_PATIENT_ID_COMMENT_TEXT: Final = (
    "新規患者の PFV を登録する場合: この列を空欄にし、"
    "patient_code 列に患者コードを入力してください。\n"
    "同 import 内の新規患者と自動で紐付きます。"
)
COMMENT_AUTHOR: Final = "システム"
