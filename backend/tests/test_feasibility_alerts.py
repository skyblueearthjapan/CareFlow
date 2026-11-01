"""実現性の警告 (カードの印・取り込み後の確認) のテスト.

設計 = docs/plans/schedule-feasibility-alerts-design-2026-11-01.md (第 1 段)。
"""

from __future__ import annotations

import uuid
from datetime import date
from datetime import time as _time

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG
from app.services.scheduling.feasibility_alerts import (
    IMPORT_NOTIFY_TYPE,
    IMPORT_REFERENCE_TYPE,
    collect_import_baseline,
    fingerprint,
    reason_text,
    record_import_alerts,
    record_import_alerts_read,
)
from app.services.scheduling.feasibility_check import (
    KIND_IMPOSSIBLE,
    KIND_OVERLAP,
    KIND_PAIR_SHORT,
    KIND_TIGHT,
    TimelineItem,
    evaluate_day,
)

DAY = date(2026, 9, 1)  # 2026-W36 火曜
A = (35.6655756, 140.0987458)
B = (35.6828189, 140.1305160)  # A から移動 10 分
FAR = (35.6314777, 140.1471311)  # A から移動 17 分
BUF = DEFAULT_SCHEDULING_CONFIG.visit_buffer_min


def v(start: str, end: str, name: str, pos, *, role: str = "主", pid=None) -> TimelineItem:
    h, m = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    return TimelineItem(
        kind="visit",
        start_min=h * 60 + m,
        end_min=h2 * 60 + m2,
        name=name,
        pos=pos,
        role=role,
        patient_id=pid or uuid.uuid4(),
        visit_ids=[uuid.uuid4()],
    )


def _find(fs, kind):
    return next(f for f in fs if f.kind == kind)


# ---------------------------------------------------------------------------
# 指紋と理由の文 (純粋関数)
# ---------------------------------------------------------------------------


def test_fingerprint_ignores_visit_ids_but_not_time_or_staff():
    pa, pb = uuid.uuid4(), uuid.uuid4()
    fs1 = evaluate_day(
        "看護A",
        DAY,
        [v("10:00", "10:35", "甲", A, pid=pa), v("10:40", "11:15", "乙", FAR, pid=pb)],
        DEFAULT_SCHEDULING_CONFIG,
        staff_key="s1",
    )[1]
    # 置換で入れ直した (訪問 ID だけ違う) → 同じ指紋
    fs2 = evaluate_day(
        "看護A",
        DAY,
        [v("10:00", "10:35", "甲", A, pid=pa), v("10:40", "11:15", "乙", FAR, pid=pb)],
        DEFAULT_SCHEDULING_CONFIG,
        staff_key="s1",
    )[1]
    f1, f2 = _find(fs1, KIND_IMPOSSIBLE), _find(fs2, KIND_IMPOSSIBLE)
    assert f1.to_visit_ids != f2.to_visit_ids
    assert fingerprint(f1) == fingerprint(f2)
    # 時刻が変われば別の指紋
    fs3 = evaluate_day(
        "看護A",
        DAY,
        [v("10:00", "10:35", "甲", A, pid=pa), v("10:45", "11:20", "乙", FAR, pid=pb)],
        DEFAULT_SCHEDULING_CONFIG,
        staff_key="s1",
    )[1]
    assert fingerprint(_find(fs3, KIND_IMPOSSIBLE)) != fingerprint(f1)
    # 担当が変われば別の指紋
    fs4 = evaluate_day(
        "看護B",
        DAY,
        [v("10:00", "10:35", "甲", A, pid=pa), v("10:40", "11:15", "乙", FAR, pid=pb)],
        DEFAULT_SCHEDULING_CONFIG,
        staff_key="s2",
    )[1]
    assert fingerprint(_find(fs4, KIND_IMPOSSIBLE)) != fingerprint(f1)


