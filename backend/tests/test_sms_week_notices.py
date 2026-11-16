"""週のお知らせ (SMS) の一覧・送信・記録の API と migration 0110 の試験.

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-1・§6・§7・§11。
お名前・番号・事業所はすべて見本 (09000000001 など)。送るのは mock だけ (外へ出さない)。
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models import AuditLog, Office, OfficeFeatureFlag, Patient, User
from app.models.business_profile import BusinessProfile
from app.models.patient_contact import PatientContact
from app.models.sms import PatientNoticeEvent, PatientWeekNotice, SmsMessage
from app.models.visit import Visit
from app.services.sms import notices as sms_notices

# 2026-11-25 (水) 10:00 JST = 01:00 UTC。来週の月曜は 11/30。
NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)
WEEK = date(2026, 11, 30)
PREVIEW = "/api/v1/sms/week-notices/preview"
SEND = "/api/v1/sms/week-notices/send"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(sms_notices, "_now", lambda: state["now"])
    monkeypatch.setattr(sms_notices, "SEND_INTERVAL_SECONDS", 0)
    return state


@pytest_asyncio.fixture
async def base(db):
    """管理者・旗 ON の拠点・事業所の情報."""
    admin = User(email="sms-admin@example.com", password_hash=hash_password("pw"), role="admin")
    office = Office(name="見本拠点", short_label="見", code="smsoff")
    db.add_all([admin, office])
    db.add(
        BusinessProfile(
            is_singleton=True, station_name="見本ステーション", contact_tel="000-000-0000"
        )
    )
    await db.commit()
    db.add(OfficeFeatureFlag(office_id=office.id, feature_key="sms_notify", enabled_at=NOW))
    await db.commit()
    return {"admin": admin, "office": office}


async def _patient(db, code: str, name: str, *, status: str = "active") -> Patient:
    p = Patient(code=code, name=name, status=status, special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _visit(db, patient: Patient, d: date, t: time, *, status: str = "planned", **kw) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=d,
        start_time=t,
        end_time=time(t.hour + 1, t.minute),
        type="regular",
        status=status,
        **kw,
    )
    db.add(v)
    await db.commit()
    return v


async def _contact(
    db,
    patient: Patient,
    *,
    phone: str | None = "09000000001",
    phone_kind: str | None = "mobile",
    relation: str = "self",
    consent: str = "confirmed",
    target: bool = True,
    order: int = 0,
) -> PatientContact:
    c = PatientContact(
        patient_id=patient.id,
        sort_order=order,
        relation=relation,
        phone=phone,
        phone_kind=phone_kind,
        is_notify_target=target,
        notify_channel="sms",
        consent_status=consent,
    )
    db.add(c)
    await db.commit()
    return c


async def _sendable_patient(db, code: str, name: str, phone: str = "09000000001") -> Patient:
    p = await _patient(db, code, name)
    await _visit(db, p, date(2026, 12, 1), time(10, 0))
    await _contact(db, p, phone=phone)
    return p


async def _preview(client, admin, week: date = WEEK):
    res = await client.post(PREVIEW, json={"week_start": week.isoformat()}, headers=_bearer(admin))
    assert res.status_code == 200, res.text
    return res.json()


async def _send(client, admin, ids, expected, **extra):
    body = {
        "week_start": WEEK.isoformat(),
        "patient_ids": [str(i) for i in ids],
        "expected_count": expected,
        **extra,
    }
    return await client.post(SEND, json=body, headers=_bearer(admin))


async def _messages(db) -> list[SmsMessage]:
    return list(
        (
            await db.scalars(
                select(SmsMessage)
                .order_by(SmsMessage.created_at, SmsMessage.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# 旗・権限
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_is_404(client, db) -> None:
    admin = User(email="sms-a2@example.com", password_hash=hash_password("pw"), role="admin")
    office = Office(name="見本拠点2", short_label="2", code="smsoff2")
    db.add_all([admin, office])
    await db.commit()
    # 旗の行はあるが OFF (enabled_at = NULL)。
    db.add(OfficeFeatureFlag(office_id=office.id, feature_key="sms_notify", enabled_at=None))
    await db.commit()
    res = await client.post(PREVIEW, json={"week_start": WEEK.isoformat()}, headers=_bearer(admin))
    assert res.status_code == 404
    res = await client.get("/api/v1/sms/messages", headers=_bearer(admin))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_is_403(client, db, base) -> None:
    staff = User(email="sms-staff@example.com", password_hash=hash_password("pw"), role="staff")
    db.add(staff)
    await db.commit()
    for method, url, body in (
        ("post", PREVIEW, {"week_start": WEEK.isoformat()}),
        ("post", SEND, {"week_start": WEEK.isoformat(), "patient_ids": [], "expected_count": 0}),
        ("get", "/api/v1/sms/batches", None),
        ("get", "/api/v1/sms/messages", None),
    ):
        kw = {"json": body} if body is not None else {}
        res = await getattr(client, method)(url, headers=_bearer(staff), **kw)
        assert res.status_code == 403, url


# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_classification(client, db, base) -> None:
    ok = await _sendable_patient(db, "S-1", "見本 一郎")
    # 同じ日に 2 回・取消・削除済み・週の外・今日より前は数えない。
    await _visit(db, ok, date(2026, 12, 1), time(15, 0))
    await _visit(db, ok, date(2026, 12, 2), time(9, 0), status="cancelled")
    await _visit(db, ok, date(2026, 12, 3), time(9, 0), deleted_at=NOW)
    await _visit(db, ok, date(2026, 12, 8), time(9, 0))

    no_contact = await _patient(db, "S-2", "見本 二郎")
    await _visit(db, no_contact, date(2026, 12, 2), time(10))

    unconfirmed = await _patient(db, "S-3", "見本 三郎")
    await _visit(db, unconfirmed, date(2026, 12, 2), time(10))
    await _contact(db, unconfirmed, consent="unconfirmed")

    ng = await _patient(db, "S-4", "見本 四郎")
    await _visit(db, ng, date(2026, 12, 2), time(10))
    await _contact(db, ng, consent="ng", target=False)

    landline = await _patient(db, "S-5", "見本 五郎")
    await _visit(db, landline, date(2026, 12, 2), time(10))
    await _contact(db, landline, phone="0430000000", phone_kind="landline")

    # 訪問の無い方・稼働中でない方は一覧に出ない。
    no_visits = await _patient(db, "S-6", "見本 六郎")
    await _contact(db, no_visits)
    inactive = await _patient(db, "S-7", "見本 七郎", status="suspended")
    await _visit(db, inactive, date(2026, 12, 2), time(10))
    await _contact(db, inactive)

    data = await _preview(client, base["admin"])
    assert data["week_label"] == "来週の"
    assert data["within_send_hours"] is True
    assert data["send_hours"] == "8:00〜20:00"

    (row,) = data["rows"]
    assert row["patient_id"] == str(ok.id)
    assert row["surname"] == "見本"
    assert row["state"] == "new"
    assert row["visits"] == [
        {"date": "2026-12-01", "start": "10:00"},
        {"date": "2026-12-01", "start": "15:00"},
    ]
    assert row["body"].split("\n")[1:3] == ["12/1(火)10:00", "12/1(火)15:00"]
    assert "一郎" not in row["body"]
    assert row["destinations"] == [
        {
            "contact_id": row["destinations"][0]["contact_id"],
            "relation": "self",
            "relation_label": "ご本人",
            "phone_last4": "0001",
        }
    ]
    assert row["char_count"] == len(row["body"]) + row["body"].count("\n")
    assert row["est_yen"] == row["segments_est"] * 11
    assert data["totals"] == {
        "people": 1,
        "messages": 1,
        "segments": row["segments_est"],
        "yen": row["est_yen"],
    }

    reasons = {r["patient_id"]: r["reason"] for r in data["not_sendable"]}
    assert reasons == {
        str(no_contact.id): "no_contact",
        str(unconfirmed.id): "consent_unconfirmed",
        str(ng.id): "consent_ng",
        str(landline.id): "not_mobile",
    }
    assert "09000000001" not in str(data)


@pytest.mark.asyncio
async def test_preview_requires_monday_and_profile(client, db, base) -> None:
    res = await client.post(
        PREVIEW, json={"week_start": "2026-12-01"}, headers=_bearer(base["admin"])
    )
    assert res.status_code == 422
    profile = await db.scalar(select(BusinessProfile))
    profile.contact_tel = None
    await db.commit()
    res = await client.post(
        PREVIEW, json={"week_start": WEEK.isoformat()}, headers=_bearer(base["admin"])
    )
    assert res.status_code == 422
    assert "事業所の情報" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 送る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_with_mock_and_state_after(client, db, base) -> None:
    p = await _sendable_patient(db, "S-1", "見本 一郎")
    res = await _send(client, base["admin"], [p.id], 1)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["patient_count"] == 1 and out["message_count"] == 1

    (msg,) = await _messages(db)
    assert msg.status == "accepted"
    assert msg.provider == "mock" and msg.provider_msg_id.startswith("mock-")
    assert msg.phone == "09000000001" and msg.relation == "self"
    assert msg.dedupe_key == f"week:{p.id}:{msg.contact_id}:{WEEK.isoformat()}:v1"
    assert msg.attempt_count == 1 and msg.next_poll_at is not None

    notice = await db.scalar(select(PatientWeekNotice))
    assert notice.version == 1 and notice.last_channel == "sms"
    assert notice.visits == [{"date": "2026-12-01", "start": "10:00"}]
    assert notice.told_by_user_id == base["admin"].id
    event = await db.scalar(select(PatientNoticeEvent))
    assert event.version == 1 and event.channel == "sms" and event.batch_id == msg.batch_id

    # 同じ内容は「送信済み」・既定では送らない。
    data = await _preview(client, base["admin"])
    assert data["rows"][0]["state"] == "already_sent_same"
    assert data["totals"]["messages"] == 0
    res = await _send(client, base["admin"], [p.id], 1)
    assert res.status_code == 409
    assert "一覧を開き直してください" in res.json()["detail"]

    # 明示して送り直すと版が上がる (鍵が違うので送れる)。
    res = await _send(client, base["admin"], [p.id], 1, resend=True)
    assert res.status_code == 200, res.text
    msgs = await _messages(db)
    assert len(msgs) == 2 and {m.notice_version for m in msgs} == {1, 2}
    # 同じ番号へは 600 秒あける (2 通目は not_before まで queued)。
    second = next(m for m in msgs if m.notice_version == 2)
    assert second.status == "queued"
    assert sms_notices.aware(second.not_before) == datetime(2026, 11, 25, 1, 10, tzinfo=UTC)

    # 予定が変わると「変わった」になる。
    await _visit(db, p, date(2026, 12, 4), time(11, 0))
    data = await _preview(client, base["admin"])
    assert data["rows"][0]["state"] == "changed_since_sent"

    # 記録の一覧・詳細。
    res = await client.get(
        "/api/v1/sms/batches",
        params={"week_start": WEEK.isoformat()},
        headers=_bearer(base["admin"]),
    )
    assert res.status_code == 200
    batches = res.json()["items"]
    assert len(batches) == 2
    assert sorted(str(b["status_counts"]) for b in batches) == [
        "{'accepted': 1}",
        "{'queued': 1}",
    ]
    assert batches[0]["created_by_name"] == "sms-admin@example.com"

    res = await client.get(
        "/api/v1/sms/messages",
        params={
            "patient_id": str(p.id),
            "status": "accepted",
            "kind": "week",
            "date_from": "2026-11-25",
            "date_to": "2026-11-25",
        },
        headers=_bearer(base["admin"]),
    )
    assert res.status_code == 200
    listed = res.json()
    assert listed["total"] == 1 and listed["items"][0]["phone_last4"] == "0001"
    assert "09000000001" not in res.text and 'phone"' not in res.text
    res = await client.get(
        "/api/v1/sms/messages", params={"date_from": "2026-11-26"}, headers=_bearer(base["admin"])
    )
    assert res.json()["total"] == 0

    first = next(m for m in msgs if m.notice_version == 1)
    res = await client.get(f"/api/v1/sms/messages/{first.id}", headers=_bearer(base["admin"]))
    assert res.status_code == 200
    detail = res.json()
    assert detail["phone"] == "09000000001" and detail["body"] == first.body
    assert {e["status"] for e in detail["events"]} == {"queued", "accepted"}


@pytest.mark.asyncio
async def test_send_body_is_composed_on_server_and_audit_has_no_body(client, db, base) -> None:
    p = await _sendable_patient(db, "S-1", "見本 一郎")
    res = await client.post(
        SEND,
        json={
            "week_start": WEEK.isoformat(),
            "patient_ids": [str(p.id)],
            "expected_count": 1,
            "body": "偽の本文",
        },
        headers=_bearer(base["admin"]),
    )
    # 本文は受け取らない (余計な項目は 422)。
    assert res.status_code == 422
    from app.middleware.audit import _skips_body_buffering

    assert _skips_body_buffering("POST", SEND, "application/json", "10") == {"_omitted": True}
    assert _skips_body_buffering("POST", "/api/v1/patients", "application/json", "10") is None
    # 監査ログ (非同期) に本文と番号が入っていない。
    rows = (await db.scalars(select(AuditLog))).all()
    assert all("09000000001" not in str(r.request_body) for r in rows)


@pytest.mark.asyncio
async def test_expected_count_mismatch_and_outside_hours(client, db, base, _clock) -> None:
    p = await _sendable_patient(db, "S-1", "見本 一郎")
    res = await _send(client, base["admin"], [p.id], 2)
    assert res.status_code == 409
    assert await _messages(db) == []

    # 20:00 JST (= 11:00 UTC) は時間の外。
    _clock["now"] = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
    res = await _send(client, base["admin"], [p.id], 1)
    assert res.status_code == 422
    assert "8:00〜20:00" in res.json()["detail"]
    data = await _preview(client, base["admin"])
    assert data["within_send_hours"] is False
    assert await _messages(db) == []


@pytest.mark.asyncio
async def test_unselected_and_not_sendable_are_not_sent(client, db, base) -> None:
    a = await _sendable_patient(db, "S-1", "見本 一郎", phone="09000000001")
    await _sendable_patient(db, "S-2", "見本 二郎", phone="09000000002")
    c = await _patient(db, "S-3", "見本 三郎")
    await _visit(db, c, date(2026, 12, 2), time(10))
    res = await _send(client, base["admin"], [a.id, c.id], 1)
    assert res.status_code == 200, res.text
    (msg,) = await _messages(db)
    assert msg.patient_id == a.id


@pytest.mark.asyncio
async def test_allowlist_skips_numbers_not_listed(client, db, base, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "sms_test_allowlist", "090-0000-0001")
    a = await _sendable_patient(db, "S-1", "見本 一郎", phone="09000000001")
    b = await _sendable_patient(db, "S-2", "見本 二郎", phone="09000000002")
    res = await _send(client, base["admin"], [a.id, b.id], 2)
    assert res.status_code == 200, res.text
    by_patient = {m.patient_id: m for m in await _messages(db)}
    assert by_patient[a.id].status == "accepted"
    assert by_patient[b.id].status == "skipped"
    assert by_patient[b.id].skip_reason == "not_in_test_allowlist"
    assert by_patient[b.id].provider_msg_id is None


@pytest.mark.asyncio
async def test_same_number_is_spaced_by_not_before(client, db, base, _clock) -> None:
    # ご夫婦で同じ番号。
    a = await _sendable_patient(db, "S-1", "見本 一郎", phone="09000000001")
    b = await _sendable_patient(db, "S-2", "見本 花子", phone="09000000001")
    res = await _send(client, base["admin"], [a.id, b.id], 2)
    assert res.status_code == 200, res.text
    msgs = await _messages(db)
    queued = [m for m in msgs if m.status == "queued"]
    sent = [m for m in msgs if m.status == "accepted"]
    assert len(queued) == 1 and len(sent) == 1
    assert sms_notices.aware(queued[0].not_before) == datetime(2026, 11, 25, 1, 10, tzinfo=UTC)

    # 時刻が来る前は送らない。来たら送る (cron からも同じ関数を呼ぶ)。
    assert await sms_notices.send_due_messages() == 0
    _clock["now"] = datetime(2026, 11, 25, 1, 10, tzinfo=UTC)
    assert await sms_notices.send_due_messages() == 1
    assert {m.status for m in await _messages(db)} == {"accepted"}


@pytest.mark.asyncio
async def test_dedupe_key_blocks_double_send(client, db, base) -> None:
    p = await _sendable_patient(db, "S-1", "見本 一郎")
    res = await _send(client, base["admin"], [p.id], 1)
    assert res.status_code == 200
    (msg,) = await _messages(db)
    # 同じ鍵の行は作れない (途中で止まっても同じ人へ二重に送らない)。
    db.add(
        SmsMessage(
            batch_id=msg.batch_id,
            kind="week",
            week_start=WEEK,
            dedupe_key=msg.dedupe_key,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db.commit()
    await db.rollback()

    # accepted の行は、もう一度順番の処理を呼んでも送り直さない。
    assert await sms_notices.send_due_messages() == 0
    assert [m.status for m in await _messages(db)] == ["accepted"]


@pytest.mark.asyncio
async def test_retry_then_fail_and_unknown(db, base, monkeypatch) -> None:
    from app.services.sms.provider import SendResult

    p = await _sendable_patient(db, "S-1", "見本 一郎")
    pv = await sms_notices.build_week_preview(db, WEEK, NOW)
    await sms_notices.create_week_batch(db, preview=pv, rows=pv.sendable, user_id=None, now=NOW)
    await db.commit()

    class Flaky:
        name = "kddi"

        def __init__(self, outcome: str) -> None:
            self.outcome = outcome

        async def send(self, phone, text):
            return SendResult(outcome=self.outcome, error_code="http_503")  # type: ignore[arg-type]

    monkeypatch.setattr(sms_notices, "get_provider", lambda _s: Flaky("retry"))
    assert await sms_notices.send_due_messages() == 1
    (msg,) = await _messages(db)
    assert (
        msg.status == "queued"
        and sms_notices.aware(msg.not_before) > NOW
        and msg.attempt_count == 1
    )
    # 送り直しの時刻まで待たずに、回数を使い切らせる。
    for _ in range(sms_notices.MAX_ATTEMPTS - 1):
        msg.not_before = None
        await db.commit()
        await sms_notices.send_due_messages()
        (msg,) = await _messages(db)
    assert msg.status == "failed" and msg.attempt_count == sms_notices.MAX_ATTEMPTS
    assert msg.error_code == "http_503" and p.id == msg.patient_id


# ---------------------------------------------------------------------------
# migration 0110
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_TABLES = (
    "sms_batches",
    "sms_messages",
    "sms_status_events",
    "patient_week_notices",
    "patient_notice_events",
)


def _load_migration() -> object:
    path = _BACKEND_ROOT / "alembic" / "versions" / "0110_sms_notify.py"
    spec = importlib.util.spec_from_file_location("migration_0110", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0110"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_migration_0110_revision_chain() -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0110_sms_notify")
    assert rev is not None
    assert rev.down_revision == "0109_contacts"
    assert list(script.get_heads()) == ["0110_sms_notify"]


def test_migration_0110_sqlite_roundtrip(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_0110.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE patients (id CHAR(32) PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE users (id CHAR(32) PRIMARY KEY)"))
    migration = _load_migration()

    def run(step) -> None:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                step()

    run(migration.upgrade)
    assert set(_TABLES) <= set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO sms_batches (id, kind, week_start) VALUES ('b1', 'week', '2026-11-30')"
            )
        )
        ins = (
            "INSERT INTO sms_messages (id, batch_id, kind, week_start, dedupe_key, status) "
            "VALUES ('{id}', 'b1', 'week', '2026-11-30', '{key}', '{st}')"
        )
        conn.execute(sa.text(ins.format(id="m1", key="k1", st="queued")))
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(ins.format(id="m2", key="k1", st="queued")))
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(ins.format(id="m3", key="k3", st="sent")))
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO patient_week_notices (id, patient_id, week_start, visits) "
                "VALUES ('n1', 'p1', '2026-11-30', '[]')"
            )
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO patient_week_notices (id, patient_id, week_start, visits) "
                    "VALUES ('n2', 'p1', '2026-11-30', '[]')"
                )
            )

    run(migration.downgrade)
    names = set(inspect(engine).get_table_names())
    assert not (set(_TABLES) & names)
    run(migration.upgrade)
    assert set(_TABLES) <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_dedupe_key_format() -> None:
    # 種類:患者:連絡先:週:版。
    pid, cid = uuid.uuid4(), uuid.uuid4()
    assert sms_notices.dedupe_key("week", pid, cid, WEEK, 3) == f"week:{pid}:{cid}:2026-11-30:v3"
