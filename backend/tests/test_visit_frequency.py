"""訪問頻度 (毎週 / 隔週 / 月の第何週) を予定づくりに反映する (段 1・backend).

正典: ``docs/plans/biweekly-monthly-design-2026-10-30.md`` §3 / §7。

検証観点:
  - 起点から 0・1・2・52・53 週目 (2026→2027 の年またぎ・2026 は 53 週)
  - 第 1〜5 週・第 5 週の無い月・月をまたぐ週
  - 枠の A/B が交互に出る
  - 設定不足 (起点なし・第何週なし) は毎週 + 状態
  - 週の生成 / 固定訪問への作り直し / 自動割当の孤児プール / 前の週のコピー /
    保留プール・エージェントの希望回数 / 提案の必要日数 が、お休みの週に予定を作らない
  - 今週だけの追加 (manual_week) は消えない
  - 保存の検査 (起点が月曜でない → 422・第何週の正規化)・枠の A/B の読み書き
"""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User, Visit
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.visit import VISIT_SOURCE_MANUAL_WEEK, VISIT_STATUS_PLANNED
from app.schemas.v2.patient import WeeklyPatternV2
from app.services.scheduling.visit_frequency import (
    desired_count_for_week,
    frequency_status,
    is_patient_visit_week,
    is_slot_visit_week,
    normalize_visit_weeks,
    nth_weekday_of_month,
    upcoming_visit_dates,
    validate_biweekly_anchor,
)

# 起点 = 2026-11-02 (月) = ISO 2026-W45
ANCHOR = date(2026, 11, 2)


def _biweekly(anchor: date | None = ANCHOR, **extra: Any) -> dict:
    wp: dict[str, Any] = {"visit_frequency": "biweekly", **extra}
    if anchor is not None:
        wp["biweekly_anchor"] = anchor.isoformat()
    return wp


def _monthly(weeks: str | None, **extra: Any) -> dict:
    wp: dict[str, Any] = {"visit_frequency": "monthly", **extra}
    if weeks is not None:
        wp["visit_weeks"] = weeks
    return wp


def _iso(d: date) -> tuple[int, int]:
    c = d.isocalendar()
    return c.year, c.week


# ---------------------------------------------------------------------------
# 純関数
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("weeks_from_anchor", "a_week"),
    [(0, True), (1, False), (2, True), (52, True), (53, False), (-1, False), (-2, True)],
)
def test_biweekly_anchor_parity_across_year(weeks_from_anchor: int, a_week: bool) -> None:
    week = ANCHOR + timedelta(weeks=weeks_from_anchor)
    wp = _biweekly()
    assert is_slot_visit_week(wp, "every", week, 1) is a_week
    assert is_slot_visit_week(wp, "A", week, 1) is a_week
    assert is_slot_visit_week(wp, "B", week, 1) is (not a_week)


def test_biweekly_does_not_use_iso_week_parity() -> None:
    """2026 は 53 週. 起点 W45 (奇数) から 52 週後は 2027-W44 (偶数) でも A の週."""
    week52 = ANCHOR + timedelta(weeks=52)
    assert _iso(ANCHOR) == (2026, 45)
    assert _iso(week52) == (2027, 44)
    assert is_patient_visit_week(_biweekly(), week52) is True
    # 週の途中の日を渡しても、その週の月曜に寄せて判定する
    assert is_slot_visit_week(_biweekly(), "A", week52 + timedelta(days=3), 3) is True


def test_ab_slots_alternate() -> None:
    wp = _biweekly()
    slots = [(1, "A"), (4, "B")]  # 火=A・金=B
    dates = upcoming_visit_dates(wp, slots, ANCHOR, 4)
    assert dates == [
        date(2026, 11, 3),  # 1 週目 (A) 火
        date(2026, 11, 13),  # 2 週目 (B) 金
        date(2026, 11, 17),  # 3 週目 (A) 火
        date(2026, 11, 27),  # 4 週目 (B) 金
    ]
    # どちらの週にも訪問がある (患者単位ではお休みの週が無い)
    for k in range(4):
        assert is_patient_visit_week(wp, ANCHOR + timedelta(weeks=k), slots) is True
    # 枠を知らないときは A の週だけ
    assert is_patient_visit_week(wp, ANCHOR + timedelta(weeks=1)) is False