def test_reason_text_impossible_has_real_end_gap_travel_and_km():
    fs = evaluate_day(
        "看護A",
        DAY,
        [v("10:00", "10:35", "甲", A), v("10:40", "11:15", "乙", FAR)],
        DEFAULT_SCHEDULING_CONFIG,
    )[1]
    text = reason_text(_find(fs, KIND_IMPOSSIBLE), buffer_min=BUF)
    assert "甲様" in text and "〜10:35" in text and "5 分" in text
    assert "移動に約 17 分" in text and "km" in text


def test_reason_text_overlap_and_tight():
    fs = evaluate_day(
        "看護A",
        DAY,
        [v("10:00", "10:35", "甲", A), v("10:20", "10:55", "乙", B)],
        DEFAULT_SCHEDULING_CONFIG,
    )[1]
    assert "15 分重なっています" in reason_text(_find(fs, KIND_OVERLAP), buffer_min=BUF)

    fs2 = evaluate_day(
        "看護A",
        DAY,
        [v("10:00", "10:35", "甲", A), v("10:47", "11:22", "乙", B)],
        DEFAULT_SCHEDULING_CONFIG,
    )[1]
    t = reason_text(_find(fs2, KIND_TIGHT), buffer_min=BUF)
    # 間 12 分・移動 10 分＋余裕 8 分 → 6 分足りない
    assert "12 分" in t and f"余裕 {BUF} 分" in t and "6 分足りません" in t


def test_reason_text_pair_short_shows_real_end():
    fs = evaluate_day(
        "看護A",
        DAY,
        [
            v("09:30", "10:00", "姉", A),
            v("10:00", "10:40", "妹", A),
            v("11:00", "11:30", "丙", B),
        ],
        DEFAULT_SCHEDULING_CONFIG,
    )[1]
    t = reason_text(_find(fs, KIND_PAIR_SHORT), buffer_min=BUF)
    assert "実際の終わりは 10:40" in t and "間は 20 分" in t


def test_reason_text_prefixes_accompanying_staff():
    fs = evaluate_day(
        "高橋",
        DAY,
        [v("10:00", "10:35", "甲", A), v("10:40", "11:15", "乙", FAR, role="同行")],
        DEFAULT_SCHEDULING_CONFIG,
    )[1]
    assert reason_text(_find(fs, KIND_IMPOSSIBLE), buffer_min=BUF).startswith("同行の高橋さん：")


# ---------------------------------------------------------------------------
# DB を使う: 印の API・取り込み後の確認
# ---------------------------------------------------------------------------


