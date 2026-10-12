"""赤ピン整理 (2026-10-12, docs/plans/red-pin-simplify-design.md) のバックエンド横断テスト.

検証観点:
  1. 赤は 2 値 (locked / unknown). is_pinned は movability=='locked' の厳密ミラー
     (どの書き込み経路でも 1 か所 = ORM イベントでそろう).
  2. 見送り昇格は time_immovable だけ locked + is_pinned を立てる (day_immovable は記録のみ).
  3. 自動割当: 赤は曜日・時刻だけを縛り、コースは縛らない (slot 0 / slot 1 とも).
  4. 盤面専用の赤トグル API (K-6) は削除済み.
"""

from __future__ import annotations

import uuid
from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User, Visit
from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.scheduling.auto_allocator_v2 import (
    V2Visit,
    V2Warning,
    _add_minutes,
    _apply_corrections_to_visits,
    build_visits_for_pool,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _admin(db, email: str) -> User:
    u = User(email=email, password_hash=hash_password("pw"), role="admin")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _patient(db, code: str, *, multi: bool = False) -> Patient:
    office = Office(name=f"o-{code}")
    db.add(office)
    await db.flush()
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        special_week_active=[],
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        requires_multiple_staff=multi,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


def _pfv(patient: Patient, **kw) -> PatientFixedVisit:
    base = {
        "patient_id": patient.id,
        "mode": "normal",
        "weekday": 0,
        "slot_index": 0,
        "start_time": time(9, 0),
        "duration_min": 30,
    }
    base.update(kw)
    return PatientFixedVisit(**base)


async def _rows(db, patient: Patient) -> list[PatientFixedVisit]:
    rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id))
    ).all()
    for r in rows:
        await db.refresh(r)
    return list(rows)


# ---------------------------------------------------------------------------
# 1. ミラー: ORM 書き込み (全経路が通る共通の出口)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, ("unknown", False)),
        ({"movability": "locked"}, ("locked", True)),
        ({"is_pinned": True}, ("locked", True)),
        ({"movability": "unknown", "is_pinned": False}, ("unknown", False)),
        # 旧値は温存されるが赤ではない (is_pinned=False)
        ({"movability": "time_flexible"}, ("time_flexible", False)),
        ({"movability": "day_flexible"}, ("day_flexible", False)),
    ],
)
async def test_orm_insert_mirror(db, kwargs, expected) -> None:
    p = await _patient(db, "RPS-INS")
    row = _pfv(p, **kwargs)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    assert (row.movability, row.is_pinned) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "change", "expected"),
    [
        ({"movability": "unknown"}, {"movability": "locked"}, ("locked", True)),
        ({"movability": "locked"}, {"movability": "unknown"}, ("unknown", False)),
        ({"movability": "unknown"}, {"is_pinned": True}, ("locked", True)),
        ({"movability": "locked"}, {"is_pinned": False}, ("unknown", False)),
    ],
)
async def test_orm_update_mirror(db, start, change, expected) -> None:
    p = await _patient(db, "RPS-UPD")
    row = _pfv(p, **start)
    db.add(row)
    await db.commit()
    for k, v in change.items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    assert (row.movability, row.is_pinned) == expected


# ---------------------------------------------------------------------------
# 1b. ミラー: API 書き込み経路 (PUT / from-week / sync)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ({"movability": "locked"}, ("locked", True)),
        ({"is_pinned": True}, ("locked", True)),
        ({"movability": "time_flexible"}, ("unknown", False)),
        ({"movability": "day_flexible", "is_pinned": False}, ("unknown", False)),
        ({}, ("unknown", False)),
    ],
)
async def test_put_fixed_visits_mirror(client, db, sent, expected) -> None:
    admin = await _admin(db, "rps-put@example.com")
    p = await _patient(db, "RPS-PUT")
    item = {"weekday": 0, "start_time": "09:00", "duration_min": 30, **sent}
    res = await client.put(
        f"/api/v1/patients/{p.id}/fixed-visits",
        headers=_bearer(admin),
        json={"mode": "normal", "items": [item]},
    )
    assert res.status_code == 200, res.text
    (row,) = await _rows(db, p)
    assert (row.movability, row.is_pinned) == expected


async def _visit(db, p: Patient, d: date, start: time, end: time) -> None:
    db.add(
        Visit(
            patient_id=p.id,
            visit_date=d,
            start_time=start,
            end_time=end,
            type="regular",
            status="planned",
            source="manual",
            required_staff_count=1,
        )
    )
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["locked", "time_flexible", "unknown"])
@pytest.mark.parametrize("endpoint", ["from_week", "from_week_bulk"])
async def test_from_week_paths_mirror(client, db, prior, endpoint) -> None:
    admin = await _admin(db, f"rps-fw-{endpoint}-{prior}@example.com")
    p = await _patient(db, f"RPS-FW-{endpoint[:11]}-{prior[:4]}")
    db.add(_pfv(p, movability=prior))
    await db.commit()
    await _visit(db, p, date(2026, 5, 11), time(9, 0), time(9, 45))
    if endpoint == "from_week":
        url = f"/api/v1/patients/{p.id}/fixed-visits/from-week"
    else:
        url = "/api/v1/patients/fixed-visits/from-week-bulk"
    res = await client.post(url, headers=_bearer(admin), params={"iso_year": 2026, "iso_week": 20})
    assert res.status_code == 200, res.text
    rows = await _rows(db, p)
    assert len(rows) == 1
    want = "locked" if prior == "locked" else "unknown"
    assert (rows[0].movability, rows[0].is_pinned) == (want, want == "locked")


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["locked", "time_flexible", "unknown"])
async def test_sync_week_visits_to_fixed_mirror(client, db, prior) -> None:
    """sync の insert (旧行から引き継ぎ) / update 経路もミラーがそろう."""
    admin = await _admin(db, f"rps-sync-{prior}@example.com")
    p = await _patient(db, f"RPS-SY-{prior[:4]}")
    db.add(_pfv(p, movability=prior))
    await db.commit()
    await _visit(db, p, date(2026, 5, 11), time(10, 0), time(10, 30))
    await _visit(db, p, date(2026, 5, 12), time(13, 0), time(13, 30))
    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 20, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    rows = await _rows(db, p)
    assert rows
    for r in rows:
        assert r.is_pinned == (r.movability == "locked"), (r.weekday, r.movability, r.is_pinned)
        if r.weekday == 1:  # insert 経路は 2 値だけ書く
            assert r.movability in ("locked", "unknown")