def test_nth_weekday_and_monthly_boundaries() -> None:
    assert nth_weekday_of_month(date(2026, 10, 29)) == 5
    assert nth_weekday_of_month(date(2026, 11, 5)) == 1
    assert nth_weekday_of_month(date(2026, 11, 19)) == 3
    wp = _monthly("1,3")
    # 10/26 の週: 木 10/29 は第 5 木曜 → 訪問なし
    assert is_slot_visit_week(wp, "every", date(2026, 10, 26), 3) is False
    # 11/2 の週: 木 11/5 = 第 1 → 訪問
    assert is_slot_visit_week(wp, "every", date(2026, 11, 2), 3) is True
    # 月をまたぐ週 (11/30 月〜12/6 日): 月 11/30 は第 5 → なし・木 12/3 は第 1 → あり
    assert is_slot_visit_week(wp, "every", date(2026, 11, 30), 0) is False
    assert is_slot_visit_week(wp, "every", date(2026, 11, 30), 3) is True
    # 月次では枠の A/B は見ない
    assert is_slot_visit_week(wp, "B", date(2026, 11, 2), 3) is True
    # 枠も希望曜日も無い患者単位: その週の **月曜の日付** で判定する
    # (7 日のどれかで判定すると、月の境目の週が 2 週続けて訪問の週になるため)
    assert is_patient_visit_week(wp, date(2026, 11, 23)) is False  # 月 11/23 = 第 4 週
    assert is_patient_visit_week(wp, date(2026, 11, 30)) is False  # 月 11/30 = 第 5 週
    assert is_patient_visit_week(wp, date(2026, 12, 7)) is True  # 月 12/7 = 第 1 週
    assert is_patient_visit_week(wp, date(2026, 10, 26)) is False  # 月 10/26 = 第 4 週
    assert is_patient_visit_week(wp, date(2026, 11, 2)) is True  # 月 11/2 = 第 1 週
    # 第 1 週だけの方が、月の境目で 2 週続けて訪問の週にならない
    first_only = _monthly("1")
    hits = [
        is_patient_visit_week(first_only, date(2026, 11, 23) + timedelta(weeks=k)) for k in range(4)
    ]
    assert hits == [False, False, True, False]  # 11/23・11/30・12/7・12/14
    assert (
        is_patient_visit_week(_monthly("1,3", preferred_weekdays=["Thu"]), date(2026, 10, 26))
        is False
    )
    entries_wp = _monthly("1,3", entries=[{"weekday": "Thu"}], preferred_weekdays=["Sun"])
    assert is_patient_visit_week(entries_wp, date(2026, 10, 26)) is False
    assert upcoming_visit_dates(wp, [(3, "every")], date(2026, 10, 26), 4) == [
        date(2026, 11, 5),
        date(2026, 11, 19),
        date(2026, 12, 3),
        date(2026, 12, 17),
    ]


def test_monthly_fifth_week_skips_months_without_it() -> None:
    wp = _monthly("5")
    # 2027 の木曜: 1 月 (7〜28)・2 月 (4〜25)・3 月 (4〜25) に第 5 木曜は無い. 4/29 が最初.
    assert upcoming_visit_dates(wp, [(3, "every")], date(2027, 1, 1), 1) == [date(2027, 4, 29)]
    assert is_patient_visit_week(wp, date(2027, 2, 22), [(3, "every")]) is False


def test_missing_settings_fall_back_to_every_week() -> None:
    no_anchor = _biweekly(anchor=None, visit_weeks="1,3")  # 今ある隔週 2 名の形
    assert frequency_status(no_anchor) == "missing_anchor"
    no_weeks = _monthly(None)
    assert frequency_status(no_weeks) == "missing_weeks"
    assert frequency_status(_monthly("")) == "missing_weeks"
    assert frequency_status({}) == "ok"
    assert frequency_status(None) == "ok"
    assert frequency_status(_biweekly()) == "ok"
    for wp in (no_anchor, no_weeks, {}, None, {"visit_frequency": "every"}):
        for k in range(6):
            week = ANCHOR + timedelta(weeks=k)
            assert is_slot_visit_week(wp, "B", week, 2) is True
            assert is_patient_visit_week(wp, week) is True
            assert desired_count_for_week(wp, 2, week, [(2, "B")]) == 2
            assert desired_count_for_week(wp, None, week) is None


def test_desired_count_for_week() -> None:
    wp = _biweekly()
    off = ANCHOR + timedelta(weeks=1)
    assert desired_count_for_week(wp, 1, ANCHOR) == 1
    assert desired_count_for_week(wp, 1, off) == 0
    # 枠を渡すと、その週に当たる枠の曜日数が上限 (A=火・B=金, 回数 2 → 各週 1)
    slots = [(1, "A"), (4, "B")]
    assert desired_count_for_week(wp, 2, ANCHOR, slots) == 1
    assert desired_count_for_week(wp, 2, off, slots) == 1
    assert desired_count_for_week(wp, None, off, slots) == 1
    # 2 名体制 (同じ曜日の slot 0/1) は 1 日と数える
    assert desired_count_for_week(wp, 3, ANCHOR, [(1, "every"), (1, "every")]) == 1
    assert desired_count_for_week(wp, 3, off, [(1, "every"), (1, "every")]) == 0


def test_validators() -> None:
    assert normalize_visit_weeks("1,3") == "1,3"
    assert normalize_visit_weeks(" 3 , 1 ") == "1,3"
    assert normalize_visit_weeks("１，３") == "1,3"
    assert normalize_visit_weeks("1、3") == "1,3"
    assert normalize_visit_weeks([3, 1]) == "1,3"
    assert normalize_visit_weeks("") is None
    assert normalize_visit_weeks(None) is None
    for bad in ("0", "6", "1,1", "a", "1,x", True):
        with pytest.raises(ValueError):
            normalize_visit_weeks(bad)
    assert validate_biweekly_anchor("2026-11-02") == "2026-11-02"
    assert validate_biweekly_anchor("") is None
    for bad in ("2026-11-03", "2026/11/02", "x"):
        with pytest.raises(ValueError):
            validate_biweekly_anchor(bad)


def test_weekly_pattern_schema_validation() -> None:
    wp = WeeklyPatternV2.model_validate(
        {"visit_frequency": "monthly", "visit_weeks": "３, 1", "other_key": 1}
    )
    assert wp.visit_weeks == "1,3"
    assert wp.model_dump()["other_key"] == 1  # extra="allow" のまま
    assert WeeklyPatternV2.model_validate({"biweekly_anchor": ""}).biweekly_anchor is None
    with pytest.raises(ValueError):
        WeeklyPatternV2.model_validate({"biweekly_anchor": "2026-11-04"})
    with pytest.raises(ValueError):
        WeeklyPatternV2.model_validate({"visit_weeks": "1,7"})


# ---------------------------------------------------------------------------
# DB を使う経路
# ---------------------------------------------------------------------------


