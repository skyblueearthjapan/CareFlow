"""変更のお知らせ (SMS・段階 2): 変更の検知・お知らせ待ち・送る・送らずに済ませる・ベル・cron.

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-2・§11、
``docs/plans/sms-notify-api-contract-2026-11-16.md`` §1〜§5。
お名前・番号・事業所はすべて見本 (09000000001 など)。送るのは mock だけ (外へ出さない)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models import Office, OfficeFeatureFlag, Patient, User
from app.models.business_profile import BusinessProfile
from app.models.notification import Notification
from app.models.patient_contact import PatientContact
from app.models.sms import PatientNoticeEvent, PatientWeekNotice, SmsBatch, SmsMessage
from app.models.staff import Staff
from app.models.visit import Visit
from app.services.sms import changes as sms_changes
from app.services.sms import notices as sms_notices
from app.services.sms.composer import VisitChange

# 2026-11-25 (水) 10:00 JST = 01:00 UTC。今週の月曜は 11/23、来週の月曜は 11/30。
NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)
TODAY = date(2026, 11, 25)
THIS_WEEK = date(2026, 11, 23)
WEEK = date(2026, 11, 30)
API = "/api/v1/sms"


#: 外部キーを確かめる (PostgreSQL と同じく、親より先に子を書いたら落ちる)。
pytestmark = pytest.mark.usefixtures("sqlite_foreign_keys")


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


#: 試験の番号 (mock でも live は送らない契約 §13-4 なので、test の状態で一覧に入れて送る)。
ALLOW = "09000000001,09000000002,09000000003"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SMS_SEND_MODE", "test")
    monkeypatch.setenv("SMS_TEST_ALLOWLIST", ALLOW)
    monkeypatch.setenv("SMS_STATION_SHORT_NAME", "")


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(sms_notices, "_now", lambda: state["now"])
    monkeypatch.setattr(sms_notices, "SEND_INTERVAL_SECONDS", 0)
    return state


@pytest_asyncio.fixture
async def base(db):
    admin = User(email="sms2-admin@example.com", password_hash=hash_password("pw"), role="admin")
    office = Office(name="見本拠点", short_label="見", code="sms2off")
    db.add_all([admin, office])
    db.add(
        BusinessProfile(
            is_singleton=True, station_name="見本ステーション", contact_tel="000-000-0000"
        )
    )
    await db.commit()
    db.add(OfficeFeatureFlag(office_id=office.id, feature_key="sms_notify", enabled_at=NOW))
    await db.commit()
    await db.refresh(admin)
    await db.refresh(office)
    return {
        "admin": admin,
        "office": office,
        "admin_id": admin.id,
        "office_id": office.id,
        "h": _bearer(admin),
    }


async def _patient(db, code: str, name: str) -> Patient:
    p = Patient(code=code, name=name, status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _visit(db, patient: Patient, d: date, t: time, **kw) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=d,
        start_time=t,
        end_time=time(t.hour + 1, t.minute),
        type="regular",
        status=kw.pop("status", "planned"),
        **kw,
    )
    db.add(v)
    await db.commit()
    return v


async def _contact(db, patient: Patient, phone: str = "09000000001", **kw) -> PatientContact:
    c = PatientContact(
        patient_id=patient.id,
        sort_order=kw.pop("order", 0),
        relation=kw.pop("relation", "self"),
        phone=phone,
        phone_display=kw.pop("display", None),
        phone_kind=kw.pop("kind", "mobile"),
        is_notify_target=kw.pop("target", True),
        notify_channel="sms",
        consent_status=kw.pop("consent", "confirmed"),
    )
    db.add(c)
    await db.commit()
    return c


async def _told(db, patient: Patient, week: date, slots: list[tuple[date, time]]):
    n = PatientWeekNotice(
        patient_id=patient.id,
        week_start=week,
        visits=[{"date": d.isoformat(), "start": f"{t.hour:02d}:{t.minute:02d}"} for d, t in slots],
        version=1,
        told_version=1,
        last_channel="sms",
        told_at=NOW - timedelta(days=1),
    )
    db.add(n)
    await db.commit()
    return n


async def _pending(db, now: datetime = NOW):
    return await sms_changes.compute_pending(db, now)


# ---------------------------------------------------------------------------
# 分け方 (DB を読まない)
# ---------------------------------------------------------------------------


def test_diff_slots_classification() -> None:
    d1, d2, d3, d4 = (date(2026, 12, i) for i in (1, 2, 3, 4))
    told = [(d1, time(10)), (d2, time(9)), (d3, time(14))]
    # 同じ日の出入りが先に組 (time)・残りを順に組 (date)・余りはお休み。
    current = [(d1, time(11)), (d4, time(9))]
    out = sms_changes.diff_slots(told, current)
    assert [c.kind for c in out] == ["time", "date", "cancel"]
    assert out[0] == VisitChange("time", d1, time(10), d1, time(11))
    assert out[1] == VisitChange("date", d2, time(9), d4, time(9))
    assert out[2] == VisitChange("cancel", old_date=d3, old_start=time(14))
    assert sms_changes.change_json(out[0])["label"] == "12/1(火)10:00→11:00"
    assert sms_changes.change_json(out[1])["label"] == "12/2(水)9:00→12/4(金)9:00"
    assert sms_changes.change_json(out[2])["to"] is None
    # 増えた方が多ければ余りは追加。同じ日に 2 回のうち 1 回だけ時刻が変わっても time。
    out = sms_changes.diff_slots(
        [(d1, time(10)), (d1, time(15))], [(d1, time(10)), (d1, time(16)), (d4, time(9))]
    )
    assert [c.kind for c in out] == ["time", "add"]
    assert out[0] == VisitChange("time", d1, time(15), d1, time(16))
    assert sms_changes.change_json(out[1])["from"] is None
    assert sms_changes.diff_slots(told, list(reversed(told))) == []
    assert sms_changes.is_urgent(out, date(2026, 11, 30)) is True  # 12/1 は明日
    assert sms_changes.is_urgent(out, date(2026, 11, 28)) is False


# ---------------------------------------------------------------------------
# 変更の検知
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_time_date_cancel_add(db, base) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    await _told(db, p, WEEK, [(date(2026, 12, 1), time(10)), (date(2026, 12, 3), time(14))])
    await _visit(db, p, date(2026, 12, 1), time(10))
    v = await _visit(db, p, date(2026, 12, 3), time(14))
    assert await _pending(db) == []

    v.start_time = time(15, 30)
    await db.commit()
    (pc,) = await _pending(db)
    assert [c.kind for c in pc.changes] == ["time"]
    assert pc.urgent is False

    v.visit_date = date(2026, 12, 4)
    v.start_time = time(10)
    await db.commit()
    (pc,) = await _pending(db)
    assert [c.kind for c in pc.changes] == ["date"]

    v.status = "cancelled"
    await db.commit()
    (pc,) = await _pending(db)
    assert [c.kind for c in pc.changes] == ["cancel"]

    v.status = "planned"
    v.visit_date = date(2026, 12, 3)
    v.start_time = time(14)
    await _visit(db, p, date(2026, 12, 5), time(9))
    (pc,) = await _pending(db)
    assert [c.kind for c in pc.changes] == ["add"]


@pytest.mark.asyncio
async def test_import_replacing_ids_staff_and_end_time_changes_are_nothing(db, base) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    await _told(db, p, WEEK, [(date(2026, 12, 1), time(10))])
    old = await _visit(db, p, date(2026, 12, 1), time(10))
    # カイポケの取込で訪問の番号が替わった (消して同じ日・時刻で入れ直した)。
    old.deleted_at = NOW
    await db.commit()
    new = await _visit(db, p, date(2026, 12, 1), time(10))
    assert await _pending(db) == []
    # 担当だけの交代・終わりの時刻だけの変更・打刻だけの予定外は知らせない。
    staff = Staff(
        name="見本看護師", role="staff", is_trainee=False, primary_office_id=base["office_id"]
    )
    db.add(staff)
    await db.commit()
    new.primary_staff_id = staff.id
    new.end_time = time(12, 30)
    await _visit(db, p, date(2026, 12, 2), time(9), is_unplanned=True)
    await db.commit()
    assert await _pending(db) == []


@pytest.mark.asyncio
async def test_past_days_and_old_weeks_are_ignored(db, base) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    # 今週: 11/24 (昨日) と 11/26 (明日)。昨日の訪問が取り消されても知らせない。
    await _told(db, p, THIS_WEEK, [(date(2026, 11, 24), time(10)), (date(2026, 11, 26), time(10))])
    await _visit(db, p, date(2026, 11, 24), time(10), status="cancelled")
    v = await _visit(db, p, date(2026, 11, 26), time(10))
    # 先週 (今日を含まない週) は比べない。
    await _told(db, p, date(2026, 11, 16), [(date(2026, 11, 17), time(10))])
    assert await _pending(db) == []
    # 明日の変更は急ぎ。
    v.start_time = time(13)
    await db.commit()
    (pc,) = await _pending(db)
    assert pc.urgent is True and pc.notice.week_start == THIS_WEEK


@pytest.mark.asyncio
async def test_returned_to_told_state_clears_pending(db, base) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    notice = await _told(db, p, WEEK, [(date(2026, 12, 1), time(10))])
    v = await _visit(db, p, date(2026, 12, 1), time(11))
    # 最初に見た回はベルを出さない (5 分そのままになるのを待つ)。
    out = await sms_changes.run_detect(db, NOW)
    assert out == {"pending": 1, "urgent": 0, "new": 1, "bells": 0}
    await db.refresh(notice)
    assert notice.pending_signature and sms_notices.aware(notice.pending_since) == NOW

    v.start_time = time(10)
    await db.commit()
    out = await sms_changes.run_detect(db, NOW + timedelta(minutes=5))
    assert out == {"pending": 0, "urgent": 0, "new": 0, "bells": 0}
    await db.refresh(notice)
    # 30 分は気づいた時刻を残す (取込の途中で消えて戻ることがある)。
    assert sms_notices.aware(notice.pending_cleared_at) == NOW + timedelta(minutes=5)
    assert sms_notices.aware(notice.pending_since) == NOW
    out = await sms_changes.run_detect(db, NOW + timedelta(minutes=40))
    await db.refresh(notice)
    assert notice.pending_signature is None and notice.pending_since is None
    assert notice.pending_cleared_at is None and notice.bell_count == 0


# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_endpoint_shape(client, db, base, monkeypatch) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    await _told(db, p, WEEK, [(date(2026, 12, 3), time(14))])
    await _visit(db, p, date(2026, 12, 3), time(15, 30))
    await _contact(db, p, "09000000001", display="090-0000-0001")
    await _contact(db, p, "0430000000", kind="landline", relation="family", order=1, target=False)
    q = await _patient(db, "C-2", "見本 二郎")
    await _told(db, q, THIS_WEEK, [(date(2026, 11, 26), time(9))])
    await _contact(db, q, consent="unconfirmed")
    # 30 分前に気づいていた。
    await sms_changes.run_detect(db, NOW - timedelta(minutes=30))
    monkeypatch.setattr(get_settings(), "sms_station_short_name", "見本訪看")

    res = await client.get(f"{API}/changes/pending", headers=base["h"])
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["count"] == 2 and data["urgent_count"] == 1
    assert data["within_send_hours"] is True and data["send_hours"] == "8:00〜20:00"
    first, second = data["items"]
    # 急ぎが先。送れない方も一覧に出る。
    assert first["patient_id"] == str(q.id) and first["urgent"] is True
    assert first["sendable"] is False and first["not_sendable_reason"] == "consent_unconfirmed"
    assert first["not_sendable_label"] == "同意が未確認です"
    assert first["changes"] == [
        {
            "kind": "cancel",
            "from": {"date": "2026-11-26", "start": "09:00"},
            "to": None,
            "label": "11/26(木)9:00 お休み",
        }
    ]
    assert second["patient_id"] == str(p.id) and second["urgent"] is False
    assert second["week_start"] == "2026-11-30"
    assert second["detected_at"].startswith("2026-11-25T00:30:00")
    assert second["waiting_minutes"] == 30
    assert second["changes"][0]["kind"] == "time"
    assert second["changes"][0]["label"] == "12/3(木)14:00→15:30"
    assert second["sendable"] is True and second["not_sendable_reason"] is None
    assert second["destinations"] == [
        {
            "contact_id": second["destinations"][0]["contact_id"],
            "relation": "self",
            "relation_label": "ご本人",
            "phone_last4": "0001",
        }
    ]
    # お電話の番号は全桁 (送る先でない固定電話も)。
    assert second["call_numbers"] == [
        {"relation_label": "ご本人", "phone_display": "090-0000-0001"},
        {"relation_label": "ご家族", "phone_display": "0430000000"},
    ]
    assert second["body"].startswith("【見本訪看】見本様 ご訪問の変更です。12/3(木)14:00→15:30")
    assert "一郎" not in second["body"]
    assert second["char_count"] == len(second["body"])
    assert second["segments_est"] == 1
    assert second["est_yen"] == second["segments_est"] * 11
    # 印 (契約 §12): 送れる方は token と change_token・送れない方は change_token だけ。
    assert len(second["token"]) == 32 and len(second["change_token"]) == 32
    assert first["token"] is None and len(first["change_token"]) == 32

    # 状態 (週の画面のボタン)。
    res = await client.get(f"{API}/status", headers=base["h"])
    st = res.json()
    assert st == {
        "enabled": True,
        "provider": "mock",
        "send_mode": "test",
        "test_allowlist_count": 3,
        "within_send_hours": True,
        "send_hours": "8:00〜20:00",
        "pending_count": 2,
        "urgent_count": 1,
        "failed_recent_count": 0,
        "provider_ok": True,
        "undelivered_count": 0,
    }


# ---------------------------------------------------------------------------
# 送る・送らずに済ませる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_change_updates_snapshot_and_events(client, db, base, _clock) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    pid = p.id
    notice = await _told(db, p, WEEK, [(date(2026, 12, 3), time(14))])
    await _visit(db, p, date(2026, 12, 3), time(15, 30))
    await _contact(db, p)
    await sms_changes.run_detect(db, NOW - timedelta(minutes=20))
    (row,) = (await client.get(f"{API}/changes/pending", headers=base["h"])).json()["items"]
    item = {"patient_id": str(pid), "week_start": WEEK.isoformat(), "token": row["token"]}

    res = await client.post(
        f"{API}/changes/send",
        json={"items": [item], "expected_count": 2},
        headers=base["h"],
    )
    assert res.status_code == 409 and "一覧を開き直してください" in res.json()["detail"]
    res = await client.post(
        f"{API}/changes/send",
        json={"items": [item], "expected_count": 1, "body": "偽の本文"},
        headers=base["h"],
    )
    assert res.status_code == 422

    res = await client.post(
        f"{API}/changes/send",
        json={"items": [item], "expected_count": 1},
        headers=base["h"],
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert set(out) == {"batch_id", "patient_count", "message_count", "segments_est_total"}
    assert out["patient_count"] == 1 and out["message_count"] == 1

    db.expire_all()
    batch = await db.scalar(select(SmsBatch))
    assert batch.kind == "change" and batch.week_start == WEEK
    (msg,) = (await db.scalars(select(SmsMessage))).all()
    assert msg.kind == "change" and msg.status == "accepted"
    assert msg.body.startswith("【見本ステーション】見本様 ご訪問の変更です。")
    assert msg.dedupe_key == f"change:{pid}:{msg.contact_id}:{WEEK.isoformat()}:v2"
    await db.refresh(notice)
    # 受け付けられた (mock) → お伝えした内容がその版に替わる。
    assert notice.version == 2 and notice.told_version == 2 and notice.last_channel == "sms"
    assert notice.visits == [{"date": "2026-12-03", "start": "15:30"}]
    assert notice.pending_signature is None
    ev = await db.scalar(select(PatientNoticeEvent))
    assert ev.kind == "change" and ev.channel == "sms" and ev.batch_id == batch.id
    assert sms_notices.aware(ev.detected_at) == NOW - timedelta(minutes=20)
    assert sms_notices.aware(ev.told_at) == NOW

    # 送った後はお知らせ待ちでない → 選んだ方が待ちでないので 409 (だれにも送らない)。
    res = await client.get(f"{API}/changes/pending", headers=base["h"])
    assert res.json()["count"] == 0
    res = await client.post(
        f"{API}/changes/send",
        json={"items": [item], "expected_count": 0},
        headers=base["h"],
    )
    assert res.status_code == 409
    res = await client.post(
        f"{API}/changes/send", json={"items": [], "expected_count": 0}, headers=base["h"]
    )
    assert res.status_code == 422

    # 時間の外 (20:00 JST) は 422。
    _clock["now"] = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
    res = await client.post(
        f"{API}/changes/send",
        json={"items": [item], "expected_count": 1},
        headers=base["h"],
    )
    assert res.status_code == 422 and "8:00〜20:00" in res.json()["detail"]


@pytest.mark.asyncio
async def test_resolve_without_sending(client, db, base) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    notice = await _told(db, p, WEEK, [(date(2026, 12, 3), time(14))])
    await _contact(db, p)
    (row,) = (await client.get(f"{API}/changes/pending", headers=base["h"])).json()["items"]
    body = {
        "patient_id": str(p.id),
        "week_start": WEEK.isoformat(),
        "channel": "phone",
        "change_token": row["change_token"],
    }

    # change_token は必須・違えば 409 (見ていない変更を済みにしない)。
    no_token = {k: v for k, v in body.items() if k != "change_token"}
    res = await client.post(
        f"{API}/changes/resolve", json={**no_token, "reason": "phoned"}, headers=base["h"]
    )
    assert res.status_code == 422
    res = await client.post(
        f"{API}/changes/resolve",
        json={**body, "reason": "phoned", "change_token": "0" * 32},
        headers=base["h"],
    )
    assert res.status_code == 409 and "予定か連絡先が変わりました" in res.json()["detail"]
    # 手段と理由の組 (phone は phoned だけ・none は patient_request / other だけ)。
    for channel, reason in (("phone", "other"), ("phone", "patient_request"), ("none", "phoned")):
        res = await client.post(
            f"{API}/changes/resolve",
            json={**body, "channel": channel, "reason": reason, "note": "x"},
            headers=base["h"],
        )
        assert res.status_code == 422, (channel, reason)
    res = await client.post(
        f"{API}/changes/resolve",
        json={**body, "channel": "none", "reason": "other"},
        headers=base["h"],
    )
    assert res.status_code == 422
    res = await client.post(
        f"{API}/changes/resolve",
        json={**body, "reason": "other", "note": "x" * 201},
        headers=base["h"],
    )
    assert res.status_code == 422
    res = await client.post(
        f"{API}/changes/resolve",
        json={**body, "channel": "sms", "reason": "phoned"},
        headers=base["h"],
    )
    assert res.status_code == 422

    res = await client.post(
        f"{API}/changes/resolve", json={**body, "reason": "phoned"}, headers=base["h"]
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "version": 2}
    db.expire_all()
    await db.refresh(notice)
    assert notice.visits == [] and notice.last_channel == "phone" and notice.told_version == 2
    assert notice.told_by_user_id == base["admin_id"]
    ev = await db.scalar(select(PatientNoticeEvent))
    assert (ev.channel, ev.reason, ev.kind, ev.batch_id) == ("phone", "phoned", "change", None)
    assert ev.detected_at is not None
    assert await db.scalar(select(SmsMessage)) is None

    # 済ませた後は待ちでない (もう一度は 409)。
    res = await client.post(
        f"{API}/changes/resolve", json={**body, "reason": "phoned"}, headers=base["h"]
    )
    assert res.status_code == 409
    assert await _pending(db) == []


# ---------------------------------------------------------------------------
# ベル
# ---------------------------------------------------------------------------


async def _bells(db) -> list[Notification]:
    db.expire_all()
    return list(
        (
            await db.scalars(
                select(Notification)
                .where(Notification.type == "sms_change_pending")
                .order_by(Notification.created_at)
            )
        ).all()
    )


@pytest.mark.asyncio
async def test_bells_after_stable_60min_gap_and_no_duplicate(db, base) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    pid = p.id
    await _told(db, p, WEEK, [(date(2026, 12, 3), time(14))])
    await _visit(db, p, date(2026, 12, 3), time(15))

    # 最初に見た回はベルなし。次の回 (5 分後) も同じ印なら最初のベル。
    assert (await sms_changes.run_detect(db, NOW))["bells"] == 0
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=5)))["bells"] == 1
    (bell,) = await _bells(db)
    assert bell.title == "予定が変わった方が 1 名います（お知らせ待ち）"
    assert "見本" not in bell.title and "見本" not in (bell.body or "")
    assert bell.user_id == base["admin_id"] and bell.data == {"href": "/schedule?sms=changes"}

    for minutes in (10, 30, 64):
        assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=minutes)))["bells"] == 0
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=65)))["bells"] == 1
    assert len(await _bells(db)) == 2

    # 新しい方の変更: 5 分そのままになっても、前のベルから 15 分あくまで出さない。
    q = await _patient(db, "C-2", "見本 二郎")
    await _told(db, q, WEEK, [(date(2026, 12, 4), time(9))])
    out = await sms_changes.run_detect(db, NOW + timedelta(minutes=70))
    assert out == {"pending": 2, "urgent": 0, "new": 1, "bells": 0}
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=75)))["bells"] == 0
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=80)))["bells"] == 1
    bells = await _bells(db)
    assert bells[-1].title == "予定が変わった方が 2 名います（お知らせ待ち）"
    notice = await db.scalar(select(PatientWeekNotice).where(PatientWeekNotice.patient_id == pid))
    assert sms_notices.aware(notice.pending_since) == NOW and notice.bell_count == 3


@pytest.mark.asyncio
async def test_bell_storm_during_import_is_held(db, base) -> None:
    """取込の途中で予定が揺れているあいだは鳴らさない・消えて 30 分の内に戻れば気づいた時刻を残す."""
    p = await _patient(db, "C-1", "見本 一郎")
    pid = p.id
    notice = await _told(db, p, WEEK, [(date(2026, 12, 3), time(14))])
    v = await _visit(db, p, date(2026, 12, 3), time(15))
    # 5 分ごとに中身が替わる (取込の途中) → 印が替わり続けるのでベルは出ない。
    for i, minutes in enumerate((0, 5, 10, 15)):
        v.start_time = time(15 + i)
        await db.commit()
        out = await sms_changes.run_detect(db, NOW + timedelta(minutes=minutes))
        assert out["bells"] == 0 and out["new"] == 1
    # 取込が終わり、いったん元に戻る (違いが消える)。
    v.start_time = time(14)
    await db.commit()
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=20)))["pending"] == 0
    # 30 分の内にまた違いが出た → 気づいた時刻は最初のまま。
    v.start_time = time(16)
    await db.commit()
    await sms_changes.run_detect(db, NOW + timedelta(minutes=40))
    db.expire_all()
    notice = await db.scalar(select(PatientWeekNotice).where(PatientWeekNotice.patient_id == pid))
    assert sms_notices.aware(notice.pending_since) == NOW
    assert notice.pending_cleared_at is None
    # 落ち着いた (5 分そのまま) ら 1 回だけ。
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=45)))["bells"] == 1
    assert len(await _bells(db)) == 1
    # 30 分より長く消えていたら、新しく気づいたことにする。
    v.start_time = time(14)
    await db.commit()
    await sms_changes.run_detect(db, NOW + timedelta(minutes=50))
    await sms_changes.run_detect(db, NOW + timedelta(minutes=85))
    v.start_time = time(17)
    await db.commit()
    await sms_changes.run_detect(db, NOW + timedelta(minutes=90))
    db.expire_all()
    notice = await db.scalar(select(PatientWeekNotice).where(PatientWeekNotice.patient_id == pid))
    assert sms_notices.aware(notice.pending_since) == NOW + timedelta(minutes=90)


@pytest.mark.asyncio
async def test_urgent_bell_every_15_minutes(db, base) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    # 明日 (11/26) の訪問がお休みに。
    await _told(db, p, THIS_WEEK, [(date(2026, 11, 26), time(10))])
    out = await sms_changes.run_detect(db, NOW)
    assert out == {"pending": 1, "urgent": 1, "new": 1, "bells": 0}
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=5)))["bells"] == 1
    (bell,) = await _bells(db)
    assert "お電話でもお伝えください" in bell.body
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=10)))["bells"] == 0
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=20)))["bells"] == 1
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=25)))["bells"] == 0
    assert len(await _bells(db)) == 2


# ---------------------------------------------------------------------------
# cron の口・旗・権限
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_endpoints_and_flag_off(client, db, base) -> None:
    p = await _patient(db, "C-1", "見本 一郎")
    await _told(db, p, WEEK, [(date(2026, 12, 3), time(14))])
    h = base["h"]
    res = await client.post(f"{API}/changes/detect/run", headers=h)
    assert res.status_code == 200 and res.json() == {
        "pending": 1,
        "urgent": 0,
        "new": 1,
        "bells": 0,
    }
    res = await client.post(f"{API}/send-due/run", headers=h)
    assert res.json() == {"sent": 0, "stuck_fixed": 0}
    res = await client.post(f"{API}/status/poll/run", headers=h)
    assert res.json() == {"polled": 0, "delivered": 0, "failed": 0}

    # 旗 OFF: cron は 200 skipped・状態は enabled:false・ほかは 404。
    flag = await db.scalar(select(OfficeFeatureFlag))
    flag.enabled_at = None
    await db.commit()
    for url in ("/changes/detect/run", "/send-due/run", "/status/poll/run"):
        res = await client.post(f"{API}{url}", headers=h)
        assert res.status_code == 200 and res.json() == {"skipped": "disabled"}, url
    res = await client.get(f"{API}/status", headers=h)
    assert res.status_code == 200 and res.json() == {"enabled": False}
    for url in (
        "/changes/pending",
        "/summary",
        "/summary/export",
        "/patients/summary",
        f"/patients/{p.id}/recent",
        "/numbers/attention",
    ):
        res = await client.get(f"{API}{url}", headers=h)
        assert res.status_code == 404, url
    res = await client.post(
        f"{API}/changes/send", json={"items": [], "expected_count": 0}, headers=h
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_is_403(client, db, base) -> None:
    staff = User(email="sms2-staff@example.com", password_hash=hash_password("pw"), role="staff")
    db.add(staff)
    await db.commit()
    h = _bearer(staff)
    for url in (
        "/status",
        "/changes/pending",
        "/summary",
        "/summary/export",
        "/patients/summary",
        f"/patients/{staff.id}/recent",
        "/numbers/attention",
    ):
        assert (await client.get(f"{API}{url}", headers=h)).status_code == 403, url
    for url, body in (
        ("/changes/send", {"items": [], "expected_count": 0}),
        (
            "/changes/resolve",
            {
                "patient_id": str(staff.id),
                "week_start": WEEK.isoformat(),
                "channel": "phone",
                "reason": "phoned",
            },
        ),
        ("/changes/detect/run", None),
        ("/send-due/run", None),
        ("/status/poll/run", None),
    ):
        kw = {"json": body} if body is not None else {}
        assert (await client.post(f"{API}{url}", headers=h, **kw)).status_code == 403, url
