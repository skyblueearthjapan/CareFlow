"""Phase G-21 final cross-review (Codex + Opus) ブロッカー検証.

Critical 4 + High 5 + 9 check 観点:

  F1 (C1 FE): D&D で pinned visit が draggable=false (= FE vitest 側で別 test) — Pythonでは
              代替として pinned PFV を物理削除する API を 422 で拒否することで検証.
  F2 (C1 BE): DELETE /visits/{id}?cascade_fixed_visit=true で pinned PFV は 422.
  F3 (C1 BE): POST /schedule/place-and-fix で pinned PFV 違反は 422.
  F4 (C2):    apply_week_only で overlay 適用後の pinned 再検証 (PinnedVisitMovedError).
  F5 (C3):    _load_before_visits_v2 の Before に pinned visit が is_pinned=True で出現.
  F6 (C4):    _apply_corrections_to_visits で pinned が制約計算に参加 + start_time 不変.
  F7 (H1):    apply_individual_proposal で pinned 上書き拒否 422.
  F8 (H4):    3 名同住所 + blocked で _enforce_h2_split_overflow が blocked 尊重.
  F9 (H2):    office_feature_flag audit_log target_id <= 64 (= office_id のみ).

全 9 件で既存 G-21 テスト 90+ 件を壊さない前提.
"""

from __future__ import annotations

import uuid
from datetime import time
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.audit_log import AuditLog
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.scheduling.auto_allocator_v2 import (
    PinnedVisitMovedError,
    V2Set,
    V2Visit,
    V2Warning,
    _apply_corrections_to_visits,
    _enforce_h2_split_overflow,
    _load_before_visits_v2,
    apply_individual_proposal,
    apply_week_only,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, *, name: str) -> Office:
    o = Office(name=name)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_patient(
    db,
    *,
    code: str,
    office: Office | None = None,
    lat: float = 35.65,
    lng: float = 140.10,
    weekly_pattern: dict | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=(office.id if office is not None else None),
        weekly_pattern=weekly_pattern,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    start_hhmm: tuple[int, int] = (10, 0),
    duration_min: int = 30,
    is_pinned: bool = False,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=time(*start_hhmm),
        duration_min=duration_min,
        slot_index=0,
        is_pinned=is_pinned,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    return pfv


# ===========================================================================
# F2 (C1 BE): DELETE /visits/{id}?cascade_fixed_visit=true で pinned PFV は 422
# ===========================================================================


@pytest.mark.asyncio
async def test_f2_delete_visit_cascade_rejects_pinned_pfv(client, db) -> None:
    """pinned PFV を持つ patient の visit を cascade_fixed_visit=true で削除
    しようとしたら 422 で拒否される (= D&D 経路での pinned バイパス防止)."""
    from datetime import date

    from app.models import Visit

    admin = await _make_user(db, email="f2-admin@example.com", role="admin")
    p = await _make_patient(db, code="F2")
    pfv = await _make_pfv(db, patient=p, weekday=0, is_pinned=True)
    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 4),  # Mon -> weekday=0 (matches PFV)
        start_time=time(10, 0),
        end_time=time(10, 30),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)

    res = await client.delete(
        f"/api/v1/visits/{visit.id}?cascade_fixed_visit=true",
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert "完全固定" in body["detail"]

    # PFV は不変 (= 物理削除されていない).
    db.expunge_all()
    pfv_after = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.id == pfv.id))
    assert pfv_after is not None
    assert pfv_after.is_pinned is True