async def _admin(db) -> dict[str, str]:
    u = User(email="vf-admin@example.com", password_hash=hash_password("pw"), role="admin")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    token = create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _patient(
    db,
    code: str,
    weekly_pattern: dict | None,
    slots: list[tuple[int, str]] = (),  # type: ignore[assignment]
    *,
    office: Office | None = None,
    template: CourseTemplate | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status="active",
        lat=35.65,
        lng=140.10,
        weekly_pattern=weekly_pattern,
        primary_office_id=office.id if office is not None else None,
        special_week_active=[],
    )
    db.add(p)
    await db.flush()
    for wd, vw in slots:
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=35,
                visit_week=vw,
                course_template_id=template.id if template is not None else None,
            )
        )
    await db.flush()
    return p


async def _office_with_staff(db, name: str) -> tuple[Office, CourseTemplate]:
    office = Office(name=name, lat=35.6, lng=140.1)
    db.add(office)
    await db.flush()
    tpl = CourseTemplate(
        office_id=office.id,
        label="A",
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
    )
    db.add(tpl)
    staff = Staff(name=f"{name}-職員", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    for wd in range(6):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))
    await db.flush()
    return office, tpl


async def _live_visits(db, patient_id, monday: date) -> list[Visit]:
    return list(
        (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.patient_id == patient_id,
                    Visit.deleted_at.is_(None),
                    Visit.visit_date >= monday,
                    Visit.visit_date <= monday + timedelta(days=6),
                )
                .order_by(Visit.visit_date)
            )
        ).all()
    )


@pytest.mark.asyncio
async def test_layer1_skips_off_week_slots(db) -> None:
    from app.services.scheduling.layer1_expander import Layer1Expander

    every = await _patient(db, "VF-E", None, [(0, "every")])
    bi = await _patient(db, "VF-B", _biweekly(frequency_per_week=1), [(0, "every")])
    ab = await _patient(db, "VF-AB", _biweekly(frequency_per_week=1), [(1, "A"), (4, "B")])
    missing = await _patient(db, "VF-M", _biweekly(anchor=None, visit_weeks="1,3"), [(2, "A")])
    await db.commit()

    for k in range(4):
        monday = ANCHOR + timedelta(weeks=k)
        y, w = _iso(monday)
        res = await Layer1Expander().expand_week(db, iso_year=y, iso_week=w)
        await db.commit()
        a_week = k % 2 == 0
        assert len(await _live_visits(db, every.id, monday)) == 1
        bi_visits = await _live_visits(db, bi.id, monday)
        assert len(bi_visits) == (1 if a_week else 0)
        ab_visits = await _live_visits(db, ab.id, monday)
        assert [v.visit_date.weekday() for v in ab_visits] == ([1] if a_week else [4])
        # 起点の無い隔週は毎週 (今までと同じ) + 設定不足として控える
        assert len(await _live_visits(db, missing.id, monday)) == 1
        assert (missing.id, "missing_anchor") in res.frequency_incomplete
        assert (bi.id in res.frequency_off_week_patient_ids) is (not a_week)
        assert all(pe.patient_id != bi.id for pe in res.pool)


@pytest.mark.asyncio
async def test_layer1_no_slot_patient_off_week_has_no_pool_entry(db) -> None:
    from app.services.scheduling.layer1_expander import Layer1Expander

    entry = {
        "weekday": "Thu",
        "time_type": "固定",
        "preferred_start": "10:00",
        "preferred_end": "10:35",
        "service_minutes": 35,
        "staff_count": 1,
    }
    monthly = await _patient(db, "VF-MO", _monthly("1,3", entries=[entry]))
    pool_only = await _patient(
        db, "VF-PO", _monthly("1", frequency_per_week=1, preferred_weekdays=["Thu"])
    )
    await db.commit()

    # 10/26 の週 (木 10/29 = 第 5 木曜) → 訪問も保留プールも無し
    y, w = _iso(date(2026, 10, 26))
    res = await Layer1Expander().expand_week(db, iso_year=y, iso_week=w)
    await db.commit()
    assert await _live_visits(db, monthly.id, date(2026, 10, 26)) == []
    assert {pe.patient_id for pe in res.pool}.isdisjoint({monthly.id, pool_only.id})

    # 11/2 の週 (第 1 週) → entries の木曜が作られ、枠も entries も無い方はプールへ
    y, w = _iso(date(2026, 11, 2))
    res = await Layer1Expander().expand_week(db, iso_year=y, iso_week=w)
    await db.commit()
    visits = await _live_visits(db, monthly.id, date(2026, 11, 2))
    assert [v.visit_date for v in visits] == [date(2026, 11, 5)]
    assert pool_only.id in {pe.patient_id for pe in res.pool}


@pytest.mark.asyncio
async def test_manual_week_visit_survives_regeneration_in_off_week(db) -> None:
    from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed
    from app.services.scheduling.layer1_expander import Layer1Expander

    office, _tpl = await _office_with_staff(db, "VF-manual")
    bi = await _patient(db, "VF-MW", _biweekly(frequency_per_week=1), [(0, "every")], office=office)
    off = ANCHOR + timedelta(weeks=1)
    manual = Visit(
        patient_id=bi.id,
        visit_date=off + timedelta(days=2),
        start_time=time(14, 0),
        end_time=time(14, 35),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source=VISIT_SOURCE_MANUAL_WEEK,
        required_staff_count=1,
    )
    db.add(manual)
    await db.commit()

    y, w = _iso(off)
    await Layer1Expander().expand_week(db, iso_year=y, iso_week=w)
    await db.commit()
    await reset_visits_to_fixed(db, iso_year=y, iso_week=w, office_ids=[office.id])
    await db.commit()
    live = await _live_visits(db, bi.id, off)
    assert [v.id for v in live] == [manual.id]


