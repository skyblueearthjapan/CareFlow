"""テスト用 架空 Excel データ生成スクリプト.

Usage:
    cd backend
    python scripts/generate_test_excel_data.py

出力:
    ../.omc/test_data/test_patients.xlsx
    ../.omc/test_data/test_staff.xlsx

ユーザーがこれをダウンロードして本番アプリ (/patients, /staff) で
Excel インポート機能 (新規登録 + 編集) を試すためのデータ.
本番データと区別しやすいよう P-TEST-* / S-TEST-* prefix を使用.

DB セッション不要。mock オブジェクト (dataclass) で ORM 行を模倣する。
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

# backend/ をパスに追加して app.* を import できるようにする
SCRIPT_DIR = Path(__file__).parent
BACKEND_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.patient_excel.exporter import build_workbook as build_patient_wb  # noqa: E402
from app.services.staff_excel.exporter import build_workbook as build_staff_wb  # noqa: E402

# ---------------------------------------------------------------------------
# 出力先
# ---------------------------------------------------------------------------

OUTPUT_DIR = BACKEND_DIR.parent / ".omc" / "test_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PATIENTS_PATH = OUTPUT_DIR / "test_patients.xlsx"
STAFF_PATH = OUTPUT_DIR / "test_staff.xlsx"

# ---------------------------------------------------------------------------
# Mock Office
# ---------------------------------------------------------------------------

INAGE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TSUGA_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


@dataclass
class MockOffice:
    id: uuid.UUID
    code: str
    name: str


OFFICES = [
    MockOffice(id=INAGE_ID, code="INAGE", name="稲毛事業所"),
    MockOffice(id=TSUGA_ID, code="TSUGA", name="津賀事業所"),
]

# ---------------------------------------------------------------------------
# Mock CourseTemplate
# ---------------------------------------------------------------------------

CT_A_ID = uuid.UUID("00000000-0000-0000-0001-000000000001")
CT_B_ID = uuid.UUID("00000000-0000-0000-0001-000000000002")
CT_C_ID = uuid.UUID("00000000-0000-0000-0001-000000000003")


@dataclass
class MockCourseTemplate:
    id: uuid.UUID
    office_id: uuid.UUID
    label: str


COURSE_TEMPLATES = [
    MockCourseTemplate(id=CT_A_ID, office_id=INAGE_ID, label="A"),
    MockCourseTemplate(id=CT_B_ID, office_id=INAGE_ID, label="B"),
    MockCourseTemplate(id=CT_C_ID, office_id=INAGE_ID, label="C"),
    # TSUGA は A のみ
    MockCourseTemplate(
        id=uuid.UUID("00000000-0000-0000-0001-000000000004"), office_id=TSUGA_ID, label="A"
    ),
]

# ---------------------------------------------------------------------------
# Mock Patient
# ---------------------------------------------------------------------------


@dataclass
class MockPatient:
    id: uuid.UUID
    code: str
    name: str
    kana: str
    sex: str
    status: str
    insurance: str
    address: str
    lat: float | None
    lng: float | None
    primary_office_id: uuid.UUID | None
    sex_restriction: str | None
    note: str | None
    weekly_pattern: dict | None = field(default=None)


# 患者データ定義 (10 名)
# office: INAGE=7名, TSUGA=3名
# sex: female 6, male 4
# status: active 8, suspended 1, pending 1
# insurance: medical 8, care 2
# sex_restriction: female_only 1, male_only 1, その他は None

PATIENT_DATA = [
    # 1: INAGE, female, active, medical
    dict(
        code="P-TEST-001",
        name="藤川 さくら",
        kana="フジカワ サクラ",
        sex="female",
        status="active",
        insurance="medical",
        address="千葉県千葉市稲毛区テスト町1-2-3",
        primary_office_id=INAGE_ID,
        sex_restriction=None,
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Mon", "time_type": "固定"},
                {"weekday": "Wed", "time_type": "時間帯"},
                {"weekday": "Fri", "time_type": "午前"},
                {"weekday": "Sat", "time_type": "固定"},
            ]
        },
    ),
    # 2: INAGE, male, active, medical
    dict(
        code="P-TEST-002",
        name="佐渡 一郎",
        kana="サド イチロウ",
        sex="male",
        status="active",
        insurance="medical",
        address="千葉県千葉市稲毛区テスト丘2-5-8",
        primary_office_id=INAGE_ID,
        sex_restriction=None,
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Tue", "time_type": "固定"},
                {"weekday": "Thu", "time_type": "午後"},
            ]
        },
    ),
    # 3: INAGE, female, active, medical, female_only
    dict(
        code="P-TEST-003",
        name="立花 美緒",
        kana="タチバナ ミオ",
        sex="female",
        status="active",
        insurance="medical",
        address="千葉県千葉市花見川区テスト台3-1-6",
        primary_office_id=INAGE_ID,
        sex_restriction="female_only",
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Mon", "time_type": "終日"},
                {"weekday": "Wed", "time_type": "固定"},
                {"weekday": "Sat", "time_type": "午前"},
            ]
        },
    ),
    # 4: TSUGA, female, active, medical
    dict(
        code="P-TEST-004",
        name="宮城 奈緒",
        kana="ミヤギ ナオ",
        sex="female",
        status="active",
        insurance="medical",
        address="千葉県千葉市中央区テスト坂4-3-9",
        primary_office_id=TSUGA_ID,
        sex_restriction=None,
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Mon", "time_type": "固定"},
                {"weekday": "Fri", "time_type": "固定"},
            ]
        },
    ),
    # 5: INAGE, male, suspended, care, male_only
    dict(
        code="P-TEST-005",
        name="白石 健太",
        kana="シライシ ケンタ",
        sex="male",
        status="suspended",
        insurance="care",
        address="千葉県千葉市美浜区テスト浜5-2-7",
        primary_office_id=INAGE_ID,
        sex_restriction="male_only",
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Tue", "time_type": "時間帯"},
                {"weekday": "Thu", "time_type": "固定"},
            ]
        },
    ),
    # 6: INAGE, female, active, medical
    dict(
        code="P-TEST-006",
        name="東田 琴音",
        kana="ヒガシダ コトネ",
        sex="female",
        status="active",
        insurance="medical",
        address="千葉県千葉市稲毛区テスト原6-8-1",
        primary_office_id=INAGE_ID,
        sex_restriction=None,
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Mon", "time_type": "固定"},
                {"weekday": "Wed", "time_type": "固定"},
                {"weekday": "Fri", "time_type": "午後"},
            ]
        },
    ),
    # 7: TSUGA, male, active, medical
    dict(
        code="P-TEST-007",
        name="神崎 竜也",
        kana="カンザキ タツヤ",
        sex="male",
        status="active",
        insurance="medical",
        address="千葉県千葉市若葉区テスト村7-4-2",
        primary_office_id=TSUGA_ID,
        sex_restriction=None,
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Tue", "time_type": "固定"},
                {"weekday": "Fri", "time_type": "時間帯"},
            ]
        },
    ),
    # 8: INAGE, female, active, care
    dict(
        code="P-TEST-008",
        name="桐島 麻由",
        kana="キリシマ マユ",
        sex="female",
        status="active",
        insurance="care",
        address="千葉県千葉市花見川区テスト谷8-6-4",
        primary_office_id=INAGE_ID,
        sex_restriction=None,
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Wed", "time_type": "午前"},
                {"weekday": "Sat", "time_type": "固定"},
            ]
        },
    ),
    # 9: INAGE, male, active, medical
    dict(
        code="P-TEST-009",
        name="遠藤 克弥",
        kana="エンドウ カツヤ",
        sex="male",
        status="active",
        insurance="medical",
        address="千葉県千葉市美浜区テスト磯9-1-5",
        primary_office_id=INAGE_ID,
        sex_restriction=None,
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Mon", "time_type": "時間帯"},
                {"weekday": "Thu", "time_type": "固定"},
                {"weekday": "Fri", "time_type": "固定"},
            ]
        },
    ),
    # 10: TSUGA, female, pending, medical
    dict(
        code="P-TEST-010",
        name="北村 依子",
        kana="キタムラ ヨリコ",
        sex="female",
        status="pending",
        insurance="medical",
        address="千葉県千葉市中央区テスト路10-9-3",
        primary_office_id=TSUGA_ID,
        sex_restriction=None,
        note="テストデータ",
        weekly_pattern={
            "entries": [
                {"weekday": "Mon", "time_type": "固定"},
                {"weekday": "Wed", "time_type": "午前"},
            ]
        },
    ),
]

PATIENTS: list[MockPatient] = []
PATIENT_ID_BY_CODE: dict[str, uuid.UUID] = {}

for row in PATIENT_DATA:
    pid = uuid.uuid4()
    p = MockPatient(
        id=pid,
        lat=None,
        lng=None,
        **row,
    )
    PATIENTS.append(p)
    PATIENT_ID_BY_CODE[p.code] = pid


# ---------------------------------------------------------------------------
# Mock PatientFixedVisit (PFV)
# ---------------------------------------------------------------------------


@dataclass
class MockPFV:
    id: uuid.UUID
    patient_id: uuid.UUID
    mode: str
    weekday: int
    slot_index: int
    start_time: time
    duration_min: int
    course_template_id: uuid.UUID | None


# weekday: 0=月 1=火 2=水 3=木 4=金 5=土
# course_template: INAGE患者→A/B/C, TSUGA患者→A


def _pfv(
    patient_code: str,
    weekday: int,
    start_h: int,
    start_m: int,
    duration_min: int,
    course_label: str,
) -> MockPFV:
    pid = PATIENT_ID_BY_CODE[patient_code]
    office_id = next(p.primary_office_id for p in PATIENTS if p.code == patient_code)
    # コーステンプレートを office + label で探す
    ct = next(
        (c for c in COURSE_TEMPLATES if c.office_id == office_id and c.label == course_label),
        None,
    )
    return MockPFV(
        id=uuid.uuid4(),
        patient_id=pid,
        mode="normal",
        weekday=weekday,
        slot_index=0,
        start_time=time(start_h, start_m),
        duration_min=duration_min,
        course_template_id=ct.id if ct else None,
    )


# 25 枚の PFV を定義 (各患者 2-3 枚、月/水/金多め)
PFV_LIST: list[MockPFV] = [
    # P-TEST-001: 月/水/金
    _pfv("P-TEST-001", 0, 9, 0, 60, "A"),
    _pfv("P-TEST-001", 2, 13, 0, 45, "B"),
    _pfv("P-TEST-001", 4, 15, 30, 30, "C"),
    # P-TEST-002: 火/木
    _pfv("P-TEST-002", 1, 10, 30, 60, "A"),
    _pfv("P-TEST-002", 3, 14, 0, 45, "B"),
    # P-TEST-003: 月/水/土
    _pfv("P-TEST-003", 0, 9, 0, 60, "A"),
    _pfv("P-TEST-003", 2, 10, 30, 30, "C"),
    _pfv("P-TEST-003", 5, 9, 0, 45, "B"),
    # P-TEST-004: 月/金 (TSUGA → A)
    _pfv("P-TEST-004", 0, 10, 30, 60, "A"),
    _pfv("P-TEST-004", 4, 13, 0, 30, "A"),
    # P-TEST-005: 火/木
    _pfv("P-TEST-005", 1, 14, 0, 60, "A"),
    _pfv("P-TEST-005", 3, 16, 0, 30, "B"),
    # P-TEST-006: 月/水/金
    _pfv("P-TEST-006", 0, 13, 0, 45, "C"),
    _pfv("P-TEST-006", 2, 9, 0, 60, "A"),
    _pfv("P-TEST-006", 4, 15, 30, 30, "B"),
    # P-TEST-007: 火/金 (TSUGA → A)
    _pfv("P-TEST-007", 1, 9, 0, 60, "A"),
    _pfv("P-TEST-007", 4, 13, 0, 45, "A"),
    # P-TEST-008: 水/土
    _pfv("P-TEST-008", 2, 10, 30, 30, "B"),
    _pfv("P-TEST-008", 5, 9, 0, 60, "A"),
    # P-TEST-009: 月/木/金
    _pfv("P-TEST-009", 0, 9, 0, 45, "A"),
    _pfv("P-TEST-009", 3, 14, 0, 60, "C"),
    _pfv("P-TEST-009", 4, 16, 0, 30, "B"),
    # P-TEST-010: 月/水 (TSUGA → A)
    _pfv("P-TEST-010", 0, 10, 30, 45, "A"),
    _pfv("P-TEST-010", 2, 13, 0, 60, "A"),
    # 25枚目: P-TEST-001 の追加 (土)
    _pfv("P-TEST-001", 5, 10, 30, 30, "A"),
]

assert len(PFV_LIST) == 25, f"PFV count mismatch: {len(PFV_LIST)}"


# ---------------------------------------------------------------------------
# Mock Staff
# ---------------------------------------------------------------------------


@dataclass
class MockStaff:
    id: uuid.UUID
    code: str
    name: str
    kana: str
    sex: str
    status: str
    role: str
    primary_office_id: uuid.UUID | None
    is_trainee: bool
    note: str | None


STAFF_DATA = [
    dict(
        code="S-TEST-01",
        name="川端 美奈子",
        kana="カワバタ ミナコ",
        sex="female",
        status="active",
        role="admin",
        primary_office_id=INAGE_ID,
        is_trainee=False,
        note="テストデータ",
    ),
    dict(
        code="S-TEST-02",
        name="橋本 拓海",
        kana="ハシモト タクミ",
        sex="male",
        status="active",
        role="manager",
        primary_office_id=INAGE_ID,
        is_trainee=False,
        note="テストデータ",
    ),
    dict(
        code="S-TEST-03",
        name="南 結衣",
        kana="ミナミ ユイ",
        sex="female",
        status="active",
        role="staff",
        primary_office_id=INAGE_ID,
        is_trainee=False,
        note="テストデータ",
    ),
    dict(
        code="S-TEST-04",
        name="相馬 大輝",
        kana="ソウマ ダイキ",
        sex="male",
        status="active",
        role="staff",
        primary_office_id=INAGE_ID,
        is_trainee=True,  # 新人
        note="テストデータ",
    ),
    dict(
        code="S-TEST-05",
        name="野島 千春",
        kana="ノジマ チハル",
        sex="female",
        status="active",
        role="staff",
        primary_office_id=TSUGA_ID,
        is_trainee=False,
        note="テストデータ",
    ),
]

STAFF_LIST: list[MockStaff] = []
STAFF_ID_BY_CODE: dict[str, uuid.UUID] = {}

for row in STAFF_DATA:
    sid = uuid.uuid4()
    s = MockStaff(id=sid, **row)
    STAFF_LIST.append(s)
    STAFF_ID_BY_CODE[s.code] = sid


# ---------------------------------------------------------------------------
# Mock StaffShift (月〜土、計 30 枚: 5 名 × 6 曜日)
# ---------------------------------------------------------------------------


@dataclass
class MockStaffShift:
    staff_id: uuid.UUID
    weekday: int
    is_on: bool
    start_time: time | None
    end_time: time | None


def _shift(
    staff_code: str, weekday: int, is_on: bool, sh: int, sm: int, eh: int, em: int
) -> MockStaffShift:
    return MockStaffShift(
        staff_id=STAFF_ID_BY_CODE[staff_code],
        weekday=weekday,
        is_on=is_on,
        start_time=time(sh, sm) if is_on else None,
        end_time=time(eh, em) if is_on else None,
    )


SHIFTS: list[MockStaffShift] = []

# 各スタッフの月〜土シフト
# 月=0, 火=1, 水=2, 木=3, 金=4, 土=5
# 基本: 月〜金 09:00-18:00, 土 休み
# バリエーション付き

SHIFT_PATTERNS: dict[str, list[tuple[int, bool, int, int, int, int]]] = {
    "S-TEST-01": [
        (0, True, 9, 0, 18, 0),  # 月 通常
        (1, True, 9, 0, 18, 0),  # 火
        (2, True, 9, 0, 15, 0),  # 水 早上がり
        (3, True, 9, 0, 18, 0),  # 木
        (4, True, 9, 0, 18, 0),  # 金
        (5, False, 0, 0, 0, 0),  # 土 休み
    ],
    "S-TEST-02": [
        (0, True, 9, 0, 18, 0),
        (1, True, 13, 0, 18, 0),  # 火 午後勤務
        (2, True, 9, 0, 18, 0),
        (3, True, 9, 0, 18, 0),
        (4, True, 9, 0, 18, 0),
        (5, True, 8, 30, 12, 30),  # 土 半日
    ],
    "S-TEST-03": [
        (0, True, 9, 0, 18, 0),
        (1, True, 9, 0, 18, 0),
        (2, True, 9, 0, 18, 0),
        (3, True, 13, 0, 18, 0),  # 木 午後
        (4, True, 9, 0, 15, 0),  # 金 早上がり
        (5, False, 0, 0, 0, 0),  # 土 休み
    ],
    "S-TEST-04": [
        (0, True, 9, 0, 18, 0),
        (1, True, 9, 0, 18, 0),
        (2, True, 9, 0, 18, 0),
        (3, True, 9, 0, 18, 0),
        (4, True, 9, 0, 18, 0),
        (5, True, 8, 30, 12, 30),  # 土 半日
    ],
    "S-TEST-05": [
        (0, True, 9, 0, 18, 0),
        (1, True, 9, 0, 18, 0),
        (2, True, 13, 0, 18, 0),  # 水 午後
        (3, True, 9, 0, 18, 0),
        (4, True, 9, 0, 18, 0),
        (5, False, 0, 0, 0, 0),  # 土 休み
    ],
}

for staff_code, patterns in SHIFT_PATTERNS.items():
    for weekday, is_on, sh, sm, eh, em in patterns:
        SHIFTS.append(_shift(staff_code, weekday, is_on, sh, sm, eh, em))

assert len(SHIFTS) == 30, f"Shift count mismatch: {len(SHIFTS)}"


# ---------------------------------------------------------------------------
# Build & save workbooks
# ---------------------------------------------------------------------------


def _clear_id_column(wb, sheet_index: int, label: str) -> None:
    """指定シートの A 列データ行 (2 行目以降) を空にする.

    exporter は新規登録用テストデータにも UUID を書き込むため、
    import 時に「ID が DB に存在しない」エラーが起きないよう除去する。
    """
    ws = wb.worksheets[sheet_index]
    for row in range(2, ws.max_row + 1):
        ws.cell(row=row, column=1).value = None
    print(f"       {label} A列 (ID) をクリアしました")


def main() -> None:
    print("テスト用 Excel データを生成中...")

    # --- 患者 ---
    patient_wb = build_patient_wb(
        patients=PATIENTS,  # type: ignore[arg-type]
        pfvs=PFV_LIST,  # type: ignore[arg-type]
        offices=OFFICES,  # type: ignore[arg-type]
        course_templates=COURSE_TEMPLATES,  # type: ignore[arg-type]
    )
    # post-process: 患者シート・PFV シートの patient_id 列 (A 列) を空にする
    # (新規登録テスト用。UUID が入ったまま import すると DB に存在しないエラーになる)
    _clear_id_column(patient_wb, 0, "患者シート")
    _clear_id_column(patient_wb, 1, "PFV シート")

    patient_wb.save(str(PATIENTS_PATH))
    size_p = PATIENTS_PATH.stat().st_size
    print(f"  [OK] {PATIENTS_PATH}")
    print(f"       サイズ: {size_p:,} bytes")
    print(f"       患者: {len(PATIENTS)} 名, PFV: {len(PFV_LIST)} 枚")

    # --- スタッフ ---
    staff_wb = build_staff_wb(
        staff_list=STAFF_LIST,  # type: ignore[arg-type]
        shifts=SHIFTS,  # type: ignore[arg-type]
        offices=OFFICES,  # type: ignore[arg-type]
    )
    # post-process: スタッフシート・シフトシートの staff_id 列 (A 列) を空にする
    _clear_id_column(staff_wb, 0, "スタッフシート")
    _clear_id_column(staff_wb, 1, "勤務シフトシート")

    staff_wb.save(str(STAFF_PATH))
    size_s = STAFF_PATH.stat().st_size
    print(f"  [OK] {STAFF_PATH}")
    print(f"       サイズ: {size_s:,} bytes")
    print(f"       スタッフ: {len(STAFF_LIST)} 名, 勤務シフト: {len(SHIFTS)} 枚")

    # --- サマリー ---
    print()
    print("=== 生成完了 ===")
    print(f"患者ファイル : {PATIENTS_PATH.name}  ({size_p:,} bytes)")
    print(f"  - 患者 {len(PATIENTS)} 名 (INAGE: 7, TSUGA: 3)")
    print(f"  - PFV  {len(PFV_LIST)} 枚 (月/水/金多め、火/木/土も含む)")
    print(f"スタッフファイル: {STAFF_PATH.name}  ({size_s:,} bytes)")
    print(f"  - スタッフ {len(STAFF_LIST)} 名 (INAGE: 4, TSUGA: 1)")
    print(f"  - 勤務シフト {len(SHIFTS)} 枚 (月〜土 × 5 名)")
    print()
    print("ファイルを /patients または /staff の Excel インポートでアップロードしてください。")


if __name__ == "__main__":
    main()
