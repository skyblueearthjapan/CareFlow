"""Patient same-address link endpoints (Phase G-21 T2).

POST   /patient-same-address-links            (UPSERT semantics)
DELETE /patient-same-address-links/{a}/{b}    (= preferred に戻す)
GET    /patient-same-address-links[?patient_id=X]

RBAC:
  POST   — admin / manager
  DELETE — admin / manager
  GET    — admin / manager / staff (read-only)

設計メモ
--------
* ``pair_mode == "preferred"`` は **DB に保存しない** (= 行無し = preferred 扱い).
  既存行があれば DELETE する.
* ``(patient_a_id, patient_b_id)`` は schema 側 model_validator で ``a < b`` に
  正規化済. ただし防御のため endpoint 側でも sort し直す.

audit_logs.target_id の長さ制約 (String(64)) について
-----------------------------------------------------
* UUID 1 個 = 36 文字. ``"{a}:{b}"`` (= 73 文字) は overflow するため、
  ``target_id = str(a)``、相手 ``b`` は before/after JSONB の ``peer_id`` に格納する.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.patient_same_address_link import PatientSameAddressLink
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.patient_same_address_link import (
    PatientSameAddressLinkCreate,
    PatientSameAddressLinkRead,
)

router = APIRouter()


def _normalize_pair(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    """``a < b`` (UUID 順) に揃える. ``a == b`` は 422."""
    if a == b:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="patient_a_id と patient_b_id は同じにできません",
        )
    if a > b:
        return b, a
    return a, b


def _staff_patient_ids_subquery(staff_id: UUID):
    """Staff が担当する patient_id を返す subquery (= patients.py と同一ロジック).

    visits.primary/secondary/mentor または visit_staff_assignments に行があれば担当.
    """
    assigned_visit_ids = select(VisitStaffAssignment.visit_id).where(
        VisitStaffAssignment.staff_id == staff_id
    )
    return (
        select(Visit.patient_id)
        .where(
            Visit.deleted_at.is_(None),
            or_(
                Visit.primary_staff_id == staff_id,
                Visit.secondary_staff_id == staff_id,
                Visit.mentor_staff_id == staff_id,
                Visit.id.in_(assigned_visit_ids),
            ),
        )
        .distinct()
    )


@router.post(
    "",
    response_model=PatientSameAddressLinkRead | None,
    summary="Phase G-21: 同住所紐付け UPSERT (preferred なら DELETE)",
)
async def create_link(
    payload: PatientSameAddressLinkCreate,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin", "manager"))],
) -> PatientSameAddressLinkRead | None:
    """同住所リンクを UPSERT.

    * 既存 (a, b) があれば ``pair_mode`` / ``note`` / ``decided_by_user_id`` を上書き.
    * ``pair_mode == "preferred"`` が指定された場合は DB から該当行を DELETE する
      (= 行無し = preferred のデフォルトに戻す). 返却は ``null``.

    audit_logs.target_id (String(64)) の制約上、UUID 2 個 (= 73 文字) は格納できない
    ため ``target_id = str(a)`` とし、相手 ``b`` は before/after JSONB に
    ``peer_id`` として格納する.
    """
    a, b = _normalize_pair(payload.patient_a_id, payload.patient_b_id)

    existing = await db.scalar(
        select(PatientSameAddressLink).where(
            PatientSameAddressLink.patient_a_id == a,
            PatientSameAddressLink.patient_b_id == b,
        )
    )

    if payload.pair_mode == "preferred":
        # preferred は DB に保持しない. 既存行があれば DELETE. 行が無い場合も
        # 「preferred を再宣言した」操作として audit_log は残す (= 監査痕跡確保).
        if existing is not None:
            before = {
                "peer_id": str(b),
                "pair_mode": existing.pair_mode,
                "note": existing.note,
            }
            await db.delete(existing)
        else:
            before = None
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                action="patient_same_address_link_set",
                target_table="patient_same_address_links",
                target_id=str(a),
                before=before,
                after={"peer_id": str(b), "pair_mode": "preferred"},
            )
        )
        await db.commit()
        return None

    if existing is not None:
        before = {
            "peer_id": str(b),
            "pair_mode": existing.pair_mode,
            "note": existing.note,
        }
        existing.pair_mode = payload.pair_mode
        existing.note = payload.note
        existing.decided_by_user_id = actor.id
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                action="patient_same_address_link_set",
                target_table="patient_same_address_links",
                target_id=str(a),
                before=before,
                after={
                    "peer_id": str(b),
                    "pair_mode": payload.pair_mode,
                    "note": payload.note,
                },
            )
        )
        await db.commit()
        await db.refresh(existing)
        return PatientSameAddressLinkRead.model_validate(existing)

    # 新規作成.
    link = PatientSameAddressLink(
        patient_a_id=a,
        patient_b_id=b,
        pair_mode=payload.pair_mode,
        note=payload.note,
        decided_by_user_id=actor.id,
    )
    db.add(link)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="patient_same_address_link_set",
            target_table="patient_same_address_links",
            target_id=str(a),
            before=None,
            after={
                "peer_id": str(b),
                "pair_mode": payload.pair_mode,
                "note": payload.note,
            },
        )
    )
    await db.commit()
    await db.refresh(link)
    return PatientSameAddressLinkRead.model_validate(link)


@router.delete(
    "/{patient_a_id}/{patient_b_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Phase G-21: 同住所紐付け削除 (= preferred に戻す)",
)
async def delete_link(
    patient_a_id: UUID,
    patient_b_id: UUID,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin", "manager"))],
) -> None:
    """同住所リンクを削除する. 行が無い場合は 204 で冪等 (= 既に preferred).

    URL パスの ``patient_a_id`` / ``patient_b_id`` は a<b で正規化される (順序を
    気にしなくて済む).
    """
    a, b = _normalize_pair(patient_a_id, patient_b_id)

    existing = await db.scalar(
        select(PatientSameAddressLink).where(
            PatientSameAddressLink.patient_a_id == a,
            PatientSameAddressLink.patient_b_id == b,
        )
    )
    if existing is None:
        # 冪等: 行が無いことは preferred (デフォルト) と等価.
        return None

    before = {"peer_id": str(b), "pair_mode": existing.pair_mode, "note": existing.note}
    await db.delete(existing)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="patient_same_address_link_delete",
            target_table="patient_same_address_links",
            target_id=str(a),
            before=before,
            after=None,
        )
    )
    await db.commit()
    return None


@router.get(
    "",
    response_model=list[PatientSameAddressLinkRead],
    summary="Phase G-21: 同住所紐付け一覧 (patient_id 指定で a/b 両方向を取得)",
)
async def list_links(
    db: DbDep,
    user: CurrentActiveUser,
    patient_id: UUID | None = Query(
        default=None, description="指定した patient_id を含む link を a/b 両方向で返す"
    ),
) -> list[PatientSameAddressLinkRead]:
    """同住所リンク一覧を取得 (read-only; audit_log は記録しない).

    ``patient_id`` 未指定なら全件 (= 行数は通常少なめ; リスト要件は後追いで paging
    化検討). 指定時は当該 patient_id を含む link (a 側 / b 側 両方向) を返す.

    Staff は担当患者を含む link のみに絞り込む (= 他患者の link を盗み見できない).
    """
    if user.role not in {"admin", "manager", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    stmt = select(PatientSameAddressLink).order_by(
        PatientSameAddressLink.patient_a_id, PatientSameAddressLink.patient_b_id
    )
    if patient_id is not None:
        stmt = stmt.where(
            or_(
                PatientSameAddressLink.patient_a_id == patient_id,
                PatientSameAddressLink.patient_b_id == patient_id,
            )
        )

    # staff は担当患者を片側に含む link のみ.
    if user.role == "staff":
        if user.staff_id is None:
            return []
        scoped_ids = _staff_patient_ids_subquery(user.staff_id)
        stmt = stmt.where(
            or_(
                PatientSameAddressLink.patient_a_id.in_(scoped_ids),
                PatientSameAddressLink.patient_b_id.in_(scoped_ids),
            )
        )

    rows = (await db.scalars(stmt)).all()
    return [PatientSameAddressLinkRead.model_validate(r) for r in rows]
