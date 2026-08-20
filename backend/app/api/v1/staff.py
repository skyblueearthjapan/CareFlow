"""Staff CRUD endpoints (Phase 2 domain router / W1-BE2 v2 schema).

Staff role users may only see their own staff record (the one referenced by
their `users.staff_id`); admin/manager see everyone.

W1-BE2 (v2 整理 §4.2): リクエスト / レスポンスの schema (`StaffCreate` /
`StaffUpdate` / `StaffRead`) は v2 9 項目構成に縮約済み。削除カラム
(can_double_team / home_address / home_lat / home_lng / areas /
max_per_day / skill_level / assignment_volume) を payload に含めて送ると
``extra="forbid"`` により 422 で拒否される。状態 (`status`) は
``active`` / ``on_leave`` / ``retired`` の 3 値 enum で厳密化。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff
from app.models.user import User, normalize_user_role
from app.schemas.staff import StaffCreate, StaffRead, StaffUpdate
from app.services.accompaniment import delete_future_accompaniments
from app.services.manager_course_sync import sync_manager_course_templates

router = APIRouter()


async def _purge_future_accompaniments(
    db, staff_id: UUID, *, include_defaults: bool
) -> tuple[int, int]:
    """当該スタッフの今週以降の同行リンク (と任意で既定) を削除する (一般化 §3-7).

    退職 (soft-delete) / 非 active 化の両経路から呼ぶ。実処理は
    ``DELETE /accompaniments/future`` と同じ ``delete_future_accompaniments``
    (単一ソース)。**commit しない** — 呼び出し側の TX 境界に相乗りする。

    ``include_defaults`` の使い分け (2026-08-17 レビュー M-2):
        - **休職 (status 非 active 化) = False**: 復帰前提なので既定は温存する。
          週リンクだけ消せば盤面からは即消え、復帰後の週生成で既定が再展開される。
        - **退職 (論理削除) = True**: もう戻らないので既定ごと畳む。

    「今週」は JST 基準で評価する: 本番 backend コンテナは TZ 未設定 (UTC) のため
    ``date.today()`` だと JST 月曜 00:00-09:00 の窓で前週判定になり、実績履歴として
    残すべき直前週のリンクまで巻き込んで消してしまう (accompaniments.py と同じ罠)。

    返却 = (削除したリンク数, 削除した既定数)。
    """
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    iso = today.isocalendar()
    monday = date.fromisocalendar(iso[0], iso[1], 1)
    return await delete_future_accompaniments(
        db,
        staff_id=staff_id,
        iso_year=iso[0],
        iso_week=iso[1],
        monday=monday,
        include_defaults=include_defaults,
    )


async def _commit_or_409(db) -> None:
    """Commit and translate IntegrityError into 409/422 (see patients.py)."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conflict: duplicate value",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Validation error: invalid foreign key",
        ) from exc


