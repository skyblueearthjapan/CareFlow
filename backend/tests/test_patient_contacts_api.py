"""患者様の連絡先 API・スタッフの連絡先・migration 0109 の試験.

正典: ``docs/plans/contacts-phone-email-design-2026-11-05.md`` §4・§5・§8。
番号・メールはすべて見本 (090-0000-0000 など)。
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select

from app.core.security import create_access_token, hash_password
from app.models import AuditLog, Patient, User
from app.models.patient_contact import PatientContact
from app.models.staff import Staff

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, *, code: str) -> Patient:
    p = Patient(code=code, name=f"見本 {code}", status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


def _url(patient: Patient) -> str:
    return f"/api/v1/patients/{patient.id}/contacts"


SELF = {
    "relation": "self",
    "phone": "090-0000-0000",
    "is_notify_target": True,
    "notify_channel": "sms",
    "consent_status": "unconfirmed",
}
FAMILY = {
    "relation": "family",
    "name": "見本 花子",
    "relation_note": "長女",
    "phone": "043-000-0000",
    "email": "Hanako@Example.com",
    "is_notify_target": False,
    "notify_channel": "email",
    "consent_status": "confirmed",
}


# ---------------------------------------------------------------------------
# GET / PUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_and_get_contacts(client, db) -> None:
    admin = await _make_user(db, email="c-admin@example.com", role="admin")
    patient = await _make_patient(db, code="C-1")

    res = await client.get(_url(patient), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["contacts"] == []

    res = await client.put(_url(patient), headers=_bearer(admin), json={"contacts": [SELF, FAMILY]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["notices"] == []
    c0, c1 = body["contacts"]
    assert c0["sort_order"] == 0 and c1["sort_order"] == 1
    assert c0["relation"] == "self" and c0["name"] is None
    assert c0["phone"] == "09000000000"
    assert c0["phone_display"] == "090-0000-0000"
    assert c0["phone_kind"] == "mobile"
    assert c0["sms_capable"] is True
    assert c0["effective_channel"] == "sms"
    assert c0["consent_on"] is None
    assert c1["phone_kind"] == "landline" and c1["sms_capable"] is False
    assert c1["email"] == "hanako@example.com"
    assert c1["email_carrier"] is False
    assert c1["effective_channel"] == "email"
    assert c1["consent_status"] == "confirmed"
    assert c1["consent_on"] is not None

    res = await client.get(_url(patient), headers=_bearer(admin))
    assert [c["id"] for c in res.json()["contacts"]] == [c0["id"], c1["id"]]

    rows = (
        await db.scalars(select(PatientContact).where(PatientContact.patient_id == patient.id))
    ).all()
    assert len(rows) == 2
    assert {r.source for r in rows} == {"manual"}


@pytest.mark.asyncio
async def test_put_keeps_consent_on_by_id_and_moves_target(client, db) -> None:
    admin = await _make_user(db, email="c-admin2@example.com", role="admin")
    patient = await _make_patient(db, code="C-2")
    res = await client.put(
        _url(patient),
        headers=_bearer(admin),
        json={
            "contacts": [{**SELF, "is_notify_target": False}, {**FAMILY, "is_notify_target": True}]
        },
    )
    assert res.status_code == 200, res.text
    first = res.json()["contacts"]

    # 同意の日を昔の日にしておき、id を付けて送ると引き継ぐ。
    row = await db.get(PatientContact, uuid.UUID(first[1]["id"]))
    row.consent_on = date(2026, 10, 1)
    await db.commit()

    # 家族を外す (送る先 0 件) → ご本人へ移る。
    res = await client.put(
        _url(patient),
        headers=_bearer(admin),
        json={
            "contacts": [
                {**SELF, "id": first[0]["id"], "is_notify_target": False},
                {**FAMILY, "id": first[1]["id"], "is_notify_target": False},
            ]
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [c["is_notify_target"] for c in body["contacts"]] == [True, False]
    assert body["notices"] and "ご本人" in body["notices"][0]
    assert body["contacts"][1]["consent_on"] == "2026-10-01"
    assert [c["id"] for c in body["contacts"]] == [first[0]["id"], first[1]["id"]]

    # 0 件にする (全部外す) のはできる。
    res = await client.put(_url(patient), headers=_bearer(admin), json={"contacts": []})
    assert res.status_code == 200
    assert res.json()["contacts"] == []


@pytest.mark.asyncio
async def test_put_swap_self_and_family(client, db) -> None:
    admin = await _make_user(db, email="c-admin3@example.com", role="admin")
    patient = await _make_patient(db, code="C-3")
    res = await client.put(_url(patient), headers=_bearer(admin), json={"contacts": [SELF, FAMILY]})
    a, b = res.json()["contacts"]
    # 送り先を入れ替えても、ご本人の部分 UNIQUE にぶつからない。
    res = await client.put(
        _url(patient),
        headers=_bearer(admin),
        json={
            "contacts": [
                {**FAMILY, "id": a["id"], "relation": "family"},
                {**SELF, "id": b["id"], "relation": "self"},
            ]
        },
    )
    assert res.status_code == 200, res.text
    assert [c["relation"] for c in res.json()["contacts"]] == ["family", "self"]


@pytest.mark.asyncio
async def test_ng_forced_off_target(client, db) -> None:
    admin = await _make_user(db, email="c-admin4@example.com", role="admin")
    patient = await _make_patient(db, code="C-4")
    res = await client.put(
        _url(patient),
        headers=_bearer(admin),
        json={"contacts": [{**SELF, "consent_status": "ng"}, FAMILY]},
    )
    assert res.status_code == 200, res.text
    c = res.json()["contacts"]
    assert [x["is_notify_target"] for x in c] == [False, True]
    assert c[0]["consent_on"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contacts", "needle"),
    [
        (
            [SELF, FAMILY, {**FAMILY, "relation": "other"}, {**FAMILY, "relation": "facility"}],
            "3 件まで",
        ),
        ([SELF, {**SELF}], "ご本人"),
        ([SELF, {**FAMILY, "phone": "090-000-000"}], "2 件目の電話番号"),
        ([{**SELF, "email": "not-an-email"}], "1 件目のメールアドレス"),
    ],
)
async def test_put_422(client, db, contacts, needle) -> None:
    admin = await _make_user(db, email="c-admin5@example.com", role="admin")
    patient = await _make_patient(db, code="C-5")
    res = await client.put(_url(patient), headers=_bearer(admin), json={"contacts": contacts})
    assert res.status_code == 422, res.text
    assert needle in res.json()["detail"]
    count = await db.scalar(
        select(sa.func.count())
        .select_from(PatientContact)
        .where(PatientContact.patient_id == patient.id)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_non_admin_403(client, db) -> None:
    staff = Staff(code="S-C1", name="見本 職員", status="active")
    db.add(staff)
    await db.commit()
    user = await _make_user(db, email="c-staff@example.com", role="staff", staff_id=staff.id)
    patient = await _make_patient(db, code="C-6")
    h = _bearer(user)
    assert (await client.get(_url(patient), headers=h)).status_code == 403
    assert (
        await client.put(_url(patient), headers=h, json={"contacts": [SELF]})
    ).status_code == 403
    assert (await client.get("/api/v1/patients/contacts/summary", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_deleted_or_unknown_patient_404(client, db) -> None:
    admin = await _make_user(db, email="c-admin7@example.com", role="admin")
    patient = await _make_patient(db, code="C-7")
    patient.deleted_at = datetime.now(UTC)
    await db.commit()
    h = _bearer(admin)
    assert (await client.get(_url(patient), headers=h)).status_code == 404
    assert (await client.put(_url(patient), headers=h, json={"contacts": []})).status_code == 404
    unknown = "/api/v1/patients/00000000-0000-0000-0000-000000000001/contacts"
    assert (await client.get(unknown, headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_audit_log_has_no_values(client, db) -> None:
    admin = await _make_user(db, email="c-admin8@example.com", role="admin")
    patient = await _make_patient(db, code="C-8")
    res = await client.put(_url(patient), headers=_bearer(admin), json={"contacts": [SELF, FAMILY]})
    assert res.status_code == 200
    log = await db.scalar(select(AuditLog).where(AuditLog.action == "patient_contacts_update"))
    assert log is not None
    assert log.target_id == str(patient.id)
    assert log.before == {"count": 0}
    assert log.after["count"] == 2
    assert "phone" in log.after["changed_fields"]
    dumped = repr(log.before) + repr(log.after)
    assert "0000" not in dumped and "example.com" not in dumped


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_counts_and_route_order(client, db) -> None:
    admin = await _make_user(db, email="c-admin9@example.com", role="admin")
    h = _bearer(admin)
    p1 = await _make_patient(db, code="C-9a")
    p2 = await _make_patient(db, code="C-9b")
    p3 = await _make_patient(db, code="C-9c")  # 連絡先なし
    gone = await _make_patient(db, code="C-9d")
    # 連絡先のある、消された患者は出ない (API を呼ぶ前に試験のセッションで作っておく)。
    db.add(PatientContact(patient_id=gone.id, sort_order=0, relation="self", is_notify_target=True))
    gone.deleted_at = datetime.now(UTC)
    await db.commit()

    # p1: ご本人 (送る先・未確認・SMS) + 家族 (送る先・確認済み・メール) + 施設 (NG)
    await client.put(
        _url(p1),
        headers=h,
        json={
            "contacts": [
                SELF,
                {**FAMILY, "is_notify_target": True},
                {"relation": "facility", "name": "見本の家", "consent_status": "ng"},
            ]
        },
    )
    # p2: 手段の無い家族 (送る先・確認済み)
    await client.put(
        _url(p2),
        headers=h,
        json={
            "contacts": [
                {
                    "relation": "family",
                    "name": "見本 一郎",
                    "is_notify_target": True,
                    "consent_status": "confirmed",
                },
            ]
        },
    )
    res = await client.get("/api/v1/patients/contacts/summary", headers=h)
    assert res.status_code == 200, res.text
    items = {i["patient_id"]: i for i in res.json()["items"]}
    assert set(items) == {str(p1.id), str(p2.id)}
    assert str(p3.id) not in items
    assert items[str(p1.id)] == {
        "patient_id": str(p1.id),
        "contact_count": 3,
        "targets": 2,
        "sms": 0,
        "email": 1,
        "no_means": 0,
        "pending_consent": 1,
        "ng": 1,
    }
    assert items[str(p2.id)]["no_means"] == 1
    assert items[str(p2.id)]["targets"] == 1
    assert items[str(p2.id)]["sms"] == 0 and items[str(p2.id)]["email"] == 0


# ---------------------------------------------------------------------------
# staff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_contacts_create_update_and_mask(client, db) -> None:
    admin = await _make_user(db, email="s-admin@example.com", role="admin")
    h = _bearer(admin)
    res = await client.post(
        "/api/v1/staff",
        headers=h,
        json={
            "name": "見本 職員",
            "phone": "０９０－００００－００００",
            "contact_email": "Staff@Docomo.ne.jp",
            "notify_channel": "sms",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    sid = body["id"]
    assert body["phone"] == "09000000000"
    assert body["phone_display"] == "090-0000-0000"
    assert body["phone_kind"] == "mobile"
    assert body["sms_capable"] is True
    assert body["contact_email"] == "staff@docomo.ne.jp"
    assert body["email_carrier"] is True
    assert body["notify_channel"] == "sms"

    # 既定は none・連絡先なし。
    res = await client.post("/api/v1/staff", headers=h, json={"name": "見本 二郎"})
    assert res.status_code == 201
    assert res.json()["notify_channel"] == "none"
    assert res.json()["phone"] is None

    # 更新: 電話の形が違えば 422・null は消す・notify_channel の null は 422。
    res = await client.patch(f"/api/v1/staff/{sid}", headers=h, json={"phone": "090-000"})
    assert res.status_code == 422
    res = await client.patch(f"/api/v1/staff/{sid}", headers=h, json={"contact_email": "bad"})
    assert res.status_code == 422
    res = await client.patch(f"/api/v1/staff/{sid}", headers=h, json={"notify_channel": None})
    assert res.status_code == 422
    res = await client.patch(
        f"/api/v1/staff/{sid}", headers=h, json={"phone": "043-000-0000", "notify_channel": "email"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["phone_kind"] == "landline"
    assert res.json()["phone_display"] == "043-000-0000"
    assert res.json()["notify_channel"] == "email"
    res = await client.patch(f"/api/v1/staff/{sid}", headers=h, json={"phone": None})
    assert res.status_code == 200
    assert res.json()["phone"] is None and res.json()["phone_kind"] is None

    # ご本人は自分の連絡先が見える。
    me = await _make_user(db, email="s-me@example.com", role="staff", staff_id=uuid.UUID(sid))
    res = await client.get(f"/api/v1/staff/{sid}", headers=_bearer(me))
    assert res.status_code == 200
    assert res.json()["contact_email"] == "staff@docomo.ne.jp"
    res = await client.get("/api/v1/staff", headers=_bearer(me))
    assert res.json()[0]["contact_email"] == "staff@docomo.ne.jp"

    # 管理者は全員分が見える。
    res = await client.get("/api/v1/staff", headers=h)
    by_id = {s["id"]: s for s in res.json()}
    assert by_id[sid]["contact_email"] == "staff@docomo.ne.jp"


@pytest.mark.asyncio
async def test_staff_read_masks_for_other_staff() -> None:
    """管理者・ご本人以外には null (v1 の一覧は staff を自分だけに絞るので helper で確かめる)."""
    from app.api.v1.staff import _to_read

    now = datetime.now(UTC)
    staff = Staff(
        id=uuid.uuid4(),
        name="見本 職員",
        status="active",
        role="staff",
        is_trainee=False,
        employment_type="full_time",
        phone="09000000000",
        phone_display="090-0000-0000",
        phone_kind="mobile",
        contact_email="a@example.com",
        notify_channel="sms",
        created_at=now,
        updated_at=now,
    )
    other = User(id=uuid.uuid4(), email="o@example.com", role="staff", staff_id=uuid.uuid4())
    masked = _to_read(staff, other)
    for f in (
        "phone",
        "phone_display",
        "phone_kind",
        "sms_capable",
        "contact_email",
        "email_carrier",
        "notify_channel",
    ):
        assert getattr(masked, f) is None, f
    me = User(id=uuid.uuid4(), email="m@example.com", role="staff", staff_id=staff.id)
    assert _to_read(staff, me).phone == "09000000000"
    admin = User(id=uuid.uuid4(), email="a2@example.com", role="admin", staff_id=None)
    assert _to_read(staff, admin).sms_capable is True


# ---------------------------------------------------------------------------
# migration 0109
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _load_migration() -> object:
    path = _BACKEND_ROOT / "alembic" / "versions" / "0109_contacts.py"
    spec = importlib.util.spec_from_file_location("migration_0109", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0109"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_migration_0109_revision_chain() -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0109_contacts")
    assert rev is not None
    assert rev.down_revision == "0108_monthly_actual_care_record"
    assert len(list(script.get_heads())) == 1


def test_migration_0109_sqlite_roundtrip(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_0109.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE patients (id CHAR(32) PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE staff (id CHAR(32) PRIMARY KEY, name VARCHAR(120))"))
        conn.execute(sa.text("INSERT INTO staff (id, name) VALUES ('s1', 'x')"))
    migration = _load_migration()

    def run(step) -> None:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                step()

    run(migration.upgrade)
    insp = inspect(engine)
    staff_cols = {c["name"] for c in insp.get_columns("staff")}
    assert {"phone", "phone_display", "phone_kind", "contact_email", "notify_channel"} <= staff_cols
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT notify_channel FROM staff")).scalar() == "none"
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE staff SET notify_channel = 'fax'"))

    ins = (
        "INSERT INTO patient_contacts (id, patient_id, sort_order, relation) "
        "VALUES ('{id}', 'p1', 0, '{rel}')"
    )
    with engine.begin() as conn:
        conn.execute(sa.text(ins.format(id="c1", rel="self")))
        conn.execute(sa.text(ins.format(id="c2", rel="family")))
        row = conn.execute(
            sa.text(
                "SELECT is_notify_target, notify_channel, consent_status, source "
                "FROM patient_contacts WHERE id = 'c1'"
            )
        ).one()
        assert tuple(row) == (0, "sms", "unconfirmed", "manual")
    # ご本人は 1 件まで。
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(ins.format(id="c3", rel="self")))
    # 値の CHECK。
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(ins.format(id="c4", rel="friend")))

    run(migration.downgrade)
    insp = inspect(engine)
    assert "patient_contacts" not in insp.get_table_names()
    assert "notify_channel" not in {c["name"] for c in insp.get_columns("staff")}
    run(migration.upgrade)
    assert "patient_contacts" in inspect(engine).get_table_names()
    engine.dispose()
