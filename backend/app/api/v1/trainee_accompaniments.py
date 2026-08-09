"""新人同行 (trainee accompaniment) エンドポイント — 設計 §6.

`docs/plans/trainee-accompaniment-design.md` v1.1:

GET  /api/v1/trainee-accompaniments?iso_year=&iso_week=[&trainee_staff_id=]
     -> その週の全同行リンク + 解決済み実効情報 (RBAC: 全ロール)
PUT  /api/v1/trainee-accompaniments
     -> 週単位の一括置換 (1 TX)。is_trainee 検証 409 / 時間重複 422 (重複ペア詳細)
        (RBAC: admin / manager)
GET  /api/v1/trainee-accompaniment-defaults?trainee_staff_id=
     -> 毎週の既定一覧 (RBAC: 全ロール)
PUT  /api/v1/trainee-accompaniment-defaults
     -> 既定の全置換 (RBAC: admin / manager)

RBAC 原則 (§6): 「全ロール同一表示・操作は権限どおり」= GET 全ロール / PUT admin・manager。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.staff import Staff
from app.models.trainee_accompaniment import (
    TraineeAccompaniment,
    TraineeAccompanimentDefault,
)
from app.models.user import User
from app.models.visit import Visit
from app.schemas.trainee_accompaniment import (
    AccompanimentCourseInfo,
    AccompanimentVisitInfo,
    TraineeAccompanimentDefaultRead,
    TraineeAccompanimentDefaultsPut,
    TraineeAccompanimentFutureDeleteResponse,
    TraineeAccompanimentRead,
    TraineeAccompanimentsListResponse,
    TraineeAccompanimentsPut,
    TraineeCourseGuardCourse,
    TraineeCourseGuardResponse,
)
from app.services.trainee_accompaniment import find_time_overlaps, load_effective_visits

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _week_bounds(iso_year: int, iso_week: int) -> tuple[date, date]:
    """ISO 週 → (月曜, 日曜)。無効な週は 422。"""
    try:
        monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid ISO week: year={iso_year} week={iso_week}",
        ) from exc
    return monday, monday + timedelta(days=6)


async def _get_trainee_or_404(db: AsyncSession, staff_id: UUID) -> Staff:
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    return staff


def _require_is_trainee(staff: Staff) -> None:
    """is_trainee=True でなければ 409 (既存 companion PUT と同じ流儀)."""
    if not staff.is_trainee:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Staff {staff.id} is not marked as is_trainee=true",
        )


async def _defaults_to_read(
    db: AsyncSession, rows: list[TraineeAccompanimentDefault]
) -> list[TraineeAccompanimentDefaultRead]:
    """既定 ORM 行を、テンプレ label / office_id を解決した Read へ変換する (§7.5).

    テンプレは高々数件のためバッチ 1 クエリで解決する。soft-delete 済みテンプレでも
    label は表示できるよう deleted フィルタは掛けない (サマリの見た目のみ)。
    """
    tmpl_ids = {r.course_template_id for r in rows}
    label_by_tmpl: dict[UUID, str] = {}
    office_by_tmpl: dict[UUID, UUID] = {}
    if tmpl_ids:
        for tid, label, office_id in (
            await db.execute(
                select(CourseTemplate.id, CourseTemplate.label, CourseTemplate.office_id).where(
                    CourseTemplate.id.in_(tmpl_ids)
                )
            )
        ).all():
            label_by_tmpl[tid] = label
            office_by_tmpl[tid] = office_id
    return [
        TraineeAccompanimentDefaultRead(
            id=r.id,
            trainee_staff_id=r.trainee_staff_id,
            weekday=r.weekday,
            course_template_id=r.course_template_id,
            course_template_label=label_by_tmpl.get(r.course_template_id),
            office_id=office_by_tmpl.get(r.course_template_id),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


async def _build_week_response(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    monday: date,
    sunday: date,
    trainee_staff_id: UUID | None = None,
) -> TraineeAccompanimentsListResponse:
    """当該週の同行リンクを解決済み情報つきで返す (GET / PUT 共通)."""
    # 週に属するリンク: course が当該週 (live) OR visit が当該週 (live)。
    week_course_ids = select(Course.id).where(
        Course.iso_year == iso_year,
        Course.iso_week == iso_week,
        Course.deleted_at.is_(None),
    )
    week_visit_ids = select(Visit.id).where(
        Visit.visit_date >= monday,
        Visit.visit_date <= sunday,
        Visit.deleted_at.is_(None),
    )
    stmt = (
        select(TraineeAccompaniment)
        .where(
            or_(
                TraineeAccompaniment.course_id.in_(week_course_ids),
                TraineeAccompaniment.visit_id.in_(week_visit_ids),
            )
        )
        .order_by(TraineeAccompaniment.created_at, TraineeAccompaniment.id)
    )
    if trainee_staff_id is not None:
        stmt = stmt.where(TraineeAccompaniment.trainee_staff_id == trainee_staff_id)
    links = list((await db.scalars(stmt)).all())

    if not links:
        return TraineeAccompanimentsListResponse(items=[])

    # バッチ解決: trainee 名 / course / visit(patient)。
    trainee_ids = {ln.trainee_staff_id for ln in links}
    course_ids = {ln.course_id for ln in links if ln.course_id is not None}
    visit_ids = {ln.visit_id for ln in links if ln.visit_id is not None}

    staff_name: dict[UUID, str | None] = {}
    if trainee_ids:
        for sid, name in (
            await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(trainee_ids)))
        ).all():
            staff_name[sid] = name

    courses: dict[UUID, Course] = {}
    if course_ids:
        for c in (await db.scalars(select(Course).where(Course.id.in_(course_ids)))).all():
            courses[c.id] = c

    visits: dict[UUID, Visit] = {}
    if visit_ids:
        for v in (
            await db.scalars(
                select(Visit).where(Visit.id.in_(visit_ids)).options(selectinload(Visit.patient))
            )
        ).all():
            visits[v.id] = v

    items: list[TraineeAccompanimentRead] = []
    for ln in links:
        course_info: AccompanimentCourseInfo | None = None
        visit_info: AccompanimentVisitInfo | None = None
        if ln.course_id is not None:
            c = courses.get(ln.course_id)
            if c is not None:
                course_info = AccompanimentCourseInfo(
                    id=c.id,
                    weekday=c.weekday,
                    code=c.code,
                    office_id=c.office_id,
                    template_id=c.template_id,
                )
        if ln.visit_id is not None:
            v = visits.get(ln.visit_id)
            if v is not None:
                visit_info = AccompanimentVisitInfo(
                    id=v.id,
                    date=v.visit_date,
                    start=v.start_time,
                    patient_name=(
                        getattr(v.patient, "name", None) if v.patient is not None else None
                    ),
                )
        items.append(
            TraineeAccompanimentRead(
                id=ln.id,
                trainee_staff_id=ln.trainee_staff_id,
                trainee_staff_name=staff_name.get(ln.trainee_staff_id),
                target_type=ln.target_type,
                source=ln.source,
                course=course_info,
                visit=visit_info,
            )
        )
    return TraineeAccompanimentsListResponse(items=items)


# ---------------------------------------------------------------------------
# GET /trainee-accompaniments (週一覧)
# ---------------------------------------------------------------------------


@router.get(
    "/trainee-accompaniments",
    response_model=TraineeAccompanimentsListResponse,
    summary="その週の同行リンク一覧 (解決済み・全ロール)",
)
async def list_trainee_accompaniments(
    db: DbDep,
    _user: CurrentActiveUser,
    iso_year: Annotated[int, Query(ge=2000, le=2100)],
    iso_week: Annotated[int, Query(ge=1, le=53)],
    trainee_staff_id: Annotated[UUID | None, Query()] = None,
) -> TraineeAccompanimentsListResponse:
    monday, sunday = _week_bounds(iso_year, iso_week)
    return await _build_week_response(
        db,
        iso_year=iso_year,
        iso_week=iso_week,
        monday=monday,
        sunday=sunday,
        trainee_staff_id=trainee_staff_id,
    )


# ---------------------------------------------------------------------------
# PUT /trainee-accompaniments (週一括置換・確定操作)
# ---------------------------------------------------------------------------


@router.put(
    "/trainee-accompaniments",
    response_model=TraineeAccompanimentsListResponse,
    summary="その新人×その週の同行リンク一括置換 (admin/manager)",
)
async def put_trainee_accompaniments(
    body: TraineeAccompanimentsPut,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> TraineeAccompanimentsListResponse:
    """1 TX でその新人×その週のリンクを全置換する (§6.2).

    バリデーション (FE ブロックとの二重防御):
      - staff.is_trainee=True でなければ 409
      - course_ids / visit_ids の存在・週一致・soft-delete チェック (422)
      - 実効同行訪問集合の時間重複があれば 422 (重複ペア詳細つき・確定ブロック)
    defaults: キー省略/null/[] = 既定に触れない。非空配列のみ含まれた曜日を upsert。
    """
    monday, sunday = _week_bounds(body.iso_year, body.iso_week)
    trainee = await _get_trainee_or_404(db, body.trainee_staff_id)
    _require_is_trainee(trainee)

    course_ids = list(dict.fromkeys(body.course_ids))  # 重複除去・順序保持
    visit_ids = list(dict.fromkeys(body.visit_ids))

    # ----- course_ids 検証 (存在・未削除・週一致) -----
    courses_by_id: dict[UUID, Course] = {}
    if course_ids:
        for c in (
            await db.scalars(
                select(Course).where(Course.id.in_(course_ids), Course.deleted_at.is_(None))
            )
        ).all():
            courses_by_id[c.id] = c
        for cid in course_ids:
            c = courses_by_id.get(cid)
            if c is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"course {cid} not found or deleted",
                )
            if c.iso_year != body.iso_year or c.iso_week != body.iso_week:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"course {cid} does not belong to ISO {body.iso_year}-W{body.iso_week}",
                )

    # ----- visit_ids 検証 (存在・未削除・週一致) -----
    if visit_ids:
        visits_by_id: dict[UUID, Visit] = {
            v.id: v
            for v in (
                await db.scalars(
                    select(Visit).where(Visit.id.in_(visit_ids), Visit.deleted_at.is_(None))
                )
            ).all()
        }
        for vid in visit_ids:
            v = visits_by_id.get(vid)
            if v is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"visit {vid} not found or deleted",
                )
            iso = v.visit_date.isocalendar()
            if iso[0] != body.iso_year or iso[1] != body.iso_week:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"visit {vid} does not belong to ISO {body.iso_year}-W{body.iso_week}",
                )

    # ----- 時間重複判定 (確定ブロック 422) -----
    effective = await load_effective_visits(db, course_ids=course_ids, visit_ids=visit_ids)
    overlaps = find_time_overlaps(effective)
    if overlaps:
        # 重複ペアの course_code を解決 (表示用)。
        eff_course_ids = {v.course_id for v in effective if v.course_id is not None}
        code_by_course: dict[UUID, str] = {}
        if eff_course_ids:
            for cid, code in (
                await db.execute(
                    select(Course.id, Course.code).where(Course.id.in_(eff_course_ids))
                )
            ).all():
                code_by_course[cid] = code

        def _side(v: Visit) -> dict:
            return {
                "visit_id": str(v.id),
                "patient_name": getattr(v.patient, "name", None) if v.patient is not None else None,
                "start": v.start_time.strftime("%H:%M"),
                "end": v.end_time.strftime("%H:%M"),
                "course_code": code_by_course.get(v.course_id) if v.course_id is not None else None,
            }

        pairs = [
            {"date": a.visit_date.isoformat(), "a": _side(a), "b": _side(b)} for a, b in overlaps
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "時間が重複する同行選択があります（同時には行けません）",
                "overlaps": pairs,
            },
        )

    # ----- defaults 検証 (非空配列時のみ・曜日重複 422 / テンプレ存在 422) -----
    touch_defaults = bool(body.defaults)
    if touch_defaults:
        seen_wd: set[int] = set()
        for it in body.defaults or []:
            if it.weekday in seen_wd:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"duplicate weekday={it.weekday} in defaults",
                )
            seen_wd.add(it.weekday)
            tmpl = await db.scalar(
                select(CourseTemplate).where(
                    CourseTemplate.id == it.course_template_id,
                    CourseTemplate.deleted_at.is_(None),
                )
            )
            if tmpl is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"course_template {it.course_template_id} not found or deleted",
                )

    # ----- 置換 (その新人×その週のみ) -----
    week_course_ids = select(Course.id).where(
        Course.iso_year == body.iso_year, Course.iso_week == body.iso_week
    )
    await db.execute(
        delete(TraineeAccompaniment).where(
            TraineeAccompaniment.trainee_staff_id == body.trainee_staff_id,
            TraineeAccompaniment.course_id.in_(week_course_ids),
        )
    )
    week_visit_ids = select(Visit.id).where(Visit.visit_date >= monday, Visit.visit_date <= sunday)
    await db.execute(
        delete(TraineeAccompaniment).where(
            TraineeAccompaniment.trainee_staff_id == body.trainee_staff_id,
            TraineeAccompaniment.visit_id.in_(week_visit_ids),
        )
    )

    for cid in course_ids:
        db.add(
            TraineeAccompaniment(
                trainee_staff_id=body.trainee_staff_id,
                target_type="course",
                course_id=cid,
                source="manual",
                created_by=user.id,
            )
        )
    for vid in visit_ids:
        db.add(
            TraineeAccompaniment(
                trainee_staff_id=body.trainee_staff_id,
                target_type="visit",
                visit_id=vid,
                source="manual",
                created_by=user.id,
            )
        )

    # ----- defaults の upsert (含まれた曜日のみ) -----
    if touch_defaults:
        for it in body.defaults or []:
            await db.execute(
                delete(TraineeAccompanimentDefault).where(
                    TraineeAccompanimentDefault.trainee_staff_id == body.trainee_staff_id,
                    TraineeAccompanimentDefault.weekday == it.weekday,
                )
            )
            db.add(
                TraineeAccompanimentDefault(
                    trainee_staff_id=body.trainee_staff_id,
                    weekday=it.weekday,
                    course_template_id=it.course_template_id,
                    created_by=user.id,
                )
            )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflict: duplicate accompaniment link",
        ) from exc

    return await _build_week_response(
        db,
        iso_year=body.iso_year,
        iso_week=body.iso_week,
        monday=monday,
        sunday=sunday,
        trainee_staff_id=body.trainee_staff_id,
    )


# ---------------------------------------------------------------------------
# GET/PUT /trainee-accompaniment-defaults
# ---------------------------------------------------------------------------


@router.get(
    "/trainee-accompaniment-defaults",
    response_model=list[TraineeAccompanimentDefaultRead],
    summary="毎週の既定一覧 (全ロール)",
)
async def list_trainee_accompaniment_defaults(
    db: DbDep,
    _user: CurrentActiveUser,
    trainee_staff_id: Annotated[UUID, Query()],
) -> list[TraineeAccompanimentDefaultRead]:
    rows = (
        await db.scalars(
            select(TraineeAccompanimentDefault)
            .where(TraineeAccompanimentDefault.trainee_staff_id == trainee_staff_id)
            .order_by(TraineeAccompanimentDefault.weekday)
        )
    ).all()
    return await _defaults_to_read(db, list(rows))


@router.put(
    "/trainee-accompaniment-defaults",
    response_model=list[TraineeAccompanimentDefaultRead],
    summary="毎週の既定を全置換 (admin/manager)",
)
async def put_trainee_accompaniment_defaults(
    body: TraineeAccompanimentDefaultsPut,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> list[TraineeAccompanimentDefaultRead]:
    """当該新人の既定を全置換する (曜日×course_template の配列).

    - staff.is_trainee=True でなければ 409
    - 曜日重複 → 422 / テンプレ不存在 or 削除済み → 422
    """
    trainee = await _get_trainee_or_404(db, body.trainee_staff_id)
    _require_is_trainee(trainee)

    seen_wd: set[int] = set()
    for it in body.items:
        if it.weekday in seen_wd:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"duplicate weekday={it.weekday}",
            )
        seen_wd.add(it.weekday)
        tmpl = await db.scalar(
            select(CourseTemplate).where(
                CourseTemplate.id == it.course_template_id,
                CourseTemplate.deleted_at.is_(None),
            )
        )
        if tmpl is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"course_template {it.course_template_id} not found or deleted",
            )

    await db.execute(
        delete(TraineeAccompanimentDefault).where(
            TraineeAccompanimentDefault.trainee_staff_id == body.trainee_staff_id
        )
    )
    for it in body.items:
        db.add(
            TraineeAccompanimentDefault(
                trainee_staff_id=body.trainee_staff_id,
                weekday=it.weekday,
                course_template_id=it.course_template_id,
                created_by=user.id,
            )
        )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflict: duplicate (trainee_staff_id, weekday)",
        ) from exc

    rows = (
        await db.scalars(
            select(TraineeAccompanimentDefault)
            .where(TraineeAccompanimentDefault.trainee_staff_id == body.trainee_staff_id)
            .order_by(TraineeAccompanimentDefault.weekday)
        )
    ).all()
    return await _defaults_to_read(db, list(rows))


# ---------------------------------------------------------------------------
# §8-4: is_trainee ON 警告用「今週以降のコース担当」ガード
# ---------------------------------------------------------------------------


def _current_week() -> tuple[int, int, date]:
    """今日 (JST) を含む ISO 週 (year, week, その週の月曜) を返す.

    本番 backend コンテナは TZ 未設定 (UTC) のため `date.today()` を使うと
    JST 月曜 00:00-09:00 の窓で前週判定になり、DELETE /future が
    「実績履歴として保持」すべき直前週リンクまで消してしまう。
    サーバ側で「今日」を評価するのは本モジュールの 2 EP のみなので JST に固定する。
    """
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    iso = today.isocalendar()
    monday = date.fromisocalendar(iso[0], iso[1], 1)
    return iso[0], iso[1], monday


@router.get(
    "/trainee-accompaniments/course-guard",
    response_model=TraineeCourseGuardResponse,
    summary="新人が今週以降にコース担当として残っているか (全ロール・警告主義 §8-4)",
)
async def trainee_course_guard(
    db: DbDep,
    _user: CurrentActiveUser,
    trainee_staff_id: Annotated[UUID, Query()],
) -> TraineeCourseGuardResponse:
    """当該スタッフが今週以降のコース担当 (courses.assigned_staff_id) に残っているか.

    is_trainee を ON にする際の警告表示用。自動解除はしない (警告主義・§8-4)。
    """
    cur_year, cur_week, _monday = _current_week()
    rows = (
        await db.execute(
            select(Course.id, Course.iso_year, Course.iso_week, Course.weekday, Course.code)
            .where(
                Course.assigned_staff_id == trainee_staff_id,
                Course.deleted_at.is_(None),
                or_(
                    Course.iso_year > cur_year,
                    and_(Course.iso_year == cur_year, Course.iso_week >= cur_week),
                ),
            )
            .order_by(Course.iso_year, Course.iso_week, Course.weekday)
        )
    ).all()
    courses = [
        TraineeCourseGuardCourse(id=cid, iso_year=iy, iso_week=iw, weekday=wd, code=code)
        for (cid, iy, iw, wd, code) in rows
    ]
    return TraineeCourseGuardResponse(
        trainee_staff_id=trainee_staff_id,
        count=len(courses),
        courses=courses,
    )


# ---------------------------------------------------------------------------
# §7.5: is_trainee OFF 時の将来リンク + 既定の一括削除
# ---------------------------------------------------------------------------


@router.delete(
    "/trainee-accompaniments/future",
    response_model=TraineeAccompanimentFutureDeleteResponse,
    summary="今週以降の同行リンク + 毎週の既定を一括削除 (admin/manager・冪等)",
)
async def delete_future_trainee_accompaniments(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    trainee_staff_id: Annotated[UUID, Query()],
) -> TraineeAccompanimentFutureDeleteResponse:
    """今週以降の週リンク (course/visit) を物理削除し、毎週の既定を全削除する (§7.5).

    is_trainee OFF 操作の確認ダイアログから呼ぶ。過去週のリンクは実績履歴として残す。
    冪等 (対象が無くても 0 件で成功)。
    """
    cur_year, cur_week, monday = _current_week()

    # 今週以降のコースリンク: course が (iso_year, iso_week) >= 今週。
    future_course_ids = select(Course.id).where(
        or_(
            Course.iso_year > cur_year,
            and_(Course.iso_year == cur_year, Course.iso_week >= cur_week),
        ),
    )
    # 今週以降の個別リンク: visit_date >= 今週の月曜。
    future_visit_ids = select(Visit.id).where(Visit.visit_date >= monday)

    res_links = await db.execute(
        delete(TraineeAccompaniment).where(
            TraineeAccompaniment.trainee_staff_id == trainee_staff_id,
            or_(
                TraineeAccompaniment.course_id.in_(future_course_ids),
                TraineeAccompaniment.visit_id.in_(future_visit_ids),
            ),
        )
    )
    res_defaults = await db.execute(
        delete(TraineeAccompanimentDefault).where(
            TraineeAccompanimentDefault.trainee_staff_id == trainee_staff_id
        )
    )
    await db.commit()

    return TraineeAccompanimentFutureDeleteResponse(
        trainee_staff_id=trainee_staff_id,
        deleted_links=int(res_links.rowcount or 0),
        deleted_defaults=int(res_defaults.rowcount or 0),
    )
