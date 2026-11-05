"""患者マスターの Excel の「連絡先」シートの試験.

正典: ``docs/plans/contacts-phone-email-design-2026-11-05.md`` §6・§8。
書き出し → シートを書きかえ → 取り込み (preview / 反映) を API で通す。番号はすべて見本。
"""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User
from app.models.patient_contact import PatientContact
from app.services.patient_excel.schema import CONTACT_COL_INDEX, CONTACT_COLUMNS, SHEET_CONTACTS

pytestmark = pytest.mark.usefixtures("fake_visit_geocode")

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _admin(db) -> User:
    user = User(email="x-admin@example.com", password_hash=hash_password("pw"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _patient(db, code: str) -> Patient:
    p = Patient(
        code=code,
        name=f"見本 {code}",
        sex="female",
        status="active",
        address="千葉県千葉市稲毛区見本町1-1",
        lat=35.6,
        lng=140.1,
        special_week_active=[],
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _export(client, admin) -> bytes:
    res = await client.get("/api/v1/patients/import-export/export", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    return res.content


async def _import(client, admin, content: bytes, *, dry_run: bool, path: str = "import"):
    return await client.post(
        f"/api/v1/patients/import-export/{path}?dry_run={'true' if dry_run else 'false'}",
        headers=_bearer(admin),
        files={"file": ("t.xlsx", content, MIME)},
    )


def _rows(content: bytes) -> list[dict[str, object]]:
    ws = load_workbook(BytesIO(content))[SHEET_CONTACTS]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None for v in row):
            continue
        out.append({str(c["key"]): row[i] for i, c in enumerate(CONTACT_COLUMNS)})
    return out


def _rewrite(content: bytes, rows: list[dict[str, object]]) -> bytes:
    wb = load_workbook(BytesIO(content))
    ws = wb[SHEET_CONTACTS]
    ws.delete_rows(2, ws.max_row)
    for r, values in enumerate(rows, start=2):
        for key, value in values.items():
            ws.cell(row=r, column=CONTACT_COL_INDEX[key] + 1, value=value)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _contacts(db, patient: Patient) -> list[PatientContact]:
    return list(
        (
            await db.scalars(
                select(PatientContact)
                .where(PatientContact.patient_id == patient.id)
                .order_by(PatientContact.sort_order)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


def _base(p: Patient) -> dict[str, object]:
    return {"patient_id": str(p.id), "patient_code": p.code, "patient_name": p.name}


@pytest.mark.asyncio
async def test_export_has_sheet_with_blank_self_row(client, db) -> None:
    admin = await _admin(db)
    p = await _patient(db, "X-1")
    content = await _export(client, admin)
    wb = load_workbook(BytesIO(content))
    assert SHEET_CONTACTS in wb.sheetnames
    ws = wb[SHEET_CONTACTS]
    assert [c.value for c in ws[1]] == [c["header"] for c in CONTACT_COLUMNS]
    # ドロップダウンが付いている (送り先・お知らせを送る・手段・同意)。
    formulas = " ".join(dv.formula1 for dv in ws.data_validations.dataValidation)
    for word in ("ご本人", "はい", "SMS", "NG 回答"):
        assert word in formulas
    rows = _rows(content)
    assert len(rows) == 1
    r = rows[0]
    assert r["patient_id"] == str(p.id)
    assert r["relation"] == "ご本人"
    assert r["is_notify_target"] == "はい"
    assert r["phone"] is None and r["email"] is None

    # テンプレートにもシート (見出しだけ) がある。
    res = await client.get("/api/v1/patients/import-export/template", headers=_bearer(admin))
    wb_t = load_workbook(BytesIO(res.content))
    assert wb_t[SHEET_CONTACTS].max_row == 1


@pytest.mark.asyncio
async def test_import_replaces_and_roundtrip_is_noop(client, db) -> None:
    admin = await _admin(db)
    p = await _patient(db, "X-2")
    content = await _export(client, admin)
    edited = _rewrite(
        content,
        [
            {
                **_base(p),
                "relation": "ご本人",
                "phone": "090-0000-0000",
                "is_notify_target": "いいえ",
                "notify_channel": "SMS",
                "consent_status": "確認済み",
            },
            {
                **_base(p),
                "relation": "ご家族",
                "name": "見本 花子",
                "relation_note": "長女",
                "email": "hanako@example.com",
                "is_notify_target": "はい",
                "notify_channel": "メール",
                "consent_status": "未確認",
            },
            # 中身の無い行は飛ばす。
            {**_base(p), "relation": "施設", "is_notify_target": "はい"},
        ],
    )

    # preview: 患者の行に連絡先の差分が載る (患者の欄は変わらないので noop → update)。
    res = await _import(client, admin, edited, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["patients_error"] == 0, body
    assert body["summary"]["patients_update"] == 1, body
    row = next(r for r in body["patient_rows"] if r["patient_id"] == str(p.id))
    assert row["operation"] == "update"
    assert {"field": "contacts", "old_value": "0 件", "new_value": "2 件"} in row["changes"]
    assert await _contacts(db, p) == []

    res = await _import(client, admin, edited, dry_run=False)
    assert res.status_code == 200, res.text
    assert res.json()["transaction_applied"] is True
    saved = await _contacts(db, p)
    assert [c.relation for c in saved] == ["self", "family"]
    assert saved[0].phone == "09000000000" and saved[0].phone_kind == "mobile"
    assert saved[0].consent_status == "confirmed" and saved[0].consent_on is not None
    assert saved[1].email == "hanako@example.com" and saved[1].is_notify_target is True
    assert {c.source for c in saved} == {"excel"}

    # 往復: 書き出したものをそのまま取り込むと変わらない。
    again = await _export(client, admin)
    rows = _rows(again)
    assert [r["phone"] for r in rows] == ["090-0000-0000", None]
    res = await _import(client, admin, again, dry_run=True)
    body = res.json()
    assert body["summary"]["patients_update"] == 0, body
    assert body["summary"]["patients_error"] == 0, body


@pytest.mark.asyncio
async def test_consent_on_carried_by_same_phone(client, db) -> None:
    admin = await _admin(db)
    p = await _patient(db, "X-3")
    old_day = date(2026, 10, 1)
    db.add(
        PatientContact(
            patient_id=p.id,
            sort_order=0,
            relation="family",
            name="見本 花子",
            phone="09000000000",
            phone_display="090-0000-0000",
            phone_kind="mobile",
            is_notify_target=True,
            consent_status="confirmed",
            consent_on=old_day,
        )
    )
    await db.commit()
    content = await _export(client, admin)
    rows = _rows(content)
    # お名前だけ変えても、電話が同じで同意も同じなら同意の日を引き継ぐ。
    rows[0]["name"] = "見本 花"
    # 新しい行 (確認済み) は今日。
    rows.append(
        {**_base(p), "relation": "ご本人", "phone": "043-000-0000", "consent_status": "確認済み"}
    )
    res = await _import(client, admin, _rewrite(content, rows), dry_run=False)
    assert res.status_code == 200, res.text
    saved = await _contacts(db, p)
    assert saved[0].name == "見本 花" and saved[0].consent_on == old_day
    assert saved[1].consent_on is not None and saved[1].consent_on != old_day


@pytest.mark.asyncio
async def test_rows_all_blank_keep_current_contacts(client, db) -> None:
    admin = await _admin(db)
    p = await _patient(db, "X-4")
    db.add(
        PatientContact(
            patient_id=p.id,
            sort_order=0,
            relation="self",
            phone="09000000000",
            phone_kind="mobile",
            is_notify_target=True,
        )
    )
    await db.commit()
    content = await _export(client, admin)
    # 連絡先の行を空にしても (Excel では) 消さない。
    edited = _rewrite(content, [{**_base(p), "relation": "ご本人"}])
    res = await _import(client, admin, edited, dry_run=False)
    assert res.status_code == 200
    assert len(await _contacts(db, p)) == 1


@pytest.mark.asyncio
async def test_old_format_without_sheet_leaves_contacts(client, db) -> None:
    admin = await _admin(db)
    p = await _patient(db, "X-5")
    db.add(
        PatientContact(
            patient_id=p.id,
            sort_order=0,
            relation="self",
            phone="09000000000",
            phone_kind="mobile",
            is_notify_target=True,
        )
    )
    await db.commit()
    wb = load_workbook(BytesIO(await _export(client, admin)))
    del wb[SHEET_CONTACTS]
    buf = BytesIO()
    wb.save(buf)
    for path in ("import", "replace-all"):
        res = await _import(client, admin, buf.getvalue(), dry_run=False, path=path)
        assert res.status_code == 200, res.text
        saved = await _contacts(db, p)
        assert len(saved) == 1 and saved[0].phone == "09000000000"


@pytest.mark.asyncio
async def test_errors_go_to_preview_and_skip_that_patient(client, db) -> None:
    admin = await _admin(db)
    bad_phone = await _patient(db, "X-6a")
    too_many = await _patient(db, "X-6b")
    two_self = await _patient(db, "X-6c")
    ok = await _patient(db, "X-6d")
    content = await _export(client, admin)
    fam = {"relation": "ご家族", "name": "見本 家族"}
    edited = _rewrite(
        content,
        [
            {**_base(bad_phone), "relation": "ご本人", "phone": "090-000-000"},
            {**_base(too_many), **fam},
            {**_base(too_many), **fam},
            {**_base(too_many), **fam},
            {**_base(too_many), **fam},
            {**_base(two_self), "relation": "ご本人", "phone": "090-0000-0000"},
            {**_base(two_self), "relation": "ご本人", "phone": "080-1000-0000"},
            {
                "patient_id": "00000000-0000-0000-0000-000000000009",
                "relation": "ご本人",
                "phone": "090-0000-0000",
            },
            {**_base(ok), "relation": "ご本人", "email": "ok@example.com"},
        ],
    )
    res = await _import(client, admin, edited, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    errors = [r for r in body["patient_rows"] if r["operation"] == "error"]
    assert body["summary"]["patients_error"] == len(errors) == 4
    messages = " ".join(r["error_message"] for r in errors)
    assert "電話番号" in messages
    assert "3 件まで" in messages
    assert "ご本人の連絡先は 1 件まで" in messages
    assert "知らない patient_id" in messages
    assert all(SHEET_CONTACTS in r["error_message"] for r in errors)

    res = await _import(client, admin, edited, dry_run=False)
    assert res.status_code == 200, res.text
    for p in (bad_phone, too_many, two_self):
        assert await _contacts(db, p) == []
    saved = await _contacts(db, ok)
    assert len(saved) == 1 and saved[0].email == "ok@example.com"
    # 送る先が 0 件にならない (normalize を通る)。
    assert saved[0].is_notify_target is True

    # 完全置換では誤りが 1 件でもあれば全体を止める。
    res = await _import(client, admin, edited, dry_run=False, path="replace-all")
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_new_patient_linked_by_code(client, db) -> None:
    admin = await _admin(db)
    existing = await _patient(db, "X-7")
    content = await _export(client, admin)
    wb = load_workbook(BytesIO(content))
    ws = wb["患者マスタ"]
    # 既存の行を写して、新しい患者 (patient_id なし・新しいコード) にする。
    new_row = [c.value for c in ws[2]]
    new_row[0] = None
    new_row[1] = "X-7N"
    new_row[2] = "見本 新規"
    ws.append(new_row)
    buf = BytesIO()
    wb.save(buf)
    edited = _rewrite(
        buf.getvalue(),
        [
            {**_base(existing), "relation": "ご本人"},
            {
                "patient_code": "X-7N",
                "relation": "ご本人",
                "phone": "090-0000-0000",
                "is_notify_target": "はい",
            },
        ],
    )
    res = await _import(client, admin, edited, dry_run=False)
    assert res.status_code == 200, res.text
    assert res.json()["summary"]["patients_new"] == 1, [
        r["error_message"] for r in res.json()["patient_rows"]
    ]
    new_p = await db.scalar(select(Patient).where(Patient.code == "X-7N"))
    assert new_p is not None
    saved = await _contacts(db, new_p)
    assert len(saved) == 1 and saved[0].phone == "09000000000"
    assert await _contacts(db, existing) == []


@pytest.mark.asyncio
async def test_replace_all_applies_contacts(client, db) -> None:
    admin = await _admin(db)
    p = await _patient(db, "X-8")
    content = await _export(client, admin)
    edited = _rewrite(
        content,
        [{**_base(p), "relation": "ご本人", "phone": 9000000000, "is_notify_target": "はい"}],
    )
    res = await _import(client, admin, edited, dry_run=True, path="replace-all")
    assert res.status_code == 200, res.text
    assert res.json()["summary"]["patients_error"] == 0
    res = await _import(client, admin, edited, dry_run=False, path="replace-all")
    assert res.status_code == 200, res.text
    saved = await _contacts(db, p)
    # Excel が数値にして先頭の 0 を落とした番号も読める。
    assert len(saved) == 1 and saved[0].phone == "09000000000"
