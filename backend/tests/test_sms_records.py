"""SMS のお知らせ (段階 2): 届いたかの照会・止まった行・届かなかったベル・お知らせの記録.

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-3・§5-4・§7-3、
``docs/plans/sms-notify-api-contract-2026-11-16.md`` §5〜§10。
お名前・番号はすべて見本。KDDI へはつながない (照会は偽の窓口・mock だけ)。
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, OfficeFeatureFlag, Patient, User
from app.models.notification import Notification
from app.models.patient_contact import PatientContact
from app.models.sms import (
    PatientNoticeEvent,
    PatientWeekNotice,
    SmsBatch,
    SmsMessage,
    SmsStatusEvent,
)
from app.services.sms import delivery, labels
from app.services.sms import notices as sms_notices
from app.services.sms.provider import MockProvider, SmsProviderError, StatusResult

# 2026-12-02 (水) 10:00 JST。
NOW = datetime(2026, 12, 2, 1, 0, tzinfo=UTC)
WEEK = date(2026, 11, 30)
API = "/api/v1/sms"


#: 外部キーを確かめる (PostgreSQL と同じく、親より先に子を書いたら落ちる)。
pytestmark = pytest.mark.usefixtures("sqlite_foreign_keys")


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SMS_SEND_MODE", "test")
    monkeypatch.setenv("SMS_TEST_ALLOWLIST", "09000000001,09000000002")


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(sms_notices, "_now", lambda: state["now"])
    monkeypatch.setattr(sms_notices, "SEND_INTERVAL_SECONDS", 0)
    return state


@pytest_asyncio.fixture
async def base(db):
    admin = User(email="sms3-admin@example.com", password_hash=hash_password("pw"), role="admin")
    office = Office(name="見本拠点", short_label="見", code="sms3off")
    db.add_all([admin, office])
    await db.commit()
    db.add(OfficeFeatureFlag(office_id=office.id, feature_key="sms_notify", enabled_at=NOW))
    batch = SmsBatch(kind="week", week_start=WEEK, created_by_user_id=admin.id, created_at=NOW)
    db.add(batch)
    await db.commit()
    await db.refresh(admin)
    await db.refresh(batch)
    return {"admin_id": admin.id, "h": _bearer(admin), "batch_id": batch.id}


async def _patient(db, code: str, name: str, kana: str | None = None) -> uuid.UUID:
    p = Patient(code=code, name=name, kana=kana, status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p.id


async def _contact(db, pid: uuid.UUID, phone: str, relation: str = "self") -> None:
    db.add(
        PatientContact(
            patient_id=pid,
            sort_order=0,
            relation=relation,
            phone=phone,
            phone_kind="mobile",
            is_notify_target=True,
            notify_channel="sms",
            consent_status="confirmed",
        )
    )
    await db.commit()


async def _msg(db, base, pid: uuid.UUID | None, **kw) -> uuid.UUID:
    created = kw.pop("created_at", NOW)
    m = SmsMessage(
        batch_id=base["batch_id"],
        kind=kw.pop("kind", "week"),
        week_start=kw.pop("week_start", WEEK),
        patient_id=pid,
        contact_id=uuid.uuid4(),
        relation=kw.pop("relation", "self"),
        phone=kw.pop("phone", "09000000001"),
        body=kw.pop("body", "見本の本文"),
        char_count=10,
        segments_est=kw.pop("segments_est", 1),
        status=kw.pop("status", "accepted"),
        dedupe_key=uuid.uuid4().hex,
        created_by_user_id=base["admin_id"],
        created_at=created,
        updated_at=created,
        **kw,
    )
    db.add(m)
    await db.commit()
    return m.id


async def _get(db, mid: uuid.UUID) -> SmsMessage:
    return await db.scalar(
        select(SmsMessage).where(SmsMessage.id == mid).execution_options(populate_existing=True)
    )


# ---------------------------------------------------------------------------
# 届いたかの照会
# ---------------------------------------------------------------------------


class FakeProvider:
    name = "kddi"

    def __init__(self, result: StatusResult | None) -> None:
        self.result = result
        self.calls = 0

    async def get_status(self, msg_id: str) -> StatusResult:
        self.calls += 1
        if self.result is None:
            raise SmsProviderError("timeout")
        return self.result


def _status(status: str, raw: str, **kw) -> StatusResult:
    return StatusResult(
        status=status,
        status_raw=raw,
        split_num=kw.get("split_num"),
        carrier=kw.get("carrier"),
        reason=kw.get("reason"),
        timestamp=None,
        transaction_id=kw.get("transaction_id"),
    )


def test_poll_schedule_offsets() -> None:
    t = NOW
    got = [delivery.next_poll_after(t, n) for n in range(7)]
    assert got == [
        t + timedelta(minutes=1),
        t + timedelta(minutes=5),
        t + timedelta(minutes=30),
        t + timedelta(hours=3),
        t + timedelta(hours=24),
        t + timedelta(hours=72),
        None,
    ]


@pytest.mark.asyncio
async def test_poll_until_unknown_after_sixth(db, base, monkeypatch) -> None:
    pid = await _patient(db, "R-1", "見本 一郎")
    mid = await _msg(
        db,
        base,
        pid,
        accepted_at=NOW,
        next_poll_at=NOW + timedelta(minutes=1),
        provider_msg_id="m1",
    )
    fake = FakeProvider(_status("accepted", "sent"))
    monkeypatch.setattr(delivery, "get_provider", lambda _s: fake)

    # 時刻の前は照会しない。
    assert (await delivery.run_poll(db, NOW + timedelta(seconds=30)))["polled"] == 0
    offsets = [
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=3),
        timedelta(hours=24),
    ]
    for i, off in enumerate(offsets, start=1):
        out = await delivery.run_poll(db, NOW + off)
        assert out == {"polled": 1, "delivered": 0, "failed": 0}
        m = await _get(db, mid)
        assert m.status == "accepted" and m.poll_count == i
        assert sms_notices.aware(m.next_poll_at) == delivery.next_poll_after(NOW, i)
    # 6 回目でも決まらない → 分からない (それ以上は照会しない)。
    await delivery.run_poll(db, NOW + timedelta(hours=72))
    m = await _get(db, mid)
    assert m.status == "unknown" and m.poll_count == 6 and m.next_poll_at is None
    assert m.status_decided_at is not None
    assert (await delivery.run_poll(db, NOW + timedelta(hours=80)))["polled"] == 0
    assert fake.calls == 6
    events = (
        await db.scalars(select(SmsStatusEvent).where(SmsStatusEvent.message_id == mid))
    ).all()
    # 状態が変わったとき (sent を初めて見た・最後に分からない) だけ残す。
    assert [e.status for e in events] == ["accepted", "unknown"]

    # 照会ができなかったときも回数に数える (いつか止まる)。
    mid2 = await _msg(
        db,
        base,
        pid,
        accepted_at=NOW,
        next_poll_at=NOW + timedelta(minutes=1),
        provider_msg_id="m2",
    )
    fake.result = None
    await delivery.run_poll(db, NOW + timedelta(minutes=1))
    m2 = await _get(db, mid2)
    assert m2.status == "accepted" and m2.poll_count == 1


@pytest.mark.asyncio
async def test_poll_delivered_and_failed_with_bell(client, db, base, monkeypatch) -> None:
    pid = await _patient(db, "R-1", "見本 一郎")
    ok = await _msg(db, base, pid, accepted_at=NOW, next_poll_at=NOW, provider_msg_id="mock-1")
    # mock の窓口は「届いた」を返す (外へ出ない)。
    monkeypatch.setattr(delivery, "get_provider", lambda _s: MockProvider())
    res = await client.post(f"{API}/status/poll/run", headers=base["h"])
    assert res.status_code == 200 and res.json() == {"polled": 1, "delivered": 1, "failed": 0}
    m = await _get(db, ok)
    assert m.status == "delivered" and m.status_decided_at is not None

    ng = await _msg(db, base, pid, accepted_at=NOW, next_poll_at=NOW, provider_msg_id="k-2")
    fake = FakeProvider(
        _status(
            "failed",
            "failed",
            carrier="au",
            split_num=2,
            reason="ESME_REGULATED",
            transaction_id="tx-1",
        )
    )
    monkeypatch.setattr(delivery, "get_provider", lambda _s: fake)
    res = await client.post(f"{API}/status/poll/run", headers=base["h"])
    assert res.json() == {"polled": 1, "delivered": 0, "failed": 1}
    m = await _get(db, ng)
    assert (m.status, m.carrier, m.split_num, m.fail_reason_raw, m.transaction_id) == (
        "failed",
        "au",
        2,
        "ESME_REGULATED",
        "tx-1",
    )
    assert m.fail_notified_at is not None
    bells = (
        await db.scalars(select(Notification).where(Notification.type == "sms_delivery_failed"))
    ).all()
    assert len(bells) == 1
    assert bells[0].title == "届かなかったお知らせが 1 件あります"
    assert "見本" not in bells[0].title + (bells[0].body or "")
    assert bells[0].data == {"href": "/sms-notices"}
    # 同じ 1 通でベルを重ねない。
    await client.post(f"{API}/status/poll/run", headers=base["h"])
    await client.post(f"{API}/send-due/run", headers=base["h"])
    db.expire_all()
    assert (
        len(
            (
                await db.scalars(
                    select(Notification).where(Notification.type == "sms_delivery_failed")
                )
            ).all()
        )
        == 1
    )


@pytest.mark.asyncio
async def test_send_due_turns_stuck_sending_into_unknown(client, db, base) -> None:
    pid = await _patient(db, "R-1", "見本 一郎")
    stuck = await _msg(db, base, pid, status="sending", claimed_at=NOW - timedelta(minutes=11))
    fresh = await _msg(db, base, pid, status="sending", claimed_at=NOW - timedelta(minutes=5))
    # 送る直前の確かめ (連絡先・稼働中) を通る方。
    await _contact(db, pid, "09000000002")
    queued = await _msg(db, base, pid, status="queued", phone="09000000002")
    res = await client.post(f"{API}/send-due/run", headers=base["h"])
    assert res.status_code == 200 and res.json() == {"sent": 1, "stuck_fixed": 1}
    assert (await _get(db, stuck)).status == "unknown"
    assert (await _get(db, fresh)).status == "sending"
    q = await _get(db, queued)
    assert q.status == "accepted" and q.attempt_count == 1
    # unknown にした行は送り直さない。
    res = await client.post(f"{API}/send-due/run", headers=base["h"])
    assert res.json() == {"sent": 0, "stuck_fixed": 0}
    assert (await _get(db, stuck)).attempt_count == 0


# ---------------------------------------------------------------------------
# お知らせの記録
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_numbers(client, db, base) -> None:
    res = await client.get(f"{API}/summary", params={"month": "2026-12"}, headers=base["h"])
    data = res.json()
    assert data["totals"]["messages"] == 0 and data["totals"]["delivered_rate"] is None
    assert data["changes"]["median_minutes_to_tell"] is None
    assert (
        await client.get(f"{API}/summary", params={"month": "2026-13"}, headers=base["h"])
    ).status_code == 422

    pid = await _patient(db, "R-1", "見本 一郎")
    await _msg(db, base, pid, status="delivered", split_num=2, carrier="au")
    await _msg(db, base, pid, status="delivered", segments_est=3, carrier="docomo")
    await _msg(db, base, pid, status="failed", carrier="au", fail_reason_raw="ESME_REGULATED")
    await _msg(db, base, pid, status="unknown", kind="change")
    await _msg(db, base, pid, status="queued", kind="change")
    await _msg(db, base, pid, status="skipped", skip_reason="test_mode", segments_est=5)
    # 11/30 23:30 JST (= 14:30 UTC) は 11 月。12/1 0:10 JST は 12 月。
    await _msg(
        db, base, pid, status="delivered", created_at=datetime(2026, 11, 30, 14, 30, tzinfo=UTC)
    )
    await _msg(
        db, base, pid, status="delivered", created_at=datetime(2026, 11, 30, 15, 10, tzinfo=UTC)
    )
    notice = PatientWeekNotice(
        patient_id=pid, week_start=WEEK, visits=[], version=3, last_channel="sms"
    )
    db.add(notice)
    await db.commit()
    for version, (ch, minutes, urgent) in enumerate(
        (("sms", 10, True), ("sms", 40, False), ("phone", 90, False)), start=1
    ):
        db.add(
            PatientNoticeEvent(
                notice_id=notice.id,
                patient_id=pid,
                week_start=WEEK,
                version=version,
                kind="change",
                channel=ch,
                urgent=urgent,
                visits=[],
                detected_at=NOW - timedelta(minutes=minutes),
                told_at=NOW,
            )
        )
    await db.commit()

    data = (
        await client.get(f"{API}/summary", params={"month": "2026-12"}, headers=base["h"])
    ).json()
    t = data["totals"]
    assert data["month"] == "2026-12"
    assert (
        t["messages"],
        t["delivered"],
        t["failed"],
        t["unknown"],
        t["pending"],
        t["skipped"],
    ) == (
        7,
        3,
        1,
        1,
        1,
        1,
    )
    assert t["delivered_rate"] == pytest.approx(3 / 5)
    # 通数: split_num があればそれ (2)・無ければ見込み。送っていない行は数えない。
    assert t["segments"] == 2 + 3 + 1 + 1 + 1 + 1
    assert t["est_yen"] == (2 + 3 + 1) * 11
    kinds = {k["kind"]: k for k in data["by_kind"]}
    assert kinds["change"] == {"kind": "change", "messages": 2, "segments": 2, "est_yen": 0}
    carriers = {c["carrier"]: c for c in data["by_carrier"]}
    assert carriers["au"] == {"carrier": "au", "messages": 2, "delivered": 1, "failed": 1}
    assert carriers["unknown"]["messages"] == 3
    assert data["by_fail_reason"] == [
        {
            "reason": "ESME_REGULATED",
            "label": "迷惑 SMS の設定などで受け取れませんでした",
            "count": 1,
        }
    ]
    assert data["by_segments"] == [
        {"segments": "1", "messages": 4},
        {"segments": "2", "messages": 1},
        {"segments": "3+", "messages": 1},
    ]
    assert data["changes"] == {
        "count": 3,
        "urgent_count": 1,
        "median_minutes_to_tell": 40,
        "by_channel": [
            {"channel": "sms", "count": 2},
            {"channel": "phone", "count": 1},
            {"channel": "none", "count": 0},
        ],
    }
    assert data["by_week"] == [
        {"week_start": "2026-11-30", "messages": 7, "delivered": 3, "failed": 1}
    ]

    # 患者様ごと。
    res = await client.get(
        f"{API}/patients/summary", params={"month": "2026-12"}, headers=base["h"]
    )
    (item,) = res.json()["items"]
    assert item["patient_id"] == str(pid) and item["patient_name"] == "見本 一郎"
    assert (item["messages"], item["delivered"], item["failed"], item["told_by_phone"]) == (
        7,
        3,
        1,
        1,
    )
    assert item["segments"] == t["segments"]
    assert item["last_delivered_at"] is not None

    # Excel (本文は入れない)。
    res = await client.get(f"{API}/summary/export", params={"month": "2026-12"}, headers=base["h"])
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "sms_notices_2026-12.xlsx" in res.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(res.content))
    rows = list(wb.active.iter_rows(values_only=True))
    assert rows[0][0] == "日時（日本時間）" and len(rows) == 1 + 7
    flat = " ".join(str(c) for r in rows for c in r if c is not None)
    assert "見本の本文" not in flat and "09000000001" not in flat
    assert "0001" in flat and "届きました" in flat and "送っていません（試験の状態のため）" in flat
    assert "迷惑 SMS の設定などで受け取れませんでした" in flat


@pytest.mark.asyncio
async def test_patient_recent(client, db, base) -> None:
    pid = await _patient(db, "R-1", "見本 一郎")
    other = await _patient(db, "R-2", "見本 二郎")
    for i in range(7):
        await _msg(db, base, pid, status="delivered", created_at=NOW - timedelta(hours=i))
    await _msg(db, base, other, status="delivered")
    res = await client.get(f"{API}/patients/{pid}/recent", headers=base["h"])
    items = res.json()["items"]
    assert len(items) == 5 and {i["patient_id"] for i in items} == {str(pid)}
    assert items[0]["phone_last4"] == "0001" and "phone" not in items[0]
    assert "body" not in items[0] and items[0]["status"] == "delivered"
    res = await client.get(f"{API}/patients/{pid}/recent", params={"limit": 2}, headers=base["h"])
    assert len(res.json()["items"]) == 2


@pytest.mark.asyncio
async def test_numbers_attention_kinds(client, db, base) -> None:
    a = await _patient(db, "R-1", "見本 一郎")
    await _contact(db, a, "09000000001")
    # 2 回続けて届かない (1 回目は届いていた)。
    await _msg(db, base, a, status="delivered", created_at=NOW - timedelta(days=3))
    await _msg(
        db, base, a, status="failed", fail_reason_raw="EXPIRED", created_at=NOW - timedelta(days=2)
    )
    await _msg(
        db, base, a, status="failed", fail_reason_raw="UNDELIV", created_at=NOW - timedelta(days=1)
    )

    b = await _patient(db, "R-2", "見本 二郎")
    await _contact(db, b, "09000000002", relation="family")
    await _msg(db, base, b, phone="09000000002", relation="family", status="failed", carrier="sms")

    c = await _patient(db, "R-3", "見本 三郎")
    await _contact(db, c, "09000000003")
    await _msg(db, base, c, phone="09000000003", status="failed", fail_reason_raw="REJECTD")

    # 届いた後は出ない・今の連絡先に無い番号は出ない。
    d = await _patient(db, "R-4", "見本 四郎")
    await _contact(db, d, "09000000004")
    await _msg(
        db,
        base,
        d,
        phone="09000000004",
        status="failed",
        fail_reason_raw="REJECTD",
        created_at=NOW - timedelta(days=1),
    )
    await _msg(db, base, d, phone="09000000004", status="delivered")
    await _msg(db, base, d, phone="09000000099", status="failed", fail_reason_raw="REJECTD")

    res = await client.get(f"{API}/numbers/attention", headers=base["h"])
    assert res.status_code == 200
    got = {(i["patient_id"], i["kind"]): i for i in res.json()["items"]}
    assert set(got) == {(str(a), "repeated_failure"), (str(b), "not_in_use"), (str(c), "blocked")}
    rf = got[(str(a), "repeated_failure")]
    assert rf["count"] == 2 and rf["kind_label"] == "続けて届いていません"
    assert rf["phone_last4"] == "0001" and rf["relation_label"] == "ご本人"
    assert rf["patient_name"] == "見本 一郎" and rf["last_at"] is not None
    assert got[(str(b), "not_in_use")]["relation_label"] == "ご家族"
    assert "09000000001" not in res.text


def test_fail_reason_labels_never_use_forbidden_words() -> None:
    assert labels.fail_reason_label("ESME_RINVDSTADR") == "番号が正しくありません"
    assert labels.fail_reason_label("esme_regulated") == "迷惑 SMS の設定などで受け取れませんでした"
    assert labels.fail_reason_label("XYZ") == "理由は分かりません（KDDI の値: XYZ）"
    assert labels.fail_reason_label(None) == "理由は記録されていません"
    for text in [*labels.FAIL_REASON_LABEL.values(), *labels.STATUS_LABEL.values()]:
        for word in ("直す", "修正", "断られ"):
            assert word not in text
    assert labels.status_label("skipped", "test_mode") == "送っていません（試験の状態のため）"


async def test_messages_status_filter_accepts_comma_separated(client, db, base) -> None:
    """状態はカンマでつないで複数渡せる（「届いていないもの」の絞り込み用）."""
    pid = await _patient(db, "S-1", "見本 三郎")
    for st in ("delivered", "failed", "unknown", "skipped", "accepted"):
        await _msg(db, base, pid, status=st)
    res = await client.get(
        f"{API}/messages", params={"status": "failed,unknown,skipped"}, headers=base["h"]
    )
    assert res.status_code == 200
    assert sorted(i["status"] for i in res.json()["items"]) == ["failed", "skipped", "unknown"]
    res = await client.get(f"{API}/messages", params={"status": "delivered"}, headers=base["h"])
    assert [i["status"] for i in res.json()["items"]] == ["delivered"]


# ---------------------------------------------------------------------------
# 2 回目のレビュー (2026-11-17): 届いていないもの・キャリア・理由なし・お名前なし
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_undelivered_filter_matches_status_count_and_carrier_ignores_case(
    client, db, base
) -> None:
    pid = await _patient(db, "U-1", "見本 一郎")
    failed = await _msg(db, base, pid, status="failed", carrier="AU")
    unknown = await _msg(db, base, pid, status="unknown")
    expired = await _msg(db, base, pid, status="skipped", skip_reason="expired")
    await _msg(db, base, pid, status="skipped", skip_reason="test_mode")
    await _msg(db, base, pid, status="delivered", carrier="au")
    # 7 日より前の分: 一覧 (undelivered) には出る・/status の件数 (直近 7 日) には数えない。
    old = await _msg(db, base, pid, status="failed", created_at=NOW - timedelta(days=8))

    res = await client.get(f"{API}/messages", params={"undelivered": "true"}, headers=base["h"])
    assert res.status_code == 200
    got = {i["id"] for i in res.json()["items"]}
    assert got == {str(failed), str(unknown), str(expired), str(old)}
    st = (await client.get(f"{API}/status", headers=base["h"])).json()
    assert st["undelivered_count"] == 3

    # カンマ区切りの status もそのまま使える。
    res = await client.get(
        f"{API}/messages", params={"status": "failed,unknown"}, headers=base["h"]
    )
    assert {i["id"] for i in res.json()["items"]} == {str(failed), str(unknown), str(old)}

    # キャリアは大文字・小文字を区別しない。
    res = await client.get(f"{API}/messages", params={"carrier": "au"}, headers=base["h"])
    assert res.json()["total"] == 2
    res = await client.get(f"{API}/messages", params={"carrier": "Au"}, headers=base["h"])
    assert res.json()["total"] == 2


@pytest.mark.asyncio
async def test_summary_reason_without_value_is_unknown(client, db, base) -> None:
    pid = await _patient(db, "U-1", "見本 一郎")
    await _msg(db, base, pid, status="failed")
    await _msg(db, base, pid, status="failed", fail_reason_raw="EXPIRED")
    res = await client.get(f"{API}/summary", params={"month": "2026-12"}, headers=base["h"])
    reasons = {r["reason"]: r["label"] for r in res.json()["by_fail_reason"]}
    assert reasons == {
        "unknown": "理由は分かりません",
        "EXPIRED": labels.fail_reason_label("EXPIRED"),
    }
    assert None not in reasons


@pytest.mark.asyncio
async def test_missing_patient_name_is_never_null(client, db, base, monkeypatch) -> None:
    """患者様の行が見つからない (消された) ときも、お名前は null にしない."""
    from app.services.sms import reports

    pid = await _patient(db, "U-1", "見本 一郎")
    await _contact(db, pid, "09000000001")
    for _ in range(2):
        await _msg(db, base, pid, status="failed", phone="09000000001")

    async def _no_patients(_db, _ids):
        return {}

    monkeypatch.setattr(reports, "_patient_names", _no_patients)
    res = await client.get(
        f"{API}/patients/summary", params={"month": "2026-12"}, headers=base["h"]
    )
    (item,) = res.json()["items"]
    assert item["patient_name"] == "（削除された方）"
    res = await client.get(f"{API}/numbers/attention", headers=base["h"])
    items = res.json()["items"]
    assert items and all(i["patient_name"] == "（削除された方）" for i in items)
