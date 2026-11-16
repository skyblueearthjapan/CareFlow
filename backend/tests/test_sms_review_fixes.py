"""SMS のお知らせ: レビュー (2026-11-16) の直しの試験.

正典: ``docs/plans/sms-notify-api-contract-2026-11-16.md`` §12 (行の印)・§13 (レビューで決めたこと)。
お名前・番号・事業所はすべて見本 (09000000001 など)。送るのは mock と偽の窓口だけ
(KDDI へはつながない・MockTransport は呼ばれたら失敗にする)。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db import session as db_session
from app.models import Office, OfficeFeatureFlag, Patient, User
from app.models.business_profile import BusinessProfile
from app.models.notification import Notification
from app.models.patient_contact import PatientContact
from app.models.sms import PatientWeekNotice, SmsMessage
from app.models.visit import Visit
from app.services.sms import changes as sms_changes
from app.services.sms import composer, delivery, labels
from app.services.sms import notices as sms_notices
from app.services.sms import provider as sms_provider
from app.services.sms.provider import SendResult, StatusResult

# 2026-11-25 (水) 10:00 JST = 01:00 UTC。今週の月曜は 11/23、来週の月曜は 11/30。
NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)
THIS_WEEK = date(2026, 11, 23)
WEEK = date(2026, 11, 30)
API = "/api/v1/sms"
ALLOW = "09000000001,09000000002,09000000003,09000000004,09000000005"


#: 外部キーを確かめる (PostgreSQL と同じく、親より先に子を書いたら落ちる)。
pytestmark = pytest.mark.usefixtures("sqlite_foreign_keys")


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SMS_SEND_MODE", "test")
    monkeypatch.setenv("SMS_TEST_ALLOWLIST", ALLOW)
    monkeypatch.setenv("SMS_PROVIDER", "mock")
    monkeypatch.setenv("SMS_STATION_SHORT_NAME", "")


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(sms_notices, "_now", lambda: state["now"])
    monkeypatch.setattr(sms_notices, "SEND_INTERVAL_SECONDS", 0)
    return state


@pytest.fixture(autouse=True)
def _no_network():
    """KDDI へは決してつながない (呼ばれたら試験を失敗にする)."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("network must not be used")

    sms_provider.set_test_transport(httpx.MockTransport(handler))
    yield calls
    sms_provider.set_test_transport(None)


@pytest_asyncio.fixture
async def base(db):
    admin = User(email="sms4-admin@example.com", password_hash=hash_password("pw"), role="admin")
    office = Office(name="見本拠点", short_label="見", code="sms4off")
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
    return {"admin": admin, "admin_id": admin.id, "h": _bearer(admin)}


async def _patient(db, code: str, name: str, status: str = "active") -> Patient:
    p = Patient(code=code, name=name, status=status, special_week_active=[])
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


async def _contact(db, patient: Patient, phone: str = "09000000001") -> PatientContact:
    c = PatientContact(
        patient_id=patient.id,
        sort_order=0,
        relation="self",
        phone=phone,
        phone_kind="mobile",
        is_notify_target=True,
        notify_channel="sms",
        consent_status="confirmed",
    )
    db.add(c)
    await db.commit()
    return c


async def _sendable(db, code: str, name: str, phone: str) -> Patient:
    p = await _patient(db, code, name)
    await _visit(db, p, date(2026, 12, 1), time(10))
    await _contact(db, p, phone)
    return p


async def _queue_week(db, now: datetime = NOW) -> list[SmsMessage]:
    """週のお知らせを待ちの行まで作る (まだ送らない)."""
    pv = await sms_notices.build_week_preview(db, WEEK, now)
    rows = [r for r in pv.sendable if r.state in sms_notices.DEFAULT_SEND_STATES]
    out = await sms_notices.create_week_batch(db, preview=pv, rows=rows, user_id=None, now=now)
    await db.commit()
    return out.messages


