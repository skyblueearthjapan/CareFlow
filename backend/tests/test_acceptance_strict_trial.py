"""受け入れ枠 (数週続けて空いているか) — 試算と同じ結果になること (設計書 §7-1).

材料は試算 (``acc_calc.py`` / 試算ページ) と同じもの。職員名は「職員A〜G」、患者名は
記号、休み以外の予定の件名は「予定」に置き換えてある (``tests/fixtures/acceptance_strict``)。

* 開発用の切り替え「予定だけで見る」(実績・祝日の除外・入職前の判定を止める) と
  上限 6 件 (試算の条件) で、週 40〜43 の 54 マスの ``weeks_ok`` と週ごとの人数が一致。
* 試算ページの組み合わせ (担い手・休みの扱い・4/8 週) も一致。8 週は 8/31 が
  データなし (8 月の予定が無い) なので月曜は 7 週で見る。
* 休みは段階 0 の ``app.core.leave`` で判定している (試算の時刻での分け方と同じ結果)。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from app.models import Office
from app.models.kaipoke_source_snapshot import KaipokeSourceSnapshot
from app.models.staff import Staff, StaffEvent, StaffShift, StaffWeeklyOverride
from app.services.scheduling import acceptance_strict_data as data
from app.services.scheduling.acceptance_strict import (
    MARK_MID,
    MARK_OK,
    StrictRules,
    summarize,
)

FIXTURES = Path(__file__).parent / "fixtures" / "acceptance_strict"
TODAY = date(2026, 10, 20)  # 週 43 (10/19〜10/24)。直近 8 週 = 週 36〜43
TRIAL_CAP = 6


def _sid(key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"acceptance-strict-trial/{key}")


def _hm(value: str | None) -> time | None:
    if not value:
        return None
    hour, minute = value.split(":")[:2]
    return time(int(hour), int(minute))


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def seed_trial(db, *, tasks_from_events: bool = False) -> Office:
    """試算の材料を入れる。``tasks_from_events`` なら kaipoke の予定を段階 0 の表 (tasks) に置く。"""
    m = _load("materials.json")
    office = Office(name="試験拠点", code="TRIAL")
    db.add(office)
    await db.flush()
    for s in m["staff"]:
        db.add(
            Staff(
                id=_sid(s["key"]),
                name=s["name"],
                role=s["role"],
                is_trainee=s["is_trainee"],
                primary_office_id=office.id,
            )
        )
    await db.flush()
    for sh in m["shifts"]:
        db.add(
            StaffShift(
                staff_id=_sid(sh["staff"]),
                weekday=sh["weekday"],
                is_on=sh["is_on"],
                start_time=_hm(sh["start"]),
                end_time=_hm(sh["end"]),
            )
        )
    for ov in m["overrides"]:
        db.add(
            StaffWeeklyOverride(
                staff_id=_sid(ov["staff"]),
                iso_year=ov["iso_year"],
                iso_week=ov["iso_week"],
                weekday=ov["weekday"],
                override_type=ov["type"],
                start_time=_hm(ov["start"]),
                end_time=_hm(ov["end"]),
            )
        )
    tasks: dict[date, list[dict]] = {}
    names = {s["key"]: s["name"] for s in m["staff"]}
    for i, ev in enumerate(m["events"]):
        starts = datetime.fromisoformat(ev["s"])
        ends = datetime.fromisoformat(ev["e"])
        if tasks_from_events and ev["source"] == "kaipoke":
            monday = date.fromordinal(starts.date().toordinal() - starts.weekday())
            tasks.setdefault(monday, []).append(
                {
                    "date": starts.date().isoformat(),
                    "start": starts.strftime("%H:%M"),
                    "end": ends.strftime("%H:%M"),
                    "title": ev["title"],
                    "staff_name": names[ev["staff"]],
                    "external_key": f"{i}:1:{starts.date().isoformat()}",
                }
            )
            continue
        db.add(
            StaffEvent(
                staff_id=_sid(ev["staff"]),
                event_type="event",
                starts_at=starts,
                ends_at=ends,
                title=ev["title"],
                source=ev["source"],
            )
        )
    now = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    for month, rows in m["plan_csv"].items():
        db.add(
            KaipokeSourceSnapshot(
                kind="plan_csv",
                period_start=date.fromisoformat(f"{month}-01"),
                rows=rows,
                row_count=len(rows),
                status="ok",
                fetched_at=now,
            )
        )
    for monday, rows in tasks.items():
        db.add(
            KaipokeSourceSnapshot(
                kind="tasks",
                period_start=monday,
                rows=rows,
                row_count=len(rows),
                covered_dates=None,
                status="ok",
                fetched_at=now,
            )
        )
    await db.commit()
    return office


def _rules(core: dict) -> StrictRules:
    r = core["rules"]
    return StrictRules(
        buffer_min=r["buffer_min"],
        lunch_min=r["lunch_min"],
        lunch_from=0,
        lunch_to=0,
        business_start=0,
        business_end=0,
        cap=r["cap"],
        hour_from=r["hours"][0],
        hour_to=r["hours"][1],
    )


def _summarize(core: dict, staff_keys: list[str], n_weeks: int, mode: str) -> dict:
    mondays = sorted(date.fromisoformat(w["monday"]) for w in core["weeks"])[-n_weeks:]
    days = {d["date"]: d["state"] for d in core["days"]}
    return summarize(
        days,
        core["cells"],
        [str(_sid(k)) for k in staff_keys],
        mondays,
        mode,
        core["weekdays"],
        _rules(core),
    )


VARIANTS = list(_load("expected.json").items())


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "expected"), VARIANTS, ids=[v[0] for v in VARIANTS])
async def test_matches_trial(db, name: str, expected: dict) -> None:
    data.clear_cache()
    office = await seed_trial(db)
    core = await data.build_strict_core(db, office.id, today=TODAY, plan_only=True, cap=TRIAL_CAP)
    mode = "real" if expected["off"] == "real" else "ignore"
    got = _summarize(core, expected["staff"], len(expected["weeks"]), mode)
    assert len(got) == 54
    diffs = []
    for key, cell in expected["cells"].items():
        wd, hour = (int(x) for x in key.split("-"))
        mine = got[(wd, hour)]
        if (mine["counted"], mine["ok"], mine["per_week"]) != (
            cell["n_weeks"],
            cell["weeks_ok"],
            cell["per_week"],
        ):
            diffs.append((key, mine, cell))
    assert diffs == []


@pytest.mark.asyncio
async def test_trial_four_staff_four_weeks_is_zero_ok_zero_mid(db) -> None:
    """職員 4 人・カイポケの予定・週 40〜43・上限 6 件 → ○0・△0 (試算の結論)。"""
    data.clear_cache()
    office = await seed_trial(db)
    core = await data.build_strict_core(db, office.id, today=TODAY, plan_only=True, cap=TRIAL_CAP)
    got = _summarize(core, ["A", "B", "C", "D"], 4, "real")
    marks = [c["mark"] for c in got.values()]
    assert marks.count(MARK_OK) == 0
    assert marks.count(MARK_MID) == 0
    # 休みを「無いとする」と土曜は全時間 4/4 週 (acc_kp_nooff)。
    nooff = _summarize(core, ["A", "B", "C", "D"], 4, "ignore")
    assert all(nooff[(5, h)]["mark"] == MARK_OK for h in range(9, 18))


@pytest.mark.asyncio
async def test_trial_same_result_from_tasks_snapshot(db) -> None:
    """kaipoke の予定を段階 0 の表 (tasks) から読んでも、試算と同じ結果になる。"""
    data.clear_cache()
    office = await seed_trial(db, tasks_from_events=True)
    core = await data.build_strict_core(db, office.id, today=TODAY, plan_only=True, cap=TRIAL_CAP)
    assert {d["tasks_from"] for d in core["days"] if d["state"] == "ok"} == {"snapshot"}
    for name, expected in VARIANTS:
        mode = "real" if expected["off"] == "real" else "ignore"
        got = _summarize(core, expected["staff"], len(expected["weeks"]), mode)
        for key, cell in expected["cells"].items():
            wd, hour = (int(x) for x in key.split("-"))
            assert got[(wd, hour)]["per_week"] == cell["per_week"], (name, key)


@pytest.mark.asyncio
async def test_trial_eight_weeks_monday_counts_seven(db) -> None:
    """8/31 は 8 月の予定が無い (データなし) ので、8 週の月曜は 7 週で見る。"""
    data.clear_cache()
    office = await seed_trial(db)
    core = await data.build_strict_core(db, office.id, today=TODAY, plan_only=True, cap=TRIAL_CAP)
    states = {d["date"]: d["state"] for d in core["days"]}
    assert states["2026-08-31"] == "no_data"
    got = _summarize(core, ["A", "B", "C", "D"], 8, "real")
    assert got[(0, 10)]["counted"] == 7
    assert got[(1, 10)]["counted"] == 8
