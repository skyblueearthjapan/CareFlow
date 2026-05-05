"""SpecialWeek REST endpoint tests (W3-A) — Wave 5-C addition.

Covers the CRUD surface plus the two non-trivial behaviours the route
guarantees:

* PATCH with ``items`` performs a *full replacement* of child rows
  (no partial merge) — see ``app/api/v1/special_weeks.py``.
* The ``UNIQUE(patient_id, week_start)`` constraint on ``special_weeks``
  surfaces as HTTP 409, not a stack trace.

Eight cases keep the suite fast and the failures specific.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, User


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_patient(db, code: str = "SW001", name: str = "特別週患者") -> Patient:
    p = Patient(code=code, name=name)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(
        subject=user.id, role=user.role, staff_id=user.staff_id
    )
    return {"Authorization": f"Bearer {token}"}


def _payload(patient_id, *, week_start="2026-05-04", items=None) -> dict:
    return {
        "patient_id": str(patient_id),
        "week_start": week_start,
        "week_end": "2026-05-10",
        "apply_mode": "ADD",
        "reason": "GW 連休",
        "status": "draft",
        "items": items
        if items is not None
        else [
            {
                "patient_id": str(patient_id),
                "visit_date": "2026-05-04",
                "weekday": 0,
                "row_label": "朝",
                "time_type": "時間帯",
                "service_minutes": 45,
                "required_staff_count": 1,
            }
        ],
    }


@pytest.mark.asyncio
async def test_special_weeks_list_admin_returns_200(client, db) -> None:
    admin = await _make_user(db, "sw-admin-1@example.com", "admin")
    res = await client.get("/api/v1/special-weeks", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_special_weeks_create_persists_items(client, db) -> None:
    admin = await _make_user(db, "sw-admin-2@example.com", "admin")
    p = await _make_patient(db, code="SW010")
    res = await client.post(
        "/api/v1/special-weeks",
        headers=_bearer(admin),
        json=_payload(p.id),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["patient_id"] == str(p.id)
    assert body["status"] == "draft"
    assert len(body["items"]) == 1
    assert body["items"][0]["service_minutes"] == 45


@pytest.mark.asyncio
async def test_special_weeks_get_unknown_returns_404(client, db) -> None:
    admin = await _make_user(db, "sw-admin-3@example.com", "admin")
    res = await client.get(
        f"/api/v1/special-weeks/{uuid4()}", headers=_bearer(admin)
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_special_weeks_unique_constraint_returns_409(client, db) -> None:
    admin = await _make_user(db, "sw-admin-4@example.com", "admin")
    p = await _make_patient(db, code="SW020")
    payload = _payload(p.id, week_start="2026-06-01")
    payload["items"][0]["visit_date"] = "2026-06-01"
    payload["week_end"] = "2026-06-07"

    first = await client.post(
        "/api/v1/special-weeks", headers=_bearer(admin), json=payload
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/special-weeks", headers=_bearer(admin), json=payload
    )
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_special_weeks_patch_full_replaces_items(client, db) -> None:
    admin = await _make_user(db, "sw-admin-5@example.com", "admin")
    p = await _make_patient(db, code="SW030")
    create = await client.post(
        "/api/v1/special-weeks", headers=_bearer(admin), json=_payload(p.id)
    )
    assert create.status_code == 201
    sw_id = create.json()["id"]

    # PATCH with two new items must drop the original child row entirely.
    new_items = [
        {
            "patient_id": str(p.id),
            "visit_date": "2026-05-05",
            "weekday": 1,
            "time_type": "固定",
            "service_minutes": 30,
            "required_staff_count": 1,
        },
        {
            "patient_id": str(p.id),
            "visit_date": "2026-05-07",
            "weekday": 3,
            "time_type": "午前",
            "service_minutes": 60,
            "required_staff_count": 2,
        },
    ]
    patch = await client.patch(
        f"/api/v1/special-weeks/{sw_id}",
        headers=_bearer(admin),
        json={"items": new_items, "reason": "差替"},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["reason"] == "差替"
    assert len(body["items"]) == 2
    visit_dates = sorted(i["visit_date"] for i in body["items"])
    assert visit_dates == ["2026-05-05", "2026-05-07"]


@pytest.mark.asyncio
async def test_special_weeks_patch_without_items_keeps_children(
    client, db
) -> None:
    """Header-only PATCH (no `items` key) must NOT wipe the existing rows."""
    admin = await _make_user(db, "sw-admin-6@example.com", "admin")
    p = await _make_patient(db, code="SW040")
    create = await client.post(
        "/api/v1/special-weeks", headers=_bearer(admin), json=_payload(p.id)
    )
    sw_id = create.json()["id"]

    patch = await client.patch(
        f"/api/v1/special-weeks/{sw_id}",
        headers=_bearer(admin),
        json={"reason": "理由のみ更新"},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["reason"] == "理由のみ更新"
    # Original 1 item must remain.
    assert len(body["items"]) == 1


@pytest.mark.asyncio
async def test_special_weeks_delete_admin_cascades_items(client, db) -> None:
    admin = await _make_user(db, "sw-admin-7@example.com", "admin")
    p = await _make_patient(db, code="SW050")
    create = await client.post(
        "/api/v1/special-weeks", headers=_bearer(admin), json=_payload(p.id)
    )
    sw_id = create.json()["id"]

    delete = await client.delete(
        f"/api/v1/special-weeks/{sw_id}", headers=_bearer(admin)
    )
    assert delete.status_code == 204, delete.text

    follow = await client.get(
        f"/api/v1/special-weeks/{sw_id}", headers=_bearer(admin)
    )
    assert follow.status_code == 404


@pytest.mark.asyncio
async def test_special_weeks_delete_manager_returns_403(client, db) -> None:
    """DELETE is admin-only; manager (otherwise full CRUD) must be 403."""
    admin = await _make_user(db, "sw-admin-8@example.com", "admin")
    manager = await _make_user(db, "sw-mgr-8@example.com", "manager")
    p = await _make_patient(db, code="SW060")
    create = await client.post(
        "/api/v1/special-weeks", headers=_bearer(admin), json=_payload(p.id)
    )
    sw_id = create.json()["id"]

    res = await client.delete(
        f"/api/v1/special-weeks/{sw_id}", headers=_bearer(manager)
    )
    assert res.status_code == 403, res.text