@pytest.mark.asyncio
async def test_reset_visits_to_fixed_skips_off_week_slots(db) -> None:
    from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed

    office, _tpl = await _office_with_staff(db, "VF-reset")
    bi = await _patient(db, "VF-R", _biweekly(), [(0, "A"), (3, "B")], office=office)
    every = await _patient(db, "VF-RE", None, [(2, "B")], office=office)  # 毎週は A/B を見ない
    # お休みの週に古い自動の予定が残っている (頻度を入れる前に作られた週)
    off = ANCHOR + timedelta(weeks=1)
    stale = Visit(
        patient_id=bi.id,
        visit_date=off,
        start_time=time(10, 0),
        end_time=time(10, 35),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
    )
    db.add(stale)
    await db.commit()

    for k in range(2):
        monday = ANCHOR + timedelta(weeks=k)
        y, w = _iso(monday)
        await reset_visits_to_fixed(db, iso_year=y, iso_week=w, office_ids=[office.id])
        await db.commit()
        got = [v.visit_date.weekday() for v in await _live_visits(db, bi.id, monday)]
        assert got == ([0] if k == 0 else [3])
        assert len(await _live_visits(db, every.id, monday)) == 1
    await db.refresh(stale)
    assert stale.deleted_at is not None


@pytest.mark.asyncio
async def test_auto_allocator_orphan_pool_respects_off_week(db) -> None:
    from app.services.scheduling.auto_allocator_v2 import run_v2_pipeline

    office, _tpl = await _office_with_staff(db, "VF-orphan")
    bi = await _patient(db, "VF-O", _biweekly(frequency_per_week=1), [(0, "every")], office=office)
    no_slot = await _patient(
        db,
        "VF-ON",
        _biweekly(frequency_per_week=1, preferred_weekdays=["Wed"], time_type="午前"),
        office=office,
    )
    await db.commit()

    for k, a_week in ((0, True), (1, False)):
        y, w = _iso(ANCHOR + timedelta(weeks=k))
        result = await run_v2_pipeline(
            db, iso_year=y, iso_week=w, office_ids=[office.id], mode="diff_add"
        )
        pool_ids = {v.patient_id for v in result["pool_visits"]}
        assert (bi.id in pool_ids) is a_week
        assert (no_slot.id in pool_ids) is a_week
        if not a_week:
            before_ids = {v.patient_id for v in result["before_visits"]}
            assert bi.id not in before_ids


@pytest.mark.asyncio
async def test_g94_desired_count_off_week(db) -> None:
    from app.services.scheduling.auto_allocator_v2 import _g93_desired_weekdays, _g94_desired_count

    p = Patient(code="VF-G", name="g94", status="active", weekly_pattern=_biweekly())
    rows = [
        PatientFixedVisit(mode="normal", weekday=1, slot_index=0, visit_week="A"),
        PatientFixedVisit(mode="normal", weekday=4, slot_index=0, visit_week="A"),
    ]
    off = ANCHOR + timedelta(weeks=1)
    assert _g94_desired_count(p, rows) == 2  # 週を渡さなければ従来どおり
    assert _g94_desired_count(p, rows, week_start=ANCHOR) == 2
    assert _g94_desired_count(p, rows, week_start=off) == 0
    assert _g93_desired_weekdays(p, rows, week_start=off) == set()
    assert _g93_desired_weekdays(p, rows, week_start=ANCHOR) == {1, 4}


@pytest.mark.asyncio
async def test_copy_week_rebuilds_frequency_patients_from_slots(db) -> None:
    from app.services.scheduling.copy_week import apply_copy_plan, build_copy_plan

    office, tpl = await _office_with_staff(db, "VF-copy")
    bi = await _patient(db, "VF-C", _biweekly(), [(0, "every")], office=office, template=tpl)
    every = await _patient(db, "VF-CE", None, [(0, "every")], office=office, template=tpl)
    source = ANCHOR  # A の週
    for p in (bi, every):
        db.add(
            Visit(
                patient_id=p.id,
                visit_date=source + timedelta(days=2),  # 水曜 (枠は月曜)
                start_time=time(13, 0),
                end_time=time(13, 35),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="auto",
                required_staff_count=1,
            )
        )
    await db.commit()

    # 写す先 = B の週 (お休み): 隔週の方は写さず、作りもしない
    off = source + timedelta(weeks=1)
    plan = await build_copy_plan(
        db,
        source_monday=source,
        target_monday=off,
        exclude_visit_ids=set(),
        fill_from_fixed=False,
        today=source,
    )
    assert plan.skipped["visit_frequency"] == 1
    assert {it.patient_id for it in plan.items} == {every.id}
    assert all(it.patient_id != bi.id for it in plan.fill_items)

    # 写す先 = A の週: 写さず、枠 (月曜 10:00) から作る (補うの指定が無くても)
    on = source + timedelta(weeks=2)
    plan = await build_copy_plan(
        db,
        source_monday=source,
        target_monday=on,
        exclude_visit_ids=set(),
        fill_from_fixed=False,
        today=source,
    )
    assert plan.skipped["visit_frequency"] == 1
    fills = [it for it in plan.fill_items if it.patient_id == bi.id]
    assert [(it.target_date, it.start_time) for it in fills] == [(on, time(10, 0))]
    assert all(m.patient.id != bi.id for m in plan.missing_fixed)
    await apply_copy_plan(db, plan)
    await db.commit()
    live = await _live_visits(db, bi.id, on)
    assert [(v.visit_date, v.start_time, v.source) for v in live] == [(on, time(10, 0), "auto")]


