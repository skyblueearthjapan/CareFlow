"""受け入れ枠 (数週続けて空いているか) — レビューの指摘の試験.

* 残骸のジョブ (更新が止まったまま) は動いていないとみなす。
* 実績の入力待ちの判定は主担当の入った行を、重複をまとめて数える。
* 個別業務の職員名が空の行は結び付けの前に飛ばす。
* 職員の権限では「らく助に無い職員名」を件数だけにする。
* カイポケの休みと重ねて働ける時間が残らない日は休み。
* 画面 (TypeScript) の集計を試算と突き合わせるための応答を書き出す。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.models.kaipoke_job import KaipokeJob
from app.services.kaipoke import sources
from app.services.scheduling import acceptance_strict_data as data
from app.services.scheduling.acceptance_strict import (
    KaipokeLeave,
    StaffDay,
    StrictRules,
    compute_cell,
)
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG
from tests.test_acceptance_strict_api import (
    MONDAY,
    _bearer,
    _cell,
    _core,
    _day,
    _office,
    _row,
    _snap,
    _staff,
    _user,
)
from tests.test_acceptance_strict_trial import TODAY as TRIAL_TODAY
from tests.test_acceptance_strict_trial import TRIAL_CAP, _load, _sid, seed_trial

FRONTEND_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "lib"
    / "__tests__"
    / "fixtures"
    / "acceptanceStrictTrial.json"
)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    data.clear_cache()


def test_no_working_time_left_is_off() -> None:
    """らく助の時間変更 (9:30〜12:00) とカイポケの午前休 (13:00〜) → 働ける時間が無い → 休み。"""
    rules = StrictRules.from_config(DEFAULT_SCHEDULING_CONFIG)
    sd = StaffDay(
        staff_id="s1",
        day=date(2026, 10, 20),
        shift=(570, 1080),
        rk_type="custom_time",
        rk_time=(570, 720),
        kp_off=KaipokeLeave("am_off", "AM休み"),
    )
    real = compute_cell(sd, rules)["real"]
    assert real["state"] == "off"
    assert real["why"] == "AM休み（カイポケ）"


@pytest.mark.asyncio
async def test_waiting_counts_main_rows_once_and_skips_blank_task_names(db) -> None:
    office = await _office(db)
    a = await _staff(db, office, "職員A")
    # 予定: 主担当の入った行 2 件 (同じ行の重複は 1 件) + 担当なし 2 件 → 2 件と数える。
    plan = [_row(1, "09:00", "09:35", "職員A")]
    plan += [_row(14, "09:00", "09:35", "職員A", patient="P1")] * 2
    plan += [_row(14, "11:00", "11:35", "職員A", patient="P2")]
    plan += [_row(14, f"{h}:00", f"{h}:35", "-", patient=f"Q{h}") for h in (13, 14)]
    actual = [_row(14, "09:00", "09:35", "職員A", patient="P1")]  # 1 対 2 → 実績で見る
    await _snap(db, "plan_csv", date(2026, 10, 1), plan)
    await _snap(db, "actual_csv", date(2026, 10, 1), actual)
    blank = {"date": "2026-10-21", "start": "10:00", "end": "11:00", "title": "会議"}
    await _snap(db, "tasks", MONDAY, [{**blank, "staff_name": " "}])
    core = await _core(db, office)
    assert _day(core, date(2026, 10, 14))["src"] == "actual"
    assert _cell(core, a, date(2026, 10, 14))["n"] == 1
    assert core["sources"]["unmatched_staff_names"] == {}
    assert core["sources"]["unmatched_staff_count"] == 0


@pytest.mark.asyncio
async def test_staff_role_sees_unmatched_count_only(client, db) -> None:
    admin, staff_user = await _user(db, "admin"), await _user(db, "staff")
    office = await _office(db)
    await _staff(db, office, "職員A")
    await _snap(db, "plan_csv", date(2026, 10, 1), [_row(19, "09:00", "09:35", "知らない人")])
    await db.commit()
    url = f"/api/v1/acceptance-matrix/strict?office_id={office.id}"
    for_staff = (await client.get(url, headers=_bearer(staff_user))).json()["sources"]
    assert for_staff["unmatched_staff_names"] == {}
    assert for_staff["unmatched_staff_count"] == 1
    for_admin = (await client.get(url, headers=_bearer(admin))).json()["sources"]
    assert for_admin["unmatched_staff_names"] == {"知らない人": 1}


@pytest.mark.asyncio
async def test_stale_refresh_job_is_not_treated_as_running(client, db) -> None:
    admin = await _user(db, "admin")
    old = datetime.now(UTC) - timedelta(minutes=sources.STALE_JOB_MINUTES + 5)
    stale = KaipokeJob(
        job_type="fetch",
        status="running",
        week_start=MONDAY,
        params={"op": "acceptance-refresh", "targets": []},
        result_summary={"done": 0, "total": 3},
        created_at=old,
        updated_at=old,
    )
    newer_other_op = KaipokeJob(
        job_type="push", status="completed", week_start=MONDAY, params={"op": "events-outbound"}
    )
    db.add_all([stale, newer_other_op])
    await db.commit()

    assert data.is_stale(stale)
    assert data.job_view(stale)["stale"] is True
    assert data.job_view(newer_other_op)["stale"] is False
    assert await data.latest_refresh_job(db, active_only=True) is None
    res = await client.get(
        "/api/v1/acceptance-matrix/strict/refresh/latest", headers=_bearer(admin)
    )
    job = res.json()["job"]
    # 動いているものは無いので直近の 1 本 (op で絞るので他のジョブは返らない)。
    assert job["id"] == str(stale.id)
    assert job["stale"] is True
    assert job["updated_at"] is not None


@pytest.mark.asyncio
async def test_frontend_trial_fixture_is_current(db) -> None:
    """画面の集計 (frontend の vitest) で試算と突き合わせる応答 (職員 A〜D・予定だけ・上限 6 件)。

    ``ACCEPTANCE_WRITE_FIXTURE=1`` で書き出す。ふだんは書き出し済みのものと同じかを確かめる。
    """
    office = await seed_trial(db)
    core = await data.build_strict_core(
        db, office.id, today=TRIAL_TODAY, plan_only=True, cap=TRIAL_CAP
    )
    keep = {str(_sid(k)): k for k in "ABCD"}
    payload = {
        **core,
        "office_id": "office-trial",
        "offices": [{**o, "id": "office-trial"} for o in core["offices"]],
        "staff": [
            {**s, "id": keep[s["id"]], "office_id": "office-trial"}
            for s in core["staff"]
            if s["id"] in keep
        ],
        "cells": {keep[sid]: cells for sid, cells in core["cells"].items() if sid in keep},
        "overrides": [],
        "sources": {**core["sources"], "last_job": None},
    }
    expected = _load("expected.json")
    out = {
        "payload": payload,
        "expected": {k: expected[k] for k in ("staff4_4w", "staff4_4w_nooff", "staff4_8w")},
    }
    # 書き出したあとは frontend の prettier で整える。比べるのは中身 (JSON の値)。
    canonical = json.loads(json.dumps(out, ensure_ascii=False))
    if os.environ.get("ACCEPTANCE_WRITE_FIXTURE") == "1":
        FRONTEND_FIXTURE.write_text(
            json.dumps(canonical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    assert json.loads(FRONTEND_FIXTURE.read_text(encoding="utf-8")) == canonical
