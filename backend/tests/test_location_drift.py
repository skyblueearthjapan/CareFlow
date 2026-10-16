"""登録の位置のずれと「打刻の場所に合わせる」のテスト (2026-10-17).

``docs/plans/location-drift-align-2026-10-17.md`` §7。

- 条件の境目: 2 件・1 日だけ・6 割未満・100m 以内は出ない。登録座標なしは距離なしで出る。
- 精度が悪い打刻 (> 100m) は数えない。精度なしは数える。
- align: patient と geocoding_cache の両方が変わり、audit が残る。ずれが無ければ 409。
- revert: 元に戻る (cache も)・記録が残る・直近の align が無ければ 404・続けては 404。
  合わせた後に住所 / 座標が変わっていたら 409。cache が他で変わっていたら cache は触らない。
- JST の日の境目 (UTC の日ではなく JST の日で数える)。
- staff は 403。
- モニターは admin だけに ``location_drift`` を入れる。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import AuditLog, Patient, Staff, User, Visit, VisitCheckin
from app.models.geocoding_cache import GeocodingCache
from app.services.checkin.judge import JST
from app.services.checkin.location_drift import compute_drift, load_location_drifts
from app.services.checkin.monitor import build_monitor
from app.services.geocoding.hash import address_hash

# 登録座標と、そこから北へ約 440m の「実際に読む場所」。
REG_LAT, REG_LNG = 35.6000000, 140.1000000
REAL_LAT, REAL_LNG = 35.6040000, 140.1000000
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=JST).astimezone(UTC)
ADDRESS = "千葉県千葉市稲毛区小仲台1-2-3"


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, code: str, *, lat=REG_LAT, lng=REG_LNG, address=None) -> Patient:
    p = Patient(code=code, name=f"患者{code}", lat=lat, lng=lng, address=address)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_staff(db) -> Staff:
    staff = Staff(name="職員")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def _make_visit(db, patient, staff, visit_date: date) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=visit_date,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _scans(
    db,
    patient,
    staff,
    days_ago: list[int],
    *,
    lat=REAL_LAT,
    lng=REAL_LNG,
    accuracy=None,
    source="qr",
) -> None:
    """``days_ago`` の日 (JST 10:00) ごとに 1 件ずつ打刻を足す (訪問もその日に作る)。"""
    for ago in days_ago:
        visit_day = (NOW.astimezone(JST) - timedelta(days=ago)).date()
        v = await _make_visit(db, patient, staff, visit_day)
        db.add(
            VisitCheckin(
                visit_id=v.id,
                patient_id=patient.id,
                staff_id=staff.id,
                kind="arrival",
                scanned_at=datetime.combine(visit_day, time(10, 0), tzinfo=JST).astimezone(UTC),
                lat=lat,
                lng=lng,
                accuracy_m=accuracy,
                match_status="mismatch",
                threshold_snapshot={"v": 1},
                checkin_source=source,
            )
        )
    await db.commit()


async def _drift(db, patient):
    return (await load_location_drifts(db, [patient], now=NOW)).get(patient.id)


# ---------------------------------------------------------------------------
# 条件の境目
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drift_found_with_three_scans_on_two_days(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-OK")
    await _scans(db, p, staff, [1, 1, 3])

    drift = await _drift(db, p)
    assert drift is not None
    assert drift.scans == 3
    assert drift.days == 2
    assert drift.suggested_lat == pytest.approx(REAL_LAT)
    assert drift.suggested_lng == pytest.approx(REAL_LNG)
    assert 400 < drift.distance_m < 480


@pytest.mark.asyncio
async def test_no_drift_with_two_scans(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-TWO")
    await _scans(db, p, staff, [1, 2])
    assert await _drift(db, p) is None


@pytest.mark.asyncio
async def test_no_drift_when_all_on_one_day(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-ONEDAY")
    await _scans(db, p, staff, [2, 2, 2, 2])
    assert await _drift(db, p) is None


@pytest.mark.asyncio
async def test_no_drift_when_cluster_under_sixty_percent(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-RATIO")
    # まとまり 3 件 + よそ (それぞれ 1km 以上離れた別々の場所) 3 件 = 5 割。
    await _scans(db, p, staff, [1, 2, 3])
    await _scans(db, p, staff, [4], lat=35.62, lng=140.12)
    await _scans(db, p, staff, [5], lat=35.58, lng=140.08)
    await _scans(db, p, staff, [6], lat=35.63, lng=140.07)
    assert await _drift(db, p) is None


@pytest.mark.asyncio
async def test_drift_tolerates_minority_outlier(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-OUTLIER")
    # まとまり 4 件 + よそ 1 件 = 8 割 → 出る (提案の位置はまとまりの中央値)。
    await _scans(db, p, staff, [1, 2, 3, 4])
    await _scans(db, p, staff, [5], lat=35.62, lng=140.12)
    drift = await _drift(db, p)
    assert drift is not None
    assert drift.scans == 4
    assert drift.suggested_lat == pytest.approx(REAL_LAT)


@pytest.mark.asyncio
async def test_no_drift_within_match_m(db) -> None:
    staff = await _make_staff(db)
    # 登録座標から約 55m (100m 以内) で読んでいる。
    p = await _make_patient(db, "LD-NEAR")
    await _scans(db, p, staff, [1, 2, 3], lat=REG_LAT + 0.0005, lng=REG_LNG)
    assert await _drift(db, p) is None


@pytest.mark.asyncio
async def test_drift_without_registered_point_has_no_distance(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-NOREG", lat=None, lng=None)
    await _scans(db, p, staff, [1, 2, 3])
    drift = await _drift(db, p)
    assert drift is not None
    assert drift.distance_m is None


@pytest.mark.asyncio
async def test_poor_accuracy_and_manual_scans_not_counted(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-ACC")
    # 精度 150m の打刻と手入力の打刻は数えない → 数えるのは 2 件だけ。
    await _scans(db, p, staff, [1, 2], accuracy=30)
    await _scans(db, p, staff, [3, 4], accuracy=150)
    await _scans(db, p, staff, [5], source="manual")
    assert await _drift(db, p) is None

    # 精度なしは数える → 3 件になって出る。
    await _scans(db, p, staff, [6])
    drift = await _drift(db, p)
    assert drift is not None
    assert drift.scans == 3


@pytest.mark.asyncio
async def test_scans_older_than_sixty_days_not_counted(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-OLD")
    await _scans(db, p, staff, [1, 61, 62])
    assert await _drift(db, p) is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_location_drift_api(client, db) -> None:
    admin = await _make_user(db, "ld-get@example.com", "admin")
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-API")
    pid = p.id
    # API は今の時刻で数えるので、今日から数えた日にする。
    today = datetime.now(JST).date()
    for ago in (1, 2, 3):
        v = await _make_visit(db, p, staff, today - timedelta(days=ago))
        db.add(
            VisitCheckin(
                visit_id=v.id,
                patient_id=pid,
                staff_id=staff.id,
                kind="arrival",
                scanned_at=datetime.combine(v.visit_date, time(10, 0), tzinfo=JST),
                lat=REAL_LAT,
                lng=REAL_LNG,
                match_status="mismatch",
                threshold_snapshot={"v": 1},
                checkin_source="qr",
            )
        )
    await db.commit()

    res = await client.get(f"/api/v1/patients/{pid}/location-drift", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scans"] == 3
    assert body["days"] == 3
    assert body["suggested_lat"] == pytest.approx(REAL_LAT)

    other = await _make_patient(db, "LD-API-NONE")
    res = await client.get(f"/api/v1/patients/{other.id}/location-drift", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json() is None


async def _drifting_patient(db, code: str, *, with_cache: bool = True) -> Patient:
    staff = await _make_staff(db)
    p = await _make_patient(db, code, address=ADDRESS if with_cache else None)
    today = datetime.now(JST).date()
    for ago in (1, 2, 3):
        v = await _make_visit(db, p, staff, today - timedelta(days=ago))
        db.add(
            VisitCheckin(
                visit_id=v.id,
                patient_id=p.id,
                staff_id=staff.id,
                kind="arrival",
                scanned_at=datetime.combine(v.visit_date, time(10, 0), tzinfo=JST),
                lat=REAL_LAT,
                lng=REAL_LNG,
                match_status="mismatch",
                threshold_snapshot={"v": 1},
                checkin_source="qr",
            )
        )
    if with_cache:
        db.add(
            GeocodingCache(
                address_hash=address_hash(ADDRESS),
                address=ADDRESS,
                lat=REG_LAT,
                lng=REG_LNG,
            )
        )
    await db.commit()
    return p


async def _fresh_patient(db, pid) -> Patient:
    return await db.scalar(
        select(Patient).where(Patient.id == pid).execution_options(populate_existing=True)
    )


async def _fresh_cache(db) -> GeocodingCache:
    return await db.scalar(
        select(GeocodingCache)
        .where(GeocodingCache.address_hash == address_hash(ADDRESS))
        .execution_options(populate_existing=True)
    )


@pytest.mark.asyncio
async def test_align_updates_patient_and_cache_and_audits(client, db) -> None:
    admin = await _make_user(db, "ld-align@example.com", "admin")
    p = await _drifting_patient(db, "LD-ALIGN")
    pid = p.id

    res = await client.post(f"/api/v1/patients/{pid}/location/align", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["lat"] == pytest.approx(REAL_LAT)

    refreshed = await _fresh_patient(db, pid)
    assert float(refreshed.lat) == pytest.approx(REAL_LAT)
    assert float(refreshed.lng) == pytest.approx(REAL_LNG)
    cache = await _fresh_cache(db)
    assert float(cache.lat) == pytest.approx(REAL_LAT)

    audit = await db.scalar(
        select(AuditLog).where(
            AuditLog.action == "patient_location_align", AuditLog.target_id == str(pid)
        )
    )
    assert audit is not None
    assert audit.actor_user_id == admin.id
    assert audit.before["lat"] == pytest.approx(REG_LAT)
    assert audit.before["cache"]["lat"] == pytest.approx(REG_LAT)
    assert audit.after["lat"] == pytest.approx(REAL_LAT)

    # 合わせた後はずれが無い → もう一度は 409。
    res = await client.post(f"/api/v1/patients/{pid}/location/align", headers=_bearer(admin))
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_align_without_drift_returns_409(client, db) -> None:
    admin = await _make_user(db, "ld-409@example.com", "admin")
    p = await _make_patient(db, "LD-409")
    res = await client.post(f"/api/v1/patients/{p.id}/location/align", headers=_bearer(admin))
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_revert_restores_patient_and_cache(client, db) -> None:
    admin = await _make_user(db, "ld-revert@example.com", "admin")
    p = await _drifting_patient(db, "LD-REVERT")
    pid = p.id

    # 合わせる前は戻せない。
    res = await client.post(f"/api/v1/patients/{pid}/location/revert", headers=_bearer(admin))
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "元に戻せる記録がありません"

    res = await client.post(f"/api/v1/patients/{pid}/location/align", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    res = await client.post(f"/api/v1/patients/{pid}/location/revert", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["lat"] == pytest.approx(REG_LAT)

    refreshed = await _fresh_patient(db, pid)
    assert float(refreshed.lat) == pytest.approx(REG_LAT)
    assert float(refreshed.lng) == pytest.approx(REG_LNG)
    cache = await _fresh_cache(db)
    assert float(cache.lat) == pytest.approx(REG_LAT)

    audit = await db.scalar(
        select(AuditLog).where(
            AuditLog.action == "patient_location_revert", AuditLog.target_id == str(pid)
        )
    )
    assert audit is not None
    assert audit.after["lat"] == pytest.approx(REG_LAT)

    # 続けて 2 回は戻せない。
    res = await client.post(f"/api/v1/patients/{pid}/location/revert", headers=_bearer(admin))
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "続けて元に戻すことはできません"


ADDRESS_B = "千葉県千葉市若葉区都賀4-5-6"
B_LAT, B_LNG = 35.6200000, 140.1600000


@pytest.mark.asyncio
async def test_revert_after_address_change_is_409_and_leaves_caches(client, db) -> None:
    admin = await _make_user(db, "ld-addr@example.com", "admin")
    p = await _drifting_patient(db, "LD-ADDR")
    pid = p.id
    db.add(
        GeocodingCache(
            address_hash=address_hash(ADDRESS_B), address=ADDRESS_B, lat=B_LAT, lng=B_LNG
        )
    )
    await db.commit()

    res = await client.post(f"/api/v1/patients/{pid}/location/align", headers=_bearer(admin))
    assert res.status_code == 200, res.text

    # 合わせた後に住所を B へ変えた (座標はそのまま)。
    patient = await _fresh_patient(db, pid)
    patient.address = ADDRESS_B
    await db.commit()

    res = await client.post(f"/api/v1/patients/{pid}/location/revert", headers=_bearer(admin))
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "合わせた後に位置が変わっているため、元に戻せません"

    cache_b = await db.scalar(
        select(GeocodingCache)
        .where(GeocodingCache.address_hash == address_hash(ADDRESS_B))
        .execution_options(populate_existing=True)
    )
    assert float(cache_b.lat) == pytest.approx(B_LAT)
    assert float(cache_b.lng) == pytest.approx(B_LNG)
    # A の cache も合わせた値のまま。
    assert float((await _fresh_cache(db)).lat) == pytest.approx(REAL_LAT)


@pytest.mark.asyncio
async def test_revert_after_manual_coordinate_change_is_409(client, db) -> None:
    admin = await _make_user(db, "ld-manual@example.com", "admin")
    p = await _drifting_patient(db, "LD-MANUAL")
    pid = p.id
    res = await client.post(f"/api/v1/patients/{pid}/location/align", headers=_bearer(admin))
    assert res.status_code == 200, res.text

    patient = await _fresh_patient(db, pid)
    patient.lat = 35.61
    await db.commit()

    res = await client.post(f"/api/v1/patients/{pid}/location/revert", headers=_bearer(admin))
    assert res.status_code == 409, res.text
    assert float((await _fresh_patient(db, pid)).lat) == pytest.approx(35.61)
    assert float((await _fresh_cache(db)).lat) == pytest.approx(REAL_LAT)


@pytest.mark.asyncio
async def test_revert_leaves_cache_changed_by_someone_else(client, db) -> None:
    admin = await _make_user(db, "ld-cache@example.com", "admin")
    p = await _drifting_patient(db, "LD-CACHE")
    pid = p.id
    res = await client.post(f"/api/v1/patients/{pid}/location/align", headers=_bearer(admin))
    assert res.status_code == 200, res.text

    # 同じ住所の別の方の保存などで cache が別の値になった。
    cache = await _fresh_cache(db)
    cache.lat = 35.65
    await db.commit()

    res = await client.post(f"/api/v1/patients/{pid}/location/revert", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert float((await _fresh_patient(db, pid)).lat) == pytest.approx(REG_LAT)
    assert float((await _fresh_cache(db)).lat) == pytest.approx(35.65)

    audit = await db.scalar(
        select(AuditLog).where(
            AuditLog.action == "patient_location_revert", AuditLog.target_id == str(pid)
        )
    )
    assert audit.after["cache"] == "unchanged"


@pytest.mark.asyncio
async def test_unknown_patient_404_is_japanese(client, db) -> None:
    admin = await _make_user(db, "ld-404@example.com", "admin")
    res = await client.post(f"/api/v1/patients/{uuid4()}/location/align", headers=_bearer(admin))
    assert res.status_code == 404, res.text
    assert res.json()["detail"] == "利用者様が見つかりません"


@pytest.mark.asyncio
async def test_align_without_cache_only_updates_patient(client, db) -> None:
    admin = await _make_user(db, "ld-nocache@example.com", "admin")
    p = await _drifting_patient(db, "LD-NOCACHE", with_cache=False)
    pid = p.id
    res = await client.post(f"/api/v1/patients/{pid}/location/align", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    refreshed = await _fresh_patient(db, pid)
    assert float(refreshed.lat) == pytest.approx(REAL_LAT)


@pytest.mark.asyncio
async def test_staff_gets_403(client, db) -> None:
    staff_user = await _make_user(db, "ld-staff@example.com", "staff")
    p = await _make_patient(db, "LD-RBAC")
    h = _bearer(staff_user)
    assert (
        await client.get(f"/api/v1/patients/{p.id}/location-drift", headers=h)
    ).status_code == 403
    assert (
        await client.post(f"/api/v1/patients/{p.id}/location/align", headers=h)
    ).status_code == 403
    assert (
        await client.post(f"/api/v1/patients/{p.id}/location/revert", headers=h)
    ).status_code == 403


# ---------------------------------------------------------------------------
# モニター
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_carries_location_drift_only_for_admin(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-MON")
    await _scans(db, p, staff, [1, 2, 3])
    today = NOW.astimezone(JST).date()
    v = await _make_visit(db, p, staff, today)
    # ずれの無い患者も同じ日に居る (None のまま)。
    other = await _make_patient(db, "LD-MON-OK")
    v_other = await _make_visit(db, other, staff, today)

    def _find(resp, visit_id):
        for row in resp.staff:
            for mv in row.visits:
                if mv.visit_id == visit_id:
                    return mv
        return None

    admin_view = await build_monitor(db, today, now=NOW, viewer_is_admin=True)
    mv = _find(admin_view, v.id)
    assert mv is not None
    assert mv.location_drift is not None
    assert mv.location_drift.scans == 3
    assert _find(admin_view, v_other.id).location_drift is None

    staff_view = await build_monitor(db, today, now=NOW, viewer_is_admin=False)
    assert _find(staff_view, v.id).location_drift is None


@pytest.mark.asyncio
async def test_monitor_skips_deleted_patient(db) -> None:
    staff = await _make_staff(db)
    p = await _make_patient(db, "LD-MON-DEL")
    await _scans(db, p, staff, [1, 2, 3])
    today = NOW.astimezone(JST).date()
    v = await _make_visit(db, p, staff, today)
    p.deleted_at = NOW
    await db.commit()

    resp = await build_monitor(db, today, now=NOW, viewer_is_admin=True)
    found = [mv for row in resp.staff for mv in row.visits if mv.visit_id == v.id]
    assert all(mv.location_drift is None for mv in found)


# ---------------------------------------------------------------------------
# JST の日の境目
# ---------------------------------------------------------------------------


def _at(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=JST).astimezone(UTC)


def test_days_counted_in_jst_across_midnight() -> None:
    # JST 23:50 と翌 00:10 は UTC では同じ日 (14:50 / 15:10) だが、JST では 2 日。
    scans = [
        (REAL_LAT, REAL_LNG, _at(2026, 10, 10, 23, 50)),
        (REAL_LAT, REAL_LNG, _at(2026, 10, 10, 23, 55)),
        (REAL_LAT, REAL_LNG, _at(2026, 10, 11, 0, 10)),
    ]
    assert len({s[2].date() for s in scans}) == 1
    drift = compute_drift(scans, REG_LAT, REG_LNG, 100)
    assert drift is not None
    assert drift.days == 2


def test_same_jst_day_across_utc_midnight_is_one_day() -> None:
    # JST 08:50 と 09:10 は UTC では別の日 (前日 23:50 / 00:10) だが、JST では 1 日。
    scans = [
        (REAL_LAT, REAL_LNG, _at(2026, 10, 11, 8, 50)),
        (REAL_LAT, REAL_LNG, _at(2026, 10, 11, 8, 55)),
        (REAL_LAT, REAL_LNG, _at(2026, 10, 11, 9, 10)),
    ]
    assert len({s[2].date() for s in scans}) == 2
    assert compute_drift(scans, REG_LAT, REG_LNG, 100) is None