async def _messages(db) -> list[SmsMessage]:
    return list(
        (
            await db.scalars(
                select(SmsMessage)
                .order_by(SmsMessage.created_at, SmsMessage.phone)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def _bells(db, type_: str) -> list[Notification]:
    db.expire_all()
    return list((await db.scalars(select(Notification).where(Notification.type == type_))).all())


async def _preview_row(db, patient: Patient, now: datetime = NOW) -> sms_notices.PreviewRow:
    pv = await sms_notices.build_week_preview(db, WEEK, now)
    return next(r for r in pv.rows if r.patient_id == patient.id)


class FixedProvider:
    """決めた結果を返す偽の窓口 (外へ出ない)."""

    def __init__(self, outcome: str, name: str = "mock", **kw) -> None:
        self.name = name
        self.outcome = outcome
        self.kw = kw
        self.calls = 0

    async def send(self, phone, text):
        self.calls += 1
        if self.outcome == "raise":
            raise RuntimeError("boom")
        msg_id = f"fake-{uuid.uuid4().hex}" if self.outcome == "accepted" else None
        return SendResult(outcome=self.outcome, msg_id=msg_id, **self.kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# §13-1 受け付けられてから「お伝えした内容」を替える・一覧の状態
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skipped_attempt_does_not_change_told_snapshot(client, db, base, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "sms_test_allowlist", "")
    p = await _sendable(db, "V-1", "見本 一郎", "09000000001")
    await _queue_week(db)
    assert await sms_notices.send_due_messages() == 1
    (msg,) = await _messages(db)
    assert msg.status == "skipped" and msg.skip_reason == "test_mode" and msg.sent_at is None

    notice = await db.scalar(select(PatientWeekNotice))
    await db.refresh(notice)
    assert notice.version == 1 and notice.told_version is None
    assert notice.visits == [] and notice.told_at is None and notice.last_channel is None
    row = await _preview_row(db, p)
    assert row.state == "not_delivered"
    assert row.last_attempt["result"] == "skipped"
    assert row.last_attempt["label"] == "送っていません（試験の状態のため）"
    # 一度も受け付けられていない方は変更のお知らせの対象にしない。
    assert await sms_changes.compute_pending(db, NOW) == []
    # 次に送れば版が上がる (鍵が違うので送れる)。
    monkeypatch.setattr(get_settings(), "sms_test_allowlist", ALLOW)
    msgs = await _queue_week(db)
    assert msgs[0].notice_version == 2
    assert await sms_notices.send_due_messages() == 1
    db.expire_all()
    notice = await db.scalar(select(PatientWeekNotice))
    assert notice.told_version == 2 and notice.visits == [{"date": "2026-12-01", "start": "10:00"}]
    assert (await _preview_row(db, p)).state == "already_sent_same"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "result", "label"),
    [
        ("failed", "failed", "受け付けられませんでした"),
        ("unknown", "unknown", "送ったか分かりません"),
        ("raise", "unknown", "送ったか分かりません"),
    ],
)
async def test_failed_and_unknown_attempts(db, base, monkeypatch, outcome, result, label) -> None:
    p = await _sendable(db, "V-1", "見本 一郎", "09000000001")
    await _queue_week(db)
    fake = FixedProvider(outcome, error_code="timeout" if outcome == "unknown" else "http_400")
    monkeypatch.setattr(sms_notices, "get_provider", lambda _s: fake)
    assert await sms_notices.send_due_messages() == 1
    (msg,) = await _messages(db)
    assert msg.status == result
    # unknown も決まった時刻を持つ (§13 の 4)。
    assert msg.status_decided_at is not None
    row = await _preview_row(db, p)
    assert row.state == "not_delivered" and row.last_attempt["result"] == result
    assert row.last_attempt["label"] == label
    notice = await db.scalar(select(PatientWeekNotice))
    await db.refresh(notice)
    assert notice.told_version is None and notice.visits == []
    # 届かなかった・分からないはベルに出る (1 通につき 1 回)。
    await delivery.bell_new_failures(db, NOW)
    await db.commit()
    (bell,) = await _bells(db, "sms_delivery_failed")
    assert "1 件" in bell.title and "見本" not in bell.title + (bell.body or "")
    if result == "unknown":
        assert "分からない" in bell.title
    await delivery.bell_new_failures(db, NOW)
    await db.commit()
    assert len(await _bells(db, "sms_delivery_failed")) == 1


@pytest.mark.asyncio
async def test_delivery_failed_after_accept_is_not_delivered(db, base, monkeypatch) -> None:
    p = await _sendable(db, "V-1", "見本 一郎", "09000000001")
    await _queue_week(db)
    assert await sms_notices.send_due_messages() == 1
    (msg,) = await _messages(db)
    assert msg.status == "accepted"
    assert (await _preview_row(db, p)).state == "already_sent_same"

    class Failing:
        name = "mock"

        async def get_status(self, msg_id):
            return StatusResult("failed", "failed", None, "au", "EXPIRED", None, None)

    monkeypatch.setattr(delivery, "get_provider", lambda _s: Failing())
    out = await delivery.run_poll(db, NOW + timedelta(minutes=2))
    assert out == {"polled": 1, "delivered": 0, "failed": 1}
    row = await _preview_row(db, p)
    assert row.state == "not_delivered"
    assert row.last_attempt["result"] == "delivery_failed"
    assert row.last_attempt["label"] == "届きませんでした"
    # お伝えした内容は残す (受け付けられた版)。
    notice = await db.scalar(select(PatientWeekNotice))
    await db.refresh(notice)
    assert notice.told_version == 1
    assert len(await _bells(db, "sms_delivery_failed")) == 1


@pytest.mark.asyncio
async def test_sending_rows_cannot_be_sent_again(client, db, base) -> None:
    p = await _sendable(db, "V-1", "見本 一郎", "09000000001")
    await _queue_week(db)
    row = await _preview_row(db, p)
    assert row.state == "sending"
    pv = await sms_notices.build_week_preview(db, WEEK, NOW)
    assert pv.totals()["messages"] == 0
    res = await client.post(
        f"{API}/week-notices/send",
        json={
            "week_start": WEEK.isoformat(),
            "patient_ids": [str(p.id)],
            "tokens": {str(p.id): row.token},
            "expected_count": 1,
            "resend": True,
        },
        headers=base["h"],
    )
    assert res.status_code == 409 and "ほかの操作と重なりました" in res.json()["detail"]
    assert len(await _messages(db)) == 1


# ---------------------------------------------------------------------------
# §13-2 送る直前の確かめ・期限・ベル
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_before_send_and_unsent_bell(db, base) -> None:
    expired = await _sendable(db, "K-1", "見本 一郎", "09000000001")
    superseded = await _sendable(db, "K-2", "見本 二郎", "09000000002")
    changed = await _sendable(db, "K-3", "見本 三郎", "09000000003")
    inactive = await _sendable(db, "K-4", "見本 四郎", "09000000004")
    ok = await _sendable(db, "K-5", "見本 五郎", "09000000005")
    msgs = {m.patient_id: m for m in await _queue_week(db)}
    for m in msgs.values():
        assert sms_notices.aware(m.expires_at) == NOW + timedelta(minutes=60)

    m = await db.get(SmsMessage, msgs[expired.id].id)
    m.expires_at = NOW - timedelta(minutes=1)
    n = await db.scalar(
        select(PatientWeekNotice).where(PatientWeekNotice.patient_id == superseded.id)
    )
    n.version = 2
    c = await db.scalar(select(PatientContact).where(PatientContact.patient_id == changed.id))
    c.phone = "09000000009"
    pt = await db.get(Patient, inactive.id)
    pt.status = "suspended"
    await db.commit()

    assert await sms_notices.send_due_messages() == 5
    got = {m.patient_id: (m.status, m.skip_reason) for m in await _messages(db)}
    assert got == {
        expired.id: ("skipped", "expired"),
        superseded.id: ("skipped", "superseded"),
        changed.id: ("skipped", "contact_changed"),
        inactive.id: ("skipped", "patient_inactive"),
        ok.id: ("accepted", None),
    }
    (bell,) = await _bells(db, "sms_unsent")
    assert bell.title == "送れなかったお知らせが 4 件あります"
    assert "見本" not in bell.title + (bell.body or "")
    for reason in ("expired", "superseded", "contact_changed", "patient_inactive"):
        assert labels.status_label("skipped", reason) == (
            f"送っていません（{labels.SKIP_REASON_LABEL[reason]}）"
        )
    row = await _preview_row(db, expired)
    assert row.state == "not_delivered"
    assert row.last_attempt["label"] == "送っていません（送る前に時間が過ぎたため）"


@pytest.mark.asyncio
async def test_expired_when_created_on_another_day(db, base, _clock) -> None:
    await _sendable(db, "K-1", "見本 一郎", "09000000001")
    # 前の日の 19:50 JST に作った待ち (20:00 で止まったまま)。
    created = datetime(2026, 11, 24, 10, 50, tzinfo=UTC)
    (msg,) = await _queue_week(db, created)
    assert sms_notices.aware(msg.expires_at) == created + timedelta(minutes=60)
    # 翌朝 8:10 JST。期限の時刻より前でも日が違えば送らない。
    _clock["now"] = datetime(2026, 11, 24, 23, 10, tzinfo=UTC)
    m = await db.get(SmsMessage, msg.id)
    m.expires_at = _clock["now"] + timedelta(hours=1)
    await db.commit()
    assert await sms_notices.send_due_messages() == 1
    (msg,) = await _messages(db)
    assert (msg.status, msg.skip_reason) == ("skipped", "expired")


@pytest.mark.asyncio
async def test_crossing_20_00_mid_run_keeps_the_rest_queued(db, base, _clock, monkeypatch) -> None:
    await _sendable(db, "K-1", "見本 一郎", "09000000001")
    await _sendable(db, "K-2", "見本 二郎", "09000000002")
    # 19:30 JST に作った。
    _clock["now"] = datetime(2026, 11, 25, 10, 30, tzinfo=UTC)
    await _queue_week(db, _clock["now"])

    class Slow:
        name = "mock"
        calls = 0

        async def send(self, phone, text):
            Slow.calls += 1
            # 送っているあいだに 20:00 を過ぎた。
            _clock["now"] = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
            return SendResult(outcome="accepted", msg_id="fake-1")

    monkeypatch.setattr(sms_notices, "get_provider", lambda _s: Slow())
    _clock["now"] = datetime(2026, 11, 25, 10, 59, 59, tzinfo=UTC)
    assert await sms_notices.send_due_messages() == 1
    statuses = sorted(m.status for m in await _messages(db))
    assert statuses == ["accepted", "queued"] and Slow.calls == 1
    # 時間の外では送らない (期限を過ぎたら expired)。
    _clock["now"] = datetime(2026, 11, 25, 11, 20, tzinfo=UTC)
    assert await sms_notices.send_due_messages() == 0
    _clock["now"] = datetime(2026, 11, 25, 11, 31, tzinfo=UTC)
    assert await sms_notices.send_due_messages() == 1
    assert sorted((m.status, m.skip_reason) for m in await _messages(db)) == [
        ("accepted", None),
        ("skipped", "expired"),
    ]


@pytest.mark.asyncio
async def test_same_number_spacing_checked_against_db(db, base, _clock) -> None:
    await _sendable(db, "K-1", "見本 一郎", "09000000001")
    (msg,) = await _queue_week(db)
    # 別の経路で同じ番号へ 3 分前に送っていた。
    other = SmsMessage(
        batch_id=msg.batch_id,
        kind="week",
        week_start=WEEK,
        phone="09000000001",
        status="accepted",
        dedupe_key="other-1",
        sent_at=NOW - timedelta(minutes=3),
        created_at=NOW - timedelta(minutes=3),
        updated_at=NOW - timedelta(minutes=3),
    )
    db.add(other)
    await db.commit()
    assert await sms_notices.send_due_messages() == 0
    got = await db.get(SmsMessage, msg.id, populate_existing=True)
    assert got.status == "queued" and got.sent_at is None
    assert sms_notices.aware(got.not_before) == NOW + timedelta(minutes=7)
    _clock["now"] = NOW + timedelta(minutes=7)
    assert await sms_notices.send_due_messages() == 1


@pytest.mark.asyncio
async def test_one_run_is_limited(db, base) -> None:
    assert sms_notices.MAX_SEND_PER_RUN == 30
    await _sendable(db, "K-1", "見本 一郎", "09000000001")
    await _sendable(db, "K-2", "見本 二郎", "09000000002")
    await _queue_week(db)
    assert await sms_notices.send_due_messages(limit=1) == 1
    assert sorted(m.status for m in await _messages(db)) == ["accepted", "queued"]


@pytest.mark.asyncio
async def test_two_runs_claim_a_row_only_once(db, base, monkeypatch) -> None:
    await _sendable(db, "K-1", "見本 一郎", "09000000001")
    (msg,) = await _queue_week(db)
    factory = db_session.get_session_factory()
    async with factory() as s1, factory() as s2:
        assert await sms_notices.claim_message(s1, msg.id, NOW) is True
        assert await sms_notices.claim_message(s2, msg.id, NOW) is False
    # 取った行を待ちに戻し、2 つの順番の処理を同時に動かしても 1 回だけ送る。
    m = await db.get(SmsMessage, msg.id, populate_existing=True)
    m.status = "queued"
    await db.commit()
    fake = FixedProvider("accepted")
    monkeypatch.setattr(sms_notices, "get_provider", lambda _s: fake)
    results = await asyncio.gather(sms_notices.send_due_messages(), sms_notices.send_due_messages())
    assert sum(results) == 1 and fake.calls == 1
    (m,) = await _messages(db)
    assert m.status == "accepted" and m.attempt_count == 1


# ---------------------------------------------------------------------------
# 同時の操作 (週・変更・済ませる)
# ---------------------------------------------------------------------------


async def _told_with_change(db) -> Patient:
    p = await _patient(db, "R-1", "見本 一郎")
    await _contact(db, p)
    db.add(
        PatientWeekNotice(
            patient_id=p.id,
            week_start=WEEK,
            visits=[{"date": "2026-12-03", "start": "14:00"}],
            version=1,
            told_version=1,
            last_channel="sms",
            told_at=NOW - timedelta(days=1),
        )
    )
    await db.commit()
    await _visit(db, p, date(2026, 12, 3), time(15))
    return p


@pytest.mark.asyncio
async def test_week_and_change_sends_racing(db, base) -> None:
    await _told_with_change(db)
    factory = db_session.get_session_factory()
    async with factory() as a, factory() as b:
        pv = await sms_notices.build_week_preview(a, WEEK, NOW)
        (row,) = pv.sendable
        assert row.state == "changed_since_sent"
        views = await sms_changes.build_pending_views(
            b, await sms_changes.compute_pending(b, NOW), NOW
        )
        await sms_notices.create_week_batch(a, preview=pv, rows=[row], user_id=None, now=NOW)
        await a.commit()
        with pytest.raises(sms_notices.SmsNoticeError) as exc:
            await sms_changes.create_change_batch(b, views=views, user_id=None, now=NOW)
        assert exc.value.status_code == 409
        assert exc.value.message == sms_notices.CONFLICT_MESSAGE
        await b.rollback()
    assert len(await _messages(db)) == 1


@pytest.mark.asyncio
async def test_resolve_and_change_send_racing(db, base) -> None:
    p = await _told_with_change(db)
    factory = db_session.get_session_factory()
    async with factory() as a, factory() as b:
        views = await sms_changes.build_pending_views(
            a, await sms_changes.compute_pending(a, NOW), NOW
        )
        (pending,) = await sms_changes.compute_pending(b, NOW)
        await sms_changes.create_change_batch(a, views=views, user_id=None, now=NOW)
        await a.commit()
        with pytest.raises(sms_notices.SmsNoticeError) as exc:
            # b は送っている途中を見ていない (版が替わった) → 409。
            await sms_changes.bump_notice_version(b, pending.notice, pending.version_seen)
        assert exc.value.status_code == 409
        await b.rollback()
    # 送っている途中の方は、一覧に出さない (済ませるのも 409・ほかの操作と重なった)。
    assert await sms_changes.compute_pending(db, NOW) == []
    with pytest.raises(sms_notices.SmsNoticeError) as exc:
        await sms_changes.resolve_change(
            db,
            patient_id=p.id,
            week_start=WEEK,
            change_token=pending.change_token,
            channel="phone",
            reason="phoned",
            note=None,
            user_id=None,
            now=NOW,
        )
    assert exc.value.status_code == 409
    assert exc.value.message == sms_notices.CONFLICT_MESSAGE


# ---------------------------------------------------------------------------
# §13-3 送れない文字・稼働中でない方
# ---------------------------------------------------------------------------


def test_bmp_detection_and_count() -> None:
    assert composer.has_unsupported_chars("𠮷田様") is True
    assert composer.has_unsupported_chars("見本様😀") is True
    assert composer.has_unsupported_chars("髙橋様") is False
    assert composer.text_length("𠮷") == 2 and composer.count_chars("𠮷\n") == 4
    with pytest.raises(sms_provider.UnsupportedCharError):
        sms_provider.encode_sms_text("𠮷田")


@pytest.mark.asyncio
async def test_provider_refuses_non_bmp(_no_network) -> None:
    from types import SimpleNamespace

    kddi = sms_provider.KddiProvider(
        SimpleNamespace(
            sms_kddi_host="fake-host.invalid",
            sms_kddi_agg_id="a",
            sms_kddi_bot_id="b",
            sms_kddi_sms_number="0000000000",
            sms_kddi_basic_id="x",
            sms_kddi_basic_password="y",
        )
    )
    res = await kddi.send("09000000001", "𠮷田様")
    assert res.outcome == "failed" and res.error_code == "unsupported_char"
    assert _no_network == []
    assert labels.fail_reason_label("unsupported_char") == "SMS で送れない文字がありました"


@pytest.mark.asyncio
async def test_unsupported_char_in_week_and_change(client, db, base) -> None:
    p = await _sendable(db, "B-1", "𠮷田 一郎", "09000000001")
    pv = await sms_notices.build_week_preview(db, WEEK, NOW)
    (row,) = pv.rows
    assert row.reason == "unsupported_char" and row.destinations == []
    res = await client.post(
        f"{API}/week-notices/preview", json={"week_start": WEEK.isoformat()}, headers=base["h"]
    )
    (ns,) = res.json()["not_sendable"]
    assert ns["reason"] == "unsupported_char"
    assert ns["reason_label"] == "お名前などに、SMS で送れない文字があります"

    db.add(
        PatientWeekNotice(
            patient_id=p.id,
            week_start=WEEK,
            visits=[{"date": "2026-12-03", "start": "14:00"}],
            version=1,
            told_version=1,
            last_channel="phone",
        )
    )
    await db.commit()
    res = await client.get(f"{API}/changes/pending", headers=base["h"])
    (item,) = res.json()["items"]
    assert item["sendable"] is False and item["not_sendable_reason"] == "unsupported_char"
    assert item["token"] is None


@pytest.mark.asyncio
async def test_inactive_patient_in_pending_can_only_be_resolved(client, db, base) -> None:
    p = await _told_with_change(db)
    pt = await db.get(Patient, p.id)
    pt.status = "hospitalized"
    await db.commit()
    res = await client.get(f"{API}/changes/pending", headers=base["h"])
    (item,) = res.json()["items"]
    assert item["sendable"] is False and item["not_sendable_reason"] == "patient_inactive"
    assert item["not_sendable_label"] == "いまは稼働中でない方です（お電話でお伝えください）"
    assert item["token"] is None and item["destinations"] == []
    res = await client.post(
        f"{API}/changes/send",
        json={
            "items": [{"patient_id": str(p.id), "week_start": WEEK.isoformat(), "token": "x"}],
            "expected_count": 1,
        },
        headers=base["h"],
    )
    assert res.status_code == 409
    res = await client.post(
        f"{API}/changes/resolve",
        json={
            "patient_id": str(p.id),
            "week_start": WEEK.isoformat(),
            "change_token": item["change_token"],
            "channel": "phone",
            "reason": "phoned",
        },
        headers=base["h"],
    )
    assert res.status_code == 200 and res.json()["version"] == 2


# ---------------------------------------------------------------------------
# §12 行の印
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_tokens_missing_mismatch_and_match(client, db, base) -> None:
    a = await _sendable(db, "T-1", "見本 一郎", "09000000001")
    b = await _sendable(db, "T-2", "見本 二郎", "09000000002")
    rows = (
        await client.post(
            f"{API}/week-notices/preview", json={"week_start": WEEK.isoformat()}, headers=base["h"]
        )
    ).json()["rows"]
    tokens = {r["patient_id"]: r["token"] for r in rows}
    body = {
        "week_start": WEEK.isoformat(),
        "patient_ids": [str(a.id), str(b.id)],
        "expected_count": 2,
    }
    # tokens が無い・足りない → 422。
    res = await client.post(f"{API}/week-notices/send", json=body, headers=base["h"])
    assert res.status_code == 422
    res = await client.post(
        f"{API}/week-notices/send",
        json={**body, "tokens": {str(a.id): tokens[str(a.id)]}},
        headers=base["h"],
    )
    assert res.status_code == 422
    # 見た後に 1 名の連絡先が変わった → 409 で、だれにも送らない。
    c = await db.scalar(select(PatientContact).where(PatientContact.patient_id == b.id))
    c.phone = "09000000003"
    await db.commit()
    res = await client.post(
        f"{API}/week-notices/send", json={**body, "tokens": tokens}, headers=base["h"]
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "予定か連絡先が変わりました。一覧を開き直してください"
    assert await _messages(db) == []
    # 開き直した印なら送れる。
    rows = (
        await client.post(
            f"{API}/week-notices/preview", json={"week_start": WEEK.isoformat()}, headers=base["h"]
        )
    ).json()["rows"]
    tokens = {r["patient_id"]: r["token"] for r in rows}
    res = await client.post(
        f"{API}/week-notices/send", json={**body, "tokens": tokens}, headers=base["h"]
    )
    assert res.status_code == 200, res.text
    assert len(await _messages(db)) == 2


@pytest.mark.asyncio
async def test_change_tokens_missing_mismatch(client, db, base) -> None:
    p = await _told_with_change(db)
    (item,) = (await client.get(f"{API}/changes/pending", headers=base["h"])).json()["items"]
    key = {"patient_id": str(p.id), "week_start": WEEK.isoformat()}
    res = await client.post(
        f"{API}/changes/send", json={"items": [key], "expected_count": 1}, headers=base["h"]
    )
    assert res.status_code == 422
    # 見た後に予定がまた変わった → 印が違う → 409 (だれにも送らない)。
    v = await db.scalar(select(Visit).where(Visit.patient_id == p.id))
    v.start_time = time(16)
    await db.commit()
    res = await client.post(
        f"{API}/changes/send",
        json={"items": [{**key, "token": item["token"]}], "expected_count": 1},
        headers=base["h"],
    )
    assert res.status_code == 409 and "予定か連絡先が変わりました" in res.json()["detail"]
    assert await _messages(db) == []
    # 済ませるのも、見ていない変更なら 409。
    res = await client.post(
        f"{API}/changes/resolve",
        json={**key, "change_token": item["change_token"], "channel": "phone", "reason": "phoned"},
        headers=base["h"],
    )
    assert res.status_code == 409
    (item,) = (await client.get(f"{API}/changes/pending", headers=base["h"])).json()["items"]
    res = await client.post(
        f"{API}/changes/send",
        json={"items": [{**key, "token": item["token"]}], "expected_count": 1},
        headers=base["h"],
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# §13-4 模擬の窓口・設定の誤り
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_value", ["mock", "kdi"])
async def test_live_without_kddi_sends_nothing(
    client, db, base, monkeypatch, provider_value
) -> None:
    monkeypatch.setattr(get_settings(), "sms_send_mode", "live")
    monkeypatch.setattr(get_settings(), "sms_provider", provider_value)
    await _sendable(db, "M-1", "見本 一郎", "09000000001")
    await _queue_week(db)
    assert await sms_notices.send_due_messages() == 1
    (msg,) = await _messages(db)
    assert (msg.status, msg.error_code, msg.provider_msg_id) == ("failed", "config", None)
    assert msg.status_decided_at is not None
    st = (await client.get(f"{API}/status", headers=base["h"])).json()
    assert st["provider"] == "mock" and st["send_mode"] == "live"
    # live なのに kddi でない・知らない値は、設定の誤り (送れない)。
    assert st["provider_ok"] is False
    assert st["undelivered_count"] == 1 and st["failed_recent_count"] == 1
    # ベルは「設定に誤りがある」(届かなかった、ではない)。24 時間に 1 回まで。
    (bell,) = await _bells(db, "sms_config_error")
    assert bell.title == "SMS の設定に誤りがあるため、送れていません"
    await delivery.bell_new_failures(db, NOW)
    await db.commit()
    assert await _bells(db, "sms_delivery_failed") == []
    await _queue_week(db)
    assert await sms_notices.send_due_messages() == 1
    assert len(await _bells(db, "sms_config_error")) == 1


def test_provider_ok_values() -> None:
    from types import SimpleNamespace

    kddi = {
        "sms_kddi_host": "fake-host.invalid",
        "sms_kddi_agg_id": "a",
        "sms_kddi_bot_id": "b",
        "sms_kddi_sms_number": "0000000000",
        "sms_kddi_basic_id": "x",
        "sms_kddi_basic_password": "y",
    }

    def ok(provider: str, mode: str, **kw) -> bool:
        return sms_provider.provider_ok(
            SimpleNamespace(sms_provider=provider, sms_send_mode=mode, **kw)
        )

    assert ok("mock", "test") is True
    assert ok("mock", "live") is False
    assert ok("kdi", "test") is False and ok("kdi", "live") is False
    assert ok("kddi", "live", **kddi) is True and ok("kddi", "test", **kddi) is True
    assert ok("kddi", "live", **{**kddi, "sms_kddi_host": ""}) is False


@pytest.mark.asyncio
async def test_kddi_without_settings_keeps_queued_and_bells_once(
    client, db, base, monkeypatch, _no_network, _clock
) -> None:
    monkeypatch.setattr(get_settings(), "sms_provider", "kddi")
    for name in ("sms_kddi_host", "sms_kddi_basic_password"):
        monkeypatch.setattr(get_settings(), name, "")
    await _sendable(db, "M-1", "見本 一郎", "09000000001")
    await _queue_week(db)
    assert await sms_notices.send_due_messages() == 0
    _clock["now"] = NOW + timedelta(minutes=1)
    assert await sms_notices.send_due_messages() == 0
    (msg,) = await _messages(db)
    assert msg.status == "queued" and msg.attempt_count == 0
    (bell,) = await _bells(db, "sms_config_missing")
    assert bell.title == "SMS の設定が足りないため、送れていません"
    assert _no_network == []
    st = (await client.get(f"{API}/status", headers=base["h"])).json()
    assert st["provider"] == "kddi" and st["provider_ok"] is False
    # 期限を過ぎたら expired (送れなかったベル)。
    _clock["now"] = NOW + timedelta(minutes=61)
    assert await sms_notices.send_due_messages() == 1
    (msg,) = await _messages(db)
    assert (msg.status, msg.skip_reason) == ("skipped", "expired")
    assert len(await _bells(db, "sms_unsent")) == 1


@pytest.mark.asyncio
async def test_mock_is_labelled(client, db, base) -> None:
    status = await sms_provider.MockProvider().get_status("mock-1")
    assert status.status == "delivered" and status.status_raw == "mock"
    await _sendable(db, "M-1", "見本 一郎", "09000000001")
    await _queue_week(db)
    await sms_notices.send_due_messages()
    res = await client.get(f"{API}/messages", headers=base["h"])
    assert res.json()["items"][0]["provider"] == "mock"


@pytest.mark.asyncio
async def test_stuck_sending_is_unknown_with_decided_time_and_bell(db, base) -> None:
    await _sendable(db, "M-1", "見本 一郎", "09000000001")
    (msg,) = await _queue_week(db)
    m = await db.get(SmsMessage, msg.id)
    m.status = "sending"
    m.claimed_at = NOW - timedelta(minutes=11)
    await db.commit()
    out = await delivery.run_send_due(db)
    assert out == {"sent": 0, "stuck_fixed": 1}
    m = await db.get(SmsMessage, msg.id, populate_existing=True)
    assert m.status == "unknown" and m.status_decided_at is not None
    assert m.fail_notified_at is not None
    assert len(await _bells(db, "sms_delivery_failed")) == 1


# ---------------------------------------------------------------------------
# §13-6・そのほか
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_todays_started_visits_are_not_listed(client, db, base) -> None:
    p = await _patient(db, "P-1", "見本 一郎")
    await _contact(db, p)
    # 今日 (11/25) 10:00 JST。9:00 は始まっている・10:00 ちょうども載せない・11:00 は載せる。
    await _visit(db, p, date(2026, 11, 25), time(9))
    await _visit(db, p, date(2026, 11, 25), time(10))
    await _visit(db, p, date(2026, 11, 25), time(11))
    await _visit(db, p, date(2026, 11, 27), time(9))
    res = await client.post(
        f"{API}/week-notices/preview",
        json={"week_start": THIS_WEEK.isoformat()},
        headers=base["h"],
    )
    (row,) = res.json()["rows"]
    assert row["visits"] == [
        {"date": "2026-11-25", "start": "11:00"},
        {"date": "2026-11-27", "start": "09:00"},
    ]
    # お伝えした内容に始まった訪問があっても、変更にはしない。
    db.add(
        PatientWeekNotice(
            patient_id=p.id,
            week_start=THIS_WEEK,
            visits=[
                {"date": "2026-11-25", "start": "09:00"},
                {"date": "2026-11-25", "start": "11:00"},
                {"date": "2026-11-27", "start": "09:00"},
            ],
            version=1,
            told_version=1,
            last_channel="sms",
        )
    )
    await db.commit()
    v9 = await db.scalar(select(Visit).where(Visit.patient_id == p.id, Visit.start_time == time(9)))
    v9.status = "cancelled"
    await db.commit()
    assert await sms_changes.compute_pending(db, NOW) == []


def test_allowlist_notation_variants() -> None:
    spec = "+81 90-0000-0001, ０９０－００００－０００２ ,090‐0000‐0003,+819000000004, "
    assert sms_notices.parse_allowlist(spec) == {
        "09000000001",
        "09000000002",
        "09000000003",
        "09000000004",
    }
    from types import SimpleNamespace

    s = SimpleNamespace(sms_send_mode="test", sms_test_allowlist=spec)
    assert sms_notices.may_really_send("09000000002", s) is True
    assert sms_notices.may_really_send("09000000005", s) is False


@pytest.mark.parametrize(
    ("name", "surname", "uncertain"),
    [
        ("見本 太郎", "見本", False),
        ("見本\t太郎", "見本", False),
        ("見本　太郎", "見本", False),
        ("見本太郎", "見本太郎", True),
        ("", "", False),
    ],
)
def test_surname_separators(name, surname, uncertain) -> None:
    assert composer.surname_of(name) == surname
    assert composer.surname_uncertain(name) is uncertain


@pytest.mark.asyncio
async def test_surname_uncertain_is_flagged_but_sendable(client, db, base) -> None:
    await _sendable(db, "N-1", "見本太郎", "09000000001")
    res = await client.post(
        f"{API}/week-notices/preview", json={"week_start": WEEK.isoformat()}, headers=base["h"]
    )
    (row,) = res.json()["rows"]
    assert row["surname_uncertain"] is True and row["surname"] == "見本太郎"


# ---------------------------------------------------------------------------
# 2 回目のレビュー (2026-11-17)
# ---------------------------------------------------------------------------


async def _api_preview_row(client, base, patient: Patient) -> dict:
    rows = (
        await client.post(
            f"{API}/week-notices/preview", json={"week_start": WEEK.isoformat()}, headers=base["h"]
        )
    ).json()["rows"]
    return next(r for r in rows if r["patient_id"] == str(patient.id))


@pytest.mark.asyncio
async def test_resend_with_stale_token_after_another_admin_sent(client, db, base) -> None:
    """一覧を開いているあいだに、ほかの管理者が送った → resend でも 2 通目を送らない."""
    p = await _sendable(db, "S-1", "見本 一郎", "09000000001")
    seen = await _api_preview_row(client, base, p)
    assert seen["state"] == "new"
    # ほかの管理者が送った (受け付けられた)。
    await _queue_week(db)
    assert await sms_notices.send_due_messages() == 1
    assert (await _api_preview_row(client, base, p))["state"] == "already_sent_same"
    body = {
        "week_start": WEEK.isoformat(),
        "patient_ids": [str(p.id)],
        "tokens": {str(p.id): seen["token"]},
        "expected_count": 1,
        "resend": True,
    }
    res = await client.post(f"{API}/week-notices/send", json=body, headers=base["h"])
    assert res.status_code == 409
    assert res.json()["detail"] == "予定か連絡先が変わりました。一覧を開き直してください"
    assert len(await _messages(db)) == 1
    # 開き直して「同じ内容を送り済み」と見たうえでの resend は送れる。
    fresh = await _api_preview_row(client, base, p)
    res = await client.post(
        f"{API}/week-notices/send",
        json={**body, "tokens": {str(p.id): fresh["token"]}},
        headers=base["h"],
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_week_token_changes_while_another_send_is_queued(client, db, base) -> None:
    """ほかの管理者の送信がまだ待ち (sending) のときも、見た印では送れない."""
    p = await _sendable(db, "S-2", "見本 二郎", "09000000002")
    seen = await _api_preview_row(client, base, p)
    await _queue_week(db)
    res = await client.post(
        f"{API}/week-notices/send",
        json={
            "week_start": WEEK.isoformat(),
            "patient_ids": [str(p.id)],
            "tokens": {str(p.id): seen["token"]},
            "expected_count": 1,
            "resend": True,
        },
        headers=base["h"],
    )
    assert res.status_code == 409
    assert len(await _messages(db)) == 1


@pytest.mark.asyncio
async def test_change_tokens_include_version(client, db, base, monkeypatch) -> None:
    """変更の一覧を見た後に、ほかの操作で版が上がった → 送る・済ませるとも 409."""
    p = await _told_with_change(db)
    (item,) = (await client.get(f"{API}/changes/pending", headers=base["h"])).json()["items"]
    # ほかの管理者が週のお知らせを送った (試験の状態のため送らなかった・版だけ上がる)。
    monkeypatch.setattr(get_settings(), "sms_test_allowlist", "")
    await _queue_week(db)
    assert await sms_notices.send_due_messages() == 1
    (again,) = (await client.get(f"{API}/changes/pending", headers=base["h"])).json()["items"]
    assert again["changes"] == item["changes"]
    assert again["token"] != item["token"] and again["change_token"] != item["change_token"]
    key = {"patient_id": str(p.id), "week_start": WEEK.isoformat()}
    res = await client.post(
        f"{API}/changes/send",
        json={"items": [{**key, "token": item["token"]}], "expected_count": 1},
        headers=base["h"],
    )
    assert res.status_code == 409 and "予定か連絡先が変わりました" in res.json()["detail"]
    res = await client.post(
        f"{API}/changes/resolve",
        json={**key, "change_token": item["change_token"], "channel": "phone", "reason": "phoned"},
        headers=base["h"],
    )
    assert res.status_code == 409
    res = await client.post(
        f"{API}/changes/resolve",
        json={**key, "change_token": again["change_token"], "channel": "phone", "reason": "phoned"},
        headers=base["h"],
    )
    assert res.status_code == 200, res.text
    notice = await db.scalar(select(PatientWeekNotice).where(PatientWeekNotice.patient_id == p.id))
    await db.refresh(notice)
    assert notice.told_via == "phone" and notice.last_channel == "phone"


@pytest.mark.asyncio
async def test_change_bell_settles_despite_cron_jitter(db, base) -> None:
    await _told_with_change(db)
    # 最初に見た回は鳴らさない・後の回でも 4 分に満たなければ鳴らさない。
    assert (await sms_changes.run_detect(db, NOW))["bells"] == 0
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=2)))["bells"] == 0
    # cron が少し早く回った (4 分 58 秒後) → 同じ印なら鳴らす。
    out = await sms_changes.run_detect(db, NOW + timedelta(minutes=4, seconds=58))
    assert out["bells"] == 1


@pytest.mark.asyncio
async def test_urgent_bell_is_not_held_by_global_gap(db, base) -> None:
    # 来週の変更 (急ぎでない)。
    await _told_with_change(db)
    assert (await sms_changes.run_detect(db, NOW))["bells"] == 0
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=5)))["bells"] == 1
    # 明日 (11/26) の訪問がお休みになった方 (急ぎ)。
    q = await _patient(db, "J-2", "見本 二郎")
    db.add(
        PatientWeekNotice(
            patient_id=q.id,
            week_start=THIS_WEEK,
            visits=[{"date": "2026-11-26", "start": "10:00"}],
            version=1,
            told_version=1,
            last_channel="sms",
        )
    )
    await db.commit()
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=6)))["bells"] == 0
    # 前のベルから 5 分しかたっていなくても、急ぎは落ち着いたら鳴らす。
    out = await sms_changes.run_detect(db, NOW + timedelta(minutes=10))
    assert out["urgent"] == 1 and out["bells"] == 1
    bells = await _bells(db, "sms_change_pending")
    assert len(bells) == 2 and "お電話でもお伝えください" in bells[-1].body
    # 急ぎの方ごとの 15 分ごとは守る。
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=20)))["bells"] == 0
    assert (await sms_changes.run_detect(db, NOW + timedelta(minutes=25)))["bells"] == 1


