"""取り込み前スナップショットと復元 — PO 決定 2026-08-09.

「間違えて取り込んでも、取り込む前に戻せる」を成立させる:

  * ``snapshot_week``  — 実適用 (dry_run=false) の直前に対象週の盤面
    (visits + スタッフ割当 + 同行リンク(訪問単位) + コース) を JSONB へ保存。
    取り込みと同一トランザクション = 取り込みが失敗すれば残らない。
    週ごとに直近 ``KEEP_PER_WEEK`` 世代のみ保持。
  * ``restore_snapshot`` — 週を白紙化 (soft delete) してスナップショットを
    書き戻す。置換取り込みと同じ単一トランザクション方式。

安全装置:
  * 打刻ガード — 対象週の有効訪問に打刻が 1 件でもあれば復元不可
    (実績記録の紐付け先を消さない。``SnapshotRestoreBlockedError``)。
  * コースは id を保って復元する (soft delete の解除 or 同 id で再作成)。
    スナップショットに無い週内コース (取り込みが作ったもの) は soft delete。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.accompaniment import ACCOMPANIMENT_KIND_TRAINEE, Accompaniment
from app.models.course import Course
from app.models.inbound_snapshot import InboundSnapshot
from app.models.visit import Visit
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 週ごとに保持する世代数 (それ以前は保存時に自動削除)。
KEEP_PER_WEEK = 5

# 復元で書き戻す Visit のカラム (id / created / deleted 系は除く)。
_VISIT_FIELDS: tuple[str, ...] = (
    "patient_id",
    "primary_staff_id",
    "secondary_staff_id",
    "mentor_staff_id",
    "visit_date",
    "start_time",
    "end_time",
    "type",
    "status",
    "source",
    "week_pinned",
    "note",
    "kaipoke_id",
    "course_id",
    "required_staff_count",
    "visit_group_id",
    "manual_staff_override",
)

_COURSE_FIELDS: tuple[str, ...] = (
    "id",
    "iso_year",
    "iso_week",
    "weekday",
    "code",
    "course_status",
    "assigned_staff_id",
    "template_id",
    "office_id",
    "note",
)


class SnapshotRestoreBlockedError(RuntimeError):
    """打刻ガード: 打刻の付いた週は復元できない。"""


def _dump(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (date, time, datetime)):
        return value.isoformat()
    return value


def _load_uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(value) if isinstance(value, str) and value else None


async def _week_visits(db: AsyncSession, week_start: date) -> list[Visit]:
    week_end = week_start + timedelta(days=6)
    return list(
        (
            await db.scalars(
                select(Visit).where(
                    Visit.visit_date >= week_start,
                    Visit.visit_date <= week_end,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
    )


async def _week_courses(db: AsyncSession, week_start: date) -> list[Course]:
    iso = week_start.isocalendar()
    return list(
        (
            await db.scalars(
                select(Course).where(
                    Course.iso_year == iso.year,
                    Course.iso_week == iso.week,
                    Course.deleted_at.is_(None),
                )
            )
        ).all()
    )


async def snapshot_week(
    db: AsyncSession,
    week_start: date,
    *,
    kind: str,
    user_id: uuid.UUID | None,
) -> InboundSnapshot:
    """対象週の盤面を丸ごと保存し、古い世代 (KEEP_PER_WEEK 超) を剪定する。"""
    visits = await _week_visits(db, week_start)
    visit_ids = [v.id for v in visits]

    assignment_rows: list[VisitStaffAssignment] = []
    accompaniment_rows: list[Accompaniment] = []
    if visit_ids:
        assignment_rows = list(
            (
                await db.scalars(
                    select(VisitStaffAssignment).where(VisitStaffAssignment.visit_id.in_(visit_ids))
                )
            ).all()
        )
        accompaniment_rows = list(
            (
                await db.scalars(
                    select(Accompaniment).where(
                        Accompaniment.target_type == "visit",
                        Accompaniment.visit_id.in_(visit_ids),
                    )
                )
            ).all()
        )

    # visit_id → payload 内 index (復元時は新 UUID になるため位置で参照する)。
    index_of = {v.id: i for i, v in enumerate(visits)}
    payload = {
        "visits": [{f: _dump(getattr(v, f)) for f in _VISIT_FIELDS} for v in visits],
        "assignments": [
            {"visit_index": index_of[a.visit_id], "staff_id": _dump(a.staff_id)}
            for a in assignment_rows
            if a.visit_id in index_of
        ],
        # mig 0072 で列名が trainee_staff_id → accompanying_staff_id へ変わった。
        # **旧キーも書き続ける**理由は「本番に残る **mig 0072 以前に取った**
        # スナップショットを、新コードの復元側が読めるようにする」ことと対で、
        # ペイロードの形を新旧どちらの読み手でも壊さないため。
        #
        # 注意: これは「アプリだけ旧デプロイへ戻す」ケースの保険にはならない。
        # 旧コードは旧テーブル (trainee_accompaniments) を読むので、mig を戻さない
        # 限り復元以前に動かない。migration 込みでロールバックする前提。
        # 復元側は新キー優先 → 旧キーの順で読む。
        "accompaniments": [
            {
                "visit_index": index_of[a.visit_id],
                "accompanying_staff_id": _dump(a.accompanying_staff_id),
                "trainee_staff_id": _dump(a.accompanying_staff_id),
                "kind": a.kind,
                "source": a.source,
            }
            for a in accompaniment_rows
            if a.visit_id is not None and a.visit_id in index_of
        ],
        "courses": [
            {f: _dump(getattr(c, f)) for f in _COURSE_FIELDS}
            for c in await _week_courses(db, week_start)
        ],
    }

    snap = InboundSnapshot(
        week_start=week_start,
        kind=kind,
        payload=payload,
        visits_count=len(visits),
        created_by_user_id=user_id,
    )
    db.add(snap)
    await db.flush()

    # 剪定: 直近 KEEP_PER_WEEK 世代のみ残す。
    olds = (
        await db.scalars(
            select(InboundSnapshot)
            .where(InboundSnapshot.week_start == week_start)
            .order_by(InboundSnapshot.created_at.desc(), InboundSnapshot.id.desc())
            .offset(KEEP_PER_WEEK)
        )
    ).all()
    for row in olds:
        await db.delete(row)
    return snap


@dataclass
class RestoreResult:
    wiped: int = 0
    restored: int = 0
    courses_restored: int = 0
    courses_removed: int = 0


async def restore_snapshot(
    db: AsyncSession, snap: InboundSnapshot, *, now: datetime
) -> RestoreResult:
    """週を白紙化してスナップショットの盤面を書き戻す (単一トランザクション)。"""
    week_start = snap.week_start
    current = await _week_visits(db, week_start)

    # 打刻ガード: 有効訪問に打刻が 1 件でもあれば復元不可。
    if current:
        checkin = await db.scalar(
            select(VisitCheckin.id)
            .where(VisitCheckin.visit_id.in_([v.id for v in current]))
            .limit(1)
        )
        if checkin is not None:
            raise SnapshotRestoreBlockedError(
                "この週には打刻（訪問実績）が記録されています。実績の紐付け先を守るため、"
                "取り込み前への復元はできません"
            )

    result = RestoreResult(wiped=len(current))

    # 1) 白紙化 (soft delete)。
    for v in current:
        v.deleted_at = now

    # 2) コース復元: id を保って戻す (visits の course_id 参照を生かすため)。
    snap_courses: list[dict] = list(snap.payload.get("courses") or [])
    snap_course_ids = {c.get("id") for c in snap_courses}
    iso = week_start.isocalendar()
    week_courses_all = list(
        (
            await db.scalars(
                select(Course).where(Course.iso_year == iso.year, Course.iso_week == iso.week)
            )
        ).all()
    )
    by_id = {str(c.id): c for c in week_courses_all}
    for cdata in snap_courses:
        existing = by_id.get(str(cdata.get("id")))
        if existing is not None:
            existing.assigned_staff_id = _load_uuid(cdata.get("assigned_staff_id"))
            existing.course_status = cdata.get("course_status") or existing.course_status
            existing.note = cdata.get("note")
            existing.deleted_at = None
        else:
            db.add(
                Course(
                    id=_load_uuid(cdata.get("id")) or uuid.uuid4(),
                    iso_year=int(cdata["iso_year"]),
                    iso_week=int(cdata["iso_week"]),
                    weekday=int(cdata["weekday"]),
                    code=str(cdata.get("code") or ""),
                    course_status=str(cdata.get("course_status") or "planned"),
                    assigned_staff_id=_load_uuid(cdata.get("assigned_staff_id")),
                    template_id=_load_uuid(cdata.get("template_id")),
                    office_id=_load_uuid(cdata.get("office_id")),
                    note=cdata.get("note"),
                )
            )
        result.courses_restored += 1
    # スナップショットに無い週内コース = 取り込みが後から作ったもの → 畳む。
    for c in week_courses_all:
        if str(c.id) not in snap_course_ids and c.deleted_at is None:
            c.deleted_at = now
            result.courses_removed += 1
    await db.flush()

    # 3) 訪問の書き戻し (新 UUID で INSERT)。
    new_visits: list[Visit] = []
    for vdata in snap.payload.get("visits") or []:
        v = Visit(
            patient_id=_load_uuid(vdata.get("patient_id")),
            primary_staff_id=_load_uuid(vdata.get("primary_staff_id")),
            secondary_staff_id=_load_uuid(vdata.get("secondary_staff_id")),
            mentor_staff_id=_load_uuid(vdata.get("mentor_staff_id")),
            visit_date=date.fromisoformat(vdata["visit_date"]),
            start_time=time.fromisoformat(vdata["start_time"]),
            end_time=time.fromisoformat(vdata["end_time"]),
            type=vdata.get("type") or "regular",
            status=vdata.get("status") or "planned",
            source=vdata.get("source") or "manual",
            week_pinned=bool(vdata.get("week_pinned")),
            note=vdata.get("note"),
            kaipoke_id=vdata.get("kaipoke_id"),
            course_id=_load_uuid(vdata.get("course_id")),
            required_staff_count=int(vdata.get("required_staff_count") or 1),
            visit_group_id=_load_uuid(vdata.get("visit_group_id")),
            manual_staff_override=bool(vdata.get("manual_staff_override")),
        )
        db.add(v)
        new_visits.append(v)
    await db.flush()
    result.restored = len(new_visits)

    # 4) スタッフ割当・同行リンクの書き戻し (index → 新 visit)。
    for adata in snap.payload.get("assignments") or []:
        idx = adata.get("visit_index")
        sid = _load_uuid(adata.get("staff_id"))
        if isinstance(idx, int) and 0 <= idx < len(new_visits) and sid is not None:
            db.add(VisitStaffAssignment(visit_id=new_visits[idx].id, staff_id=sid))
    for tdata in snap.payload.get("accompaniments") or []:
        idx = tdata.get("visit_index")
        # 新キー優先 → 旧キー (mig 0072 以前に取ったスナップショットの復元)。
        tid = _load_uuid(tdata.get("accompanying_staff_id")) or _load_uuid(
            tdata.get("trainee_staff_id")
        )
        if isinstance(idx, int) and 0 <= idx < len(new_visits) and tid is not None:
            db.add(
                Accompaniment(
                    accompanying_staff_id=tid,
                    target_type="visit",
                    visit_id=new_visits[idx].id,
                    source=tdata.get("source") or "manual",
                    kind=tdata.get("kind") or ACCOMPANIMENT_KIND_TRAINEE,
                    created_by=None,
                )
            )
    await db.flush()
    return result