@router.get("", response_model=list[StaffRead], summary="List staff")
async def list_staff(
    db: DbDep,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Staff]:
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    stmt = select(Staff).where(Staff.deleted_at.is_(None))
    if user.role == "staff":
        # Staff can only see their own record.
        if user.staff_id is None:
            return []
        stmt = stmt.where(Staff.id == user.staff_id)
    # 登録ナンバー (code) 昇順で常に固定表示。code 未設定は末尾、同コードは登録順で安定化。
    stmt = (
        stmt.order_by(Staff.code.asc().nulls_last(), Staff.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.scalars(stmt)).all()
    return list(rows)


@router.get("/{staff_id}", response_model=StaffRead, summary="Get staff by id")
async def get_staff(
    staff_id: UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> Staff:
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    if user.role == "staff" and user.staff_id != staff_id:
        # Don't leak existence to non-owners.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return staff


@router.post(
    "",
    response_model=StaffRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create staff",
)
async def create_staff(
    payload: StaffCreate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> Staff:
    data = payload.model_dump()
    # コード空欄 = 自動採番 (S + ゼロ埋め3桁・staff_code.py)。手入力はそのまま尊重。
    if not (data.get("code") or "").strip():
        from app.services.staff_code import generate_next_staff_code

        data["code"] = await generate_next_staff_code(db)
    staff = Staff(**data)
    db.add(staff)
    await db.flush()

    # W16-A-4: manager の追加 → 当該拠点の M 系 course_templates を自動同期
    if staff.role == "manager" and staff.primary_office_id is not None:
        await sync_manager_course_templates(db, office_id=staff.primary_office_id)

    await _commit_or_409(db)
    await db.refresh(staff)
    return staff


@router.patch("/{staff_id}", response_model=StaffRead, summary="Update staff")
async def update_staff(
    staff_id: UUID,
    payload: StaffUpdate,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> StaffRead:
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # W16-A-4: 旧拠点 / 旧 role / 旧 status を保持 (manager 同期判定のため)
    prev_office_id = staff.primary_office_id
    prev_role = staff.role
    prev_status = staff.status

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(staff, field, value)
    await db.flush()

    # W16-A-4: manager 関連の状態変化に応じて M course_templates を sync
    role_changed = prev_role != staff.role
    status_changed = prev_status != staff.status

    office_changed = prev_office_id != staff.primary_office_id
    is_or_was_manager = prev_role == "manager" or staff.role == "manager"
    if is_or_was_manager and (role_changed or status_changed or office_changed):
        sync_targets: set[UUID] = set()
        if prev_office_id is not None and prev_role == "manager":
            sync_targets.add(prev_office_id)
        if staff.primary_office_id is not None and staff.role == "manager":
            sync_targets.add(staff.primary_office_id)
        for office_id in sync_targets:
            await sync_manager_course_templates(db, office_id=office_id)

    # 同行のライフサイクル (一般化 §3-7): active から外れた (休職 / 退職手続き前) 時点で
    # 今週以降の同行**リンク**を消す。PUT /accompaniments の
    # ``_require_accompaniment_eligible`` は**新規登録**しか止められないため、
    # 既に張られたリンクはここで畳まないと盤面に残り続ける。
    # **毎週の既定は温存する** (レビュー M-2): 休職は復帰前提なので、既定まで消すと
    # 復帰時に人手で組み直しになる。既定を畳むのは退職 (論理削除) と is_trainee OFF のみ。
    # active のままの更新 (拠点変更など) では触らない。
    purged_links = 0
    purged_defaults = 0
    if status_changed and staff.status != "active":
        purged_links, purged_defaults = await _purge_future_accompaniments(
            db, staff.id, include_defaults=False
        )

    await _commit_or_409(db)
    await db.refresh(staff)
    # 非破壊追加: 何件畳んだかを返す (FE の表示配線は Phase E)。
    return StaffRead.model_validate(staff, from_attributes=True).model_copy(
        update={
            "purged_accompaniment_links": purged_links,
            "purged_accompaniment_defaults": purged_defaults,
        }
    )


@router.delete(
    "/{staff_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete staff (admin only)",
)
async def delete_staff(
    staff_id: UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    """Soft-delete staff.

    NG スタッフ行 (:class:`PatientNgStaff`) は物理削除する。soft delete では
    FK ON DELETE CASCADE が発火しないため (同住所リンクと同じ既知罠)。
    """
    staff = await db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if staff is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # W16-A-4: 退職対象の拠点を後で sync するため事前に保存
    sync_office_id = staff.primary_office_id if staff.role == "manager" else None
    staff.deleted_at = func.now()

    # NG スタッフ行を物理削除 (soft delete では FK CASCADE が発火しないため).
    await db.execute(delete(PatientNgStaff).where(PatientNgStaff.staff_id == staff_id))

    # 同行のライフサイクル (一般化 §3-7): 退職者を同行者として残すと、出勤予定が
    # 無い人が盤面に「同行」として出続け、現場で誰も来ない事故になる。
    # 今週以降のリンクと毎週の既定を消す (過去週は実績履歴として残す)。
    # **退職は復帰前提ではない**ので既定ごと畳む (休職 = PATCH 経路とはここが違う)。
    await _purge_future_accompaniments(db, staff_id, include_defaults=True)

    await db.flush()

    # W16-A-4: manager の削除 → 当該拠点の M 系 course_templates を自動同期
    if sync_office_id is not None:
        await sync_manager_course_templates(db, office_id=sync_office_id)

    await db.commit()
    return None
