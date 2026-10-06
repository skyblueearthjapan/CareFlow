"""「PC から実績の時刻を合わせる」のテスト.

正典設計書: ``docs/plans/pc-actual-time-edit-design-2026-10-06.md`` (D2 / Q3 / Q4)。

* D2: 打刻なしの訪問に、管理者だけ到着・退出を手で入れられる (スタッフは 409)。
  status は planned → in_progress → completed、消すと手で入れる前に戻る。
* Q4: 訪問モニターで、前日以前の到着だけの訪問は「退出未記録」(滞在を数え続けない)
  で要対応に載る。当日の訪問中は従来どおり。
* 打刻履歴: 「打刻なし」(``state=none``) の行に ``manual_arrival_allowed`` が載る。

ヘルパと時計の固定は ``test_actual_time_adjust.py`` と同じものを使う。
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.api.v1 import visit_history as visit_history_api
from app.api.v1 import visits as visits_api
from app.models import AuditLog
from app.models.visit_review import VisitReview
from app.services.checkin.monitor import build_monitor
from tests.test_actual_time_adjust import (
    _NOW,
    _adjustments,
    _at,
    _bearer,
    _checkin,
    _delete,
    _frozen_datetime,
    _hm,
    _monitor_visit,
    _nurse,
    _patient,
    _put,
    _reload_visit,
    _today,
    _user,
    _visit,
    _yesterday,
)

HISTORY_URL = "/api/v1/visit-history"


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch) -> None:
    """API 側の時計を ``_NOW`` に固定する (``test_actual_time_adjust`` と同じ)。"""
    frozen = _frozen_datetime(_NOW)
    monkeypatch.setattr(visits_api, "datetime", frozen)
    monkeypatch.setattr(visit_history_api, "datetime", frozen)


async def _planned_visit(db, code: str, *, day: date | None = None):
    """打刻なし (予定のみ・planned) の訪問 (予定 10:00–10:45) と担当・管理者を作る。"""
    day = day or _yesterday()
    staff, user = await _nurse(db, f"看護 {code}", f"{code.lower()}@example.com")
    admin = await _user(db, f"{code.lower()}-admin@example.com", role="admin")
    visit = await _visit(
        db,
        await _patient(db, code),
        staff,
        day,
        start=time(10, 0),
        end=time(10, 45),
        status="planned",
    )
    return staff, user, admin, visit


# ---------------------------------------------------------------------------
# D2: 到着の手入力 (管理者だけ)
# ---------------------------------------------------------------------------


async def test_admin_can_enter_manual_arrival_and_departure(client, db) -> None:
    _staff, _user_, admin, visit = await _planned_visit(db, "PC-A1")

    res = await _put(client, admin, visit, "arrival", "10:02")
    assert res.status_code == 200, res.text
    body = res.json()
    assert _hm(body["actual_arrival_at"]) == "10:02:00"
    assert body["actual_arrival_read_at"] is None
    assert (body["actual_arrival_adjusted"], body["actual_arrival_manual"]) == (True, True)
    assert body["status"] == "in_progress"
    # 到着が入ったので、手入力の入口は閉じ、合わせる枠が開く。
    assert body["actual_manual_arrival_allowed"] is False
    assert body["actual_adjust_allowed"] is True

    (row,) = await _adjustments(db, visit)
    assert (row.kind, row.base_checkin_id, row.prev_visit_status) == ("arrival", None, "planned")

    res = await _put(client, admin, visit, "departure", "10:47")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    assert _hm(body["actual_departure_at"]) == "10:47:00"
    assert body["actual_departure_manual"] is True

    # 監査ログは既存と同じ action で残る。
    logs = (
        await db.scalars(
            select(AuditLog).where(AuditLog.target_id == str(visit.id)).order_by(AuditLog.id)
        )
    ).all()
    assert [log.action for log in logs].count("visit_actual_time_adjust") == 2
    assert all(log.role == "admin" for log in logs if log.action.startswith("visit_actual"))
    await db.rollback()


async def test_staff_cannot_enter_manual_arrival(client, db) -> None:
    _staff, user, _admin, visit = await _planned_visit(db, "PC-A2")

    res = await _put(client, user, visit, "arrival", "10:02")
    assert res.status_code == 409
    assert res.json()["detail"] == "到着の記録がありません"
    assert await _adjustments(db, visit) == []
    # スタッフの画面には手入力の入口を出さない。
    res = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.json()["actual_manual_arrival_allowed"] is False
    assert res.json()["actual_adjust_allowed"] is False
    await db.rollback()


async def test_staff_cannot_add_departure_to_admin_manual_arrival(client, db) -> None:
    """管理者が手で入れた到着だけの訪問でも、スタッフの規則 (到着の読み取りが必要) は変えない。"""
    _staff, user, admin, visit = await _planned_visit(db, "PC-A3")
    assert (await _put(client, admin, visit, "arrival", "10:02")).status_code == 200

    res = await _put(client, user, visit, "departure", "10:47")
    assert res.status_code == 409
    assert res.json()["detail"] == "到着の記録がありません"
    res = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(user))
    assert res.json()["actual_adjust_allowed"] is False
    await db.rollback()


async def test_manual_arrival_validation(client, db) -> None:
    _staff, _user_, admin, visit = await _planned_visit(db, "PC-A4", day=_today())
    # 書き込み系の API より前に作っておく (commit の順序・test_actual_time_adjust の注意)。
    tomorrow = await _visit(
        db,
        await _patient(db, "PC-A4F"),
        _staff,
        _today() + timedelta(days=1),
        status="planned",
    )
    cancelled = await _visit(
        db, await _patient(db, "PC-A4C"), _staff, _yesterday(), status="cancelled"
    )

    # 今日の訪問: いま (_NOW = 20:00) より後は入れられない。
    res = await _put(client, admin, visit, "arrival", "20:30")
    assert res.status_code == 422
    assert res.json()["detail"] == "到着はいまの時刻より後にはできません"
    # 退出は到着より後。
    assert (await _put(client, admin, visit, "arrival", "10:02")).status_code == 200
    res = await _put(client, admin, visit, "departure", "10:02")
    assert res.status_code == 422
    assert "到着" in res.json()["detail"]
    assert (await _put(client, admin, visit, "departure", "10:47")).status_code == 200
    # 到着は退出より前。
    res = await _put(client, admin, visit, "arrival", "10:50")
    assert res.status_code == 422
    assert res.json()["detail"] == "到着は退出（10:47）より前の時刻にしてください"

    # まだ訪問日になっていない訪問は手で入れられない (従来どおり 409)。
    res = await _put(client, admin, tomorrow, "arrival", "10:02")
    assert res.status_code == 409
    # 取消済みの訪問も不可。
    res = await _put(client, admin, cancelled, "arrival", "10:02")
    assert res.status_code == 409
    assert res.json()["detail"] == "取り消された訪問には到着を手で入れられません"
    await db.rollback()


async def test_delete_manual_arrival_restores_status(client, db) -> None:
    _staff, _user_, admin, visit = await _planned_visit(db, "PC-A5")
    assert (await _put(client, admin, visit, "arrival", "10:02")).status_code == 200
    # 合わせ直しても、手で入れる前の status (planned) を引き継ぐ。
    assert (await _put(client, admin, visit, "arrival", "10:05")).status_code == 200
    assert (await _put(client, admin, visit, "departure", "10:47")).status_code == 200
    assert (await _reload_visit(db, visit)).status == "completed"

    # 手で入れた退出が残ったまま到着は消せない。
    res = await _delete(client, admin, visit, "arrival")
    assert res.status_code == 422
    assert res.json()["detail"] == "先に退出の手入力を消してください"

    res = await _delete(client, admin, visit, "departure")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "in_progress"
    assert res.json()["actual_departure_at"] is None

    res = await _delete(client, admin, visit, "arrival")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "planned"
    assert body["actual_arrival_at"] is None
    assert body["actual_arrival_manual"] is False
    assert body["actual_manual_arrival_allowed"] is True
    logs = (await db.scalars(select(AuditLog).where(AuditLog.target_id == str(visit.id)))).all()
    assert [log.action for log in logs].count("visit_actual_time_reset") == 2
    await db.rollback()


async def test_manual_arrival_with_departure_read_completes(client, db) -> None:
    """退出だけ読んである訪問に到着を手で入れると完了になる。"""
    staff, _user_, admin, visit = await _planned_visit(db, "PC-A6")
    day = visit.visit_date
    await _checkin(db, visit, staff, "departure", _at(day, 10, 46))

    res = await _put(client, admin, visit, "arrival", "10:01")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"
    assert _hm(res.json()["actual_departure_read_at"]) == "10:46:00"
    await db.rollback()


# ---------------------------------------------------------------------------
# Q4: 訪問モニターの「退出未記録」
# ---------------------------------------------------------------------------


async def test_monitor_past_day_without_departure_is_departure_missing(client, db) -> None:
    day = _yesterday()
    staff, _user_, admin, visit = await _planned_visit(db, "PC-M1", day=day)
    await _checkin(db, visit, staff, "arrival", _at(day, 10, 3))

    # 前日以前: 滞在を数え続けず、要対応 (review) に載る。
    past = _monitor_visit(await build_monitor(db, day, now=_NOW, viewer_is_admin=True), visit)
    assert past.departure_missing is True
    assert past.stay_minutes is None
    assert (past.phase, past.alert_level) == ("inprogress", "review")
    assert (past.adjust_allowed, past.manual_arrival_allowed) == (True, False)

    # 当日 (その日のうちに見た場合) は従来どおり「訪問中」で今まで数える。
    same_day = _monitor_visit(
        await build_monitor(db, day, now=_at(day, 10, 33), viewer_is_admin=True), visit
    )
    assert same_day.departure_missing is False
    assert (same_day.phase, same_day.alert_level, same_day.stay_minutes) == (
        "inprogress",
        "none",
        30,
    )

    # 管理者以外には合わせる枠を出さない。
    staff_view = _monitor_visit(await build_monitor(db, day, now=_NOW), visit)
    assert (staff_view.adjust_allowed, staff_view.manual_arrival_allowed) == (False, False)

    # 退出を手で入れると「退出未記録」から外れる。
    assert (await _put(client, admin, visit, "departure", "10:48")).status_code == 200
    done = _monitor_visit(await build_monitor(db, day, now=_NOW, viewer_is_admin=True), visit)
    assert done.departure_missing is False
    assert (done.phase, done.alert_level, done.stay_minutes) == ("done", "none", 45)
    assert done.departure_manual is True
    await db.rollback()


async def test_monitor_manual_arrival_only_is_departure_missing(client, db) -> None:
    """Q3: 打刻なしで到着だけ手で入れた訪問も、前日以前なら「退出未記録」。"""
    day = _yesterday()
    _staff, _user_, admin, visit = await _planned_visit(db, "PC-M2", day=day)

    before = _monitor_visit(await build_monitor(db, day, now=_NOW, viewer_is_admin=True), visit)
    assert before.phase == "missing"
    assert (before.adjust_allowed, before.manual_arrival_allowed) == (False, True)

    assert (await _put(client, admin, visit, "arrival", "10:02")).status_code == 200
    after = _monitor_visit(await build_monitor(db, day, now=_NOW, viewer_is_admin=True), visit)
    assert after.arrival is None  # 生の打刻は無い
    assert (after.arrival_manual, after.arrival_adjusted) == (True, True)
    assert after.departure_missing is True
    assert after.alert_level == "review"
    assert (after.adjust_allowed, after.manual_arrival_allowed) == (True, False)

    # API の応答にも項目が載る。
    res = await client.get(
        "/api/v1/monitor", headers=_bearer(admin), params={"date": day.isoformat()}
    )
    assert res.status_code == 200, res.text
    item = next(
        v for row in res.json()["staff"] for v in row["visits"] if v["visit_id"] == str(visit.id)
    )
    assert item["departure_missing"] is True
    assert item["arrival_manual"] is True
    assert item["stay_minutes"] is None
    await db.rollback()


# ---------------------------------------------------------------------------
# 打刻履歴: 「打刻なし」の絞り込みと手入力の入口
# ---------------------------------------------------------------------------


async def test_history_none_filter_carries_manual_arrival_flag(client, db) -> None:
    day = _yesterday()
    staff, user, admin, none_visit = await _planned_visit(db, "PC-H1", day=day)
    arrived = await _visit(db, await _patient(db, "PC-H1B"), staff, day, status="in_progress")
    await _checkin(db, arrived, staff, "arrival", _at(day, 13, 2))
    params = {"from": day.isoformat(), "to": day.isoformat(), "state": "none"}

    res = await client.get(HISTORY_URL, headers=_bearer(admin), params=params)
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [i["visit_id"] for i in items] == [str(none_visit.id)]
    (item,) = items
    assert (item["start_time"], item["end_time"]) == ("10:00", "10:45")
    assert (item["manual_arrival_allowed"], item["adjust_allowed"]) == (True, False)

    # スタッフには手入力の入口を出さない。
    res = await client.get(HISTORY_URL, headers=_bearer(user), params=params)
    (item,) = res.json()["items"]
    assert item["manual_arrival_allowed"] is False

    # 「退出なし」の行は合わせる枠 (退出の手入力) の対象。
    res = await client.get(HISTORY_URL, headers=_bearer(admin), params={**params, "state": "nodep"})
    (item,) = res.json()["items"]
    assert item["visit_id"] == str(arrived.id)
    assert (item["adjust_allowed"], item["manual_arrival_allowed"]) == (True, False)

    # 手で入れた到着は、行に印が付き「打刻なし」から外れる。
    assert (await _put(client, admin, none_visit, "arrival", "10:02")).status_code == 200
    res = await client.get(HISTORY_URL, headers=_bearer(admin), params=params)
    assert res.json()["items"] == []
    res = await client.get(HISTORY_URL, headers=_bearer(admin), params={**params, "state": "nodep"})
    manual = next(i for i in res.json()["items"] if i["visit_id"] == str(none_visit.id))
    assert (manual["arrival_manual"], manual["adjust_allowed"]) == (True, True)
    await db.rollback()


# ---------------------------------------------------------------------------
# レビュー指摘 (2026-10-07)
# ---------------------------------------------------------------------------


async def test_staff_cannot_delete_admin_hand_entered_times(client, db) -> None:
    """管理者が手で入れた到着・退出 (到着の読み取りなし) を、スタッフは消せない。"""
    _staff, user, admin, visit = await _planned_visit(db, "PC-R1")
    assert (await _put(client, admin, visit, "arrival", "10:02")).status_code == 200
    assert (await _put(client, admin, visit, "departure", "10:47")).status_code == 200

    for kind in ("departure", "arrival"):
        res = await _delete(client, user, visit, kind)
        assert res.status_code == 409, (kind, res.text)
        assert res.json()["detail"] == "到着の記録がありません"
    assert (await _reload_visit(db, visit)).status == "completed"
    # 消されていない (調整は 2 行のまま)。
    assert len(await _adjustments(db, visit)) == 2
    await db.rollback()


async def test_staff_can_still_reset_own_manual_departure_with_arrival_read(client, db) -> None:
    """到着の読み取りがある訪問の手入力の退出は、従来どおりスタッフ本人が消せる。"""
    staff, user, _admin, visit = await _planned_visit(db, "PC-R2")
    await _checkin(db, visit, staff, "arrival", _at(visit.visit_date, 10, 3))
    assert (await _put(client, user, visit, "departure", "10:48")).status_code == 200
    res = await _delete(client, user, visit, "departure")
    assert res.status_code == 200, res.text
    assert res.json()["actual_departure_at"] is None
    await db.rollback()


async def test_delete_manual_arrival_refused_when_departure_was_read(client, db) -> None:
    """手で入れた到着の後に退出が読み取られたら、到着は消せない (planned + 退出あり にしない)。"""
    staff, _user_, admin, visit = await _planned_visit(db, "PC-R3")
    assert (await _put(client, admin, visit, "arrival", "10:02")).status_code == 200
    await _checkin(db, visit, staff, "departure", _at(visit.visit_date, 10, 46))

    res = await _delete(client, admin, visit, "arrival")
    assert res.status_code == 422
    assert res.json()["detail"] == (
        "退出の読み取りがあるため、手で入れた到着は消せません。到着の時刻を合わせてください"
    )
    assert (await _reload_visit(db, visit)).status == "in_progress"
    await db.rollback()


async def test_manual_arrival_on_no_show_visit_is_recorded(client, db) -> None:
    """未訪問の記録がある訪問にも到着は入れられる。監査に no_show_present、表示は履歴扱い。"""
    day = _yesterday()
    staff, _user_, admin, visit = await _planned_visit(db, "PC-R4", day=day)
    await _checkin(db, visit, staff, "no_show", _at(day, 10, 20), reason="不在でした")

    before = _monitor_visit(await build_monitor(db, day, now=_NOW, viewer_is_admin=True), visit)
    assert (before.phase, before.reason, before.manual_arrival_allowed) == (
        "missing",
        "不在でした",
        True,
    )

    res = await _put(client, admin, visit, "arrival", "10:30")
    assert res.status_code == 200, res.text
    log = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.target_id == str(visit.id),
                AuditLog.action == "visit_actual_time_adjust",
            )
        )
    ).one()
    assert log.after["no_show_present"] is True
    assert log.after["manual"] is True

    after = _monitor_visit(await build_monitor(db, day, now=_NOW, viewer_is_admin=True), visit)
    assert after.phase == "inprogress"
    # 未訪問の理由はいまの状態として出さない (no_show に履歴として残る)。
    assert after.reason is None
    assert after.no_show is not None and after.no_show.reason == "不在でした"

    res = await client.get(
        HISTORY_URL,
        headers=_bearer(admin),
        params={"from": day.isoformat(), "to": day.isoformat()},
    )
    (item,) = res.json()["items"]
    assert (item["has_no_show"], item["no_show_reason"]) == (True, "不在でした")
    assert "未訪問の記録あり" in item["remarks"]
    await db.rollback()


async def test_audit_marks_manual_and_qr_adjustments(client, db) -> None:
    staff, user, _admin, visit = await _planned_visit(db, "PC-R5")
    await _checkin(db, visit, staff, "arrival", _at(visit.visit_date, 10, 6))
    assert (await _put(client, user, visit, "arrival", "10:00")).status_code == 200
    log = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.target_id == str(visit.id),
                AuditLog.action == "visit_actual_time_adjust",
            )
        )
    ).one()
    assert log.after["manual"] is False
    assert "no_show_present" not in log.after
    await db.rollback()


async def test_reviewed_does_not_clear_departure_missing(db) -> None:
    """「確認済み」にしても、退出未記録は退出を入れるまで要対応に残る (PO 決定 2026-10-07)。"""
    day = _yesterday()
    staff, _user_, admin, visit = await _planned_visit(db, "PC-R6", day=day)
    await _checkin(db, visit, staff, "arrival", _at(day, 10, 3))
    db.add(VisitReview(visit_id=visit.id, reviewed_by=admin.id, reviewed_at=_NOW))
    await db.commit()

    past = _monitor_visit(await build_monitor(db, day, now=_NOW, viewer_is_admin=True), visit)
    assert past.reviewed is True
    assert past.departure_missing is True
    assert past.alert_level == "review"

    # 当日の訪問中 (退出未記録ではない) は従来どおり確認済みで外れる。
    same_day = _monitor_visit(
        await build_monitor(db, day, now=_at(day, 10, 33), viewer_is_admin=True), visit
    )
    assert same_day.alert_level == "none"
    await db.rollback()
