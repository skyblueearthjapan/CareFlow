"""Tests for POST /api/v1/schedule/v2/propose-slots (Phase2-2).

提案 API: 候補患者の希望から、対象週 × 拠点の実スケジュールの実現可能な
空き枠を算出・ランキングして返す read-only endpoint.

検証内容:
    - 実現可能枠のみ返る / 実現不能 (満杯コース・18時際・午前希望×午後コース) が除外.
    - ランキング順 (近接 + 余裕で降順).
    - 同住所ペア成立が最優先.
    - travel_time_shortage 警告の付与.
    - address→geocode 経路 (geocode_address をモック).
    - 0 件時のメッセージ.
    - 認証 (staff 403 / no-auth 401).

ローカル SQLite のみ (本番 DB 禁止).

座標メモ (千葉市近辺, test_proposal_solver.py と整合):
    BASE   = (35.6000, 140.1000)
    NEAR   = (35.6010, 140.1010)  → BASE から ~0.14km (近接)
    FAR    = (35.6300, 140.1400)  → BASE から ~4.5km (遠い)
    SAME   = (35.60005, 140.10005) → BASE と同住所 (<=100m)
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)
SAME = (35.60005, 140.10005)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_office_staff(
    db, *, name: str = "稲", code: str | None = "INAGE"
) -> tuple[Office, Staff]:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    staff = Staff(name="担当看護師", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    return office, staff


async def _seed_patient(
    db,
    *,
    office: Office,
    code: str,
    lat: float,
    lng: float,
    name: str | None = None,
    sex_restriction: str | None = None,
    requires_multiple_staff: bool = False,
) -> Patient:
    p = Patient(
        code=code,
        name=name or f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        sex_restriction=sex_restriction,
        requires_multiple_staff=requires_multiple_staff,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_course(
    db, *, office: Office, staff: Staff | None, weekday: int = 0, code: str = "A"
) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit(
    db,
    *,
    patient: Patient,
    course: Course,
    start: time,
    end: time,
    weekday_offset: int = 0,
) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        visit_date=WEEK_MONDAY,
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
    )
    db.add(visit)
    await db.flush()
    return visit


def _base_payload(office: Office, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "lat": BASE[0],
        "lng": BASE[1],
        "service_minutes": 30,
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_ids": [str(office.id)],
        "preferred_weekdays": ["Mon"],
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# 実現可能枠の算出 / ランキング
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_returns_feasible_slots_ranked(client, db) -> None:
    admin = await _make_user(db, email="ps-admin1@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    # 既存訪問 1 件 (09:30-10:00, NEAR). ③ 表示統一の色分け検証用に性別制限を付与.
    pn = await _seed_patient(
        db,
        office=office,
        code="EX1",
        lat=NEAR[0],
        lng=NEAR[1],
        sex_restriction="female_only",
    )
    await _seed_visit(db, patient=pn, course=course, start=time(9, 30), end=time(10, 0))
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"], body
    # すべて実現可能 (start < end, 18:00 以内).
    for s in body["slots"]:
        assert s["start_time"] < s["end_time"]
        assert s["end_time"] <= "18:00"
        assert s["weekday_code"] == "Mon"
        assert s["course_code"] == "A"
        assert s["course_label"] == "稲A"
        assert s["staff_name"] == "担当看護師"
    # スコア降順.
    scores = [s["score"] for s in body["slots"]]
    assert scores == sorted(scores, reverse=True)
    # mini_schedule に既存訪問 + 提案枠 (is_here) が含まれる.
    top = body["slots"][0]
    assert any(e["is_here"] for e in top["mini_schedule"])
    assert any(e["name"] == "P-EX1" for e in top["mini_schedule"])
    # ③ 表示統一: mini_schedule の各行に色分け用フィールドが含まれ、 既存患者 P-EX1 の
    # 性別制限 (female_only) が伝播する (通常リストと同じ色分けを FE が出せる).
    for e in top["mini_schedule"]:
        assert "sex_restriction" in e
        assert "is_multi_staff" in e
    ex1_row = next(e for e in top["mini_schedule"] if e["name"] == "P-EX1")
    assert ex1_row["sex_restriction"] == "female_only"


@pytest.mark.asyncio
async def test_propose_course_label_uses_office_code_tsuga(client, db) -> None:
    """course_label は office_code 基準の正準短縮 (TSUGA→津) で「津A」になる.

    拠点名先頭 1 字ヒューリスティック (都賀→「都」) ではなく
    ``OFFICE_CODE_TO_SHORT`` 経由で board / 患者 Excel と同一短縮へ統一する.
    """
    admin = await _make_user(db, email="ps-admin-tsuga@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="都賀", code="TSUGA")
    course = await _seed_course(db, office=office, staff=staff)
    pn = await _seed_patient(db, office=office, code="EX1", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=pn, course=course, start=time(9, 30), end=time(10, 0))
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"], body
    for s in body["slots"]:
        assert s["course_label"] == "津A"


@pytest.mark.asyncio
async def test_propose_excludes_full_course(client, db) -> None:
    """満杯コース (6 名) には枠を出さない (容量で除外)."""
    admin = await _make_user(db, email="ps-admin2@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    # 6 件埋める (異住所で散らす). 30 分刻み 09:30 から.
    starts = [time(9, 30), time(10, 10), time(10, 50), time(13, 0), time(13, 40), time(14, 20)]
    for i, st in enumerate(starts):
        lat = BASE[0] + 0.02 * (i + 1)
        lng = BASE[1] + 0.02 * (i + 1)
        p = await _seed_patient(db, office=office, code=f"FULL{i}", lat=lat, lng=lng)
        end = time(st.hour, st.minute + 30) if st.minute < 30 else time(st.hour + 1, st.minute - 30)
        await _seed_visit(db, patient=p, course=course, start=st, end=end)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office, lat=FAR[0], lng=FAR[1]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 満杯 (6 名) + 異住所候補 → 通常スロットは出ない.
    assert body["slots"] == []
    assert body["message"] is not None
    assert "入れられる枠なし" in body["message"]


@pytest.mark.asyncio
async def test_propose_excludes_am_preference_against_pm_only_gap(client, db) -> None:
    """午前希望の候補は、午後しか空いていないコースには出ない (time_type で除外)."""
    admin = await _make_user(db, email="ps-admin3@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    # AM 枠を埋める長時間訪問 (09:30-12:00) → 午前に空きなし.
    p = await _seed_patient(db, office=office, code="AMFILL", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=p, course=course, start=time(9, 30), end=time(12, 0))
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office, time_type="午前"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 午前希望だが AM 枠は埋まっている → 実現可能枠なし.
    assert body["slots"] == []


@pytest.mark.asyncio
async def test_propose_same_address_pair_is_top_ranked(client, db) -> None:
    """同住所ペア成立は最優先 (is_pair=True, pair_partner, 移動0 理由)."""
    admin = await _make_user(db, email="ps-admin4@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    # 既存単独患者 (BASE と同住所).
    pair_p = await _seed_patient(
        db, office=office, code="PAIR", lat=BASE[0], lng=BASE[1], name="同居者A"
    )
    await _seed_visit(db, patient=pair_p, course=course, start=time(10, 0), end=time(10, 30))
    # 別住所の既存訪問も 1 件 (通常スロットも出るように).
    far_p = await _seed_patient(db, office=office, code="FAR1", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=far_p, course=course, start=time(13, 0), end=time(13, 30))
    await db.commit()

    # 候補は SAME (= BASE と同住所).
    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office, lat=SAME[0], lng=SAME[1]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"], body
    top = body["slots"][0]
    assert top["is_pair"] is True
    assert top["pair_partner"] == "同居者A"
    assert any("同住所" in r for r in top["reasons"])
    # ペアは同時刻 (10:00) に入る.
    assert top["start_time"] == "10:00"


@pytest.mark.asyncio
async def test_propose_emits_travel_time_shortage_warning(client, db) -> None:
    """固定時刻で移動が僅かに不足 → travel_time_shortage 警告付きで実現可能."""
    admin = await _make_user(db, email="ps-admin5@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    # 既存訪問 09:30-10:00 (NEAR ~0.14km → 移動 1 分 + buffer 8 = 9 分).
    # 固定 10:05 を希望 → earliest_raw = 10:09, shortage = 4 分 (< 5) → 警告付き許容.
    p = await _seed_patient(db, office=office, code="PREV", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=p, course=course, start=time(9, 30), end=time(10, 0))
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office, time_type="固定", preferred_start="10:05"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"], body
    fixed_slots = [s for s in body["slots"] if s["start_time"] == "10:05"]
    assert fixed_slots, body["slots"]
    assert any(s["warnings"] for s in fixed_slots)


# ---------------------------------------------------------------------------
# address → geocode 経路 (モック)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_geocodes_address(client, db, monkeypatch) -> None:
    admin = await _make_user(db, email="ps-admin6@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    pn = await _seed_patient(db, office=office, code="GEO1", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=pn, course=course, start=time(9, 30), end=time(10, 0))
    await db.commit()

    from app.services.geocoding.client import GeocodeResult

    async def _fake_geocode(address: str, **kwargs: Any) -> GeocodeResult:
        return GeocodeResult(
            lat=BASE[0],
            lng=BASE[1],
            formatted_address="千葉県千葉市…",
            place_id="fake",
            status="OK",
        )

    # endpoint が import している参照を差し替える.
    monkeypatch.setattr("app.api.v1.schedule_v2.geocode_address", _fake_geocode)

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json={
            "address": "千葉県千葉市中央区1-1",
            "service_minutes": 30,
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_ids": [str(office.id)],
            "preferred_weekdays": ["Mon"],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["candidate_lat"] == pytest.approx(BASE[0])
    assert body["candidate_lng"] == pytest.approx(BASE[1])
    assert body["slots"], body


@pytest.mark.asyncio
async def test_propose_no_coords_returns_empty_with_message(client, db) -> None:
    """住所も lat/lng も無い → 0 件 + メッセージ (400 にしない)."""
    admin = await _make_user(db, email="ps-admin7@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    await _seed_course(db, office=office, staff=staff)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json={
            "service_minutes": 30,
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_ids": [str(office.id)],
            "preferred_weekdays": ["Mon"],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"] == []
    assert body["message"] is not None


@pytest.mark.asyncio
async def test_propose_empty_when_no_courses(client, db) -> None:
    """対象週にコースが無い → 0 件 + 「入れられる枠なし」."""
    admin = await _make_user(db, email="ps-admin8@example.com", role="admin")
    office, _ = await _seed_office_staff(db)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"] == []
    assert "入れられる枠なし" in body["message"]


# ---------------------------------------------------------------------------
# 週N日カバレッジ (coverage)
# ---------------------------------------------------------------------------


async def _seed_open_course_with_anchor(
    db,
    *,
    office: Office,
    staff: Staff,
    weekday: int,
    code: str = "A",
) -> Course:
    """指定曜日に「近接の既存訪問 1 件だけ」の開講コースを作る (空き枠が出る状態)."""
    course = await _seed_course(db, office=office, staff=staff, weekday=weekday, code=code)
    pn = await _seed_patient(
        db, office=office, code=f"ANC{weekday}{code}", lat=NEAR[0], lng=NEAR[1]
    )
    await _seed_visit(db, patient=pn, course=course, start=time(9, 30), end=time(10, 0))
    return course


@pytest.mark.asyncio
async def test_coverage_partial_when_only_some_weekdays_open(client, db) -> None:
    """希望週3日 (Mon/Tue/Wed) で Mon/Tue のみ開講 → 3 日中 2 日 has_slot, 未充足."""
    admin = await _make_user(db, email="ps-cov1@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    # Mon(0), Tue(1) は空き枠あり / Wed(2) は開講なし.
    await _seed_open_course_with_anchor(db, office=office, staff=staff, weekday=0)
    await _seed_open_course_with_anchor(db, office=office, staff=staff, weekday=1)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office, preferred_weekdays=["Mon", "Tue", "Wed"]),
    )
    assert res.status_code == 200, res.text
    cov = res.json()["coverage"]
    assert cov is not None
    assert cov["required_days"] == 3  # len(preferred_weekdays)
    assert cov["requested_weekdays"] == [0, 1, 2]
    by_wd = {d["weekday"]: d for d in cov["per_day"]}
    assert by_wd[0]["has_slot"] is True
    assert by_wd[1]["has_slot"] is True
    assert by_wd[2]["has_slot"] is False
    assert by_wd[2]["best_slot"] is None
    assert cov["covered_days"] == 2
    assert cov["fully_covered"] is False


@pytest.mark.asyncio
async def test_coverage_fully_covered_when_all_weekdays_open(client, db) -> None:
    """希望週3日が全て開講 → covered_days==3, fully_covered=True. best_slot 付与."""
    admin = await _make_user(db, email="ps-cov2@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    for wd in (0, 1, 2):
        await _seed_open_course_with_anchor(db, office=office, staff=staff, weekday=wd)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office, preferred_weekdays=["Mon", "Tue", "Wed"]),
    )
    assert res.status_code == 200, res.text
    cov = res.json()["coverage"]
    assert cov["covered_days"] == 3
    assert cov["fully_covered"] is True
    for d in cov["per_day"]:
        assert d["has_slot"] is True
        assert d["best_slot"] is not None
        # best_slot はその曜日のスロット (weekday 一致).
        assert d["best_slot"]["weekday"] == d["weekday"]


@pytest.mark.asyncio
async def test_coverage_required_days_prefers_frequency_per_week(client, db) -> None:
    """required_days は frequency_per_week 優先 (希望曜日数より frequency が勝つ)."""
    admin = await _make_user(db, email="ps-cov3@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    await _seed_open_course_with_anchor(db, office=office, staff=staff, weekday=0)
    await _seed_open_course_with_anchor(db, office=office, staff=staff, weekday=1)
    await db.commit()

    # 希望曜日 3 日だが frequency_per_week=2 → required_days=2, covered=2 → 充足.
    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office, preferred_weekdays=["Mon", "Tue", "Wed"], frequency_per_week=2),
    )
    assert res.status_code == 200, res.text
    cov = res.json()["coverage"]
    assert cov["required_days"] == 2  # frequency_per_week 優先 (len=3 ではない).
    assert cov["covered_days"] == 2
    assert cov["fully_covered"] is True


@pytest.mark.asyncio
async def test_coverage_full_day_has_no_slot(client, db) -> None:
    """満杯曜日 (6 名) は has_slot=False, best_slot=None になる."""
    admin = await _make_user(db, email="ps-cov4@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    # Mon(0): 空き枠あり.
    await _seed_open_course_with_anchor(db, office=office, staff=staff, weekday=0)
    # Tue(1): 6 名で満杯 (異住所で散らす).
    full_course = await _seed_course(db, office=office, staff=staff, weekday=1, code="A")
    starts = [time(9, 30), time(10, 10), time(10, 50), time(13, 0), time(13, 40), time(14, 20)]
    for i, st in enumerate(starts):
        lat = BASE[0] + 0.02 * (i + 1)
        lng = BASE[1] + 0.02 * (i + 1)
        p = await _seed_patient(db, office=office, code=f"TFULL{i}", lat=lat, lng=lng)
        end = time(st.hour, st.minute + 30) if st.minute < 30 else time(st.hour + 1, st.minute - 30)
        await _seed_visit(db, patient=p, course=full_course, start=st, end=end)
    await db.commit()

    # 候補は異住所 (FAR) で同住所ペア成立を防ぎ、満杯曜日に枠が出ないようにする.
    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office, lat=FAR[0], lng=FAR[1], preferred_weekdays=["Mon", "Tue"]),
    )
    assert res.status_code == 200, res.text
    cov = res.json()["coverage"]
    by_wd = {d["weekday"]: d for d in cov["per_day"]}
    assert by_wd[0]["has_slot"] is True
    assert by_wd[1]["has_slot"] is False
    assert by_wd[1]["best_slot"] is None
    assert cov["covered_days"] == 1


@pytest.mark.asyncio
async def test_coverage_best_slot_is_top_for_that_weekday(client, db) -> None:
    """best_slot はその曜日 slots の最上位 (slots[] 内の同曜日トップと一致)."""
    admin = await _make_user(db, email="ps-cov5@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    await _seed_open_course_with_anchor(db, office=office, staff=staff, weekday=0)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json=_base_payload(office, preferred_weekdays=["Mon"]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    cov = body["coverage"]
    mon = next(d for d in cov["per_day"] if d["weekday"] == 0)
    assert mon["has_slot"] is True
    # slots[] の Mon 最上位 (= スコア降順なので最初に出る Mon).
    mon_slots = [s for s in body["slots"] if s["weekday"] == 0]
    assert mon_slots, body["slots"]
    top_mon = mon_slots[0]
    assert mon["best_slot"]["start_time"] == top_mon["start_time"]
    assert mon["best_slot"]["score"] == top_mon["score"]
    assert mon["best_slot"]["course_label"] == top_mon["course_label"]


# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="ps-staff@example.com", role="staff")
    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(staff_user),
        json={
            "lat": BASE[0],
            "lng": BASE[1],
            "service_minutes": 30,
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_propose_rejects_no_auth(client, db) -> None:
    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        json={
            "lat": BASE[0],
            "lng": BASE[1],
            "service_minutes": 30,
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 401
