"""同行 (accompaniment) エンドポイント — 設計 §6 / 一般化 §3.

正典設計書:
- `docs/plans/trainee-accompaniment-design.md` v1.1 (基盤・新人同行)
- `docs/plans/general-accompaniment-design.md` (一般化・mig 0072)

GET  /api/v1/accompaniments?iso_year=&iso_week=[&staff_id=]
     -> その週の全同行リンク + 解決済み実効情報 (RBAC: 全ロール)
PUT  /api/v1/accompaniments
     -> 週単位の一括置換 (1 TX)。対象適格 409 / 時間重複 422 / NG・性別 422 確認
        (RBAC: admin)
GET  /api/v1/accompaniment-defaults?staff_id=
     -> 毎週の既定一覧 (RBAC: 全ロール)
PUT  /api/v1/accompaniment-defaults
     -> 既定の全置換 (RBAC: admin)
GET  /api/v1/accompaniments/course-guard?staff_id=
DELETE /api/v1/accompaniments/future?staff_id=

**旧パス** (``/trainee-accompaniments`` 系) は同一ハンドラの互換エイリアスとして
残してある (一般化 §3-8: FE 移行後に削除)。パスが 2 系統あるだけで、実装・
バリデーション・レスポンスは完全に同一。

RBAC 原則 (§6): 「全ロール同一表示・操作は権限どおり」= GET 全ロール / PUT admin。
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
from app.models.accompaniment import (
    Accompaniment,
    AccompanimentDefault,
)
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import Visit
from app.schemas.accompaniment import (
    AccompanimentCourseInfo,
    AccompanimentDefaultRead,
    AccompanimentDefaultsPut,
    AccompanimentFutureDeleteResponse,
    AccompanimentRead,
    AccompanimentsListResponse,
    AccompanimentsPut,
    AccompanimentVisitInfo,
    CourseGuardCourse,
    CourseGuardResponse,
)
from app.services.accompaniment import (
    accompaniment_overlap_detail,
    collect_accompaniment_conflicts,
    delete_future_accompaniments,
    load_effective_visits,
    load_own_duty_visits,
    resolve_accompaniment_kind,
)
from app.services.constraint_override_notify import (
    ConstraintWarning,
    collect_constraint_warnings_for_staff,
    constraint_confirmation_detail,
    notify_constraint_override,
)

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


async def _get_staff_or_404(db: AsyncSession, staff_id: UUID) -> Staff:
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    return staff


def _require_accompaniment_eligible(staff: Staff) -> None:
    """同行者になれるか (一般化 §3-1: **新人限定を撤廃**).

    旧 ``_require_is_trainee`` は ``is_trainee=False`` を 409 で弾いていたが、
    一般スタッフの同行を認めた (決定#0) ため条件は「在籍していること」だけになる:
    ``status='active'`` かつ ``deleted_at IS NULL`` (呼び出し前に 404 で担保済み)。

    退職者 / 休職者を同行に指名できてしまうと、その人には出勤予定が無いので
    現場で誰も来ない事故になる。ここは新人判定の代わりに残す最後の門番。
    """
    if staff.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Staff {staff.id} is not active (status={staff.status!r})",
        )


async def _defaults_to_read(
    db: AsyncSession, rows: list[AccompanimentDefault]
) -> list[AccompanimentDefaultRead]:
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
        AccompanimentDefaultRead(
            id=r.id,
            staff_id=r.accompanying_staff_id,
            trainee_staff_id=r.accompanying_staff_id,
            kind=r.kind,
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
    staff_id: UUID | None = None,
) -> AccompanimentsListResponse:
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
        select(Accompaniment)
        .where(
            or_(
                Accompaniment.course_id.in_(week_course_ids),
                Accompaniment.visit_id.in_(week_visit_ids),
            )
        )
        .order_by(Accompaniment.created_at, Accompaniment.id)
    )
    if staff_id is not None:
        stmt = stmt.where(Accompaniment.accompanying_staff_id == staff_id)
    links = list((await db.scalars(stmt)).all())

    if not links:
        return AccompanimentsListResponse(items=[])

    # バッチ解決: スタッフ名 / course / visit(patient)。
    staff_ids = {ln.accompanying_staff_id for ln in links}
    course_ids = {ln.course_id for ln in links if ln.course_id is not None}
    visit_ids = {ln.visit_id for ln in links if ln.visit_id is not None}

    staff_name: dict[UUID, str | None] = {}
    if staff_ids:
        for sid, name in (
            await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids)))
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

    items: list[AccompanimentRead] = []
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
        resolved_name = staff_name.get(ln.accompanying_staff_id)
        items.append(
            AccompanimentRead(
                id=ln.id,
                staff_id=ln.accompanying_staff_id,
                staff_name=resolved_name,
                trainee_staff_id=ln.accompanying_staff_id,
                trainee_staff_name=resolved_name,
                kind=ln.kind,
                target_type=ln.target_type,
                source=ln.source,
                course=course_info,
                visit=visit_info,
            )
        )
    return AccompanimentsListResponse(items=items)


async def _collect_accompaniment_constraint_warnings(
    db: AsyncSession,
    *,
    staff_id: UUID,
    effective: list[Visit],
) -> list[ConstraintWarning]:
    """同行対象の患者 × 同行スタッフの NG / 性別違反を洗い出す (一般化 決定#4).

    同行者も患者宅へ上がる以上、NG スタッフ指定や性別制限は担当と同じ重みで
    効かなければならない。検査の芯は ``constraint_override_notify`` と共有し
    (``collect_constraint_warnings_for_staff``)、警告の形・通知本文・FE ダイアログを
    他経路と完全同形にする。

    患者は重複除去してから 1 回で問い合わせる (N+1 禁止)。
    """
    patient_ids = list(dict.fromkeys(v.patient_id for v in effective if v.patient_id is not None))
    if not patient_ids:
        return []
    return await collect_constraint_warnings_for_staff(
        db, staff_id=staff_id, patient_ids=patient_ids
    )


async def _notify_accompaniment_override(
    db: AsyncSession,
    *,
    staff: Staff,
    warnings: list[ConstraintWarning],
    effective: list[Visit],
    actor: User | None,
) -> None:
    """acknowledge で通した制約 override を管理者へお知らせする (**commit しない**).

    通知本文は「代表コース」を 1 つ選んで週/コース/拠点欄に使う: 違反が出た患者が
    乗っている訪問のうち、日付→開始時刻が最も早いもののコース。

    **コースが引けなくても通知は必ず出す** (2026-08-17 レビュー M-3):
    患者単位リンクが臨時 / 予定外 visit (course_id NULL) にだけ掛かっていると
    代表コースが取れないが、そこで黙ると「確認して通した」監査記録が欠落する
    (通知はお知らせ兼監査ログ)。``notify_constraint_override`` は course=None を
    受けられるので、その場合は週/コース欄を省いた本文で通知する。

    冪等キー: 同行 PUT には op_group_id が無いため ``reference_id=None`` = 毎回通知。
    None を突き合わせると過去の NULL 通知全部にヒットして以後止まる既知の罠が
    あるため、``notify_constraint_override`` 側が None のとき重複検査をスキップする。
    """
    if not warnings:
        return
    warned_patient_ids = {w.patient_id for w in warnings}
    candidates = [
        v for v in effective if v.patient_id in warned_patient_ids and v.course_id is not None
    ]
    course: Course | None = None
    if candidates:
        candidates.sort(key=lambda v: (v.visit_date, v.start_time, str(v.id)))
        # 未 flush の DELETE/INSERT が autoflush で割り込む順序依存を断つため明示 flush。
        await db.flush()
        course = await db.scalar(select(Course).where(Course.id == candidates[0].course_id))
    await notify_constraint_override(
        db,
        kind_summary={w.kind for w in warnings},
        course=course,
        staff=staff,
        patient_warnings=warnings,
        actor=actor,
        op_group_id=None,
    )


# ---------------------------------------------------------------------------
# GET /accompaniments (週一覧)
# ---------------------------------------------------------------------------


@router.get(
    "/accompaniments",
    response_model=AccompanimentsListResponse,
    summary="その週の同行リンク一覧 (解決済み・全ロール)",
)
async def list_accompaniments(
    db: DbDep,
    _user: CurrentActiveUser,
    iso_year: Annotated[int, Query(ge=2000, le=2100)],
    iso_week: Annotated[int, Query(ge=1, le=53)],
    staff_id: Annotated[UUID | None, Query()] = None,
    trainee_staff_id: Annotated[UUID | None, Query(deprecated=True)] = None,
) -> AccompanimentsListResponse:
    monday, sunday = _week_bounds(iso_year, iso_week)
    return await _build_week_response(
        db,
        iso_year=iso_year,
        iso_week=iso_week,
        monday=monday,
        sunday=sunday,
        staff_id=staff_id or trainee_staff_id,
    )


# ---------------------------------------------------------------------------
# PUT /accompaniments (週一括置換・確定操作)
# ---------------------------------------------------------------------------


@router.put(
    "/accompaniments",
    response_model=AccompanimentsListResponse,
    summary="そのスタッフ×その週の同行リンク一括置換 (admin)",
)
async def put_accompaniments(
    body: AccompanimentsPut,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> AccompanimentsListResponse:
    """1 TX でそのスタッフ×その週のリンクを全置換する (§6.2 / 一般化 §3).

    バリデーション (FE ブロックとの二重防御):
      - スタッフが active でなければ 409 (**新人限定は撤廃**・一般化 §3-1)
      - course_ids / visit_ids の存在・週一致・soft-delete チェック (422)
      - 時間重複 (同行どうし **+ 本人担当**) があれば構造化 422
        (``code='accompaniment_overlap'``・決定#1)
      - NG スタッフ / 性別制限に抵触したら ``code='constraint_confirmation_required'``
        の 422 → ``acknowledge_constraint_warnings:true`` 再送で通過 + 管理者通知
        (決定#4)
    ``kind`` はサーバが ``staff.is_trainee`` から自動判定する (入力では受け取らない)。
    defaults: キー省略/null/[] = 既定に触れない。非空配列のみ含まれた曜日を upsert。
    """
    monday, sunday = _week_bounds(body.iso_year, body.iso_week)
    target_staff_id = body.effective_staff_id
    staff = await _get_staff_or_404(db, target_staff_id)
    _require_accompaniment_eligible(staff)
    kind = resolve_accompaniment_kind(staff)

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

    # ----- 時間重複判定 (確定ブロック 422・決定#1) -----
    # 同行どうし + 本人担当 (primary/secondary/mentor/VSA) を同じ土俵で検査する。
    # override 不可のハードブロックなので、acknowledge で通せる NG/性別確認より**先**。
    effective = await load_effective_visits(db, course_ids=course_ids, visit_ids=visit_ids)
    own_duty = await load_own_duty_visits(db, target_staff_id, monday, sunday)
    conflicts = await collect_accompaniment_conflicts(db, effective=effective, own_duty=own_duty)
    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=accompaniment_overlap_detail(conflicts),
        )

    # ----- NG スタッフ / 性別制限の確認フロー (422 → ack 再送・決定#4) -----
    constraint_warnings = await _collect_accompaniment_constraint_warnings(
        db, staff_id=target_staff_id, effective=effective
    )
    if constraint_warnings and not body.acknowledge_constraint_warnings:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=constraint_confirmation_detail(constraint_warnings),
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

    # ----- 置換 (そのスタッフ×その週のみ) -----
    week_course_ids = select(Course.id).where(
        Course.iso_year == body.iso_year, Course.iso_week == body.iso_week
    )
    await db.execute(
        delete(Accompaniment).where(
            Accompaniment.accompanying_staff_id == target_staff_id,
            Accompaniment.course_id.in_(week_course_ids),
        )
    )
    week_visit_ids = select(Visit.id).where(Visit.visit_date >= monday, Visit.visit_date <= sunday)
    await db.execute(
        delete(Accompaniment).where(
            Accompaniment.accompanying_staff_id == target_staff_id,
            Accompaniment.visit_id.in_(week_visit_ids),
        )
    )

    for cid in course_ids:
        db.add(
            Accompaniment(
                accompanying_staff_id=target_staff_id,
                target_type="course",
                course_id=cid,
                source="manual",
                kind=kind,
                created_by=user.id,
            )
        )
    for vid in visit_ids:
        db.add(
            Accompaniment(
                accompanying_staff_id=target_staff_id,
                target_type="visit",
                visit_id=vid,
                source="manual",
                kind=kind,
                created_by=user.id,
            )
        )

    # ----- defaults の upsert (含まれた曜日のみ) -----
    if touch_defaults:
        for it in body.defaults or []:
            await db.execute(
                delete(AccompanimentDefault).where(
                    AccompanimentDefault.accompanying_staff_id == target_staff_id,
                    AccompanimentDefault.weekday == it.weekday,
                )
            )
            db.add(
                AccompanimentDefault(
                    accompanying_staff_id=target_staff_id,
                    weekday=it.weekday,
                    course_template_id=it.course_template_id,
                    kind=kind,
                    created_by=user.id,
                )
            )

    # §7-3: acknowledge で制約を通した事実を管理者へお知らせする。
    # commit 前に add = リンク登録と同一トランザクション (通ったものだけ通知される)。
    await _notify_accompaniment_override(
        db, staff=staff, warnings=constraint_warnings, effective=effective, actor=user
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
        staff_id=target_staff_id,
    )


# ---------------------------------------------------------------------------
# GET/PUT /accompaniment-defaults
# ---------------------------------------------------------------------------


@router.get(
    "/accompaniment-defaults",
    response_model=list[AccompanimentDefaultRead],
    summary="毎週の既定一覧 (全ロール)",
)
async def list_accompaniment_defaults(
    db: DbDep,
    _user: CurrentActiveUser,
    staff_id: Annotated[UUID | None, Query()] = None,
    trainee_staff_id: Annotated[UUID | None, Query(deprecated=True)] = None,
) -> list[AccompanimentDefaultRead]:
    target = staff_id or trainee_staff_id
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="staff_id (or legacy trainee_staff_id) is required",
        )
    rows = (
        await db.scalars(
            select(AccompanimentDefault)
            .where(AccompanimentDefault.accompanying_staff_id == target)
            .order_by(AccompanimentDefault.weekday)
        )
    ).all()
    return await _defaults_to_read(db, list(rows))


@router.put(
    "/accompaniment-defaults",
    response_model=list[AccompanimentDefaultRead],
    summary="毎週の既定を全置換 (admin)",
)
async def put_accompaniment_defaults(
    body: AccompanimentDefaultsPut,
    db: DbDep,
    user: Annotated[User, Depends(require_role("admin"))],
) -> list[AccompanimentDefaultRead]:
    """当該スタッフの既定を全置換する (曜日×course_template の配列).

    - スタッフが active でなければ 409 (**新人限定は撤廃**・一般化 §3-1)
    - 曜日重複 → 422 / テンプレ不存在 or 削除済み → 422
    - ``kind`` はサーバ自動判定 (入力では受け取らない)
    """
    target_staff_id = body.effective_staff_id
    staff = await _get_staff_or_404(db, target_staff_id)
    _require_accompaniment_eligible(staff)
    kind = resolve_accompaniment_kind(staff)

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
        delete(AccompanimentDefault).where(
            AccompanimentDefault.accompanying_staff_id == target_staff_id
        )
    )
    for it in body.items:
        db.add(
            AccompanimentDefault(
                accompanying_staff_id=target_staff_id,
                weekday=it.weekday,
                course_template_id=it.course_template_id,
                kind=kind,
                created_by=user.id,
            )
        )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflict: duplicate (accompanying_staff_id, weekday)",
        ) from exc

    rows = (
        await db.scalars(
            select(AccompanimentDefault)
            .where(AccompanimentDefault.accompanying_staff_id == target_staff_id)
            .order_by(AccompanimentDefault.weekday)
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
    "/accompaniments/course-guard",
    response_model=CourseGuardResponse,
    summary="新人が今週以降にコース担当として残っているか (全ロール・警告主義 §8-4)",
)
async def accompaniment_course_guard(
    db: DbDep,
    _user: CurrentActiveUser,
    staff_id: Annotated[UUID | None, Query()] = None,
    trainee_staff_id: Annotated[UUID | None, Query(deprecated=True)] = None,
) -> CourseGuardResponse:
    """当該スタッフが今週以降のコース担当 (courses.assigned_staff_id) に残っているか.

    is_trainee を ON にする際の警告表示用。自動解除はしない (警告主義・§8-4)。

    **保存済み ``kind`` でゲートしてはいけない** (2026-08-17 レビュー HIGH):
    このEPの用途は「**これから** is_trainee を ON にする人」への事前警告で、FE は
    保存前のフォーム値で発火する。呼ばれる時点の DB 上のスタッフはまだ一般スタッフ
    (= kind 'support' 相当) なので、kind でゲートすると警告が恒久的に出なくなる
    (機能回帰)。判定はせず常に件数を返し、出すか否かは FE のフォーム状態に委ねる。

    ``applicable`` は互換のため残すが**常に true**。
    """
    target = staff_id or trainee_staff_id
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="staff_id (or legacy trainee_staff_id) is required",
        )

    cur_year, cur_week, _monday = _current_week()
    rows = (
        await db.execute(
            select(Course.id, Course.iso_year, Course.iso_week, Course.weekday, Course.code)
            .where(
                Course.assigned_staff_id == target,
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
        CourseGuardCourse(id=cid, iso_year=iy, iso_week=iw, weekday=wd, code=code)
        for (cid, iy, iw, wd, code) in rows
    ]
    return CourseGuardResponse(
        staff_id=target,
        trainee_staff_id=target,
        applicable=True,
        count=len(courses),
        courses=courses,
    )


# ---------------------------------------------------------------------------
# §7.5: 将来リンク + 既定の一括削除
# ---------------------------------------------------------------------------


@router.delete(
    "/accompaniments/future",
    response_model=AccompanimentFutureDeleteResponse,
    summary="今週以降の同行リンク + 毎週の既定を一括削除 (admin・冪等)",
)
async def delete_future_accompaniments_endpoint(
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
    staff_id: Annotated[UUID | None, Query()] = None,
    trainee_staff_id: Annotated[UUID | None, Query(deprecated=True)] = None,
) -> AccompanimentFutureDeleteResponse:
    """今週以降の週リンク (course/visit) を物理削除し、毎週の既定を全削除する (§7.5).

    is_trainee OFF 操作の確認ダイアログから呼ぶ。過去週のリンクは実績履歴として残す。
    冪等 (対象が無くても 0 件で成功)。実処理は
    ``services/accompaniment.delete_future_accompaniments`` (退職/非 active 化の
    ライフサイクル経路と共有する単一ソース)。
    """
    target = staff_id or trainee_staff_id
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="staff_id (or legacy trainee_staff_id) is required",
        )
    cur_year, cur_week, monday = _current_week()
    deleted_links, deleted_defaults = await delete_future_accompaniments(
        db, staff_id=target, iso_year=cur_year, iso_week=cur_week, monday=monday
    )
    await db.commit()

    return AccompanimentFutureDeleteResponse(
        staff_id=target,
        trainee_staff_id=target,
        deleted_links=deleted_links,
        deleted_defaults=deleted_defaults,
    )


# ---------------------------------------------------------------------------
# 旧パス互換エイリアス (一般化 §3-8 — FE 移行後に削除)
# ---------------------------------------------------------------------------
#
# ``/trainee-accompaniments`` 系を **同一ハンドラ**へ束ねる。新パスと分岐させないため
# 関数を再定義せず、``APIRouter.add_api_route`` で同じ callable を 2 つ目のパスに
# 登録する (実装のズレが構造的に起きない)。``include_in_schema=False`` で OpenAPI
# からは隠し、新パスだけがドキュメント上の契約になるようにする。

_LEGACY_ROUTES: tuple[tuple[str, str, object, object], ...] = (
    ("/trainee-accompaniments", "GET", list_accompaniments, AccompanimentsListResponse),
    ("/trainee-accompaniments", "PUT", put_accompaniments, AccompanimentsListResponse),
    (
        "/trainee-accompaniment-defaults",
        "GET",
        list_accompaniment_defaults,
        list[AccompanimentDefaultRead],
    ),
    (
        "/trainee-accompaniment-defaults",
        "PUT",
        put_accompaniment_defaults,
        list[AccompanimentDefaultRead],
    ),
    (
        "/trainee-accompaniments/course-guard",
        "GET",
        accompaniment_course_guard,
        CourseGuardResponse,
    ),
    (
        "/trainee-accompaniments/future",
        "DELETE",
        delete_future_accompaniments_endpoint,
        AccompanimentFutureDeleteResponse,
    ),
)

for _path, _method, _endpoint, _response_model in _LEGACY_ROUTES:
    router.add_api_route(
        _path,
        _endpoint,  # type: ignore[arg-type]
        methods=[_method],
        response_model=_response_model,
        include_in_schema=False,
        # 明示的なルート名を付ける (レビュー LOW-9)。既定は endpoint 関数名なので、
        # 新パスと旧エイリアスが同名になり ``url_path_for`` が引けなくなる。
        name=f"legacy_{_method.lower()}_{_path.strip('/').replace('/', '_').replace('-', '_')}",
        summary=f"[deprecated] {_path} — /accompaniments 系の互換エイリアス",
    )
