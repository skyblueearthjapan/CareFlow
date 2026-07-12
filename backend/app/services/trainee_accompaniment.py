"""新人同行 (trainee accompaniment) サービス — 設計 §5 / §6.4.

`docs/plans/trainee-accompaniment-design.md` v1.1:
- ``expand_accompaniment_defaults``: 毎週の既定を週へ物質化する (冪等・週全体)。
  週生成 / 固定枠に戻す / 自動割当 の 3 地点から呼ぶ。孤立リンク掃除 (S-1) を同梱。
- 可視性ヘルパー: モバイル 3 経路 (§6.4 C-1) が同行訪問を「新人本人の訪問」と
  みなすための条件 (visit 直リンク OR course リンク・live JOIN)。
- 射影ヘルパー: VisitRead / モニターへ同行者名を非破壊で載せる解決。
- 重複判定ヘルパー: PUT の確定ブロック (時間重複 422) 用。

**唯一の正典** は本テーブル。読み出しは常に live JOIN (``courses.deleted_at IS NULL``)
で守り、``visits.*_staff_id`` / VSA には書かない (設計 §2)。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.course import COURSE_STATUS_PROPOSED, Course
from app.models.course_template import CourseTemplate
from app.models.staff import Staff
from app.models.trainee_accompaniment import (
    TraineeAccompaniment,
    TraineeAccompanimentDefault,
)
from app.models.visit import VISIT_STATUS_CANCELLED, Visit

# ---------------------------------------------------------------------------
# 展開 (物質化) — 設計 §5.1
# ---------------------------------------------------------------------------


async def _cleanup_orphan_links(db: AsyncSession, iso_year: int, iso_week: int) -> int:
    """当該週の soft-delete 済み visit / course を参照する同行リンクを物理削除 (S-1).

    visit は soft-delete のため FK CASCADE が発火しない。読み出しは live JOIN で
    守られるが、蓄積を防ぐため物理削除する。返却 = 削除件数。
    """
    try:
        monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError:
        return 0
    sunday = monday + timedelta(days=6)

    removed = 0

    dead_course_ids = list(
        (
            await db.scalars(
                select(Course.id).where(
                    Course.iso_year == iso_year,
                    Course.iso_week == iso_week,
                    Course.deleted_at.is_not(None),
                )
            )
        ).all()
    )
    if dead_course_ids:
        res = await db.execute(
            delete(TraineeAccompaniment).where(TraineeAccompaniment.course_id.in_(dead_course_ids))
        )
        removed += int(res.rowcount or 0)

    dead_visit_ids = list(
        (
            await db.scalars(
                select(Visit.id).where(
                    Visit.visit_date >= monday,
                    Visit.visit_date <= sunday,
                    Visit.deleted_at.is_not(None),
                )
            )
        ).all()
    )
    if dead_visit_ids:
        res = await db.execute(
            delete(TraineeAccompaniment).where(TraineeAccompaniment.visit_id.in_(dead_visit_ids))
        )
        removed += int(res.rowcount or 0)

    return removed


async def expand_accompaniment_defaults(db: AsyncSession, iso_year: int, iso_week: int) -> int:
    """毎週の既定を当該週の同行リンクへ物質化する (冪等・週全体) — 設計 §5.1.

    - **冪等**: 既存リンク (trainee × course) はスキップする。
    - **週全体**: defaults は新人×曜日で高々数行のため常に全走査する (スコープ絞りの
      バグを構造的に避ける)。
    - 展開対象コース: ``template_id 一致 AND weekday 一致 AND course_status != 'proposed'
      AND deleted_at IS NULL``。proposed へはリンクを張らない (再算出 soft-delete で
      孤立するため)。defaults 側は ``course_templates.deleted_at IS NULL`` を常時適用。
    - 孤立リンク掃除 (S-1) を先に行う。

    ``commit`` はしない (``flush`` のみ)。呼び出し側 (週生成 / reset / 自動割当) が
    トランザクション境界を制御する。返却 = 新規に張ったリンク数。
    """
    await _cleanup_orphan_links(db, iso_year, iso_week)

    defaults = list(
        (
            await db.scalars(
                select(TraineeAccompanimentDefault)
                .join(
                    CourseTemplate,
                    TraineeAccompanimentDefault.course_template_id == CourseTemplate.id,
                )
                .where(CourseTemplate.deleted_at.is_(None))
            )
        ).all()
    )
    if not defaults:
        return 0

    # 既存リンク (trainee, course) を 1 度だけロードして冪等判定に使う。
    existing_pairs: set[tuple[UUID, UUID]] = set(
        (
            await db.execute(
                select(
                    TraineeAccompaniment.trainee_staff_id,
                    TraineeAccompaniment.course_id,
                ).where(TraineeAccompaniment.course_id.is_not(None))
            )
        ).all()
    )

    created = 0
    for d in defaults:
        courses = list(
            (
                await db.scalars(
                    select(Course).where(
                        Course.iso_year == iso_year,
                        Course.iso_week == iso_week,
                        Course.weekday == d.weekday,
                        Course.template_id == d.course_template_id,
                        Course.course_status != COURSE_STATUS_PROPOSED,
                        Course.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        for course in courses:
            key = (d.trainee_staff_id, course.id)
            if key in existing_pairs:
                continue
            db.add(
                TraineeAccompaniment(
                    trainee_staff_id=d.trainee_staff_id,
                    target_type="course",
                    course_id=course.id,
                    source="default",
                    created_by=d.created_by,
                )
            )
            existing_pairs.add(key)
            created += 1

    if created:
        await db.flush()
    return created


# ---------------------------------------------------------------------------
# 可視性 (§6.4 C-1) — visit 直リンク OR course リンク (live JOIN)
# ---------------------------------------------------------------------------


def accompaniment_visibility_condition(staff_id: UUID) -> ColumnElement[bool]:
    """当該 trainee スタッフに同行訪問が見える WHERE 条件 (``visits.py`` の一覧用).

    visit 直リンク OR course リンク (``courses.deleted_at IS NULL`` の live JOIN)。
    ``_staff_visibility_filter`` の ``or_(...)`` に足す。
    """
    direct_subq = select(TraineeAccompaniment.visit_id).where(
        TraineeAccompaniment.trainee_staff_id == staff_id,
        TraineeAccompaniment.visit_id.is_not(None),
    )
    course_subq = (
        select(TraineeAccompaniment.course_id)
        .join(Course, TraineeAccompaniment.course_id == Course.id)
        .where(
            TraineeAccompaniment.trainee_staff_id == staff_id,
            TraineeAccompaniment.course_id.is_not(None),
            Course.deleted_at.is_(None),
        )
    )
    return or_(
        Visit.id.in_(direct_subq),
        and_(Visit.course_id.is_not(None), Visit.course_id.in_(course_subq)),
    )


async def is_accompaniment_visit_for_staff(
    db: AsyncSession,
    *,
    visit_id: UUID,
    course_id: UUID | None,
    staff_id: UUID,
) -> bool:
    """単体 visit が当該 trainee スタッフの同行対象か (set 判定 3 経路の共通ヘルパー).

    visit 直リンク OR course リンク (live JOIN)。GET /visits/{id} と checkin ロードの
    可視性 set 判定に足す (漏れると新人がカードをタップした瞬間 404)。
    """
    direct = await db.scalar(
        select(TraineeAccompaniment.id)
        .where(
            TraineeAccompaniment.trainee_staff_id == staff_id,
            TraineeAccompaniment.visit_id == visit_id,
        )
        .limit(1)
    )
    if direct is not None:
        return True
    if course_id is None:
        return False
    course_link = await db.scalar(
        select(TraineeAccompaniment.id)
        .join(Course, TraineeAccompaniment.course_id == Course.id)
        .where(
            TraineeAccompaniment.trainee_staff_id == staff_id,
            TraineeAccompaniment.course_id == course_id,
            Course.deleted_at.is_(None),
        )
        .limit(1)
    )
    return course_link is not None


# ---------------------------------------------------------------------------
# 射影 (VisitRead / モニター) — 同行者名の非破壊解決
# ---------------------------------------------------------------------------


async def resolve_accompaniment_by_visit(
    db: AsyncSession, visits: list[Visit]
) -> dict[UUID, tuple[UUID, str | None]]:
    """visit_id -> (trainee_staff_id, trainee_name) を解決する (直リンク優先).

    直リンクが無ければ course リンク (live JOIN) で解決する。同行が無い visit は
    キーを含めない。VisitRead / モニター両方で共用する。
    """
    if not visits:
        return {}

    visit_ids = [v.id for v in visits]
    course_ids = {v.course_id for v in visits if v.course_id is not None}

    name_by_staff: dict[UUID, str | None] = {}

    # コースリンク: course_id -> trainee_staff_id (live JOIN)。
    course_map: dict[UUID, UUID] = {}
    if course_ids:
        rows = (
            await db.execute(
                select(
                    TraineeAccompaniment.course_id,
                    TraineeAccompaniment.trainee_staff_id,
                    Staff.name,
                )
                .join(Course, TraineeAccompaniment.course_id == Course.id)
                .join(Staff, TraineeAccompaniment.trainee_staff_id == Staff.id)
                .where(
                    TraineeAccompaniment.course_id.in_(course_ids),
                    Course.deleted_at.is_(None),
                )
            )
        ).all()
        for cid, tsid, tname in rows:
            course_map[cid] = tsid
            name_by_staff[tsid] = tname

    # 直リンク: visit_id -> trainee_staff_id。
    direct_map: dict[UUID, UUID] = {}
    rows = (
        await db.execute(
            select(
                TraineeAccompaniment.visit_id,
                TraineeAccompaniment.trainee_staff_id,
                Staff.name,
            )
            .join(Staff, TraineeAccompaniment.trainee_staff_id == Staff.id)
            .where(TraineeAccompaniment.visit_id.in_(visit_ids))
        )
    ).all()
    for vid, tsid, tname in rows:
        direct_map[vid] = tsid
        name_by_staff[tsid] = tname

    result: dict[UUID, tuple[UUID, str | None]] = {}
    for v in visits:
        tsid = direct_map.get(v.id)
        if tsid is None and v.course_id is not None:
            tsid = course_map.get(v.course_id)
        if tsid is not None:
            result[v.id] = (tsid, name_by_staff.get(tsid))
    return result


async def resolve_accompaniment_trainee_by_course(
    db: AsyncSession, course_ids: list[UUID]
) -> dict[UUID, set[UUID]]:
    """course_id -> 同行新人 staff_id の集合を解決する (live JOIN).

    逆取込 (inbound) の add 経路が「新設 visit が張り付くコースに同行リンクがあり、
    カイポケ側 staff2 がその同行新人と一致するか」を突合するための course-level
    ヘルパー (設計 §9)。同行が無いコースはキーを含めない。

    集合を返すのは、1 コースに複数新人が張られている場合 (UNIQUE は
    (trainee, course) 単位のため可能) に単一値の last-wins だと突合が非決定的になり、
    CSV が送った新人と別の新人を比較して「同行由来なのに secondary へ書き戻す」
    汚染が起き得るため (Phase 3 レビュー MINOR-2)。membership 判定で構造的に防ぐ。
    """
    if not course_ids:
        return {}
    rows = (
        await db.execute(
            select(
                TraineeAccompaniment.course_id,
                TraineeAccompaniment.trainee_staff_id,
            )
            .join(Course, TraineeAccompaniment.course_id == Course.id)
            .where(
                TraineeAccompaniment.course_id.in_(course_ids),
                Course.deleted_at.is_(None),
            )
        )
    ).all()
    result: dict[UUID, set[UUID]] = {}
    for cid, tsid in rows:
        result.setdefault(cid, set()).add(tsid)
    return result


async def resolve_accompaniment_trainees_by_visit(
    db: AsyncSession, visits: list[Visit]
) -> dict[UUID, set[UUID]]:
    """visit_id -> その訪問に同行する新人 staff_id の集合を解決する (live JOIN).

    逆取込 (inbound) の edit 経路の同行由来 staff2 突合用 (設計 §9)。
    表示用の resolve_accompaniment_by_visit は代表 1 名しか返さないため、
    複数新人リンク時の突合が非決定的になる — こちらは直リンク ∪ course リンクの
    全集合を返し、membership 判定でラウンドトリップ汚染を構造的に防ぐ
    (Phase 3 レビュー MINOR-2)。同行が無い visit はキーを含めない。
    """
    if not visits:
        return {}

    visit_ids = [v.id for v in visits]
    course_ids = {v.course_id for v in visits if v.course_id is not None}

    course_sets: dict[UUID, set[UUID]] = (
        await resolve_accompaniment_trainee_by_course(db, list(course_ids))
        if course_ids
        else {}
    )

    direct_sets: dict[UUID, set[UUID]] = {}
    rows = (
        await db.execute(
            select(
                TraineeAccompaniment.visit_id,
                TraineeAccompaniment.trainee_staff_id,
            ).where(TraineeAccompaniment.visit_id.in_(visit_ids))
        )
    ).all()
    for vid, tsid in rows:
        direct_sets.setdefault(vid, set()).add(tsid)

    result: dict[UUID, set[UUID]] = {}
    for v in visits:
        merged = set(direct_sets.get(v.id, set()))
        if v.course_id is not None:
            merged |= course_sets.get(v.course_id, set())
        if merged:
            result[v.id] = merged
    return result


# ---------------------------------------------------------------------------
# 重複判定 (PUT の確定ブロック) — 設計 §6.2 / §7
# ---------------------------------------------------------------------------


async def load_effective_visits(
    db: AsyncSession,
    *,
    course_ids: list[UUID],
    visit_ids: list[UUID],
) -> list[Visit]:
    """実効同行訪問集合を構築する (コースリンク先の planned 訪問 ∪ 個別リンク訪問).

    patient を eager-load して返す (重複排除済み)。soft-delete / cancelled は除外。
    """
    seen: dict[UUID, Visit] = {}

    if course_ids:
        rows = (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.course_id.in_(course_ids),
                    Visit.deleted_at.is_(None),
                    Visit.status != VISIT_STATUS_CANCELLED,
                )
                .options(selectinload(Visit.patient))
            )
        ).all()
        for v in rows:
            seen[v.id] = v
    if visit_ids:
        rows = (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.id.in_(visit_ids),
                    Visit.deleted_at.is_(None),
                    Visit.status != VISIT_STATUS_CANCELLED,
                )
                .options(selectinload(Visit.patient))
            )
        ).all()
        for v in rows:
            seen[v.id] = v
    return list(seen.values())


def find_time_overlaps(visits: list[Visit]) -> list[tuple[Visit, Visit]]:
    """同一日で時間帯 (start〜end) が交差する visit ペアを列挙する.

    同一人物 (新人) が同時刻に 2 箇所は物理的に不可能 → 確定ブロック (422) の根拠。
    区間交差の定義: ``a.start < b.end AND b.start < a.end``。
    """
    by_date: dict[date, list[Visit]] = defaultdict(list)
    for v in visits:
        by_date[v.visit_date].append(v)

    pairs: list[tuple[Visit, Visit]] = []
    for group in by_date.values():
        group.sort(key=lambda v: (v.start_time, v.end_time))
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.start_time < b.end_time and b.start_time < a.end_time:
                    pairs.append((a, b))
    return pairs
