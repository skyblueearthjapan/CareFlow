"""予定のお知らせ (SMS) の API (管理者だけ・機能の旗 ``sms_notify``).

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-1・§5-4 (段階 1 = 土台)。

POST /sms/week-notices/preview   → 週のお知らせの一覧 (送れる方 / 送れない方 / 合計)
POST /sms/week-notices/send      → 送る (batch を作り、応答の後に 1 件ずつ送る)
GET  /sms/batches                → 送った回の一覧
GET  /sms/messages               → 1 通ずつの一覧 (番号は末尾 4 桁だけ)
GET  /sms/messages/{id}          → 1 通の詳細 (本文・番号・状態の経過)

旗 ``sms_notify`` が ON の拠点が 1 つも無ければ 404。管理者でなければ 403。
本文と番号は監査ログに書かない (``app/middleware/audit.py`` が ``/api/v1/sms`` の本文を残さない)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.deps import DbDep, require_role
from app.models.patient import Patient
from app.models.sms import SmsBatch, SmsMessage, SmsStatusEvent
from app.models.user import User
from app.services.contacts import RELATION_LABEL, db_error_label
from app.services.sms import notices

router = APIRouter()

AdminDep = Annotated[User, Depends(require_role("admin"))]

NOT_ENABLED = "予定のお知らせは、まだ使えません（機能の旗 sms_notify が OFF です）"


async def _require_enabled(db) -> None:
    if not await notices.flag_enabled_anywhere(db):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_ENABLED)


def _raise(exc: notices.SmsNoticeError) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------


class WeekNoticePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date


class WeekNoticeSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    patient_ids: list[uuid.UUID] = Field(default_factory=list)
    #: 画面で確かめた通数 (送る行の数)。サーバーで数えた数と違えば 409。
    expected_count: int = Field(ge=0)
    #: 同じ内容を送り済みの方も送る。
    resend: bool = False


class VisitSlot(BaseModel):
    date: date
    start: str


class DestinationOut(BaseModel):
    contact_id: uuid.UUID
    relation: str
    relation_label: str
    phone_last4: str


class SendableRow(BaseModel):
    patient_id: uuid.UUID
    patient_name: str
    surname: str
    state: Literal["new", "already_sent_same", "changed_since_sent"]
    destinations: list[DestinationOut]
    visits: list[VisitSlot]
    body: str
    char_count: int
    segments_est: int
    message_count: int
    est_yen: int


class NotSendableRow(BaseModel):
    patient_id: uuid.UUID
    patient_name: str
    reason: str
    reason_label: str
    visits: list[VisitSlot]


class Totals(BaseModel):
    people: int
    messages: int
    segments: int
    yen: int


class WeekNoticePreview(BaseModel):
    week_start: date
    week_label: str
    within_send_hours: bool
    send_hours: str
    rows: list[SendableRow]
    not_sendable: list[NotSendableRow]
    totals: Totals


class WeekNoticeSendResult(BaseModel):
    batch_id: uuid.UUID
    week_start: date
    patient_count: int
    message_count: int
    segments_est_total: int


class BatchOut(BaseModel):
    id: uuid.UUID
    kind: str
    week_start: date
    created_by_user_id: uuid.UUID | None
    created_by_name: str | None
    created_at: datetime
    patient_count: int
    message_count: int
    segments_est_total: int
    status_counts: dict[str, int]


class BatchList(BaseModel):
    items: list[BatchOut]


class MessageOut(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    kind: str
    week_start: date
    patient_id: uuid.UUID | None
    patient_name: str | None
    relation: str | None
    relation_label: str | None
    phone_last4: str | None
    status: str
    status_raw: str | None
    skip_reason: str | None
    error_code: str | None
    char_count: int
    segments_est: int
    split_num: int | None
    carrier: str | None
    created_by_user_id: uuid.UUID | None
    created_by_name: str | None
    created_at: datetime
    sent_at: datetime | None
    accepted_at: datetime | None
    status_decided_at: datetime | None


class MessageList(BaseModel):
    items: list[MessageOut]
    total: int
    limit: int
    offset: int


class StatusEventOut(BaseModel):
    at: datetime
    status: str
    raw_status: str | None
    detail: str | None


class MessageDetail(MessageOut):
    phone: str | None
    body: str | None
    provider: str | None
    provider_msg_id: str | None
    transaction_id: str | None
    error_message: str | None
    fail_reason_raw: str | None
    not_before: datetime | None
    attempt_count: int
    notice_version: int
    events: list[StatusEventOut]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _slots(row: notices.PreviewRow) -> list[VisitSlot]:
    return [VisitSlot(date=v["date"], start=v["start"]) for v in row.visits_json]


def _user_name(u: User | None) -> str | None:
    if u is None:
        return None
    return u.display_name or u.username or u.email


async def _user_names(db, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str | None]:
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return {}
    users = (await db.scalars(select(User).where(User.id.in_(wanted)))).all()
    return {u.id: _user_name(u) for u in users}


def _message_out(
    m: SmsMessage, patient_name: str | None, names: dict[uuid.UUID, str | None]
) -> dict:
    return {
        "id": m.id,
        "batch_id": m.batch_id,
        "kind": m.kind,
        "week_start": m.week_start,
        "patient_id": m.patient_id,
        "patient_name": patient_name,
        "relation": m.relation,
        "relation_label": RELATION_LABEL.get(m.relation, m.relation) if m.relation else None,
        "phone_last4": notices.mask_phone(m.phone),
        "status": m.status,
        "status_raw": m.status_raw,
        "skip_reason": m.skip_reason,
        "error_code": m.error_code,
        "char_count": m.char_count,
        "segments_est": m.segments_est,
        "split_num": m.split_num,
        "carrier": m.carrier,
        "created_by_user_id": m.created_by_user_id,
        "created_by_name": names.get(m.created_by_user_id) if m.created_by_user_id else None,
        "created_at": notices.aware(m.created_at),
        "sent_at": notices.aware(m.sent_at),
        "accepted_at": notices.aware(m.accepted_at),
        "status_decided_at": notices.aware(m.status_decided_at),
    }


async def _run_send_due() -> None:
    await notices.send_due_messages()


# ---------------------------------------------------------------------------
# 週のお知らせ
# ---------------------------------------------------------------------------


@router.post(
    "/week-notices/preview",
    response_model=WeekNoticePreview,
    summary="週のお知らせの一覧 (admin only・旗 sms_notify)",
)
async def preview_week_notices(
    payload: WeekNoticePreviewRequest, db: DbDep, _actor: AdminDep
) -> WeekNoticePreview:
    await _require_enabled(db)
    settings = get_settings()
    now = notices._now()
    try:
        pv = await notices.build_week_preview(db, payload.week_start, now)
    except notices.SmsNoticeError as exc:
        raise _raise(exc) from None
    return WeekNoticePreview(
        week_start=pv.week_start,
        week_label=pv.week_label,
        within_send_hours=notices.within_send_hours(now, settings.sms_send_hours),
        send_hours=notices.send_hours_label(settings.sms_send_hours),
        rows=[
            SendableRow(
                patient_id=r.patient_id,
                patient_name=r.patient_name,
                surname=r.surname,
                state=r.state,  # type: ignore[arg-type]
                destinations=[
                    DestinationOut(
                        contact_id=d.contact_id,
                        relation=d.relation,
                        relation_label=d.relation_label,
                        phone_last4=notices.mask_phone(d.phone) or "",
                    )
                    for d in r.destinations
                ],
                visits=_slots(r),
                body=r.body,
                char_count=r.char_count,
                segments_est=r.segments_est,
                message_count=r.message_count,
                est_yen=r.est_yen,
            )
            for r in pv.sendable
        ],
        not_sendable=[
            NotSendableRow(
                patient_id=r.patient_id,
                patient_name=r.patient_name,
                reason=r.reason or "",
                reason_label=notices.REASON_LABEL.get(r.reason or "", r.reason or ""),
                visits=_slots(r),
            )
            for r in pv.not_sendable
        ],
        totals=Totals(**pv.totals()),
    )


@router.post(
    "/week-notices/send",
    response_model=WeekNoticeSendResult,
    summary="週のお知らせを送る (admin only・旗 sms_notify)",
)
async def send_week_notices(
    payload: WeekNoticeSendRequest,
    background: BackgroundTasks,
    db: DbDep,
    actor: AdminDep,
) -> WeekNoticeSendResult:
    await _require_enabled(db)
    settings = get_settings()
    now = notices._now()
    if not notices.within_send_hours(now, settings.sms_send_hours):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"お知らせを送れるのは {notices.send_hours_label(settings.sms_send_hours)}（日本時間）です。"
            "時間内にもう一度送ってください",
        )
    try:
        # 本文は画面から受け取らず、ここで作り直す。
        pv = await notices.build_week_preview(db, payload.week_start, now)
    except notices.SmsNoticeError as exc:
        raise _raise(exc) from None
    rows = notices.select_rows_to_send(pv, payload.patient_ids, resend=payload.resend)
    count = sum(r.message_count for r in rows)
    if count != payload.expected_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"送る件数が変わりました（確認 {payload.expected_count} 件・いま {count} 件）。"
            "一覧を開き直してください",
        )
    if count == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "送るお知らせがありません")
    try:
        result = await notices.create_week_batch(
            db, preview=pv, rows=rows, user_id=actor.id, now=now
        )
        await db.commit()
    except IntegrityError as exc:
        # 二重の押下・同時の送信 (二重送信を防ぐ鍵か、お伝えした内容の UNIQUE)。番号は出さない。
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"同じお知らせを同時に送ろうとしました（{db_error_label(exc)}）。一覧を開き直してください",
        ) from None
    background.add_task(_run_send_due)
    return WeekNoticeSendResult(
        batch_id=result.batch.id,
        week_start=result.batch.week_start,
        patient_count=result.batch.patient_count,
        message_count=result.batch.message_count,
        segments_est_total=result.batch.segments_est_total,
    )


# ---------------------------------------------------------------------------
# 記録
# ---------------------------------------------------------------------------


@router.get("/batches", response_model=BatchList, summary="送った回の一覧 (admin only)")
async def list_batches(
    db: DbDep,
    _actor: AdminDep,
    week_start: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> BatchList:
    await _require_enabled(db)
    q = select(SmsBatch).order_by(SmsBatch.created_at.desc()).limit(limit)
    if week_start is not None:
        q = q.where(SmsBatch.week_start == week_start)
    batches = (await db.scalars(q)).all()
    counts: dict[uuid.UUID, dict[str, int]] = {b.id: {} for b in batches}
    if batches:
        for batch_id, st, n in (
            await db.execute(
                select(SmsMessage.batch_id, SmsMessage.status, func.count())
                .where(SmsMessage.batch_id.in_([b.id for b in batches]))
                .group_by(SmsMessage.batch_id, SmsMessage.status)
            )
        ).all():
            counts[batch_id][st] = n
    names = await _user_names(db, {b.created_by_user_id for b in batches})
    return BatchList(
        items=[
            BatchOut(
                id=b.id,
                kind=b.kind,
                week_start=b.week_start,
                created_by_user_id=b.created_by_user_id,
                created_by_name=names.get(b.created_by_user_id) if b.created_by_user_id else None,
                created_at=notices.aware(b.created_at),  # type: ignore[arg-type]
                patient_count=b.patient_count,
                message_count=b.message_count,
                segments_est_total=b.segments_est_total,
                status_counts=counts[b.id],
            )
            for b in batches
        ]
    )


@router.get("/messages", response_model=MessageList, summary="1 通ずつの一覧 (admin only)")
async def list_messages(
    db: DbDep,
    _actor: AdminDep,
    date_from: Annotated[date | None, Query(description="作った日 (日本時間) の始まり")] = None,
    date_to: Annotated[
        date | None, Query(description="作った日 (日本時間) の終わり (含む)")
    ] = None,
    patient_id: uuid.UUID | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    kind: str | None = None,
    carrier: str | None = None,
    batch_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MessageList:
    await _require_enabled(db)
    conds = []
    if date_from is not None:
        conds.append(
            SmsMessage.created_at
            >= datetime.combine(date_from, time(0), tzinfo=notices.JST).astimezone(UTC)
        )
    if date_to is not None:
        conds.append(
            SmsMessage.created_at
            < datetime.combine(date_to + timedelta(days=1), time(0), tzinfo=notices.JST).astimezone(
                UTC
            )
        )
    if patient_id is not None:
        conds.append(SmsMessage.patient_id == patient_id)
    if status_:
        conds.append(SmsMessage.status == status_)
    if kind:
        conds.append(SmsMessage.kind == kind)
    if carrier:
        conds.append(SmsMessage.carrier == carrier)
    if batch_id is not None:
        conds.append(SmsMessage.batch_id == batch_id)

    total = await db.scalar(select(func.count()).select_from(SmsMessage).where(*conds)) or 0
    rows = (
        await db.execute(
            select(SmsMessage, Patient.name)
            .outerjoin(Patient, Patient.id == SmsMessage.patient_id)
            .where(*conds)
            .order_by(SmsMessage.created_at.desc(), SmsMessage.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    names = await _user_names(db, {m.created_by_user_id for m, _ in rows})
    return MessageList(
        items=[MessageOut(**_message_out(m, pname, names)) for m, pname in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/messages/{message_id}", response_model=MessageDetail, summary="1 通の詳細 (admin only)"
)
async def get_message(message_id: uuid.UUID, db: DbDep, _actor: AdminDep) -> MessageDetail:
    await _require_enabled(db)
    m = await db.get(SmsMessage, message_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "お知らせが見つかりません")
    pname = (
        await db.scalar(select(Patient.name).where(Patient.id == m.patient_id))
        if m.patient_id
        else None
    )
    names = await _user_names(db, {m.created_by_user_id})
    events = (
        await db.scalars(
            select(SmsStatusEvent)
            .where(SmsStatusEvent.message_id == m.id)
            .order_by(SmsStatusEvent.at, SmsStatusEvent.id)
        )
    ).all()
    return MessageDetail(
        **_message_out(m, pname, names),
        phone=m.phone,
        body=m.body,
        provider=m.provider,
        provider_msg_id=m.provider_msg_id,
        transaction_id=m.transaction_id,
        error_message=m.error_message,
        fail_reason_raw=m.fail_reason_raw,
        not_before=notices.aware(m.not_before),
        attempt_count=m.attempt_count,
        notice_version=m.notice_version,
        events=[
            StatusEventOut(
                at=notices.aware(e.at), status=e.status, raw_status=e.raw_status, detail=e.detail
            )  # type: ignore[arg-type]
            for e in events
        ],
    )
