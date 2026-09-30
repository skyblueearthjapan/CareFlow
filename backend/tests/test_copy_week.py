"""前の週をコピーして週を作る (docs/plans/copy-week-design-2026-09-30.md §7).

検証観点:
  - 写す対象と除外の各区分 (取消 / 予定外 / 特別訪問週間の追加分 / 止まっている患者 /
    利用者が外したもの / 残す訪問と同じ枠 / 打刻のある週の埋まっている日)
  - 担当が一切写らないこと (primary / secondary / mentor / VSA / コース担当 /
    manual_staff_override)・メモ / kaipoke_id / 青ピン / 同行も写らない
  - 2 名体制の組み直し (新しい組 ID)・サービス内容の上書きは写る
  - 臨時コース配下はコースなし・通常はテンプレートの同じコースへ (無ければ作る)
  - 写す先に打刻・取込・青ピン・取消がある場合 (§4-5)
  - スナップショットからの復元 (「コピー前に戻す」)
  - 固定訪問 (PFV) が 1 行も変わらないこと
  - 続けて自動割当した場合に 3 か所 (コース・primary・VSA) の担当が揃うこと
  - 月跨ぎ週 / 排他 (409) / confirm 必須 / 写す先は今週以降 / admin のみ
  - 写す元の候補週一覧 / 祝日の判定
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select, text

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffShift, User, Visit
from app.models.accompaniment import Accompaniment
from app.models.audit_log import AuditLog
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED
from app.models.course_template import CourseTemplate
from app.models.inbound_snapshot import InboundSnapshot
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.special_visit import SpecialVisitMark, SpecialVisitPeriod
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.patient_status_sync import today_jst
from app.services.scheduling.copy_week import monday_of
from app.services.scheduling.jp_holidays import holidays_between

URL = "/api/v1/schedule/v2/copy-week"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _weeks(ahead: int = 2) -> tuple[date, date]:
    """(写す元, 写す先) — 写す先は今週より ``ahead`` 週先 (過去日を含まない)."""
    target = monday_of(today_jst()) + timedelta(weeks=ahead)
    return target - timedelta(weeks=1), target


async def _user(db, role: str = "admin") -> User:
    u = User(email=f"cw-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("pw"))
    u.role = role
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _bearer(u: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=u.id, role=u.role)}"}


class World:
    """1 拠点 + テンプレート A + スタッフ 3 名 + 患者を作る小さな盤面."""

    def __init__(self, db, source: date, target: date) -> None:
        self.db = db
        self.source = source
        self.target = target

    async def setup(self) -> World:
        db = self.db
        self.office = Office(name="コピー拠点", lat=35.6, lng=140.1)
        db.add(self.office)
        await db.flush()
        self.tpl = CourseTemplate(
            office_id=self.office.id,
            label="A",
            capacity_mon=6,
            capacity_tue=6,
            capacity_wed=6,
            capacity_thu=6,
            capacity_fri=6,
            capacity_sat=6,
        )
        db.add(self.tpl)
        self.staff = []
        for i in range(3):
            s = Staff(
                code=f"CW-S{i}",
                name=f"職員{i}",
                role="staff",
                status="active",
                primary_office_id=self.office.id,
            )
            db.add(s)
            self.staff.append(s)
        await db.flush()
        for s in self.staff:
            for wd in range(6):
                db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
        await db.flush()
        return self

    async def patient(self, code: str, *, status: str = "active", pfv: tuple[int, ...] = ()):
        p = Patient(
            code=code,
            name=f"患者{code}",
            status=status,
            primary_office_id=self.office.id,
            special_week_active=[],
        )
        self.db.add(p)
        await self.db.flush()
        for wd in pfv:
            self.db.add(
                PatientFixedVisit(
                    patient_id=p.id,
                    mode="normal",
                    weekday=wd,
                    start_time=time(9, 0),
                    duration_min=60,
                    course_template_id=self.tpl.id,
                )
            )
        await self.db.flush()
        return p

    async def course(self, monday: date, weekday: int, *, code: str = "A", staff=None) -> Course:
        iso = monday.isocalendar()
        c = Course(
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=weekday,
            code=code,
            course_status=COURSE_STATUS_STAFF_ASSIGNED if staff else "course_fixed",
            assigned_staff_id=staff.id if staff else None,
            template_id=self.tpl.id if not code.startswith("臨") else None,
            office_id=self.office.id,
        )
        self.db.add(c)
        await self.db.flush()
        return c

    async def visit(
        self,
        patient: Patient,
        monday: date,
        weekday: int,
        *,
        start: time = time(9, 0),
        course: Course | None = None,
        staff=None,
        **kw,
    ) -> Visit:
        v = Visit(
            patient_id=patient.id,
            visit_date=monday + timedelta(days=weekday),
            start_time=start,
            end_time=time(start.hour + 1, start.minute),
            type="regular",
            status=kw.pop("status", "planned"),
            source=kw.pop("source", "auto"),
            course_id=course.id if course else None,
            primary_staff_id=staff.id if staff else None,
            **kw,
        )
        self.db.add(v)
        await self.db.flush()
        if staff is not None:
            self.db.add(VisitStaffAssignment(visit_id=v.id, staff_id=staff.id))
            await self.db.flush()
        return v


async def _checkin(db, v: Visit) -> None:
    db.add(
        VisitCheckin(
            visit_id=v.id,
            patient_id=v.patient_id,
            kind="arrival",
            scanned_at=datetime.now(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.flush()


def _body(source: date, target: date, **kw) -> dict:
    return {"source_week_start": source.isoformat(), "target_week_start": target.isoformat(), **kw}


async def _pfv_rows(db) -> list[tuple]:
    rows = (await db.scalars(select(PatientFixedVisit).order_by(PatientFixedVisit.id))).all()
    cols = [c.key for c in PatientFixedVisit.__table__.columns]
    return [tuple(getattr(r, c) for c in cols) for r in rows]


async def _live(db, monday: date) -> list[Visit]:
    return list(
        (
            await db.scalars(
                select(Visit)
                .execution_options(populate_existing=True)
                .where(
                    Visit.visit_date >= monday,
                    Visit.visit_date <= monday + timedelta(days=6),
                    Visit.deleted_at.is_(None),
                )
                .order_by(Visit.visit_date, Visit.start_time)
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# 1. 除外の各区分 + preview は DB を変えない
# ---------------------------------------------------------------------------


async def _seed_categories(db):
    source, target = _weeks()
    w = await World(db, source, target).setup()
    s0, s1, s2 = w.staff
    mon = await w.course(source, 0, staff=s0)
    fri_temp = await w.course(source, 4, code="臨", staff=s1)
    p1 = await w.patient("P1", pfv=(0,))
    p2 = await w.patient("P2", pfv=(2,))
    p3 = await w.patient("P3", pfv=(3,))
    p4 = await w.patient("P4", status="admitted", pfv=(0,))
    p5 = await w.patient("P5", pfv=(0,))
    p6 = await w.patient("P6", pfv=(0,))
    p6.requires_multiple_staff = True

    normal = await w.visit(
        p1,
        source,
        0,
        course=mon,
        staff=s0,
        note="申し送り",
        kaipoke_id="KP-1",
        kaipoke_service_override="訪看I5",
        manual_staff_override=True,
        mentor_staff_id=s2.id,
        secondary_staff_id=s1.id,
        week_pinned=True,
    )
    await w.visit(p1, source, 1, status="cancelled")
    await w.visit(p2, source, 2, is_unplanned=True, status="completed")
    extra = await w.visit(p3, source, 3)
    period = SpecialVisitPeriod(
        patient_id=p3.id, start_date=source, end_date=target + timedelta(days=6)
    )
    db.add(period)
    await db.flush()
    iso = source.isocalendar()
    db.add(
        SpecialVisitMark(
            period_id=period.id,
            patient_id=p3.id,
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=3,
            kind="extra",
            status="placed",
            placed_visit_id=extra.id,
        )
    )
    await w.visit(p4, source, 0, course=mon)
    temp = await w.visit(p5, source, 4, course=fri_temp, staff=s1)  # 固定訪問は月のみ
    gid = uuid.uuid4()
    mon_b_course = mon  # 2 名体制は同じコースでも組が区別できる
    pair = [
        await w.visit(
            p6,
            source,
            0,
            start=time(13, 0),
            course=mon_b_course,
            visit_group_id=gid,
            required_staff_count=2,
        ),
        await w.visit(
            p6,
            source,
            0,
            start=time(13, 0),
            visit_group_id=gid,
            required_staff_count=2,
        ),
    ]
    db.add(
        Accompaniment(
            accompanying_staff_id=s2.id,
            target_type="visit",
            visit_id=normal.id,
            source="manual",
            kind="trainee",
        )
    )
    await db.commit()
    return w, dict(normal=normal, temp=temp, pair=pair, gid=gid, p5=p5)


@pytest.mark.asyncio
async def test_preview_counts_each_category_and_writes_nothing(client, db) -> None:
    admin = await _user(db)
    w, seeded = await _seed_categories(db)
    before = len((await db.scalars(select(Visit))).all())

    res = await client.post(
        f"{URL}/preview", headers=_bearer(admin), json=_body(w.source, w.target)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "replace"
    # 写す: P1 月 / P5 金 (臨時) / P6 月の 2 名体制 2 行
    assert body["copy_count"] == 4
    sk = body["skipped"]
    assert sk["cancelled"] == 1
    assert sk["unplanned"] == 1
    assert sk["special_extra"] == 1
    assert sk["inactive_patient"] == 1
    assert body["temp_course_count"] == 1
    # 固定訪問に無い (患者・曜日) = P5 の金曜だけ
    assert [r["patient_name"] for r in body["not_in_fixed"]] == ["患者P5"]
    assert body["not_in_fixed"][0]["excluded"] is False
    assert {d["weekday"]: d["count"] for d in body["by_weekday"]} == {0: 3, 4: 1}

    assert len((await db.scalars(select(Visit))).all()) == before
    assert (await db.scalars(select(InboundSnapshot))).all() == []


# ---------------------------------------------------------------------------
# 2. 実行: 担当は写らない・2 名体制の組み直し・コース・スナップショット・監査・PFV 不変
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_never_copies_staff_and_regroups_pairs(client, db) -> None:
    admin = await _user(db)
    w, seeded = await _seed_categories(db)
    pfv_before = await _pfv_rows(db)

    res = await client.post(
        URL,
        headers=_bearer(admin),
        json=_body(w.source, w.target, assign_staff=False, confirm=True),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["created"] == 4
    assert body["restorable"] is True
    assert body["assign_result"] is None

    copied = await _live(db, w.target)
    assert len(copied) == 4
    for v in copied:
        assert v.source == "manual_week"
        assert v.status == "planned"
        assert v.primary_staff_id is None
        assert v.secondary_staff_id is None
        assert v.mentor_staff_id is None
        assert v.manual_staff_override is False
        assert v.week_pinned is False
        assert v.note is None
        assert v.kaipoke_id is None
    ids = [v.id for v in copied]
    assert (
        await db.scalars(select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(ids)))
    ).all() == []
    assert (
        await db.scalars(select(Accompaniment).where(Accompaniment.visit_id.in_(ids)))
    ).all() == []

    p1_visit = next(v for v in copied if v.start_time == time(9, 0) and v.visit_date == w.target)
    assert p1_visit.kaipoke_service_override == "訪看I5"
    course = await db.get(Course, p1_visit.course_id, populate_existing=True)
    iso = w.target.isocalendar()
    assert (course.iso_year, course.iso_week, course.weekday, course.template_id) == (
        iso.year,
        iso.week,
        0,
        w.tpl.id,
    )
    assert course.assigned_staff_id is None  # コース担当も写らない

    temp = next(v for v in copied if v.visit_date == w.target + timedelta(days=4))
    assert temp.course_id is None  # 臨時コース配下はコースなし
    assert temp.end_time == time(10, 0)

    pair = [v for v in copied if v.start_time == time(13, 0)]
    assert len(pair) == 2
    assert pair[0].visit_group_id == pair[1].visit_group_id
    assert pair[0].visit_group_id not in (None, seeded["gid"])
    assert {v.required_staff_count for v in pair} == {2}

    snaps = (await db.scalars(select(InboundSnapshot))).all()
    assert [(s.kind, s.week_start) for s in snaps] == [("copy_week", w.target)]
    assert str(snaps[0].id) == body["snapshot_id"]
    audit = (
        await db.scalars(select(AuditLog).where(AuditLog.action == "schedule_copy_week"))
    ).all()
    assert len(audit) == 1
    assert audit[0].after["source_week_start"] == w.source.isoformat()
    assert audit[0].after["created"] == 4
    assert audit[0].after["options"]["assign_staff"] is False

    assert await _pfv_rows(db) == pfv_before  # 固定訪問は 1 行も変わらない


@pytest.mark.asyncio
async def test_user_can_exclude_not_in_fixed_visits(client, db) -> None:
    admin = await _user(db)
    w, seeded = await _seed_categories(db)
    body = _body(w.source, w.target, exclude_visit_ids=[str(seeded["temp"].id)])
    res = await client.post(f"{URL}/preview", headers=_bearer(admin), json=body)
    assert res.status_code == 200
    pv = res.json()
    assert pv["copy_count"] == 3
    assert pv["skipped"]["user_excluded"] == 1
    assert pv["not_in_fixed"][0]["excluded"] is True  # 外しても一覧には残る

    # 2 名体制は片方を外すと組ごと外れる
    body2 = _body(w.source, w.target, exclude_visit_ids=[str(seeded["pair"][0].id)])
    pv2 = (await client.post(f"{URL}/preview", headers=_bearer(admin), json=body2)).json()
    assert pv2["copy_count"] == 2
    assert pv2["skipped"]["user_excluded"] == 2

    res = await client.post(
        URL, headers=_bearer(admin), json={**body, "assign_staff": False, "confirm": True}
    )
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 3
    assert all(v.patient_id != seeded["p5"].id for v in await _live(db, w.target))


# ---------------------------------------------------------------------------
# 3. 写す先に既存訪問がある (§4-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_keeps_import_pinned_cancelled(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    s0 = w.staff[0]
    pa = await w.patient("RA", pfv=(0,))
    pb = await w.patient("RB", pfv=(1,))
    pc = await w.patient("RC", pfv=(2,))
    pd = await w.patient("RD", pfv=(3,))
    for p, wd in ((pa, 0), (pb, 1), (pc, 2), (pd, 3)):
        await w.visit(p, source, wd)
    tcourse = await w.course(target, 0, staff=s0)
    # 写す訪問と同じ枠 → 置き換え (論理削除) が先に効かないと本番の部分 UNIQUE に当たる
    replaced = await w.visit(pa, target, 0, course=tcourse, staff=s0)
    imp = await w.visit(pb, target, 1, start=time(11, 0), source="import")
    pinned = await w.visit(pc, target, 2, start=time(14, 0), week_pinned=True)
    cancelled = await w.visit(pd, target, 3, status="cancelled", source="manual_cancel")
    await db.execute(
        text(
            "CREATE UNIQUE INDEX uq_visits_pds_group_active "
            "ON visits (patient_id, visit_date, start_time, "
            "COALESCE(visit_group_id, '00000000-0000-0000-0000-000000000000')) "
            "WHERE deleted_at IS NULL"
        )
    )
    await db.commit()

    pv = (
        await client.post(f"{URL}/preview", headers=_bearer(admin), json=_body(source, target))
    ).json()
    assert pv["mode"] == "replace"
    ex = pv["existing"]
    assert (
        ex["total"],
        ex["replace"],
        ex["keep_import"],
        ex["keep_pinned"],
        ex["keep_cancelled"],
    ) == (
        4,
        1,
        1,
        1,
        1,
    )
    assert pv["skipped"]["kept_conflict"] == 1  # RD 木 9:00 = 取消行と同じ枠
    assert pv["copy_count"] == 3

    res = await client.post(
        URL, headers=_bearer(admin), json=_body(source, target, assign_staff=False, confirm=True)
    )
    assert res.status_code == 200, res.text
    assert res.json()["replaced"] == 1
    assert res.json()["courses_cleared"] == 1
    assert (await db.get(Visit, replaced.id, populate_existing=True)).deleted_at is not None
    for kept in (imp, pinned, cancelled):
        assert (await db.get(Visit, kept.id, populate_existing=True)).deleted_at is None
    # 置き換えで空になったコースは担当を外す (写した訪問と 3 か所を揃える)
    assert (await db.get(Course, tcourse.id, populate_existing=True)).assigned_staff_id is None
    live = await _live(db, target)
    assert len(live) == 3 + 3


@pytest.mark.asyncio
async def test_target_with_checkin_switches_to_add_only_and_mirrors_course_staff(
    client, db
) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    s0, s1, _ = w.staff
    p1 = await w.patient("K1", pfv=(0,))
    p2 = await w.patient("K2", pfv=(0,))
    scourse = await w.course(source, 0, staff=s1)
    await w.visit(p1, source, 0, course=scourse, staff=s1)
    await w.visit(p2, source, 0, start=time(11, 0), course=scourse, staff=s1)
    tcourse = await w.course(target, 0, staff=s0)
    done = await w.visit(p1, target, 0, start=time(10, 0), course=tcourse, staff=s0)
    await _checkin(db, done)
    await db.commit()

    pv = (
        await client.post(f"{URL}/preview", headers=_bearer(admin), json=_body(source, target))
    ).json()
    assert pv["mode"] == "add_only"
    assert pv["existing"]["replace"] == 0
    assert pv["skipped"]["occupied_day"] == 1
    assert pv["copy_count"] == 1

    res = await client.post(
        URL, headers=_bearer(admin), json=_body(source, target, assign_staff=False, confirm=True)
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert (out["mode"], out["created"], out["replaced"], out["restorable"]) == (
        "add_only",
        1,
        0,
        False,
    )
    live = await _live(db, target)
    added = next(v for v in live if v.patient_id == p2.id)
    # 担当の決まっているコースへ入れた訪問は、写す先のコース担当 (s0) で 3 か所を揃える。
    # 写す元の担当 (s1) は写らない。
    assert added.course_id == tcourse.id
    assert added.primary_staff_id == s0.id
    vsa = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == added.id)
        )
    ).all()
    assert [a.staff_id for a in vsa] == [s0.id]
    assert (await db.get(Visit, done.id, populate_existing=True)).deleted_at is None


# ---------------------------------------------------------------------------
# 4. 「コピー前に戻す」 = スナップショットからの復元
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_snapshot_undoes_copy(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    s0 = w.staff[0]
    p1 = await w.patient("U1", pfv=(0,))
    p2 = await w.patient("U2", pfv=(1,))
    await w.visit(p2, source, 1)
    tcourse = await w.course(target, 0, staff=s0)
    await w.visit(p1, target, 0, course=tcourse, staff=s0, kaipoke_service_override="X")
    await db.commit()

    res = await client.post(
        URL, headers=_bearer(admin), json=_body(source, target, assign_staff=False, confirm=True)
    )
    assert res.status_code == 200, res.text
    snap_id = res.json()["snapshot_id"]
    assert [v.patient_id for v in await _live(db, target)] == [p2.id]

    r = await client.post(
        f"/api/v1/integrations/inbound-snapshots/{snap_id}/restore", headers=_bearer(admin)
    )
    assert r.status_code == 200, r.text
    live = await _live(db, target)
    assert [v.patient_id for v in live] == [p1.id]
    assert live[0].primary_staff_id == s0.id
    assert live[0].kaipoke_service_override == "X"
    vsa = (
        await db.scalars(
            select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == live[0].id)
        )
    ).all()
    assert [a.staff_id for a in vsa] == [s0.id]
    assert (await db.get(Course, tcourse.id, populate_existing=True)).assigned_staff_id == s0.id


# ---------------------------------------------------------------------------
# 5. 固定訪問から補う (任意)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fill_from_fixed_is_optional(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    p1 = await w.patient("F1", pfv=(0,))
    fresh = await w.patient("F2", pfv=(2,))  # 写す元の週に訪問が無い (新規 or お休み)
    await w.visit(p1, source, 0)
    await db.commit()
    pfv_before = await _pfv_rows(db)

    pv = (
        await client.post(f"{URL}/preview", headers=_bearer(admin), json=_body(source, target))
    ).json()
    assert pv["missing_fixed_count"] == 1
    assert pv["missing_patients_without_visits"] == 1
    assert pv["fill_count"] == 0
    assert pv["missing_fixed"][0]["patient_name"] == "患者F2"

    res = await client.post(
        URL,
        headers=_bearer(admin),
        json=_body(source, target, fill_from_fixed=True, assign_staff=False, confirm=True),
    )
    assert res.status_code == 200, res.text
    assert res.json()["filled"] == 1
    live = await _live(db, target)
    filled = next(v for v in live if v.patient_id == fresh.id)
    assert filled.visit_date == target + timedelta(days=2)
    assert filled.primary_staff_id is None
    assert await _pfv_rows(db) == pfv_before


# ---------------------------------------------------------------------------
# 6. 続けて自動スタッフ割当 → 3 か所の担当が揃う
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_assign_after_copy_keeps_three_places_consistent(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    s0 = w.staff[0]
    scourse = await w.course(source, 0, staff=s0)
    for i in range(3):
        p = await w.patient(f"A{i}", pfv=(0,))
        await w.visit(p, source, 0, start=time(9 + 2 * i, 0), course=scourse, staff=s0)
    await db.commit()

    res = await client.post(URL, headers=_bearer(admin), json=_body(source, target, confirm=True))
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["created"] == 3
    assert out["assign_result"] is not None
    live = await _live(db, target)
    assert len(live) == 3
    for v in live:
        course = await db.get(Course, v.course_id, populate_existing=True)
        assert course.assigned_staff_id is not None
        assert v.primary_staff_id == course.assigned_staff_id
        vsa = (
            await db.scalars(
                select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == v.id)
            )
        ).all()
        assert [a.staff_id for a in vsa] == [course.assigned_staff_id]


# ---------------------------------------------------------------------------
# 7. 月跨ぎ週 / 排他 / 入力検査 / 権限
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_month_crossing_week(client, db) -> None:
    admin = await _user(db)
    target = monday_of(today_jst()) + timedelta(weeks=1)
    while target.month == (target + timedelta(days=6)).month:
        target += timedelta(weeks=1)
    source = target - timedelta(weeks=1)
    w = await World(db, source, target).setup()
    p = await w.patient("M1", pfv=(0, 5))
    await w.visit(p, source, 0)
    await w.visit(p, source, 5)
    await db.commit()
    res = await client.post(
        URL, headers=_bearer(admin), json=_body(source, target, assign_staff=False, confirm=True)
    )
    assert res.status_code == 200, res.text
    dates = sorted(v.visit_date for v in await _live(db, target))
    assert dates == [target, target + timedelta(days=5)]
    assert dates[0].month != (target + timedelta(days=6)).month or dates[1].month != target.month


@pytest.mark.asyncio
async def test_lock_conflict_returns_409(client, db) -> None:
    from app.api.v1.schedule import _get_assign_staff_only_lock

    admin = await _user(db)
    source, target = _weeks()
    iso = target.isocalendar()
    lock = _get_assign_staff_only_lock(iso.year, iso.week)
    async with lock:
        res = await client.post(
            URL, headers=_bearer(admin), json=_body(source, target, confirm=True)
        )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_validation_and_rbac(client, db) -> None:
    admin = await _user(db)
    staff_user = await _user(db, role="staff")
    source, target = _weeks()
    # confirm 無しは 422
    res = await client.post(URL, headers=_bearer(admin), json=_body(source, target))
    assert res.status_code == 422
    # 写す先が先週 (今週より前) は 422
    past = monday_of(today_jst()) - timedelta(weeks=1)
    res = await client.post(
        f"{URL}/preview", headers=_bearer(admin), json=_body(past - timedelta(weeks=1), past)
    )
    assert res.status_code == 422
    # 写す元が 9 週前は 422
    res = await client.post(
        f"{URL}/preview", headers=_bearer(admin), json=_body(target - timedelta(weeks=9), target)
    )
    assert res.status_code == 422
    # 月曜以外は 422
    res = await client.post(
        f"{URL}/preview", headers=_bearer(admin), json=_body(source + timedelta(days=1), target)
    )
    assert res.status_code == 422
    # staff は 403
    res = await client.post(
        f"{URL}/preview", headers=_bearer(staff_user), json=_body(source, target)
    )
    assert res.status_code == 403
    res = await client.post(
        URL, headers=_bearer(staff_user), json=_body(source, target, confirm=True)
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_past_days_of_this_week_are_not_touched(client, db) -> None:
    today = today_jst()
    if today.weekday() == 0:
        pytest.skip("月曜は今週に過去日が無い")
    admin = await _user(db)
    target = monday_of(today)
    source = target - timedelta(weeks=1)
    w = await World(db, source, target).setup()
    p = await w.patient("PD", pfv=(0,))
    await w.visit(p, source, 0)  # 写す先では月曜 = 今日より前
    old = await w.visit(p, target, 0, start=time(15, 0))
    await db.commit()
    pv = (
        await client.post(f"{URL}/preview", headers=_bearer(admin), json=_body(source, target))
    ).json()
    assert pv["skipped"]["past_day"] == 1
    assert pv["existing"]["keep_past"] == 1
    assert pv["copy_count"] == 0
    res = await client.post(
        URL, headers=_bearer(admin), json=_body(source, target, assign_staff=False, confirm=True)
    )
    assert res.status_code == 200, res.text
    assert (await db.get(Visit, old.id, populate_existing=True)).deleted_at is None


# ---------------------------------------------------------------------------
# 8. 写す元の候補週 / 祝日
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sources_list_counts(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks(ahead=3)
    w = await World(db, source, target).setup()
    p1 = await w.patient("S1")
    p2 = await w.patient("S2")
    v = await w.visit(p1, source, 0)
    await w.visit(p2, source, 1)
    await w.visit(p2, source, 2, status="cancelled")
    await w.visit(p1, source, 3, is_unplanned=True)
    await _checkin(db, v)
    older = source - timedelta(weeks=2)
    await w.visit(p1, older, 0)
    await w.visit(p1, target - timedelta(weeks=9), 0)  # 範囲外
    await db.commit()

    res = await client.get(
        f"{URL}/sources", headers=_bearer(admin), params={"target_week_start": target.isoformat()}
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [i["week_start"] for i in items] == [source.isoformat(), older.isoformat()]
    first = items[0]
    assert (
        first["visits"],
        first["patients"],
        first["cancelled"],
        first["unplanned"],
        first["qr_arrivals"],
    ) == (
        2,
        2,
        1,
        1,
        1,
    )


def test_japanese_holidays() -> None:
    sep = dict(holidays_between(date(2026, 9, 21), date(2026, 9, 27)))
    assert sep == {
        date(2026, 9, 21): "敬老の日",
        date(2026, 9, 22): "国民の休日",
        date(2026, 9, 23): "秋分の日",
    }
    may = dict(holidays_between(date(2026, 5, 3), date(2026, 5, 6)))
    assert may[date(2026, 5, 6)] == "振替休日"
    assert holidays_between(date(2026, 10, 5), date(2026, 10, 11)) == []
    assert dict(holidays_between(date(2026, 10, 12), date(2026, 10, 12))) == {
        date(2026, 10, 12): "スポーツの日"
    }
    assert date(2026, 3, 20) in dict(holidays_between(date(2026, 3, 1), date(2026, 3, 31)))
