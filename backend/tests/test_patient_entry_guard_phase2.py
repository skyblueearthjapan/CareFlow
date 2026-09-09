"""患者ステータス連動 Phase 2 — 入口ガードのエンドポイント結線テスト.

正典 = ``docs/plans/patient-status-schedule-design-2026-09-09.md`` §3-3 / §7-3(d)。
Phase 1 で契約だけ固定した ``ensure_patient_schedulable`` /
``split_schedulable_patient_ids`` を実際のハンドラへ結線したことを検証する。

検証の軸:
    A. 単一患者経路 = 非稼働なら 422 ``code='patient_not_active'`` /
       稼働中なら素通り (ガードで落ちない)。
    B. 一括系 (pool-overview / pool-bulk-simulate / pool-bulk-apply) =
       **拒否ではなく除外** して ``excluded_patients`` に載せる。
    C. ``PUT /patients/{id}/fixed-visits`` = 型だけの編集は非稼働でも許可し、
       ``change_scope=pattern_and_week`` だけ 422 (+``allowed_scope``)。
    D. ⭐ プール / カレンダーは非稼働でも **除外しない** (``patient_status`` を載せる)。
    E. カイポケ取込 = 非稼働患者の add をプレビューで自動選択せず、適用でも skip。

ローカル SQLite のみ (本番 DB 禁止)。
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.special_visit import SpecialVisitMark, SpecialVisitPeriod
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit

# 過去日ガードを踏まないよう、基準週は常に「次の月曜から始まる未来週」にする。
_TODAY_JST = datetime.now(UTC).astimezone(ZoneInfo("Asia/Tokyo")).date()
WEEK_MONDAY = _TODAY_JST + timedelta(days=7 - _TODAY_JST.weekday())
ISO_YEAR, ISO_WEEK, _ = WEEK_MONDAY.isocalendar()

# 非稼働として代表させる status (メッセージ / ラベルは Phase 1 のテストで網羅済み)。
INACTIVE = "admitted"
INACTIVE_LABEL = "入院中"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, *, name: str = "稲毛") -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


async def _make_patient(
    db,
    *,
    code: str,
    status: str = "active",
    office: Office | None = None,
    name: str | None = None,
) -> Patient:
    patient = Patient(
        code=code,
        name=name or f"患者{code}",
        status=status,
        primary_office_id=office.id if office is not None else None,
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


async def _make_template(db, *, office: Office, label: str = "A") -> CourseTemplate:
    tpl = CourseTemplate(
        office_id=office.id,
        label=label,
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


def _assert_not_active_detail(res, patient: Patient, **extra) -> dict:
    """422 の形 (FE 契約) をまとめて検証する."""
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "patient_not_active"
    assert detail["patient_id"] == str(patient.id)
    assert detail["status"] == INACTIVE
    assert detail["status_label"] == INACTIVE_LABEL
    assert detail["can_override"] is True
    assert INACTIVE_LABEL in detail["message"]
    for key, value in extra.items():
        assert detail[key] == value
    return detail


def _assert_guard_passed(res) -> None:
    """入口ガードを素通りしたことだけを確かめる (下流の業務エラーは許容).

    「稼働中なら入口で止まらない」ことが検証したい性質で、下流が 422 を返す
    ケース (退避できる固定訪問が無い等) まで作り込むのは本テストの関心外。
    """
    if res.status_code != 422:
        return
    detail = res.json()["detail"]
    assert not (isinstance(detail, dict) and detail.get("code") == "patient_not_active"), res.text


# ---------------------------------------------------------------------------
# A-1. POST /schedule/place-and-fix
# ---------------------------------------------------------------------------


def _place_and_fix_payload(patient_id, template_id) -> dict:
    return {
        "patient_id": str(patient_id),
        "course_template_id": str(template_id),
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "weekday": 0,
        "start_time": "10:00:00",
        "duration_min": 45,
        "staff_count": 1,
        "fix_pattern": True,
    }


@pytest.mark.asyncio
async def test_place_and_fix_rejects_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-paf@example.com")
    office = await _make_office(db)
    tpl = await _make_template(db, office=office)
    patient = await _make_patient(db, code="G-PAF1", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_place_and_fix_payload(patient.id, tpl.id),
    )
    _assert_not_active_detail(res, patient)
    # 書き込み前に止まっている (visit は 1 件も作られない)。
    assert (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all() == []


@pytest.mark.asyncio
async def test_place_and_fix_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-paf-ok@example.com")
    office = await _make_office(db)
    tpl = await _make_template(db, office=office)
    patient = await _make_patient(db, code="G-PAF2", status="active", office=office)

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_place_and_fix_payload(patient.id, tpl.id),
    )
    assert res.status_code == 200, res.text
    assert res.json()["visit"]["patient_id"] == str(patient.id)


# ---------------------------------------------------------------------------
# A-2. POST /schedule/fix-or-pattern
# ---------------------------------------------------------------------------


async def _make_visit(db, patient: Patient, *, weekday: int = 0) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        visit_date=WEEK_MONDAY + timedelta(days=weekday),
        start_time=time(9, 0),
        end_time=time(9, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["this_week_only", "pattern_change"])
async def test_fix_or_pattern_rejects_non_active(client, db, mode: str) -> None:
    admin = await _make_user(db, email=f"g-fop-{mode}@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code=f"G-FOP-{mode}", status=INACTIVE, office=office)
    visit = await _make_visit(db, patient)

    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=_bearer(admin),
        json={
            "visit_id": str(visit.id),
            "mode": mode,
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "new_weekday": 1,
            "new_start_time": "11:00:00",
            "new_duration_min": 30,
        },
    )
    _assert_not_active_detail(res, patient)
    await db.refresh(visit)
    assert visit.start_time == time(9, 0)  # 触られていない


@pytest.mark.asyncio
async def test_fix_or_pattern_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-fop-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-FOP-OK", status="active", office=office)
    visit = await _make_visit(db, patient)

    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=_bearer(admin),
        json={
            "visit_id": str(visit.id),
            "mode": "this_week_only",
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "new_weekday": 1,
            "new_start_time": "11:00:00",
            "new_duration_min": 30,
        },
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# A-3. POST /visits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_visit_rejects_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-visit@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-V1", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/visits",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "visit_date": WEEK_MONDAY.isoformat(),
            "start_time": "09:00:00",
            "end_time": "09:30:00",
            "type": "regular",
        },
    )
    _assert_not_active_detail(res, patient)
    assert (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all() == []


@pytest.mark.asyncio
async def test_create_visit_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-visit-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-V2", status="active", office=office)

    res = await client.post(
        "/api/v1/visits",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "visit_date": WEEK_MONDAY.isoformat(),
            "start_time": "09:00:00",
            "end_time": "09:30:00",
            "type": "regular",
        },
    )
    assert res.status_code == 201, res.text


# ---------------------------------------------------------------------------
# A-3b. PATCH /visits/{id} — 患者付け替え + 取消の巻き戻し防止
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_visit_rejects_reassign_to_non_active_patient(client, db) -> None:
    admin = await _make_user(db, email="g-pv1@example.com")
    office = await _make_office(db)
    active = await _make_patient(db, code="G-PV1-A", status="active", office=office)
    inactive = await _make_patient(db, code="G-PV1-B", status=INACTIVE, office=office)
    visit = await _make_visit(db, active)

    res = await client.patch(
        f"/api/v1/visits/{visit.id}",
        headers=_bearer(admin),
        json={"patient_id": str(inactive.id)},
    )
    _assert_not_active_detail(res, inactive)
    await db.refresh(visit)
    assert visit.patient_id == active.id  # 付け替わっていない


@pytest.mark.asyncio
async def test_patch_visit_allows_same_patient_update_when_non_active(client, db) -> None:
    """同一患者への PATCH (メモ等) は非稼働でも通す — 残骸の手当てを塞がない."""
    admin = await _make_user(db, email="g-pv2@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-PV2", status=INACTIVE, office=office)
    visit = await _make_visit(db, patient)

    res = await client.patch(
        f"/api/v1/visits/{visit.id}",
        headers=_bearer(admin),
        json={"note": "入院中の残骸を確認"},
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["status_cancel", "manual_cancel"])
async def test_patch_visit_rejects_reviving_local_cancel(client, db, source: str) -> None:
    """らく助側の意思による取消を PATCH で planned に戻させない."""
    admin = await _make_user(db, email=f"g-pv3-{source}@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code=f"G-PV3-{source}", status=INACTIVE, office=office)
    visit = await _make_visit(db, patient)
    visit.status = "cancelled"
    visit.source = source
    await db.commit()

    res = await client.patch(
        f"/api/v1/visits/{visit.id}",
        headers=_bearer(admin),
        json={"status": "planned"},
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == "ステータス連動で取消された予定は稼働中に戻してください"
    await db.refresh(visit)
    assert visit.status == "cancelled"


@pytest.mark.asyncio
async def test_patch_visit_allows_reviving_inbound_cancel(client, db) -> None:
    """取込 delete 由来の cancelled は従来どおり戻せる (ガードの対象外)."""
    admin = await _make_user(db, email="g-pv4@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-PV4", status="active", office=office)
    visit = await _make_visit(db, patient)
    visit.status = "cancelled"
    visit.source = "import"
    await db.commit()

    res = await client.patch(
        f"/api/v1/visits/{visit.id}",
        headers=_bearer(admin),
        json={"status": "planned"},
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# A-4. POST /schedule/v2/apply-individual
# ---------------------------------------------------------------------------


def _apply_individual_body(patient_id, office_id, change_scope: str) -> dict:
    body: dict = {
        "confirm": True,
        "patient_id": str(patient_id),
        "change_scope": change_scope,
        "visit_plans": [
            {
                "weekday": 0,
                "start_time": "10:00:00",
                "end_time": "10:30:00",
                "duration_min": 30,
                "course_code": "A",
                "office_id": str(office_id),
                "am_pm": "am",
            }
        ],
    }
    if change_scope == "pattern_and_week":
        body["iso_year"] = ISO_YEAR
        body["iso_week"] = ISO_WEEK
    return body


@pytest.mark.asyncio
async def test_apply_individual_rejects_non_active_pattern_and_week(client, db) -> None:
    """PUT fixed-visits と対称: 週へ反映する経路だけ 422 (+allowed_scope)."""
    admin = await _make_user(db, email="g-ai@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-AI1", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=_bearer(admin),
        json=_apply_individual_body(patient.id, office.id, "pattern_and_week"),
    )
    _assert_not_active_detail(res, patient, allowed_scope="pattern_only")


@pytest.mark.asyncio
async def test_apply_individual_pattern_only_allowed_for_non_active(client, db) -> None:
    """型だけの更新は復帰に備えて非稼働でも通す (PUT fixed-visits と同じ)."""
    admin = await _make_user(db, email="g-ai-po@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-AI2", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=_bearer(admin),
        json=_apply_individual_body(patient.id, office.id, "pattern_only"),
    )
    assert res.status_code == 200, res.text
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_apply_individual_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-ai-act@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-AI3", status="active", office=office)

    res = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=_bearer(admin),
        json=_apply_individual_body(patient.id, office.id, "pattern_only"),
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# A-5. POST /schedule/v2/visit-move-week-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_move_week_only_rejects_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-mvw@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-MVW", status=INACTIVE, office=office)
    await _make_visit(db, patient)

    res = await client.post(
        "/api/v1/schedule/v2/visit-move-week-only",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "old_weekday": 0,
            "old_start_time": "09:00:00",
            "new_weekday": 1,
            "new_start_time": "10:00:00",
        },
    )
    _assert_not_active_detail(res, patient)


@pytest.mark.asyncio
async def test_visit_move_week_only_keeps_404_for_missing_patient(client, db) -> None:
    """ガードを前に置いても、不存在患者の 404 は従来どおり (契約を変えない)."""
    admin = await _make_user(db, email="g-mvw-404@example.com")
    res = await client.post(
        "/api/v1/schedule/v2/visit-move-week-only",
        headers=_bearer(admin),
        json={
            "patient_id": str(uuid4()),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "old_weekday": 0,
            "old_start_time": "09:00:00",
            "new_weekday": 1,
            "new_start_time": "10:00:00",
        },
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_visit_move_week_only_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-mvw-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-MVW-OK", status="active", office=office)
    await _make_visit(db, patient)

    res = await client.post(
        "/api/v1/schedule/v2/visit-move-week-only",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "old_weekday": 0,
            "old_start_time": "09:00:00",
            "new_weekday": 1,
            "new_start_time": "10:00:00",
        },
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# A-5b. POST /schedule/v2/sync-fixed-to-week (型 → 今週の再生成)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_fixed_to_week_rejects_non_active(client, db) -> None:
    """型から今週を作り直す操作 = pattern_and_week と同じ扱いで 422."""
    admin = await _make_user(db, email="g-sync@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-SYNC", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/schedule/v2/sync-fixed-to-week",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    _assert_not_active_detail(res, patient)


@pytest.mark.asyncio
async def test_sync_fixed_to_week_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-sync-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-SYNC-OK", status="active", office=office)

    res = await client.post(
        "/api/v1/schedule/v2/sync-fixed-to-week",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# A-6. POST /schedule/v2/update-fixed-time-master
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_fixed_time_master_rejects_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-uftm@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-UFTM", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-master",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "weekday": 0,
            "new_start": "10:00",
            "new_end": "10:30",
        },
    )
    _assert_not_active_detail(res, patient)
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert rows == []  # 書き込み前に止まっている


@pytest.mark.asyncio
async def test_update_fixed_time_master_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-uftm-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-UFTM-OK", status="active", office=office)

    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-master",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "weekday": 0,
            "new_start": "10:00",
            "new_end": "10:30",
        },
    )
    assert res.status_code == 200, res.text
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# A-7. POST /schedule/v2/improvement-suggestions/apply-swap (両患者)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("inactive_side", ["a", "b"])
async def test_apply_swap_rejects_non_active_on_either_side(client, db, inactive_side: str) -> None:
    admin = await _make_user(db, email=f"g-swap-{inactive_side}@example.com")
    office = await _make_office(db)
    pa = await _make_patient(
        db,
        code=f"G-SWAP-A-{inactive_side}",
        status=INACTIVE if inactive_side == "a" else "active",
        office=office,
    )
    pb = await _make_patient(
        db,
        code=f"G-SWAP-B-{inactive_side}",
        status=INACTIVE if inactive_side == "b" else "active",
        office=office,
    )

    res = await client.post(
        "/api/v1/schedule/v2/improvement-suggestions/apply-swap",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "patient_a_id": str(pa.id),
            "patient_b_id": str(pb.id),
            "a_new": {"weekday": 1, "start_time": "10:00"},
            "b_new": {"weekday": 0, "start_time": "11:00"},
        },
    )
    _assert_not_active_detail(res, pa if inactive_side == "a" else pb)


@pytest.mark.asyncio
async def test_apply_swap_allows_active_pair(client, db) -> None:
    """両者稼働中ならガードは通す (下流の 404「固定枠が無い」まで到達する)."""
    admin = await _make_user(db, email="g-swap-ok@example.com")
    office = await _make_office(db)
    pa = await _make_patient(db, code="G-SWAP-OK-A", status="active", office=office)
    pb = await _make_patient(db, code="G-SWAP-OK-B", status="active", office=office)

    res = await client.post(
        "/api/v1/schedule/v2/improvement-suggestions/apply-swap",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "patient_a_id": str(pa.id),
            "patient_b_id": str(pb.id),
            "a_new": {"weekday": 1, "start_time": "10:00"},
            "b_new": {"weekday": 0, "start_time": "11:00"},
        },
    )
    # 固定枠 (slot0) が無いので 404。patient_not_active では **ない** = ガードは素通り。
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# A-8. POST /schedule/v2/propose-slots (existing_patient_id 指定時のみ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_slots_rejects_non_active_existing_patient(client, db) -> None:
    admin = await _make_user(db, email="g-ps@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-PS1", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "existing_patient_id": str(patient.id),
            "service_minutes": 30,
        },
    )
    _assert_not_active_detail(res, patient)


@pytest.mark.asyncio
async def test_propose_slots_allows_active_existing_patient(client, db) -> None:
    admin = await _make_user(db, email="g-ps-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-PS-OK", status="active", office=office)

    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "existing_patient_id": str(patient.id),
            "service_minutes": 30,
        },
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_propose_slots_without_existing_patient_is_unguarded(client, db) -> None:
    """新規候補 (existing_patient_id=None) は患者行が無いのでガード対象外."""
    admin = await _make_user(db, email="g-ps2@example.com")
    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "service_minutes": 30},
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# A-9〜A-13. 特別訪問週間 (期間作成 / ○ 追加 / 配置 / 復元 / 退避)
# ---------------------------------------------------------------------------


async def _make_period(db, patient: Patient) -> SpecialVisitPeriod:
    period = SpecialVisitPeriod(
        patient_id=patient.id,
        start_date=WEEK_MONDAY,
        end_date=WEEK_MONDAY + timedelta(days=13),
        weekly_target=3,
        status="active",
    )
    db.add(period)
    await db.commit()
    await db.refresh(period)
    return period


async def _make_mark(
    db,
    period: SpecialVisitPeriod,
    *,
    kind: str = "extra",
    status: str = "pool",
    weekday: int = 0,
) -> SpecialVisitMark:
    mark = SpecialVisitMark(
        period_id=period.id,
        patient_id=period.patient_id,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        kind=kind,
        status=status,
    )
    db.add(mark)
    await db.commit()
    await db.refresh(mark)
    return mark


@pytest.mark.asyncio
async def test_create_period_rejects_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-svp@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-SVP", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/special-visit-periods",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "start_date": WEEK_MONDAY.isoformat(),
            "end_date": (WEEK_MONDAY + timedelta(days=13)).isoformat(),
            "weekly_target": 3,
        },
    )
    _assert_not_active_detail(res, patient)
    assert (await db.scalars(select(SpecialVisitPeriod))).all() == []


@pytest.mark.asyncio
async def test_create_extra_mark_rejects_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-mark@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-MARK", status=INACTIVE, office=office)
    period = await _make_period(db, patient)

    res = await client.post(
        f"/api/v1/special-visit-periods/{period.id}/marks",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 0},
    )
    _assert_not_active_detail(res, patient)


@pytest.mark.asyncio
async def test_displace_rejects_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-disp@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-DISP", status=INACTIVE, office=office)
    period = await _make_period(db, patient)

    res = await client.post(
        f"/api/v1/special-visit-periods/{period.id}/displace",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 0},
    )
    _assert_not_active_detail(res, patient)


@pytest.mark.asyncio
async def test_place_mark_rejects_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-place@example.com")
    office = await _make_office(db)
    tpl = await _make_template(db, office=office)
    patient = await _make_patient(db, code="G-PLACE", status=INACTIVE, office=office)
    period = await _make_period(db, patient)
    mark = await _make_mark(db, period)

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(tpl.id), "start_time": "10:00"},
    )
    _assert_not_active_detail(res, patient)
    await db.refresh(mark)
    assert mark.status == "pool"  # 配置されていない


@pytest.mark.asyncio
async def test_restore_mark_rejects_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-restore@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-RESTORE", status=INACTIVE, office=office)
    period = await _make_period(db, patient)
    mark = await _make_mark(db, period, kind="displaced")

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/restore",
        headers=_bearer(admin),
    )
    _assert_not_active_detail(res, patient)


@pytest.mark.asyncio
async def test_place_mark_cancelled_ticket_returns_409_not_422(client, db) -> None:
    """取消済みチケットは 409 が正しい (422 の「稼働中にして続ける」は行き止まり).

    ガードは競合チェックの **後**に置くこと (restore_mark と同じ順序)。
    """
    admin = await _make_user(db, email="g-place-409@example.com")
    office = await _make_office(db)
    tpl = await _make_template(db, office=office)
    patient = await _make_patient(db, code="G-PLACE409", status=INACTIVE, office=office)
    period = await _make_period(db, patient)
    mark = await _make_mark(db, period, status="cancelled")

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(tpl.id), "start_time": "10:00"},
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "取消済みのチケットです"


# --- ⭐ 稼働中の素通り (ガードで落ちないこと) --------------------------------


@pytest.mark.asyncio
async def test_create_period_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-svp-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-SVP-OK", status="active", office=office)

    res = await client.post(
        "/api/v1/special-visit-periods",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "start_date": WEEK_MONDAY.isoformat(),
            "end_date": (WEEK_MONDAY + timedelta(days=13)).isoformat(),
            "weekly_target": 3,
        },
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_create_extra_mark_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-mark-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-MARK-OK", status="active", office=office)
    period = await _make_period(db, patient)

    res = await client.post(
        f"/api/v1/special-visit-periods/{period.id}/marks",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 0},
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_displace_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-disp-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-DISP-OK", status="active", office=office)
    period = await _make_period(db, patient)

    res = await client.post(
        f"/api/v1/special-visit-periods/{period.id}/displace",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 0},
    )
    # 退避できる固定訪問が無ければ下流が 422 を返すが、入口ガードは素通りしている。
    _assert_guard_passed(res)


@pytest.mark.asyncio
async def test_place_mark_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-place-ok@example.com")
    office = await _make_office(db)
    tpl = await _make_template(db, office=office)
    patient = await _make_patient(db, code="G-PLACE-OK", status="active", office=office)
    period = await _make_period(db, patient)
    mark = await _make_mark(db, period)

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(tpl.id), "start_time": "10:00"},
    )
    assert res.status_code == 200, res.text
    await db.refresh(mark)
    assert mark.status == "placed"


@pytest.mark.asyncio
async def test_restore_mark_allows_active(client, db) -> None:
    admin = await _make_user(db, email="g-restore-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-RESTORE-OK", status="active", office=office)
    period = await _make_period(db, patient)
    mark = await _make_mark(db, period, kind="displaced")

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/restore",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    await db.refresh(mark)
    assert mark.status == "cancelled"  # 退避解除 = マークは取消扱い


# ---------------------------------------------------------------------------
# B. 一括系 = 拒否ではなく除外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_overview_excludes_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-po@example.com")
    office = await _make_office(db)
    active = await _make_patient(db, code="G-PO-A", status="active", office=office)
    inactive = await _make_patient(db, code="G-PO-B", status=INACTIVE, office=office, name="山田")

    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(active.id), str(inactive.id)],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [it["patient_id"] for it in body["items"]] == [str(active.id)]
    assert len(body["excluded_patients"]) == 1
    ex = body["excluded_patients"][0]
    assert ex == {
        "patient_id": str(inactive.id),
        "status": INACTIVE,
        "status_label": INACTIVE_LABEL,
        "message": f"山田様は{INACTIVE_LABEL}のため予定に入れられません",
    }


@pytest.mark.asyncio
async def test_pool_overview_all_excluded_returns_200(client, db) -> None:
    admin = await _make_user(db, email="g-po-all@example.com")
    office = await _make_office(db)
    inactive = await _make_patient(db, code="G-PO-ALL", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(inactive.id)],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["items"] == []
    assert [e["patient_id"] for e in body["excluded_patients"]] == [str(inactive.id)]


@pytest.mark.asyncio
async def test_pool_bulk_simulate_excludes_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-pbs@example.com")
    office = await _make_office(db)
    active = await _make_patient(db, code="G-PBS-A", status="active", office=office)
    inactive = await _make_patient(db, code="G-PBS-B", status=INACTIVE, office=office)

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(active.id), str(inactive.id)],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [e["patient_id"] for e in body["excluded_patients"]] == [str(inactive.id)]
    # 除外した患者は placements / partial / unplaced のどこにも出てこない。
    for bucket in ("placements", "partial", "unplaced"):
        assert all(row["patient_id"] != str(inactive.id) for row in body[bucket])


@pytest.mark.asyncio
async def test_pool_bulk_apply_excludes_non_active(client, db) -> None:
    """非稼働の placements は落とし、200 + excluded_patients + warnings で返す."""
    admin = await _make_user(db, email="g-pba@example.com")
    office = await _make_office(db)
    inactive = await _make_patient(db, code="G-PBA", status=INACTIVE, office=office, name="小湊")

    sim = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [],
        },
    )
    assert sim.status_code == 200, sim.text
    state_token = sim.json()["state_token"]

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-apply",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "state_token": state_token,
            "placements": [
                {
                    "seq": 1,
                    "patient_id": str(inactive.id),
                    "patient_name": inactive.name,
                    "weekday": 0,
                    "course_code": "A",
                    "office_id": str(office.id),
                    "start_time": "10:00:00",
                    "service_minutes": 30,
                    "delta_minutes": 0.0,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied_patients"] == 0
    assert body["applied_slots"] == 0
    assert [e["patient_id"] for e in body["excluded_patients"]] == [str(inactive.id)]
    assert any(INACTIVE_LABEL in w for w in body["warnings"])
    # PFV は 1 行も作られていない。
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == inactive.id)
        )
    ).all()
    assert rows == []


@pytest.mark.asyncio
async def test_pool_bulk_apply_mixed_batch_applies_active_only(client, db) -> None:
    """混在バッチ: 稼働中は適用され、非稼働だけ落ちる (全体は 200)."""
    admin = await _make_user(db, email="g-pba-mix@example.com")
    office = await _make_office(db)
    active = await _make_patient(db, code="G-PBA-MIX-A", status="active", office=office)
    inactive = await _make_patient(
        db, code="G-PBA-MIX-B", status=INACTIVE, office=office, name="小湊"
    )

    sim = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [],
        },
    )
    assert sim.status_code == 200, sim.text
    state_token = sim.json()["state_token"]

    def _placement(seq: int, patient: Patient, start: str) -> dict:
        return {
            "seq": seq,
            "patient_id": str(patient.id),
            "patient_name": patient.name,
            "weekday": 0,
            "course_code": "A",
            "office_id": str(office.id),
            "start_time": start,
            "service_minutes": 30,
            "delta_minutes": 0.0,
        }

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-apply",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "state_token": state_token,
            "placements": [
                _placement(1, active, "10:00:00"),
                _placement(2, inactive, "11:00:00"),
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied_patients"] == 1
    assert body["applied_slots"] == 1
    assert [e["patient_id"] for e in body["excluded_patients"]] == [str(inactive.id)]
    assert any(INACTIVE_LABEL in w for w in body["warnings"])

    # 稼働中だけ PFV が入り、非稼働は 1 行も入っていない。
    active_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == active.id))
    ).all()
    assert len(active_rows) == 1
    inactive_rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == inactive.id)
        )
    ).all()
    assert inactive_rows == []


# ---------------------------------------------------------------------------
# C. PUT /patients/{id}/fixed-visits — 型だけは許可・週反映だけ 422
# ---------------------------------------------------------------------------


def _fixed_visits_body(change_scope: str) -> dict:
    body: dict = {
        "mode": "normal",
        "items": [{"weekday": 0, "start_time": "10:00:00", "duration_min": 30}],
        "change_scope": change_scope,
    }
    if change_scope == "pattern_and_week":
        body["iso_year"] = ISO_YEAR
        body["iso_week"] = ISO_WEEK
    return body


@pytest.mark.asyncio
async def test_put_fixed_visits_pattern_only_allowed_for_non_active(client, db) -> None:
    """復帰 (型から作り直す) に備え、型の編集は非稼働でも通す."""
    admin = await _make_user(db, email="g-pfv-ok@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-PFV1", status=INACTIVE, office=office)

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_fixed_visits_body("pattern_only"),
    )
    assert res.status_code == 200, res.text
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_put_fixed_visits_pattern_and_week_rejected_for_non_active(client, db) -> None:
    admin = await _make_user(db, email="g-pfv-ng@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-PFV2", status=INACTIVE, office=office)

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_fixed_visits_body("pattern_and_week"),
    )
    _assert_not_active_detail(res, patient, allowed_scope="pattern_only")
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert rows == []  # 破壊的な DELETE→INSERT の前に止まっている


@pytest.mark.asyncio
async def test_put_fixed_visits_pattern_and_week_allowed_for_active(client, db) -> None:
    admin = await _make_user(db, email="g-pfv-act@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-PFV3", status="active", office=office)

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_fixed_visits_body("pattern_and_week"),
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# D. ⭐ プール / カレンダーは非稼働でも除外しない (patient_status を載せる)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_keeps_non_active_patient_and_reports_status(client, db) -> None:
    admin = await _make_user(db, email="g-svpool@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-SVPOOL", status=INACTIVE, office=office)
    period = await _make_period(db, patient)
    await _make_mark(db, period)

    res = await client.get(
        "/api/v1/special-visit-marks/pool",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    tickets = res.json()
    assert len(tickets) == 1  # 除外していない (PO 決定「残す」)
    assert tickets[0]["patient"]["id"] == str(patient.id)
    assert tickets[0]["patient"]["patient_status"] == INACTIVE


@pytest.mark.asyncio
async def test_calendar_keeps_non_active_patient_and_reports_status(client, db) -> None:
    admin = await _make_user(db, email="g-svcal@example.com")
    office = await _make_office(db)
    patient = await _make_patient(db, code="G-SVCAL", status=INACTIVE, office=office)
    period = await _make_period(db, patient)

    res = await client.get(
        f"/api/v1/special-visit-periods/{period.id}/calendar",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["patient_status"] == INACTIVE


# ---------------------------------------------------------------------------
# E. カイポケ取込 — 非稼働患者を復活させない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_sheet_does_not_auto_select_inactive_add(db) -> None:
    """プレビュー: 非稼働患者の add 行は自動選択せず、summary に件数を出す."""
    from app.api.v1.integrations import _build_inbound_sheet
    from app.services.diff.engine import Correction

    office = await _make_office(db)
    active = await _make_patient(db, code="G-IN-A", status="active", office=office, name="佐藤太郎")
    inactive = await _make_patient(
        db, code="G-IN-B", status=INACTIVE, office=office, name="鈴木花子"
    )
    staff = Staff(name="担当看護師", role="staff", primary_office_id=office.id)
    db.add(staff)
    await db.commit()

    week_start = WEEK_MONDAY

    def _add(user_name: str, start: str, end: str) -> Correction:
        return Correction(
            user_name=user_name,
            date_from=str(week_start.day),
            date_to=str(week_start.day),
            start_time_from="",
            start_time_to=start,
            end_time_from="",
            end_time_to=end,
            staff1_from="",
            staff1_to="担当看護師",
            staff2_from="",
            staff2_to="",
            service_type="訪問看護",
            action="add",
        )

    corrections = [_add("佐藤太郎", "10:00", "10:30"), _add("鈴木花子", "11:00", "11:30")]

    _sheet, summary = await _build_inbound_sheet(
        db,
        corrections=corrections,
        month=f"{week_start.year:04d}-{week_start.month:02d}",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        user_id=None,
    )
    await db.commit()

    assert summary["inactive_patient"] == 1

    from app.models.correction_sheet import CorrectionSheetItem

    rows = (await db.scalars(select(CorrectionSheetItem))).all()
    by_patient = {r.patient_id: r for r in rows}
    assert by_patient[active.id].include is True
    assert by_patient[inactive.id].include is False


@pytest.mark.asyncio
async def test_apply_inbound_skips_inactive_add(db) -> None:
    """適用: 人が明示的に ON にしても、非稼働患者の add は skip する (二重の蓋)."""
    from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
    from app.services.kaipoke.inbound import apply_inbound_items

    office = await _make_office(db)
    patient = await _make_patient(db, code="G-INAPP", status=INACTIVE, office=office, name="鈴木")

    week_start = WEEK_MONDAY
    sheet = CorrectionSheet(
        target_month=f"{week_start.year:04d}-{week_start.month:02d}",
        status="ready",
        direction="inbound",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
    )
    db.add(sheet)
    await db.flush()
    item = CorrectionSheetItem(
        sheet_id=sheet.id,
        patient_id=patient.id,
        action="add",
        before={},
        after={
            "date": str(week_start.day),
            "start_time": "10:00",
            "end_time": "10:30",
            "staff1": "担当看護師",
        },
        include=True,
    )
    db.add(item)
    await db.commit()

    summary = await apply_inbound_items(
        db,
        items=[item],
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        days=None,
        dry_run=True,
        now=datetime.now(UTC),
    )

    assert summary.added == 0
    assert summary.skipped == 1
    assert [r.reason for r in summary.results] == ["inactive_patient"]
    assert (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all() == []


@pytest.mark.asyncio
async def test_replace_inbound_skips_inactive_patient(db) -> None:
    """置換取込: 非稼働患者の行は挿入せず skipped (code='inactive_patient') に積む."""
    from app.services.diff.engine import ScheduleEntry
    from app.services.kaipoke.replace_inbound import replace_week_from_kaipoke

    office = await _make_office(db)
    await _make_patient(db, code="G-RIN", status=INACTIVE, office=office, name="鈴木花子")
    staff = Staff(name="担当看護師", role="staff", primary_office_id=office.id)
    db.add(staff)
    await db.commit()

    week_start = WEEK_MONDAY
    entry = ScheduleEntry(
        user_name="鈴木花子",
        date=str(week_start.day),
        weekday="月",
        business_type="医療保険",
        service_type="訪問看護",
        start_time="10:00",
        end_time="10:30",
        staff1_name="担当看護師",
        staff1_type="看護師",
    )

    result = await replace_week_from_kaipoke(
        db,
        week_start=week_start,
        entries=[entry],
        dry_run=True,
        now=datetime.now(UTC),
    )

    assert result.inserted == 0
    assert [s.code for s in result.skipped] == ["inactive_patient"]
    assert INACTIVE_LABEL in result.skipped[0].reason
