"""型の作業場 (template workspace) API — /api/v1/template-ws.

管理者だけ・拠点の feature flag ``template_workspace`` が ON の拠点だけ (既定 OFF).
人 (画面) もらく助エージェントも同じ API で下書きを編集する (actor = human | agent)。
下書きの編集に承認は要らない。マスターへの登録 (commit / week-apply) だけ確認の画面を通す。
変更系は ``expected_version`` を送る (違えば 409)。読む側は ETag / ``/version`` で軽くポーリングできる。
"""

# ruff: noqa: N812, N806, N803, N818, B905

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import DbDep, require_role
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.template_draft import TemplateDraft
from app.models.user import User
from app.services.template_ws import commit as C
from app.services.template_ws import optimise as O
from app.services.template_ws import service as S
from app.services.template_ws.errors import TemplateError

router = APIRouter()
AdminDep = Annotated[User, Depends(require_role("admin"))]

Actor = Literal["human", "agent"]


class _Mut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    actor: Actor = "human"
    reason: str | None = Field(default=None, max_length=500)


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    office_id: UUID | None = None
    all_offices: bool = False  # true = 全拠点の下書き (型の作業場が使える拠点をまとめて)
    name: str
    from_draft_id: UUID | None = None


class DraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    archived: bool | None = None


class MoveBody(_Mut):
    key: str
    course_id: str
    start: str


class WhatifBody(_Mut):
    staff_count: dict[str, int | None] | None = None
    drop_staff: list[str] | None = None
    prefs: dict[str, dict[str, str]] | None = None
    red: Literal["keep", "ignore"] | None = None
    clear: bool = False


class AddPatientBody(_Mut):
    code: str
    days: list[str] | None = None
    win: str | None = None


class RemovePatientBody(_Mut):
    code: str


class OptimiseBody(_Mut):
    weekdays: list[str] | None = None  # None / [] = 全部
    seconds: int = Field(default=20, ge=1, le=60)
    max_changes: int | None = Field(default=None, ge=0)


class HistBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    actor: Actor = "human"


class CommitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    confirm: bool = False


class WeekApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iso_year: int = Field(ge=2020, le=2100)
    iso_week: int = Field(ge=1, le=53)
    week_hash: str
    expected_version: int | None = None
    confirm: bool = False


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------


async def _enabled_office_ids(db) -> list[UUID]:
    rows = (await db.scalars(select(OfficeFeatureFlag).where(
        OfficeFeatureFlag.feature_key == S.FEATURE_KEY, OfficeFeatureFlag.enabled_at.is_not(None)))).all()
    return [r.office_id for r in rows]


async def _draft(db, draft_id: UUID, *, for_update: bool = False) -> TemplateDraft:
    try:
        row = await S.get_draft(db, draft_id, for_update=for_update)
    except S.TemplateNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    oids = S.office_ids_of(row)
    ok = {str(o) for o in await _enabled_office_ids(db)}
    if not oids or not set(oids) <= ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "型の作業場はこの拠点ではまだ使えません")
    return row


def _summary_json(r: TemplateDraft) -> dict:
    return {"id": str(r.id), "name": r.name, "status": r.status, "version": r.version,
            "office_id": str(r.office_id) if r.office_id else None, "all_offices": r.office_id is None,
            "offices": S.office_ids_of(r),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "totals": (r.metrics or {}).get("totals")}


async def _guard(db, fn):
    """サービスの例外を HTTP に直す。"""
    try:
        return await fn()
    except TemplateError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except S.TemplateConflict as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except S.TemplateNotFound as e:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


def _etag(row: TemplateDraft) -> str:
    return f'W/"{row.id}-{row.version}"'


async def _after_mutation(db, row: TemplateDraft) -> dict:
    await db.commit()
    await db.refresh(row)
    return await S.build_view(db, row)


# ---------------------------------------------------------------------------
# 一覧・作る・読む
# ---------------------------------------------------------------------------


@router.get("/availability", summary="型の作業場を使える拠点 (feature flag ON)")
async def availability(db: DbDep, _u: AdminDep):
    return {"office_ids": [str(o) for o in await _enabled_office_ids(db)]}


@router.get("/drafts", summary="下書きの一覧")
async def list_drafts(db: DbDep, _u: AdminDep, office_id: UUID | None = None, archived: bool = False,
                      all_offices: bool = False):
    """office_id = その拠点の下書き / all_offices=true = 全拠点の下書き / どちらも無し = 全部 (エージェント用)。"""
    ok = {str(o) for o in await _enabled_office_ids(db)}
    if office_id and str(office_id) not in ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "型の作業場はこの拠点ではまだ使えません")
    rows = [r for r in await S.list_drafts(db, office_id, archived, all_offices=all_offices)
            if S.office_ids_of(r) and set(S.office_ids_of(r)) <= ok]
    return [_summary_json(r) for r in rows]


