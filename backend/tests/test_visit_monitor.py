"""QR 訪問チェックイン Phase 3 — 訪問モニター集計のテスト.

- 実効状態の合成 (compute_phase / compute_alert の全分岐 + build_monitor 統合):
  future / awaiting / inprogress / done / missing × none / review / mismatch / missing。
- 次訪問までの距離 (haversine, 時刻順)。
- JST 境界 (深夜の当日判定)。
- RBAC (staff は 403, admin/manager は 200)。
- office_id フィルタ。
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


@pytest.mark.asyncio
async def test_build_monitor_unassigned_split_by_course(db) -> None:
    """担当未設定の visits はコース別に行が分かれる (PO 報告 2026-07-03).

    従来は primary_staff_id=None を 1 行に集約していたため A-D コースの訪問が
    混ざって「データが壊れた」ように見えた。コース別行 + コース無しは別 1 行。
    """
    from app.models import Course

    office = Office(name="モニタ拠点")
    db.add(office)
    await db.commit()
    await db.refresh(office)

    course_a = Course(
        iso_year=2026,
        iso_week=27,
        weekday=1,  # TARGET=2026-06-30 (火)
        code="A",
        course_status="course_fixed",
        office_id=office.id,
    )
    course_b = Course(
        iso_year=2026,
        iso_week=27,
        weekday=1,
        code="B",
        course_status="course_fixed",
        office_id=office.id,
    )
    db.add_all([course_a, course_b])
    await db.commit()
    await db.refresh(course_a)
    await db.refresh(course_b)

    pa = await _make_patient(db, "UN-A")
    pb = await _make_patient(db, "UN-B")
    pc = await _make_patient(db, "UN-NONE")
    staff = await _make_staff(db, "担当あり", office_id=office.id)
    pd = await _make_patient(db, "AS-D")

    # 未割当 × コース A / B / コース無し + 担当ありの通常 visit。
    for patient, course_id, start in (
        (pa, course_a.id, time(9, 0)),
        (pb, course_b.id, time(10, 0)),
        (pc, None, time(11, 0)),
    ):
        db.add(
            Visit(
                patient_id=patient.id,
                primary_staff_id=None,
                course_id=course_id,
                visit_date=TARGET,
                start_time=start,
                end_time=time(start.hour, 35),
                type="regular",
                status="planned",
            )
        )
    await db.commit()
    await _make_visit(db, pd, staff)

    resp = await build_monitor(db, TARGET, now=_utc(13, 30))

    unassigned_rows = [r for r in resp.staff if r.staff_id is None]
    # A / B / コース無し の 3 行に分かれる (従来は 1 行に集約されていた)。
    assert len(unassigned_rows) == 3
    labels = sorted((r.course_label or "") for r in unassigned_rows)
    assert labels == ["", "Aコース", "Bコース"]
    # 各行の visits は当該コースのみ。
    for r in unassigned_rows:
        assert len(r.visits) == 1
    # 担当ありの行は従来どおり 1 行。
    assigned_rows = [r for r in resp.staff if r.staff_id is not None]
    assert len(assigned_rows) == 1


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