@pytest.mark.asyncio
async def test_retry_clears_sent_at_and_is_not_swept_as_stuck(db, base, monkeypatch, _clock):
    await _sendable(db, "Y-1", "見本 一郎", "09000000001")
    (msg,) = await _queue_week(db)
    fake = FixedProvider("retry", error_code="http_503")
    monkeypatch.setattr(sms_notices, "get_provider", lambda _s: fake)
    assert await sms_notices.send_due_messages() == 1
    m = await db.get(SmsMessage, msg.id, populate_existing=True)
    assert m.status == "queued" and m.sent_at is None and m.claimed_at is None
    assert m.attempt_count == 1
    # 15 分後に、もう一度取った (送っている途中)。古い時刻で「止まった」としない。
    later = NOW + timedelta(minutes=15)
    _clock["now"] = later
    assert await sms_notices.claim_message(db, msg.id, later) is True
    assert await delivery.fix_stuck_sending(db, later) == 0
    await db.commit()
    m = await db.get(SmsMessage, msg.id, populate_existing=True)
    assert m.status == "sending" and sms_notices.aware(m.claimed_at) == later
    # 取ってから 10 分たてば unknown。
    assert await delivery.fix_stuck_sending(db, later + timedelta(minutes=11)) == 1


@pytest.mark.asyncio
async def test_mock_told_is_not_told_after_switching_to_kddi(db, base, monkeypatch) -> None:
    p = await _sendable(db, "Z-1", "見本 一郎", "09000000001")
    await _queue_week(db)
    assert await sms_notices.send_due_messages() == 1
    notice = await db.scalar(select(PatientWeekNotice))
    await db.refresh(notice)
    assert notice.told_via == "mock" and notice.told_version == 1
    # 窓口が mock のあいだは、今のまま (送り済み・変更はお知らせ待ちに出る)。
    await _visit(db, p, date(2026, 12, 2), time(9))
    assert (await _preview_row(db, p)).state == "changed_since_sent"
    assert len(await sms_changes.compute_pending(db, NOW)) == 1

    # kddi に切り替えた (接続の値は見本・送らないので外へは出ない)。
    s = get_settings()
    monkeypatch.setattr(s, "sms_provider", "kddi")
    for name, value in (
        ("sms_kddi_host", "fake-host.invalid"),
        ("sms_kddi_agg_id", "a"),
        ("sms_kddi_bot_id", "b"),
        ("sms_kddi_sms_number", "0000000000"),
        ("sms_kddi_basic_id", "x"),
        ("sms_kddi_basic_password", "y"),
    ):
        monkeypatch.setattr(s, name, value)
    row = await _preview_row(db, p)
    assert row.state == "new" and row.last_attempt is None
    assert await sms_changes.compute_pending(db, NOW) == []
    pv = await sms_notices.build_week_preview(db, WEEK, NOW)
    assert pv.totals()["people"] == 1


