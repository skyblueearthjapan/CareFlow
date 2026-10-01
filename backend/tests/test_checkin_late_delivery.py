"""圏外で退避して遅れて届いた QR 打刻のテスト.

正典設計書: ``docs/plans/checkin-late-delivery-design-2026-10-01.md``。

* 読み取った日 (``device_time`` の JST の日付) の訪問へ、受信から 72 時間以内なら付ける。
* 実績時刻は読み取った時刻のまま。「遅れて届いた（10/2 8:30 受信）」を見えるようにする。
* 端末の時計が進んでいる・``device_time`` が無い・72 時間を過ぎた打刻は従来どおり。
* 予定外訪問は読み取った日に作る。同じ読み取りの再送・先に入れた調整も崩れない。

「いま」は固定する (``visits.py`` / ``visit_history.py`` の ``datetime.now``)。
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select

from app.api.v1 import visit_history as visit_history_api
from app.api.v1 import visits as visits_api
from app.core.security import create_access_token, hash_password
from app.models import Notification, Patient, Staff, User, Visit, VisitCheckin, VisitReview
from app.services.checkin.history import late_delivery_remarks
from app.services.checkin.monitor import build_monitor
from app.services.checkin.notify import NOTIFY_LATE_ARRIVAL, NOTIFY_MISMATCH, NOTIFY_MISSING

JST = ZoneInfo("Asia/Tokyo")

#: 読み取った日 (火曜) と、その翌朝の受信。
READ_DAY = date(2026, 9, 29)
NEXT_DAY = date(2026, 9, 30)

FORBIDDEN_WORDS = ("直す", "直し", "修正", "補正")


def _at(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    """``day`` の JST の時刻を UTC aware で返す (SQLite は timestamptz の時差を保存しない
    ので、API にも UTC で送る = 本番の PostgreSQL と同じ値として読み戻せる)。"""
    return datetime.combine(day, time(hour, minute, second), tzinfo=JST).astimezone(UTC)


def _freeze(monkeypatch, fixed: datetime) -> None:
    """API 側の ``datetime.now`` を ``fixed`` に固定する (呼ぶたびに 1 ミリ秒進める)."""
    ticks = itertools.count()

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206 - stdlib シグネチャに合わせる
            moment = fixed + timedelta(milliseconds=next(ticks))
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    monkeypatch.setattr(visits_api, "datetime", _Frozen)
    monkeypatch.setattr(visit_history_api, "datetime", _Frozen)


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _jst(iso: str | None) -> str | None:
    """API の ISO 文字列 → JST の ``M/D HH:MM``。"""
    if iso is None:
        return None
    value = datetime.fromisoformat(iso)
    if value.tzinfo is None:  # SQLite は naive (= UTC) で返す。
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(JST)
    return f"{value.month}/{value.day} {value:%H:%M}"


async def _nurse(db, code: str) -> tuple[Staff, User]:
    staff = Staff(name=f"看護 {code}")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    user = User(
        email=f"{code.lower()}@example.com",
        password_hash=hash_password("x"),
        role="staff",
        staff_id=staff.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return staff, user


async def _admin(db, code: str) -> User:
    user = User(email=f"{code.lower()}-admin@example.com", password_hash=hash_password("x"))
    user.role = "admin"
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _patient(db, code: str, **kwargs) -> Patient:
    patient = Patient(code=code, name=f"利用者 {code}", qr_token=f"tok-{code.lower()}", **kwargs)
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


async def _visit(db, patient: Patient, staff: Staff, day: date, **kwargs) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=day,
        start_time=kwargs.pop("start", time(18, 30)),
        end_time=kwargs.pop("end", time(19, 30)),
        type="regular",
        status=kwargs.pop("status", "planned"),
        **kwargs,
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


async def _post(client, user: User, visit: Visit, kind: str, payload: dict):
    path = "checkin" if kind == "arrival" else "checkout"
    return await client.post(
        f"/api/v1/visits/{visit.id}/{path}", headers=_bearer(user), json=payload
    )


async def _checkin_count(db, visit_id) -> int:
    return await db.scalar(
        select(func.count()).select_from(VisitCheckin).where(VisitCheckin.visit_id == visit_id)
    )


# ---------------------------------------------------------------------------
# 遅れて届いた打刻を、読み取った日の訪問に付ける
# ---------------------------------------------------------------------------


async def test_late_arrival_and_departure_go_to_the_read_day(client, db, monkeypatch) -> None:
    """18:45 に圏外で読んだ到着が翌朝 8:30 に届く → 読み取った日の訪問・実績 18:45."""
    staff, user = await _nurse(db, "LD1")
    admin = await _admin(db, "LD1")
    patient = await _patient(db, "LD1", lat=35.6, lng=140.1)
    visit = await _visit(db, patient, staff, READ_DAY)
    today_visit = await _visit(db, patient, staff, NEXT_DAY, start=time(10, 0), end=time(11, 0))

    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))
    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {"qr_token": "tok-ld1", "at": _at(READ_DAY, 18, 45).isoformat(), "lat": 35.6, "lng": 140.1},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == str(visit.id)
    assert body["status"] == "in_progress"
    assert _jst(body["actual_arrival_at"]) == "9/29 18:45"
    assert _jst(body["actual_arrival_read_at"]) == "9/29 18:45"
    assert _jst(body["actual_arrival_late_received_at"]) == "9/30 08:30"
    # 位置は読んだときの GPS で判定する (患者宅の座標と同じ = match)。
    assert body["latest_checkin"]["match_status"] == "match"

    # 退出も翌朝届く → 前日の訪問が完了する。
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 31))
    res = await _post(
        client,
        user,
        visit,
        "departure",
        {"qr_token": "tok-ld1", "at": _at(READ_DAY, 19, 30).isoformat()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    assert _jst(body["actual_departure_at"]) == "9/29 19:30"
    assert _jst(body["actual_departure_late_received_at"]) == "9/30 08:31"
    # 前日の訪問は、合わせられる期間 (7 日) の内。
    assert body["actual_adjust_allowed"] is True

    # 今日の一覧 (スマホの「今日の訪問」) は今日の訪問だけ。前日の打刻は混ざらない。
    res = await client.get(
        "/api/v1/visits",
        headers=_bearer(user),
        params={"week_start": NEXT_DAY.isoformat(), "week_end": NEXT_DAY.isoformat()},
    )
    assert res.status_code == 200, res.text
    items = res.json()
    assert [i["id"] for i in items] == [str(today_visit.id)]
    assert items[0]["actual_arrival_at"] is None
    assert items[0]["status"] == "planned"

    # 訪問モニター (読み取った日) に「遅れて届いた」の受信時刻が出る。
    monitor = await build_monitor(db, READ_DAY, now=_at(NEXT_DAY, 9, 0))
    row = next(v for r in monitor.staff for v in r.visits if v.visit_id == visit.id)
    assert row.phase == "done"
    assert row.stay_minutes == 45
    assert _jst(row.arrival_late_received_at.isoformat()) == "9/30 08:30"
    assert _jst(row.departure_late_received_at.isoformat()) == "9/30 08:31"

    # 打刻履歴の一覧・Excel・A4 の備考。
    params = {"from": READ_DAY.isoformat(), "to": READ_DAY.isoformat()}
    res = await client.get("/api/v1/visit-history", headers=_bearer(admin), params=params)
    assert res.status_code == 200, res.text
    item = next(i for i in res.json()["items"] if i["visit_id"] == str(visit.id))
    assert _jst(item["arrival_late_received_at"]) == "9/30 08:30"
    assert _jst(item["departure_late_received_at"]) == "9/30 08:31"
    assert item["remarks"] == [
        "到着が遅れて届いた（9/30 8:30 受信）",
        "退出が遅れて届いた（9/30 8:31 受信）",
    ]
    res = await client.get("/api/v1/visit-history/export", headers=_bearer(admin), params=params)
    assert res.status_code == 200, res.text
    sheet = load_workbook(BytesIO(res.content))["QR読み取りあり"]
    cells = [str(c) for row in sheet.iter_rows(values_only=True) for c in row if c]
    assert any("到着が遅れて届いた（9/30 8:30 受信）" in c for c in cells)
    res = await client.get("/api/v1/visit-history/report", headers=_bearer(admin), params=params)
    assert res.status_code == 200, res.text
    assert "到着が遅れて届いた（9/30 8:30 受信）" in res.text
    assert "「遅れて届いた」" in res.text
    for word in FORBIDDEN_WORDS:
        assert word not in "".join(item["remarks"])
    await db.rollback()


async def test_late_arrival_older_than_72h_is_rejected(client, db, monkeypatch) -> None:
    """読み取りから 73 時間後に届いた打刻は 409 (期限切れ)。打刻は残らない."""
    staff, user = await _nurse(db, "LD2")
    visit = await _visit(db, await _patient(db, "LD2"), staff, READ_DAY)
    read = _at(READ_DAY, 18, 45)

    _freeze(monkeypatch, read + timedelta(hours=73))
    res = await _post(
        client, user, visit, "arrival", {"qr_token": "tok-ld2", "at": read.isoformat()}
    )
    assert res.status_code == 409, res.text
    assert res.json() == {
        "detail": "読み取りから 3 日を過ぎたため送信できません。管理者に連絡してください",
        "code": "late_expired",
    }
    assert await _checkin_count(db, visit.id) == 0

    # 72 時間 + 1 秒も断る (境界)。
    _freeze(monkeypatch, read + timedelta(hours=72, seconds=1))
    res = await _post(
        client, user, visit, "arrival", {"qr_token": "tok-ld2", "at": read.isoformat()}
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "late_expired"

    # 72 時間ちょうどまでは受け付ける。
    _freeze(monkeypatch, read + timedelta(hours=72))
    res = await _post(
        client, user, visit, "arrival", {"qr_token": "tok-ld2", "at": read.isoformat()}
    )
    assert res.status_code == 200, res.text
    assert _jst(res.json()["actual_arrival_at"]) == "9/29 18:45"
    await db.rollback()


async def test_device_clock_ahead_falls_back_to_server_time(client, db, monkeypatch) -> None:
    """端末の時計が進んでいる (120 秒超) ``device_time`` は採らない."""
    staff, user = await _nurse(db, "LD3")
    patient = await _patient(db, "LD3")
    today = await _visit(db, patient, staff, NEXT_DAY, start=time(8, 0), end=time(9, 0))
    tomorrow = await _visit(db, patient, staff, NEXT_DAY + timedelta(days=1))
    now = _at(NEXT_DAY, 8, 30)
    _freeze(monkeypatch, now)

    # 今日の訪問: 受信時刻が実績になり、「遅れて届いた」ではない。
    res = await _post(
        client,
        user,
        today,
        "arrival",
        {"qr_token": "tok-ld3", "at": (now + timedelta(minutes=10)).isoformat()},
    )
    assert res.status_code == 200, res.text
    assert _jst(res.json()["actual_arrival_at"]) == "9/30 08:30"
    assert res.json()["actual_arrival_late_received_at"] is None

    # 明日の時刻に進んだ時計でも、明日の訪問には付けない。
    res = await _post(
        client,
        user,
        tomorrow,
        "arrival",
        {"qr_token": "tok-ld3", "at": _at(tomorrow.visit_date, 18, 45).isoformat()},
    )
    assert res.status_code == 409, res.text
    assert res.json() == {
        "detail": "この訪問は今日の予定ではないため記録できません",
        "code": "not_visit_day",
    }
    await db.rollback()


async def test_without_device_time_the_next_day_is_rejected(client, db, monkeypatch) -> None:
    """``device_time`` が無い打刻は従来どおり受信した日だけ (前日の訪問は 409)."""
    staff, user = await _nurse(db, "LD4")
    visit = await _visit(db, await _patient(db, "LD4"), staff, READ_DAY)
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))

    res = await _post(client, user, visit, "arrival", {"qr_token": "tok-ld4"})
    assert res.status_code == 409, res.text
    # 訪問の画面を開いたまま日付をまたいだ場合もこれ。何日の予定かを示す。
    assert res.json() == {
        "detail": "この訪問は昨日（9/29）の予定のため、今日は記録できません。管理者に連絡してください",
        "code": "not_visit_day",
    }
    # 読み取った日が訪問日と違う device_time も同じ。
    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {"qr_token": "tok-ld4", "at": _at(NEXT_DAY, 8, 29).isoformat()},
    )
    assert res.status_code == 409, res.text
    assert await _checkin_count(db, visit.id) == 0
    await db.rollback()


async def test_device_clock_behind_still_records_todays_visit(client, db, monkeypatch) -> None:
    """端末の時計が 1 日遅れていても、今日の訪問は従来どおり記録できる (受信時刻を採る)."""
    staff, user = await _nurse(db, "LD5")
    visit = await _visit(db, await _patient(db, "LD5"), staff, NEXT_DAY)
    _freeze(monkeypatch, _at(NEXT_DAY, 10, 0))

    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {"qr_token": "tok-ld5", "at": _at(READ_DAY, 10, 0).isoformat()},
    )
    assert res.status_code == 200, res.text
    assert _jst(res.json()["actual_arrival_at"]) == "9/30 10:00"
    assert res.json()["actual_arrival_late_received_at"] is None
    await db.rollback()


# ---------------------------------------------------------------------------
# 予定外訪問・再送・調整・通知
# ---------------------------------------------------------------------------


async def test_unplanned_late_arrival_is_created_on_the_read_day(client, db, monkeypatch) -> None:
    """予定外の到着が翌朝届く → 読み取った日に予定外訪問を作り、退出も前日の訪問を閉じる."""
    staff, user = await _nurse(db, "LD6")
    patient = await _patient(db, "LD6")
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))

    payload = {"qr_token": "tok-ld6", "at": _at(READ_DAY, 18, 45).isoformat()}
    res = await client.post("/api/v1/visits/adhoc-checkin", headers=_bearer(user), json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["visit_date"] == READ_DAY.isoformat()
    assert body["start_time"] == "18:45:00"
    assert body["is_unplanned"] is True
    assert _jst(body["actual_arrival_late_received_at"]) == "9/30 08:30"

    # 同じ読み取りの再送は、同じ予定外訪問に付く (2 本目を作らない)。
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 35))
    res = await client.post("/api/v1/visits/adhoc-checkin", headers=_bearer(user), json=payload)
    assert res.status_code == 200, res.text
    assert res.json()["id"] == body["id"]
    assert _jst(res.json()["actual_arrival_at"]) == "9/29 18:45"
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 1

    visit = visits[0]
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 36))
    res = await _post(
        client,
        user,
        visit,
        "departure",
        {"qr_token": "tok-ld6", "at": _at(READ_DAY, 19, 20).isoformat()},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"
    assert res.json()["end_time"] == "19:20:00"
    await db.rollback()


async def test_duplicate_late_resend_is_idempotent(client, db, monkeypatch) -> None:
    """同じ ``device_time`` の再送は同じ読み取り: 実績・status・調整は変わらない."""
    staff, user = await _nurse(db, "LD7")
    visit = await _visit(db, await _patient(db, "LD7"), staff, READ_DAY)
    payload = {"qr_token": "tok-ld7", "at": _at(READ_DAY, 18, 45).isoformat()}

    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))
    first = await _post(client, user, visit, "arrival", payload)
    assert first.status_code == 200, first.text
    # 先に入れた調整 (到着を 18:40 に合わせる) は再送の後も効いたまま。
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 32))
    res = await client.put(
        f"/api/v1/visits/{visit.id}/actual-time",
        headers=_bearer(user),
        json={"kind": "arrival", "time": "18:40", "reason_code": None, "reason_text": None},
    )
    assert res.status_code == 200, res.text
    assert _jst(res.json()["actual_arrival_at"]) == "9/29 18:40"

    _freeze(monkeypatch, _at(NEXT_DAY, 8, 40))
    second = await _post(client, user, visit, "arrival", payload)
    assert second.status_code == 200, second.text
    body = second.json()
    assert _jst(body["actual_arrival_at"]) == "9/29 18:40"
    assert _jst(body["actual_arrival_read_at"]) == "9/29 18:45"
    assert body["actual_arrival_adjusted"] is True
    assert body["status"] == first.json()["status"] == "in_progress"
    await db.rollback()


async def test_bundled_adjustment_on_a_late_arrival_applies(client, db, monkeypatch) -> None:
    """退避中に「その場で合わせた時刻」も、遅れて届いた到着と一緒に効く."""
    staff, user = await _nurse(db, "LD8")
    visit = await _visit(db, await _patient(db, "LD8"), staff, READ_DAY)
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))

    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {"qr_token": "tok-ld8", "at": _at(READ_DAY, 18, 45).isoformat(), "adjusted_time": "18:41"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert _jst(body["actual_arrival_at"]) == "9/29 18:41"
    assert body["actual_arrival_adjusted"] is True
    assert _jst(body["actual_arrival_late_received_at"]) == "9/30 08:30"
    await db.rollback()


async def test_late_mismatch_notification_names_the_read_day(client, db, monkeypatch) -> None:
    """場所違いの通知は遅れて届いても出す。時刻は読み取った日の時刻 + 「遅れて届いた」."""
    staff, user = await _nurse(db, "LD9")
    await _admin(db, "LD9")
    visit = await _visit(db, await _patient(db, "LD9", lat=35.6, lng=140.1), staff, READ_DAY)
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))

    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {"qr_token": "tok-ld9", "at": _at(READ_DAY, 18, 45).isoformat(), "lat": 35.7, "lng": 140.3},
    )
    assert res.status_code == 200, res.text
    assert res.json()["latest_checkin"]["match_status"] == "mismatch"
    bodies = (
        await db.scalars(
            select(Notification.body).where(
                Notification.type == NOTIFY_MISMATCH, Notification.reference_id == visit.id
            )
        )
    ).all()
    assert bodies
    assert all("9/29 18:45（遅れて届いた）" in b for b in bodies)
    await db.rollback()


def test_on_time_checkin_is_not_marked_late() -> None:
    """受信が読み取りから 30 分以内・同じ日なら「遅れて届いた」にしない."""
    from app.services.checkin.actuals import late_received_at

    read = _at(READ_DAY, 13, 0)
    assert late_received_at(read, read + timedelta(minutes=30), READ_DAY) is None
    assert late_received_at(read, read + timedelta(minutes=31), READ_DAY) is not None
    # 日付をまたげば 30 分以内でも「遅れて届いた」。
    night = _at(READ_DAY, 23, 50)
    assert late_received_at(night, night + timedelta(minutes=15), READ_DAY) is not None
    assert late_received_at(None, read, READ_DAY) is None


def test_late_delivery_remarks_wording() -> None:
    received = _at(NEXT_DAY, 8, 30)
    assert late_delivery_remarks(received, None) == ["遅れて届いた（9/30 8:30 受信）"]
    assert late_delivery_remarks(None, received) == ["退出が遅れて届いた（9/30 8:30 受信）"]
    assert late_delivery_remarks(received, received + timedelta(seconds=20)) == [
        "到着・退出が遅れて届いた（9/30 8:30 受信）"
    ]
    assert late_delivery_remarks(None, None) == []


@pytest.fixture(autouse=True)
def _no_real_clock(monkeypatch) -> None:
    """どのテストも実時計に依存しない (既定は受信した朝)."""
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))


def _monitor_row(monitor, visit: Visit):
    return next(v for r in monitor.staff for v in r.visits if v.visit_id == visit.id)


async def _review(db, visit: Visit, admin: User, at: datetime) -> None:
    db.add(VisitReview(visit_id=visit.id, reviewed_by=admin.id, reviewed_at=at, comment=None))
    await db.commit()


async def _notifications(db, type_: str, visit: Visit) -> list[Notification]:
    return list(
        (
            await db.scalars(
                select(Notification).where(
                    Notification.reference_type == type_, Notification.reference_id == visit.id
                )
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# H-1: 前日以前へ遅れて届いた到着は、管理者に必ず見える
# ---------------------------------------------------------------------------


async def test_late_arrival_replaces_the_missing_notification(client, db, monkeypatch) -> None:
    """「未訪問」通知は黙って消さず、「到着が遅れて届きました」に置き換える (冪等)."""
    staff, user = await _nurse(db, "LD10")
    admin = await _admin(db, "LD10")
    visit = await _visit(db, await _patient(db, "LD10"), staff, READ_DAY)
    db.add(
        Notification(
            user_id=admin.id,
            type=NOTIFY_MISSING,
            title="未訪問",
            body="利用者 LD10",
            reference_type=NOTIFY_MISSING,
            reference_id=visit.id,
        )
    )
    await db.commit()

    payload = {"qr_token": "tok-ld10", "at": _at(READ_DAY, 18, 45).isoformat()}
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))
    assert (await _post(client, user, visit, "arrival", payload)).status_code == 200
    assert await _notifications(db, NOTIFY_MISSING, visit) == []
    late = await _notifications(db, NOTIFY_LATE_ARRIVAL, visit)
    assert [(n.user_id, n.title, n.body) for n in late] == [
        (
            admin.id,
            "前日以前の訪問に到着が遅れて届きました",
            "利用者 LD10（看護 LD10）読み取り 9/29 18:45・受信 9/30 8:30",
        )
    ]

    # 同じ読み取りの再送でも 1 通のまま。
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 40))
    assert (await _post(client, user, visit, "arrival", payload)).status_code == 200
    assert len(await _notifications(db, NOTIFY_LATE_ARRIVAL, visit)) == 1
    await db.rollback()


async def test_same_day_late_arrival_does_not_notify(client, db, monkeypatch) -> None:
    """同じ日のうちに届いた打刻 (30 分超でも) は「前日以前」の通知を出さない."""
    staff, user = await _nurse(db, "LD11")
    await _admin(db, "LD11")
    visit = await _visit(db, await _patient(db, "LD11"), staff, NEXT_DAY)
    _freeze(monkeypatch, _at(NEXT_DAY, 10, 0))
    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {"qr_token": "tok-ld11", "at": _at(NEXT_DAY, 9, 0).isoformat()},
    )
    assert res.status_code == 200, res.text
    assert res.json()["actual_arrival_late_received_at"] is not None
    assert await _notifications(db, NOTIFY_LATE_ARRIVAL, visit) == []
    await db.rollback()


async def test_monitor_late_arrival_to_an_earlier_day_needs_review(client, db, monkeypatch) -> None:
    """前日以前へ遅れて届いた到着は最低「要確認」。受信より前の「確認済み」は効かない."""
    staff, user = await _nurse(db, "LD12")
    admin = await _admin(db, "LD12")
    visit = await _visit(db, await _patient(db, "LD12", lat=35.6, lng=140.1), staff, READ_DAY)
    # 受信より前に、管理者が「確認済み」にしていた (未訪問を確認した)。
    await _review(db, visit, admin, _at(NEXT_DAY, 8, 0))
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))
    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {
            "qr_token": "tok-ld12",
            "at": _at(READ_DAY, 18, 45).isoformat(),
            "lat": 35.6,
            "lng": 140.1,
        },
    )
    assert res.status_code == 200, res.text
    row = _monitor_row(await build_monitor(db, READ_DAY, now=_at(NEXT_DAY, 9, 0)), visit)
    assert row.arrival.match_status == "match"
    assert row.alert_level == "review"
    assert row.reviewed is False

    # 受信の後に「確認済み」にすれば消える。
    await db.delete(await db.scalar(select(VisitReview).where(VisitReview.visit_id == visit.id)))
    await db.commit()
    await _review(db, visit, admin, _at(NEXT_DAY, 9, 0))
    row = _monitor_row(await build_monitor(db, READ_DAY, now=_at(NEXT_DAY, 9, 5)), visit)
    assert (row.alert_level, row.reviewed) == ("none", True)
    await db.rollback()


async def test_monitor_late_arrival_with_a_location_problem_always_needs_review(
    client, db, monkeypatch
) -> None:
    """位置なし・場所違いの到着が遅れて届いたら、先の「確認済み」があっても要対応に戻す."""
    staff, user = await _nurse(db, "LD13")
    admin = await _admin(db, "LD13")
    patient = await _patient(db, "LD13", lat=35.6, lng=140.1)
    no_gps = await _visit(db, patient, staff, NEXT_DAY, start=time(8, 0), end=time(8, 30))
    far = await _visit(db, patient, staff, NEXT_DAY, start=time(9, 0), end=time(9, 30))
    for visit in (no_gps, far):
        await _review(db, visit, admin, _at(NEXT_DAY, 9, 50))
    _freeze(monkeypatch, _at(NEXT_DAY, 10, 0))
    res = await _post(
        client,
        user,
        no_gps,
        "arrival",
        {"qr_token": "tok-ld13", "at": _at(NEXT_DAY, 8, 5).isoformat()},
    )
    assert res.status_code == 200, res.text
    res = await _post(
        client,
        user,
        far,
        "arrival",
        {"qr_token": "tok-ld13", "at": _at(NEXT_DAY, 9, 5).isoformat(), "lat": 35.7, "lng": 140.3},
    )
    assert res.status_code == 200, res.text
    monitor = await build_monitor(db, NEXT_DAY, now=_at(NEXT_DAY, 10, 5))
    row = _monitor_row(monitor, no_gps)
    assert (row.arrival.match_status, row.alert_level, row.reviewed) == ("no_gps", "review", False)
    row = _monitor_row(monitor, far)
    assert (row.arrival.match_status, row.alert_level, row.reviewed) == (
        "mismatch",
        "mismatch",
        False,
    )
    await db.rollback()


# ---------------------------------------------------------------------------
# M-1: 断るときの code
# ---------------------------------------------------------------------------


async def test_rejections_carry_a_code(client, db, monkeypatch) -> None:
    staff, user = await _nurse(db, "LD14")
    patient = await _patient(db, "LD14")
    await _patient(db, "LD14B")
    visit = await _visit(db, patient, staff, NEXT_DAY)
    cancelled = await _visit(db, patient, staff, NEXT_DAY, status="cancelled")
    deleted = await _visit(db, patient, staff, NEXT_DAY, deleted_at=_at(NEXT_DAY, 7, 0))
    _freeze(monkeypatch, _at(NEXT_DAY, 10, 0))

    res = await _post(client, user, visit, "arrival", {"qr_token": "tok-ld14b"})
    assert res.status_code == 409, res.text
    assert res.json() == {
        "detail": "QR does not match this visit's patient",
        "code": "wrong_patient",
    }
    res = await _post(client, user, cancelled, "arrival", {"qr_token": "tok-ld14"})
    assert res.json() == {"detail": "Visit is cancelled", "code": "cancelled"}
    res = await _post(client, user, deleted, "arrival", {"qr_token": "tok-ld14"})
    assert res.json() == {"detail": "Visit is deleted", "code": "deleted"}
    # 予定外の期限切れも同じ code (422)。
    res = await client.post(
        "/api/v1/visits/adhoc-checkin",
        headers=_bearer(user),
        json={
            "qr_token": "tok-ld14",
            "at": (_at(NEXT_DAY, 10, 0) - timedelta(hours=73)).isoformat(),
        },
    )
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "late_expired"
    await db.rollback()


# ---------------------------------------------------------------------------
# M-2: 予定外の到着の再送は、完了した後に届いても 2 本目を作らない
# ---------------------------------------------------------------------------


async def test_adhoc_resend_after_completion_is_idempotent(client, db, monkeypatch) -> None:
    """応答が消えた → 退出で完了 → 同じ到着が再送 → 同じ訪問を返し、何も足さない."""
    staff, user = await _nurse(db, "LD15")
    patient = await _patient(db, "LD15")
    payload = {"qr_token": "tok-ld15", "at": _at(NEXT_DAY, 9, 0).isoformat()}
    _freeze(monkeypatch, _at(NEXT_DAY, 9, 0, 5))
    first = await client.post("/api/v1/visits/adhoc-checkin", headers=_bearer(user), json=payload)
    assert first.status_code == 200, first.text
    visit = await db.scalar(select(Visit).where(Visit.patient_id == patient.id))

    _freeze(monkeypatch, _at(NEXT_DAY, 9, 40))
    res = await _post(
        client,
        user,
        visit,
        "departure",
        {"qr_token": "tok-ld15", "at": _at(NEXT_DAY, 9, 40).isoformat()},
    )
    assert res.json()["status"] == "completed"
    count = await _checkin_count(db, visit.id)

    _freeze(monkeypatch, _at(NEXT_DAY, 9, 45))
    again = await client.post("/api/v1/visits/adhoc-checkin", headers=_bearer(user), json=payload)
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["status"] == "completed"
    assert len((await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()) == 1
    assert await _checkin_count(db, visit.id) == count
    await db.rollback()


# ---------------------------------------------------------------------------
# M-4 / L-6: 読み取った瞬間で比べる
# ---------------------------------------------------------------------------


async def test_adjustment_survives_a_read_that_arrives_after_it(client, db, monkeypatch) -> None:
    """読んだのが調整より前なら、調整の後に届いても調整は消えない (読取は残る)."""
    staff, user = await _nurse(db, "LD16")
    visit = await _visit(db, await _patient(db, "LD16"), staff, READ_DAY)
    _freeze(monkeypatch, _at(READ_DAY, 18, 30))
    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {"qr_token": "tok-ld16", "at": _at(READ_DAY, 18, 30).isoformat()},
    )
    assert res.status_code == 200, res.text
    # 翌朝、退出を手で入れた (読み取りは圏外で端末に残っていた)。
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 0))
    res = await client.put(
        f"/api/v1/visits/{visit.id}/actual-time",
        headers=_bearer(user),
        json={"kind": "departure", "time": "19:20", "reason_code": None, "reason_text": None},
    )
    assert res.status_code == 200, res.text
    assert res.json()["actual_departure_manual"] is True

    # 19:30 に読んだ退出が 8:30 に届く。
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))
    res = await _post(
        client,
        user,
        visit,
        "departure",
        {"qr_token": "tok-ld16", "at": _at(READ_DAY, 19, 30).isoformat()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert _jst(body["actual_departure_at"]) == "9/29 19:20"
    assert body["actual_departure_adjusted"] is True
    assert _jst(body["actual_departure_read_at"]) == "9/29 19:30"
    assert _jst(body["actual_departure_late_received_at"]) == "9/30 08:30"
    assert body["status"] == "completed"
    await db.rollback()


async def test_older_queued_read_does_not_override_a_newer_online_read(
    client, db, monkeypatch
) -> None:
    staff, user = await _nurse(db, "LD17")
    visit = await _visit(db, await _patient(db, "LD17"), staff, NEXT_DAY)
    _freeze(monkeypatch, _at(NEXT_DAY, 10, 5))
    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {"qr_token": "tok-ld17", "at": _at(NEXT_DAY, 10, 5).isoformat()},
    )
    assert res.status_code == 200, res.text
    _freeze(monkeypatch, _at(NEXT_DAY, 10, 30))
    res = await _post(
        client,
        user,
        visit,
        "arrival",
        {"qr_token": "tok-ld17", "at": _at(NEXT_DAY, 9, 50).isoformat()},
    )
    assert res.status_code == 200, res.text
    assert _jst(res.json()["actual_arrival_at"]) == "9/30 10:05"
    assert res.json()["actual_arrival_late_received_at"] is None
    await db.rollback()


# ---------------------------------------------------------------------------
# L-7: 境界
# ---------------------------------------------------------------------------


async def test_device_clock_tolerance_boundary(client, db, monkeypatch) -> None:
    """端末の時計が 120 秒進んでいるまでは読取時刻に採り、121 秒は採らない."""
    staff, user = await _nurse(db, "LD18")
    patient = await _patient(db, "LD18")
    ok = await _visit(db, patient, staff, NEXT_DAY, start=time(10, 0), end=time(10, 30))
    ahead = await _visit(db, patient, staff, NEXT_DAY, start=time(11, 0), end=time(11, 30))
    now = _at(NEXT_DAY, 10, 0)
    _freeze(monkeypatch, now)
    res = await _post(
        client,
        user,
        ok,
        "arrival",
        {"qr_token": "tok-ld18", "at": (now + timedelta(seconds=120)).isoformat()},
    )
    assert _jst(res.json()["actual_arrival_at"]) == "9/30 10:02"
    res = await _post(
        client,
        user,
        ahead,
        "arrival",
        {"qr_token": "tok-ld18", "at": (now + timedelta(seconds=121)).isoformat()},
    )
    assert _jst(res.json()["actual_arrival_at"]) == "9/30 10:00"
    await db.rollback()


async def test_midnight_boundary(client, db, monkeypatch) -> None:
    """23:59:59 に読み 0:00:00 に届く → 前日の訪問・遅れて届いた。0:00:00 は今日の訪問."""
    staff, user = await _nurse(db, "LD19")
    patient = await _patient(db, "LD19")
    yesterday = await _visit(db, patient, staff, READ_DAY, start=time(23, 30), end=time(23, 59))
    today = await _visit(db, patient, staff, NEXT_DAY, start=time(0, 0), end=time(0, 30))
    _freeze(monkeypatch, _at(NEXT_DAY, 0, 0, 0))
    res = await _post(
        client,
        user,
        yesterday,
        "arrival",
        {"qr_token": "tok-ld19", "at": _at(READ_DAY, 23, 59, 59).isoformat()},
    )
    assert res.status_code == 200, res.text
    assert _jst(res.json()["actual_arrival_late_received_at"]) == "9/30 00:00"

    _freeze(monkeypatch, _at(NEXT_DAY, 0, 0, 1))
    res = await _post(
        client,
        user,
        today,
        "arrival",
        {"qr_token": "tok-ld19", "at": _at(NEXT_DAY, 0, 0, 0).isoformat()},
    )
    assert res.status_code == 200, res.text
    assert res.json()["actual_arrival_late_received_at"] is None
    # 0:00:00 に読んだものは前日の訪問には付かない。
    res = await _post(
        client,
        user,
        yesterday,
        "departure",
        {"qr_token": "tok-ld19", "at": _at(NEXT_DAY, 0, 0, 0).isoformat()},
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "not_visit_day"
    await db.rollback()


# ---------------------------------------------------------------------------
# M-5 / L-5: 打刻履歴の絞り込みと未訪問の記録
# ---------------------------------------------------------------------------


async def test_history_late_filter_and_late_no_show(client, db, monkeypatch) -> None:
    staff, user = await _nurse(db, "LD20")
    admin = await _admin(db, "LD20")
    patient = await _patient(db, "LD20")
    late = await _visit(db, patient, staff, READ_DAY, start=time(18, 30), end=time(19, 0))
    no_show = await _visit(db, patient, staff, READ_DAY, start=time(19, 30), end=time(20, 0))
    on_time = await _visit(db, patient, staff, READ_DAY, start=time(10, 0), end=time(10, 30))
    _freeze(monkeypatch, _at(READ_DAY, 10, 5))
    assert (
        await _post(
            client,
            user,
            on_time,
            "arrival",
            {"qr_token": "tok-ld20", "at": _at(READ_DAY, 10, 5).isoformat()},
        )
    ).status_code == 200
    _freeze(monkeypatch, _at(NEXT_DAY, 8, 30))
    assert (
        await _post(
            client,
            user,
            late,
            "arrival",
            {"qr_token": "tok-ld20", "at": _at(READ_DAY, 18, 45).isoformat()},
        )
    ).status_code == 200
    res = await client.post(
        f"/api/v1/visits/{no_show.id}/no-show",
        headers=_bearer(user),
        json={"reason": "不在", "at": _at(READ_DAY, 19, 40).isoformat()},
    )
    assert res.status_code == 200, res.text

    params = {"from": READ_DAY.isoformat(), "to": READ_DAY.isoformat(), "state": "late"}
    res = await client.get("/api/v1/visit-history", headers=_bearer(admin), params=params)
    assert res.status_code == 200, res.text
    items = {i["visit_id"]: i for i in res.json()["items"]}
    assert set(items) == {str(late.id), str(no_show.id)}
    assert "未訪問の記録が遅れて届いた（9/30 8:30 受信）" in items[str(no_show.id)]["remarks"]
    # Excel・A4 も同じ絞り込みで出る。
    res = await client.get("/api/v1/visit-history/export", headers=_bearer(admin), params=params)
    assert res.status_code == 200, res.text
    sheet = load_workbook(BytesIO(res.content))["全予定"]
    cells = [str(c) for row in sheet.iter_rows(values_only=True) for c in row if c]
    assert any("未訪問の記録が遅れて届いた" in c for c in cells)
    assert not any("10:05" in c for c in cells)
    res = await client.get("/api/v1/visit-history/report", headers=_bearer(admin), params=params)
    assert res.status_code == 200, res.text
    # A4 は既定で到着のある行だけ (未訪問の行は Excel の「全予定」で見る)。
    assert "遅れて届いた（9/30 8:30 受信）" in res.text
    assert "10:05" not in res.text
    await db.rollback()
