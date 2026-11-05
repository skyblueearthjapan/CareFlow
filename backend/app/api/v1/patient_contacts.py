"""患者様の連絡先 (電話番号・メール) の API (管理者だけ).

正典: ``docs/plans/contacts-phone-email-design-2026-11-05.md`` §5。

GET /patients/contacts/summary        → 患者ごとの件数 (一覧の「お知らせ」列)
GET /patients/{patient_id}/contacts   → 連絡先 (sort_order 順)
PUT /patients/{patient_id}/contacts   → 全部置きかえ (+ 自動で移したときの知らせ)

``/contacts/summary`` が ``/{patient_id}`` に食われないよう、``api/v1/__init__.py`` で
``patients.router`` より前に登録する。このファイルの中でも summary を先に定義する。

監査ログ (audit_logs) には件数と変わった欄名だけを書き、番号・メールそのものは書かない。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.patient import Patient
from app.models.patient_contact import PatientContact
from app.models.user import User
from app.schemas.patient_contact import (
    ContactList,
    ContactRead,
    ContactsPut,
    ContactSummary,
    ContactSummaryItem,
)
from app.services.contacts import (
    MAX_PATIENT_CONTACTS,
    ContactValueError,
    build_contact_row,
    changed_field_names,
    db_error_label,
    effective_channel,
    is_carrier_email,
    load_patient_contacts,
    normalize_patient_contacts,
    previous_from_models,
    replace_patient_contacts,
    sms_capable_for_kind,
)

logger = logging.getLogger(__name__)

router = APIRouter()

ACTION_CONTACTS_UPDATE = "patient_contacts_update"


def contact_to_read(c: PatientContact) -> ContactRead:
    return ContactRead(
        id=c.id,
        sort_order=c.sort_order,
        relation=c.relation,  # type: ignore[arg-type]
        name=c.name,
        relation_note=c.relation_note,
        phone=c.phone,
        phone_display=c.phone_display,
        phone_kind=c.phone_kind,
        sms_capable=sms_capable_for_kind(c.phone_kind),
        email=c.email,
        email_carrier=is_carrier_email(c.email),
        is_notify_target=c.is_notify_target,
        notify_channel=c.notify_channel,  # type: ignore[arg-type]
        effective_channel=effective_channel(c.notify_channel, c.phone_kind, c.email),  # type: ignore[arg-type]
        consent_status=c.consent_status,  # type: ignore[arg-type]
        consent_on=c.consent_on,
    )


async def _require_active_patient(db, patient_id: UUID) -> Patient:
    patient = await db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


@router.get(
    "/contacts/summary",
    response_model=ContactSummary,
    summary="患者ごとの連絡先の件数 (admin only)",
)
async def contacts_summary(
    db: DbDep,
    _actor: Annotated[User, Depends(require_role("admin"))],
) -> ContactSummary:
    """連絡先が 1 件以上ある、削除されていない患者だけを返す (無い患者は items に出ない)."""
    rows = (
        await db.scalars(
            select(PatientContact)
            .join(Patient, Patient.id == PatientContact.patient_id)
            .where(Patient.deleted_at.is_(None))
            .order_by(PatientContact.patient_id, PatientContact.sort_order)
        )
    ).all()
    by_patient: dict[UUID, list[PatientContact]] = defaultdict(list)
    for c in rows:
        by_patient[c.patient_id].append(c)

    items: list[ContactSummaryItem] = []
    for patient_id, contacts in by_patient.items():
        targets = [c for c in contacts if c.is_notify_target]
        channels = [effective_channel(c.notify_channel, c.phone_kind, c.email) for c in targets]
        confirmed = [
            ch for c, ch in zip(targets, channels, strict=True) if c.consent_status == "confirmed"
        ]
        items.append(
            ContactSummaryItem(
                patient_id=patient_id,
                contact_count=len(contacts),
                targets=len(targets),
                sms=confirmed.count("sms"),
                email=confirmed.count("email"),
                no_means=channels.count("none"),
                pending_consent=sum(1 for c in targets if c.consent_status == "unconfirmed"),
                ng=sum(1 for c in contacts if c.consent_status == "ng"),
            )
        )
    return ContactSummary(items=items)


@router.get(
    "/{patient_id}/contacts",
    response_model=ContactList,
    summary="患者の連絡先 (admin only)",
)
async def get_patient_contacts(
    patient_id: UUID,
    db: DbDep,
    _actor: Annotated[User, Depends(require_role("admin"))],
) -> ContactList:
    await _require_active_patient(db, patient_id)
    contacts = await load_patient_contacts(db, patient_id)
    return ContactList(contacts=[contact_to_read(c) for c in contacts])


@router.put(
    "/{patient_id}/contacts",
    response_model=ContactList,
    summary="患者の連絡先を全部置きかえる (admin only)",
)
async def put_patient_contacts(
    patient_id: UUID,
    payload: ContactsPut,
    db: DbDep,
    actor: Annotated[User, Depends(require_role("admin"))],
) -> ContactList:
    await _require_active_patient(db, patient_id)

    if len(payload.contacts) > MAX_PATIENT_CONTACTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"連絡先は {MAX_PATIENT_CONTACTS} 件までです",
        )
    if sum(1 for c in payload.contacts if c.relation == "self") > 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ご本人の連絡先は 1 件までです",
        )

    existing = await load_patient_contacts(db, patient_id)
    existing_ids = {c.id for c in existing}

    rows = []
    for idx, c in enumerate(payload.contacts, start=1):
        try:
            rows.append(
                build_contact_row(
                    # ほかの患者の id や知らない id は新しい行として扱う。
                    id=c.id if c.id in existing_ids else None,
                    relation=c.relation,
                    name=c.name,
                    relation_note=c.relation_note,
                    phone=c.phone,
                    email=c.email,
                    is_notify_target=c.is_notify_target,
                    notify_channel=c.notify_channel,
                    consent_status=c.consent_status,
                )
            )
        except ContactValueError as exc:
            label = "メールアドレス" if exc.kind == "email" else "電話番号"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{idx} 件目の{label}：{exc.message}",
            ) from exc

    result = normalize_patient_contacts(rows, previous_from_models(existing))
    changed = changed_field_names(existing, result.rows)
    before_count = len(existing)

    try:
        saved = await replace_patient_contacts(
            db, patient_id, result.rows, source="manual", existing=existing
        )
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                action=ACTION_CONTACTS_UPDATE,
                target_table="patient_contacts",
                target_id=str(patient_id),
                # 番号・メールそのものは書かない (件数と変わった欄名だけ)。
                before={"count": before_count},
                after={"count": len(saved), "changed_fields": changed},
            )
        )
        await db.commit()
    except IntegrityError as exc:
        # 同時に保存されたなど。例外の文には番号・メールが入るので、型と制約名だけを残す。
        await db.rollback()
        logger.warning("patient_contacts put IntegrityError: %s", db_error_label(exc))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="連絡先を保存できませんでした（同時に保存された可能性があります）。もう一度保存してください",
        ) from None
    contacts = await load_patient_contacts(db, patient_id)
    return ContactList(contacts=[contact_to_read(c) for c in contacts], notices=result.notices)
