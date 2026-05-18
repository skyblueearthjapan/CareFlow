"""Tests for /api/v1/patients/import-export/replace-all (完全置換).

Coverage:
  1. RBAC: admin 200 / manager 403 / staff 403
  2. dry_run=True: DB 変更なし、削除対象一覧が返る
  3. apply: Excel に無い既存患者が soft delete される
  4. apply: 空セルが NULL で上書きされる
  5. apply: 既存 PFV が全件物理削除されて Excel から再投入される
  6. apply: error 1 件で全 rollback (transaction_applied=False, 422)
  7. apply: 新規患者 + 既存更新 + 削除 + 復活 mix
  8. summary 件数の正確性
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from io import BytesIO

import pytest
from openpyxl import Workbook
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
        "address": "千葉市稲毛区test",
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
    course_template_id=None,
    start_time=time(9, 0),
    duration_min: int = 30,
):
    pfv = PatientFixedVisit(
        patient_id=patient_id,
        mode="normal",
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


async def _upload(client, user, *, content: bytes, dry_run: bool = True):
    files = {
        "file": (
            "test.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    return await client.post(
        f"/api/v1/patients/import-export/replace-all?dry_run={'true' if dry_run else 'false'}",
        headers=_bearer(user),
        files=files,
    )


# ---------------------------------------------------------------------------
# 1) RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_admin_ok(client, db) -> None:
    admin = await _make_user(db, "ra-admin@example.com", "admin")
    content = _build_workbook_bytes(patient_rows=[])
    res = await _upload(client, admin, content=content, dry_run=True)
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_replace_all_manager_forbidden(client, db) -> None:
    manager = await _make_user(db, "ra-mgr@example.com", "manager")
    content = _build_workbook_bytes(patient_rows=[])
    res = await _upload(client, manager, content=content, dry_run=True)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_replace_all_staff_forbidden(client, db) -> None:
    staff_user = await _make_user(db, "ra-staff@example.com", "staff")
    content = _build_workbook_bytes(patient_rows=[])
    res = await _upload(client, staff_user, content=content, dry_run=True)
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# 2) dry_run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_dry_run_returns_soft_delete_list_no_db_change(client, db) -> None:
    admin = await _make_user(db, "ra-dry@example.com", "admin")
    # 既存 alive 2 名
    p_keep = await _make_patient(db, code="P-RA-KEEP", name="残す")
    p_keep_id = p_keep.id
    p_drop = await _make_patient(db, code="P-RA-DROP", name="消える")
    p_drop_id = p_drop.id

    # Excel には KEEP のみ
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(p_keep_id),
                "patient_code": "P-RA-KEEP",
                "name": "残す",
                "sex": "male",
                "status": "active",
                "address": "千葉市稲毛区test",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is False
    assert body["summary"]["patients_to_soft_delete"] == 1
    assert body["summary"]["patients_to_update"] >= 0  # name 同じなら noop
    assert len(body["patients_to_soft_delete_preview"]) == 1
    assert body["patients_to_soft_delete_preview"][0]["patient_code"] == "P-RA-DROP"

    # DB 変更なし
    db.expire_all()
    p_drop_after = await db.get(Patient, p_drop_id)
    assert p_drop_after is not None
    assert p_drop_after.deleted_at is None


# ---------------------------------------------------------------------------
# 3) apply: 削除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_apply_soft_deletes_missing_patients(client, db) -> None:
    admin = await _make_user(db, "ra-del@example.com", "admin")
    p_keep = await _make_patient(db, code="P-RA-K1", name="残す")
    p_keep_id = p_keep.id
    p_drop = await _make_patient(db, code="P-RA-D1", name="消える")
    p_drop_id = p_drop.id
    # PFV 付き
    pfv_drop = await _make_pfv(db, patient_id=p_drop_id, weekday=0)
    pfv_drop_id = pfv_drop.id

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(p_keep_id),
                "patient_code": "P-RA-K1",
                "name": "残す",
                "sex": "male",
                "status": "active",
                "address": "千葉市稲毛区test",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["patients_to_soft_delete"] == 1

    db.expire_all()
    p_keep_after = await db.get(Patient, p_keep_id)
    assert p_keep_after is not None and p_keep_after.deleted_at is None
    p_drop_after = await db.get(Patient, p_drop_id)
    assert p_drop_after is not None and p_drop_after.deleted_at is not None  # soft deleted
    # PFV は物理削除
    pfv_after = await db.get(PatientFixedVisit, pfv_drop_id)
    assert pfv_after is None


# ---------------------------------------------------------------------------
# 4) apply: 空セル → NULL 上書き
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_apply_blank_cells_overwrite_to_null(client, db) -> None:
    admin = await _make_user(db, "ra-null@example.com", "admin")
    patient = await _make_patient(
        db,
        code="P-RA-NUL",
        name="保持",
        sex="male",
        status="active",
        address="千葉市稲毛区test",
        kana="ホジ",
        note="残ってるはずの備考",
    )
    patient_id = patient.id

    # Excel では kana/note を空セルに (= NULL に上書きされるはず)
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient_id),
                "patient_code": "P-RA-NUL",
                "name": "保持",
                "sex": "male",
                "status": "active",
                "address": "千葉市稲毛区test",
                # kana / note 空セル
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True

    db.expire_all()
    p_after = await db.get(Patient, patient_id)
    assert p_after is not None
    assert p_after.kana is None  # NULL で上書きされた
    assert p_after.note is None


# ---------------------------------------------------------------------------
# 5) apply: PFV 全件 replace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_apply_pfv_replaces_all(client, db) -> None:
    admin = await _make_user(db, "ra-pfv@example.com", "admin")
    patient = await _make_patient(db, code="P-RA-PFV", name="PFV テスト")
    patient_id = patient.id
    # 既存 PFV 2 件
    pfv1 = await _make_pfv(db, patient_id=patient_id, weekday=0)
    pfv2 = await _make_pfv(db, patient_id=patient_id, weekday=1)
    pfv1_id = pfv1.id
    pfv2_id = pfv2.id

    # Excel では 1 件のみ (異なる weekday)
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient_id),
                "patient_code": "P-RA-PFV",
                "name": "PFV テスト",
                "sex": "male",
                "status": "active",
                "address": "千葉市稲毛区test",
            }
        ],
        pfv_rows=[
            {
                "patient_id": str(patient_id),
                "weekday": "水",
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
    assert body["summary"]["pfv_to_create"] == 1
    # 既存 PFV 2 件 + (削除対象 patient はゼロ) → pfv_to_replace=2
    assert body["summary"]["pfv_to_replace"] == 2

    db.expire_all()
    # 旧 PFV は両方消えてる
    assert await db.get(PatientFixedVisit, pfv1_id) is None
    assert await db.get(PatientFixedVisit, pfv2_id) is None
    # 新 PFV が 1 件入ってる
    remaining = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()
    assert len(remaining) == 1
    assert remaining[0].weekday == 2  # 水
    assert remaining[0].duration_min == 45


# ---------------------------------------------------------------------------
# 6) apply: error → 全 rollback (422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_apply_error_rolls_back_everything(client, db) -> None:
    """完全置換は atomic. error 1 件あれば全 rollback (422 返却)."""
    admin = await _make_user(db, "ra-rollback@example.com", "admin")
    patient = await _make_patient(
        db, code="P-RA-RB", name="ロールバック前", sex="male", status="active", address="addr"
    )
    patient_id = patient.id

    # 正常 update 1 件 + error 1 件 (enum 違反) → atomic で何も適用されないこと
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient_id),
                "patient_code": "P-RA-RB",
                "name": "更新後",
                "sex": "male",
                "status": "active",
                "address": "addr-new",
            },
            {
                "patient_code": "P-RA-BAD",
                "name": "エラー行",
                "sex": "INVALID",  # enum 違反
                "status": "active",
                "address": "addr",
            },
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 422, res.text
    # 既存患者は更新されていないこと
    db.expire_all()
    p_after = await db.get(Patient, patient_id)
    assert p_after is not None
    assert p_after.name == "ロールバック前"  # 更新されていない


# ---------------------------------------------------------------------------
# 7) apply mix: new + update + delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_apply_mix_new_update_delete(client, db) -> None:
    admin = await _make_user(db, "ra-mix@example.com", "admin")
    p_upd = await _make_patient(db, code="P-RA-U", name="旧", sex="male", status="active")
    p_upd_id = p_upd.id
    p_del = await _make_patient(db, code="P-RA-X", name="消える", sex="male", status="active")
    p_del_id = p_del.id

    content = _build_workbook_bytes(
        patient_rows=[
            # update 行
            {
                "patient_id": str(p_upd_id),
                "patient_code": "P-RA-U",
                "name": "新",
                "sex": "male",
                "status": "active",
                "address": "addr-new",
            },
            # new 行
            {
                "patient_code": "P-RA-N",
                "name": "新規",
                "sex": "female",
                "status": "active",
                "address": "addr-n",
            },
            # P-RA-X は Excel に無い → soft delete
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    s = body["summary"]
    assert s["patients_to_create"] == 1
    assert s["patients_to_update"] == 1
    assert s["patients_to_soft_delete"] == 1
    assert s["patients_error"] == 0

    db.expire_all()
    new_p = await db.scalar(select(Patient).where(Patient.code == "P-RA-N"))
    assert new_p is not None
    assert new_p.deleted_at is None
    upd_p = await db.get(Patient, p_upd_id)
    assert upd_p.name == "新"
    del_p = await db.get(Patient, p_del_id)
    assert del_p.deleted_at is not None


# ---------------------------------------------------------------------------
# 8) 復活: soft-deleted 同じ code を Excel で再登録
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_resurrects_soft_deleted_patient(client, db) -> None:
    admin = await _make_user(db, "ra-res@example.com", "admin")
    patient = await _make_patient(db, code="P-RA-RES", name="削除前")
    patient.deleted_at = datetime.now(UTC)
    await db.commit()
    original_id = patient.id

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_code": "P-RA-RES",
                "name": "復活後",
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

    db.expire_all()
    p_after = await db.get(Patient, original_id)
    assert p_after is not None
    assert p_after.deleted_at is None
    assert p_after.name == "復活後"


# ---------------------------------------------------------------------------
# CRITICAL 回帰: update 行の NOT NULL カラムが空セル → error (rollback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_required_field_null_in_update_row_errors(client, db) -> None:
    """update パスで NOT NULL カラム (name/status) が空セルの場合、
    error として扱われ全件 rollback (422) される."""
    admin = await _make_user(db, "ra-null-update@example.com", "admin")
    patient = await _make_patient(
        db, code="P-RA-NUL-UPD", name="既存患者", sex="male", status="active", address="addr"
    )
    patient_id = patient.id

    # patient_id 明示 (update パス) + name を空セルに
    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient_id),
                "patient_code": "P-RA-NUL-UPD",
                # name 欠落 (空セル = None)
                "sex": "male",
                "status": "active",
                "address": "addr",
            }
        ],
    )
    res = await _upload(client, admin, content=content, dry_run=False)
    # error 行があるので 422
    assert res.status_code == 422, res.text

    # DB は変更されていないこと
    db.expire_all()
    p_after = await db.get(Patient, patient_id)
    assert p_after is not None
    assert p_after.name == "既存患者"  # rollback 確認


# ---------------------------------------------------------------------------
# E-4 parity: replace_all.py も importer.py と同じく以下を吸収する.
#   - time_type が空セル → DEFAULT_TIME_TYPE で吸収 (error にしない)
#   - course_template_code が拠点不在 → course_template_id=None で保存
#   - round-trip (export → そのまま replace-all import) で error 0 件
# 並列構成: test_patients_excel_api.py の対応する E-4 テストを参照.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_all_accepts_empty_time_type_with_default(client, db) -> None:
    """time_type 空セルは default で吸収され error にならない (E-4 parity)."""
    admin = await _make_user(db, "ra-e4-tt@example.com", "admin")
    patient = await _make_patient(db, code="P-RA-E4-TT", name="time_type空でも OK")
    patient_id = patient.id

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient_id),
                "patient_code": "P-RA-E4-TT",
                "name": "time_type空でも OK",
                "sex": "male",
                "status": "active",
                "address": "千葉市稲毛区test",
            }
        ],
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
    assert body["summary"]["pfv_to_create"] == 1
    assert body["summary"]["pfv_error"] == 0

    db.expire_all()
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_replace_all_fallback_for_unknown_course_template_code(client, db) -> None:
    """course_template_code が患者拠点に存在しない場合、error にならず
    course_template_id=None で取り込む (E-4 parity)."""
    admin = await _make_user(db, "ra-e4-ct@example.com", "admin")
    office = await _make_office(db, code="INAGE", name="稲毛")
    patient = await _make_patient(
        db,
        code="P-RA-E4-CTU",
        name="unknown ct",
        primary_office_id=office.id,
    )
    patient_id = patient.id

    content = _build_workbook_bytes(
        patient_rows=[
            {
                "patient_id": str(patient_id),
                "patient_code": "P-RA-E4-CTU",
                "name": "unknown ct",
                "sex": "male",
                "status": "active",
                "address": "千葉市稲毛区test",
                "office_code": "INAGE",
            }
        ],
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
    assert body["summary"]["pfv_to_create"] == 1
    assert body["summary"]["pfv_error"] == 0

    db.expire_all()
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].course_template_id is None  # 拠点不在 code は剥がされる


@pytest.mark.asyncio
async def test_replace_all_export_then_import_round_trip_completes_without_errors(
    client, db
) -> None:
    """export → そのまま replace-all import で error 0 件 (E-4 parity).

    replace_all は import (差分パス) と違い「全 PFV を物理削除して Excel から
    再投入」する仕様なので、round-trip 結果は全 PFV が pfv_to_create に倒れる.
    cross-office template は同じく空欄に倒される (= silent loss だが運用上許容).
    """
    admin = await _make_user(db, "ra-e4-roundtrip@example.com", "admin")
    office_a = await _make_office(db, code="INAGE", name="稲毛")
    office_b = await _make_office(db, code="TSUGA", name="都賀")
    template_a = CourseTemplate(office_id=office_a.id, label="M")
    template_b = CourseTemplate(office_id=office_b.id, label="C")
    db.add_all([template_a, template_b])
    await db.commit()
    await db.refresh(template_a)
    await db.refresh(template_b)

    patient_a = await _make_patient(
        db, code="P-RA-RT-A", name="患者A", primary_office_id=office_a.id
    )
    patient_b = await _make_patient(
        db, code="P-RA-RT-B", name="患者B", primary_office_id=office_a.id
    )
    # 患者 A: weekly_pattern エントリ無し (time_type 解決不能だが PFV には影響しない).
    patient_a.weekly_pattern = None
    await db.commit()
    # PFV-A: same-office template
    await _make_pfv(db, patient_id=patient_a.id, weekday=0, course_template_id=template_a.id)
    # PFV-B: 患者 B (拠点 INAGE) に対する PFV だが template_b (拠点 TSUGA) を指す
    # クロスオフィス参照. export で course_template_code が空欄に倒れ、replace-all
    # で再投入時に course_template_id=None になる (silent loss).
    await _make_pfv(db, patient_id=patient_b.id, weekday=2, course_template_id=template_b.id)

    # export
    export_res = await client.get(
        "/api/v1/patients/import-export/export",
        headers=_bearer(admin),
    )
    assert export_res.status_code == 200
    exported_bytes = export_res.content

    # そのまま replace-all (dry_run=True で計上のみ).
    res = await _upload(client, admin, content=exported_bytes, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    summary = body["summary"]
    # round-trip では error 0 件 (E-4 で吸収済).
    assert summary["patients_error"] == 0, body
    assert summary["pfv_error"] == 0, body
    # Excel に居る 2 名は削除対象にならない.
    assert summary["patients_to_soft_delete"] == 0, body
    # PFV は全件再投入される. 既存 2 件 / 再投入 2 件.
    assert summary["pfv_to_replace"] == 2, body
    assert summary["pfv_to_create"] == 2, body