# ---------------------------------------------------------------------------
# 2. 見送り昇格: どちらの理由でも locked + is_pinned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["time_immovable", "day_immovable"])
async def test_dismissal_promotion_only_time_immovable(client, db, reason) -> None:
    admin = await _admin(db, f"rps-dis-{reason}@example.com")
    p = await _patient(db, f"RPS-DIS-{reason[:3]}")
    row = _pfv(p)
    db.add(row)
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/improvement-suggestions/dismiss",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "kind": "time_change",
            "target_weekday": 0,
            "reason": reason,
            "promote_movability": True,
        },
    )
    assert res.status_code == 200, res.text
    await db.refresh(row)
    if reason == "time_immovable":
        assert res.json()["new_movability"] == "locked"
        assert (row.movability, row.is_pinned) == ("locked", True)
    else:
        assert res.json()["new_movability"] is None
        assert (row.movability, row.is_pinned) != ("locked", True)


# ---------------------------------------------------------------------------
# 3. 自動割当: 赤は曜日・時刻だけ縛り、コースは縛らない
# ---------------------------------------------------------------------------


def _v2(pid: uuid.UUID, office: uuid.UUID, *, name: str, h: int, course: str, **kw) -> V2Visit:
    st = time(h, 0)
    return V2Visit(
        patient_id=pid,
        patient_name=name,
        patient_code=None,
        weekday=0,
        start_time=st,
        end_time=_add_minutes(st, 30),
        service_minutes=30,
        lat=35.65,
        lng=140.10,
        office_id=office,
        am_pm="am",
        source_kind="fixed",
        course_code=course,
        time_type="時間帯",
        preferred_start=st.strftime("%H:%M"),
        preferred_end=None,
        **kw,
    )


def test_fence_restores_time_but_not_course() -> None:
    """フェンスは時刻を元に戻すが、コースは巻き戻さない (コースは赤が縛らない)."""
    office = uuid.uuid4()
    red = _v2(uuid.uuid4(), office, name="赤", h=9, course="A", is_movability_locked=True)
    other = _v2(uuid.uuid4(), office, name="他", h=9, course="A")
    warnings: list[V2Warning] = []
    start0 = red.start_time
    red.course_code = "B"  # Stage 4 でコースが付け替わった状況
    _apply_corrections_to_visits([red, other], warnings=warnings)
    assert red.start_time == start0
    assert red.course_code == "B"


@pytest.mark.asyncio
@pytest.mark.parametrize("red_slot", [0, 1])
async def test_red_on_either_slot_freezes_weekday(db, red_slot) -> None:
    """2 名体制: slot 0 / slot 1 のどちらが赤でも、その曜日の visit は凍結扱い."""
    p = await _patient(db, f"RPS-S{red_slot}", multi=True)
    s0 = _pfv(p, slot_index=0, start_time=time(9, 30), movability="locked" if red_slot == 0 else "unknown")
    s1 = _pfv(p, slot_index=1, start_time=time(9, 30), movability="locked" if red_slot == 1 else "unknown")
    db.add_all([s0, s1])
    await db.commit()
    await db.refresh(s0)
    await db.refresh(s1)
    visits = build_visits_for_pool(
        [p], fixed_by_patient={p.id: [s0, s1]}, use_fixed_as_source=True
    )
    mon = [v for v in visits if v.weekday == 0]
    assert mon, "visit が組まれる"
    assert all(v.is_movability_locked or v.is_pinned for v in mon)
    assert all(v.start_time == time(9, 30) for v in mon)


# ---------------------------------------------------------------------------
# 4. 盤面専用の赤トグル API は無い (K-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_pin_routes_removed(client, db) -> None:
    admin = await _admin(db, "rps-routes@example.com")
    pid = uuid.uuid4()
    r1 = await client.patch(
        f"/api/v1/patients/fixed-visits/{pid}/pin", headers=_bearer(admin), json={"is_pinned": True}
    )
    assert r1.status_code in (404, 405), r1.text
    r2 = await client.post(
        "/api/v1/patients/fixed-visits/pin/bulk",
        headers=_bearer(admin),
        json=[{"pfv_id": str(pid), "is_pinned": True}],
    )
    assert r2.status_code in (404, 405, 422), r2.text