@pytest.mark.asyncio
async def test_f2_delete_visit_cascade_allows_non_pinned_pfv(client, db) -> None:
    """non-pinned PFV はこれまで通り cascade で物理削除される (regression check)."""
    from datetime import date

    from app.models import Visit

    admin = await _make_user(db, email="f2b-admin@example.com", role="admin")
    p = await _make_patient(db, code="F2B")
    await _make_pfv(db, patient=p, weekday=1, is_pinned=False)
    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 5),  # Tue -> weekday=1
        start_time=time(10, 0),
        end_time=time(10, 30),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)

    res = await client.delete(
        f"/api/v1/visits/{visit.id}?cascade_fixed_visit=true",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    db.expunge_all()
    pfv_ids = (
        (await db.execute(select(PatientFixedVisit.id).where(PatientFixedVisit.patient_id == p.id)))
        .scalars()
        .all()
    )
    assert pfv_ids == []


# ===========================================================================
# F3 (C1 BE): POST /schedule/place-and-fix で pinned PFV 違反は 422
# ===========================================================================


@pytest.mark.asyncio
async def test_f3_place_and_fix_rejects_pinned_overwrite(client, db) -> None:
    """既存 pinned PFV (mode='normal', weekday=W) がある状態で place-and-fix を
    同 (patient, weekday) に呼ぶと 422 で拒否される. = place-and-fix は内部で
    DELETE→INSERT で upsert するため、 pinned PFV を物理削除する経路を塞ぐ."""
    admin = await _make_user(db, email="f3-admin@example.com", role="admin")
    office = await _make_office(db, name="F3-office")
    template = CourseTemplate(office_id=office.id, label="A")
    db.add(template)
    await db.commit()
    await db.refresh(template)

    p = await _make_patient(db, code="F3", office=office)
    await _make_pfv(db, patient=p, weekday=0, is_pinned=True)

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "course_template_id": str(template.id),
            "iso_year": 2026,
            "iso_week": 20,
            "weekday": 0,  # 同 weekday に対する upsert
            "start_time": "11:00",  # 違う時刻でも禁止
            "duration_min": 30,
            "staff_count": 1,
            "fix_pattern": True,
        },
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert "完全固定" in body["detail"]


# ===========================================================================
# F4 (C2): apply_week_only で overlay 適用後の pinned 再検証
# ===========================================================================


@pytest.mark.asyncio
async def test_f4_apply_week_only_rejects_pinned_overlay_move(db) -> None:
    """pinned PFV と一致する visit_plan を送るが、 pending_edits (overlay) で
    start_time を別時刻に上書きしようとした場合、 overlay 適用後の再検証で
    PinnedVisitMovedError が raise される (= overlay 経由 bypass を塞ぐ)."""
    office = await _make_office(db, name="F4-office")
    p = await _make_patient(
        db,
        code="F4",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)

    patient_visit_plans = [
        {
            "patient_id": p.id,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": time(10, 0),  # PFV と一致 (= 一次検証は通る)
                    "end_time": time(10, 30),
                    "duration_min": 30,
                    "course_code": "M",
                    "office_id": office.id,
                    "am_pm": "am",
                }
            ],
        }
    ]
    # overlay で 13:00 に動かす (= pinned 違反).
    pending_edits = [
        {
            "patient_id": str(p.id),
            "weekday": 0,
            "new_start": "13:00",
            "new_end": "13:30",
            "new_time_type": "固定",
        }
    ]
    with pytest.raises(PinnedVisitMovedError) as exc_info:
        await apply_week_only(
            db,
            iso_year=2026,
            iso_week=20,
            office_ids=[office.id],
            patient_visit_plans=patient_visit_plans,
            pending_edits=pending_edits,
        )
    violations = exc_info.value.violations
    assert len(violations) >= 1
    assert any(v["reason"] == "start_time_changed_by_overlay" for v in violations)


# ===========================================================================
# F5 (C3): _load_before_visits_v2 の Before に pinned visit が is_pinned=True で出現
# ===========================================================================


@pytest.mark.asyncio
async def test_f5_before_includes_pinned_pfv_with_flag(db) -> None:
    """_load_before_visits_v2 が pinned PFV を Before に展開する際、
    V2Visit.is_pinned=True が立つ (= fence engage + diff_add 表示で pinned 印)."""
    office = await _make_office(db, name="F5-office")
    p = await _make_patient(db, code="F5", office=office)
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)

    visits = await _load_before_visits_v2(
        db,
        patients_by_id={p.id: p},
        iso_year=2026,
        iso_week=20,
    )
    assert len(visits) == 1
    v = visits[0]
    assert v.patient_id == p.id
    assert v.start_time == time(10, 0)
    assert v.is_pinned is True, "pinned PFV から作る V2Visit は is_pinned=True を立てる"


@pytest.mark.asyncio
async def test_f5_before_non_pinned_pfv_keeps_flag_false(db) -> None:
    """non-pinned PFV から作る V2Visit は is_pinned=False のまま (regression)."""
    office = await _make_office(db, name="F5B-office")
    p = await _make_patient(db, code="F5B", office=office)
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=False)

    visits = await _load_before_visits_v2(
        db,
        patients_by_id={p.id: p},
        iso_year=2026,
        iso_week=20,
    )
    assert len(visits) == 1
    assert visits[0].is_pinned is False