def test_pool_and_agent_desired_counts_are_zero_on_off_week() -> None:
    from app.api.v1.agent import _desired_per_week
    from app.services.scheduling.pool_bulk_inserter import _desired_weekly_visits

    wp = _biweekly(frequency_per_week=2)
    p = Patient(code="VF-P", name="pool", status="active", weekly_pattern=wp)
    off = ANCHOR + timedelta(weeks=1)
    assert _desired_weekly_visits(p) == 2
    assert _desired_weekly_visits(p, week_start=ANCHOR) == 2
    assert _desired_weekly_visits(p, week_start=off) == 0
    rows = [PatientFixedVisit(mode="normal", weekday=1, visit_week="B")]
    assert _desired_weekly_visits(p, week_start=off, fixed_rows=rows) == 1
    assert _desired_weekly_visits(p, week_start=ANCHOR, fixed_rows=rows) == 0
    assert _desired_per_week(wp) == 2
    assert _desired_per_week(wp, week_start=off) == 0
    assert _desired_per_week(wp, week_start=off, slots=[(1, "B")]) == 1


@pytest.mark.asyncio
async def test_pool_overview_marks_off_week(client, db) -> None:
    headers = await _admin(db)
    office, _tpl = await _office_with_staff(db, "VF-pool")
    bi = await _patient(db, "VF-PV", _biweekly(frequency_per_week=1), office=office)
    await db.commit()
    y, w = _iso(ANCHOR + timedelta(weeks=1))
    resp = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        json={
            "iso_year": y,
            "iso_week": w,
            "office_id": str(office.id),
            "patient_ids": [str(bi.id)],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["off_week"] is True
    assert item["top_excluded_reason"] == "off_week"
    assert item["candidate_count"] == 0


@pytest.mark.asyncio
async def test_propose_required_days_off_week(db) -> None:
    from app.api.v1.schedule_v2 import _frequency_adjusted_required_days

    p = await _patient(db, "VF-PR", _biweekly(frequency_per_week=2), [(1, "A"), (4, "A")])
    await db.commit()
    y, w = _iso(ANCHOR)
    assert (
        await _frequency_adjusted_required_days(
            db, patient_id=p.id, iso_year=y, iso_week=w, required_days=2
        )
        == 2
    )
    y, w = _iso(ANCHOR + timedelta(weeks=1))
    assert (
        await _frequency_adjusted_required_days(
            db, patient_id=p.id, iso_year=y, iso_week=w, required_days=2
        )
        == 0
    )


@pytest.mark.asyncio
async def test_patient_patch_validates_frequency_keys(client, db) -> None:
    headers = await _admin(db)
    p = await _patient(db, "VF-PATCH", None)
    await db.commit()
    url = f"/api/v1/patients/{p.id}"
    bad = await client.patch(
        url,
        json={"weekly_pattern": {"visit_frequency": "biweekly", "biweekly_anchor": "2026-11-03"}},
        headers=headers,
    )
    assert bad.status_code == 422, bad.text
    ok = await client.patch(
        url,
        json={
            "weekly_pattern": {
                "visit_frequency": "monthly",
                "visit_weeks": "３，1",
                "biweekly_anchor": "2026-11-02",
            }
        },
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    wp = ok.json()["weekly_pattern"]
    assert wp["visit_weeks"] == "1,3"
    assert wp["biweekly_anchor"] == "2026-11-02"


@pytest.mark.asyncio
async def test_pfv_visit_week_read_write_and_kept_when_omitted(client, db) -> None:
    headers = await _admin(db)
    p = await _patient(db, "VF-PFV", _biweekly())
    await db.commit()
    url = f"/api/v1/patients/{p.id}/fixed-visits"
    put = await client.put(
        url,
        json={
            "mode": "normal",
            "items": [
                {"weekday": 1, "start_time": "10:00", "visit_week": "A"},
                {"weekday": 4, "start_time": "10:00", "visit_week": "B"},
            ],
        },
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert {(i["weekday"], i["visit_week"]) for i in put.json()["items"]} == {(1, "A"), (4, "B")}

    # 旧 FE のように visit_week を送らない保存: 既存の値を引き継ぎ、新しい枠は every
    put2 = await client.put(
        url,
        json={
            "mode": "normal",
            "items": [
                {"weekday": 1, "start_time": "11:00"},
                {"weekday": 4, "start_time": "10:00"},
                {"weekday": 2, "start_time": "10:00"},
            ],
        },
        headers=headers,
    )
    assert put2.status_code == 200, put2.text
    got = await client.get(url, headers=headers)
    assert got.status_code == 200
    assert {(i["weekday"], i["visit_week"]) for i in got.json()} == {
        (1, "A"),
        (2, "every"),
        (4, "B"),
    }

    bad = await client.put(
        url,
        json={
            "mode": "normal",
            "items": [{"weekday": 1, "start_time": "10:00", "visit_week": "C"}],
        },
        headers=headers,
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_visit_frequency_preview_endpoint(client, db) -> None:
    headers = await _admin(db)
    p = await _patient(db, "VF-PRE", _biweekly(), [(1, "A"), (4, "B")])
    no_slot = await _patient(db, "VF-PRE2", {"preferred_weekdays": ["Wed"]})
    await db.commit()
    resp = await client.get(
        f"/api/v1/patients/{p.id}/visit-frequency-preview",
        params={"from_date": "2026-11-02"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["frequency_status"] == "ok"
    assert body["slots_source"] == "fixed_visits"
    assert body["upcoming_visit_dates"] == [
        "2026-11-03",
        "2026-11-13",
        "2026-11-17",
        "2026-11-27",
        "2026-12-01",
        "2026-12-11",
    ]
    # 保存前の値で試す: 起点を 1 週ずらすと A/B が入れ替わる
    resp = await client.get(
        f"/api/v1/patients/{p.id}/visit-frequency-preview",
        params={"from_date": "2026-11-02", "biweekly_anchor": "2026-11-09", "count": 2},
        headers=headers,
    )
    assert resp.json()["upcoming_visit_dates"] == ["2026-11-06", "2026-11-10"]
    bad = await client.get(
        f"/api/v1/patients/{p.id}/visit-frequency-preview",
        params={"biweekly_anchor": "2026-11-10"},
        headers=headers,
    )
    assert bad.status_code == 422
    resp = await client.get(
        f"/api/v1/patients/{no_slot.id}/visit-frequency-preview",
        params={"from_date": "2026-11-02", "count": 2, "visit_frequency": "biweekly"},
        headers=headers,
    )
    body = resp.json()
    assert body["frequency_status"] == "missing_anchor"
    assert body["slots_source"] == "preferred_weekdays"
    assert body["upcoming_visit_dates"] == ["2026-11-04", "2026-11-11"]


# ---------------------------------------------------------------------------
# レビューの指摘への手当て (段 1 の 2 回目)
# ---------------------------------------------------------------------------


def _ab_pattern(**extra: Any) -> dict:
    """隔週・希望 火金 午前 (枠は 火=A・金=B で持たせる)."""
    return _biweekly(preferred_weekdays=["Tue", "Fri"], time_type="午前", **extra)


def _pool_weekdays(result: dict, patient_id) -> set[int]:
    return {v.weekday for v in result["pool_visits"] if v.patient_id == patient_id}


def _planned(patient: Patient, d: date, start: time = time(13, 0)) -> Visit:
    return Visit(
        patient_id=patient.id,
        visit_date=d,
        start_time=start,
        end_time=time(start.hour, 35),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
    )


def test_visit_weekdays_for_week_and_generating_slots() -> None:
    from app.services.scheduling.visit_frequency import pfv_slots, visit_weekdays_for_week

    wp = _biweekly()
    off = ANCHOR + timedelta(weeks=1)
    slots = [(1, "A"), (4, "B")]
    assert visit_weekdays_for_week(wp, ANCHOR, slots) == frozenset({1})
    assert visit_weekdays_for_week(wp, off, slots) == frozenset({4})
    # 枠の無い隔週: A の週は 7 日・B の週は空
    assert visit_weekdays_for_week(wp, ANCHOR) == frozenset(range(7))
    assert visit_weekdays_for_week(wp, off) == frozenset()
    # 枠の無い月次: 曜日ごと (11/30 の週: 月 11/30 = 第 5・火 12/1〜 = 第 1)
    assert visit_weekdays_for_week(_monthly("1"), date(2026, 11, 30)) == frozenset(range(1, 7))
    # 毎週・設定不足は None (絞らない)
    assert visit_weekdays_for_week({}, ANCHOR, slots) is None
    assert visit_weekdays_for_week(_biweekly(anchor=None), ANCHOR, slots) is None

    rows = [
        PatientFixedVisit(mode="normal", weekday=1, slot_index=0, visit_week="A"),
        PatientFixedVisit(mode="normal", weekday=4, slot_index=1, visit_week="every"),
        PatientFixedVisit(mode="special", weekday=2, slot_index=0, visit_week="every"),
    ]
    assert pfv_slots(rows) == [(1, "A"), (4, "every")]
    assert pfv_slots(rows, requires_multiple_staff=True) == [(1, "A"), (4, "every")]
    # 1 名の方は slot 1 を数えない (週の生成が slot 1 を無視するのとそろえる)
    assert pfv_slots(rows, requires_multiple_staff=False) == [(1, "A")]


def test_desired_count_ignores_slot1_for_single_staff() -> None:
    from app.services.scheduling.auto_allocator_v2 import _g94_desired_count
    from app.services.scheduling.pool_bulk_inserter import _desired_weekly_visits

    rows = [
        PatientFixedVisit(mode="normal", weekday=1, slot_index=0, visit_week="A"),
        # 1 名の方に誤って残った slot 1 (週の生成は作らない)
        PatientFixedVisit(mode="normal", weekday=4, slot_index=1, visit_week="every"),
    ]
    off = ANCHOR + timedelta(weeks=1)
    single = Patient(
        code="VF-S1",
        name="single",
        status="active",
        weekly_pattern=_biweekly(),
        requires_multiple_staff=False,
    )
    assert _g94_desired_count(single, rows, week_start=ANCHOR) == 1
    assert _g94_desired_count(single, rows, week_start=off) == 0
    single.weekly_pattern = _biweekly(frequency_per_week=2)
    assert _desired_weekly_visits(single, week_start=ANCHOR, fixed_rows=rows) == 1
    assert _desired_weekly_visits(single, week_start=off, fixed_rows=rows) == 0


@pytest.mark.asyncio
async def test_full_optimize_proposes_only_this_weeks_ab_slot(db) -> None:
    """隔週・火=A・金=B・希望 火金: A の週に金を、B の週に火を提案しない (全面最適化)."""
    from app.services.scheduling.auto_allocator_v2 import run_v2_pipeline

    office, _tpl = await _office_with_staff(db, "VF-full")
    ab = await _patient(db, "VF-FAB", _ab_pattern(), [(1, "A"), (4, "B")], office=office)
    await db.commit()

    for k, expected in ((0, {1}), (1, {4})):
        y, w = _iso(ANCHOR + timedelta(weeks=k))
        result = await run_v2_pipeline(
            db, iso_year=y, iso_week=w, office_ids=[office.id], mode="full_optimize"
        )
        assert _pool_weekdays(result, ab.id) == expected


@pytest.mark.asyncio
async def test_diff_add_proposes_only_this_weeks_ab_slot(db) -> None:
    """差分追加: 固定枠の候補も、希望へのフォールバック (G-92) も、その週の枠の曜日だけ."""
    from app.services.scheduling.auto_allocator_v2 import run_v2_pipeline

    office, _tpl = await _office_with_staff(db, "VF-diff")
    ab = await _patient(db, "VF-DAB", _ab_pattern(), [(1, "A"), (4, "B")], office=office)
    await db.commit()

    for k, expected in ((0, {1}), (1, {4})):
        y, w = _iso(ANCHOR + timedelta(weeks=k))
        result = await run_v2_pipeline(
            db, iso_year=y, iso_week=w, office_ids=[office.id], mode="diff_add"
        )
        assert _pool_weekdays(result, ab.id) == expected
        before_wds = {v.weekday for v in result["before_visits"] if v.patient_id == ab.id}
        assert before_wds <= expected


@pytest.mark.asyncio
async def test_monthly_no_slot_patient_is_judged_per_weekday(db) -> None:
    """枠の無い月次 (第 1 週・希望 月木): 11/30 の週は月 11/30 (第 5) を作らず木 12/3 だけ."""
    from app.services.scheduling.auto_allocator_v2 import run_v2_pipeline

    office, _tpl = await _office_with_staff(db, "VF-mon")
    mo = await _patient(
        db,
        "VF-MON",
        _monthly("1", preferred_weekdays=["Mon", "Thu"], time_type="午前"),
        office=office,
    )
    await db.commit()
    y, w = _iso(date(2026, 11, 30))
    for mode in ("diff_add", "full_optimize"):
        result = await run_v2_pipeline(
            db, iso_year=y, iso_week=w, office_ids=[office.id], mode=mode
        )
        assert {v.weekday for v in result["before_visits"] if v.patient_id == mo.id} == {3}
        assert _pool_weekdays(result, mo.id) == {3}


@pytest.mark.asyncio
async def test_from_week_keeps_other_week_slots(client, db) -> None:
    """A の週の訪問から枠を作り直しても B の枠は消えない (一括も同じ・毎週の方は今までどおり)."""
    headers = await _admin(db)
    ab = await _patient(db, "VF-FW", _biweekly(), [(1, "A"), (4, "B")])
    every = await _patient(db, "VF-FWE", None, [(1, "every"), (4, "every")])
    for p in (ab, every):
        db.add(_planned(p, ANCHOR + timedelta(days=1), time(11, 0)))  # 火
    await db.commit()
    y, w = _iso(ANCHOR)

    resp = await client.post(
        f"/api/v1/patients/{ab.id}/fixed-visits/from-week",
        params={"iso_year": y, "iso_week": w},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    got = {(r["weekday"], r["visit_week"], r["start_time"][:5]) for r in resp.json()}
    assert got == {(1, "A", "11:00"), (4, "B", "10:00")}

    resp = await client.post(
        "/api/v1/patients/fixed-visits/from-week-bulk",
        params={"iso_year": y, "iso_week": w},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == ab.id))
    ).all()
    assert {(r.weekday, r.visit_week) for r in rows} == {(1, "A"), (4, "B")}
    # 毎週の方は今までどおり (週の訪問に無い金の枠は作り直しで消える)
    rows_e = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == every.id))
    ).all()
    assert {(r.weekday, r.visit_week) for r in rows_e} == {(1, "every")}


@pytest.mark.asyncio
async def test_put_carries_visit_week_when_weekday_moves(client, db) -> None:
    headers = await _admin(db)
    p = await _patient(db, "VF-MV", _biweekly(), [(4, "B")])
    await db.commit()
    url = f"/api/v1/patients/{p.id}/fixed-visits"
    # 旧 FE: 金 (B) を木へ動かす (visit_week を送らない) → 同じ slot が全部 B なので B
    put = await client.put(
        url,
        json={"mode": "normal", "items": [{"weekday": 3, "start_time": "10:00"}]},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert [(i["weekday"], i["visit_week"]) for i in put.json()["items"]] == [(3, "B")]


@pytest.mark.asyncio
async def test_pattern_change_keeps_visit_week(client, db) -> None:
    headers = await _admin(db)
    p = await _patient(db, "VF-PC", _biweekly(), [(0, "B")])
    off = ANCHOR + timedelta(weeks=1)
    v = _planned(p, off, time(10, 0))
    db.add(v)
    await db.commit()
    y, w = _iso(off)
    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=headers,
        json={
            "mode": "pattern_change",
            "visit_id": str(v.id),
            "new_weekday": 2,
            "new_start_time": "14:00",
            "new_duration_min": 35,
            "iso_year": y,
            "iso_week": w,
        },
    )
    assert res.status_code == 200, res.text
    rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    assert [(r.weekday, r.visit_week) for r in rows] == [(2, "B")]


@pytest.mark.asyncio
async def test_two_staff_slots_must_share_visit_week(client, db) -> None:
    headers = await _admin(db)
    p = await _patient(db, "VF-2S", _biweekly())
    p.requires_multiple_staff = True
    await db.commit()
    url = f"/api/v1/patients/{p.id}/fixed-visits"

    def _items(vw0: str, vw1: str) -> list[dict]:
        return [
            {"weekday": 1, "start_time": "10:00", "slot_index": 0, "visit_week": vw0},
            {"weekday": 1, "start_time": "10:00", "slot_index": 1, "visit_week": vw1},
        ]

    bad = await client.put(url, json={"mode": "normal", "items": _items("A", "B")}, headers=headers)
    assert bad.status_code == 422, bad.text
    detail = str(bad.json()["detail"])
    assert "そろえて" in detail
    assert "火" in detail
    ok = await client.put(url, json={"mode": "normal", "items": _items("B", "B")}, headers=headers)
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_copy_week_copies_no_slot_frequency_patients_on_visit_weekdays(db) -> None:
    from app.services.scheduling.copy_week import build_copy_plan

    office, _tpl = await _office_with_staff(db, "VF-copy2")
    bi = await _patient(db, "VF-CN", _biweekly(), office=office)
    mo = await _patient(db, "VF-CM", _monthly("1"), office=office)
    source = ANCHOR  # 11/2 の週 (A・第 1 週)
    db.add(_planned(bi, source + timedelta(days=2)))  # 水 11/4
    db.add(_planned(mo, source))  # 月 11/2 (第 1 月曜)
    db.add(_planned(mo, source + timedelta(days=3)))  # 木 11/5 (第 1 木曜)
    await db.commit()

    async def _plan(target: date):
        return await build_copy_plan(
            db,
            source_monday=source,
            target_monday=target,
            exclude_visit_ids=set(),
            fill_from_fixed=False,
            today=source,
        )

    # B の週 (11/9): 隔週の方は写さない. 月次 (月 11/9・木 11/12 = 第 2) も写さない
    plan = await _plan(source + timedelta(weeks=1))
    assert plan.skipped["visit_frequency"] == 3
    assert plan.items == []
    # A の週 (11/16): 隔週の方は写す. 月次 (第 3) は写さない
    plan = await _plan(source + timedelta(weeks=2))
    assert [(it.patient_id, it.target_date) for it in plan.items] == [(bi.id, date(2026, 11, 18))]
    # 11/30 の週 (A): 月 11/30 (第 5) は写さず、木 12/3 (第 1) だけ写す
    plan = await _plan(date(2026, 11, 30))
    assert {(it.patient_id, it.target_date) for it in plan.items} == {
        (bi.id, date(2026, 12, 2)),
        (mo.id, date(2026, 12, 3)),
    }


@pytest.mark.asyncio
async def test_visit_frequency_preview_forbidden_for_staff(client, db) -> None:
    u = User(email="vf-staff@example.com", password_hash=hash_password("pw"), role="staff")
    db.add(u)
    p = await _patient(db, "VF-403", _biweekly(), [(1, "A")])
    await db.commit()
    await db.refresh(u)
    token = create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    resp = await client.get(
        f"/api/v1/patients/{p.id}/visit-frequency-preview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_special_week_is_not_thinned_by_frequency(db) -> None:
    """特別週の方は、頻度でお休みの週でも特別週の枠から作る (頻度を当てない)."""
    from app.services.scheduling.layer1_expander import Layer1Expander

    off = ANCHOR + timedelta(weeks=1)
    y, w = _iso(off)
    p = await _patient(db, "VF-SP", _biweekly(), [(0, "A")])
    p.special_week_active = [{"iso_year": y, "iso_week": w}]
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="special",
            weekday=2,
            start_time=time(10, 0),
            duration_min=35,
            visit_week="every",
        )
    )
    await db.commit()
    res = await Layer1Expander().expand_week(db, iso_year=y, iso_week=w)
    await db.commit()
    assert [v.visit_date for v in await _live_visits(db, p.id, off)] == [off + timedelta(days=2)]
    assert p.id not in res.frequency_off_week_patient_ids


@pytest.mark.asyncio
async def test_every_week_patient_is_unchanged(db) -> None:
    """毎週の方は、枠に A/B が入っていても (見ない) 週の生成・コピー・プールが今までどおり."""
    from app.services.scheduling.auto_allocator_v2 import run_v2_pipeline
    from app.services.scheduling.copy_week import build_copy_plan
    from app.services.scheduling.layer1_expander import Layer1Expander
    from app.services.scheduling.pool_bulk_inserter import _desired_weekly_visits

    office, tpl = await _office_with_staff(db, "VF-every")
    wp = {"preferred_weekdays": ["Tue", "Fri"], "time_type": "午前", "frequency_per_week": 2}
    ev = await _patient(db, "VF-EV", wp, [(1, "B"), (4, "A")], office=office, template=tpl)
    await db.commit()
    rows = list(
        (
            await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == ev.id))
        ).all()
    )

    for k in range(2):
        monday = ANCHOR + timedelta(weeks=k)
        y, w = _iso(monday)
        result = await run_v2_pipeline(
            db, iso_year=y, iso_week=w, office_ids=[office.id], mode="full_optimize"
        )
        assert _pool_weekdays(result, ev.id) == {1, 4}
        assert _desired_weekly_visits(ev, week_start=monday, fixed_rows=rows) == 2
        res = await Layer1Expander().expand_week(db, iso_year=y, iso_week=w)
        await db.commit()
        assert [v.visit_date.weekday() for v in await _live_visits(db, ev.id, monday)] == [1, 4]
        assert ev.id not in res.frequency_off_week_patient_ids

    plan = await build_copy_plan(
        db,
        source_monday=ANCHOR,
        target_monday=ANCHOR + timedelta(weeks=2),
        exclude_visit_ids=set(),
        fill_from_fixed=False,
        today=ANCHOR,
    )
    assert plan.skipped.get("visit_frequency", 0) == 0
    copied = sorted(it.target_date.weekday() for it in plan.items if it.patient_id == ev.id)
    assert copied == [1, 4]
