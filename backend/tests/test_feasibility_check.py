"""実現性チェック (feasibility_check) の判定ロジックと API のテスト.

判定は純粋関数 ``evaluate_day`` を直接叩く (座標は千葉市稲毛周辺の実寸で 20km/h 換算)。
API は admin 限定・read-only・json/html の両形式を確認する。
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient

from app.core.security import create_access_token, hash_password
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG
from app.services.scheduling.feasibility_check import (
    KIND_IMPOSSIBLE,
    KIND_NO_LUNCH,
    KIND_OVERLAP,
    KIND_PAIR_NOT_SAME_START,
    KIND_PAIR_OVER,
    KIND_PAIR_SHORT,
    KIND_TIGHT,
    KIND_WATCH,
    TimelineItem,
    evaluate_day,
)

DAY = date(2026, 9, 1)
A = (35.6655756, 140.0987458)  # 稲毛区
B = (35.6828189, 140.1305160)  # 花見川区 (A から直線 ≈ 3.5km → 20km/h で ≈ 10 分)
FAR = (35.6314777, 140.1471311)  # 都賀 (A から ≈ 5.6km → ≈ 17 分)


def v(start: str, end: str, name: str, pos, role: str = "主") -> TimelineItem:
    h, m = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    return TimelineItem(
        kind="visit", start_min=h * 60 + m, end_min=h2 * 60 + m2, name=name, pos=pos, role=role
    )


def kinds(findings) -> list[str]:
    return [f.kind for f in findings]


def test_overlap_is_flagged():
    items = [v("10:00", "10:35", "甲", A), v("10:20", "10:55", "乙", B)]
    tl, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    assert KIND_OVERLAP in kinds(fs)
    assert tl.items[1].level == "overlap"


def test_impossible_when_gap_shorter_than_travel():
    # A→FAR ≈ 17 分必要、間隔 5 分
    items = [v("10:00", "10:35", "甲", A), v("10:40", "11:15", "乙", FAR)]
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    assert KIND_IMPOSSIBLE in kinds(fs)
    f = next(x for x in fs if x.kind == KIND_IMPOSSIBLE)
    assert f.gap_min == 5 and f.need_min is not None and f.need_min > 5


def test_tight_when_gap_lacks_buffer():
    # A→B ≈ 10 分必要 + バッファ 8 分 = 18 分。間隔 12 分 → バッファ不足
    items = [v("10:00", "10:35", "甲", A), v("10:47", "11:22", "乙", B)]
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    assert KIND_TIGHT in kinds(fs)
    assert KIND_IMPOSSIBLE not in kinds(fs)


def test_watch_when_only_road_factor_fails():
    # A→B 直線 10 分 + 8 = 18 分は満たすが、実走行 (×1.3 ≈ 14 分) + 8 = 22 分には足りない間隔 20 分
    items = [v("10:00", "10:35", "甲", A), v("10:55", "11:30", "乙", B)]
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    assert kinds(fs) == [KIND_WATCH] or KIND_WATCH in kinds(fs)


def test_same_address_consecutive_is_no_travel_and_pair_occupies_90():
    # 同住所 2 名を連続配置 (35+35=70 分) → ペア扱い・占有は 90 分 (10:00〜11:30)。
    # 次の訪問が 11:20 (同住所ではない B) だと「90 分未確保」。
    items = [
        v("10:00", "10:35", "姉", A),
        v("10:35", "11:10", "妹", A),
        v("11:20", "11:55", "丙", B),
    ]
    tl, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    ks = kinds(fs)
    assert KIND_PAIR_NOT_SAME_START in ks  # 連続 = ルール (同時刻) から外れる指摘
    assert KIND_PAIR_SHORT in ks
    assert KIND_OVERLAP not in ks and KIND_IMPOSSIBLE not in ks
    assert "同住所ペア" in tl.items[1].note


def test_same_address_same_start_pair_ok_when_next_after_90():
    items = [
        v("10:00", "10:35", "姉", A),
        v("10:00", "10:35", "妹", A),
        v("11:45", "12:20", "丙", B),
    ]
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    ks = kinds(fs)
    assert (
        KIND_PAIR_NOT_SAME_START not in ks and KIND_PAIR_SHORT not in ks and KIND_OVERLAP not in ks
    )


def test_three_at_same_address_same_time_is_violation():
    items = [
        v("10:00", "10:35", "甲", A),
        v("10:00", "10:35", "乙", A),
        v("10:00", "10:35", "丙", A),
    ]
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    assert KIND_PAIR_OVER in kinds(fs)


def test_lunch_window_without_60min_gap_is_reported():
    items = [
        v("11:00", "11:35", "甲", A),
        v("12:00", "12:35", "乙", A),
        v("13:00", "13:35", "丙", A),
    ]
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    assert KIND_NO_LUNCH in kinds(fs)


def test_non_blocking_event_does_not_occupy():
    memo = TimelineItem(
        kind="event", start_min=600, end_min=660, name="メモ", blocking=False, role="行事"
    )
    items = [v("10:00", "10:35", "甲", A), memo, v("10:35", "11:10", "乙", A)]
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    assert KIND_OVERLAP not in kinds(fs)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _headers(user) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_api_requires_admin(client: AsyncClient, db):
    from app.models import User

    staff_user = User(
        email="staff@example.com", password_hash=hash_password("x" * 12), role="staff"
    )
    db.add(staff_user)
    await db.commit()
    await db.refresh(staff_user)
    res = await client.get(
        "/api/v1/schedule/v2/feasibility-report?iso_year=2026&iso_week=36",
        headers=_headers(staff_user),
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_api_json_and_html_on_empty_week(client: AsyncClient, test_user):
    res = await client.get(
        "/api/v1/schedule/v2/feasibility-report?iso_year=2026&iso_week=36",
        headers=_headers(test_user),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["iso_year"] == 2026 and body["iso_week"] == 36
    assert body["week_start"] == "2026-08-31" and body["week_end"] == "2026-09-05"
    assert body["findings"] == [] and body["hard_count"] == 0
    assert body["assumptions"]["travel_speed_kmh"] == 20.0
    assert (
        body["html"]
        and "<!doctype html>" in body["html"]
        and "fonts.googleapis" not in body["html"]
    )

    res2 = await client.get(
        "/api/v1/schedule/v2/feasibility-report?iso_year=2026&iso_week=36&format=html",
        headers=_headers(test_user),
    )
    assert res2.status_code == 200
    assert res2.headers["content-type"].startswith("text/html")
    assert "実現性チェック" in res2.text


# ---------------------------------------------------------------------------
# レビュー是正の回帰 (2026-08-31 code-reviewer HIGH-1/2/3, MEDIUM-2)
# ---------------------------------------------------------------------------


def test_three_back_to_back_same_address_is_not_a_violation():
    """連続配置の 3 名 (同住所) は物理的に成立する = ❗にしない (同時刻のみ上限判定)。"""
    import uuid

    items = [
        v("09:00", "09:30", "甲", A),
        v("09:30", "10:00", "乙", A),
        v("10:00", "10:30", "丙", A),
    ]
    for it in items:
        it.patient_id = uuid.uuid4()
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    ks = kinds(fs)
    assert KIND_PAIR_OVER not in ks
    assert KIND_OVERLAP not in ks and KIND_IMPOSSIBLE not in ks


def test_same_patient_split_visit_is_not_a_pair():
    """同一患者の分割訪問 (同住所・連続) はペアではない → 90 分占有も指摘も付かない。"""
    import uuid

    pid = uuid.uuid4()
    items = [
        v("09:00", "09:45", "田中", A),
        v("09:45", "10:30", "田中", A),
        v("10:40", "11:15", "乙", B),
    ]
    for it in items[:2]:
        it.patient_id = pid
    items[2].patient_id = uuid.uuid4()
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    ks = kinds(fs)
    assert KIND_PAIR_NOT_SAME_START not in ks and KIND_PAIR_SHORT not in ks
    assert KIND_OVERLAP not in ks


def test_missing_coordinates_are_reported_not_silently_ok():
    from app.services.scheduling.feasibility_check import KIND_NO_COORD

    items = [v("10:00", "10:35", "甲", None), v("10:36", "11:11", "乙", FAR)]
    tl, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    assert KIND_NO_COORD in kinds(fs)
    assert "座標なし" in tl.items[1].note


@pytest.mark.asyncio
async def test_loader_dedupes_two_staff_visit_and_resolves_course_staff(db):
    """2 名体制 (同一患者・同時刻の 2 行・secondary が相互参照) は各職員 1 件・重なり無し。
    担当は訪問自身の primary_staff_id を正とし、コース担当は未設定時のフォールバック
    (2026-09-01 是正: 盤面で担当を手直しした週に実担当へ一致させる)。"""
    import uuid
    from datetime import time as _time

    from app.models.course import Course
    from app.models.office import Office
    from app.models.patient import Patient
    from app.models.staff import Staff
    from app.models.visit import Visit
    from app.services.scheduling.feasibility_check import build_feasibility_report

    office = Office(name="稲毛", lat=35.63, lng=140.09)
    sx = Staff(name="看護X")
    sy = Staff(name="看護Y")
    sz = Staff(name="看護Z")
    pat = Patient(code="P-TEST-1", name="二名 太郎", lat=A[0], lng=A[1])
    db.add_all([office, sx, sy, sz, pat])
    await db.flush()
    gid = uuid.uuid4()
    day = date(2026, 9, 1)  # 2026-W36 火曜
    common = dict(
        patient_id=pat.id,
        visit_date=day,
        start_time=_time(10, 0),
        end_time=_time(10, 35),
        type="regular",
        status="planned",
    )
    db.add_all(
        [
            Visit(
                primary_staff_id=sx.id,
                secondary_staff_id=sy.id,
                visit_group_id=gid,
                required_staff_count=2,
                **common,
            ),
            Visit(
                primary_staff_id=sy.id,
                secondary_staff_id=sx.id,
                visit_group_id=gid,
                required_staff_count=2,
                **common,
            ),
        ]
    )
    # コース担当 = Z / visits.primary_staff_id = X (手動変更) → 実担当 X を正にする。
    # 担当未設定 (primary_staff_id=None) の訪問だけコース担当 Z へフォールバック。
    course = Course(
        iso_year=2026,
        iso_week=36,
        weekday=2,
        code="A",
        office_id=office.id,
        assigned_staff_id=sz.id,
        course_status="staff_assigned",
    )
    db.add(course)
    await db.flush()
    db.add(
        Visit(
            patient_id=pat.id,
            visit_date=date(2026, 9, 2),
            start_time=_time(14, 0),
            end_time=_time(14, 35),
            type="regular",
            status="planned",
            primary_staff_id=sx.id,
            course_id=course.id,
        )
    )
    # 担当未設定の訪問 → コース担当 Z へフォールバック
    db.add(
        Visit(
            patient_id=pat.id,
            visit_date=date(2026, 9, 2),
            start_time=_time(16, 0),
            end_time=_time(16, 35),
            type="regular",
            status="planned",
            primary_staff_id=None,
            course_id=course.id,
        )
    )
    await db.commit()

    report = await build_feasibility_report(db, iso_year=2026, iso_week=36)
    by = {(t.staff, t.day): t for t in report.timelines}
    assert len(by[("看護X", day)].items) == 1 and by[("看護X", day)].items[0].role == "主"
    assert len(by[("看護Y", day)].items) == 1 and by[("看護Y", day)].items[0].role == "主"
    assert all(
        f.kind not in (KIND_OVERLAP, KIND_PAIR_SHORT, KIND_PAIR_NOT_SAME_START)
        for f in report.findings
    )
    # 9/2 14:00 (primary=X・コース担当=Z) は実担当 X の行に載る (コース担当は使わない)
    x2 = by[("看護X", date(2026, 9, 2))]
    assert any(i.start_min == 14 * 60 for i in x2.items)
    z2 = by.get(("看護Z", date(2026, 9, 2)))
    assert z2 is None or not any(i.start_min == 14 * 60 for i in z2.items)
    # 担当未設定の 16:00 はコース担当 Z の行に載る (フォールバック)
    assert ("看護Z", date(2026, 9, 2)) in by
    assert any(i.start_min == 16 * 60 for i in by[("看護Z", date(2026, 9, 2))].items)
    # 2 名体制 (2 行) は 1 件と数える (レビュー NEW-3) → 2 名体制 1 + 9/2 の 2 = 3
    assert report.visit_count == 3


def test_missing_coordinates_reported_once_per_patient():
    from app.services.scheduling.feasibility_check import KIND_NO_COORD

    items = [
        v("10:00", "10:35", "甲", A),
        v("10:45", "11:20", "乙", None),
        v("11:30", "12:05", "丙", B),
    ]
    _, fs = evaluate_day("看護A", DAY, items, DEFAULT_SCHEDULING_CONFIG)
    assert kinds(fs).count(KIND_NO_COORD) == 1


def test_homonym_staff_keep_separate_rows_in_html():
    """同名スタッフ (staff.id が違う) はレポートの一覧で別行になる (レビュー LOW-6)。"""
    from app.services.scheduling.feasibility_check import evaluate_week
    from app.services.scheduling.feasibility_report_html import render_feasibility_html

    items = {
        ("id-1", DAY): [v("10:00", "10:35", "甲", A)],
        ("id-2", DAY): [v("10:00", "10:35", "乙", B)],
    }
    rep = evaluate_week(
        items,
        DEFAULT_SCHEDULING_CONFIG,
        iso_year=2026,
        iso_week=36,
        week_start=date(2026, 8, 31),
        week_end=date(2026, 9, 5),
        staff_names={"id-1": "山田 花子", "id-2": "山田 花子"},
    )
    html_out = render_feasibility_html(rep)
    assert html_out.count("<tr><th>山田 花子</th>") == 2
    assert rep.visit_count == 2 and not [f for f in rep.findings if f.kind == KIND_OVERLAP]