@router.post("/drafts", status_code=status.HTTP_201_CREATED, summary="いまの型（マスター）から下書きを作る")
async def create_draft(body: DraftCreate, db: DbDep, user: AdminDep):
    offices: list[UUID] | None = None
    if body.all_offices:
        offices = await _enabled_office_ids(db)
        if not offices:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "型の作業場が使える拠点がありません")
    elif body.office_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "拠点を選ぶか、全拠点にしてください")
    elif not await S.flag_enabled(db, body.office_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "型の作業場はこの拠点ではまだ使えません")

    async def go():
        row = await S.create_draft(db, user, None if body.all_offices else body.office_id, body.name,
                                   body.from_draft_id, offices=offices)
        await db.commit()
        await db.refresh(row)
        return await S.build_view(db, row)

    return await _guard(db, go)


@router.get("/drafts/{draft_id}", summary="下書き (枠・数・履歴)")
async def get_draft(draft_id: UUID, request: Request, response: Response, db: DbDep, _u: AdminDep,
                    compare_with: UUID | None = None):
    row = await _draft(db, draft_id)
    tag = _etag(row)
    if compare_with is None and request.headers.get("if-none-match") == tag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": tag})
    response.headers["ETag"] = tag
    return await _guard(db, lambda: S.build_view(db, row, compare_with))


@router.get("/drafts/{draft_id}/version", summary="版だけ (3 秒ポーリング用)")
async def get_version(draft_id: UUID, db: DbDep, _u: AdminDep):
    row = await _draft(db, draft_id)
    steps = await S.list_steps(db, row.id, 1)
    return {"version": row.version, "status": row.status, "latest_seq": steps[0].seq if steps else 0,
            "latest_actor": steps[0].actor if steps else None,
            "running": any(j["draft_id"] == str(row.id) and j["status"] in ("queued", "running") for j in O.JOBS.values())}


@router.patch("/drafts/{draft_id}", summary="名前を変える / しまう")
async def patch_draft(draft_id: UUID, body: DraftPatch, db: DbDep, _u: AdminDep):
    row = await _draft(db, draft_id)

    async def go():
        if body.name is not None:
            await S.rename(db, row, body.name)
        if body.archived is not None:
            await S.set_archived(db, row, body.archived)
        await db.commit()
        await db.refresh(row)
        return {"id": str(row.id), "name": row.name, "status": row.status, "version": row.version}

    return await _guard(db, go)


@router.get("/drafts/{draft_id}/steps", summary="編集の履歴")
async def get_steps(draft_id: UUID, db: DbDep, _u: AdminDep, limit: int = Query(default=50, ge=1, le=200)):
    row = await _draft(db, draft_id)
    return [S.step_json(s) for s in await S.list_steps(db, row.id, limit)]


@router.get("/drafts/{draft_id}/report", summary="最適化の報告（いちばん新しい最適化。seq でその手順の報告）")
async def get_report(draft_id: UUID, db: DbDep, _u: AdminDep, seq: int | None = Query(default=None, ge=1)):
    row = await _draft(db, draft_id)
    return await _guard(db, lambda: S.step_report(db, row, seq))


# ---------------------------------------------------------------------------
# 編集 (人もエージェントも同じ)
# ---------------------------------------------------------------------------


@router.post("/drafts/{draft_id}/move", summary="1 枠を動かす（コース・時刻。曜日は動かさない）")
async def move(draft_id: UUID, body: MoveBody, db: DbDep, user: AdminDep):
    row = await _draft(db, draft_id, for_update=True)

    async def go():
        res = await S.move_slot(db, row, user, actor=body.actor, key=body.key, course_id=body.course_id,
                                start=body.start, reason=body.reason, expected_version=body.expected_version)
        view = await _after_mutation(db, row)
        return {"view": view, "new_issues": res["check"]["new_issues"], "travel": res["check"]["travel"]}

    return await _guard(db, go)


@router.post("/drafts/{draft_id}/whatif", summary="もしもの条件（職員の人数・職員を外す・希望の仮置き）")
async def whatif(draft_id: UUID, body: WhatifBody, db: DbDep, user: AdminDep):
    row = await _draft(db, draft_id, for_update=True)
    patch = body.model_dump(exclude={"expected_version", "actor", "reason"})

    async def go():
        await S.update_whatif(db, row, user, actor=body.actor, patch=patch, reason=body.reason,
                              expected_version=body.expected_version)
        return {"view": await _after_mutation(db, row)}

    return await _guard(db, go)


@router.post("/drafts/{draft_id}/add-patient", summary="まだ枠の無い方を足す")
async def add_patient(draft_id: UUID, body: AddPatientBody, db: DbDep, user: AdminDep):
    row = await _draft(db, draft_id, for_update=True)

    async def go():
        await S.add_patient(db, row, user, actor=body.actor, code=body.code, days=body.days, win=body.win,
                            reason=body.reason, expected_version=body.expected_version)
        return {"view": await _after_mutation(db, row)}

    return await _guard(db, go)


