"""CRUD + RBAC tests for /api/v1/visits."""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User, Visit
from app.models.patient_fixed_visit import PatientFixedVisit


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


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_visits_list_admin_returns_200(client, db) -> None:
    admin = await _make_user(db, "v-admin@example.com", "admin")
    res = await client.get("/api/v1/visits", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_visits_get_unknown_returns_404(client, db) -> None:
    admin = await _make_user(db, "v-admin2@example.com", "admin")
    res = await client.get(f"/api/v1/visits/{uuid4()}", headers=_bearer(admin))
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_visits_delete_staff_returns_403(client, db) -> None:
    p = Patient(code="V001", name="患者")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 1),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="visit",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)

    staff_user = await _make_user(db, "v-staff@example.com", "staff")
    res = await client.delete(f"/api/v1/visits/{visit.id}", headers=_bearer(staff_user))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_visits_list_no_token_returns_401(client) -> None:
    res = await client.get("/api/v1/visits")
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# W18 Codex-fix 重大-1: DELETE /visits/{id} の RBAC 拡張 (manager も許可)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visits_delete_manager_returns_204(client, db) -> None:
    """W18 Codex-fix 重大-1: manager ロールが visit を soft-delete できる."""
    p = Patient(code="V-MGR-1", name="患者")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 4),  # Mon
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="visit",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)

    manager_user = await _make_user(db, "v-mgr-1@example.com", "manager")
    res = await client.delete(f"/api/v1/visits/{visit.id}", headers=_bearer(manager_user))
    assert res.status_code == 204, res.text

    # 確認: visit が soft-delete されている → 後続 GET (admin) で 404 が返る
    # (delete_visit は soft-delete のみで、API 側は deleted_at IS NULL で
    # フィルタするため 404).
    admin_user = await _make_user(db, "v-mgr-1-admin@example.com", "admin")
    follow_up = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(admin_user))
    assert follow_up.status_code == 404, follow_up.text


# ---------------------------------------------------------------------------
# W18 Codex-fix 重大-2: cascade_fixed_visit パラメータ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visits_delete_cascade_fixed_visit_removes_normal_pfv(client, db) -> None:
    """重大-2: cascade_fixed_visit=true で同 (patient_id, weekday) の
    mode='normal' の patient_fixed_visits が物理削除される."""
    p = Patient(code="V-CFV-1", name="患者")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    # Mon の visit + 同 weekday の固定枠 (mode='normal')
    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 4),  # Mon -> weekday=0
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="visit",
    )
    pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=0,
        start_time=time(9, 0),
        duration_min=60,
    )
    db.add_all([visit, pfv])
    await db.commit()
    await db.refresh(visit)

    admin = await _make_user(db, "v-cfv-1@example.com", "admin")
    res = await client.delete(
        f"/api/v1/visits/{visit.id}?cascade_fixed_visit=true",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    # 固定枠が物理削除されていること.
    # 別セッションの commit を反映するため、まず ORM の identity map から
    # 切り離してから ID-only クエリで件数だけ確認する (lazy load を避ける).
    db.expunge_all()
    pfv_ids = (
        (await db.execute(select(PatientFixedVisit.id).where(PatientFixedVisit.patient_id == p.id)))
        .scalars()
        .all()
    )
    assert pfv_ids == []


@pytest.mark.asyncio
async def test_visits_delete_cascade_fixed_visit_removes_both_modes(client, db) -> None:
    """重大-2: cascade_fixed_visit=true は同 weekday の normal / special
    両方の patient_fixed_visits を物理削除する."""
    p = Patient(code="V-CFV-2", name="患者")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 5),  # Tue -> weekday=1
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="visit",
    )
    pfv_normal = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=1,
        start_time=time(9, 0),
        duration_min=60,
    )
    pfv_special = PatientFixedVisit(
        patient_id=p.id,
        mode="special",
        weekday=1,
        start_time=time(10, 0),
        duration_min=45,
    )
    # 別 weekday の固定枠は残るべき
    pfv_other_weekday = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=2,
        start_time=time(9, 0),
        duration_min=60,
    )
    db.add_all([visit, pfv_normal, pfv_special, pfv_other_weekday])
    await db.commit()
    await db.refresh(visit)

    manager_user = await _make_user(db, "v-cfv-2@example.com", "manager")
    res = await client.delete(
        f"/api/v1/visits/{visit.id}?cascade_fixed_visit=true",
        headers=_bearer(manager_user),
    )
    assert res.status_code == 204, res.text

    # weekday=1 の normal/special は両方消える、weekday=2 の normal は残る.
    # identity map 切り離し + tuple-only クエリで lazy load を避ける.
    db.expunge_all()
    rows = (
        await db.execute(
            select(PatientFixedVisit.weekday, PatientFixedVisit.mode).where(
                PatientFixedVisit.patient_id == p.id
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].weekday == 2
    assert rows[0].mode == "normal"


@pytest.mark.asyncio
async def test_visits_delete_without_cascade_keeps_pfv(client, db) -> None:
    """重大-2: cascade_fixed_visit を立てない (default=false) と固定枠は残る."""
    p = Patient(code="V-CFV-3", name="患者")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 6),  # Wed -> weekday=2
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="visit",
    )
    pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=2,
        start_time=time(9, 0),
        duration_min=60,
    )
    db.add_all([visit, pfv])
    await db.commit()
    await db.refresh(visit)

    admin = await _make_user(db, "v-cfv-3@example.com", "admin")
    # cascade を指定しない = default false
    res = await client.delete(f"/api/v1/visits/{visit.id}", headers=_bearer(admin))
    assert res.status_code == 204, res.text

    # 固定枠は残っている (identity map 切り離し + tuple-only クエリ).
    db.expunge_all()
    rows = (
        await db.execute(
            select(PatientFixedVisit.weekday, PatientFixedVisit.mode).where(
                PatientFixedVisit.patient_id == p.id
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].mode == "normal"
    assert rows[0].weekday == 2
