"""Tests for /api/v1/patients/import-export/* (Excel import / export).

Coverage map (spec §6):
  1.  export: 0 名のとき空テンプレート、N 名のとき N+1 行
  2.  export: PFV が patient_code でリンクされている
  3.  import dry_run: 新規行が検出される
  4.  import dry_run: 更新行で changes が diff される
  5.  import dry_run: <DELETE> フラグで delete 候補になる
  6.  import dry_run: <CLEAR> で NULL 上書き候補になる
  7.  import dry_run: 空セルは「変更しない」 (noop)
  8.  import dry_run: バリデーションエラー (UUID 不正、enum 違反、必須欠落)
  9.  import dry_run: DB は変更されない
  10. import apply: 新規 + 更新 + 削除が 1 transaction で反映
  11. import apply: partial commit — error 行は skip、有効行のみ反映
  12. import apply: PFV の物理削除
  13. import apply: course_template_code → course_template_id 解決
  14. import apply: 拠点コード → primary_office_id 解決
  15. import apply: patient_code 重複で error (skip され、有効行のみ反映)
  16. RBAC: staff は 403
  17. template ダウンロード: 1 行のみ (ヘッダー)
  27. import apply: 全件 error → transaction_applied=False、DB 変更なし
  28. import apply: pfv_error 1 件 + patient_new 2 件 → patient は反映、pfv は skip
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import (
    CourseTemplate,
    Office,
    Patient,
    PatientFixedVisit,
    User,
)
from app.services.patient_excel.schema import (
    PATIENT_COL_INDEX,
    PATIENT_COLUMNS,
    PFV_COL_INDEX,
    PFV_COLUMNS,
    SHEET_PATIENTS,
    SHEET_PFV,
    SHEET_WEEKLY,
    WEEKLY_COL_INDEX,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, code: str, name: str | None = None) -> Office:
    o = Office(code=code, name=name or code)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_patient(db, **overrides) -> Patient:
    defaults = {
        "code": "P-DEFAULT",
        "name": "デフォルト患者",
        "sex": "male",
        "status": "active",
        "address": "千葉市稲毛区test1",
    }
    defaults.update(overrides)
    p = Patient(**defaults)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_pfv(
    db,
    *,
    patient_id,
    weekday: int = 0,
    slot_index: int = 0,
    mode: str = "normal",
    start_time=time(9, 0),
    duration_min: int = 30,
    course_template_id=None,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient_id,
        mode=mode,
        weekday=weekday,
        slot_index=slot_index,
        start_time=start_time,
        duration_min=duration_min,
        course_template_id=course_template_id,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    return pfv


def _patient_headers() -> list[str]:
    return [str(c["header"]) for c in PATIENT_COLUMNS]


def _pfv_headers() -> list[str]:
    return [str(c["header"]) for c in PFV_COLUMNS]


def _build_workbook_bytes(
    *,
    patient_rows: list[dict] | None = None,
    pfv_rows: list[dict] | None = None,
) -> bytes:
    """テスト用ヘルパー: 各 dict は { col_key: value } を持つ.

    値は str / int / float / None / time など何でもよい (openpyxl 任せ).
    """
    wb = Workbook()
    ws_p = wb.active
    ws_p.title = SHEET_PATIENTS
    for col_idx, header in enumerate(_patient_headers(), start=1):
        ws_p.cell(row=1, column=col_idx, value=header)
    for r_idx, row_dict in enumerate(patient_rows or [], start=2):
        for col_key, idx in PATIENT_COL_INDEX.items():
            v = row_dict.get(col_key)
            if v is not None:
                ws_p.cell(row=r_idx, column=idx + 1, value=v)

    ws_f = wb.create_sheet(title=SHEET_PFV)
    for col_idx, header in enumerate(_pfv_headers(), start=1):
        ws_f.cell(row=1, column=col_idx, value=header)
    for r_idx, row_dict in enumerate(pfv_rows or [], start=2):
        for col_key, idx in PFV_COL_INDEX.items():
            v = row_dict.get(col_key)
            if v is not None:
                ws_f.cell(row=r_idx, column=idx + 1, value=v)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _upload(client, admin, *, content: bytes, dry_run: bool = True):
    """multipart helper. fastapi UploadFile は ``file`` という名前で受ける."""
    files = {
        "file": (
            "test.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    return await client.post(
        f"/api/v1/patients/import-export/import?dry_run={'true' if dry_run else 'false'}",
        headers=_bearer(admin),
        files=files,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# 1) export: 0 名のとき empty + 1 名のとき 2 行
@pytest.mark.asyncio
async def test_export_empty_db_returns_template_only(client, db) -> None:
    admin = await _make_user(db, "ex-empty@example.com", "admin")
    res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    wb = load_workbook(BytesIO(res.content))
    assert SHEET_PATIENTS in wb.sheetnames
    ws = wb[SHEET_PATIENTS]
    # ヘッダー 1 行のみ (max_row が 1 になることを許容)
    assert ws.max_row == 1
    # Phase E-7 (gap P2): クロスオフィス warning が 0 件でも response header を返す.
    assert res.headers.get("X-Excel-Crossoffice-Warnings-Count") == "0"


@pytest.mark.asyncio
async def test_export_n_patients_writes_n_plus_1_rows(client, db) -> None:
    admin = await _make_user(db, "ex-n@example.com", "admin")
    for code in ("P-EX-001", "P-EX-002", "P-EX-003"):
        await _make_patient(db, code=code, name=f"名前{code}")

    res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws = wb[SHEET_PATIENTS]
    assert ws.max_row == 4  # 1 header + 3 patients
    codes = [ws.cell(row=r, column=PATIENT_COL_INDEX["patient_code"] + 1).value for r in (2, 3, 4)]
    assert set(codes) == {"P-EX-001", "P-EX-002", "P-EX-003"}


# 2) export: PFV が patient_code でリンクされている
@pytest.mark.asyncio
async def test_export_pfv_links_to_patient_code(client, db) -> None:
    admin = await _make_user(db, "ex-pfv@example.com", "admin")
    patient = await _make_patient(db, code="P-PFV-LINK", name="リンク患者")
    await _make_pfv(db, patient_id=patient.id, weekday=0)

    res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws_f = wb[SHEET_PFV]
    assert ws_f.max_row == 2  # 1 header + 1 PFV
    code_cell = ws_f.cell(row=2, column=PFV_COL_INDEX["patient_code"] + 1).value
    name_cell = ws_f.cell(row=2, column=PFV_COL_INDEX["patient_name"] + 1).value
    assert code_cell == "P-PFV-LINK"
    assert name_cell == "リンク患者"


# 3) dry_run: 新規行検出
@pytest.mark.asyncio
async def test_import_dry_run_detects_new_patient(client, db) -> None:
    admin = await _make_user(db, "im-new@example.com", "admin")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-NEW-001",
                "name": "新規 太郎",
                "sex": "male",
                "status": "active",
                "address": "千葉市稲毛区xxx",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is False
    assert body["summary"]["patients_new"] == 1
    assert body["summary"]["patients_error"] == 0
    row = body["patient_rows"][0]
    assert row["operation"] == "new"
    assert row["patient_code"] == "P-NEW-001"


# 4) dry_run: 更新行で changes が diff される
@pytest.mark.asyncio
async def test_import_dry_run_diffs_updates(client, db) -> None:
    admin = await _make_user(db, "im-upd@example.com", "admin")
    patient = await _make_patient(
        db, code="P-UPD-001", name="旧名前", address="旧住所", sex="male", status="active"
    )
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient.id),
                "patient_code": "P-UPD-001",
                "name": "新名前",
                "sex": "male",
                "status": "active",
                "address": "新住所",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["patients_update"] == 1
    row = body["patient_rows"][0]
    assert row["operation"] == "update"
    fields = {c["field"]: c for c in row["changes"]}
    assert "name" in fields
    assert fields["name"]["old_value"] == "旧名前"
    assert fields["name"]["new_value"] == "新名前"
    assert "address" in fields


# 5) dry_run: <DELETE> フラグ
@pytest.mark.asyncio
async def test_import_dry_run_detects_delete_flag(client, db) -> None:
    admin = await _make_user(db, "im-del@example.com", "admin")
    patient = await _make_patient(db, code="P-DEL-001", name="削除予定")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient.id),
                "patient_code": "P-DEL-001",
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["patients_delete"] == 1
    assert body["patient_rows"][0]["operation"] == "delete"


# 6) dry_run: <CLEAR> で NULL 上書き
@pytest.mark.asyncio
async def test_import_dry_run_clear_to_null(client, db) -> None:
    admin = await _make_user(db, "im-clr@example.com", "admin")
    patient = await _make_patient(db, code="P-CLR-001", name="クリア対象", note="残ってる備考")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient.id),
                "patient_code": "P-CLR-001",
                "name": "クリア対象",
                "sex": "male",
                "status": "active",
                "address": "千葉市稲毛区test1",
                "note": "<CLEAR>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    row = body["patient_rows"][0]
    assert row["operation"] == "update"
    notes = [c for c in row["changes"] if c["field"] == "note"]
    assert len(notes) == 1
    assert notes[0]["new_value"] is None
    assert notes[0]["old_value"] == "残ってる備考"


# 7) dry_run: 空セルは noop
@pytest.mark.asyncio
async def test_import_dry_run_blank_cells_are_noop(client, db) -> None:
    admin = await _make_user(db, "im-noop@example.com", "admin")
    patient = await _make_patient(
        db,
        code="P-NOOP-001",
        name="変更なし",
        sex="male",
        status="active",
        address="千葉市稲毛区test1",
        kana="ヘンコウナシ",
    )
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient.id),
                "patient_code": "P-NOOP-001",
                # 他全部空 → 「触らない」
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    row = body["patient_rows"][0]
    # 既存値と一致 (= 触らない) なので noop. patient_code は明示指定だが既存値と同じ.
    assert row["operation"] == "noop"
    assert body["summary"]["patients_noop"] == 1


# 8) dry_run: バリデーションエラー多種
@pytest.mark.asyncio
async def test_import_dry_run_validation_errors(client, db) -> None:
    admin = await _make_user(db, "im-err@example.com", "admin")
    content = _build_workbook_bytes(
        patient_rows=[
            # 行 2: UUID 不正
            {
                "patient_id": "not-a-uuid",
                "patient_code": "P-BAD-1",
                "name": "uuid不正",
            },
            # 行 3: enum 違反
            {
                "patient_code": "P-BAD-2",
                "name": "enum違反",
                "sex": "INVALID",
                "status": "active",
                "address": "addr",
            },
            # 行 4: 必須欠落 (name)
            {
                "patient_code": "P-BAD-3",
                "sex": "male",
                "status": "active",
                "address": "addr",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["patients_error"] == 3
    ops = {r["row_number"]: r for r in body["patient_rows"]}
    assert ops[2]["operation"] == "error"
    assert ops[3]["operation"] == "error"
    assert ops[4]["operation"] == "error"
    assert "UUID" in ops[2]["error_message"] or "uuid" in ops[2]["error_message"].lower()
    assert "sex" in ops[3]["error_message"].lower() or "性別" in ops[3]["error_message"]
    assert (
        "name" in ops[4]["error_message"].lower()
        or "患者名" in ops[4]["error_message"]
        or "新規作成" in ops[4]["error_message"]
    )


# 9) dry_run: DB は変更されない
@pytest.mark.asyncio
async def test_import_dry_run_does_not_modify_db(client, db) -> None:
    admin = await _make_user(db, "im-noch@example.com", "admin")
    before_count = len((await db.scalars(select(Patient))).all())

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-DRY-001",
                "name": "新規",
                "sex": "male",
                "status": "active",
                "address": "addr",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    assert res.status_code == 200
    assert res.json()["transaction_applied"] is False

    # 再 SELECT
    db.expire_all()
    after_count = len((await db.scalars(select(Patient))).all())
    assert after_count == before_count


# 10) apply: 新規 + 更新 + 削除 が 1 TX で反映
@pytest.mark.asyncio
async def test_import_apply_mixed_operations(client, db) -> None:
    admin = await _make_user(db, "im-apply@example.com", "admin")
    p_upd = await _make_patient(db, code="P-AP-UPD", name="旧", sex="male", status="active")
    p_del = await _make_patient(db, code="P-AP-DEL", name="消す", sex="male", status="active")

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-AP-NEW",
                "name": "新規",
                "sex": "female",
                "status": "active",
                "address": "addr-new",
            },
            {
                "patient_id": str(p_upd.id),
                "patient_code": "P-AP-UPD",
                "name": "新",
                "sex": "male",
                "status": "active",
                "address": "addr-new",
            },
            {
                "patient_id": str(p_del.id),
                "patient_code": "P-AP-DEL",
                "delete_flag": "<DELETE>",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"] == {
        "patients_new": 1,
        "patients_update": 1,
        "patients_delete": 1,
        "patients_error": 0,
        "patients_noop": 0,
        "pfv_new": 0,
        "pfv_update": 0,
        "pfv_delete": 0,
        "pfv_error": 0,
        "pfv_noop": 0,
    }

    # 確認 (API 側で commit 済みなので、テスト用 session の identity map を expire してから再取得)
    db.expire_all()
    rows = (
        await db.scalars(
            select(Patient).where(Patient.code.in_(["P-AP-NEW", "P-AP-UPD", "P-AP-DEL"]))
        )
    ).all()
    by_code = {p.code: p for p in rows}
    assert by_code["P-AP-NEW"].deleted_at is None
    assert by_code["P-AP-UPD"].name == "新"
    assert by_code["P-AP-DEL"].deleted_at is not None  # soft delete


# 11) apply: partial commit — error 行は skip、有効行のみ反映
@pytest.mark.asyncio
async def test_import_apply_partial_commit_skips_error_rows(client, db) -> None:
    """error 1 件 + 正常 update 1 件 → 正常 update は commit、error 行は skip."""
    admin = await _make_user(db, "im-rb@example.com", "admin")
    p_upd = await _make_patient(db, code="P-RB-UPD", name="旧")
    p_upd_id = p_upd.id  # expire_all 前に id を確保 (lazy-load 回避)

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(p_upd_id),
                "patient_code": "P-RB-UPD",
                "name": "正常な更新",
                "sex": "male",
                "status": "active",
                "address": "addr",
            },
            {
                "patient_code": "P-RB-ERR",
                "name": "エラー行",
                "sex": "INVALID",
                "status": "active",
                "address": "addr",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    body = res.json()
    # 有効な op (update 1) があるので transaction は commit される.
    assert body["transaction_applied"] is True
    assert body["summary"]["patients_error"] == 1
    assert body["summary"]["patients_update"] == 1

    # P-RB-UPD は更新されているはず (partial commit).
    db.expire_all()
    refreshed = await db.get(Patient, p_upd_id)
    assert refreshed.name == "正常な更新"
    # P-RB-ERR は skip されているので DB に存在しない.
    err_row = await db.scalar(select(Patient).where(Patient.code == "P-RB-ERR"))
    assert err_row is None


# 12) apply: PFV の物理削除
@pytest.mark.asyncio
async def test_import_apply_pfv_hard_delete(client, db) -> None:
    admin = await _make_user(db, "im-pfvdel@example.com", "admin")
    patient = await _make_patient(db, code="P-PFV-DEL", name="PFV削除")
    pfv = await _make_pfv(db, patient_id=patient.id, weekday=0)
    pfv_id = pfv.id

    content = _build_workbook_bytes(
        pfv_rows=[
            {
                "patient_id": str(patient.id),
                "weekday": "月",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "09:00",
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["pfv_delete"] == 1

    db.expire_all()
    refreshed = await db.get(PatientFixedVisit, pfv_id)
    assert refreshed is None  # 物理削除


# 13) apply: course_template_code 解決
@pytest.mark.asyncio
async def test_import_apply_resolves_course_template(client, db) -> None:
    admin = await _make_user(db, "im-ct@example.com", "admin")
    office = await _make_office(db, code="INAGE", name="稲毛")
    template = CourseTemplate(office_id=office.id, label="A")
    db.add(template)
    await db.commit()
    await db.refresh(template)

    patient = await _make_patient(db, code="P-CT-001", name="CT患者", primary_office_id=office.id)
    patient_id = patient.id
    template_id = template.id

    content = _build_workbook_bytes(
        pfv_rows=[
            {
                "patient_id": str(patient_id),
                "weekday": "火",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "10:00",
                "course_template_code": "A",
                "duration_min": 45,
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    body = res.json()
    assert res.status_code == 200, body
    assert body["transaction_applied"] is True
    assert body["summary"]["pfv_new"] == 1

    db.expire_all()
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].course_template_id == template_id


# 14) apply: 拠点コード解決
@pytest.mark.asyncio
async def test_import_apply_resolves_office_code(client, db) -> None:
    admin = await _make_user(db, "im-off@example.com", "admin")
    office = await _make_office(db, code="TSUGA", name="都賀")
    office_id = office.id

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-OFF-001",
                "name": "拠点指定",
                "sex": "male",
                "status": "active",
                "address": "千葉市若葉区xxx",
                "office_code": "TSUGA",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True

    db.expire_all()
    rows = (await db.scalars(select(Patient).where(Patient.code == "P-OFF-001"))).all()
    assert len(rows) == 1
    assert rows[0].primary_office_id == office_id


# 15) Phase G-48: patient_id 空 + 既存 code → 新規 error ではなく UPDATE 突合.
# ユーザーが export (code 入り) を直接編集して再アップするだけで更新できる.
@pytest.mark.asyncio
async def test_import_existing_code_without_id_updates(client, db) -> None:
    admin = await _make_user(db, "im-dup@example.com", "admin")
    p = await _make_patient(db, code="P-DUP-001", name="既存", sex="male", status="active")
    pid = p.id

    content = _build_workbook_bytes(
        patient_rows=[
            {
                # patient_id 空 + 既存 code → その患者を UPDATE する (旧来は error).
                "patient_code": "P-DUP-001",
                "name": "更新後の名前",
                "sex": "female",
                "status": "active",
                "address": "addr",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["patients_error"] == 0
    assert body["summary"]["patients_update"] == 1
    assert body["summary"]["patients_new"] == 0
    row = body["patient_rows"][0]
    assert row["operation"] == "update"
    assert row["patient_id"] == str(pid)

    db.expire_all()
    p_after = await db.get(Patient, pid)
    assert p_after.name == "更新後の名前"
    assert p_after.sex == "female"
    # 新規行は作られていない (同一 code が 1 件だけ).
    rows = (await db.scalars(select(Patient).where(Patient.code == "P-DUP-001"))).all()
    assert len(rows) == 1


# 15b) 同一ファイル内 patient_code 重複は従来どおり error.
@pytest.mark.asyncio
async def test_import_in_file_duplicate_code_errors(client, db) -> None:
    admin = await _make_user(db, "im-dup-infile@example.com", "admin")

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-INFILE-DUP",
                "name": "1行目",
                "sex": "male",
                "status": "active",
                "address": "addr",
            },
            {
                "patient_code": "P-INFILE-DUP",  # 同ファイル内重複!
                "name": "2行目",
                "sex": "female",
                "status": "active",
                "address": "addr",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    # 1 件目は new, 2 件目は同ファイル内重複で error.
    assert body["summary"]["patients_error"] == 1
    assert body["summary"]["patients_new"] == 1
    err_row = next(r for r in body["patient_rows"] if r["operation"] == "error")
    assert "P-INFILE-DUP" in err_row["error_message"]


# 16) RBAC: staff は 403
@pytest.mark.asyncio
async def test_import_export_requires_admin_or_manager(client, db) -> None:
    staff_user = await _make_user(db, "im-staff@example.com", "staff")
    res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403

    res = await client.get(
        "/api/v1/patients/import-export/template",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403

    # POST /import も同様に staff は 403 にならねばならない (危険 endpoint).
    content = _build_workbook_bytes(patient_rows=[])
    files = {
        "file": (
            "test.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    res = await client.post(
        "/api/v1/patients/import-export/import?dry_run=true",
        headers=_bearer(staff_user),
        files=files,
    )
    assert res.status_code == 403


# 16b) RBAC: manager は import endpoint に到達できる (200).
@pytest.mark.asyncio
async def test_import_allows_manager_role(client, db) -> None:
    manager = await _make_user(db, "im-mgr@example.com", "manager")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-MGR-OK",
                "name": "manager OK",
                "sex": "male",
                "status": "active",
                "address": "addr",
            }
        ],
    )
    res = await _upload(client, manager, content=content, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["patients_new"] == 1


# 17) template ダウンロード: 1 行 (ヘッダーのみ)
@pytest.mark.asyncio
async def test_template_download_has_header_only(client, db) -> None:
    admin = await _make_user(db, "im-tpl@example.com", "admin")
    res = await client.get(
        "/api/v1/patients/import-export/template",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    assert SHEET_PATIENTS in wb.sheetnames
    assert SHEET_PFV in wb.sheetnames
    assert wb[SHEET_PATIENTS].max_row == 1
    assert wb[SHEET_PFV].max_row == 1


# 18) magic word: case-insensitive
@pytest.mark.asyncio
async def test_magic_word_case_insensitive(client, db) -> None:
    """<delete> (lower-case) も認識される."""
    admin = await _make_user(db, "im-magic@example.com", "admin")
    patient = await _make_patient(db, code="P-MAGIC-001", name="ケース不問")

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient.id),
                "patient_code": "P-MAGIC-001",
                "delete_flag": "<delete>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["patients_delete"] == 1
    assert body["patient_rows"][0]["operation"] == "delete"


# 19) PFV update via key (patient_id, mode, weekday, slot_index)
@pytest.mark.asyncio
async def test_pfv_update_via_composite_key(client, db) -> None:
    admin = await _make_user(db, "im-pfvup@example.com", "admin")
    patient = await _make_patient(db, code="P-PFV-UP", name="PFV更新")
    pfv = await _make_pfv(
        db, patient_id=patient.id, weekday=2, slot_index=0, start_time=time(9, 0), duration_min=30
    )
    patient_id = patient.id
    pfv_id = pfv.id

    content = _build_workbook_bytes(
        pfv_rows=[
            {
                "patient_id": str(patient_id),
                "weekday": "水",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "10:30",
                "duration_min": 60,
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["pfv_update"] == 1

    db.expire_all()
    refreshed = await db.get(PatientFixedVisit, pfv_id)
    assert refreshed.start_time == time(10, 30)
    assert refreshed.duration_min == 60


# 19b) regression: 患者を <DELETE> すると関連 PFV も物理削除される (orphan 防止)
@pytest.mark.asyncio
async def test_patient_delete_cascades_pfv_cleanup(client, db) -> None:
    admin = await _make_user(db, "im-delcasc@example.com", "admin")
    patient = await _make_patient(db, code="P-DELCASC", name="orphan source")
    patient_id = patient.id
    pfv = await _make_pfv(db, patient_id=patient_id, weekday=0)
    pfv_id = pfv.id

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient_id),
                "patient_code": "P-DELCASC",
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["patients_delete"] == 1

    db.expire_all()
    patient_after = await db.get(Patient, patient_id)
    assert patient_after is not None
    assert patient_after.deleted_at is not None  # soft delete
    # 関連 PFV は物理削除されているはず.
    pfv_after = await db.get(PatientFixedVisit, pfv_id)
    assert pfv_after is None
    remaining = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()
    assert remaining == []


# 20) patient_id 不正 (DB に無い UUID)
@pytest.mark.asyncio
async def test_unknown_patient_id_errors(client, db) -> None:
    admin = await _make_user(db, "im-unk@example.com", "admin")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(uuid4()),  # 存在しない
                "patient_code": "P-UNK-001",
                "name": "存在しない",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["patients_error"] == 1
    assert "存在" in body["patient_rows"][0]["error_message"]


# 21) PFV patient_code リンク (新規患者 + その PFV を 1 回の import で登録)
@pytest.mark.asyncio
async def test_pfv_links_to_new_patient_via_code(client, db) -> None:
    """新規患者 (patient_id 空) + 同 import 内の PFV を patient_code でリンクできる."""
    admin = await _make_user(db, "im-pfvlink-new@example.com", "admin")

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-LINK-NEW",
                "name": "リンク新規",
                "sex": "male",
                "status": "active",
                "address": "千葉市稲毛区xxx",
            }
        ],
        pfv_rows=[
            {
                # patient_id 空 + patient_code で参照
                "patient_code": "P-LINK-NEW",
                "weekday": "月",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "09:00",
                "duration_min": 30,
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["patients_new"] == 1
    assert body["summary"]["pfv_new"] == 1
    assert body["summary"]["patients_error"] == 0
    assert body["summary"]["pfv_error"] == 0

    # DB に新規患者 + 紐付いた PFV が 1 件あること
    db.expire_all()
    patient_rows = (await db.scalars(select(Patient).where(Patient.code == "P-LINK-NEW"))).all()
    assert len(patient_rows) == 1
    patient = patient_rows[0]
    pfv_rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(pfv_rows) == 1
    assert pfv_rows[0].weekday == 0
    assert pfv_rows[0].duration_min == 30


# 22) PFV patient_code が import 内にも DB にも無い → error
@pytest.mark.asyncio
async def test_pfv_code_lookup_unknown_errors(client, db) -> None:
    admin = await _make_user(db, "im-pfvlink-miss@example.com", "admin")
    content = _build_workbook_bytes(
        pfv_rows=[
            {
                # patient_id 空 + 同 import 内にも DB にも無い code
                "patient_code": "P-MISS-XYZ",
                "weekday": "月",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "09:00",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["pfv_error"] == 1
    row = body["pfv_rows"][0]
    assert row["operation"] == "error"
    assert "P-MISS-XYZ" in row["error_message"]


# 23) 既存患者の PFV を patient_id 空 + patient_code で参照しても OK
@pytest.mark.asyncio
async def test_pfv_code_lookup_existing_patient_ok(client, db) -> None:
    admin = await _make_user(db, "im-pfvlink-exist@example.com", "admin")
    patient = await _make_patient(db, code="P-LINK-EXIST", name="既存患者")
    patient_id = patient.id

    content = _build_workbook_bytes(
        pfv_rows=[
            {
                # patient_id 空 + 既存 code で参照
                "patient_code": "P-LINK-EXIST",
                "weekday": "火",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "10:00",
                "duration_min": 45,
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["pfv_new"] == 1
    assert body["summary"]["pfv_error"] == 0

    db.expire_all()
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].weekday == 1


# 24) PFV の patient_id 空 + patient_code も空 → error
@pytest.mark.asyncio
async def test_pfv_both_id_and_code_blank_errors(client, db) -> None:
    admin = await _make_user(db, "im-pfvlink-blank@example.com", "admin")
    content = _build_workbook_bytes(
        pfv_rows=[
            {
                # patient_id も patient_code も空
                "weekday": "月",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "09:00",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["pfv_error"] == 1
    row = body["pfv_rows"][0]
    assert row["operation"] == "error"
    msg = row["error_message"]
    assert "patient_id" in msg and "patient_code" in msg and "必須" in msg


# 25) HIGH#1 regression: PFV で patient_id と patient_code が異なる患者を指している場合 error
@pytest.mark.asyncio
async def test_pfv_id_and_code_mismatch_errors(client, db) -> None:
    """両者が異なる患者を指していても silent に id を優先して code を無視するのは
    データ破壊リスクなので明示的に error にする."""
    admin = await _make_user(db, "im-pfv-mismatch@example.com", "admin")
    patient_a = await _make_patient(db, code="P-MIS-A", name="患者A")
    patient_b = await _make_patient(db, code="P-MIS-B", name="患者B")
    patient_a_id = patient_a.id

    content = _build_workbook_bytes(
        pfv_rows=[
            {
                # id=A, code=B (異なる患者) → error
                "patient_id": str(patient_a_id),
                "patient_code": patient_b.code,
                "weekday": "月",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "09:00",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["pfv_error"] == 1
    row = body["pfv_rows"][0]
    assert row["operation"] == "error"
    msg = row["error_message"]
    assert "patient_id" in msg and "patient_code" in msg
    assert "P-MIS-B" in msg


# 26) HIGH#1 regression: PFV で patient_id と patient_code が一致していれば正常処理
@pytest.mark.asyncio
async def test_pfv_id_and_code_consistent_ok(client, db) -> None:
    admin = await _make_user(db, "im-pfv-consistent@example.com", "admin")
    patient = await _make_patient(db, code="P-CONS-OK", name="一致患者")
    patient_id = patient.id

    content = _build_workbook_bytes(
        pfv_rows=[
            {
                # id=A, code=A (同じ患者) → 正常
                "patient_id": str(patient_id),
                "patient_code": "P-CONS-OK",
                "weekday": "月",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "09:00",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["pfv_new"] == 1
    assert body["summary"]["pfv_error"] == 0

    db.expire_all()
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()
    assert len(rows) == 1


# 27) partial commit: error 1 件 + new 2 件 → new 2 件のみ DB に反映
@pytest.mark.asyncio
async def test_import_apply_partial_commit_new_rows_with_one_error(client, db) -> None:
    """error 1 件 + new 2 件 で apply → DB に new 2 件のみ反映、error 1 件は skip."""
    admin = await _make_user(db, "im-partial-mix@example.com", "admin")

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-PC-OK-1",
                "name": "正常 1",
                "sex": "male",
                "status": "active",
                "address": "addr1",
            },
            {
                "patient_code": "P-PC-ERR",
                "name": "エラー (enum 違反)",
                "sex": "INVALID",
                "status": "active",
                "address": "addr-err",
            },
            {
                "patient_code": "P-PC-OK-2",
                "name": "正常 2",
                "sex": "female",
                "status": "active",
                "address": "addr2",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["patients_new"] == 2
    assert body["summary"]["patients_error"] == 1

    # DB: 正常 2 件のみ反映 (error 行は skip)
    db.expire_all()
    rows = (
        await db.scalars(
            select(Patient).where(Patient.code.in_(["P-PC-OK-1", "P-PC-ERR", "P-PC-OK-2"]))
        )
    ).all()
    codes = sorted(p.code for p in rows)
    assert codes == ["P-PC-OK-1", "P-PC-OK-2"]


# 28) partial commit: 全件 error → transaction_applied=False、DB 変更なし
@pytest.mark.asyncio
async def test_import_apply_all_errors_no_commit(client, db) -> None:
    admin = await _make_user(db, "im-partial-allerr@example.com", "admin")
    before_count = len((await db.scalars(select(Patient))).all())

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-ALL-ERR-1",
                "name": "エラー 1",
                "sex": "INVALID",
                "status": "active",
                "address": "addr",
            },
            {
                "patient_code": "P-ALL-ERR-2",
                "name": "エラー 2",
                "sex": "male",
                "status": "INVALID_STATUS",
                "address": "addr",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    # 有効 op = 0 件 → 非 commit.
    assert body["transaction_applied"] is False
    assert body["summary"]["patients_error"] == 2

    # DB は変更なし.
    db.expire_all()
    after_count = len((await db.scalars(select(Patient))).all())
    assert after_count == before_count


# 29) partial commit: pfv_error 1 件 + patient_new 2 件 → patient 反映、pfv は正常分のみ反映
@pytest.mark.asyncio
async def test_import_apply_partial_commit_mixed_patient_and_pfv_errors(client, db) -> None:
    """patient シートに正常 new 2 件、pfv シートに error 1 件 + 正常 new 1 件.
    → patient 2 件 + pfv 1 件 が commit される (pfv error 1 件は skip)."""
    admin = await _make_user(db, "im-partial-pfv@example.com", "admin")

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-MIX-A",
                "name": "患者 A",
                "sex": "male",
                "status": "active",
                "address": "addr-a",
            },
            {
                "patient_code": "P-MIX-B",
                "name": "患者 B",
                "sex": "female",
                "status": "active",
                "address": "addr-b",
            },
        ],
        pfv_rows=[
            {
                # 正常 pfv 行 (患者 A 紐付け via code)
                "patient_code": "P-MIX-A",
                "weekday": "月",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "09:00",
                "duration_min": 30,
            },
            {
                # error 行: weekday の値が候補外
                "patient_code": "P-MIX-B",
                "weekday": "INVALID_DAY",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "10:00",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["patients_new"] == 2
    assert body["summary"]["pfv_new"] == 1
    assert body["summary"]["pfv_error"] == 1

    db.expire_all()
    # 患者 2 件はどちらも DB にいる.
    patient_rows = (
        await db.scalars(select(Patient).where(Patient.code.in_(["P-MIX-A", "P-MIX-B"])))
    ).all()
    assert {p.code for p in patient_rows} == {"P-MIX-A", "P-MIX-B"}
    by_code = {p.code: p for p in patient_rows}
    # 患者 A の PFV のみ DB に存在 (患者 B の error 行は skip).
    pfvs_a = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == by_code["P-MIX-A"].id)
        )
    ).all()
    assert len(pfvs_a) == 1
    pfvs_b = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == by_code["P-MIX-B"].id)
        )
    ).all()
    assert pfvs_b == []


# 30) regression (bug 1): 新規 patient INSERT 時に special_week_active が NULL ではなく
#     [] になり、GET /api/v1/patients の Pydantic レスポンス schema を通る.
@pytest.mark.asyncio
async def test_import_apply_new_patient_special_week_active_defaults_to_empty_list(
    client, db
) -> None:
    """Excel import で新規追加した patient の special_week_active が NULL のままだと
    /api/v1/patients が ``Input should be a valid list`` で 500 になる回帰を防ぐ.

    importer は通常の POST /patients ルートを通らず ORM INSERT を直接行うため、
    Pydantic の default_factory に頼れない. importer 側で必ず [] を埋める必要がある.
    """
    admin = await _make_user(db, "im-swa@example.com", "admin")
    # bearer headers は expire_all 前に capture しておく (expire_all 後に
    # admin.id を触ると async greenlet 越しの lazy load が走って fail する).
    headers = _bearer(admin)
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-SWA-001",
                "name": "swa新規",
                "sex": "male",
                "status": "active",
                "address": "addr",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    assert res.json()["transaction_applied"] is True

    # DB から直接取得して default を確認.
    db.expire_all()
    patient = await db.scalar(select(Patient).where(Patient.code == "P-SWA-001"))
    assert patient is not None
    assert patient.special_week_active == []  # NULL ではなく []

    # API ラウンドトリップ (回帰の本丸 — Pydantic validation を通ること)
    res = await client.get("/api/v1/patients", headers=headers)
    assert res.status_code == 200, res.text
    items = res.json()
    by_code = {p["code"]: p for p in items}
    assert "P-SWA-001" in by_code
    assert by_code["P-SWA-001"]["special_week_active"] == []


# 31) bug 2: patient_id 空 + patient_code = 既存患者 + <DELETE> で削除成功
@pytest.mark.asyncio
async def test_import_apply_delete_via_patient_code_only(client, db) -> None:
    """ユーザーが手で「削除したい code + <DELETE>」だけ書いた Excel でも削除できる."""
    admin = await _make_user(db, "im-delcode@example.com", "admin")
    patient = await _make_patient(db, code="P-DELCODE-001", name="code削除")
    patient_id = patient.id

    content = _build_workbook_bytes(
        patient_rows=[
            {
                # patient_id 空 + patient_code のみ + <DELETE>
                "patient_code": "P-DELCODE-001",
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["patients_delete"] == 1
    row = body["patient_rows"][0]
    assert row["operation"] == "delete"
    assert row["patient_code"] == "P-DELCODE-001"

    db.expire_all()
    refreshed = await db.get(Patient, patient_id)
    assert refreshed is not None
    assert refreshed.deleted_at is not None  # soft delete されている


# 32) bug 2: patient_id 空 + 存在しない patient_code + <DELETE> は idempotent noop
@pytest.mark.asyncio
async def test_import_delete_via_unknown_patient_code_is_noop(client, db) -> None:
    """既に削除済み (or そもそも居ない) code を <DELETE> しても error にせず noop."""
    admin = await _make_user(db, "im-delnoop@example.com", "admin")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-DELNOOP-XYZ",
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["patients_noop"] == 1
    assert body["summary"]["patients_error"] == 0
    assert body["patient_rows"][0]["operation"] == "noop"


# 33) bug 2: patient_id 空 + patient_code 空 + <DELETE> は error
@pytest.mark.asyncio
async def test_import_delete_with_both_id_and_code_blank_errors(client, db) -> None:
    admin = await _make_user(db, "im-delblank@example.com", "admin")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                # 両方空
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["patients_error"] == 1
    msg = body["patient_rows"][0]["error_message"]
    assert "patient_id" in msg and "patient_code" in msg


# 34) resurrection: soft-deleted patient_code を Excel で再 import すると復活する
@pytest.mark.asyncio
async def test_resurrection_via_code_for_soft_deleted_patient(client, db) -> None:
    """ユーザー運用フロー: UI で削除 (soft delete) → 同じ code を Excel で再 import.

    soft-deleted 行があるまま INSERT すると UNIQUE 制約違反で 409 になるため、
    importer は INSERT ではなく既存行の deleted_at=NULL + 内容更新で復活させる.
    """
    admin = await _make_user(db, "im-resurrect@example.com", "admin")
    patient = await _make_patient(
        db,
        code="P-RES-001",
        name="削除前の名前",
        sex="male",
        status="active",
        address="千葉市稲毛区old",
    )
    # 直接 soft delete (UI からの削除を再現)
    patient.deleted_at = datetime.now(UTC)
    await db.commit()

    content = _build_workbook_bytes(
        patient_rows=[
            {
                # patient_id 空 + 同じ code で再 import (新規行のつもりで書く)
                "patient_code": "P-RES-001",
                "name": "復活後の名前",
                "sex": "female",
                "status": "active",
                "address": "千葉市稲毛区new",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    # 設計判断: 復活も UI 上は update として表示 (operation="update").
    assert body["summary"]["patients_update"] == 1
    assert body["summary"]["patients_new"] == 0
    assert body["summary"]["patients_error"] == 0
    row = body["patient_rows"][0]
    assert row["operation"] == "update"
    assert row["patient_code"] == "P-RES-001"
    # changes に deleted_at: <old> → null が出ている (= 復活の証跡).
    fields = {c["field"]: c for c in row["changes"]}
    assert "deleted_at" in fields, f"changes に deleted_at が無い: {row['changes']}"
    assert fields["deleted_at"]["new_value"] is None
    assert fields["deleted_at"]["old_value"] is not None
    # 内容更新も diff されている.
    assert "name" in fields and fields["name"]["new_value"] == "復活後の名前"
    assert "sex" in fields and fields["sex"]["new_value"] == "female"
    assert "address" in fields and fields["address"]["new_value"] == "千葉市稲毛区new"

    # DB: deleted_at=NULL に戻り、内容も更新されている.
    await db.refresh(patient)
    assert patient.deleted_at is None
    assert patient.name == "復活後の名前"
    assert patient.sex == "female"
    assert patient.address == "千葉市稲毛区new"


# 35) resurrection: 復活時に patient の id が再発番されないこと (UUID 継続性)
@pytest.mark.asyncio
async def test_resurrection_preserves_id(client, db) -> None:
    """復活時に id は元のまま (新しい UUID は発番されない).

    これにより audit log 等の外部参照が断絶しない.
    """
    admin = await _make_user(db, "im-resurrect-id@example.com", "admin")
    patient = await _make_patient(db, code="P-RES-ID", name="UUID 継続テスト")
    original_id = patient.id
    patient.deleted_at = datetime.now(UTC)
    await db.commit()

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-RES-ID",
                "name": "復活した",
                "sex": "male",
                "status": "active",
                "address": "addr",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    row = body["patient_rows"][0]
    assert row["operation"] == "update"
    # 返却された patient_id が元の id と同じ.
    assert row["patient_id"] == str(original_id)

    # DB 確認: 元の id がそのまま生きている (= 同じ行が復活した).
    await db.refresh(patient)
    assert patient.id == original_id
    assert patient.deleted_at is None
    assert patient.code == "P-RES-ID"
    # 同じ code を持つ他レコードが新規作成されていないこと.
    rows = (await db.scalars(select(Patient).where(Patient.code == "P-RES-ID"))).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# E-4: バックアップ運用 (export → そのまま import) で 0 エラーを担保
# ---------------------------------------------------------------------------


# E-4-1) export 時に patient.weekly_pattern エントリが無くても time_type が
# default ("時間帯") で書き出され、import 側で空欄エラーにならない.
@pytest.mark.asyncio
async def test_patient_export_fills_default_time_type_when_empty(client, db) -> None:
    admin = await _make_user(db, "e4-export-tt@example.com", "admin")
    patient = await _make_patient(db, code="P-E4-TT-1", name="time_type欠落")
    # weekly_pattern は明示的に None (該当エントリ無し).
    patient.weekly_pattern = None
    await db.commit()
    await _make_pfv(db, patient_id=patient.id, weekday=0)

    res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws_f = wb[SHEET_PFV]
    # PFV 行 (row 2) の time_type セルが空ではなく default "時間帯".
    tt_value = ws_f.cell(row=2, column=PFV_COL_INDEX["time_type"] + 1).value
    assert tt_value == "時間帯"


# E-4-2) export 時に patient の primary_office と PFV.course_template の office が
# 不一致 (クロスオフィス参照) の場合、course_template_code は空欄で書き出される.
# round-trip import で「拠点に存在しません」エラーを発生させないため.
@pytest.mark.asyncio
async def test_patient_export_omits_cross_office_course_template_code(client, db) -> None:
    admin = await _make_user(db, "e4-export-ct@example.com", "admin")
    office_a = await _make_office(db, code="INAGE", name="稲毛")
    office_b = await _make_office(db, code="TSUGA", name="都賀")
    # 患者の拠点 = INAGE
    patient = await _make_patient(
        db,
        code="P-E4-CT-1",
        name="cross-office",
        primary_office_id=office_a.id,
    )
    # PFV は TSUGA 側の template を指す (データ不整合).
    template_b = CourseTemplate(office_id=office_b.id, label="B")
    db.add(template_b)
    await db.commit()
    await db.refresh(template_b)
    await _make_pfv(db, patient_id=patient.id, weekday=0, course_template_id=template_b.id)

    res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws_f = wb[SHEET_PFV]
    ct_value = ws_f.cell(row=2, column=PFV_COL_INDEX["course_template_code"] + 1).value
    # クロスオフィス参照は空欄に倒される.
    assert ct_value is None


# E-4-3) export 時に patient の primary_office と template の office が一致して
# いる場合は course_template_code が書き出される.
@pytest.mark.asyncio
async def test_patient_export_writes_course_template_code_for_same_office(client, db) -> None:
    admin = await _make_user(db, "e4-export-ct-ok@example.com", "admin")
    office = await _make_office(db, code="INAGE", name="稲毛")
    template = CourseTemplate(office_id=office.id, label="M")
    db.add(template)
    await db.commit()
    await db.refresh(template)
    patient = await _make_patient(
        db,
        code="P-E4-CT-OK",
        name="same-office",
        primary_office_id=office.id,
    )
    await _make_pfv(db, patient_id=patient.id, weekday=0, course_template_id=template.id)

    res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws_f = wb[SHEET_PFV]
    ct_value = ws_f.cell(row=2, column=PFV_COL_INDEX["course_template_code"] + 1).value
    assert ct_value == "M"


# E-4-4) import 時に time_type が空セルでも error にならず、default で吸収される.
@pytest.mark.asyncio
async def test_patient_import_accepts_empty_time_type_with_default(client, db) -> None:
    admin = await _make_user(db, "e4-import-tt@example.com", "admin")
    patient = await _make_patient(db, code="P-E4-TT-IN", name="time_type空でも OK")
    patient_id = patient.id

    content = _build_workbook_bytes(
        pfv_rows=[
            {
                "patient_id": str(patient_id),
                "weekday": "月",
                "slot_index": 0,
                "mode": "normal",
                # time_type を意図的に省略.
                "start_time": "09:00",
                "duration_min": 30,
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["pfv_new"] == 1
    assert body["summary"]["pfv_error"] == 0
    db.expire_all()
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()
    assert len(rows) == 1


# E-4-5) import 時に course_template_code が患者拠点に存在しない場合でも
# error にならず、course_template_id=None で PFV を保存する.
@pytest.mark.asyncio
async def test_patient_import_fallback_for_unknown_course_template_code(client, db) -> None:
    admin = await _make_user(db, "e4-import-ct@example.com", "admin")
    office = await _make_office(db, code="INAGE", name="稲毛")
    # 患者拠点 = INAGE. ただし INAGE には 'D' template は存在しない.
    patient = await _make_patient(
        db,
        code="P-E4-CTU",
        name="unknown ct",
        primary_office_id=office.id,
    )
    patient_id = patient.id

    content = _build_workbook_bytes(
        pfv_rows=[
            {
                "patient_id": str(patient_id),
                "weekday": "月",
                "slot_index": 0,
                "mode": "normal",
                "time_type": "固定",
                "start_time": "09:00",
                "duration_min": 30,
                "course_template_code": "D",  # 拠点に存在しない
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["pfv_new"] == 1
    assert body["summary"]["pfv_error"] == 0

    db.expire_all()
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()
    assert len(rows) == 1
    # course_template_id は剥がされて None で保存.
    assert rows[0].course_template_id is None


# E-4-6) round-trip: export → そのまま import で 0 エラーを担保.
# 複数患者 + 複数 PFV (うち 1 件は weekly_pattern エントリ無し、
# 1 件はクロスオフィス template 参照) を入れた状態でも、
# export してから何も編集せず import すると error が 0 件になる.
#
# 注: 現状の round-trip 挙動は「データロス vs UX」のトレードオフで
# **意図的** に silent update を許容している:
#   - PFV-A (same-office template, weekly_pattern 未設定) → noop
#     (time_type は表示専用で PFV テーブルに保存先が無いため変更検出されない)
#   - PFV-B (cross-office template) → silent update
#     (export 時に course_template_code が空欄に倒され、import 時に
#      course_template_id=None で再解決されるため、既存 template_b.id との
#      差分として update が記録される — データロスではあるが運用上許容)
#
# クリーンデータでの完全 noop round-trip は
# ``test_patient_export_then_import_clean_data_full_noop_round_trip`` を参照.
@pytest.mark.asyncio
async def test_patient_export_then_import_round_trip_completes_without_errors(client, db) -> None:
    admin = await _make_user(db, "e4-roundtrip@example.com", "admin")
    office_a = await _make_office(db, code="INAGE", name="稲毛")
    office_b = await _make_office(db, code="TSUGA", name="都賀")
    template_a = CourseTemplate(office_id=office_a.id, label="M")
    template_b = CourseTemplate(office_id=office_b.id, label="C")  # 患者 A の拠点に C は無い
    db.add_all([template_a, template_b])
    await db.commit()
    await db.refresh(template_a)
    await db.refresh(template_b)

    patient_a = await _make_patient(db, code="P-RT-A", name="患者A", primary_office_id=office_a.id)
    patient_b = await _make_patient(db, code="P-RT-B", name="患者B", primary_office_id=office_a.id)
    # 1) PFV-A: weekly_pattern エントリ無し (time_type 解決不能だが PFV テーブルには
    #    time_type カラムが無いので diff には影響しない)
    patient_a.weekly_pattern = None
    # 2) PFV-B: 患者 B (拠点 INAGE) に対する PFV だが template_b (拠点 TSUGA) を
    #    指している (クロスオフィス参照). export は course_template_code を空欄に倒し、
    #    import 時に course_template_id=None として再投入される → silent update.
    await db.commit()
    await _make_pfv(db, patient_id=patient_a.id, weekday=0, course_template_id=template_a.id)
    await _make_pfv(db, patient_id=patient_b.id, weekday=2, course_template_id=template_b.id)

    # export
    export_res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(admin),
    )
    assert export_res.status_code == 200
    exported_bytes = export_res.content

    # そのまま import (dry_run=True で error 計上を確認)
    files = {
        "file": (
            "exported.xlsx",
            exported_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    import_res = await client.post(
        "/api/v1/patients/import-export/import?dry_run=true",
        headers=_bearer(admin),
        files=files,
    )
    assert import_res.status_code == 200, import_res.text
    body = import_res.json()
    summary = body["summary"]
    # round-trip では「変更なし」(noop) が主体になる. error は 0 件.
    assert summary["patients_error"] == 0, body
    assert summary["pfv_error"] == 0, body
    # cross-office PFV-B は silent update (course_template_id が剥がされる) になる.
    # この挙動を test で明示しておくことで、将来の意図しない挙動変化を検出できる.
    assert summary["pfv_update"] == 1, body  # PFV-B: cross-office で course_template_id=None に
    assert summary["pfv_noop"] == 1, body  # PFV-A: same-office なので noop


# E-4-7) round-trip (クリーンデータ): 同じ拠点の template + weekly_pattern 設定済 + 全列適切な
# patient で export → import すると、patient と PFV が共に完全 noop になる
# (= データロスや silent update が一切無いことを担保).
@pytest.mark.asyncio
async def test_patient_export_then_import_clean_data_full_noop_round_trip(client, db) -> None:
    admin = await _make_user(db, "e4-roundtrip-clean@example.com", "admin")
    office = await _make_office(db, code="INAGE", name="稲毛")
    template = CourseTemplate(office_id=office.id, label="M")
    db.add(template)
    await db.commit()
    await db.refresh(template)

    patient = await _make_patient(
        db,
        code="P-RT-CLEAN",
        name="クリーン患者",
        primary_office_id=office.id,
    )
    # weekly_pattern を設定 (time_type を export で resolvable にする).
    patient.weekly_pattern = {"time_type": "固定"}
    await db.commit()
    await _make_pfv(
        db,
        patient_id=patient.id,
        weekday=0,
        course_template_id=template.id,
        start_time=time(9, 0),
        duration_min=30,
    )

    # export → そのまま import.
    export_res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(admin),
    )
    assert export_res.status_code == 200
    files = {
        "file": (
            "exported.xlsx",
            export_res.content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    import_res = await client.post(
        "/api/v1/patients/import-export/import?dry_run=true",
        headers=_bearer(admin),
        files=files,
    )
    assert import_res.status_code == 200, import_res.text
    body = import_res.json()
    summary = body["summary"]
    # クリーンデータでは patient / PFV ともに完全 noop. update / error は 0 件.
    assert summary["patients_error"] == 0, body
    assert summary["patients_update"] == 0, body
    assert summary["patients_noop"] == 1, body
    assert summary["pfv_error"] == 0, body
    assert summary["pfv_update"] == 0, body
    assert summary["pfv_noop"] == 1, body


# ---------------------------------------------------------------------------
# Phase E-7 (gap P0-1): Patient.requires_multiple_staff Excel 往復
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_writes_requires_multiple_staff_true(client, db) -> None:
    """requires_multiple_staff=True の患者を export すると列に "TRUE" が入る."""
    admin = await _make_user(db, "ex-rms-t@example.com", "admin")
    await _make_patient(
        db, code="P-RMS-T", name="複数必須", status="active", requires_multiple_staff=True
    )
    res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws = wb[SHEET_PATIENTS]
    val = ws.cell(row=2, column=PATIENT_COL_INDEX["requires_multiple_staff"] + 1).value
    # Phase G-48: 日本語ラベル「はい/いいえ」で書き出す.
    assert val == "はい"


@pytest.mark.asyncio
async def test_export_writes_requires_multiple_staff_false(client, db) -> None:
    """requires_multiple_staff=False の患者を export すると列に "FALSE" が入る."""
    admin = await _make_user(db, "ex-rms-f@example.com", "admin")
    await _make_patient(
        db, code="P-RMS-F", name="単独可", status="active", requires_multiple_staff=False
    )
    res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws = wb[SHEET_PATIENTS]
    val = ws.cell(row=2, column=PATIENT_COL_INDEX["requires_multiple_staff"] + 1).value
    # Phase G-48: 日本語ラベル「はい/いいえ」で書き出す.
    assert val == "いいえ"


@pytest.mark.asyncio
async def test_import_new_patient_with_requires_multiple_staff_true(client, db) -> None:
    """新規患者の requires_multiple_staff=TRUE が DB に保存される."""
    admin = await _make_user(db, "im-rms-new@example.com", "admin")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-NEW-RMS",
                "name": "新規複数必須",
                "sex": "female",
                "status": "active",
                "address": "千葉市稲毛区",
                "requires_multiple_staff": "TRUE",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    p = (await db.scalars(select(Patient).where(Patient.code == "P-NEW-RMS"))).first()
    assert p is not None
    assert p.requires_multiple_staff is True


@pytest.mark.asyncio
async def test_import_update_existing_requires_multiple_staff(client, db) -> None:
    """既存患者の requires_multiple_staff を TRUE → FALSE に変更."""
    admin = await _make_user(db, "im-rms-upd@example.com", "admin")
    p = await _make_patient(
        db, code="P-RMS-UPD", name="切替対象", status="active", requires_multiple_staff=True
    )
    pid = p.id
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(pid),
                "patient_code": "P-RMS-UPD",
                "requires_multiple_staff": "FALSE",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["patients_update"] == 1
    db.expire_all()
    p_after = (await db.scalars(select(Patient).where(Patient.id == pid))).first()
    assert p_after is not None
    assert p_after.requires_multiple_staff is False


@pytest.mark.asyncio
async def test_roundtrip_requires_multiple_staff(client, db) -> None:
    """export → import で requires_multiple_staff が保持される."""
    admin = await _make_user(db, "rt-rms@example.com", "admin")
    p = await _make_patient(
        db, code="P-RT-RMS", name="往復対象", status="active", requires_multiple_staff=True
    )
    pid = p.id
    # export
    export_res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    assert export_res.status_code == 200
    files = {
        "file": (
            "export.xlsx",
            export_res.content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    # そのまま再 import
    import_res = await client.post(
        "/api/v1/patients/import-export/import?dry_run=false",
        headers=_bearer(admin),
        files=files,
    )
    assert import_res.status_code == 200, import_res.text
    body = import_res.json()
    assert body["summary"]["patients_error"] == 0
    db.expire_all()
    p_after = (await db.scalars(select(Patient).where(Patient.id == pid))).first()
    assert p_after is not None
    assert p_after.requires_multiple_staff is True  # 保持


@pytest.mark.asyncio
async def test_import_blank_requires_multiple_staff_preserves_existing(client, db) -> None:
    """既存患者の更新で requires_multiple_staff を空セルにすると値が維持される (通常 import)."""
    admin = await _make_user(db, "im-rms-blank@example.com", "admin")
    p = await _make_patient(
        db, code="P-RMS-BLANK", name="維持対象", status="active", requires_multiple_staff=True
    )
    pid = p.id
    # name を変更するが requires_multiple_staff は空セル
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(pid),
                "patient_code": "P-RMS-BLANK",
                "name": "新名前",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200
    db.expire_all()
    p_after = (await db.scalars(select(Patient).where(Patient.id == pid))).first()
    assert p_after is not None
    assert p_after.name == "新名前"
    assert p_after.requires_multiple_staff is True  # 維持された


# ---------------------------------------------------------------------------
# Phase E-8: 希望訪問パターン (patient.weekly_pattern) Excel 対応
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_includes_weekly_pattern_in_patient_sheet(client, db) -> None:
    """Phase G-48: weekly_pattern は患者マスタシートに統合して書き出される.

    旧 Phase E-8 の独立「希望訪問パターン」シートは export では作られない.
    """
    admin = await _make_user(db, "e8-export@example.com", "admin")
    p = await _make_patient(db, code="P-E8-1", name="希望あり患者")
    p.weekly_pattern = {
        "time_type": "時間帯",
        "preferred_start": "09:00",
        "preferred_end": "12:00",
        "preferred_weekdays": ["Mon", "Wed", "Fri"],
        "service_minutes": 35,
        "frequency_per_week": 3,
        "visit_frequency": "every",
    }
    await db.commit()
    res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    # 独立シートは作られない (2 シート構成).
    assert SHEET_WEEKLY not in wb.sheetnames
    ws = wb[SHEET_PATIENTS]
    row = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    assert row[PATIENT_COL_INDEX["patient_code"]] == "P-E8-1"
    assert row[PATIENT_COL_INDEX["weekly_time_type"]] == "時間帯"
    assert row[PATIENT_COL_INDEX["preferred_start"]] == "09:00"
    assert row[PATIENT_COL_INDEX["preferred_end"]] == "12:00"
    assert row[PATIENT_COL_INDEX["frequency_per_week"]] == 3
    assert row[PATIENT_COL_INDEX["service_minutes"]] == 35
    # 訪問頻度: DB "every" → 日本語 "毎週".
    assert row[PATIENT_COL_INDEX["visit_frequency"]] == "毎週"
    # 希望曜日: 1 セルにカンマ区切り (Mon..Sun 正準順).
    assert row[PATIENT_COL_INDEX["preferred_weekdays"]] == "月,水,金"


@pytest.mark.asyncio
async def test_import_updates_weekly_pattern(client, db) -> None:
    """Phase G-48: 統合シートで patient.weekly_pattern を更新できる."""
    admin = await _make_user(db, "e8-import@example.com", "admin")
    p = await _make_patient(db, code="P-E8-2", name="更新対象")
    p.weekly_pattern = None
    await db.commit()
    pid = p.id

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(pid),
                "patient_code": "P-E8-2",
                "name": "更新対象",
                "sex": "男性",
                "status": "稼働",
                "address": "千葉市稲毛区test1",
                "weekly_time_type": "固定",
                "preferred_start": "10:00",
                "service_minutes": 45,
                "preferred_weekdays": "月,水",
                "visit_frequency": "隔週",
                "frequency_per_week": 2,
            }
        ],
    )

    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    p_after = (await db.scalars(select(Patient).where(Patient.id == pid))).first()
    assert p_after is not None
    assert p_after.weekly_pattern is not None
    assert p_after.weekly_pattern["time_type"] == "固定"
    assert p_after.weekly_pattern["preferred_start"] == "10:00"
    assert p_after.weekly_pattern["service_minutes"] == 45
    assert set(p_after.weekly_pattern.get("preferred_weekdays", [])) == {"Mon", "Wed"}
    # 訪問頻度は DB 英語正準値へ正規化される.
    assert p_after.weekly_pattern["visit_frequency"] == "biweekly"
    assert p_after.weekly_pattern["frequency_per_week"] == 2


@pytest.mark.asyncio
async def test_weekly_pattern_roundtrip(client, db) -> None:
    """Phase G-48: export → 編集なし import で weekly_pattern が完全 noop."""
    admin = await _make_user(db, "e8-roundtrip@example.com", "admin")
    p = await _make_patient(db, code="P-E8-RT", name="round-trip")
    p.weekly_pattern = {
        "time_type": "時間帯",
        "preferred_start": "13:00",
        "preferred_end": "15:00",
        "preferred_weekdays": ["Tue", "Thu"],
        "service_minutes": 30,
        "visit_frequency": "monthly",
    }
    await db.commit()
    export_res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    assert export_res.status_code == 200
    files = {
        "file": (
            "exported.xlsx",
            export_res.content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    import_res = await client.post(
        "/api/v1/patients/import-export/import?dry_run=true",
        headers=_bearer(admin),
        files=files,
    )
    assert import_res.status_code == 200, import_res.text
    body = import_res.json()
    summary = body["summary"]
    # round-trip: clean data → 完全 noop (weekly_pattern も含む)
    assert summary["patients_error"] == 0, body
    assert summary["patients_update"] == 0, body
    assert summary["patients_noop"] == 1, body


@pytest.mark.asyncio
async def test_weekly_pattern_roundtrip_preserves_unmanaged_keys(client, db) -> None:
    """CRITICAL 回帰防止 (Phase G-48 hotfix): weekly_pattern に管理外キー
    (entries / staff_count) を持つ患者を export → 無編集 import したとき、

      (a) patients_update == 0 (真の noop)
      (b) DB 上の entries / staff_count が **保持** される

    ことを担保する. exporter は管理 8 キーのみ書き出し importer も 8 キーのみ
    再構築するため、丸ごと置換だと無編集でも entries が消えて update 扱いになる
    (= スケジューラの訪問時刻 / 2 名体制が静かに壊れる). merge 化でこれを防ぐ.

    既存の round-trip テスト (test_weekly_pattern_roundtrip) は 8 キーのみで
    管理外キーが盲点だったため、本テストを管理外キー入りで追加する.

    visit_frequency は canonical 値 (biweekly) を入れて round-trip が noop に
    なる (= legacy-JP 正規化が canonical をそのまま通す) ことも確認する.
    """
    admin = await _make_user(db, "g48-rt-unmanaged@example.com", "admin")
    p = await _make_patient(db, code="P-G48-RT-ENTRIES", name="管理外キー保持")
    original_wp = {
        # ---- 管理 8 キー (exporter/import が扱う、round-trip で復元される) ----
        "frequency_per_week": 3,
        "visit_frequency": "biweekly",  # canonical 値 (JP 正規化が noop で通すこと)
        "preferred_weekdays": ["Mon", "Wed", "Fri"],
        "service_minutes": 45,
        "time_type": "時間帯",
        "preferred_start": "10:00",
        "preferred_end": "11:30",
        # ---- 管理外キー (本パイプライン非管理. merge で保持されねばならない) ----
        "staff_count": 1,
        "entries": [
            {
                "weekday": "Mon",
                "time_type": "時間帯",
                "preferred_start": "10:00",
                "preferred_end": "11:30",
                "service_minutes": 45,
                "staff_count": 1,
            },
            {
                "weekday": "Wed",
                "time_type": "固定",
                "preferred_start": "14:00",
                "preferred_end": "15:00",
                "service_minutes": 60,
                "staff_count": 2,  # 2 名体制エントリ — 消えると静かに壊れる
            },
            {
                "weekday": "Fri",
                "time_type": "時間帯",
                "preferred_start": "10:00",
                "preferred_end": "11:30",
                "service_minutes": 45,
                "staff_count": 1,
            },
        ],
    }
    p.weekly_pattern = dict(original_wp)
    await db.commit()
    pid = p.id

    # export → 無編集 import (dry_run でまず noop を確認)
    export_res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    assert export_res.status_code == 200
    exported = export_res.content
    dry_files = {
        "file": (
            "exported.xlsx",
            exported,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    dry_res = await client.post(
        "/api/v1/patients/import-export/import?dry_run=true",
        headers=_bearer(admin),
        files=dry_files,
    )
    assert dry_res.status_code == 200, dry_res.text
    summary = dry_res.json()["summary"]
    # (a) 真の noop — entries 持ち患者でも update にならない
    assert summary["patients_error"] == 0, dry_res.json()
    assert summary["patients_update"] == 0, dry_res.json()
    assert summary["patients_noop"] == 1, dry_res.json()

    # apply して DB 上の entries / staff_count が保持されることを確認
    apply_files = {
        "file": (
            "exported.xlsx",
            exported,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    apply_res = await client.post(
        "/api/v1/patients/import-export/import?dry_run=false",
        headers=_bearer(admin),
        files=apply_files,
    )
    assert apply_res.status_code == 200, apply_res.text
    assert apply_res.json()["summary"]["patients_update"] == 0, apply_res.json()

    db.expire_all()
    p_after = await db.get(Patient, pid)
    # (b) 管理外キーが保持される
    assert p_after.weekly_pattern is not None
    assert p_after.weekly_pattern.get("staff_count") == 1
    assert p_after.weekly_pattern.get("entries") == original_wp["entries"]
    # 2 名体制エントリも保持
    wed = next(e for e in p_after.weekly_pattern["entries"] if e["weekday"] == "Wed")
    assert wed["staff_count"] == 2
    # 管理 8 キーも維持 (canonical visit_frequency が round-trip 安定)
    assert p_after.weekly_pattern["visit_frequency"] == "biweekly"
    assert p_after.weekly_pattern["frequency_per_week"] == 3


@pytest.mark.asyncio
async def test_weekly_pattern_partial_edit_preserves_unmanaged_keys(client, db) -> None:
    """Phase G-48 hotfix: 管理 8 キーの一部だけ編集して import した場合でも、
    管理外キー (entries/staff_count) は保持され、編集した管理キーのみ反映される.

    blank=keep + 管理外キー保持 の両立を確認する (merge セマンティクス)."""
    admin = await _make_user(db, "g48-partial-unmanaged@example.com", "admin")
    p = await _make_patient(db, code="P-G48-PARTIAL", name="部分編集")
    p.weekly_pattern = {
        "time_type": "時間帯",
        "preferred_start": "09:00",
        "preferred_end": "10:00",
        "service_minutes": 30,
        "staff_count": 2,
        "entries": [
            {"weekday": "Tue", "time_type": "時間帯", "staff_count": 2},
        ],
    }
    await db.commit()
    pid = p.id
    # service_minutes のみ 30 → 60 に変更、他 weekly 列は export 相当で埋める.
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(pid),
                "patient_code": "P-G48-PARTIAL",
                "name": "部分編集",
                "weekly_time_type": "時間帯",
                "preferred_start": "09:00",
                "preferred_end": "10:00",
                "service_minutes": 60,  # ここだけ変更
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    p_after = await db.get(Patient, pid)
    # 編集した管理キーは反映
    assert p_after.weekly_pattern["service_minutes"] == 60
    # 管理外キーは保持
    assert p_after.weekly_pattern.get("staff_count") == 2
    assert p_after.weekly_pattern.get("entries") == [
        {"weekday": "Tue", "time_type": "時間帯", "staff_count": 2}
    ]


# ---------------------------------------------------------------------------
# Phase G-48: 日本語化 + 英↔日マッピング + code 突合 (UUID 不要化) + 統合シート
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_writes_japanese_enum_labels(client, db) -> None:
    """Phase G-48: enum 系が日本語ラベルで書き出される (DB 英語値 → 日本語)."""
    admin = await _make_user(db, "g48-ex-ja@example.com", "admin")
    await _make_patient(
        db,
        code="P-G48-EX",
        name="日本語出力",
        sex="female",
        status="suspended",
        insurance="medical",
        sex_restriction="female_only",
        address="千葉市稲毛区test1",
    )
    res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws = wb[SHEET_PATIENTS]
    row = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    assert row[PATIENT_COL_INDEX["sex"]] == "女性"
    assert row[PATIENT_COL_INDEX["status"]] == "休止"
    assert row[PATIENT_COL_INDEX["insurance"]] == "医療保険"
    assert row[PATIENT_COL_INDEX["sex_restriction"]] == "女性のみ"


@pytest.mark.asyncio
async def test_export_sex_restriction_none_writes_nashi(client, db) -> None:
    """sex_restriction が DB NULL のとき「なし」を明示出力する."""
    admin = await _make_user(db, "g48-ex-nashi@example.com", "admin")
    await _make_patient(db, code="P-G48-NASHI", name="制限なし", sex_restriction=None)
    res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    wb = load_workbook(BytesIO(res.content))
    ws = wb[SHEET_PATIENTS]
    row = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    assert row[PATIENT_COL_INDEX["sex_restriction"]] == "なし"


@pytest.mark.asyncio
async def test_import_japanese_enum_labels_new_patient(client, db) -> None:
    """Phase G-48: 日本語ラベルで新規患者を登録すると DB に英語 enum が格納される."""
    admin = await _make_user(db, "g48-im-ja@example.com", "admin")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-G48-JA-NEW",
                "name": "日本語入力",
                "sex": "女性",
                "status": "入院",
                "insurance": "介護保険",
                "sex_restriction": "男性のみ",
                "address": "千葉市稲毛区xxx",
                "requires_multiple_staff": "はい",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    p = await db.scalar(select(Patient).where(Patient.code == "P-G48-JA-NEW"))
    assert p is not None
    assert p.sex == "female"
    assert p.status == "admitted"
    assert p.insurance == "care"
    assert p.sex_restriction == "male_only"
    assert p.requires_multiple_staff is True


@pytest.mark.asyncio
async def test_import_english_enum_values_still_accepted(client, db) -> None:
    """後方互換: 英語 enum 値 (旧 export) でも import が壊れない."""
    admin = await _make_user(db, "g48-im-en@example.com", "admin")
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-G48-EN-NEW",
                "name": "英語値互換",
                "sex": "male",
                "status": "active",
                "insurance": "medical",
                "sex_restriction": "female_only",
                "address": "千葉市稲毛区xxx",
                "requires_multiple_staff": "TRUE",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    p = await db.scalar(select(Patient).where(Patient.code == "P-G48-EN-NEW"))
    assert p is not None
    assert p.sex == "male"
    assert p.status == "active"
    assert p.insurance == "medical"
    assert p.sex_restriction == "female_only"
    assert p.requires_multiple_staff is True


@pytest.mark.asyncio
async def test_import_sex_restriction_nashi_clears_to_null(client, db) -> None:
    """「なし」を import すると sex_restriction が NULL になる (制限解除)."""
    admin = await _make_user(db, "g48-im-nashi@example.com", "admin")
    p = await _make_patient(db, code="P-G48-CLR", name="制限解除", sex_restriction="female_only")
    pid = p.id
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(pid),
                "patient_code": "P-G48-CLR",
                "sex_restriction": "なし",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["patients_update"] == 1
    db.expire_all()
    p_after = await db.get(Patient, pid)
    assert p_after.sex_restriction is None


@pytest.mark.asyncio
async def test_import_update_via_code_without_id(client, db) -> None:
    """Phase G-48 (最重要): patient_id 空 + patient_code で既存患者を UPDATE."""
    admin = await _make_user(db, "g48-code-upd@example.com", "admin")
    p = await _make_patient(
        db, code="P-G48-CODE", name="旧名前", sex="male", status="active", address="旧住所"
    )
    pid = p.id
    content = _build_workbook_bytes(
        patient_rows=[
            {
                # patient_id 列なし (UUID 不要) — code だけで突合.
                "patient_code": "P-G48-CODE",
                "name": "新名前",
                "address": "新住所",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["patients_update"] == 1
    assert body["summary"]["patients_new"] == 0
    assert body["patient_rows"][0]["patient_id"] == str(pid)
    db.expire_all()
    p_after = await db.get(Patient, pid)
    assert p_after.name == "新名前"
    assert p_after.address == "新住所"


@pytest.mark.asyncio
async def test_pfv_mode_japanese_label(client, db) -> None:
    """Phase G-48: PFV モードを日本語「特別」で指定すると DB に "special" が入る."""
    admin = await _make_user(db, "g48-pfv-mode@example.com", "admin")
    p = await _make_patient(db, code="P-G48-PFV", name="モード日本語")
    pid = p.id
    content = _build_workbook_bytes(
        pfv_rows=[
            {
                "patient_id": str(pid),
                "weekday": "月",
                "slot_index": 0,
                "mode": "特別",
                "time_type": "固定",
                "start_time": "09:00",
                "duration_min": 30,
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == pid))
    ).all()
    assert len(rows) == 1
    assert rows[0].mode == "special"


@pytest.mark.asyncio
async def test_export_pfv_mode_japanese(client, db) -> None:
    """PFV モードが日本語で export される (special → 特別)."""
    admin = await _make_user(db, "g48-ex-pfv-mode@example.com", "admin")
    p = await _make_patient(db, code="P-G48-PFVEX", name="モード出力")
    await _make_pfv(db, patient_id=p.id, weekday=0, mode="special")
    res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    wb = load_workbook(BytesIO(res.content))
    ws_f = wb[SHEET_PFV]
    mode_val = ws_f.cell(row=2, column=PFV_COL_INDEX["mode"] + 1).value
    assert mode_val == "特別"


@pytest.mark.asyncio
async def test_weekday_single_cell_comma_variants(client, db) -> None:
    """希望曜日 1 セル: 半角/全角カンマ・読点いずれの区切りも受理する."""
    admin = await _make_user(db, "g48-wd-cell@example.com", "admin")
    p = await _make_patient(db, code="P-G48-WD", name="曜日1セル")
    p.weekly_pattern = None
    await db.commit()
    pid = p.id
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(pid),
                "patient_code": "P-G48-WD",
                # 全角カンマ + 読点 + 半角カンマ混在.
                "preferred_weekdays": "月，火、水,金",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    p_after = await db.get(Patient, pid)
    assert p_after.weekly_pattern is not None
    # Mon..Sun 正準順で格納される.
    assert p_after.weekly_pattern["preferred_weekdays"] == ["Mon", "Tue", "Wed", "Fri"]


@pytest.mark.asyncio
async def test_weekly_blank_keeps_existing(client, db) -> None:
    """weekly フィールド全空の行は既存 weekly_pattern を維持 (clear しない)."""
    admin = await _make_user(db, "g48-wk-keep@example.com", "admin")
    p = await _make_patient(db, code="P-G48-KEEP", name="維持")
    existing_wp = {
        "time_type": "時間帯",
        "preferred_start": "09:00",
        "preferred_end": "12:00",
        "preferred_weekdays": ["Mon"],
    }
    p.weekly_pattern = dict(existing_wp)
    await db.commit()
    pid = p.id
    # name だけ変更、weekly 列は全空.
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(pid),
                "patient_code": "P-G48-KEEP",
                "name": "新名前",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    p_after = await db.get(Patient, pid)
    assert p_after.name == "新名前"
    # weekly_pattern は破壊されず維持される.
    assert p_after.weekly_pattern == existing_wp


@pytest.mark.asyncio
async def test_full_japanese_roundtrip_noop(client, db) -> None:
    """Phase G-48: 日本語 enum + weekly 統合 + 全列適切な患者で export → 無編集
    import が完全 noop (0 変更) になる (round-trip 安定性、最重要)."""
    admin = await _make_user(db, "g48-rt-noop@example.com", "admin")
    office = await _make_office(db, code="INAGE", name="稲毛")
    p = await _make_patient(
        db,
        code="P-G48-RT",
        name="往復患者",
        kana="オウフクカンジャ",
        sex="female",
        status="active",
        insurance="care",
        address="千葉市稲毛区test1",
        sex_restriction="female_only",
        requires_multiple_staff=True,
        primary_office_id=office.id,
    )
    p.weekly_pattern = {
        "frequency_per_week": 3,
        "visit_frequency": "every",
        "visit_weeks": "1,3",
        "preferred_weekdays": ["Mon", "Wed", "Fri"],
        "service_minutes": 40,
        "time_type": "時間帯",
        "preferred_start": "09:00",
        "preferred_end": "12:00",
    }
    await db.commit()

    export_res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    assert export_res.status_code == 200
    files = {
        "file": (
            "exported.xlsx",
            export_res.content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    import_res = await client.post(
        "/api/v1/patients/import-export/import?dry_run=true",
        headers=_bearer(admin),
        files=files,
    )
    assert import_res.status_code == 200, import_res.text
    summary = import_res.json()["summary"]
    assert summary["patients_error"] == 0, import_res.json()
    assert summary["patients_update"] == 0, import_res.json()
    assert summary["patients_noop"] == 1, import_res.json()


@pytest.mark.asyncio
async def test_backward_compat_legacy_three_sheet_english(client, db) -> None:
    """後方互換: 旧 3 シート構成 (英語値 + 曜日 7 列 TRUE/FALSE) も import 可能.

    旧 export ファイル (Phase E-8 形式) を手で組み立てて import する.
    """
    from app.services.patient_excel.schema import WEEKLY_COLUMNS

    admin = await _make_user(db, "g48-bc-legacy@example.com", "admin")
    p = await _make_patient(db, code="P-G48-LEGACY", name="旧形式")
    p.weekly_pattern = None
    await db.commit()
    pid = p.id

    wb = Workbook()
    # 患者シート: 旧形式は patient_id を持つだけの update なし行でよい.
    # ただし旧 patient sheet には weekly 列が無い前提なので、新ヘッダーで空行を作る
    # と weekly は空 = 維持. weekly は独立シートから来る.
    ws_p = wb.active
    ws_p.title = SHEET_PATIENTS
    ws_p.append([str(c["header"]) for c in PATIENT_COLUMNS])
    prow = [None] * len(PATIENT_COLUMNS)
    prow[PATIENT_COL_INDEX["patient_id"]] = str(pid)
    prow[PATIENT_COL_INDEX["patient_code"]] = "P-G48-LEGACY"
    ws_p.append(prow)
    # PFV シート (空).
    pfv_ws = wb.create_sheet(SHEET_PFV)
    pfv_ws.append([str(c["header"]) for c in PFV_COLUMNS])
    # 旧 独立「希望訪問パターン」シート (曜日 7 列 TRUE/FALSE).
    ws_w = wb.create_sheet(SHEET_WEEKLY)
    ws_w.append([str(c["header"]) for c in WEEKLY_COLUMNS])
    wrow = [None] * len(WEEKLY_COLUMNS)
    wrow[WEEKLY_COL_INDEX["patient_id"]] = str(pid)
    wrow[WEEKLY_COL_INDEX["patient_code"]] = "P-G48-LEGACY"
    wrow[WEEKLY_COL_INDEX["time_type"]] = "固定"
    wrow[WEEKLY_COL_INDEX["preferred_start"]] = "10:00"
    wrow[WEEKLY_COL_INDEX["wd_tue"]] = "TRUE"
    wrow[WEEKLY_COL_INDEX["wd_thu"]] = "TRUE"
    # 旧形式では visit_frequency に日本語が入っていた可能性 → 英語へ正規化されること.
    wrow[WEEKLY_COL_INDEX["visit_frequency"]] = "毎週"
    ws_w.append(wrow)
    buf = BytesIO()
    wb.save(buf)

    res = await _upload(client, admin, content=buf.getvalue(), dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    p_after = await db.get(Patient, pid)
    assert p_after.weekly_pattern is not None
    assert p_after.weekly_pattern["time_type"] == "固定"
    assert p_after.weekly_pattern["preferred_start"] == "10:00"
    assert set(p_after.weekly_pattern["preferred_weekdays"]) == {"Tue", "Thu"}
    # 旧シートの日本語 visit_frequency も英語正準値へ正規化される.
    assert p_after.weekly_pattern["visit_frequency"] == "every"


@pytest.mark.asyncio
async def test_backward_compat_legacy_weekly_sheet_delete_clears(client, db) -> None:
    """後方互換 / HIGH 回帰防止 (Phase G-48 hotfix-2):

    旧 3 シート構成の「希望訪問パターン」シートに ``<DELETE>`` 行を含むファイルを
    import すると、(a) 例外 (TypeError) を出さず 200 で完了し、(b) 当該患者の
    weekly_pattern が None にクリアされること.

    旧経路は ``<DELETE>`` 行で merge 関数へ ``wp=None`` を渡すため、None ガードが
    無いと ``key in None`` で TypeError → import が 500 / rollback していた.
    """
    from app.services.patient_excel.schema import WEEKLY_COLUMNS

    admin = await _make_user(db, "g48-bc-legacy-del@example.com", "admin")
    p = await _make_patient(db, code="P-G48-LEGDEL", name="旧形式削除")
    # 既存 weekly_pattern (管理外キー込み) を設定 → クリア対象.
    p.weekly_pattern = {
        "time_type": "固定",
        "preferred_start": "10:00",
        "preferred_weekdays": ["Tue", "Thu"],
        "staff_count": 2,
        "entries": [{"weekday": "Tue", "start": "10:00"}],
    }
    await db.commit()
    pid = p.id

    wb = Workbook()
    ws_p = wb.active
    ws_p.title = SHEET_PATIENTS
    ws_p.append([str(c["header"]) for c in PATIENT_COLUMNS])
    prow = [None] * len(PATIENT_COLUMNS)
    prow[PATIENT_COL_INDEX["patient_id"]] = str(pid)
    prow[PATIENT_COL_INDEX["patient_code"]] = "P-G48-LEGDEL"
    ws_p.append(prow)
    pfv_ws = wb.create_sheet(SHEET_PFV)
    pfv_ws.append([str(c["header"]) for c in PFV_COLUMNS])
    # 旧 独立「希望訪問パターン」シートに <DELETE> 行.
    ws_w = wb.create_sheet(SHEET_WEEKLY)
    ws_w.append([str(c["header"]) for c in WEEKLY_COLUMNS])
    wrow = [None] * len(WEEKLY_COLUMNS)
    wrow[WEEKLY_COL_INDEX["patient_id"]] = str(pid)
    wrow[WEEKLY_COL_INDEX["patient_code"]] = "P-G48-LEGDEL"
    wrow[WEEKLY_COL_INDEX["delete_flag"]] = "<DELETE>"
    ws_w.append(wrow)
    buf = BytesIO()
    wb.save(buf)

    # (a) 例外を出さず 200 で完了する.
    res = await _upload(client, admin, content=buf.getvalue(), dry_run=False)
    assert res.status_code == 200, res.text
    db.expire_all()
    p_after = await db.get(Patient, pid)
    # (b) weekly_pattern が None にクリアされる (明示削除なので entries 等も消える).
    assert p_after.weekly_pattern is None
