"""手動経路の制約 override (性別制限 / NG スタッフ) 確認フロー + 管理者お知らせ tests.

正典設計書: ``docs/plans/patient-ng-staff-design.md`` §7 (PO 決定2)。

検査観点:
  C1  NG 患者のコースへ該当スタッフを PATCH → 422 (code/warnings)
  C2  acknowledge 付き再送 → 200 適用 + admin 人数分の通知
  C3  性別違反 (female_only 患者 × 男性) → 422 → acknowledge → 通知
  C4  staff.sex=None は違反にしない (手動経路は誤検知回避側)
  C5  違反なし PATCH → 素通り・通知なし
  C6  担当解除 (None) / 他フィールドのみ PATCH → 無検査
  C7  op_group_id 付き 2 回適用 → 通知は冪等 (2 回目は増えない)
  C8  apply-staff-review: reason='ng_staff' 付き承認 → 通知 / reason 無し → 通知なし
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, User, Visit
from app.models.course import COURSE_STATUS_COURSE_FIXED
from app.models.notification import Notification
from app.models.patient_ng_staff import PatientNgStaff
from app.models.visit import VISIT_STATUS_PLANNED

ISO_YEAR = 2026
ISO_WEEK = 27
WEEK_MONDAY = date(2026, 6, 29)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed(
    db,
    *,
    prefix: str,
    staff_sex: str | None,
    sex_restriction: str | None = None,
    ng: bool = False,
    ng_note: str | None = None,
    code: str = "A",
    weekday: int = 0,
) -> dict:
    """1 拠点 + 1 スタッフ + 1 患者 + 1 コース + 1 planned visit を seed する."""
    office = Office(name=f"{prefix} 拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code=f"{prefix}-S",
        name=f"{prefix} スタッフ",
        role="staff",
        status="active",
        sex=staff_sex,
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()

    patient = Patient(
        code=f"{prefix}-P",
        name=f"{prefix} 患者",
        status="active",
        sex_restriction=sex_restriction,
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.flush()

    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    db.add(
        Visit(
            patient_id=patient.id,
            visit_date=WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
        )
    )
    if ng:
        db.add(PatientNgStaff(patient_id=patient.id, staff_id=staff.id, note=ng_note))
    await db.commit()

    # commit 後の ORM 属性は expire するため ID / 名前は先に確保する。
    return {
        "office_id": office.id,
        "staff_id": staff.id,
        "staff_name": staff.name,
        "patient_id": patient.id,
        "patient_name": patient.name,
        "course_id": course.id,
    }


async def _override_notifications(db) -> list[Notification]:
    # client 側 session の commit を確実に見るため、fixture session の TX を切り直す
    # (test_patient_ng_staff_api.py と同じ流儀)。
    await db.rollback()
    return list(
        (
            await db.scalars(select(Notification).where(Notification.type == "constraint_override"))
        ).all()
    )


# ---------------------------------------------------------------------------
# C1 / C2: NG スタッフ — 422 → acknowledge → 適用 + 通知
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c1_ng_staff_patch_requires_confirmation(client, db) -> None:
    admin = await _make_user(db, "co-ng-1@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CO-NG1", staff_sex="female", ng=True, ng_note="相性不良")

    res = await client.patch(
        f"/api/v1/courses/{seed['course_id']}",
        headers=headers,
        json={"assigned_staff_id": str(seed["staff_id"])},
    )
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "constraint_confirmation_required"
    assert len(detail["warnings"]) == 1
    w = detail["warnings"][0]
    assert w["kind"] == "ng_staff"
    assert w["patient_id"] == str(seed["patient_id"])
    assert w["patient_name"] == seed["patient_name"]
    assert w["staff_id"] == str(seed["staff_id"])
    assert w["staff_name"] == seed["staff_name"]
    assert w["note"] == "相性不良"

    # 適用されていない + 通知も無い。
    await db.rollback()
    assigned = await db.scalar(
        select(Course.assigned_staff_id).where(Course.id == seed["course_id"])
    )
    assert assigned is None
    assert await _override_notifications(db) == []


@pytest.mark.asyncio
async def test_c2_acknowledge_applies_and_notifies_admins(client, db) -> None:
    admin = await _make_user(db, "co-ng-2a@example.com")
    other_admin = await _make_user(db, "co-ng-2b@example.com")
    await _make_user(db, "co-ng-2c@example.com", role="staff")  # 通知対象外
    headers = _bearer(admin)
    # rollback / commit で ORM オブジェクトは expire するため ID は先に退避する。
    admin_id, other_admin_id = admin.id, other_admin.id
    seed = await _seed(db, prefix="CO-NG2", staff_sex="female", ng=True, ng_note="要注意")

    res = await client.patch(
        f"/api/v1/courses/{seed['course_id']}",
        headers=headers,
        json={
            "assigned_staff_id": str(seed["staff_id"]),
            "acknowledge_constraint_warnings": True,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["assigned_staff_id"] == str(seed["staff_id"])

    await db.rollback()
    assigned = await db.scalar(
        select(Course.assigned_staff_id).where(Course.id == seed["course_id"])
    )
    assert assigned == seed["staff_id"]

    rows = await _override_notifications(db)
    # active admin 全員 (操作者本人を含む) = 2 名。staff ロールには出さない。
    assert len(rows) == 2
    assert {r.user_id for r in rows} == {admin_id, other_admin_id}
    assert all(r.reference_type == "constraint_override" for r in rows)
    assert all(r.reference_id is None for r in rows)  # op_group_id 無し = 毎回通知
    assert "NGスタッフ割当を承認" in rows[0].title
    assert seed["patient_name"] in rows[0].title
    assert "メモ: 要注意" in (rows[0].body or "")
    assert "第27週" in (rows[0].body or "")


# ---------------------------------------------------------------------------
# C3: 性別制限 — female_only 患者のコースへ男性
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c3_gender_violation_requires_confirmation_then_notifies(client, db) -> None:
    admin = await _make_user(db, "co-gender@example.com")
    headers = _bearer(admin)
    seed = await _seed(
        db, prefix="CO-GEN", staff_sex="male", sex_restriction="female_only", ng=False
    )

    res = await client.patch(
        f"/api/v1/courses/{seed['course_id']}",
        headers=headers,
        json={"assigned_staff_id": str(seed["staff_id"])},
    )
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "constraint_confirmation_required"
    assert [w["kind"] for w in detail["warnings"]] == ["gender"]
    assert detail["warnings"][0]["note"] is None

    res = await client.patch(
        f"/api/v1/courses/{seed['course_id']}",
        headers=headers,
        json={
            "assigned_staff_id": str(seed["staff_id"]),
            "acknowledge_constraint_warnings": True,
        },
    )
    assert res.status_code == 200, res.text

    rows = await _override_notifications(db)
    assert len(rows) == 1
    assert "性別制限外の割当を承認" in rows[0].title


# ---------------------------------------------------------------------------
# C4: staff.sex = None は違反にしない (手動経路は誤検知回避側)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c4_unknown_staff_sex_is_not_a_violation(client, db) -> None:
    admin = await _make_user(db, "co-sexnone@example.com")
    headers = _bearer(admin)
    seed = await _seed(
        db, prefix="CO-SEXN", staff_sex=None, sex_restriction="female_only", ng=False
    )

    res = await client.patch(
        f"/api/v1/courses/{seed['course_id']}",
        headers=headers,
        json={"assigned_staff_id": str(seed["staff_id"])},
    )
    assert res.status_code == 200, res.text
    assert await _override_notifications(db) == []


# ---------------------------------------------------------------------------
# C5: 違反なし PATCH は素通り・通知なし
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c5_clean_patch_passes_without_notification(client, db) -> None:
    admin = await _make_user(db, "co-clean@example.com")
    headers = _bearer(admin)
    seed = await _seed(
        db, prefix="CO-CLEAN", staff_sex="female", sex_restriction="female_only", ng=False
    )

    res = await client.patch(
        f"/api/v1/courses/{seed['course_id']}",
        headers=headers,
        json={"assigned_staff_id": str(seed["staff_id"])},
    )
    assert res.status_code == 200, res.text
    assert await _override_notifications(db) == []


# ---------------------------------------------------------------------------
# C6: 担当解除 (None) / 他フィールドのみ PATCH は無検査
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c6_unassign_and_other_fields_are_not_checked(client, db) -> None:
    admin = await _make_user(db, "co-unassign@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CO-UNAS", staff_sex="female", ng=True, ng_note="NG")

    # NG スタッフを DB 直で担当に据える (= 検査を経ずに違反状態を作る)。
    course = await db.get(Course, seed["course_id"])
    course.assigned_staff_id = seed["staff_id"]
    await db.commit()

    # 担当解除 (None 化) は無検査。
    res = await client.patch(
        f"/api/v1/courses/{seed['course_id']}",
        headers=headers,
        json={"assigned_staff_id": None},
    )
    assert res.status_code == 200, res.text
    assert res.json()["assigned_staff_id"] is None

    # 他フィールドのみの PATCH も無検査。
    res = await client.patch(
        f"/api/v1/courses/{seed['course_id']}",
        headers=headers,
        json={"note": "メモだけ更新"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["note"] == "メモだけ更新"

    assert await _override_notifications(db) == []


# ---------------------------------------------------------------------------
# C7: op_group_id 付き 2 回適用 → 通知は冪等
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c7_op_group_id_makes_notification_idempotent(client, db) -> None:
    admin = await _make_user(db, "co-idem@example.com")
    headers = _bearer(admin)
    seed_a = await _seed(db, prefix="CO-IDEM-A", staff_sex="female", ng=True, code="A")
    seed_b = await _seed(db, prefix="CO-IDEM-B", staff_sex="female", ng=True, code="B")
    # 全桁が数字の UUID は SQLite の NUMERIC affinity で REAL に化けるため、
    # テストでは英字を含む値を使う (テスト環境固有の罠。PostgreSQL は無影響)。
    op_group_id = "aaaaaaaa-1111-4111-8111-111111111111"

    for seed in (seed_a, seed_b):
        res = await client.patch(
            f"/api/v1/courses/{seed['course_id']}",
            headers=headers,
            json={
                "assigned_staff_id": str(seed["staff_id"]),
                "acknowledge_constraint_warnings": True,
                "op_group_id": op_group_id,
            },
        )
        assert res.status_code == 200, res.text

    rows = await _override_notifications(db)
    # 一括変更 (同一 op_group_id) は admin 1 名につき 1 通に集約される。
    assert len(rows) == 1
    assert str(rows[0].reference_id) == op_group_id


# ---------------------------------------------------------------------------
# C8: apply-staff-review — reason 付きは通知 / reason 無し (旧形式) は通知なし
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c8_apply_staff_review_notifies_only_with_reason(client, db) -> None:
    admin = await _make_user(db, "co-review@example.com")
    headers = _bearer(admin)
    seed_legacy = await _seed(db, prefix="CO-REV-L", staff_sex="female", ng=True, code="A")
    seed_new = await _seed(
        db, prefix="CO-REV-N", staff_sex="female", ng=True, ng_note="レビュー承認", code="B"
    )

    # (a) 旧形式 (reason 無し) → 従来どおり通す・通知なし。
    res = await client.post(
        "/api/v1/schedule/apply-staff-review",
        headers=headers,
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "items": [
                {
                    "course_id": str(seed_legacy["course_id"]),
                    "staff_id": str(seed_legacy["staff_id"]),
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied_count"] == 1
    assert await _override_notifications(db) == []

    # (b) reason='ng_staff' 付き → 通知が作られる。
    res = await client.post(
        "/api/v1/schedule/apply-staff-review",
        headers=headers,
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "items": [
                {
                    "course_id": str(seed_new["course_id"]),
                    "staff_id": str(seed_new["staff_id"]),
                    "reason": "ng_staff",
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied_count"] == 1

    rows = await _override_notifications(db)
    assert len(rows) == 1
    assert "NGスタッフ割当を承認" in rows[0].title
    assert "メモ: レビュー承認" in (rows[0].body or "")
