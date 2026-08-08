"""W2-BE4: Course CRUD + UNIQUE 制約 + RBAC tests.

設計仕様書 v0.9 §4.5 / API 契約 §4 / 実装手順書 v0.2 §3 W2-BE4 に対応。
本チケットは CRUD のみ (generate / fix / assign-staff は Wave 4)。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, User

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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


async def _make_office(db, name: str = "テスト事業所") -> Office:
    """W15-BE-FIXPATTERN: courses.office_id NOT NULL 化に伴うヘルパー."""
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _course_payload(office_id: UUID, **overrides) -> dict:
    """Minimal valid CourseV2Create payload (W15-BE-FIXPATTERN: office_id 必須)."""
    base = {
        "iso_year": 2026,
        "iso_week": 20,
        "weekday": 0,  # Monday
        "code": "A",
        "course_status": "proposed",
        "office_id": str(office_id),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Happy path: CRUD ハッピーパス
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courses_create_returns_201(client, db) -> None:
    admin = await _make_user(db, "c-create-admin@example.com", "admin")
    office = await _make_office(db, "事業所-create")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["iso_year"] == 2026
    assert body["iso_week"] == 20
    assert body["weekday"] == 0
    assert body["code"] == "A"
    assert body["course_status"] == "proposed"
    assert body["course_fixed_at"] is None
    assert body["staff_assigned_at"] is None
    assert body["assigned_staff_id"] is None
    assert body["office_id"] == str(office.id)
    assert "id" in body


@pytest.mark.asyncio
async def test_courses_get_detail_returns_200(client, db) -> None:
    admin = await _make_user(db, "c-get-admin@example.com", "admin")
    office = await _make_office(db, "事業所-get")
    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, weekday=2, code="B"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    res = await client.get(f"/api/v1/courses/{cid}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == cid
    assert body["code"] == "B"
    assert body["weekday"] == 2


@pytest.mark.asyncio
async def test_courses_get_unknown_returns_404(client, db) -> None:
    admin = await _make_user(db, "c-get-404-admin@example.com", "admin")
    res = await client.get(f"/api/v1/courses/{uuid4()}", headers=_bearer(admin))
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_courses_list_returns_array(client, db) -> None:
    admin = await _make_user(db, "c-list-admin@example.com", "admin")
    office = await _make_office(db, "事業所-list")
    # 2 件作成
    for code in ("A", "B"):
        res = await client.post(
            "/api/v1/courses",
            headers=_bearer(admin),
            json=_course_payload(office.id, weekday=3, code=code),
        )
        assert res.status_code == 201, res.text

    res = await client.get("/api/v1/courses", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, list)
    codes = {item["code"] for item in body if item["weekday"] == 3}
    assert {"A", "B"} <= codes


@pytest.mark.asyncio
async def test_courses_list_filters_by_year_week_weekday(client, db) -> None:
    admin = await _make_user(db, "c-list-f-admin@example.com", "admin")
    office = await _make_office(db, "事業所-list-f")
    # 2 件: 別週
    await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, iso_week=21, weekday=1, code="A"),
    )
    await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, iso_week=22, weekday=1, code="A"),
    )

    res = await client.get(
        "/api/v1/courses?iso_year=2026&iso_week=22",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert all(item["iso_week"] == 22 for item in body)


@pytest.mark.asyncio
async def test_courses_patch_updates_status(client, db) -> None:
    admin = await _make_user(db, "c-patch-admin@example.com", "admin")
    office = await _make_office(db, "事業所-patch")
    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, weekday=4, code="C"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    res = await client.patch(
        f"/api/v1/courses/{cid}",
        headers=_bearer(admin),
        json={"course_status": "course_fixed", "note": "確定"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["course_status"] == "course_fixed"
    assert body["note"] == "確定"


@pytest.mark.asyncio
async def test_courses_patch_trainee_assignee_returns_422(client, db) -> None:
    """新人同行 §8: is_trainee=true をコース担当に指定すると 422 (同行で割り当てる)."""
    from app.models import Staff

    admin = await _make_user(db, "c-patch-trainee@example.com", "admin")
    office = await _make_office(db, "事業所-trainee")
    trainee = Staff(name="新人ハナコ", role="staff", status="active", is_trainee=True)
    normal = Staff(name="先輩タロウ", role="staff", status="active", is_trainee=False)
    db.add_all([trainee, normal])
    await db.commit()
    await db.refresh(trainee)
    await db.refresh(normal)

    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, weekday=2, code="B"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    # 新人を担当に指定 → 422
    res = await client.patch(
        f"/api/v1/courses/{cid}",
        headers=_bearer(admin),
        json={"assigned_staff_id": str(trainee.id)},
    )
    assert res.status_code == 422, res.text

    # 通常スタッフは OK (回帰確認)
    ok = await client.patch(
        f"/api/v1/courses/{cid}",
        headers=_bearer(admin),
        json={"assigned_staff_id": str(normal.id)},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["assigned_staff_id"] == str(normal.id)


@pytest.mark.asyncio
async def test_courses_delete_admin_returns_204(client, db) -> None:
    admin = await _make_user(db, "c-del-admin@example.com", "admin")
    office = await _make_office(db, "事業所-del")
    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, weekday=5, code="D"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    res = await client.delete(f"/api/v1/courses/{cid}", headers=_bearer(admin))
    assert res.status_code == 204, res.text

    follow = await client.get(f"/api/v1/courses/{cid}", headers=_bearer(admin))
    assert follow.status_code == 404, follow.text


# ---------------------------------------------------------------------------
# 2. UNIQUE 制約 (iso_year, iso_week, weekday, code)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courses_create_duplicate_returns_409(client, db) -> None:
    """W41 v1.0 feedback / cross-review C1: UNIQUE は partial
    (``WHERE course_status != 'proposed' AND deleted_at IS NULL``) になったため、
    確定済み (course_fixed / staff_assigned) かつ未 soft-delete 同士の
    (year, week, weekday, code, office) 重複のみが 409 になる.
    proposed 同士は再算出で共存できる仕様 (migration 0030 → 0031)."""
    admin = await _make_user(db, "c-uniq-admin@example.com", "admin")
    office = await _make_office(db, "事業所-uniq")
    # course_fixed 同士の重複は 409 のまま (partial UNIQUE 範囲内)
    payload = _course_payload(
        office.id,
        iso_year=2026,
        iso_week=30,
        weekday=0,
        code="A",
        course_status="course_fixed",
    )
    first = await client.post("/api/v1/courses", headers=_bearer(admin), json=payload)
    assert first.status_code == 201, first.text

    duplicate = await client.post("/api/v1/courses", headers=_bearer(admin), json=payload)
    assert duplicate.status_code == 409, duplicate.text


@pytest.mark.asyncio
async def test_courses_create_duplicate_proposed_ok(client, db) -> None:
    """W41 v1.0 feedback: proposed 同士は partial UNIQUE の対象外なので
    (year, week, weekday, code, office) が一致しても 201 で共存できる."""
    admin = await _make_user(db, "c-uniq-prop-admin@example.com", "admin")
    office = await _make_office(db, "事業所-uniq-prop")
    payload = _course_payload(
        office.id,
        iso_year=2026,
        iso_week=33,
        weekday=0,
        code="A",
        course_status="proposed",
    )
    first = await client.post("/api/v1/courses", headers=_bearer(admin), json=payload)
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/courses", headers=_bearer(admin), json=payload)
    # partial UNIQUE のため proposed 同士は 201 (共存可)
    assert second.status_code == 201, second.text


@pytest.mark.asyncio
async def test_courses_create_after_soft_delete_finalized_ok(client, db) -> None:
    """W41 v1.0 cross-review C1 (migration 0031): partial UNIQUE INDEX に
    ``deleted_at IS NULL`` 条件が追加されたため、soft-delete 済みの
    finalized Course は新規 finalized Course と (year, week, weekday, code,
    office) を共有しても UNIQUE 違反にならない.

    これは ``apply_proposal`` で既存 finalized を soft-delete してから
    proposed を staff_assigned に昇格させる契約を支える挙動.
    """
    admin = await _make_user(db, "c-uniq-softdel-admin@example.com", "admin")
    office = await _make_office(db, "事業所-uniq-softdel")
    payload = _course_payload(
        office.id,
        iso_year=2026,
        iso_week=34,
        weekday=0,
        code="A",
        course_status="course_fixed",
    )
    first = await client.post("/api/v1/courses", headers=_bearer(admin), json=payload)
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    # soft-delete (DELETE は admin のみ; 204 を返す)
    del_res = await client.delete(f"/api/v1/courses/{first_id}", headers=_bearer(admin))
    assert del_res.status_code == 204, del_res.text

    # 同じキーで新規 finalized を作成できる (partial UNIQUE INDEX が
    # deleted_at IS NULL を考慮するため UNIQUE 違反にならない).
    second = await client.post("/api/v1/courses", headers=_bearer(admin), json=payload)
    assert second.status_code == 201, second.text


@pytest.mark.asyncio
async def test_courses_create_different_weekday_same_code_ok(client, db) -> None:
    """同じ (year, week, code) でも weekday が違えば許可される."""
    admin = await _make_user(db, "c-uniq2-admin@example.com", "admin")
    office = await _make_office(db, "事業所-uniq2")
    a = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, iso_week=31, weekday=0, code="A"),
    )
    assert a.status_code == 201, a.text
    b = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, iso_week=31, weekday=1, code="A"),
    )
    assert b.status_code == 201, b.text


@pytest.mark.asyncio
async def test_courses_create_same_key_different_office_ok(client, db) -> None:
    """W15-codex-fix (4): UNIQUE が office スコープになり、別拠点なら同 (year, week,
    weekday, code) で共存できる."""
    admin = await _make_user(db, "c-uniq3-admin@example.com", "admin")
    office_a = await _make_office(db, "事業所-uniq3a")
    office_b = await _make_office(db, "事業所-uniq3b")

    r1 = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office_a.id, iso_week=32, weekday=0, code="A"),
    )
    assert r1.status_code == 201, r1.text
    r2 = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office_b.id, iso_week=32, weekday=0, code="A"),
    )
    # office が違えば 201 (旧仕様だと 409)
    assert r2.status_code == 201, r2.text


# ---------------------------------------------------------------------------
# 3. CHECK 制約 / バリデーション
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courses_create_invalid_code_returns_422(client, db) -> None:
    """A/B/C/D/M 以外の code は 422 (Pydantic 側で弾かれる)."""
    admin = await _make_user(db, "c-bad-admin@example.com", "admin")
    office = await _make_office(db, "事業所-bad")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, code="X"),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_courses_create_invalid_status_returns_422(client, db) -> None:
    admin = await _make_user(db, "c-bad2-admin@example.com", "admin")
    office = await _make_office(db, "事業所-bad2")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, course_status="invalid_status"),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_courses_create_weekday_out_of_range_returns_422(client, db) -> None:
    admin = await _make_user(db, "c-bad3-admin@example.com", "admin")
    office = await _make_office(db, "事業所-bad3")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, weekday=7),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_courses_create_without_office_id_returns_422(client, db) -> None:
    """W15-BE-FIXPATTERN: office_id 必須化のバリデーション確認."""
    admin = await _make_user(db, "c-no-office-admin@example.com", "admin")
    payload = {
        "iso_year": 2026,
        "iso_week": 20,
        "weekday": 0,
        "code": "A",
        "course_status": "proposed",
        # office_id 欠落
    }
    res = await client.post("/api/v1/courses", headers=_bearer(admin), json=payload)
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 4. RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courses_list_no_token_returns_401(client) -> None:
    res = await client.get("/api/v1/courses")
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_courses_create_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "c-staff@example.com", "staff")
    office = await _make_office(db, "事業所-staff")
    res = await client.post(
        "/api/v1/courses",
        headers=_bearer(staff),
        json=_course_payload(office.id),
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_courses_list_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "c-list-staff@example.com", "staff")
    res = await client.get("/api/v1/courses", headers=_bearer(staff))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_courses_delete_manager_returns_403(client, db) -> None:
    """DELETE は admin のみ. manager は 403."""
    admin = await _make_user(db, "c-d-admin@example.com", "admin")
    manager = await _make_user(db, "c-d-mgr@example.com", "manager")
    office = await _make_office(db, "事業所-del-mgr")
    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, weekday=6, code="M"),
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    res = await client.delete(f"/api/v1/courses/{cid}", headers=_bearer(manager))
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 5. ORM round-trip (model 直接アクセス)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_course_orm_round_trip(db) -> None:
    """ORM 経由で Course を作成・取得できる (timestamps が埋まる).

    W15-BE-FIXPATTERN: ``office_id`` を NOT NULL 化したため、
    必ず Office を先に作成して FK を埋める必要がある。
    """
    office = await _make_office(db, "事業所-orm")
    course = Course(
        iso_year=2026,
        iso_week=15,
        weekday=2,
        code="C",
        office_id=office.id,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)

    assert course.id is not None
    assert course.course_status == "proposed"
    assert course.created_at is not None
    assert course.updated_at is not None
    assert course.deleted_at is None
    assert course.office_id == office.id


# ---------------------------------------------------------------------------
# 6. Wave 16 退行ガード: code='E' round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_course_code_e_round_trip(client, db) -> None:
    """code='E' で Course を作成し GET でも 'E' のまま返ることを確認。

    Wave 16 codex-fix で migration 0023 + Pydantic Literal 拡張を整合させた
    退行ガード。ResponseValidationError 500 および Zod parse 失敗が再発しない
    ことを保証する。
    """
    admin = await _make_user(db, "c-e-admin@example.com", "admin")
    office = await _make_office(db, "事業所-e")

    # POST /api/v1/courses with code='E' → 201
    create_res = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(office.id, iso_week=40, weekday=0, code="E"),
    )
    assert create_res.status_code == 201, create_res.text
    body = create_res.json()
    assert body["code"] == "E"
    cid = body["id"]

    # GET /api/v1/courses/{id} → 200, code='E' のまま返る
    get_res = await client.get(f"/api/v1/courses/{cid}", headers=_bearer(admin))
    assert get_res.status_code == 200, get_res.text
    assert get_res.json()["code"] == "E"

    # GET /api/v1/courses?iso_year=2026&iso_week=40 → リストに code='E' が含まれる
    list_res = await client.get(
        "/api/v1/courses?iso_year=2026&iso_week=40",
        headers=_bearer(admin),
    )
    assert list_res.status_code == 200, list_res.text
    codes = {item["code"] for item in list_res.json()}
    assert "E" in codes


@pytest.mark.asyncio
async def test_courses_patch_staff_propagates_to_visits(client, db) -> None:
    """PATCH /courses で担当変更すると、そのコースの visits.primary_staff_id / VSA も追従する。

    回帰 (PO報告 2026-07-09): 追従しないと訪問モニター/モバイル「今日の訪問」/
    ダッシュボード (visits.primary_staff_id 参照) が担当変更後にスケジュールとズレる。
    """
    from datetime import date, time

    from sqlalchemy import select

    from app.models.patient import Patient
    from app.models.staff import Staff
    from app.models.visit import Visit

    admin = await _make_user(db, "c-patch-staff@example.com", "admin")
    office = await _make_office(db, "事業所-staffprop")
    s_old = Staff(name="旧担当", role="staff", is_trainee=False, primary_office_id=office.id)
    s_new = Staff(name="新担当", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add_all([s_old, s_new])
    await db.flush()
    p = Patient(
        code="CPSPROP",
        name="患者",
        status="active",
        lat=35.6,
        lng=140.1,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()

    create = await client.post(
        "/api/v1/courses",
        headers=_bearer(admin),
        json=_course_payload(
            office.id,
            weekday=3,
            code="A",
            course_status="staff_assigned",
            assigned_staff_id=str(s_old.id),
        ),
    )
    assert create.status_code == 201, create.text
    cid = UUID(create.json()["id"])

    # このコースに紐付く visit (旧担当で割当済み)。
    v = Visit(
        patient_id=p.id,
        course_id=cid,
        visit_date=date(2026, 5, 14),
        start_time=time(9, 0),
        end_time=time(9, 40),
        type="regular",
        status="planned",
        source="auto_alloc",
        required_staff_count=1,
        primary_staff_id=s_old.id,
        manual_staff_override=False,
    )
    # 手動上書き visit は担当変更で触られないこと。
    v_manual = Visit(
        patient_id=p.id,
        course_id=cid,
        visit_date=date(2026, 5, 14),
        start_time=time(10, 0),
        end_time=time(10, 40),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=1,
        primary_staff_id=s_old.id,
        manual_staff_override=True,
    )
    db.add_all([v, v_manual])
    await db.commit()
    vid, vid_manual = v.id, v_manual.id

    # 担当を新担当へ変更。
    res = await client.patch(
        f"/api/v1/courses/{cid}",
        headers=_bearer(admin),
        json={"assigned_staff_id": str(s_new.id)},
    )
    assert res.status_code == 200, res.text

    # PATCH は別セッションで DB を更新するため、テストセッションの識別マップを破棄して読み直す。
    db.expunge_all()
    refreshed = await db.scalar(select(Visit).where(Visit.id == vid))
    assert refreshed is not None
    assert refreshed.primary_staff_id == s_new.id, "primary_staff_id が新担当へ追従していない"
    manual = await db.scalar(select(Visit).where(Visit.id == vid_manual))
    assert manual is not None
    assert manual.primary_staff_id == s_old.id, "手動上書き visit が誤って変更された"