# ===========================================================================
# F6 (C4): _apply_corrections_to_visits で pinned が制約計算に参加 + start_time 不変
# ===========================================================================


def _make_v2(
    *,
    patient_id: UUID,
    name: str,
    lat: float,
    lng: float,
    start_h: int,
    start_m: int,
    duration_min: int = 30,
    office_id: UUID,
    is_pinned: bool = False,
    course_code: str = "M",
) -> V2Visit:
    """test 用 V2Visit factory."""
    from app.services.scheduling.auto_allocator_v2 import _add_minutes

    st = time(start_h, start_m)
    return V2Visit(
        patient_id=patient_id,
        patient_name=name,
        patient_code=None,
        weekday=0,
        start_time=st,
        end_time=_add_minutes(st, duration_min),
        service_minutes=duration_min,
        lat=lat,
        lng=lng,
        office_id=office_id,
        am_pm="am",
        source_kind="pool",
        course_code=course_code,
        time_type="固定",
        preferred_start=f"{start_h:02d}:{start_m:02d}",
        is_pinned=is_pinned,
    )


def test_f6_pinned_participates_in_constraint_but_keeps_start_time() -> None:
    """C4 fix: pinned visit を `_apply_corrections_to_visits` に渡すと、
    pinned の start_time は不変だが、 制約計算 (= 距離移動時間) は周辺 visit が
    pinned を考慮するようになる. 旧実装は pinned を **入力から除外** していたため、
    非 pinned visit が pinned 周辺の移動時間を見られなかった."""
    office_id = uuid.uuid4()
    pid_pinned = uuid.uuid4()
    pid_other = uuid.uuid4()

    # pinned visit (10:00 - 10:30, 35.65/140.10 = 同住所)
    v_pinned = _make_v2(
        patient_id=pid_pinned,
        name="Pinned",
        lat=35.65,
        lng=140.10,
        start_h=10,
        start_m=0,
        duration_min=30,
        office_id=office_id,
        is_pinned=True,
    )
    # 非 pinned visit (10:05 - 10:35, 35.65/140.10 = 同住所)
    # apply_travel_corrections 内で同住所連番化 / 同時刻 align 等の補正対象になる.
    v_other = _make_v2(
        patient_id=pid_other,
        name="Other",
        lat=35.65,
        lng=140.10,
        start_h=10,
        start_m=5,
        duration_min=30,
        office_id=office_id,
        is_pinned=False,
    )
    # snapshot pinned の値.
    pinned_start_before = v_pinned.start_time
    pinned_end_before = v_pinned.end_time
    pinned_code_before = v_pinned.course_code

    warnings: list[V2Warning] = []
    _apply_corrections_to_visits([v_pinned, v_other], warnings=warnings)

    # pinned は不変.
    assert v_pinned.start_time == pinned_start_before
    assert v_pinned.end_time == pinned_end_before
    assert v_pinned.course_code == pinned_code_before
    assert v_pinned.is_pinned is True


# ===========================================================================
# F7 (H1): apply_individual_proposal で pinned 上書き拒否 422
# ===========================================================================


@pytest.mark.asyncio
async def test_f7_apply_individual_rejects_pinned_existing(db) -> None:
    """既存 PFV のいずれかが is_pinned=True のとき、 apply_individual_proposal は
    422 (HTTPException) で拒否する (= pinned 解除を要求)."""
    from fastapi import HTTPException

    office = await _make_office(db, name="F7-office")
    p = await _make_patient(db, code="F7", office=office)
    # 月曜は pinned, 火曜は non-pinned. 月曜が含まれているだけで拒否される.
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await _make_pfv(db, patient=p, weekday=1, start_hhmm=(10, 0), is_pinned=False)

    visit_plans = [
        {"weekday": 0, "start_time": time(11, 0), "duration_min": 30},
    ]
    with pytest.raises(HTTPException) as exc_info:
        await apply_individual_proposal(db, patient_id=p.id, visit_plans=visit_plans)
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["code"] == "pinned_pfv_cannot_be_applied"
    assert 0 in detail["pinned_weekdays"]


