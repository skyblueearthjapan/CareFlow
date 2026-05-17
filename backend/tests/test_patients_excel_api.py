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


# 15) apply: patient_code 重複 → error 行は skip (有効 op が他に無ければ TX 非実行)
@pytest.mark.asyncio
async def test_import_apply_duplicate_code_errors(client, db) -> None:
    admin = await _make_user(db, "im-dup@example.com", "admin")
    await _make_patient(db, code="P-DUP-001", name="既存")

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-DUP-001",  # 重複!
                "name": "重複新規",
                "sex": "male",
                "status": "active",
                "address": "addr",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    body = res.json()
    # 唯一の行が error なので有効 op = 0 → transaction_applied=False.
    assert body["transaction_applied"] is False
    assert body["summary"]["patients_error"] == 1
    assert "P-DUP-001" in body["patient_rows"][0]["error_message"]


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
    assert rows[0].id == original_id
