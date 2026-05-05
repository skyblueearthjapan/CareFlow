"""Tests for /api/v1/dashboard endpoints (Phase 7 — W2-D).

Covers the critic-flagged regressions:
* admin sees every visit, staff sees only their own (primary / secondary /
  mentor slots);
* staff without a linked staff_id receives well-formed zero aggregates;
* `today_overlapping` treats adjacent slots (start_j == end_i) as
  non-overlapping and excludes cancelled visits;
* `this_week_completion_rate` is 0.0 when there are no visits today (i.e.
  zero-division guard) — and more generally when the week is empty;
* week aggregate excludes cancelled visits (does not pad totals or drag
  completion rate);
* `/trend` back-fills empty days with zero rows;
* `/trend` honours staff scope across primary/secondary/mentor slots;
* `/trend` returns a well-formed empty series for staff without a linked
  staff_id;
* `/trend` excludes cancelled visits from totals;
* `/trend` "end" date is computed in JST (so the window is JST-anchored);
* `days=0` and `days=91` are rejected with 422;
* "today" is computed in JST so a request issued at UTC 2026-05-04T16:00
  (= JST 2026-05-05T01:00) returns date(2026, 5, 5).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit


# ---------- helpers --------------------------------------------------------

async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
        staff_id=staff_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff(db, name: str = "テストスタッフ") -> Staff:
    staff = Staff(name=name)
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def _make_patient(db, code: str = "DASH-P1") -> Patient:
    patient = Patient(code=code, name="患者")
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _today_jst() -> date:
    """Match the production handler's notion of "today" so tests are stable
    regardless of the runner's local clock when the day has not crossed in
    JST. (The dedicated TZ test below freezes the clock.)"""
    from app.api.v1.dashboard import JST  # local import to avoid cycle

    return datetime.now(JST).date()


async def _add_visit(
    db,
    *,
    patient_id,
    visit_date: date,
    start: time,
    end: time,
    status: str = "planned",
    primary_staff_id=None,
    secondary_staff_id=None,
    mentor_staff_id=None,
) -> Visit:
    v = Visit(
        patient_id=patient_id,
        primary_staff_id=primary_staff_id,
        secondary_staff_id=secondary_staff_id,
        mentor_staff_id=mentor_staff_id,
        visit_date=visit_date,
        start_time=start,
        end_time=end,
        type="visit",
        status=status,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


# ---------- /kpi -----------------------------------------------------------


@pytest.mark.asyncio
async def test_kpi_admin_sees_all_visits(client, db) -> None:
    today = _today_jst()
    patient = await _make_patient(db, "DASH-A")
    staff_a = await _make_staff(db, "Aさん")
    staff_b = await _make_staff(db, "Bさん")
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=staff_a.id,
    )
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(11, 0), end=time(12, 0),
        primary_staff_id=staff_b.id, status="completed",
    )

    admin = await _make_user(db, "dash-admin@example.com", "admin")
    res = await client.get("/api/v1/dashboard/kpi", headers=_bearer(admin))

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["today_visits"] == 2
    assert body["today_completed"] == 1
    assert body["today_unassigned"] == 0
    assert body["today_overlapping"] == 0


@pytest.mark.asyncio
async def test_kpi_staff_sees_only_own_across_three_slots(client, db) -> None:
    today = _today_jst()
    patient = await _make_patient(db, "DASH-B")
    me = await _make_staff(db, "本人")
    other = await _make_staff(db, "他人")

    # Mine — primary
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=me.id,
    )
    # Mine — secondary (同行)
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(10, 30), end=time(11, 30),
        primary_staff_id=other.id, secondary_staff_id=me.id,
    )
    # Mine — mentor
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(13, 0), end=time(14, 0),
        primary_staff_id=other.id, mentor_staff_id=me.id,
        status="completed",
    )
    # Not mine
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(15, 0), end=time(16, 0),
        primary_staff_id=other.id,
    )

    staff_user = await _make_user(
        db, "dash-staff@example.com", "staff", staff_id=me.id
    )
    res = await client.get("/api/v1/dashboard/kpi", headers=_bearer(staff_user))

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["today_visits"] == 3
    assert body["today_completed"] == 1
    # one of my visits has no primary_staff (I'm in secondary slot but the
    # primary is `other`), so unassigned remains 0 here.
    assert body["today_unassigned"] == 0


@pytest.mark.asyncio
async def test_kpi_staff_without_staff_id_returns_zeros(client, db) -> None:
    today = _today_jst()
    patient = await _make_patient(db, "DASH-C")
    other = await _make_staff(db, "他人")
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=other.id,
    )

    orphan = await _make_user(db, "dash-orphan@example.com", "staff", staff_id=None)
    res = await client.get("/api/v1/dashboard/kpi", headers=_bearer(orphan))

    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {
        "today_visits": 0,
        "today_completed": 0,
        "today_unassigned": 0,
        "today_overlapping": 0,
        "this_week_visits": 0,
        "this_week_completion_rate": 0.0,
    }


@pytest.mark.asyncio
async def test_kpi_overlap_treats_adjacent_slots_as_non_overlapping(client, db) -> None:
    today = _today_jst()
    patient = await _make_patient(db, "DASH-D")
    staff = await _make_staff(db, "連続スタッフ")

    # Two visits that share a boundary (10:00) — must NOT count as overlap.
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=staff.id,
    )
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(10, 0), end=time(11, 0),
        primary_staff_id=staff.id,
    )

    admin = await _make_user(db, "dash-admin-d@example.com", "admin")
    res = await client.get("/api/v1/dashboard/kpi", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["today_overlapping"] == 0


@pytest.mark.asyncio
async def test_kpi_overlap_excludes_cancelled(client, db) -> None:
    today = _today_jst()
    patient = await _make_patient(db, "DASH-E")
    staff = await _make_staff(db, "キャンセル混在")

    # 9:00-10:00 active + 9:30-10:30 cancelled. Without the filter the active
    # one would flip to overlapping; with the filter overlap=0.
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=staff.id,
    )
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 30), end=time(10, 30),
        primary_staff_id=staff.id, status="cancelled",
    )

    admin = await _make_user(db, "dash-admin-e@example.com", "admin")
    res = await client.get("/api/v1/dashboard/kpi", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["today_overlapping"] == 0
    # Cancelled visit must also not be counted in today_visits.
    assert body["today_visits"] == 1


@pytest.mark.asyncio
async def test_kpi_completion_rate_is_zero_when_no_visits(client, db) -> None:
    # Pristine DB — no visits at all this ISO week.
    admin = await _make_user(db, "dash-empty@example.com", "admin")
    res = await client.get("/api/v1/dashboard/kpi", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["today_visits"] == 0
    assert body["this_week_visits"] == 0
    assert body["this_week_completion_rate"] == 0.0


# ---------- /trend ---------------------------------------------------------


@pytest.mark.asyncio
async def test_trend_backfills_empty_days_with_zero_rows(client, db) -> None:
    today = _today_jst()
    patient = await _make_patient(db, "DASH-T")
    staff = await _make_staff(db, "Tスタッフ")

    # Only seed a single day in a 7-day window; the other six must be zero.
    seeded = today - timedelta(days=2)
    await _add_visit(
        db, patient_id=patient.id, visit_date=seeded,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=staff.id, status="completed",
    )

    admin = await _make_user(db, "dash-admin-t@example.com", "admin")
    res = await client.get(
        "/api/v1/dashboard/trend?days=7", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["days"] == 7
    assert len(body["items"]) == 7
    # Oldest → newest, contiguous.
    dates = [item["date"] for item in body["items"]]
    expected = [
        (today - timedelta(days=6 - i)).isoformat() for i in range(7)
    ]
    assert dates == expected

    seeded_iso = seeded.isoformat()
    for item in body["items"]:
        if item["date"] == seeded_iso:
            assert item["total"] == 1
            assert item["completed"] == 1
        else:
            assert item["total"] == 0
            assert item["completed"] == 0
            assert item["unassigned"] == 0


@pytest.mark.asyncio
async def test_trend_rejects_days_zero(client, db) -> None:
    admin = await _make_user(db, "dash-admin-422a@example.com", "admin")
    res = await client.get(
        "/api/v1/dashboard/trend?days=0", headers=_bearer(admin)
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_trend_rejects_days_above_max(client, db) -> None:
    admin = await _make_user(db, "dash-admin-422b@example.com", "admin")
    res = await client.get(
        "/api/v1/dashboard/trend?days=91", headers=_bearer(admin)
    )
    assert res.status_code == 422, res.text


# ---------- timezone -------------------------------------------------------


@pytest.mark.asyncio
async def test_kpi_today_is_resolved_in_jst(client, db, monkeypatch) -> None:
    """At UTC 2026-05-04T16:00 (= JST 2026-05-05T01:00), KPI must roll up
    visits whose `visit_date == 2026-05-05`, not 2026-05-04."""
    import app.api.v1.dashboard as dashboard_mod

    frozen_utc = datetime(2026, 5, 4, 16, 0, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_utc.replace(tzinfo=None)
            return frozen_utc.astimezone(tz)

    monkeypatch.setattr(dashboard_mod, "datetime", _FrozenDatetime)

    patient = await _make_patient(db, "DASH-TZ")
    staff = await _make_staff(db, "TZスタッフ")
    # Visit on the JST "today" (2026-05-05) — must show up.
    await _add_visit(
        db, patient_id=patient.id, visit_date=date(2026, 5, 5),
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=staff.id,
    )
    # Visit on UTC "today" (2026-05-04) — must NOT show up in KPI.
    await _add_visit(
        db, patient_id=patient.id, visit_date=date(2026, 5, 4),
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=staff.id, status="completed",
    )

    admin = await _make_user(db, "dash-tz@example.com", "admin")
    res = await client.get("/api/v1/dashboard/kpi", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["today_visits"] == 1
    assert body["today_completed"] == 0  # the completed visit is on 5-04


# ---------- /kpi week math (cancelled excluded) ---------------------------


@pytest.mark.asyncio
async def test_kpi_week_completion_excludes_cancelled(client, db) -> None:
    """Cancelled visits must NOT count toward `this_week_visits` or drag the
    completion rate down. With 2 completed + 1 planned + 1 cancelled this
    week, totals should be 3 and rate 2/3."""
    today = _today_jst()
    patient = await _make_patient(db, "DASH-WK")
    staff = await _make_staff(db, "週スタッフ")

    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=staff.id, status="completed",
    )
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(11, 0), end=time(12, 0),
        primary_staff_id=staff.id, status="completed",
    )
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(13, 0), end=time(14, 0),
        primary_staff_id=staff.id,  # planned
    )
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(15, 0), end=time(16, 0),
        primary_staff_id=staff.id, status="cancelled",
    )

    admin = await _make_user(db, "dash-week@example.com", "admin")
    res = await client.get("/api/v1/dashboard/kpi", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["this_week_visits"] == 3
    assert body["this_week_completion_rate"] == round(2 / 3, 4)


# ---------- /trend (extra coverage) ---------------------------------------


@pytest.mark.asyncio
async def test_trend_staff_sees_only_own_across_three_slots(client, db) -> None:
    """`/trend` must apply the same secondary/mentor staff scope as `/kpi`."""
    today = _today_jst()
    patient = await _make_patient(db, "DASH-T-MS")
    me = await _make_staff(db, "本人T")
    other = await _make_staff(db, "他人T")

    # Mine — primary (today)
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=me.id,
    )
    # Mine — secondary (yesterday) – completed
    await _add_visit(
        db, patient_id=patient.id, visit_date=today - timedelta(days=1),
        start=time(10, 0), end=time(11, 0),
        primary_staff_id=other.id, secondary_staff_id=me.id,
        status="completed",
    )
    # Mine — mentor (2 days ago)
    await _add_visit(
        db, patient_id=patient.id, visit_date=today - timedelta(days=2),
        start=time(13, 0), end=time(14, 0),
        primary_staff_id=other.id, mentor_staff_id=me.id,
    )
    # Not mine — must not appear
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(15, 0), end=time(16, 0),
        primary_staff_id=other.id,
    )

    staff_user = await _make_user(
        db, "dash-trend-staff@example.com", "staff", staff_id=me.id
    )
    res = await client.get(
        "/api/v1/dashboard/trend?days=7", headers=_bearer(staff_user)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 7

    by_date = {item["date"]: item for item in body["items"]}
    assert by_date[today.isoformat()]["total"] == 1
    assert by_date[(today - timedelta(days=1)).isoformat()]["total"] == 1
    assert by_date[(today - timedelta(days=1)).isoformat()]["completed"] == 1
    assert by_date[(today - timedelta(days=2)).isoformat()]["total"] == 1

    grand_total = sum(item["total"] for item in body["items"])
    assert grand_total == 3  # the `other`-only visit is excluded


@pytest.mark.asyncio
async def test_trend_staff_without_staff_id_returns_empty_series(client, db) -> None:
    """Staff with no linked staff_id must get a well-formed zero-padded
    series of length `days` instead of all visits / a 500."""
    today = _today_jst()
    patient = await _make_patient(db, "DASH-T-OR")
    other = await _make_staff(db, "他人TOR")
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=other.id,
    )

    orphan = await _make_user(
        db, "dash-trend-orphan@example.com", "staff", staff_id=None
    )
    res = await client.get(
        "/api/v1/dashboard/trend?days=5", headers=_bearer(orphan)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["days"] == 5
    assert len(body["items"]) == 5
    assert all(item["total"] == 0 for item in body["items"])
    assert all(item["completed"] == 0 for item in body["items"])
    assert all(item["unassigned"] == 0 for item in body["items"])
    # Series ends on JST today.
    assert body["items"][-1]["date"] == today.isoformat()


@pytest.mark.asyncio
async def test_trend_excludes_cancelled_visits(client, db) -> None:
    """Cancelled visits must not pad `total` in `/trend` output."""
    today = _today_jst()
    patient = await _make_patient(db, "DASH-T-CXL")
    staff = await _make_staff(db, "Tキャンセル")

    # 1 active + 1 cancelled on the same day → trend total must be 1.
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=staff.id,
    )
    await _add_visit(
        db, patient_id=patient.id, visit_date=today,
        start=time(11, 0), end=time(12, 0),
        primary_staff_id=staff.id, status="cancelled",
    )

    admin = await _make_user(db, "dash-trend-cxl@example.com", "admin")
    res = await client.get(
        "/api/v1/dashboard/trend?days=3", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    today_iso = today.isoformat()
    today_item = next(item for item in body["items"] if item["date"] == today_iso)
    assert today_item["total"] == 1
    assert today_item["completed"] == 0


@pytest.mark.asyncio
async def test_trend_end_date_is_resolved_in_jst(client, db, monkeypatch) -> None:
    """At UTC 2026-05-04T16:00 (= JST 2026-05-05T01:00), `/trend` must end on
    JST today (2026-05-05) rather than UTC today (2026-05-04)."""
    import app.api.v1.dashboard as dashboard_mod

    frozen_utc = datetime(2026, 5, 4, 16, 0, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen_utc.replace(tzinfo=None)
            return frozen_utc.astimezone(tz)

    monkeypatch.setattr(dashboard_mod, "datetime", _FrozenDatetime)

    patient = await _make_patient(db, "DASH-T-TZ")
    staff = await _make_staff(db, "TトレンドTZ")
    # On JST today (5-05) — must surface in the trend window.
    await _add_visit(
        db, patient_id=patient.id, visit_date=date(2026, 5, 5),
        start=time(9, 0), end=time(10, 0),
        primary_staff_id=staff.id,
    )

    admin = await _make_user(db, "dash-trend-tz@example.com", "admin")
    res = await client.get(
        "/api/v1/dashboard/trend?days=3", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["end_date"] == "2026-05-05"
    assert body["start_date"] == "2026-05-03"
    # And the JST-today visit must be visible at the tail of the series.
    assert body["items"][-1]["date"] == "2026-05-05"
    assert body["items"][-1]["total"] == 1
