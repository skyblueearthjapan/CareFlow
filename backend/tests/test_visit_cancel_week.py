"""訪問の「今週だけ取消」 — POST /api/v1/schedule/v2/visit-cancel-week

正典 = docs/plans/week-cockpit-design.md 決定 D1 / §2-2。

取消の表現は取込の delete と同一の ``visits.status='cancelled'``
(行は残る = 履歴が追える・``csv_builder`` が cancelled を除外するので
カイポケへの送信差分は delete になる)。

検証観点:
  1. planned → cancelled → planned の往復 (VisitRead を返す)
  2. ガード: 当日以前 (JST) / 打刻あり / in_progress・completed / 青ピン は 422
  3. 2 名体制ペア (visit_group_id) は一緒に切り替わる
  4. PFV (マスタ) は不変 — 憲法1
  5. RBAC: staff は 403 / 未知の visit は 404
  6. undo/redo (op_log ``cancel_visit``) が状態を往復させる
  7. POST /visits が source='manual_week' を受け付ける (D6 の前提)

日付はテストがいつ走っても成立するよう JST の「今日」から相対に取る。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User, Visit
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.schedule_op_log import ScheduleOpLog
from app.models.visit_checkin import VisitCheckin
from app.services.scheduling.layer1_expander import Layer1Expander

_URL = "/api/v1/schedule/v2/visit-cancel-week"
_JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(UTC).astimezone(_JST).date()


def _future_day() -> date:
    return _today_jst() + timedelta(days=7)


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


async def _make_patient(db, *, code: str) -> Patient:
    p = Patient(code=code, name=f"患者{code}", status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db,
    *,
    patient: Patient,
    visit_date: date | None = None,
    status_value: str = "planned",
    week_pinned: bool = False,
    group_id: uuid.UUID | None = None,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=visit_date or _future_day(),
        start_time=time(10, 0),
        end_time=time(10, 30),
        type="regular",
        status=status_value,
        source="auto",
        week_pinned=week_pinned,
        visit_group_id=group_id,
        required_staff_count=2 if group_id is not None else 1,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


# ---------------------------------------------------------------------------
# 1. 往復
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_and_restore_round_trip(client, db) -> None:
    admin = await _make_user(db, email="vcw-1@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-1")
    visit = await _make_visit(db, patient=patient)

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"visit_id": str(visit.id), "cancel": True, "reason": "ご家族の都合"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == str(visit.id)
    assert body["status"] == "cancelled"
    assert body["source"] == "manual_cancel"
    await db.refresh(visit)
    assert visit.status == "cancelled"
    # 出所に取消の印 — 取込 delete 由来の cancelled と区別する (取込の add が
    # らく助側の取消まで復活させないため)。
    assert visit.source == "manual_cancel"
    # reason は note を汚さない (現場向けの申し送りと混ざらない・戻しても残らない)
    assert visit.note is None
    # 代わりに操作ジャーナルへ残る (label + forward_payload)
    op = await db.scalar(select(ScheduleOpLog).where(ScheduleOpLog.op_kind == "cancel_visit"))
    assert op is not None
    assert "ご家族の都合" in op.label
    assert op.forward_payload.get("reason") == "ご家族の都合"

    back = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": False}
    )
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "planned"
    await db.refresh(visit)
    assert visit.status == "planned"
    assert visit.source == "manual_week"  # 戻したら週限りの手操作として残る


@pytest.mark.asyncio
async def test_restore_requires_cancelled_status(client, db) -> None:
    admin = await _make_user(db, email="vcw-2@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-2")
    visit = await _make_visit(db, patient=patient)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": False}
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# 2. ガード
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_rejects_today_and_past(client, db) -> None:
    admin = await _make_user(db, email="vcw-3@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-3")
    today_visit = await _make_visit(db, patient=patient, visit_date=_today_jst())
    past_visit = await _make_visit(db, patient=patient, visit_date=_today_jst() - timedelta(days=3))

    for v in (today_visit, past_visit):
        res = await client.post(
            _URL, headers=_bearer(admin), json={"visit_id": str(v.id), "cancel": True}
        )
        assert res.status_code == 422, res.text
        assert "当日以前" in res.json()["detail"]
        await db.refresh(v)
        assert v.status == "planned"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_status", ["in_progress", "completed"])
async def test_cancel_rejects_non_planned(client, db, bad_status: str) -> None:
    admin = await _make_user(db, email=f"vcw-4-{bad_status}@example.com", role="admin")
    patient = await _make_patient(db, code=f"VCW-4-{bad_status}")
    visit = await _make_visit(db, patient=patient, status_value=bad_status)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": True}
    )
    assert res.status_code == 422, res.text
    await db.refresh(visit)
    assert visit.status == bad_status


@pytest.mark.asyncio
async def test_cancel_rejects_checked_in_visit(client, db) -> None:
    admin = await _make_user(db, email="vcw-5@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-5")
    visit = await _make_visit(db, patient=patient)
    db.add(
        VisitCheckin(
            visit_id=visit.id,
            patient_id=patient.id,
            kind="arrival",
            scanned_at=datetime.now(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": True}
    )
    assert res.status_code == 422, res.text
    assert "打刻" in res.json()["detail"]
    await db.refresh(visit)
    assert visit.status == "planned"


@pytest.mark.asyncio
async def test_cancel_rejects_blue_pinned_visit(client, db) -> None:
    admin = await _make_user(db, email="vcw-6@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-6")
    visit = await _make_visit(db, patient=patient, week_pinned=True)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": True}
    )
    assert res.status_code == 422, res.text
    assert "青ピン" in res.json()["detail"]
    await db.refresh(visit)
    assert visit.status == "planned"


# ---------------------------------------------------------------------------
# 3. 2 名体制ペア
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_applies_to_visit_group_pair(client, db) -> None:
    admin = await _make_user(db, email="vcw-7@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-7")
    gid = uuid.uuid4()
    a = await _make_visit(db, patient=patient, group_id=gid)
    b = await _make_visit(db, patient=patient, group_id=gid)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(a.id), "cancel": True}
    )
    assert res.status_code == 200, res.text
    await db.refresh(a)
    await db.refresh(b)
    assert a.status == "cancelled"
    assert b.status == "cancelled"  # 片肺を作らない

    back = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(b.id), "cancel": False}
    )
    assert back.status_code == 200, back.text
    await db.refresh(a)
    await db.refresh(b)
    assert a.status == "planned"
    assert b.status == "planned"


# ---------------------------------------------------------------------------
# 4. 憲法1: マスタ (PFV) 不変
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_does_not_touch_fixed_visit_master(client, db) -> None:
    admin = await _make_user(db, email="vcw-8@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-8")
    target_date = _future_day()
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=target_date.weekday(),
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    visit = await _make_visit(db, patient=patient, visit_date=target_date)

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": True}
    )
    assert res.status_code == 200, res.text

    rows = list((await db.scalars(select(PatientFixedVisit))).all())
    assert len(rows) == 1
    assert rows[0].id == pfv.id
    assert rows[0].weekday == target_date.weekday()
    assert rows[0].start_time == time(10, 0)
    assert rows[0].duration_min == 30


# ---------------------------------------------------------------------------
# 5. RBAC / 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_forbidden_for_staff(client, db) -> None:
    user = await _make_user(db, email="vcw-9@example.com", role="staff")
    patient = await _make_patient(db, code="VCW-9")
    visit = await _make_visit(db, patient=patient)

    res = await client.post(
        _URL, headers=_bearer(user), json={"visit_id": str(visit.id), "cancel": True}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_cancel_404_for_unknown_visit(client, db) -> None:
    admin = await _make_user(db, email="vcw-10@example.com", role="admin")

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(uuid.uuid4()), "cancel": True}
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 6. undo / redo (op_log cancel_visit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_visit_undo_redo(client, db) -> None:
    admin = await _make_user(db, email="vcw-11@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-11")
    target_date = _future_day()
    visit = await _make_visit(db, patient=patient, visit_date=target_date)
    iso = target_date.isocalendar()
    week = {"iso_year": iso.year, "iso_week": iso.week}

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": True}
    )
    assert res.status_code == 200, res.text
    await db.refresh(visit)
    assert visit.status == "cancelled"

    undo = await client.post("/api/v1/schedule/v2/op-log/undo", headers=_bearer(admin), json=week)
    assert undo.status_code == 200, undo.text
    await db.refresh(visit)
    assert visit.status == "planned"

    redo = await client.post("/api/v1/schedule/v2/op-log/redo", headers=_bearer(admin), json=week)
    assert redo.status_code == 200, redo.text
    await db.refresh(visit)
    assert visit.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_visit_undo_conflicts_when_status_changed(client, db) -> None:
    """undo 前検証 = 「現 status が期待値か」 — 他者が動かしていたら 409."""
    admin = await _make_user(db, email="vcw-12@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-12")
    target_date = _future_day()
    visit = await _make_visit(db, patient=patient, visit_date=target_date)
    iso = target_date.isocalendar()

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": True}
    )
    assert res.status_code == 200, res.text

    # 他者が状態を戻した体で書き換える。テストセッションから直接書くと
    # in-memory SQLite の共有コネクションで API 側と取引が絡んで不安定なため、
    # op_log を積まない別経路 (PATCH /visits) を使う。
    patch = await client.patch(
        f"/api/v1/visits/{visit.id}", headers=_bearer(admin), json={"status": "planned"}
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["status"] == "planned"

    undo = await client.post(
        "/api/v1/schedule/v2/op-log/undo",
        headers=_bearer(admin),
        json={"iso_year": iso.year, "iso_week": iso.week},
    )
    assert undo.status_code == 409, undo.text


# ---------------------------------------------------------------------------
# 7. D6 の前提: POST /visits が source='manual_week' を受け付ける
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_visit_accepts_manual_week_source(client, db) -> None:
    """盤面「＋訪問」は既存 POST /visits に source='manual_week' を渡す (D6).

    この source は週生成 (generate-week-only) と固定枠戻し (reset-to-fixed) の
    削除対象外 = 保護される (回帰は tests/test_change_scope_u0.py が固定)。
    """
    admin = await _make_user(db, email="vcw-13@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-13")

    res = await client.post(
        "/api/v1/visits",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "visit_date": _future_day().isoformat(),
            "start_time": "11:00:00",
            "end_time": "11:30:00",
            "type": "regular",
            "status": "planned",
            "source": "manual_week",
        },
    )
    assert res.status_code == 201, res.text
    assert res.json()["source"] == "manual_week"

    created = await db.scalar(select(Visit).where(Visit.id == uuid.UUID(res.json()["id"])))
    assert created is not None
    assert created.source == "manual_week"


# ---------------------------------------------------------------------------
# 8. 週生成 (generate-week-only) で取消枠が復活・二重化しないこと
#
# Layer1 の削除側 (_delete_existing_auto_visits) は status='planned' しか消さない。
# cancelled 行は生き残るので、衝突判定 (_fetch_manual_conflict_keys) が
# cancelled を拾わないと同じ (患者×日×開始時刻) に auto 行が再 INSERT され、
# 取消が復活したうえに枠が二重化する (2 名体制なら 2 行とも)。
# ---------------------------------------------------------------------------


async def _all_visits(db, patient: Patient) -> list[Visit]:
    """この患者の生存 visit を **DB の現値で** 読み直す.

    API は別セッションで更新するため、identity map に残った既存インスタンスを
    そのまま返されると status が古いまま見える (expire_on_commit=False)。
    ``populate_existing`` で取得行の値を必ず上書きする
    (expire_all() は patient など無関係な既存インスタンスまで期限切れにし、
    式の組み立て中に同期 lazy load が走って MissingGreenlet になる)。
    """
    rows = await db.scalars(
        select(Visit)
        .where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )
    return list(rows.all())


@pytest.mark.asyncio
async def test_cancelled_visit_is_not_regenerated_by_week_expand(client, db) -> None:
    admin = await _make_user(db, email="vcw-14@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-14")
    target_date = _future_day()
    iso = target_date.isocalendar()
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=target_date.weekday(),
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    expander = Layer1Expander()
    await expander.expand_week(db, iso_year=iso.year, iso_week=iso.week)
    await db.commit()
    rows = await _all_visits(db, patient)
    assert len(rows) == 1
    visit = rows[0]
    assert visit.source == "auto"

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": True}
    )
    assert res.status_code == 200, res.text

    # もう一度週生成しても 500 にならず、枠が増えない (取消も復活しない)
    await expander.expand_week(db, iso_year=iso.year, iso_week=iso.week)
    await db.commit()
    rows_after = await _all_visits(db, patient)
    assert len(rows_after) == 1, [(r.id, r.status, r.source) for r in rows_after]
    assert rows_after[0].id == visit.id
    assert rows_after[0].status == "cancelled"


@pytest.mark.asyncio
async def test_cancelled_two_staff_pair_is_not_regenerated_by_week_expand(client, db) -> None:
    """2 名体制 (slot 0/1) でもペアごと復活・二重化しない."""
    admin = await _make_user(db, email="vcw-15@example.com", role="admin")
    patient = Patient(
        code="VCW-15",
        name="患者VCW-15",
        status="active",
        special_week_active=[],
        requires_multiple_staff=True,
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)

    target_date = _future_day()
    iso = target_date.isocalendar()
    for slot in (0, 1):
        db.add(
            PatientFixedVisit(
                patient_id=patient.id,
                mode="normal",
                weekday=target_date.weekday(),
                start_time=time(13, 0),
                duration_min=60,
                slot_index=slot,
            )
        )
    await db.commit()

    expander = Layer1Expander()
    await expander.expand_week(db, iso_year=iso.year, iso_week=iso.week)
    await db.commit()
    rows = await _all_visits(db, patient)
    assert len(rows) == 2
    assert len({r.visit_group_id for r in rows}) == 1

    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(rows[0].id), "cancel": True}
    )
    assert res.status_code == 200, res.text

    await expander.expand_week(db, iso_year=iso.year, iso_week=iso.week)
    await db.commit()
    rows_after = await _all_visits(db, patient)
    assert len(rows_after) == 2, [(r.id, r.status) for r in rows_after]
    assert {r.status for r in rows_after} == {"cancelled"}


# ---------------------------------------------------------------------------
# 9. undo/redo のガードはエンドポイントと共用 (M-7)
#
# ガードが片方にしか無いと「undo/redo なら過去日でも取り消せる」抜け道になる。
# 条件が変わっていたら 409 (OpLogConflictError) で止める。
# ---------------------------------------------------------------------------


async def _cancel_then_undo(client, db, admin, visit: Visit) -> dict[str, int]:
    """取消 → undo (planned に戻す) まで進め、redo 用の週を返す."""
    res = await client.post(
        _URL, headers=_bearer(admin), json={"visit_id": str(visit.id), "cancel": True}
    )
    assert res.status_code == 200, res.text
    iso = visit.visit_date.isocalendar()
    week = {"iso_year": iso.year, "iso_week": iso.week}
    undo = await client.post("/api/v1/schedule/v2/op-log/undo", headers=_bearer(admin), json=week)
    assert undo.status_code == 200, undo.text
    return week


@pytest.mark.asyncio
async def test_redo_conflicts_when_visit_became_blue_pinned(client, db) -> None:
    admin = await _make_user(db, email="vcw-16@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-16")
    visit = await _make_visit(db, patient=patient)
    week = await _cancel_then_undo(client, db, admin, visit)

    pin = await client.patch(
        f"/api/v1/schedule/v2/visits/{visit.id}/week-pin",
        headers=_bearer(admin),
        json={"pinned": True},
    )
    assert pin.status_code == 200, pin.text

    redo = await client.post("/api/v1/schedule/v2/op-log/redo", headers=_bearer(admin), json=week)
    assert redo.status_code == 409, redo.text
    assert "青ピン" in redo.json()["detail"]

    rows = await _all_visits(db, patient)
    assert rows[0].status == "planned"  # 取り消されていない


@pytest.mark.asyncio
async def test_redo_conflicts_when_day_has_passed(client, db) -> None:
    admin = await _make_user(db, email="vcw-17@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-17")
    visit = await _make_visit(db, patient=patient)
    week = await _cancel_then_undo(client, db, admin, visit)

    # 「日が過ぎた」を再現 (op_log を積まない PATCH で過去日へ動かす)
    past = (_today_jst() - timedelta(days=1)).isoformat()
    patch = await client.patch(
        f"/api/v1/visits/{visit.id}", headers=_bearer(admin), json={"visit_date": past}
    )
    assert patch.status_code == 200, patch.text

    redo = await client.post("/api/v1/schedule/v2/op-log/redo", headers=_bearer(admin), json=week)
    assert redo.status_code == 409, redo.text
    assert "当日以前" in redo.json()["detail"]

    rows = await _all_visits(db, patient)
    assert rows[0].status == "planned"


@pytest.mark.asyncio
async def test_redo_conflicts_when_checkin_appeared(client, db) -> None:
    admin = await _make_user(db, email="vcw-18@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-18")
    visit = await _make_visit(db, patient=patient)
    week = await _cancel_then_undo(client, db, admin, visit)

    db.add(
        VisitCheckin(
            visit_id=visit.id,
            patient_id=patient.id,
            kind="arrival",
            scanned_at=datetime.now(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()

    redo = await client.post("/api/v1/schedule/v2/op-log/redo", headers=_bearer(admin), json=week)
    assert redo.status_code == 409, redo.text
    assert "打刻" in redo.json()["detail"]

    rows = await _all_visits(db, patient)
    assert rows[0].status == "planned"


@pytest.mark.asyncio
async def test_undo_restores_original_source(client, db) -> None:
    """undo は取消前の出所を戻す (op_log payload に控えている)."""
    admin = await _make_user(db, email="vcw-19@example.com", role="admin")
    patient = await _make_patient(db, code="VCW-19")
    visit = await _make_visit(db, patient=patient)
    # 取込由来の訪問を取り消す → undo で 'import' が戻る
    await client.patch(
        f"/api/v1/visits/{visit.id}", headers=_bearer(admin), json={"source": "import"}
    )
    await _cancel_then_undo(client, db, admin, visit)

    rows = await _all_visits(db, patient)
    assert rows[0].status == "planned"
    assert rows[0].source == "import"