def _headers(user) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_overlap(db, *, second_start: _time = _time(10, 20)):
    """看護X の 9/1 に 10:00〜10:35 (A) と second_start〜 (B) を置く。"""
    from app.models.patient import Patient
    from app.models.staff import Staff
    from app.models.visit import Visit

    sx = Staff(name="看護X")
    p1 = Patient(code="P-AL-1", name="甲野 一郎", lat=A[0], lng=A[1])
    p2 = Patient(code="P-AL-2", name="乙川 二郎", lat=B[0], lng=B[1])
    db.add_all([sx, p1, p2])
    await db.flush()
    v1 = Visit(
        patient_id=p1.id,
        visit_date=DAY,
        start_time=_time(10, 0),
        end_time=_time(10, 35),
        type="regular",
        status="planned",
        primary_staff_id=sx.id,
    )
    end_min = second_start.hour * 60 + second_start.minute + 35
    v2 = Visit(
        patient_id=p2.id,
        visit_date=DAY,
        start_time=second_start,
        end_time=_time(end_min // 60, end_min % 60),
        type="regular",
        status="planned",
        primary_staff_id=sx.id,
    )
    db.add_all([v1, v2])
    await db.commit()
    return sx, v1, v2


@pytest.mark.asyncio
async def test_marks_api_requires_admin(client: AsyncClient, db):
    from app.models import User

    staff_user = User(
        email="staff-marks@example.com", password_hash=hash_password("x" * 12), role="staff"
    )
    db.add(staff_user)
    await db.commit()
    await db.refresh(staff_user)
    res = await client.get(
        "/api/v1/schedule/v2/feasibility-marks?date_from=2026-09-01&date_to=2026-09-01",
        headers=_headers(staff_user),
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_marks_api_rejects_more_than_7_days(client: AsyncClient, test_user):
    res = await client.get(
        "/api/v1/schedule/v2/feasibility-marks?date_from=2026-08-31&date_to=2026-09-07",
        headers=_headers(test_user),
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_marks_api_returns_marks_by_visit_id(client: AsyncClient, db, test_user):
    _sx, v1, v2 = await _seed_overlap(db)
    res = await client.get(
        "/api/v1/schedule/v2/feasibility-marks?date_from=2026-08-31&date_to=2026-09-05",
        headers=_headers(test_user),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["hardCount"] == 1 and body["softCount"] == 0
    assert body["items"][0]["kind"] == KIND_OVERLAP
    assert body["items"][0]["visitIds"] == [str(v2.id)]
    assert body["items"][0]["patientName"] == "乙川 二郎"
    marks = body["marks"]
    assert list(marks) == [str(v2.id)]
    assert marks[str(v2.id)][0]["tier"] == "hard"
    assert "重なっています" in marks[str(v2.id)][0]["reason"]
    assert str(v1.id) not in marks

    # 1 日だけ (訪問モニター) でも同じ結果・別の日は空
    res2 = await client.get(
        "/api/v1/schedule/v2/feasibility-marks?date_from=2026-09-02&date_to=2026-09-02",
        headers=_headers(test_user),
    )
    assert res2.json()["items"] == []


@pytest.mark.asyncio
async def test_marks_api_spanning_two_weeks(client: AsyncClient, db, test_user):
    await _seed_overlap(db)
    res = await client.get(
        "/api/v1/schedule/v2/feasibility-marks?date_from=2026-08-30&date_to=2026-09-01",
        headers=_headers(test_user),
    )
    assert res.status_code == 200, res.text
    assert res.json()["hardCount"] == 1


async def _new_job(db, week_start: date):
    from app.models.kaipoke_job import KaipokeJob

    job = KaipokeJob(
        job_type="fetch",
        week_start=week_start,
        params={"op": "smart-apply"},
        status="completed",
    )
    job.result_summary = {"diff": None}
    db.add(job)
    await db.flush()
    return job


@pytest.mark.asyncio
async def test_import_alerts_notify_only_new_and_only_once(db, test_user):
    from app.models.notification import Notification
    from app.models.visit import Visit

    week_start = date(2026, 8, 31)
    _sx, _v1, v2 = await _seed_overlap(db, second_start=_time(11, 30))  # 取り込み前は問題なし
    before = await collect_import_baseline(db, week_start)
    assert before == set()

    # 「取り込み」で 2 件目が 10:20 に動いた → 重なり (新しい赤)
    v2.start_time, v2.end_time = _time(10, 20), _time(10, 55)
    job = await _new_job(db, week_start)
    res = await record_import_alerts(db, week_start=week_start, job=job, before=before)
    await db.commit()
    assert res["ok"] is True and res["hard_count"] == 1
    assert res["items"][0]["kind"] == KIND_OVERLAP
    assert job.result_summary["diff"] is None  # 既存の結果は残す
    assert job.result_summary["feasibility"]["hard_count"] == 1
    assert len(job.result_summary["feasibility"]["notified"]) == 1
    rows = (
        await db.scalars(select(Notification).where(Notification.type == IMPORT_NOTIFY_TYPE))
    ).all()
    assert len(rows) == 1  # 管理者 1 人 × 1 行
    n = rows[0]
    assert n.user_id == test_user.id
    assert n.reference_type == IMPORT_REFERENCE_TYPE and n.reference_id == job.id
    assert "無理 1 件・注意 0 件" in n.title and "9/1" in n.title
    assert "乙川 二郎様" in n.body
    assert n.data["week_start"] == "2026-08-31" and n.data["dates"] == ["2026-09-01"]

    # らく助で一度合わせた後、次の取り込みで同じ重なりがまた出ても、知らせ済みなのでベルは出さない
    before2 = set()  # 取り込み前は消えていた
    job2 = await _new_job(db, week_start)
    res2 = await record_import_alerts(db, week_start=week_start, job=job2, before=before2)
    await db.commit()
    assert res2["hard_count"] == 1  # 結果の画面には出す
    assert res2["notified"] == []
    rows2 = (
        await db.scalars(select(Notification).where(Notification.type == IMPORT_NOTIFY_TYPE))
    ).all()
    assert len(rows2) == 1

    # 取り込み前からあった指摘は「新しい」に入らない
    job3 = await _new_job(db, week_start)
    before3 = await collect_import_baseline(db, week_start)
    res3 = await record_import_alerts(db, week_start=week_start, job=job3, before=before3)
    assert res3["items"] == [] and res3["hard_count"] == 0
    assert (await db.get(Visit, v2.id)) is not None


@pytest.mark.asyncio
async def test_import_alerts_info_only_does_not_notify(db, test_user):
    """参考 (要注意(実走行)) だけが新しく出ても、結果にもベルにも出さない。"""
    from app.models.notification import Notification

    week_start = date(2026, 8, 31)
    _sx, _v1, v2 = await _seed_overlap(db, second_start=_time(11, 30))
    before = await collect_import_baseline(db, week_start)
    # 間 20 分 (移動 10＋8 は満たすが実走行 13＋8=21 に足りない) → 要注意 = 参考
    v2.start_time, v2.end_time = _time(10, 55), _time(11, 30)
    job = await _new_job(db, week_start)
    res = await record_import_alerts(db, week_start=week_start, job=job, before=before)
    await db.commit()
    assert res["ok"] is True and res["items"] == []
    rows = (
        await db.scalars(select(Notification).where(Notification.type == IMPORT_NOTIFY_TYPE))
    ).all()
    assert rows == []


@pytest.mark.asyncio
async def test_import_alerts_failure_does_not_raise(db, test_user, monkeypatch):
    """判定で例外が起きても外へ出さず、結果に「確認できませんでした」を残す。"""
    import app.services.scheduling.feasibility_alerts as fa

    async def boom(*_a, **_k):
        raise RuntimeError("boom")

    week_start = date(2026, 8, 31)
    job = await _new_job(db, week_start)
    monkeypatch.setattr(fa, "_week_alerts", boom)
    assert await collect_import_baseline(db, week_start) is None
    res = await record_import_alerts(db, week_start=week_start, job=job, before=set())
    await db.commit()
    assert res["ok"] is False and "できませんでした" in res["message"]
    assert job.result_summary["feasibility"]["ok"] is False
    # before が None (取り込み前の判定に失敗) のときも同じ
    res2 = await record_import_alerts(db, week_start=week_start, job=job, before=None)
    assert res2["ok"] is False


@pytest.mark.asyncio
async def test_import_alerts_read_shape_failure_does_not_raise(db, test_user, monkeypatch):
    """応答の形に合わない結果でも例外を外へ出さず、ok=False の応答にする。"""
    import app.services.scheduling.feasibility_alerts as fa

    async def bad(*_a, **_k):
        return {"ok": True, "items": [{"tier": "hard"}]}  # 必須の欄が足りない

    week_start = date(2026, 8, 31)
    job = await _new_job(db, week_start)
    good = await record_import_alerts_read(db, week_start=week_start, job=job, before=set())
    assert good.ok is True

    monkeypatch.setattr(fa, "record_import_alerts", bad)
    res = await record_import_alerts_read(db, week_start=week_start, job=job, before=set())
    assert res.ok is False and "できませんでした" in (res.message or "")
