"""固定イベントの一括登録 + 休み連携 (staff-event-history-design.md §2 Phase 3).

正典 = docs/plans/staff-event-history-design.md §2 Phase 3 /
       docs/mockups/event-defaults-bulk-mock.html (変更A / 変更C)。

検証観点:
  1. 一括登録 API (POST /api/v1/staff-event-defaults/bulk)
     - staff_ids × weekdays の全組を作る (created の実数)
     - 同一内容の既定は作らない (skipped の実数・2 回押しても増えない)
     - 422: 存在しない / 休職スタッフ混在 (全体棄却) / end <= start /
            weekday 6 / 空配列
     - RBAC: staff ロールは 403 / 未認証は 401
  2. 展開の休みスキップ (expand_staff_event_defaults)
     - 週間シフト is_on=False の曜日は展開しない
     - 当該週の休み override ('off') の曜日は展開しない
     - 時間変更 (custom_time) や別週の休みは従来どおり展開する
     - シフト行が 1 件も無いスタッフは従来どおり展開する (既存挙動を壊さない)
  3. 「🛌 休みにする」(staff-off-week) の自動不参加
     - その日の source='fixed' イベントに取消印が付く (行は消えない)
     - manual / kaipoke / 別日 / 別スタッフのイベントには触れない
     - undo で取消印が外れる (同一 op_group) / redo で再び付く
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.staff import (
    Staff,
    StaffEvent,
    StaffEventDefault,
    StaffShift,
    StaffWeeklyOverride,
)
from app.models.user import User
from app.services.staff_event_defaults import expand_staff_event_defaults

_BULK_URL = "/api/v1/staff-event-defaults/bulk"
_OFF_URL = "/api/v1/schedule/v2/staff-off-week"
_UNDO_URL = "/api/v1/schedule/v2/op-log/undo"
_REDO_URL = "/api/v1/schedule/v2/op-log/redo"

ISO_YEAR, ISO_WEEK = 2026, 37  # 2026-09-07(月) の週
MONDAY = date(2026, 9, 7)


def _today_jst() -> date:
    """エンドポイントの過去日ガードと同じ基準 (JST) の今日."""
    return datetime.now(UTC).astimezone(ZoneInfo("Asia/Tokyo")).date()


def _future_date(weekday: int = 0) -> date:
    """常に未来の、指定曜日の日を返す (過去日ガードに掛からない)."""
    base = _today_jst() + timedelta(days=8)
    return base + timedelta(days=(weekday - base.weekday()) % 7)


async def _make_user(db, *, email: str, role: str = "admin", staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_staff(db, *, name: str, status: str = "active") -> Staff:
    s = Staff(name=name, status=status)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_default(
    db,
    staff: Staff,
    *,
    weekday: int,
    start: time = time(9, 0),
    end: time = time(9, 15),
    title: str = "朝会",
) -> StaffEventDefault:
    row = StaffEventDefault(
        staff_id=staff.id, weekday=weekday, start_time=start, end_time=end, title=title
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _make_event(
    db,
    staff: Staff,
    *,
    on: date,
    source: str,
    title: str = "朝会",
    start_h: int = 9,
    external_id: str | None = None,
) -> StaffEvent:
    row = StaffEvent(
        staff_id=staff.id,
        event_type="event",
        starts_at=datetime.combine(on, time(start_h, 0)),
        ends_at=datetime.combine(on, time(start_h, 15)),
        title=title,
        source=source,
        external_id=external_id or f"{uuid4()}",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _defaults_of(db, staff: Staff) -> list[StaffEventDefault]:
    rows = await db.scalars(select(StaffEventDefault).where(StaffEventDefault.staff_id == staff.id))
    return list(rows.all())


def _payload(staff_ids, weekdays, **over) -> dict:
    body = {
        "staff_ids": [str(s) for s in staff_ids],
        "weekdays": list(weekdays),
        "start_time": "09:00",
        "end_time": "09:15",
        "title": "朝会",
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# 1) 一括登録 API
# ---------------------------------------------------------------------------


async def test_bulk_creates_all_combinations(client, db) -> None:
    """N名 × N曜日 の全組を 1 回で作る (モック 変更A のプレビュー件数と一致)."""
    admin = await _make_user(db, email="bulk-1@example.com")
    a = await _make_staff(db, name="川名 千恵")
    b = await _make_staff(db, name="熊澤 妙子")

    res = await client.post(
        _BULK_URL, headers=_bearer(admin), json=_payload([a.id, b.id], [0, 1, 2])
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"created": 6, "skipped": 0}

    rows = await _defaults_of(db, a)
    assert sorted(r.weekday for r in rows) == [0, 1, 2]
    assert {r.title for r in rows} == {"朝会"}
    assert rows[0].start_time == time(9, 0)
    assert rows[0].end_time == time(9, 15)
    assert rows[0].blocking is False
    assert len(await _defaults_of(db, b)) == 3


async def test_bulk_skips_duplicates(client, db) -> None:
    """同一 (staff, 曜日, 開始, 終了, タイトル) は作らない — 2 回押しても増えない."""
    admin = await _make_user(db, email="bulk-2@example.com")
    a = await _make_staff(db, name="高岡 真由美")
    # 1 件だけ手動で先に登録しておく (タイトルは前後空白付きでも同一とみなす)
    await _make_default(db, a, weekday=1, title=" 朝会 ".strip())

    res = await client.post(_BULK_URL, headers=_bearer(admin), json=_payload([a.id], [0, 1, 2]))
    assert res.status_code == 200, res.text
    assert res.json() == {"created": 2, "skipped": 1}

    # 2 回目は全部スキップ
    res = await client.post(_BULK_URL, headers=_bearer(admin), json=_payload([a.id], [0, 1, 2]))
    assert res.json() == {"created": 0, "skipped": 3}
    assert len(await _defaults_of(db, a)) == 3

    # 時刻が違えば別物として作る (重複判定は 5 要素の完全一致)
    res = await client.post(
        _BULK_URL,
        headers=_bearer(admin),
        json=_payload([a.id], [0], start_time="13:00", end_time="14:00"),
    )
    assert res.json() == {"created": 1, "skipped": 0}
    assert len(await _defaults_of(db, a)) == 4


async def test_bulk_dedupes_input_and_accepts_blocking_note(client, db) -> None:
    """入力の重複 (同じ staff / 曜日を 2 回) は 1 件に畳む。blocking / note も通る."""
    admin = await _make_user(db, email="bulk-3@example.com")
    a = await _make_staff(db, name="本名 大")

    res = await client.post(
        _BULK_URL,
        headers=_bearer(admin),
        json=_payload(
            [a.id, a.id],
            [2, 2, 0],
            blocking=True,
            note="全体朝礼",
            title="  カンファレンス  ",
            start_time="13:00",
            end_time="14:00",
        ),
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"created": 2, "skipped": 0}
    rows = await _defaults_of(db, a)
    assert sorted(r.weekday for r in rows) == [0, 2]
    assert {r.title for r in rows} == {"カンファレンス"}  # trim される
    assert all(r.blocking is True and r.note == "全体朝礼" for r in rows)


async def test_bulk_rejects_invalid_staff_all_or_nothing(client, db) -> None:
    """存在しない / 休職スタッフが 1 件でも混ざれば 422 で全体棄却 (半分登録しない)."""
    admin = await _make_user(db, email="bulk-4@example.com")
    ok = await _make_staff(db, name="宇田川 優莉")
    gone = await _make_staff(db, name="退職 太郎", status="retired")

    res = await client.post(_BULK_URL, headers=_bearer(admin), json=_payload([ok.id, gone.id], [0]))
    assert res.status_code == 422, res.text
    assert "退職 太郎" in res.json()["detail"]
    assert await _defaults_of(db, ok) == []

    res = await client.post(_BULK_URL, headers=_bearer(admin), json=_payload([ok.id, uuid4()], [0]))
    assert res.status_code == 422
    assert await _defaults_of(db, ok) == []


async def test_bulk_validation_errors(client, db) -> None:
    admin = await _make_user(db, email="bulk-5@example.com")
    a = await _make_staff(db, name="髙梨 桂子")

    # end <= start は 422 (逆転も同時刻も不可)
    for start, end in (("10:00", "09:00"), ("09:00", "09:00")):
        res = await client.post(
            _BULK_URL,
            headers=_bearer(admin),
            json=_payload([a.id], [0], start_time=start, end_time=end),
        )
        assert res.status_code == 422, (start, end, res.text)

    # 日曜 (6) は定義不可
    res = await client.post(_BULK_URL, headers=_bearer(admin), json=_payload([a.id], [6]))
    assert res.status_code == 422
    # 空配列は不可
    assert (
        await client.post(_BULK_URL, headers=_bearer(admin), json=_payload([a.id], []))
    ).status_code == 422
    assert (
        await client.post(_BULK_URL, headers=_bearer(admin), json=_payload([], [0]))
    ).status_code == 422
    # 空タイトルは不可
    res = await client.post(
        _BULK_URL, headers=_bearer(admin), json=_payload([a.id], [0], title="  ")
    )
    assert res.status_code == 422
    assert await _defaults_of(db, a) == []


async def test_bulk_rbac(client, db) -> None:
    a = await _make_staff(db, name="小西 彩稀")
    member = await _make_user(db, email="bulk-6@example.com", role="staff", staff_id=a.id)

    res = await client.post(_BULK_URL, headers=_bearer(member), json=_payload([a.id], [0]))
    assert res.status_code == 403, res.text
    res = await client.post(_BULK_URL, json=_payload([a.id], [0]))
    assert res.status_code == 401
    assert await _defaults_of(db, a) == []


# ---------------------------------------------------------------------------
# 2) 展開の休みスキップ
# ---------------------------------------------------------------------------


async def _expand(db) -> int:
    created = await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    return created


async def test_expand_skips_shift_off_weekday(client, db) -> None:
    """週間シフトで is_on=False の曜日には展開しない。他の曜日は展開する."""
    a = await _make_staff(db, name="シフト 休子")
    await _make_default(db, a, weekday=2)  # 水
    await _make_default(db, a, weekday=3)  # 木
    db.add(StaffShift(staff_id=a.id, weekday=2, is_on=False))
    db.add(StaffShift(staff_id=a.id, weekday=3, is_on=True))
    await db.commit()

    assert await _expand(db) == 1
    rows = list((await db.scalars(select(StaffEvent).where(StaffEvent.source == "fixed"))).all())
    assert len(rows) == 1
    assert rows[0].starts_at == datetime(2026, 9, 10, 9, 0)  # 木曜のみ


async def test_expand_skips_week_off_override(client, db) -> None:
    """当該週の休み override ('off') の曜日には展開しない."""
    a = await _make_staff(db, name="今週 休美")
    await _make_default(db, a, weekday=1)
    db.add(
        StaffWeeklyOverride(
            staff_id=a.id,
            iso_year=ISO_YEAR,
            iso_week=ISO_WEEK,
            weekday=1,
            override_type="off",
        )
    )
    await db.commit()
    assert await _expand(db) == 0


async def test_expand_ignores_custom_time_and_other_week_off(client, db) -> None:
    """時間変更 (custom_time) と **別週** の休みは展開を止めない."""
    a = await _make_staff(db, name="時間 変子")
    await _make_default(db, a, weekday=1)
    db.add(
        StaffWeeklyOverride(
            staff_id=a.id,
            iso_year=ISO_YEAR,
            iso_week=ISO_WEEK,
            weekday=1,
            override_type="custom_time",
            start_time=time(10, 0),
            end_time=time(16, 0),
        )
    )
    db.add(
        StaffWeeklyOverride(
            staff_id=a.id,
            iso_year=ISO_YEAR,
            iso_week=ISO_WEEK - 1,
            weekday=1,
            override_type="off",
        )
    )
    await db.commit()
    assert await _expand(db) == 1


async def test_expand_without_shift_rows_still_expands(client, db) -> None:
    """シフト行が 1 件も無いスタッフは従来どおり展開する (既存挙動の保護)."""
    a = await _make_staff(db, name="シフト 未登録")
    await _make_default(db, a, weekday=0)
    assert await _expand(db) == 1


# ---------------------------------------------------------------------------
# 3) 「🛌 休みにする」の自動不参加
# ---------------------------------------------------------------------------


async def test_staff_off_week_cancels_fixed_events(client, db) -> None:
    """休みにした日の固定イベントに取消印が付く。他 source / 他日 / 他人は不変."""
    admin = await _make_user(db, email="off-ev-1@example.com")
    a = await _make_staff(db, name="休む 花子")
    other = await _make_staff(db, name="別人 次郎")
    target = _future_date()

    fixed = await _make_event(db, a, on=target, source="fixed")
    manual = await _make_event(db, a, on=target, source="manual", title="面談", start_h=13)
    kaipoke = await _make_event(db, a, on=target, source="kaipoke", title="取込", start_h=15)
    next_day = await _make_event(db, a, on=target + timedelta(days=1), source="fixed")
    other_fixed = await _make_event(db, other, on=target, source="fixed")

    res = await client.post(
        _OFF_URL,
        headers=_bearer(admin),
        json={"staff_id": str(a.id), "date": target.isoformat(), "to_staff_id": None},
    )
    assert res.status_code == 200, res.text

    for row in (fixed, manual, kaipoke, next_day, other_fixed):
        await db.refresh(row)
    assert fixed.cancelled_at is not None  # 取消印
    assert manual.cancelled_at is None  # 手入力は触らない
    assert kaipoke.cancelled_at is None  # 取込も触らない
    assert next_day.cancelled_at is None  # 別日は触らない
    assert other_fixed.cancelled_at is None  # 別スタッフは触らない
    # 行は消えていない (冪等キーを空けない)
    total = list((await db.scalars(select(StaffEvent))).all())
    assert len(total) == 5


async def test_staff_off_week_cancel_is_undone_and_redone(client, db) -> None:
    """undo で取消印が外れ (休みと同一 op_group)、redo でまた付く."""
    admin = await _make_user(db, email="off-ev-2@example.com")
    a = await _make_staff(db, name="戻す 三郎")
    target = _future_date()
    iso = target.isocalendar()
    fixed = await _make_event(db, a, on=target, source="fixed")

    res = await client.post(
        _OFF_URL,
        headers=_bearer(admin),
        json={"staff_id": str(a.id), "date": target.isoformat(), "to_staff_id": None},
    )
    assert res.status_code == 200, res.text
    await db.refresh(fixed)
    assert fixed.cancelled_at is not None

    undo = await client.post(
        _UNDO_URL, headers=_bearer(admin), json={"iso_year": iso.year, "iso_week": iso.week}
    )
    assert undo.status_code == 200, undo.text
    await db.refresh(fixed)
    assert fixed.cancelled_at is None
    # 休み本体も一緒に戻る (同一 op_group の従来挙動を壊していない)
    assert (
        await db.scalar(select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == a.id))
    ) is None

    redo = await client.post(
        _REDO_URL, headers=_bearer(admin), json={"iso_year": iso.year, "iso_week": iso.week}
    )
    assert redo.status_code == 200, redo.text
    await db.refresh(fixed)
    assert fixed.cancelled_at is not None


async def test_staff_off_week_skips_already_cancelled_event(client, db) -> None:
    """既に「今週だけ外す」で取消済みの行は触らない (undo で誤って復活させない)."""
    admin = await _make_user(db, email="off-ev-3@example.com")
    a = await _make_staff(db, name="既取消 四郎")
    target = _future_date()
    iso = target.isocalendar()
    fixed = await _make_event(db, a, on=target, source="fixed")
    fixed.cancelled_at = datetime(2026, 1, 1, tzinfo=UTC)
    await db.commit()

    res = await client.post(
        _OFF_URL,
        headers=_bearer(admin),
        json={"staff_id": str(a.id), "date": target.isoformat(), "to_staff_id": None},
    )
    assert res.status_code == 200, res.text

    undo = await client.post(
        _UNDO_URL, headers=_bearer(admin), json={"iso_year": iso.year, "iso_week": iso.week}
    )
    assert undo.status_code == 200, undo.text
    await db.refresh(fixed)
    assert fixed.cancelled_at is not None  # 手動の取消印は残ったまま
