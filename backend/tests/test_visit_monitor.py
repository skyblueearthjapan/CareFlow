"""QR 訪問チェックイン Phase 3 — 訪問モニター集計のテスト.

- 実効状態の合成 (compute_phase / compute_alert の全分岐 + build_monitor 統合):
  future / awaiting / inprogress / done / missing × none / review / mismatch / missing。
- 次訪問までの距離 (haversine, 時刻順)。
- JST 境界 (深夜の当日判定)。
- RBAC (staff は 403, admin/manager は 200)。
- office_id フィルタ。
- 行 = 職員 (monitor-staff-rows-design-2026-09-30.md §6)。
- /monitor/nearby (近隣患者候補)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, Staff, User, Visit, VisitCheckin
from app.services.checkin.monitor import (
    ALERT_MISMATCH,
    ALERT_MISSING,
    ALERT_NONE,
    ALERT_REVIEW,
    PHASE_AWAITING,
    PHASE_DONE,
    PHASE_FUTURE,
    PHASE_INPROGRESS,
    PHASE_MISSING,
    build_monitor,
    compute_alert,
    compute_phase,
)

JST = ZoneInfo("Asia/Tokyo")
TARGET = date(2026, 6, 30)


def _utc(h: int, mi: int, *, d: date = TARGET) -> datetime:
    """JST 壁時計 (d の h:mi) を UTC aware に変換する (DB 保存は UTC 前提)."""
    return datetime(d.year, d.month, d.day, h, mi, tzinfo=JST).astimezone(UTC)


def _jst_dt(h: int, mi: int) -> datetime:
    return datetime(TARGET.year, TARGET.month, TARGET.day, h, mi, tzinfo=JST)


async def _make_staff(db, name: str, office_id=None) -> Staff:
    staff = Staff(name=name, primary_office_id=office_id)
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("x"),
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


async def _make_patient(db, code: str, *, lat=None, lng=None) -> Patient:
    p = Patient(code=code, name=f"患者{code}", lat=lat, lng=lng)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db, patient, staff, *, start=time(9, 0), end=time(10, 0), status="planned", visit_group_id=None
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=TARGET,
        start_time=start,
        end_time=end,
        type="regular",
        status=status,
        visit_group_id=visit_group_id,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _add_checkin(
    db,
    visit,
    staff,
    kind,
    *,
    scanned_at,
    match_status="match",
    distance_m=None,
    accuracy_m=None,
    reason=None,
    lat=None,
    lng=None,
    device_time=None,
) -> VisitCheckin:
    c = VisitCheckin(
        visit_id=visit.id,
        patient_id=visit.patient_id,
        staff_id=staff.id,
        kind=kind,
        scanned_at=scanned_at,
        device_time=device_time,
        lat=lat,
        lng=lng,
        accuracy_m=accuracy_m,
        distance_m=distance_m,
        match_status=match_status,
        threshold_snapshot={"v": 1},
        reason=reason,
        is_override=False,
        checkin_source="qr",
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


def _find(resp, visit_id):
    for row in resp.staff:
        for v in row.visits:
            if v.visit_id == visit_id:
                return v
    return None


# ---------------------------------------------------------------------------
# Unit: compute_phase
# ---------------------------------------------------------------------------


def test_phase_future_before_start() -> None:
    assert (
        compute_phase(
            arrival_scanned=None,
            departure_scanned=None,
            has_no_show=False,
            start_dt=_jst_dt(15, 0),
            now=_jst_dt(13, 30),
            grace_min=20,
        )
        == PHASE_FUTURE
    )


def test_phase_awaiting_within_grace() -> None:
    assert (
        compute_phase(
            arrival_scanned=None,
            departure_scanned=None,
            has_no_show=False,
            start_dt=_jst_dt(13, 20),
            now=_jst_dt(13, 30),
            grace_min=20,
        )
        == PHASE_AWAITING
    )


def test_phase_missing_after_grace() -> None:
    assert (
        compute_phase(
            arrival_scanned=None,
            departure_scanned=None,
            has_no_show=False,
            start_dt=_jst_dt(9, 0),
            now=_jst_dt(13, 30),
            grace_min=20,
        )
        == PHASE_MISSING
    )


def test_phase_missing_when_no_show_row_even_before_grace() -> None:
    # no_show 行があれば猶予前でも missing。
    assert (
        compute_phase(
            arrival_scanned=None,
            departure_scanned=None,
            has_no_show=True,
            start_dt=_jst_dt(13, 25),
            now=_jst_dt(13, 30),
            grace_min=20,
        )
        == PHASE_MISSING
    )


def test_phase_inprogress_and_done() -> None:
    assert (
        compute_phase(
            arrival_scanned=_jst_dt(9, 5),
            departure_scanned=None,
            has_no_show=False,
            start_dt=_jst_dt(9, 0),
            now=_jst_dt(13, 30),
            grace_min=20,
        )
        == PHASE_INPROGRESS
    )
    assert (
        compute_phase(
            arrival_scanned=_jst_dt(9, 5),
            departure_scanned=_jst_dt(9, 55),
            has_no_show=False,
            start_dt=_jst_dt(9, 0),
            now=_jst_dt(13, 30),
            grace_min=20,
        )
        == PHASE_DONE
    )


# ---------------------------------------------------------------------------
# Unit: compute_alert
# ---------------------------------------------------------------------------


def test_alert_missing_overrides_all() -> None:
    assert (
        compute_alert(
            phase=PHASE_MISSING,
            arrival_match_status=None,
            arrival_scanned=None,
            start_dt=_jst_dt(9, 0),
            late_min=15,
        )
        == ALERT_MISSING
    )


def test_alert_none_when_no_arrival_future() -> None:
    assert (
        compute_alert(
            phase=PHASE_FUTURE,
            arrival_match_status=None,
            arrival_scanned=None,
            start_dt=_jst_dt(15, 0),
            late_min=15,
        )
        == ALERT_NONE
    )


def test_alert_mismatch() -> None:
    assert (
        compute_alert(
            phase=PHASE_DONE,
            arrival_match_status="mismatch",
            arrival_scanned=_jst_dt(9, 5),
            start_dt=_jst_dt(9, 0),
            late_min=15,
        )
        == ALERT_MISMATCH
    )


def test_alert_review_from_no_gps_and_late_and_review() -> None:
    # review status (on-time) → review。
    assert (
        compute_alert(
            phase=PHASE_DONE,
            arrival_match_status="review",
            arrival_scanned=_jst_dt(9, 5),
            start_dt=_jst_dt(9, 0),
            late_min=15,
        )
        == ALERT_REVIEW
    )
    # no_gps → review。
    assert (
        compute_alert(
            phase=PHASE_INPROGRESS,
            arrival_match_status="no_gps",
            arrival_scanned=_jst_dt(9, 5),
            start_dt=_jst_dt(9, 0),
            late_min=15,
        )
        == ALERT_REVIEW
    )
    # match だが遅延 (>= 15 分) → review。
    assert (
        compute_alert(
            phase=PHASE_INPROGRESS,
            arrival_match_status="match",
            arrival_scanned=_jst_dt(9, 20),
            start_dt=_jst_dt(9, 0),
            late_min=15,
        )
        == ALERT_REVIEW
    )


def test_alert_none_when_match_on_time() -> None:
    assert (
        compute_alert(
            phase=PHASE_DONE,
            arrival_match_status="match",
            arrival_scanned=_jst_dt(9, 5),
            start_dt=_jst_dt(9, 0),
            late_min=15,
        )
        == ALERT_NONE
    )


def test_alert_long_inprogress_boundary() -> None:
    # 退出忘れ (長時間 inprogress) は MAX_INPROGRESS_MIN (240) 超で review。
    # 境界: 239 → none、241 → review (on-time match で他要因なし)。
    common = dict(
        phase=PHASE_INPROGRESS,
        arrival_match_status="match",
        arrival_scanned=_jst_dt(9, 5),
        start_dt=_jst_dt(9, 0),
        late_min=15,
    )
    assert compute_alert(**common, stay_minutes=239) == ALERT_NONE
    assert compute_alert(**common, stay_minutes=240) == ALERT_NONE
    assert compute_alert(**common, stay_minutes=241) == ALERT_REVIEW


# ---------------------------------------------------------------------------
# Integration: build_monitor synthesis matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_monitor_done_match(db) -> None:
    staff = await _make_staff(db, "S-done")
    p = await _make_patient(db, "M-DONE", lat=35.0, lng=139.0)
    v = await _make_visit(db, p, staff, start=time(9, 0), end=time(10, 0))
    await _add_checkin(db, v, staff, "arrival", scanned_at=_utc(9, 5), match_status="match")
    await _add_checkin(db, v, staff, "departure", scanned_at=_utc(9, 55), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    mv = _find(resp, v.id)
    assert mv is not None
    assert mv.phase == PHASE_DONE
    assert mv.alert_level == ALERT_NONE
    assert mv.stay_minutes == 50
    assert mv.arrival_delay_min == 5


@pytest.mark.asyncio
async def test_build_monitor_inprogress_review_late(db) -> None:
    staff = await _make_staff(db, "S-inprog")
    p = await _make_patient(db, "M-INPROG", lat=35.0, lng=139.0)
    v = await _make_visit(db, p, staff, start=time(9, 0), end=time(10, 0))
    # 20 分遅れの到着・退出なし → inprogress + review(late)。
    await _add_checkin(db, v, staff, "arrival", scanned_at=_utc(9, 20), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    mv = _find(resp, v.id)
    assert mv.phase == PHASE_INPROGRESS
    assert mv.alert_level == ALERT_REVIEW
    assert mv.departure is None
    # 進行中の滞在 = now - arrival (13:30 - 9:20 = 250 分)。
    assert mv.stay_minutes == 250


@pytest.mark.asyncio
async def test_build_monitor_done_mismatch(db) -> None:
    staff = await _make_staff(db, "S-mis")
    p = await _make_patient(db, "M-MIS", lat=35.0, lng=139.0)
    v = await _make_visit(db, p, staff, start=time(9, 0), end=time(10, 0))
    await _add_checkin(
        db,
        v,
        staff,
        "arrival",
        scanned_at=_utc(9, 5),
        match_status="mismatch",
        distance_m=360.0,
        lat=35.01,
        lng=139.01,
        reason="隣の棟で測位",
    )
    await _add_checkin(db, v, staff, "departure", scanned_at=_utc(9, 55), match_status="mismatch")

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    mv = _find(resp, v.id)
    assert mv.phase == PHASE_DONE
    assert mv.alert_level == ALERT_MISMATCH
    assert mv.arrival.distance_m == 360.0
    # mismatch の地図表示用に GPS 座標を返す。
    assert mv.arrival.lat == 35.01
    assert mv.reason == "隣の棟で測位"


@pytest.mark.asyncio
async def test_build_monitor_awaiting_and_future_and_missing(db) -> None:
    staff = await _make_staff(db, "S-time")
    pa = await _make_patient(db, "M-AWAIT")
    pf = await _make_patient(db, "M-FUT")
    pm = await _make_patient(db, "M-MISS")
    va = await _make_visit(db, pa, staff, start=time(13, 20), end=time(14, 0))
    vf = await _make_visit(db, pf, staff, start=time(15, 0), end=time(16, 0))
    vm = await _make_visit(db, pm, staff, start=time(9, 0), end=time(10, 0))

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert _find(resp, va.id).phase == PHASE_AWAITING
    assert _find(resp, vf.id).phase == PHASE_FUTURE
    assert _find(resp, vm.id).phase == PHASE_MISSING
    assert _find(resp, vm.id).alert_level == ALERT_MISSING


@pytest.mark.asyncio
async def test_build_monitor_missing_with_no_show_reason(db) -> None:
    staff = await _make_staff(db, "S-ns")
    p = await _make_patient(db, "M-NS")
    v = await _make_visit(db, p, staff, start=time(13, 25), end=time(14, 0))
    await _add_checkin(
        db, v, staff, "no_show", scanned_at=_utc(13, 28), match_status="no_gps", reason="不在"
    )

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    mv = _find(resp, v.id)
    assert mv.phase == PHASE_MISSING
    assert mv.alert_level == ALERT_MISSING
    assert mv.reason == "不在"


@pytest.mark.asyncio
async def test_build_monitor_excludes_cancelled(db) -> None:
    # 取消済み (status=cancelled) の visit はモニターに出ない (judge ガードと同じ)。
    staff = await _make_staff(db, "S-cancel")
    p_ok = await _make_patient(db, "C-OK")
    p_cancelled = await _make_patient(db, "C-CANCELLED")
    v_ok = await _make_visit(db, p_ok, staff, start=time(9, 0), end=time(10, 0))
    v_cancelled = await _make_visit(
        db, p_cancelled, staff, start=time(11, 0), end=time(12, 0), status="cancelled"
    )

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert _find(resp, v_ok.id) is not None
    assert _find(resp, v_cancelled.id) is None


@pytest.mark.asyncio
async def test_build_monitor_long_inprogress_review(db) -> None:
    # 到着済・退出未記録で MAX_INPROGRESS_MIN (240分) 超の滞在 → review (退出忘れ)。
    staff = await _make_staff(db, "S-longinprog")
    p = await _make_patient(db, "M-LONG", lat=35.0, lng=139.0)
    v = await _make_visit(db, p, staff, start=time(9, 0), end=time(10, 0))
    # 9:00 到着・退出なし・now 13:30 → 滞在 270 分 (>240)。
    await _add_checkin(db, v, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    mv = _find(resp, v.id)
    assert mv.phase == PHASE_INPROGRESS
    assert mv.stay_minutes == 270
    assert mv.alert_level == ALERT_REVIEW


# ---------------------------------------------------------------------------
# Integration: 同住所・同時刻ペア補正 (後攻の誤警告対策)
# ---------------------------------------------------------------------------

_PAIR_LAT = 35.0
_PAIR_LNG = 139.0


async def _make_pair(db, staff, label, *, start=time(9, 0), end=time(9, 45)):
    """同住所・同時刻ペアの 2 visit (A, B) を作る (同 staff・同時刻・同座標・別患者)。"""
    pa = await _make_patient(db, f"PR-A-{label}", lat=_PAIR_LAT, lng=_PAIR_LNG)
    pb = await _make_patient(db, f"PR-B-{label}", lat=_PAIR_LAT, lng=_PAIR_LNG)
    va = await _make_visit(db, pa, staff, start=start, end=end)
    vb = await _make_visit(db, pb, staff, start=start, end=end)
    return va, vb


@pytest.mark.asyncio
async def test_pair_second_not_missing_and_pair_waiting(db) -> None:
    """A 到着済・B 未読・now=予定+30分 → B は missing にならず pair_waiting=True."""
    staff = await _make_staff(db, "S-pair1")
    va, vb = await _make_pair(db, staff, "1")  # 9:00–9:45
    # A は定刻 9:00 到着 (退出なし)。A 完了見込 = 9:00 + 45 = 9:45。
    await _add_checkin(db, va, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(9, 30))
    mvb = _find(resp, vb.id)
    assert mvb.phase == PHASE_AWAITING
    assert mvb.alert_level == ALERT_NONE
    assert mvb.pair_waiting is True
    # A 自身は到着済 (inprogress) で pair_waiting は付かない。
    assert _find(resp, va.id).pair_waiting is False


@pytest.mark.asyncio
async def test_pair_second_missing_after_partner_departure_grace(db) -> None:
    """B が A 退出 + grace を超過まで未読 → missing."""
    staff = await _make_staff(db, "S-pair2")
    va, vb = await _make_pair(db, staff, "2")  # 9:00–9:45
    await _add_checkin(db, va, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")
    await _add_checkin(db, va, staff, "departure", scanned_at=_utc(9, 40), match_status="match")
    # 補正後起点 = A 退出 9:40。9:40 + grace(20) = 10:00 を超過。
    resp = await build_monitor(db, TARGET, now=_utc(10, 5))
    mvb = _find(resp, vb.id)
    assert mvb.phase == PHASE_MISSING
    assert mvb.alert_level == ALERT_MISSING
    assert mvb.pair_waiting is False


@pytest.mark.asyncio
async def test_pair_second_read_after_partner_departure_no_review(db) -> None:
    """B を A 退出 + 10分に読む → 到着遅延 review が付かない (補正が効く)."""
    staff = await _make_staff(db, "S-pair3")
    va, vb = await _make_pair(db, staff, "3")  # 9:00–9:45
    await _add_checkin(db, va, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")
    await _add_checkin(db, va, staff, "departure", scanned_at=_utc(9, 40), match_status="match")
    # B は A 退出 (9:40) + 10分 = 9:50 に到着。予定比 +50分 だが補正後起点比 +10分。
    await _add_checkin(db, vb, staff, "arrival", scanned_at=_utc(9, 50), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(10, 5))
    mvb = _find(resp, vb.id)
    assert mvb.phase == PHASE_INPROGRESS
    assert mvb.alert_level == ALERT_NONE  # 補正後起点比 10分 < late_min(15)。


@pytest.mark.asyncio
async def test_pair_both_unread_both_missing(db) -> None:
    """両方未読・予定 + grace 超過 → 両方 missing (従来どおり真の未訪問を検出)."""
    staff = await _make_staff(db, "S-pair4")
    va, vb = await _make_pair(db, staff, "4")  # 9:00–9:45
    resp = await build_monitor(db, TARGET, now=_utc(9, 30))  # 9:00 + grace(20) 超過
    assert _find(resp, va.id).phase == PHASE_MISSING
    assert _find(resp, vb.id).phase == PHASE_MISSING
    assert _find(resp, va.id).pair_waiting is False
    assert _find(resp, vb.id).pair_waiting is False


@pytest.mark.asyncio
async def test_pair_both_read_on_time_all_none(db) -> None:
    """両方即読み (定刻到着) → 補正不要・全 none."""
    staff = await _make_staff(db, "S-pair5")
    va, vb = await _make_pair(db, staff, "5")  # 9:00–9:45
    await _add_checkin(db, va, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")
    await _add_checkin(db, vb, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(9, 30))
    for v in (va, vb):
        mv = _find(resp, v.id)
        assert mv.phase == PHASE_INPROGRESS
        assert mv.alert_level == ALERT_NONE
        assert mv.pair_waiting is False


@pytest.mark.asyncio
async def test_pair_symmetric_when_second_read_first(db) -> None:
    """対称性: B 先読みでも A に同補正 (A が pair_waiting)."""
    staff = await _make_staff(db, "S-pair6")
    va, vb = await _make_pair(db, staff, "6")  # 9:00–9:45
    # B が先に 9:00 到着 (退出なし)。A は未読。
    await _add_checkin(db, vb, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(9, 30))
    mva = _find(resp, va.id)
    assert mva.phase == PHASE_AWAITING
    assert mva.pair_waiting is True
    assert _find(resp, vb.id).pair_waiting is False


@pytest.mark.asyncio
async def test_pair_no_show_overrides_pair_waiting(db) -> None:
    """no_show 手動行はペア補正より強い: A 到着済でも B は missing (レビュー LOW-2)."""
    staff = await _make_staff(db, "S-pair8")
    va, vb = await _make_pair(db, staff, "8")  # 9:00–9:45
    await _add_checkin(db, va, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")
    await _add_checkin(
        db, vb, staff, "no_show", scanned_at=_utc(9, 5), match_status="no_gps", reason="不在"
    )

    resp = await build_monitor(db, TARGET, now=_utc(9, 10))
    mvb = _find(resp, vb.id)
    assert mvb.phase == PHASE_MISSING
    assert mvb.pair_waiting is False


@pytest.mark.asyncio
async def test_pair_three_members_all_waiting(db) -> None:
    """3人同条件グループ: A 到着済 → B/C とも pair_waiting (レビュー LOW-3)."""
    staff = await _make_staff(db, "S-pair9")
    va, vb = await _make_pair(db, staff, "9")  # 9:00–9:45 同座標
    # 3 人目 (同座標・同時刻・同担当・別患者)。_make_pair と同じ座標を使う。
    pc = await _make_patient(db, "PAIR-9C", lat=_PAIR_LAT, lng=_PAIR_LNG)
    vc = await _make_visit(db, pc, staff, start=time(9, 0), end=time(9, 45))
    await _add_checkin(db, va, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(9, 30))
    assert _find(resp, vb.id).pair_waiting is True
    assert _find(resp, vc.id).pair_waiting is True
    assert _find(resp, va.id).pair_waiting is False


@pytest.mark.asyncio
async def test_pair_zero_coord_not_grouped(db) -> None:
    """(0,0) 座標は未設定既定値でありうるためペア判定に使わない (誤ペア化ガード)."""
    staff = await _make_staff(db, "S-pair10")
    pa = await _make_patient(db, "ZERO-A", lat=0.0, lng=0.0)
    pb = await _make_patient(db, "ZERO-B", lat=0.0, lng=0.0)
    va = await _make_visit(db, pa, staff, start=time(9, 0), end=time(9, 45))
    vb = await _make_visit(db, pb, staff, start=time(9, 0), end=time(9, 45))
    await _add_checkin(db, va, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(9, 30))
    mvb = _find(resp, vb.id)
    assert mvb.phase == PHASE_MISSING  # ペア扱いしない → 従来どおり missing。
    assert mvb.pair_waiting is False


@pytest.mark.asyncio
async def test_non_pair_missing_unchanged_regression(db) -> None:
    """非ペア (別住所・同 staff・同時刻) は補正されず従来どおり missing になる."""
    staff = await _make_staff(db, "S-pair7")
    pa = await _make_patient(db, "NP-A", lat=35.0, lng=139.0)
    pb = await _make_patient(db, "NP-B", lat=35.5, lng=139.5)  # 別住所
    va = await _make_visit(db, pa, staff, start=time(9, 0), end=time(9, 45))
    vb = await _make_visit(db, pb, staff, start=time(9, 0), end=time(9, 45))
    await _add_checkin(db, va, staff, "arrival", scanned_at=_utc(9, 0), match_status="match")

    resp = await build_monitor(db, TARGET, now=_utc(9, 30))
    mvb = _find(resp, vb.id)
    assert mvb.phase == PHASE_MISSING  # 別住所なのでペア補正なし。
    assert mvb.pair_waiting is False


# ---------------------------------------------------------------------------
# Integration: next distance / office filter / nearby
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_monitor_distance_to_next(db) -> None:
    staff = await _make_staff(db, "S-dist")
    p1 = await _make_patient(db, "D-1", lat=35.0000, lng=139.0000)
    p2 = await _make_patient(db, "D-2", lat=35.0100, lng=139.0000)
    v1 = await _make_visit(db, p1, staff, start=time(9, 0), end=time(10, 0))
    v2 = await _make_visit(db, p2, staff, start=time(10, 30), end=time(11, 30))

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    mv1 = _find(resp, v1.id)
    mv2 = _find(resp, v2.id)
    # 緯度 0.01 度 ≒ 1.1km。
    assert mv1.distance_to_next_m is not None
    assert 1000 < mv1.distance_to_next_m < 1200
    # 最後の visit は次が無いので None。
    assert mv2.distance_to_next_m is None


@pytest.mark.asyncio
async def test_build_monitor_office_filter(db) -> None:
    office_a = Office(name="稲毛")
    office_b = Office(name="都賀")
    db.add_all([office_a, office_b])
    await db.commit()
    await db.refresh(office_a)
    await db.refresh(office_b)

    staff_a = await _make_staff(db, "S-A", office_id=office_a.id)
    staff_b = await _make_staff(db, "S-B", office_id=office_b.id)
    pa = await _make_patient(db, "OF-A")
    pb = await _make_patient(db, "OF-B")
    va = await _make_visit(db, pa, staff_a)
    vb = await _make_visit(db, pb, staff_b)

    # フィルタなし → 両拠点が登場。
    full = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert _find(full, va.id) is not None
    assert _find(full, vb.id) is not None
    assert {o.name for o in full.offices} == {"稲毛", "都賀"}

    # office_a フィルタ → A のみ。
    only_a = await build_monitor(db, TARGET, office_id=office_a.id, now=_utc(13, 30))
    assert _find(only_a, va.id) is not None
    assert _find(only_a, vb.id) is None


# ---------------------------------------------------------------------------
# 行 = 職員 (monitor-staff-rows-design-2026-09-30.md §2 / §6)
# ---------------------------------------------------------------------------


async def _office(db, name: str, sort_order: int | None = None) -> Office:
    office = Office(name=name, sort_order=sort_order)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


async def _staff(db, name: str, *, office=None, code=None, status="active") -> Staff:
    staff = Staff(
        name=name,
        code=code,
        status=status,
        primary_office_id=office.id if office is not None else None,
    )
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def _course(db, office, code: str, *, assigned=None):
    from app.models import Course

    course = Course(
        iso_year=2026,
        iso_week=27,
        weekday=1,  # TARGET=2026-06-30 (火)
        code=code,
        course_status="course_fixed",
        office_id=office.id,
        assigned_staff_id=assigned.id if assigned is not None else None,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


async def _visit(
    db,
    patient,
    staff,
    *,
    course=None,
    start=time(9, 0),
    end=None,
    secondary=None,
    manual_override=False,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id if staff is not None else None,
        secondary_staff_id=secondary.id if secondary is not None else None,
        course_id=course.id if course is not None else None,
        visit_date=TARGET,
        start_time=start,
        end_time=end or time(start.hour, 35),
        type="regular",
        status="planned",
        manual_staff_override=manual_override,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _row_of(resp, staff_id):
    return next((r for r in resp.staff if r.staff_id == staff_id), None)


@pytest.mark.asyncio
async def test_rows_one_row_per_staff_across_courses(db) -> None:
    """1 人が 2 コースを持つ日も 1 行。札は重複なし・初出順。行の拠点は所属."""
    inage = await _office(db, "稲毛", 1)
    tsuga = await _office(db, "都賀", 2)
    course_d = await _course(db, inage, "D")
    course_r = await _course(db, tsuga, "臨2")
    staff = await _staff(db, "掛持 一号", office=inage, code="S1")
    other = await _staff(db, "別人 二号", office=inage, code="S2")
    p = [await _make_patient(db, f"ONE-{i}") for i in range(5)]
    v1 = await _visit(db, p[0], staff, course=course_d, start=time(9, 0))
    v2 = await _visit(db, p[1], staff, course=course_r, start=time(10, 0))
    v3 = await _visit(db, p[2], staff, course=course_d, start=time(11, 0))
    v4 = await _visit(db, p[3], staff, course=None, start=time(12, 0))
    # 同じコースを別の人が回る訪問は、その人の行に入る (コース行に同居しない)。
    v5 = await _visit(db, p[4], other, course=course_r, start=time(10, 0))

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert [r.staff_id for r in resp.staff] == [staff.id, other.id]
    row = _row_of(resp, staff.id)
    assert [mv.visit_id for mv in row.visits] == [v1.id, v2.id, v3.id, v4.id]
    assert [t.label for t in row.course_tags] == ["稲D", "都臨2"]
    assert [t.course_id for t in row.course_tags] == [course_d.id, course_r.id]
    assert row.course_tags[1].office_id == tsuga.id
    assert row.course_tags[1].office_name == "都賀"
    assert row.staff_name == "掛持 一号"
    assert row.staff_ids == [staff.id]
    assert row.office_id == inage.id  # 行の拠点は所属 (コースの拠点ではない)
    assert row.office_name == "稲毛"
    # 互換の項目は残すが常に null。
    assert row.course_id is None
    assert row.course_label is None
    assert row.course_staff_id is None
    assert row.course_staff_name is None
    # 訪問ごとの札と拠点。コース無しは札なし・拠点は患者の主担当拠点 (未設定なら None)。
    mv2 = _find(resp, v2.id)
    assert (mv2.course_id, mv2.course_tag) == (course_r.id, "都臨2")
    assert (mv2.course_office_id, mv2.course_office_name) == (tsuga.id, "都賀")
    mv4 = _find(resp, v4.id)
    assert (mv4.course_id, mv4.course_tag, mv4.course_office_id) == (None, None, None)
    assert [mv.visit_id for mv in _row_of(resp, other.id).visits] == [v5.id]
    # 拠点チップ = 訪問のコース拠点 ∪ 所属 (sort_order 順)。
    assert [o.name for o in resp.offices] == ["稲毛", "都賀"]


@pytest.mark.asyncio
async def test_rows_empty_primary_falls_back_to_course_staff(db) -> None:
    """担当が空の訪問はコース担当の行へ。どちらも無い訪問は末尾の「担当なし」行."""
    office = await _office(db, "稲毛", 1)
    course_staff = await _staff(db, "コース 担当", office=office, code="S1")
    course_a = await _course(db, office, "A", assigned=course_staff)
    course_b = await _course(db, office, "B")  # コース担当なし
    retired = await _staff(db, "退職 太郎", office=office, code="S0", status="retired")
    course_c = await _course(db, office, "C", assigned=retired)
    pa = await _make_patient(db, "FB-A")
    pb = await _make_patient(db, "FB-B")
    pc = await _make_patient(db, "FB-C")
    pd = await _make_patient(db, "FB-D")
    pm = await _make_patient(db, "FB-M")
    va = await _visit(db, pa, None, course=course_a, start=time(9, 0))
    vb = await _visit(db, pb, None, course=course_b, start=time(10, 0))
    vc = await _visit(db, pc, None, course=course_c, start=time(11, 0))
    vd = await _visit(db, pd, None, course=None, start=time(12, 0))
    # 手動で担当を外した訪問 (manual_staff_override) はフォールバックしない。
    vm = await _visit(db, pm, None, course=course_a, start=time(13, 0), manual_override=True)

    resp = await build_monitor(db, TARGET, now=_utc(8, 0))
    row = _row_of(resp, course_staff.id)
    assert [mv.visit_id for mv in row.visits] == [va.id]
    # 訪問の担当 (= visits.primary_staff_id) の意味は変えない: 空のまま。
    assert row.visits[0].staff_id is None
    # 「担当なし」行は末尾・1 本。
    assert resp.staff[-1].staff_id is None
    assert resp.staff[-1].staff_name is None
    assert resp.staff[-1].staff_ids == []
    assert [mv.visit_id for mv in resp.staff[-1].visits] == [vb.id, vc.id, vd.id, vm.id]
    assert len([r for r in resp.staff if r.staff_id is None]) == 1
    # 退職者の行は作らない (フォールバック先にもしない)。
    assert _row_of(resp, retired.id) is None


@pytest.mark.asyncio
async def test_rows_unplanned_visit_is_in_reader_row(db) -> None:
    """予定外の訪問は読み取った本人 (= 主担当) の行に入る (専用行は作らない)."""
    office = await _office(db, "稲毛", 1)
    staff = await _staff(db, "実績 太郎", office=office)
    p1 = await _make_patient(db, "UP-1")
    p2 = await _make_patient(db, "UP-2")
    planned = await _visit(db, p1, staff, start=time(9, 0))
    adhoc = await _visit(db, p2, staff, start=time(11, 0))
    adhoc.is_unplanned = True
    await db.commit()

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert len(resp.staff) == 1
    assert [mv.visit_id for mv in resp.staff[0].visits] == [planned.id, adhoc.id]
    mv = _find(resp, adhoc.id)
    assert mv.is_unplanned is True
    assert mv.course_tag is None


@pytest.mark.asyncio
async def test_rows_event_only_and_day_off_staff_get_rows(db) -> None:
    """訪問が無くてもイベント (取消でない)・休み・時間変更がある在籍中の職員は行になる."""
    from app.models.staff import StaffEvent, StaffWeeklyOverride

    office = await _office(db, "稲毛", 1)
    ev_staff = await _staff(db, "会議 一郎", office=office, code="S1")
    off_staff = await _staff(db, "休み 二郎", office=office, code="S2")
    custom_staff = await _staff(db, "時短 三郎", office=office, code="S3")
    cancelled_staff = await _staff(db, "取消 四郎", office=office, code="S4")
    other_day_staff = await _staff(db, "別日 五郎", office=office, code="S5")
    retired = await _staff(db, "退職 六郎", office=office, code="S6", status="retired")
    nothing = await _staff(db, "何も無し", office=office, code="S7")

    def _ev(staff, d=TARGET, cancelled=False):
        return StaffEvent(
            staff_id=staff.id,
            event_type="イベント",
            starts_at=datetime.combine(d, time(9, 0)),
            ends_at=datetime.combine(d, time(9, 30)),
            title="朝会",
            cancelled_at=datetime(2026, 6, 29, tzinfo=UTC) if cancelled else None,
        )

    iso = TARGET.isocalendar()
    db.add_all(
        [
            _ev(ev_staff),
            _ev(cancelled_staff, cancelled=True),
            _ev(other_day_staff, d=date(2026, 7, 1)),
            _ev(retired),
            StaffWeeklyOverride(
                staff_id=off_staff.id,
                iso_year=iso.year,
                iso_week=iso.week,
                weekday=TARGET.weekday(),
                override_type="off",
                reason="有給",
            ),
            StaffWeeklyOverride(
                staff_id=custom_staff.id,
                iso_year=iso.year,
                iso_week=iso.week,
                weekday=TARGET.weekday(),
                override_type="custom_time",
                start_time=time(10, 0),
                end_time=time(15, 0),
            ),
        ]
    )
    await db.commit()

    resp = await build_monitor(db, TARGET, now=_utc(8, 0), viewer_is_admin=True)
    assert [r.staff_id for r in resp.staff] == [ev_staff.id, off_staff.id, custom_staff.id]
    for r in resp.staff:
        assert r.visits == []
        assert r.staff_ids == [r.staff_id]
    assert _row_of(resp, ev_staff.id).day_override is None
    off = _row_of(resp, off_staff.id).day_override
    assert (off.kind, off.reason) == ("off", "有給")
    # 休みの理由は admin だけ。staff が見るときは種別・時刻だけで理由は null。
    as_staff = await build_monitor(db, TARGET, now=_utc(8, 0), viewer_is_admin=False)
    staff_off = _row_of(as_staff, off_staff.id).day_override
    assert (staff_off.kind, staff_off.reason) == ("off", None)
    custom = _row_of(resp, custom_staff.id).day_override
    assert (custom.kind, custom.start_time, custom.end_time) == ("custom_time", "10:00", "15:00")
    for absent in (cancelled_staff, other_day_staff, retired, nothing):
        assert _row_of(resp, absent.id) is None
    # 拠点チップは所属からも立つ。
    assert [o.name for o in resp.offices] == ["稲毛"]


@pytest.mark.asyncio
async def test_rows_sorted_by_office_sort_order_then_staff_code(db) -> None:
    """並び = 所属拠点の sort_order → 職員コード (数字は数値順・コード無しは末尾)。担当なしは最後."""
    first = await _office(db, "都賀", 1)  # 名前順なら後ろだが sort_order が先
    second = await _office(db, "稲毛", 2)
    s10 = await _staff(db, "十番", office=first, code="S10")
    s2 = await _staff(db, "二番", office=first, code="S2")
    nocode = await _staff(db, "コード無し", office=first)
    s1_second = await _staff(db, "一番", office=second, code="S1")
    no_office = await _staff(db, "所属無し", code="S0")
    staffs = [s10, s2, nocode, s1_second, no_office]
    for i, st in enumerate(staffs):
        await _visit(db, await _make_patient(db, f"ORD-{i}"), st, start=time(9 + i, 0))
    await _visit(db, await _make_patient(db, "ORD-NONE"), None, start=time(8, 0))

    resp = await build_monitor(db, TARGET, now=_utc(8, 0))
    assert [r.staff_name for r in resp.staff] == [
        "二番",
        "十番",
        "コード無し",
        "一番",
        "所属無し",
        None,
    ]
    assert [o.name for o in resp.offices] == ["都賀", "稲毛"]


@pytest.mark.asyncio
async def test_office_order_is_master_order_not_the_days_offices(db) -> None:
    """札の色の基準 (office_order) は拠点マスタの順。その日に出る拠点だけの日でも変わらない."""
    inage = await _office(db, "稲毛", 1)
    tsuga = await _office(db, "都賀", 2)
    no_sort = await _office(db, "未設定")
    staff = await _staff(db, "都賀 だけ", office=tsuga, code="S1")
    course = await _course(db, tsuga, "A")
    await _visit(db, await _make_patient(db, "OO-1"), staff, course=course)

    resp = await build_monitor(db, TARGET, now=_utc(8, 0))
    # チップはその日の拠点だけ。
    assert [o.id for o in resp.offices] == [tsuga.id]
    # 色の基準はマスタ全体の順 (sort_order → sort_order 無しは末尾)。
    assert resp.office_order == [inage.id, tsuga.id, no_sort.id]


@pytest.mark.asyncio
async def test_rows_distance_to_next_follows_the_staff_day(db) -> None:
    """次の訪問までの距離は、コースをまたいでもその人の 1 日の時刻順で出す."""
    office = await _office(db, "稲毛", 1)
    course_a = await _course(db, office, "A")
    course_b = await _course(db, office, "B")
    staff = await _staff(db, "距離 太郎", office=office)
    p1 = await _make_patient(db, "DS-1", lat=35.0000, lng=139.0000)
    p2 = await _make_patient(db, "DS-2", lat=35.0100, lng=139.0000)
    p3 = await _make_patient(db, "DS-3", lat=35.0200, lng=139.0000)
    v1 = await _visit(db, p1, staff, course=course_a, start=time(9, 0))
    v2 = await _visit(db, p2, staff, course=course_b, start=time(10, 0))
    v3 = await _visit(db, p3, staff, course=course_a, start=time(11, 0))

    resp = await build_monitor(db, TARGET, now=_utc(8, 0))
    # A→B→A: コースごとの順 (旧: A の 9:00 → 11:00 = 2.2km) ではなく 1 日の順 (1.1km)。
    assert 1000 < _find(resp, v1.id).distance_to_next_m < 1200
    assert 1000 < _find(resp, v2.id).distance_to_next_m < 1200
    assert _find(resp, v3.id).distance_to_next_m is None


@pytest.mark.asyncio
async def test_rows_companion_visit_ids(db) -> None:
    """同行・副担当として関わる訪問は、その人の行の companion_visit_ids に入る."""
    from app.models.accompaniment import Accompaniment

    office = await _office(db, "稲毛", 1)
    main = await _staff(db, "主担当", office=office, code="S1")
    sub = await _staff(db, "副担当", office=office, code="S2")
    trainee = await _staff(db, "新人", office=office, code="S3")
    p1 = await _make_patient(db, "CP-1")
    p2 = await _make_patient(db, "CP-2")
    v1 = await _visit(db, p1, main, start=time(9, 0), secondary=sub)
    v2 = await _visit(db, p2, main, start=time(10, 0))
    db.add(
        Accompaniment(
            accompanying_staff_id=trainee.id,
            target_type="visit",
            visit_id=v2.id,
            source="manual",
            kind="support",
        )
    )
    await db.commit()

    resp = await build_monitor(db, TARGET, now=_utc(8, 0))
    main_row = _row_of(resp, main.id)
    assert [mv.visit_id for mv in main_row.visits] == [v1.id, v2.id]
    assert main_row.companion_visit_ids == []
    # 副担当・同行者の行は訪問を持たず、関わる訪問の id だけを持つ (訪問は主担当の行)。
    sub_row = _row_of(resp, sub.id)
    assert sub_row.visits == []
    assert sub_row.companion_visit_ids == [v1.id]
    trainee_row = _row_of(resp, trainee.id)
    assert trainee_row.visits == []
    assert trainee_row.companion_visit_ids == [v2.id]
    # 訪問本体は 1 回だけ (集計が二重にならない)。
    assert sum(len(r.visits) for r in resp.staff) == 2


@pytest.mark.asyncio
async def test_rows_two_person_group_is_not_companion_of_its_own_members(db) -> None:
    """2 名体制 (Layer3: 組の両方に副担当) は、組の訪問を持つ人の行に同行として重ねない.

    A = 主 X・副 Y、B = 主 Y・副 X (同じ visit_group_id)。X の行は A だけ、Y の行は B だけで、
    どちらも companion は空。組の外の人が副担当なら、その人には同行として出す。
    カイポケ取込の「1 訪問に主と副」(visit_group_id 無し) は従来どおり同行。
    """
    import uuid

    office = await _office(db, "稲毛", 1)
    x = await _staff(db, "二名 エックス", office=office, code="S1")
    y = await _staff(db, "二名 ワイ", office=office, code="S2")
    z = await _staff(db, "取込 ゼット", office=office, code="S3")
    p = await _make_patient(db, "TW-1")
    group = uuid.uuid4()
    va = await _visit(db, p, x, start=time(9, 0), secondary=y)
    vb = await _visit(db, p, y, start=time(9, 0), secondary=x)
    for v in (va, vb):
        v.visit_group_id = group
        v.required_staff_count = 2
    # 組の無い「1 訪問に主と副」: X が主・Z が副。
    vk = await _visit(db, await _make_patient(db, "TW-2"), x, start=time(11, 0), secondary=z)
    # 組の訪問でも、その組の訪問を持たない人 (Z) が副担当なら同行に出す。
    vz = await _visit(db, await _make_patient(db, "TW-3"), y, start=time(13, 0), secondary=z)
    vz.visit_group_id = uuid.uuid4()
    await db.commit()

    resp = await build_monitor(db, TARGET, now=_utc(8, 0))
    x_row = _row_of(resp, x.id)
    assert [mv.visit_id for mv in x_row.visits] == [va.id, vk.id]
    assert x_row.companion_visit_ids == []
    y_row = _row_of(resp, y.id)
    assert [mv.visit_id for mv in y_row.visits] == [vb.id, vz.id]
    assert y_row.companion_visit_ids == []
    z_row = _row_of(resp, z.id)
    assert z_row.visits == []
    assert z_row.companion_visit_ids == [vk.id, vz.id]
    # 訪問本体の担当 (primary_staff_id) の意味は変えない。
    assert _find(resp, vb.id).staff_id == y.id


@pytest.mark.asyncio
async def test_rows_office_filter_uses_visit_course_office(db) -> None:
    """拠点の絞り込み = その拠点の訪問を 1 件でも持つ人 (中身は 1 日全部)。訪問の無い人は所属."""
    from app.models.staff import StaffWeeklyOverride

    inage = await _office(db, "稲毛", 1)
    tsuga = await _office(db, "都賀", 2)
    course_i = await _course(db, inage, "A")
    course_t = await _course(db, tsuga, "臨")
    helper = await _staff(db, "応援 稲毛", office=inage, code="S1")
    stay = await _staff(db, "稲毛 だけ", office=inage, code="S2")
    tsuga_off = await _staff(db, "都賀 休み", office=tsuga, code="S3")
    inage_off = await _staff(db, "稲毛 休み", office=inage, code="S4")
    v_help_i = await _visit(db, await _make_patient(db, "OF-1"), helper, course=course_i)
    v_help_t = await _visit(
        db, await _make_patient(db, "OF-2"), helper, course=course_t, start=time(11, 0)
    )
    await _visit(db, await _make_patient(db, "OF-3"), stay, course=course_i, start=time(10, 0))
    iso = TARGET.isocalendar()
    for st in (tsuga_off, inage_off):
        db.add(
            StaffWeeklyOverride(
                staff_id=st.id,
                iso_year=iso.year,
                iso_week=iso.week,
                weekday=TARGET.weekday(),
                override_type="off",
            )
        )
    await db.commit()

    only_tsuga = await build_monitor(db, TARGET, office_id=tsuga.id, now=_utc(8, 0))
    # 稲毛所属の応援者は都賀の訪問を持つので出る。行の中身はその人の 1 日全部。
    assert [r.staff_id for r in only_tsuga.staff] == [helper.id, tsuga_off.id]
    assert [mv.visit_id for mv in _row_of(only_tsuga, helper.id).visits] == [
        v_help_i.id,
        v_help_t.id,
    ]

    only_inage = await build_monitor(db, TARGET, office_id=inage.id, now=_utc(8, 0))
    assert [r.staff_id for r in only_inage.staff] == [helper.id, stay.id, inage_off.id]


@pytest.mark.asyncio
async def test_visit_course_staff_mismatch(db) -> None:
    """コース担当と訪問の担当が違う訪問に印 (手動の付け替えは除く)."""
    office = await _office(db, "稲毛", 1)
    owner = await _staff(db, "コース 担当", office=office, code="S1")
    other = await _staff(db, "別の 人", office=office, code="S2")
    course = await _course(db, office, "A", assigned=owner)
    same = await _visit(db, await _make_patient(db, "MM-1"), owner, course=course)
    diff = await _visit(
        db, await _make_patient(db, "MM-2"), other, course=course, start=time(10, 0)
    )
    manual = await _visit(
        db,
        await _make_patient(db, "MM-3"),
        other,
        course=course,
        start=time(11, 0),
        manual_override=True,
    )

    resp = await build_monitor(db, TARGET, now=_utc(8, 0))
    assert _find(resp, same.id).course_staff_mismatch is False
    assert _find(resp, diff.id).course_staff_mismatch is True
    assert _find(resp, manual.id).course_staff_mismatch is False


def test_staff_code_sort_key_matches_fe_compare_by_staff_code() -> None:
    """FE ``compareByStaffCode`` と同じ並び (lib/__tests__/kana-sort.test.ts と同じ例)."""
    from app.services.checkin.monitor import staff_code_sort_key

    rows = [("S010", "い"), ("", "う"), ("S2", "え"), ("S001", "お"), ("", "あ"), ("S2", "あ")]
    ordered = sorted(rows, key=lambda r: staff_code_sort_key(r[0], r[1]))
    assert [f"{c or '-'}:{n}" for c, n in ordered] == [
        "S001:お",
        "S2:あ",
        "S2:え",
        "S010:い",
        "-:あ",
        "-:う",
    ]


@pytest.mark.asyncio
async def test_nearby_returns_within_radius_sorted(db) -> None:
    await _make_patient(db, "N-near", lat=35.0001, lng=139.0000)  # ~11m
    await _make_patient(db, "N-mid", lat=35.0010, lng=139.0000)  # ~111m
    await _make_patient(db, "N-far", lat=35.0100, lng=139.0000)  # ~1.1km (out)

    from app.services.checkin.monitor import find_nearby_patients

    res = await find_nearby_patients(db, lat=35.0, lng=139.0, radius_m=150.0, limit=5)
    codes = [n.code for n in res.items]
    assert codes == ["N-near", "N-mid"]  # 距離昇順、far は除外。
    assert res.items[0].distance_m < res.items[1].distance_m


# ---------------------------------------------------------------------------
# API: RBAC + JST 境界
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_api_rbac(client, db) -> None:
    staff = await _make_staff(db, "S-rbac")
    staff_user = await _make_user(db, "mon-staff@example.com", "staff", staff_id=staff.id)
    manager_user = await _make_user(db, "mon-mgr@example.com", "manager")

    # RB (2026-07-08): PC版の表示統一で閲覧 GET は staff にも開放 → 200。
    res_staff = await client.get(
        "/api/v1/monitor", params={"date": TARGET.isoformat()}, headers=_bearer(staff_user)
    )
    assert res_staff.status_code == 200, res_staff.text

    # manager → 200。
    res_mgr = await client.get(
        "/api/v1/monitor", params={"date": TARGET.isoformat()}, headers=_bearer(manager_user)
    )
    assert res_mgr.status_code == 200, res_mgr.text
    body = res_mgr.json()
    assert body["date"] == TARGET.isoformat()
    assert "thresholds" in body
    assert "staff" in body


@pytest.mark.asyncio
async def test_nearby_api_rbac(client, db) -> None:
    # RB (2026-07-08): /monitor/nearby も閲覧 GET として staff に開放 → 200。
    staff = await _make_staff(db, "S-nearby-rbac")
    staff_user = await _make_user(db, "nearby-staff@example.com", "staff", staff_id=staff.id)
    manager_user = await _make_user(db, "nearby-mgr@example.com", "manager")
    params = {"lat": 35.0, "lng": 139.0}

    res_staff = await client.get(
        "/api/v1/monitor/nearby", params=params, headers=_bearer(staff_user)
    )
    assert res_staff.status_code == 200, res_staff.text

    res_mgr = await client.get(
        "/api/v1/monitor/nearby", params=params, headers=_bearer(manager_user)
    )
    assert res_mgr.status_code == 200, res_mgr.text


@pytest.mark.asyncio
async def test_monitor_api_returns_visit(client, db) -> None:
    admin = await _make_user(db, "mon-admin@example.com", "admin")
    staff = await _make_staff(db, "S-api")
    p = await _make_patient(db, "API-1", lat=35.0, lng=139.0)
    await _make_visit(db, p, staff, start=time(9, 0), end=time(10, 0))

    res = await client.get(
        "/api/v1/monitor", params={"date": TARGET.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    rows = body["staff"]
    assert len(rows) == 1
    assert rows[0]["visits"][0]["patient_code"] == "API-1"


@pytest.mark.asyncio
async def test_monitor_api_day_override_reason_admin_only(client, db) -> None:
    """休みの理由は admin への応答にだけ入る (staff には種別だけ)."""
    from app.models.staff import StaffWeeklyOverride

    off_staff = await _make_staff(db, "S-off-reason")
    viewer = await _make_staff(db, "S-viewer")
    admin = await _make_user(db, "mon-reason-admin@example.com", "admin")
    staff_user = await _make_user(db, "mon-reason-staff@example.com", "staff", staff_id=viewer.id)
    iso = TARGET.isocalendar()
    db.add(
        StaffWeeklyOverride(
            staff_id=off_staff.id,
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=TARGET.weekday(),
            override_type="off",
            reason="通院",
        )
    )
    await db.commit()

    def _override(body):
        row = next(r for r in body["staff"] if r["staff_id"] == str(off_staff.id))
        return row["day_override"]

    params = {"date": TARGET.isoformat()}
    res_admin = await client.get("/api/v1/monitor", params=params, headers=_bearer(admin))
    assert res_admin.status_code == 200, res_admin.text
    assert _override(res_admin.json())["reason"] == "通院"
    res_staff = await client.get("/api/v1/monitor", params=params, headers=_bearer(staff_user))
    assert res_staff.status_code == 200, res_staff.text
    staff_override = _override(res_staff.json())
    assert staff_override["kind"] == "off"
    assert staff_override["reason"] is None


@pytest.mark.asyncio
async def test_build_monitor_jst_midnight_same_day(db) -> None:
    """JST 00:30 (= 前日 UTC 15:30) でも当日 visit は当日として集計される."""
    staff = await _make_staff(db, "S-jst")
    p = await _make_patient(db, "JST-M", lat=35.0, lng=139.0)
    v = await _make_visit(db, p, staff, start=time(0, 10), end=time(1, 0))
    # JST 00:30 の now (前日 UTC 15:30)。
    now = datetime(2026, 6, 30, 0, 30, tzinfo=JST).astimezone(UTC)
    resp = await build_monitor(db, TARGET, now=now)
    mv = _find(resp, v.id)
    assert mv is not None
    # 00:10 開始・00:30 現在・到着なし → 猶予 (20分) 境界。grace 20 で 00:30>=00:30 → missing。
    assert mv.phase == PHASE_MISSING


@pytest.mark.asyncio
async def test_course_tag_uses_office_short_label(db) -> None:
    """札の略称は offices.short_label (PO 決定 2026-10-01)。未設定なら拠点名の 1 文字目."""
    inage = await _office(db, "稲毛", 1)  # short_label 未設定 → 「稲」
    tsuga = await _office(db, "都賀", 2)
    tsuga.short_label = "津"
    await db.commit()
    course_d = await _course(db, inage, "D")
    course_r = await _course(db, tsuga, "臨2")
    staff = await _staff(db, "略称 一号", office=inage, code="S1")
    p = [await _make_patient(db, f"SHORT-{i}") for i in range(2)]
    v1 = await _visit(db, p[0], staff, course=course_d, start=time(9, 0))
    v2 = await _visit(db, p[1], staff, course=course_r, start=time(10, 0))

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))
    row = _row_of(resp, staff.id)
    assert [t.label for t in row.course_tags] == ["稲D", "津臨2"]
    assert _find(resp, v1.id).course_tag == "稲D"
    assert _find(resp, v2.id).course_tag == "津臨2"
    # 凡例用に拠点チップへも略称を載せる (未設定は拠点名の 1 文字目)。
    assert [(o.name, o.short_label) for o in resp.offices] == [("稲毛", "稲"), ("都賀", "津")]
