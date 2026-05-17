"""Tests for /api/v1/staff/import-export/* (Excel import / export).

Coverage map (spec §7):
  1.  export: 0 名のとき空テンプレート、N 名のとき N+1 行
  2.  export: shift が staff_id でリンクされている
  3.  import dry_run: 新規行が検出される
  4.  import dry_run: 更新行で changes が diff される
  5.  import dry_run: <DELETE> フラグで delete 候補になる
  6.  import dry_run: <CLEAR> で NULL 上書き候補になる
  7.  import dry_run: 空セルは「変更しない」 (noop)
  8.  import dry_run: バリデーションエラー (UUID 不正、enum 違反、必須欠落)
  9.  import dry_run: DB は変更されない
  10. import apply: 新規 + 更新 + 削除が 1 transaction で反映
  11. import apply: partial commit — error 行は skip、有効行のみ反映
  12. import apply: staff soft-delete で関連 shift も物理削除
  13. import apply: 拠点コード → primary_office_id 解決
  14. import apply: staff_code 重複で error (skip、有効行のみ反映)
  15. RBAC: staff は 403 (3 endpoints すべて)
  16. RBAC: manager OK
  17. template ダウンロード: 1 行のみ
  18. magic word: case-insensitive
  19. shift update via composite key (staff_id, weekday)
  20. shift: 新規スタッフ + その shifts を staff_code リンクで登録 (UUID 不要)
  21. shift: is_on=FALSE のとき start/end 不要で noop / delete
  27. import apply: 全件 error → transaction_applied=False、DB 変更なし
  28. import apply: shift_error 1 件 + staff_new 2 件 → staff は反映、shift は正常分のみ反映
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
    Office,
    Staff,
    StaffShift,
    User,
)
from app.services.staff_excel.schema import (
    SHEET_SHIFT,
    SHEET_STAFF,
    SHIFT_COL_INDEX,
    SHIFT_COLUMNS,
    STAFF_COL_INDEX,
    STAFF_COLUMNS,
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


async def _make_staff(db, **overrides) -> Staff:
    defaults = {
        "code": "S-DEFAULT",
        "name": "デフォルト",
        "status": "active",
        "role": "staff",
    }
    defaults.update(overrides)
    s = Staff(**defaults)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_shift(
    db,
    *,
    staff_id,
    weekday: int = 0,
    is_on: bool = True,
    start_time=time(9, 0),
    end_time=time(18, 0),
) -> StaffShift:
    sh = StaffShift(
        staff_id=staff_id,
        weekday=weekday,
        is_on=is_on,
        start_time=start_time,
        end_time=end_time,
    )
    db.add(sh)
    await db.commit()
    await db.refresh(sh)
    return sh


def _staff_headers() -> list[str]:
    return [str(c["header"]) for c in STAFF_COLUMNS]


def _shift_headers() -> list[str]:
    return [str(c["header"]) for c in SHIFT_COLUMNS]


def _build_workbook_bytes(
    *,
    staff_rows: list[dict] | None = None,
    shift_rows: list[dict] | None = None,
) -> bytes:
    """テスト用ヘルパー: 各 dict は { col_key: value } を持つ."""
    wb = Workbook()
    ws_s = wb.active
    ws_s.title = SHEET_STAFF
    for col_idx, header in enumerate(_staff_headers(), start=1):
        ws_s.cell(row=1, column=col_idx, value=header)
    for r_idx, row_dict in enumerate(staff_rows or [], start=2):
        for col_key, idx in STAFF_COL_INDEX.items():
            v = row_dict.get(col_key)
            if v is not None:
                ws_s.cell(row=r_idx, column=idx + 1, value=v)

    ws_f = wb.create_sheet(title=SHEET_SHIFT)
    for col_idx, header in enumerate(_shift_headers(), start=1):
        ws_f.cell(row=1, column=col_idx, value=header)
    for r_idx, row_dict in enumerate(shift_rows or [], start=2):
        for col_key, idx in SHIFT_COL_INDEX.items():
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
        f"/api/v1/staff/import-export/import?dry_run={'true' if dry_run else 'false'}",
        headers=_bearer(admin),
        files=files,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# 1) export: 0 名のとき empty + N 名のとき N+1 行
@pytest.mark.asyncio
async def test_export_empty_db_returns_template_only(client, db) -> None:
    admin = await _make_user(db, "stx-empty@example.com", "admin")
    res = await client.get(
        "/api/v1/staff/import-export/export",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    wb = load_workbook(BytesIO(res.content))
    assert SHEET_STAFF in wb.sheetnames
    ws = wb[SHEET_STAFF]
    assert ws.max_row == 1  # ヘッダー 1 行のみ


@pytest.mark.asyncio
async def test_export_n_staff_writes_n_plus_1_rows(client, db) -> None:
    admin = await _make_user(db, "stx-n@example.com", "admin")
    for code in ("S-EX-001", "S-EX-002", "S-EX-003"):
        await _make_staff(db, code=code, name=f"name-{code}")

    res = await client.get(
        "/api/v1/staff/import-export/export",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws = wb[SHEET_STAFF]
    assert ws.max_row == 4
    codes = [ws.cell(row=r, column=STAFF_COL_INDEX["staff_code"] + 1).value for r in (2, 3, 4)]
    assert set(codes) == {"S-EX-001", "S-EX-002", "S-EX-003"}


# 2) export: shift が staff_id でリンクされている
@pytest.mark.asyncio
async def test_export_shift_links_to_staff_code(client, db) -> None:
    admin = await _make_user(db, "stx-sh@example.com", "admin")
    staff = await _make_staff(db, code="S-SH-LINK", name="シフト持ち")
    await _make_shift(db, staff_id=staff.id, weekday=0)

    res = await client.get(
        "/api/v1/staff/import-export/export",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    ws_f = wb[SHEET_SHIFT]
    assert ws_f.max_row == 2  # 1 header + 1 shift
    code_cell = ws_f.cell(row=2, column=SHIFT_COL_INDEX["staff_code"] + 1).value
    name_cell = ws_f.cell(row=2, column=SHIFT_COL_INDEX["staff_name"] + 1).value
    assert code_cell == "S-SH-LINK"
    assert name_cell == "シフト持ち"


# 3) dry_run: 新規行検出
@pytest.mark.asyncio
async def test_import_dry_run_detects_new_staff(client, db) -> None:
    admin = await _make_user(db, "stx-new@example.com", "admin")
    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-NEW-001",
                "name": "新規 太郎",
                "sex": "male",
                "status": "active",
                "role": "staff",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is False
    assert body["summary"]["staff_new"] == 1
    assert body["summary"]["staff_error"] == 0
    row = body["staff_rows"][0]
    assert row["operation"] == "new"
    assert row["staff_code"] == "S-NEW-001"


# 4) dry_run: 更新行で changes が diff される
@pytest.mark.asyncio
async def test_import_dry_run_diffs_updates(client, db) -> None:
    admin = await _make_user(db, "stx-upd@example.com", "admin")
    staff = await _make_staff(
        db, code="S-UPD-001", name="旧名前", kana="フルナマエ", status="active", role="staff"
    )
    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_id": str(staff.id),
                "staff_code": "S-UPD-001",
                "name": "新名前",
                "status": "active",
                "role": "staff",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["staff_update"] == 1
    row = body["staff_rows"][0]
    assert row["operation"] == "update"
    fields = {c["field"]: c for c in row["changes"]}
    assert "name" in fields
    assert fields["name"]["old_value"] == "旧名前"
    assert fields["name"]["new_value"] == "新名前"


# 5) dry_run: <DELETE> フラグ
@pytest.mark.asyncio
async def test_import_dry_run_detects_delete_flag(client, db) -> None:
    admin = await _make_user(db, "stx-del@example.com", "admin")
    staff = await _make_staff(db, code="S-DEL-001", name="削除予定")
    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_id": str(staff.id),
                "staff_code": "S-DEL-001",
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["staff_delete"] == 1
    assert body["staff_rows"][0]["operation"] == "delete"


# 6) dry_run: <CLEAR> で NULL 上書き
@pytest.mark.asyncio
async def test_import_dry_run_clear_to_null(client, db) -> None:
    admin = await _make_user(db, "stx-clr@example.com", "admin")
    staff = await _make_staff(db, code="S-CLR-001", name="クリア対象", note="残ってる備考")
    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_id": str(staff.id),
                "staff_code": "S-CLR-001",
                "name": "クリア対象",
                "status": "active",
                "role": "staff",
                "note": "<CLEAR>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    row = body["staff_rows"][0]
    assert row["operation"] == "update"
    notes = [c for c in row["changes"] if c["field"] == "note"]
    assert len(notes) == 1
    assert notes[0]["new_value"] is None
    assert notes[0]["old_value"] == "残ってる備考"


# 7) dry_run: 空セルは noop
@pytest.mark.asyncio
async def test_import_dry_run_blank_cells_are_noop(client, db) -> None:
    admin = await _make_user(db, "stx-noop@example.com", "admin")
    staff = await _make_staff(
        db,
        code="S-NOOP-001",
        name="変更なし",
        status="active",
        role="staff",
        kana="ヘンコウナシ",
    )
    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_id": str(staff.id),
                "staff_code": "S-NOOP-001",
                # 他全部空 → 「触らない」
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    row = body["staff_rows"][0]
    assert row["operation"] == "noop"
    assert body["summary"]["staff_noop"] == 1


# 8) dry_run: バリデーションエラー多種
@pytest.mark.asyncio
async def test_import_dry_run_validation_errors(client, db) -> None:
    admin = await _make_user(db, "stx-err@example.com", "admin")
    content = _build_workbook_bytes(
        staff_rows=[
            # 行 2: UUID 不正
            {
                "staff_id": "not-a-uuid",
                "staff_code": "S-BAD-1",
                "name": "uuid不正",
            },
            # 行 3: enum 違反
            {
                "staff_code": "S-BAD-2",
                "name": "enum違反",
                "sex": "INVALID",
                "status": "active",
                "role": "staff",
            },
            # 行 4: 必須欠落 (name)
            {
                "staff_code": "S-BAD-3",
                "status": "active",
                "role": "staff",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["staff_error"] == 3
    ops = {r["row_number"]: r for r in body["staff_rows"]}
    assert ops[2]["operation"] == "error"
    assert ops[3]["operation"] == "error"
    assert ops[4]["operation"] == "error"
    assert "UUID" in ops[2]["error_message"] or "uuid" in ops[2]["error_message"].lower()
    assert "sex" in ops[3]["error_message"].lower() or "性別" in ops[3]["error_message"]
    assert "name" in ops[4]["error_message"].lower() or "新規作成" in ops[4]["error_message"]


# 9) dry_run: DB は変更されない
@pytest.mark.asyncio
async def test_import_dry_run_does_not_modify_db(client, db) -> None:
    admin = await _make_user(db, "stx-noch@example.com", "admin")
    before_count = len((await db.scalars(select(Staff))).all())

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-DRY-001",
                "name": "新規",
                "status": "active",
                "role": "staff",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    assert res.status_code == 200
    assert res.json()["transaction_applied"] is False

    db.expire_all()
    after_count = len((await db.scalars(select(Staff))).all())
    assert after_count == before_count


# 10) apply: 新規 + 更新 + 削除 が 1 TX で反映
@pytest.mark.asyncio
async def test_import_apply_mixed_operations(client, db) -> None:
    admin = await _make_user(db, "stx-apply@example.com", "admin")
    s_upd = await _make_staff(db, code="S-AP-UPD", name="旧", status="active", role="staff")
    s_del = await _make_staff(db, code="S-AP-DEL", name="消す", status="active", role="staff")

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-AP-NEW",
                "name": "新規",
                "sex": "female",
                "status": "active",
                "role": "staff",
            },
            {
                "staff_id": str(s_upd.id),
                "staff_code": "S-AP-UPD",
                "name": "新",
                "status": "active",
                "role": "staff",
            },
            {
                "staff_id": str(s_del.id),
                "staff_code": "S-AP-DEL",
                "delete_flag": "<DELETE>",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"] == {
        "staff_new": 1,
        "staff_update": 1,
        "staff_delete": 1,
        "staff_error": 0,
        "staff_noop": 0,
        "shift_new": 0,
        "shift_update": 0,
        "shift_delete": 0,
        "shift_error": 0,
        "shift_noop": 0,
    }

    db.expire_all()
    rows = (
        await db.scalars(select(Staff).where(Staff.code.in_(["S-AP-NEW", "S-AP-UPD", "S-AP-DEL"])))
    ).all()
    by_code = {s.code: s for s in rows}
    assert by_code["S-AP-NEW"].deleted_at is None
    assert by_code["S-AP-UPD"].name == "新"
    assert by_code["S-AP-DEL"].deleted_at is not None  # soft delete


# 11) apply: partial commit — error 行は skip、有効行のみ反映
@pytest.mark.asyncio
async def test_import_apply_partial_commit_skips_error_rows(client, db) -> None:
    """error 1 件 + 正常 update 1 件 → 正常 update は commit、error 行は skip."""
    admin = await _make_user(db, "stx-rb@example.com", "admin")
    s_upd = await _make_staff(db, code="S-RB-UPD", name="旧")
    s_upd_id = s_upd.id

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_id": str(s_upd_id),
                "staff_code": "S-RB-UPD",
                "name": "正常な更新",
                "status": "active",
                "role": "staff",
            },
            {
                "staff_code": "S-RB-ERR",
                "name": "エラー行",
                "sex": "INVALID",
                "status": "active",
                "role": "staff",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    body = res.json()
    # 有効な op (update 1) があるので transaction は commit される.
    assert body["transaction_applied"] is True
    assert body["summary"]["staff_error"] == 1
    assert body["summary"]["staff_update"] == 1

    # S-RB-UPD は更新されているはず (partial commit).
    db.expire_all()
    refreshed = await db.get(Staff, s_upd_id)
    assert refreshed.name == "正常な更新"
    # S-RB-ERR は skip されているので DB に存在しない.
    err_row = await db.scalar(select(Staff).where(Staff.code == "S-RB-ERR"))
    assert err_row is None


# 12) apply: staff soft-delete で関連 shift も物理削除
@pytest.mark.asyncio
async def test_staff_delete_cascades_shift_cleanup(client, db) -> None:
    admin = await _make_user(db, "stx-delcasc@example.com", "admin")
    staff = await _make_staff(db, code="S-DELCASC", name="orphan source")
    staff_id = staff.id
    shift = await _make_shift(db, staff_id=staff_id, weekday=0)
    # 復合主キーなので id 列はない; (staff_id, weekday) で識別.

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_id": str(staff_id),
                "staff_code": "S-DELCASC",
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["staff_delete"] == 1

    db.expire_all()
    staff_after = await db.get(Staff, staff_id)
    assert staff_after is not None
    assert staff_after.deleted_at is not None  # soft delete
    # 関連 shift は物理削除されているはず.
    remaining = (await db.scalars(select(StaffShift).where(StaffShift.staff_id == staff_id))).all()
    assert remaining == []
    # shift fixture も解放しておく
    del shift


# 13) apply: 拠点コード解決
@pytest.mark.asyncio
async def test_import_apply_resolves_office_code(client, db) -> None:
    admin = await _make_user(db, "stx-off@example.com", "admin")
    office = await _make_office(db, code="TSUGA", name="都賀")
    office_id = office.id

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-OFF-001",
                "name": "拠点指定",
                "status": "active",
                "role": "staff",
                "office_code": "TSUGA",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True

    db.expire_all()
    rows = (await db.scalars(select(Staff).where(Staff.code == "S-OFF-001"))).all()
    assert len(rows) == 1
    assert rows[0].primary_office_id == office_id


# 14) apply: staff_code 重複 → error 行は skip (有効 op が他に無ければ TX 非実行)
@pytest.mark.asyncio
async def test_import_apply_duplicate_code_errors(client, db) -> None:
    admin = await _make_user(db, "stx-dup@example.com", "admin")
    await _make_staff(db, code="S-DUP-001", name="既存")

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-DUP-001",  # 重複!
                "name": "重複新規",
                "status": "active",
                "role": "staff",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    body = res.json()
    # 唯一の行が error なので有効 op = 0 → transaction_applied=False.
    assert body["transaction_applied"] is False
    assert body["summary"]["staff_error"] == 1
    assert "S-DUP-001" in body["staff_rows"][0]["error_message"]


# 14b) 同一ファイル内で staff_code が重複していたら 2 行目を error にする
@pytest.mark.asyncio
async def test_import_in_file_duplicate_code_errors(client, db) -> None:
    admin = await _make_user(db, "stx-infile@example.com", "admin")
    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-INFILE-DUP",
                "name": "1st",
                "status": "active",
                "role": "staff",
            },
            {
                "staff_code": "S-INFILE-DUP",
                "name": "2nd",
                "status": "active",
                "role": "staff",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["staff_new"] == 1
    assert body["summary"]["staff_error"] == 1


# 15) RBAC: staff は 403
@pytest.mark.asyncio
async def test_import_export_requires_admin_or_manager(client, db) -> None:
    staff_user = await _make_user(db, "stx-staff@example.com", "staff")
    res = await client.get(
        "/api/v1/staff/import-export/export",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403

    res = await client.get(
        "/api/v1/staff/import-export/template",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403

    # POST /import も同様に staff は 403.
    content = _build_workbook_bytes(staff_rows=[])
    files = {
        "file": (
            "test.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    res = await client.post(
        "/api/v1/staff/import-export/import?dry_run=true",
        headers=_bearer(staff_user),
        files=files,
    )
    assert res.status_code == 403


# 16) RBAC: manager は import endpoint に到達できる (200).
@pytest.mark.asyncio
async def test_import_allows_manager_role(client, db) -> None:
    manager = await _make_user(db, "stx-mgr@example.com", "manager")
    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-MGR-OK",
                "name": "manager OK",
                "status": "active",
                "role": "staff",
            }
        ],
    )
    res = await _upload(client, manager, content=content, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["staff_new"] == 1


# 17) template ダウンロード: 1 行 (ヘッダーのみ)
@pytest.mark.asyncio
async def test_template_download_has_header_only(client, db) -> None:
    admin = await _make_user(db, "stx-tpl@example.com", "admin")
    res = await client.get(
        "/api/v1/staff/import-export/template",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    wb = load_workbook(BytesIO(res.content))
    assert SHEET_STAFF in wb.sheetnames
    assert SHEET_SHIFT in wb.sheetnames
    assert wb[SHEET_STAFF].max_row == 1
    assert wb[SHEET_SHIFT].max_row == 1


# 18) magic word: case-insensitive
@pytest.mark.asyncio
async def test_magic_word_case_insensitive(client, db) -> None:
    """<delete> (lower-case) も認識される."""
    admin = await _make_user(db, "stx-magic@example.com", "admin")
    staff = await _make_staff(db, code="S-MAGIC-001", name="ケース不問")

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_id": str(staff.id),
                "staff_code": "S-MAGIC-001",
                "delete_flag": "<delete>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["staff_delete"] == 1
    assert body["staff_rows"][0]["operation"] == "delete"


# 19) shift update via composite key (staff_id, weekday)
@pytest.mark.asyncio
async def test_shift_update_via_composite_key(client, db) -> None:
    admin = await _make_user(db, "stx-shup@example.com", "admin")
    staff = await _make_staff(db, code="S-SH-UP", name="SH更新")
    await _make_shift(db, staff_id=staff.id, weekday=2, start_time=time(9, 0), end_time=time(18, 0))
    staff_id = staff.id

    content = _build_workbook_bytes(
        shift_rows=[
            {
                "staff_id": str(staff_id),
                "weekday": "水",
                "is_on": "TRUE",
                "start_time": "10:30",
                "end_time": "19:00",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["shift_update"] == 1

    db.expire_all()
    refreshed = await db.scalar(
        select(StaffShift).where(StaffShift.staff_id == staff_id, StaffShift.weekday == 2)
    )
    assert refreshed.start_time == time(10, 30)
    assert refreshed.end_time == time(19, 0)


# 20) shift: 新規 staff + その shifts を staff_code リンクで登録 (UUID 不要)
@pytest.mark.asyncio
async def test_new_staff_with_shifts_via_code_link(client, db) -> None:
    admin = await _make_user(db, "stx-link@example.com", "admin")

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-LINK-001",
                "name": "リンク新規",
                "status": "active",
                "role": "staff",
            }
        ],
        shift_rows=[
            {
                # staff_id 空, staff_code で参照
                "staff_code": "S-LINK-001",
                "weekday": "月",
                "is_on": "TRUE",
                "start_time": "09:00",
                "end_time": "18:00",
            },
            {
                "staff_code": "S-LINK-001",
                "weekday": "火",
                "is_on": "TRUE",
                "start_time": "09:00",
                "end_time": "18:00",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["staff_new"] == 1
    assert body["summary"]["shift_new"] == 2

    db.expire_all()
    new_staff = await db.scalar(select(Staff).where(Staff.code == "S-LINK-001"))
    assert new_staff is not None
    shifts = (await db.scalars(select(StaffShift).where(StaffShift.staff_id == new_staff.id))).all()
    assert len(shifts) == 2
    weekdays = sorted(s.weekday for s in shifts)
    assert weekdays == [0, 1]


# 21) shift: is_on=FALSE のとき start/end が空でも error にならない
@pytest.mark.asyncio
async def test_shift_is_on_false_allows_empty_times(client, db) -> None:
    admin = await _make_user(db, "stx-off-day@example.com", "admin")
    staff = await _make_staff(db, code="S-OFFDAY", name="休み日あり")
    staff_id = staff.id  # expire_all 前に id を確保

    content = _build_workbook_bytes(
        shift_rows=[
            {
                "staff_id": str(staff_id),
                "weekday": "日",
                "is_on": "FALSE",
                # start_time / end_time 空 → OK
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["shift_new"] == 1

    db.expire_all()
    sh = await db.scalar(
        select(StaffShift).where(StaffShift.staff_id == staff_id, StaffShift.weekday == 6)
    )
    assert sh is not None
    assert sh.is_on is False


# 22) ファイル size 10MB 超で 413
@pytest.mark.asyncio
async def test_upload_too_large_413(client, db) -> None:
    admin = await _make_user(db, "stx-big@example.com", "admin")
    # 10MB + 1 byte の dummy bytes.
    big = b"x" * (10 * 1024 * 1024 + 1)
    files = {
        "file": (
            "huge.xlsx",
            big,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    res = await client.post(
        "/api/v1/staff/import-export/import?dry_run=true",
        headers=_bearer(admin),
        files=files,
    )
    assert res.status_code == 413


# 23) 存在しない staff_id を指定したらエラー
@pytest.mark.asyncio
async def test_unknown_staff_id_errors(client, db) -> None:
    admin = await _make_user(db, "stx-unk@example.com", "admin")
    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_id": str(uuid4()),  # 存在しない
                "staff_code": "S-UNK-001",
                "name": "存在しない",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["staff_error"] == 1
    assert "存在" in body["staff_rows"][0]["error_message"]


# 24) HIGH#2 regression: 既存 shift に staff_id+weekday のみ記入 → noop
@pytest.mark.asyncio
async def test_shift_blank_cells_preserve_existing(client, db) -> None:
    """blank cell = 既存値継承の契約. is_on=TRUE, start=09:00, end=18:00 な既存
    shift に対して staff_id + weekday のみの行を import すると、本来 noop に
    なるべき (現状は『勤務=TRUE のとき開始/終了時刻必須』エラーになる回帰防止)."""
    admin = await _make_user(db, "stx-blank@example.com", "admin")
    staff = await _make_staff(db, code="S-BLANK", name="空セル継承")
    await _make_shift(
        db, staff_id=staff.id, weekday=0, is_on=True, start_time=time(9, 0), end_time=time(18, 0)
    )
    staff_id = staff.id

    content = _build_workbook_bytes(
        shift_rows=[
            {
                "staff_id": str(staff_id),
                "weekday": "月",
                # is_on / start_time / end_time すべて空 → 既存値継承で noop
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["shift_error"] == 0, body
    assert body["summary"]["shift_noop"] == 1
    assert body["shift_rows"][0]["operation"] == "noop"


# 25) HIGH#2 regression: 既存 shift で end_time だけ変更 → single-field update
@pytest.mark.asyncio
async def test_shift_partial_update_only_end_time(client, db) -> None:
    admin = await _make_user(db, "stx-partial@example.com", "admin")
    staff = await _make_staff(db, code="S-PART", name="部分更新")
    await _make_shift(
        db, staff_id=staff.id, weekday=1, is_on=True, start_time=time(9, 0), end_time=time(18, 0)
    )
    staff_id = staff.id

    content = _build_workbook_bytes(
        shift_rows=[
            {
                "staff_id": str(staff_id),
                "weekday": "火",
                # is_on / start_time は空 = 既存値継承
                "end_time": "20:00",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["shift_update"] == 1
    row = body["shift_rows"][0]
    changes = {c["field"]: c for c in row["changes"]}
    assert "end_time" in changes
    assert changes["end_time"]["new_value"] == "20:00"
    # start_time / is_on は変更されていない (= changes に含まれない)
    assert "start_time" not in changes
    assert "is_on" not in changes

    db.expire_all()
    refreshed = await db.scalar(
        select(StaffShift).where(StaffShift.staff_id == staff_id, StaffShift.weekday == 1)
    )
    assert refreshed.start_time == time(9, 0)  # 既存値維持
    assert refreshed.end_time == time(20, 0)  # 更新
    assert refreshed.is_on is True


# 26) HIGH#3 regression: DB レベルの UNIQUE 制約 (partial UNIQUE INDEX)
@pytest.mark.asyncio
async def test_db_unique_constraint_on_staff_code(db) -> None:
    """staff.code に対して deleted_at IS NULL AND code IS NOT NULL の partial
    UNIQUE INDEX が効いていること (concurrent import race condition 防止).

    SQLite 3.8+ で同条件の partial UNIQUE INDEX をサポートするためテストで再現可."""
    from sqlalchemy.exc import IntegrityError

    await _make_staff(db, code="S-UNIQUE-001", name="既存")

    duplicate = Staff(code="S-UNIQUE-001", name="重複", status="active", role="staff")
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# 27) partial commit: error 1 件 + new 2 件 → new 2 件のみ DB に反映
@pytest.mark.asyncio
async def test_import_apply_partial_commit_new_rows_with_one_error(client, db) -> None:
    """error 1 件 + new 2 件 で apply → DB に new 2 件のみ反映、error 1 件は skip."""
    admin = await _make_user(db, "stx-partial-mix@example.com", "admin")

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-PC-OK-1",
                "name": "正常 1",
                "status": "active",
                "role": "staff",
            },
            {
                "staff_code": "S-PC-ERR",
                "name": "エラー (enum 違反)",
                "sex": "INVALID",
                "status": "active",
                "role": "staff",
            },
            {
                "staff_code": "S-PC-OK-2",
                "name": "正常 2",
                "status": "active",
                "role": "staff",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["staff_new"] == 2
    assert body["summary"]["staff_error"] == 1

    # DB: 正常 2 件のみ反映 (error 行は skip)
    db.expire_all()
    rows = (
        await db.scalars(
            select(Staff).where(Staff.code.in_(["S-PC-OK-1", "S-PC-ERR", "S-PC-OK-2"]))
        )
    ).all()
    codes = sorted(s.code for s in rows)
    assert codes == ["S-PC-OK-1", "S-PC-OK-2"]


# 28) partial commit: 全件 error → transaction_applied=False、DB 変更なし
@pytest.mark.asyncio
async def test_import_apply_all_errors_no_commit(client, db) -> None:
    admin = await _make_user(db, "stx-partial-allerr@example.com", "admin")
    before_count = len((await db.scalars(select(Staff))).all())

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-ALL-ERR-1",
                "name": "エラー 1",
                "sex": "INVALID",
                "status": "active",
                "role": "staff",
            },
            {
                "staff_code": "S-ALL-ERR-2",
                "name": "エラー 2",
                "status": "INVALID_STATUS",
                "role": "staff",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    # 有効 op = 0 件 → 非 commit.
    assert body["transaction_applied"] is False
    assert body["summary"]["staff_error"] == 2

    # DB は変更なし.
    db.expire_all()
    after_count = len((await db.scalars(select(Staff))).all())
    assert after_count == before_count


# 29) partial commit: shift_error 1 件 + staff_new 2 件 → staff 反映、shift は正常分のみ反映
@pytest.mark.asyncio
async def test_import_apply_partial_commit_mixed_staff_and_shift_errors(client, db) -> None:
    """staff シートに正常 new 2 件、shift シートに error 1 件 + 正常 new 1 件.
    → staff 2 件 + shift 1 件 が commit される (shift error 1 件は skip)."""
    admin = await _make_user(db, "stx-partial-shift@example.com", "admin")

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-MIX-A",
                "name": "スタッフ A",
                "status": "active",
                "role": "staff",
            },
            {
                "staff_code": "S-MIX-B",
                "name": "スタッフ B",
                "status": "active",
                "role": "staff",
            },
        ],
        shift_rows=[
            {
                # 正常 shift 行 (スタッフ A 紐付け via code)
                "staff_code": "S-MIX-A",
                "weekday": "月",
                "is_on": "TRUE",
                "start_time": "09:00",
                "end_time": "18:00",
            },
            {
                # error 行: weekday の値が候補外
                "staff_code": "S-MIX-B",
                "weekday": "INVALID_DAY",
                "is_on": "TRUE",
                "start_time": "10:00",
                "end_time": "19:00",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["staff_new"] == 2
    assert body["summary"]["shift_new"] == 1
    assert body["summary"]["shift_error"] == 1

    db.expire_all()
    # スタッフ 2 件はどちらも DB にいる.
    staff_rows = (
        await db.scalars(select(Staff).where(Staff.code.in_(["S-MIX-A", "S-MIX-B"])))
    ).all()
    assert {s.code for s in staff_rows} == {"S-MIX-A", "S-MIX-B"}
    by_code = {s.code: s for s in staff_rows}
    # スタッフ A の shift のみ DB に存在 (スタッフ B の error 行は skip).
    shifts_a = (
        await db.scalars(select(StaffShift).where(StaffShift.staff_id == by_code["S-MIX-A"].id))
    ).all()
    assert len(shifts_a) == 1
    shifts_b = (
        await db.scalars(select(StaffShift).where(StaffShift.staff_id == by_code["S-MIX-B"].id))
    ).all()
    assert shifts_b == []


# 30) bug 2: staff_id 空 + staff_code = 既存スタッフ + <DELETE> で削除成功
@pytest.mark.asyncio
async def test_import_apply_delete_via_staff_code_only(client, db) -> None:
    """ユーザーが手で「削除したい code + <DELETE>」だけ書いた Excel でも削除できる."""
    admin = await _make_user(db, "stx-delcode@example.com", "admin")
    staff = await _make_staff(db, code="S-DELCODE-001", name="code削除")
    staff_id = staff.id

    content = _build_workbook_bytes(
        staff_rows=[
            {
                # staff_id 空 + staff_code のみ + <DELETE>
                "staff_code": "S-DELCODE-001",
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["staff_delete"] == 1
    row = body["staff_rows"][0]
    assert row["operation"] == "delete"
    assert row["staff_code"] == "S-DELCODE-001"

    db.expire_all()
    refreshed = await db.get(Staff, staff_id)
    assert refreshed is not None
    assert refreshed.deleted_at is not None  # soft delete されている


# 31) bug 2: staff_id 空 + 存在しない staff_code + <DELETE> は idempotent noop
@pytest.mark.asyncio
async def test_import_delete_via_unknown_staff_code_is_noop(client, db) -> None:
    """既に削除済み (or そもそも居ない) code を <DELETE> しても error にせず noop."""
    admin = await _make_user(db, "stx-delnoop@example.com", "admin")
    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-DELNOOP-XYZ",
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["staff_noop"] == 1
    assert body["summary"]["staff_error"] == 0
    assert body["staff_rows"][0]["operation"] == "noop"


# 32) bug 2: staff_id 空 + staff_code 空 + <DELETE> は error
@pytest.mark.asyncio
async def test_import_delete_with_both_id_and_code_blank_errors(client, db) -> None:
    admin = await _make_user(db, "stx-delblank@example.com", "admin")
    content = _build_workbook_bytes(
        staff_rows=[
            {
                # 両方空
                "delete_flag": "<DELETE>",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    body = res.json()
    assert body["summary"]["staff_error"] == 1
    msg = body["staff_rows"][0]["error_message"]
    assert "staff_id" in msg and "staff_code" in msg


# 33) resurrection: soft-deleted staff_code を Excel で再 import すると復活する
@pytest.mark.asyncio
async def test_resurrection_via_code_for_soft_deleted_staff(client, db) -> None:
    """UI で削除 (soft delete) → 同じ code を Excel で再 import.

    soft-deleted 行があるまま INSERT すると UNIQUE 制約違反で 409 になるため、
    importer は INSERT ではなく既存行の deleted_at=NULL + 内容更新で復活させる.
    """
    admin = await _make_user(db, "stx-resurrect@example.com", "admin")
    staff = await _make_staff(
        db, code="S-RES-001", name="削除前の名前", status="active", role="staff"
    )
    staff.deleted_at = datetime.now(UTC)
    await db.commit()

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-RES-001",
                "name": "復活後の名前",
                "status": "active",
                "role": "manager",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["staff_update"] == 1
    assert body["summary"]["staff_new"] == 0
    assert body["summary"]["staff_error"] == 0
    row = body["staff_rows"][0]
    assert row["operation"] == "update"
    assert row["staff_code"] == "S-RES-001"
    fields = {c["field"]: c for c in row["changes"]}
    assert "deleted_at" in fields, f"changes に deleted_at が無い: {row['changes']}"
    assert fields["deleted_at"]["new_value"] is None
    assert fields["deleted_at"]["old_value"] is not None
    assert "name" in fields and fields["name"]["new_value"] == "復活後の名前"
    assert "role" in fields and fields["role"]["new_value"] == "manager"

    await db.refresh(staff)
    assert staff.deleted_at is None
    assert staff.name == "復活後の名前"
    assert staff.role == "manager"


# 34) resurrection: 復活時に staff の id が再発番されないこと (UUID 継続性)
@pytest.mark.asyncio
async def test_resurrection_preserves_id(client, db) -> None:
    """復活時に id は元のまま (新しい UUID は発番されない)."""
    admin = await _make_user(db, "stx-resurrect-id@example.com", "admin")
    staff = await _make_staff(db, code="S-RES-ID", name="UUID 継続テスト")
    original_id = staff.id
    staff.deleted_at = datetime.now(UTC)
    await db.commit()

    content = _build_workbook_bytes(
        staff_rows=[
            {
                "staff_code": "S-RES-ID",
                "name": "復活した",
                "status": "active",
                "role": "staff",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    row = body["staff_rows"][0]
    assert row["operation"] == "update"
    assert row["staff_id"] == str(original_id)

    await db.refresh(staff)
    assert staff.id == original_id
    assert staff.deleted_at is None
    assert staff.code == "S-RES-ID"
    rows = (await db.scalars(select(Staff).where(Staff.code == "S-RES-ID"))).all()
    assert len(rows) == 1
    assert rows[0].id == original_id