@router.post("/drafts/{draft_id}/remove-patient", summary="足した方を外す")
async def remove_patient(draft_id: UUID, body: RemovePatientBody, db: DbDep, user: AdminDep):
    row = await _draft(db, draft_id, for_update=True)

    async def go():
        await S.remove_patient(db, row, user, actor=body.actor, code=body.code, reason=body.reason,
                               expected_version=body.expected_version)
        return {"view": await _after_mutation(db, row)}

    return await _guard(db, go)


@router.post("/drafts/{draft_id}/undo", summary="一つ前に戻す（下書きの中だけ）")
async def undo(draft_id: UUID, body: HistBody, db: DbDep, user: AdminDep):
    row = await _draft(db, draft_id, for_update=True)

    async def go():
        await S.undo(db, row, user, actor=body.actor, expected_version=body.expected_version)
        return {"view": await _after_mutation(db, row)}

    return await _guard(db, go)


@router.post("/drafts/{draft_id}/redo", summary="やり直す（下書きの中だけ）")
async def redo(draft_id: UUID, body: HistBody, db: DbDep, user: AdminDep):
    row = await _draft(db, draft_id, for_update=True)

    async def go():
        await S.redo(db, row, user, actor=body.actor, expected_version=body.expected_version)
        return {"view": await _after_mutation(db, row)}

    return await _guard(db, go)


@router.post("/drafts/{draft_id}/optimise", status_code=status.HTTP_202_ACCEPTED,
             summary="最適化を始める（曜日 / 全部）。結果は /jobs/{id} をポーリング")
async def optimise(draft_id: UUID, body: OptimiseBody, db: DbDep, user: AdminDep):
    row = await _draft(db, draft_id)

    async def go():
        S.check_version(row, body.expected_version)
        from app.services.template_ws import problems as P

        wds = [P.day_index(d) for d in body.weekdays] if body.weekdays else None
        job = O.start_job(row.id, user.id, actor=body.actor, weekdays=wds, seconds=body.seconds, reason=body.reason,
                          base_version=body.expected_version, max_changes=body.max_changes)
        return job

    return await _guard(db, go)


@router.get("/jobs/{job_id}", summary="最適化ジョブの状態")
async def get_job(job_id: str, _u: AdminDep):
    job = O.JOBS.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ジョブが見つかりません（サーバーが再起動したかもしれません）")
    return job


# ---------------------------------------------------------------------------
# 見比べ・前提・登録
# ---------------------------------------------------------------------------


@router.get("/drafts/{draft_id}/compare", summary="見比べ（相手 = master か下書き ID）")
async def compare(draft_id: UUID, db: DbDep, _u: AdminDep, other: str = "master"):
    row = await _draft(db, draft_id)
    oid = None if other == "master" else UUID(other)
    return await _guard(db, lambda: S.compare(db, row, oid))


@router.get("/drafts/{draft_id}/inputs", summary="最適化が使う前提（距離・職員の勤務/休み/予定・時刻の決まり方）")
async def inputs(draft_id: UUID, db: DbDep, _u: AdminDep, weekday: int = Query(ge=0, le=5),
                 office_id: str | None = None):
    row = await _draft(db, draft_id)
    return await _guard(db, lambda: S.inputs_view(db, row, weekday, office_id))


@router.get("/drafts/{draft_id}/commit-preview", summary="①型に登録: 変わるものの一覧（書かない）")
async def commit_preview(draft_id: UUID, db: DbDep, _u: AdminDep):
    row = await _draft(db, draft_id)
    return await _guard(db, lambda: C.commit_preview(db, row))


@router.post("/drafts/{draft_id}/commit", summary="①固定訪問（型）に登録する（次に週を生成した週から）")
async def commit(draft_id: UUID, body: CommitBody, db: DbDep, user: AdminDep):
    if not body.confirm:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "確認のうえ confirm=true で送ってください")
    row = await _draft(db, draft_id, for_update=True)
    return await _guard(db, lambda: C.commit_to_master(db, row, user, expected_version=body.expected_version))


@router.get("/drafts/{draft_id}/week-preview", summary="②この週のコースに入れる: 件数と理由（書かない）")
async def week_preview(draft_id: UUID, db: DbDep, _u: AdminDep, iso_year: int = Query(ge=2020, le=2100),
                       iso_week: int = Query(ge=1, le=53)):
    row = await _draft(db, draft_id)
    return await _guard(db, lambda: C.week_preview(db, row, iso_year, iso_week))


@router.post("/drafts/{draft_id}/week-apply", summary="②この週のコースに入れる（型は変えない・戻るで戻せる）")
async def week_apply(draft_id: UUID, body: WeekApplyBody, db: DbDep, user: AdminDep):
    if not body.confirm:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "確認のうえ confirm=true で送ってください")
    row = await _draft(db, draft_id, for_update=True)
    return await _guard(db, lambda: C.week_apply(
        db, row, user, iso_year=body.iso_year, iso_week=body.iso_week, expected_week_hash=body.week_hash,
        expected_version=body.expected_version))