@pytest.mark.asyncio
async def test_told_snapshot_never_goes_back(db, base) -> None:
    """古い版の受付が遅れて書かれても、新しい版のお伝えした内容を戻さない."""
    await _sendable(db, "W-1", "見本 一郎", "09000000001")
    (msg,) = await _queue_week(db)
    factory = db_session.get_session_factory()
    async with factory() as a, factory() as b:
        stale = await a.scalar(select(PatientWeekNotice))
        assert stale.told_version is None
        fresh = await b.scalar(select(PatientWeekNotice))
        fresh.told_version = 5
        fresh.version = 5
        fresh.visits = [{"date": "2026-12-04", "start": "09:00"}]
        await b.commit()
        m = await a.get(SmsMessage, msg.id)
        assert await sms_notices.record_accepted(a, m, NOW) is False
        await a.commit()
    db.expire_all()
    notice = await db.scalar(select(PatientWeekNotice))
    assert notice.told_version == 5
    assert notice.visits == [{"date": "2026-12-04", "start": "09:00"}]


@pytest.mark.asyncio
async def test_full_flow_with_foreign_keys_on(client, db, base, _clock) -> None:
    """外部キーを確かめる状態で、週の送信 → 変更の送信 → 済ませる が通る (PostgreSQL の順番)."""
    assert (await db.execute(text("PRAGMA foreign_keys"))).scalar() == 1
    p = await _sendable(db, "F-1", "見本 一郎", "09000000001")
    row = await _api_preview_row(client, base, p)
    res = await client.post(
        f"{API}/week-notices/send",
        json={
            "week_start": WEEK.isoformat(),
            "patient_ids": [str(p.id)],
            "tokens": {str(p.id): row["token"]},
            "expected_count": 1,
        },
        headers=base["h"],
    )
    assert res.status_code == 200, res.text
    await sms_notices.send_due_messages()
    # 予定が変わった → 変更のお知らせを送る。
    v = await db.scalar(select(Visit).where(Visit.patient_id == p.id))
    v.start_time = time(11)
    await db.commit()
    _clock["now"] = NOW + timedelta(minutes=11)
    (item,) = (await client.get(f"{API}/changes/pending", headers=base["h"])).json()["items"]
    key = {"patient_id": str(p.id), "week_start": WEEK.isoformat()}
    res = await client.post(
        f"{API}/changes/send",
        json={"items": [{**key, "token": item["token"]}], "expected_count": 1},
        headers=base["h"],
    )
    assert res.status_code == 200, res.text
    await sms_notices.send_due_messages()
    # また変わった → 送らずに済ませる。
    v = await db.scalar(select(Visit).where(Visit.patient_id == p.id))
    v.start_time = time(13)
    await db.commit()
    (item,) = (await client.get(f"{API}/changes/pending", headers=base["h"])).json()["items"]
    res = await client.post(
        f"{API}/changes/resolve",
        json={
            **key,
            "change_token": item["change_token"],
            "channel": "none",
            "reason": "other",
            "note": "見本の説明",
        },
        headers=base["h"],
    )
    assert res.status_code == 200, res.text
    notice = await db.scalar(select(PatientWeekNotice).where(PatientWeekNotice.patient_id == p.id))
    await db.refresh(notice)
    assert notice.told_version == 3 and notice.told_via == "none"
    assert notice.visits == [{"date": "2026-12-01", "start": "13:00"}]