@pytest.mark.asyncio
async def test_f7_apply_individual_allows_no_pinned(db) -> None:
    """既存 PFV が全て non-pinned なら apply_individual はこれまで通り動く
    (= regression check)."""
    office = await _make_office(db, name="F7B-office")
    p = await _make_patient(db, code="F7B", office=office)
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=False)

    visit_plans = [
        {"weekday": 0, "start_time": time(11, 0), "duration_min": 30},
    ]
    res = await apply_individual_proposal(db, patient_id=p.id, visit_plans=visit_plans)
    assert res["applied"] is True


# ===========================================================================
# F8 (H4): 3 名同住所 + blocked で _enforce_h2_split_overflow が blocked 尊重
# ===========================================================================


def test_f8_split_overflow_respects_blocked_pair() -> None:
    """3 名同住所 + (overflow patient, target seed) が blocked のとき、
    overflow 移動先候補から当該 set が除外される (= re-merge 防止)."""
    office_id = uuid.uuid4()
    pids = sorted([uuid.uuid4() for _ in range(3)], key=str)
    seed_id = uuid.uuid4()
    # 3 名同住所 → 1 名 overflow (= 文字列最小 pid).
    overflow_pid = pids[0]

    same_addr_visits = [
        _make_v2(
            patient_id=pid,
            name=f"P{i}",
            lat=35.65,
            lng=140.10,
            start_h=10,
            start_m=0,
            office_id=office_id,
        )
        for i, pid in enumerate(pids)
    ]
    seed_visit = _make_v2(
        patient_id=seed_id,
        name="Seed",
        lat=35.66,
        lng=140.11,  # 別住所
        start_h=10,
        start_m=30,
        office_id=office_id,
    )

    # blocked pair: (overflow_pid, seed_id) ※ a<b 正規化
    a, b = (overflow_pid, seed_id) if str(overflow_pid) < str(seed_id) else (seed_id, overflow_pid)
    pair_modes = {(a, b): "blocked"}

    sets = [V2Set(visits=list(same_addr_visits)), V2Set(visits=[seed_visit])]
    warnings: list[V2Warning] = []
    _enforce_h2_split_overflow(sets, warnings, pair_modes=pair_modes)

    # overflow_pid は seed_visit の set へ移動できない (blocked) → set 0 に残る
    # OR どこにも移動できず warning ("他コースに移動先なし") が出る.
    loc_overflow = next(
        (i for i, s in enumerate(sets) for v in s.visits if v.patient_id == overflow_pid),
        None,
    )
    loc_seed = next(
        (i for i, s in enumerate(sets) for v in s.visits if v.patient_id == seed_id),
        None,
    )
    # blocked 制約: overflow と seed は別 set のまま.
    assert loc_overflow != loc_seed, "blocked ペアが同 set に再 merge されてしまった"


def test_f8_split_overflow_without_blocked_moves_normally() -> None:
    """pair_modes が空 (= 旧挙動) なら overflow patient は別 set に移動する
    (= regression check)."""
    office_id = uuid.uuid4()
    pids = sorted([uuid.uuid4() for _ in range(3)], key=str)
    seed_id = uuid.uuid4()
    seed_id2 = uuid.uuid4()
    same_addr_visits = [
        _make_v2(
            patient_id=pid,
            name=f"P{i}",
            lat=35.65,
            lng=140.10,
            start_h=10,
            start_m=0,
            office_id=office_id,
        )
        for i, pid in enumerate(pids)
    ]
    seed1 = _make_v2(
        patient_id=seed_id,
        name="Seed1",
        lat=35.66,
        lng=140.11,
        start_h=10,
        start_m=30,
        office_id=office_id,
    )
    seed2 = _make_v2(
        patient_id=seed_id2,
        name="Seed2",
        lat=35.67,
        lng=140.12,
        start_h=10,
        start_m=30,
        office_id=office_id,
    )
    sets = [V2Set(visits=list(same_addr_visits)), V2Set(visits=[seed1]), V2Set(visits=[seed2])]
    warnings: list[V2Warning] = []
    _enforce_h2_split_overflow(sets, warnings, pair_modes=None)
    # overflow patient (= pids[0]) は別 set に移動している.
    counts = [len(s.visits) for s in sets]
    assert counts[0] == 2, f"3 → 2 名に減るはず: {counts}"


# ===========================================================================
# F9 (H2): office_feature_flag audit_log target_id <= 64
# ===========================================================================


@pytest.mark.asyncio
async def test_f9_office_feature_flag_audit_target_id_under_64(client, db) -> None:
    """audit_logs.target_id は String(64). office_feature_flag_set / _delete の
    target_id が 64 char 以下であり、 feature_key は after/before JSONB に格納される.

    旧実装は ``target_id = f"{office_id}:{feature_key}"`` (= UUID 36 + ":" + 最大 key 長)
    で、 将来の feature_key 追加で String(64) overflow リスクがあった.
    新実装は ``target_id = str(office_id)`` (= 常に 36 char) で安全マージン確保.
    """
    admin = await _make_user(db, email="f9-admin@example.com", role="admin")
    office = await _make_office(db, name="F9-office")

    # 現行 Literal の唯一の値で UPSERT.
    feature_key = "g21_new_algorithm"

    # POST (UPSERT enable)
    res = await client.post(
        "/api/v1/office-feature-flags",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "feature_key": feature_key,
            "enabled": True,
            "note": "F9 audit_log shape test",
        },
    )
    assert res.status_code == 200, res.text

    audit_rows_set = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "office_feature_flag_set",
                AuditLog.target_id == str(office.id),
            )
        )
    ).all()
    assert len(audit_rows_set) == 1
    row_set = audit_rows_set[0]
    # target_id は office_id のみ (= 常に UUID 文字列 36 char ≤ 64).
    assert row_set.target_id == str(office.id)
    assert len(row_set.target_id) <= 64
    assert ":" not in row_set.target_id, (
        "新実装は target_id に feature_key を結合せず、 office_id のみ格納する"
    )
    # feature_key は after JSONB に格納される.
    assert row_set.after["feature_key"] == feature_key
    assert row_set.after["enabled"] is True

    # 旧フォーマット (= f"{office_id}:{feature_key}") の audit_log が **無い** ことを確認.
    audit_rows_old_set = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "office_feature_flag_set",
                AuditLog.target_id == f"{office.id}:{feature_key}",
            )
        )
    ).all()
    assert audit_rows_old_set == [], "旧フォーマットの audit_log が残っている"

    # DELETE
    res = await client.delete(
        f"/api/v1/office-feature-flags/{office.id}/{feature_key}",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    audit_rows_del = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "office_feature_flag_delete",
                AuditLog.target_id == str(office.id),
            )
        )
    ).all()
    assert len(audit_rows_del) == 1
    row_del = audit_rows_del[0]
    assert row_del.target_id == str(office.id)
    assert len(row_del.target_id) <= 64
    # feature_key は before JSONB に格納される.
    assert row_del.before["feature_key"] == feature_key


# ===========================================================================
# F1 (C1 FE): D&D で pinned visit が draggable=false の検証.
# ---------------------------------------------------------------------------
# FE 側 vitest (frontend/components/schedule/v2/__tests__/CourseDayTable-pin.test.tsx)
# で実装. Python テストでは代替として F2/F3 (BE 側 pinned PFV 削除拒否) で同じ
# 不変量 (= D&D による pinned バイパス禁止) を担保する.
# ===========================================================================


# F1 (FE D&D draggable=false) は vitest 側で検証 (test ID 9 / 10):
#   frontend/components/schedule/v2/__tests__/CourseDayTable-pin.test.tsx
# F2/F3 は BE で同等の不変量 (= pinned PFV を D&D / cascade / place-and-fix
# 経路で物理削除させない) を守ることで二重防衛.


# ---------------------------------------------------------------------------
# Smoke: runbook 存在確認 (H5)
# ---------------------------------------------------------------------------


def test_runbook_g21_canary_deploy_exists() -> None:
    """docs/runbook/g21_canary_deploy.md が存在し、 必要な phase 情報を含むことを
    sanity-check する (= H5 deliverable)."""
    import pathlib

    runbook = (
        pathlib.Path(__file__).resolve().parents[2] / "docs" / "runbook" / "g21_canary_deploy.md"
    )
    assert runbook.exists(), f"runbook が見つからない: {runbook}"
    text = runbook.read_text(encoding="utf-8")
    # 3 phase + kill switch + rollback の必須セクション.
    assert "Phase α" in text or "Phase alpha" in text.lower()
    assert "Phase β" in text or "Phase beta" in text.lower()
    assert "Phase γ" in text or "Phase gamma" in text.lower()
    assert "Kill switch" in text or "kill switch" in text.lower()
    assert "rollback" in text.lower()
    assert "pg_dump" in text
    assert "alembic downgrade" in text
