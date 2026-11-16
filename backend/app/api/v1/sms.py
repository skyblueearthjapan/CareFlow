"""予定のお知らせ (SMS) の API (管理者だけ・機能の旗 ``sms_notify``).

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-1・§5-4 (段階 1 = 土台)。

POST /sms/week-notices/preview   → 週のお知らせの一覧 (送れる方 / 送れない方 / 合計)
POST /sms/week-notices/send      → 送る (batch を作り、応答の後に 1 件ずつ送る)
GET  /sms/batches                → 送った回の一覧
GET  /sms/messages               → 1 通ずつの一覧 (番号は末尾 4 桁だけ)
GET  /sms/messages/{id}          → 1 通の詳細 (本文・番号・状態の経過)

段階 2 (``docs/plans/sms-notify-api-contract-2026-11-16.md``):
GET  /sms/status                  → 週の画面のボタンと印 (旗 OFF でも 200・enabled:false)
GET  /sms/changes/pending         → お知らせ待ちの方 (文面・送り先・お電話の番号)
POST /sms/changes/send            → 変更のお知らせを送る
POST /sms/changes/resolve         → 送らずに「お伝え済み」にする
POST /sms/changes/detect/run      → cron: 変更の検知とベル
POST /sms/send-due/run            → cron: 送る時刻の来た分を送る
POST /sms/status/poll/run         → cron: 届いたかの照会
GET  /sms/summary                 → 月のまとめ
GET  /sms/summary/export          → 月の 1 通ごとの一覧 (Excel・本文なし)
GET  /sms/patients/summary        → 患者様ごと
GET  /sms/patients/{id}/recent    → 患者様の直近のお知らせ
GET  /sms/numbers/attention       → 気になる番号

見た内容と送る内容を合わせる印 (``token``・``change_token``) は契約 §12。合わなければ 409 で、
だれにも送らない。同じ方へのほかの操作と重なったときも 409 (契約 §13)。
旗 ``sms_notify`` が ON の拠点が 1 つも無ければ 404。管理者でなければ 403。
本文と番号は監査ログに書かない (``app/middleware/audit.py`` が ``/api/v1/sms`` の本文を残さない)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.deps import DbDep, require_role
from app.models.patient import Patient
from app.models.sms import SmsBatch, SmsMessage, SmsStatusEvent
from app.models.user import User
from app.services.contacts import RELATION_LABEL, db_error_label
from app.services.sms import changes as sms_changes
from app.services.sms import composer, delivery, labels, notices, reports
from app.services.sms.provider import provider_ok, provider_setting

logger = logging.getLogger(__name__)

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
    #: 一覧の行の印 ``{"<patient_id>": "<token>"}`` (patient_ids の全員分・契約 §12)。
    tokens: dict[uuid.UUID, str]
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


class LastAttemptOut(BaseModel):
    at: datetime | None
    result: Literal["skipped", "failed", "unknown", "delivery_failed"]
    label: str


class SendableRow(BaseModel):
    patient_id: uuid.UUID
    patient_name: str
    surname: str
    #: 名前に区切りの空白が無く、姓を分けられない (名前の全体を姓として使っている)。
    surname_uncertain: bool
    state: Literal["new", "changed_since_sent", "already_sent_same", "sending", "not_delivered"]
    last_attempt: LastAttemptOut | None
    #: 本文・送り先・訪問の並びの印 (契約 §12)。
    token: str
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
    #: mock (模擬・実際には送っていない) / kddi。まだ送っていなければ null。
    provider: str | None
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


def _undelivered_condition():
    """「届いていないもの」: 届かなかった・分からない・送れなかった (試験の状態のためは除く).

    ``/status`` の ``undelivered_count`` (直近 7 日) と ``/messages?undelivered=true``
    (日の範囲は付けない) で同じ決まりを使う。
    """
    return or_(
        SmsMessage.status.in_(("failed", "unknown")),
        and_(
            SmsMessage.status == "skipped",
            SmsMessage.skip_reason.in_(sorted(labels.UNSENT_SKIP_REASONS)),
        ),
    )


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
        "provider": m.provider,
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
                surname_uncertain=r.surname_uncertain,
                state=r.state,  # type: ignore[arg-type]
                last_attempt=(
                    LastAttemptOut(**r.last_attempt) if r.last_attempt is not None else None
                ),
                token=r.token,
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
    if any(pid not in payload.tokens for pid in payload.patient_ids):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "選んだ方の印（tokens）が足りません。一覧を開き直してください",
        )
    try:
        # 本文は画面から受け取らず、ここで作り直して印を比べる。
        pv = await notices.build_week_preview(db, payload.week_start, now)
        rows = notices.select_rows_to_send(
            pv, payload.patient_ids, payload.tokens, resend=payload.resend
        )
    except notices.SmsNoticeError as exc:
        raise _raise(exc) from None
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
    except notices.SmsNoticeError as exc:
        # 同じ方へのほかの操作 (週・変更・済ませる) と重なった (版が替わっていた)。
        await db.rollback()
        raise _raise(exc) from None
    except IntegrityError as exc:
        # 二重の押下・同時の送信 (二重送信を防ぐ鍵・お伝えした内容・版の UNIQUE)。番号は出さない。
        await db.rollback()
        logger.info("sms week send conflict: %s", db_error_label(exc))
        raise HTTPException(status.HTTP_409_CONFLICT, notices.CONFLICT_MESSAGE) from None
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
    undelivered: Annotated[
        bool,
        Query(
            description=(
                "届いていないものだけ (/status の undelivered_count と同じ決まり・"
                "日の範囲は付けない)"
            )
        ),
    ] = False,
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
        # 「届いていないもの」のように、状態をカンマでつないで複数渡せる (failed,unknown,skipped)。
        wanted = [x.strip() for x in status_.split(",") if x.strip()]
        if len(wanted) == 1:
            conds.append(SmsMessage.status == wanted[0])
        elif wanted:
            conds.append(SmsMessage.status.in_(wanted))
    if kind:
        conds.append(SmsMessage.kind == kind)
    if carrier:
        conds.append(func.lower(SmsMessage.carrier) == carrier.strip().lower())
    if undelivered:
        conds.append(_undelivered_condition())
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


# ---------------------------------------------------------------------------
# 段階 2: 状態・変更のお知らせ
# ---------------------------------------------------------------------------


class SmsStatusOut(BaseModel):
    enabled: bool
    provider: str | None = None
    send_mode: str | None = None
    test_allowlist_count: int | None = None
    within_send_hours: bool | None = None
    send_hours: str | None = None
    pending_count: int | None = None
    urgent_count: int | None = None
    failed_recent_count: int | None = None
    #: 窓口の設定がそろっているか (kddi は接続の値がすべてある・mock は test のときだけ true・
    #: 知らない値は false)。
    provider_ok: bool | None = None
    #: 直近 7 日の、届かなかった・分からない・送れなかった件数 (試験の状態のためは除く)。
    undelivered_count: int | None = None


class SlotOut(BaseModel):
    date: date
    start: str


class ChangeOut(BaseModel):
    kind: Literal["time", "date", "cancel", "add"]
    from_: SlotOut | None = Field(default=None, serialization_alias="from")
    to: SlotOut | None
    label: str


class CallNumberOut(BaseModel):
    relation_label: str
    phone_display: str


class PendingItemOut(BaseModel):
    patient_id: uuid.UUID
    patient_name: str
    week_start: date
    urgent: bool
    detected_at: datetime
    waiting_minutes: int
    changes: list[ChangeOut]
    sendable: bool
    not_sendable_reason: str | None
    not_sendable_label: str | None
    #: 本文・送り先・変更の並びの印 (送れない方は null・契約 §12)。
    token: str | None
    #: 変更の並びだけの印 (全員にある・済ませるときに送る)。
    change_token: str
    destinations: list[DestinationOut]
    call_numbers: list[CallNumberOut]
    body: str
    char_count: int
    segments_est: int
    est_yen: int


class PendingListOut(BaseModel):
    within_send_hours: bool
    send_hours: str
    count: int
    urgent_count: int
    items: list[PendingItemOut]


class ChangeKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    week_start: date
    #: 一覧の行の印 (契約 §12)。
    token: str


class ChangeSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ChangeKey] = Field(default_factory=list)
    #: 画面で確かめた通数 (送る行の数)。サーバーで数えた数と違えば 409。
    expected_count: int = Field(ge=0)


class ChangeSendResult(BaseModel):
    batch_id: uuid.UUID
    patient_count: int
    message_count: int
    segments_est_total: int


class ChangeResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: uuid.UUID
    week_start: date
    #: 見た変更の並びの印 (契約 §12)。今の並びと違えば 409。
    change_token: str
    channel: Literal["phone", "none"]
    reason: Literal["phoned", "patient_request", "other"]
    note: str = Field(default="", max_length=200)


class ChangeResolveResult(BaseModel):
    ok: bool
    version: int


def _outside_hours_message(spec: str) -> str:
    return (
        f"お知らせを送れるのは {notices.send_hours_label(spec)}（日本時間）です。"
        "時間内にもう一度送ってください"
    )


@router.get(
    "/status",
    response_model=SmsStatusOut,
    response_model_exclude_none=True,
    summary="週の画面のボタンと印 (admin only・旗 OFF でも 200 で enabled:false)",
)
async def sms_status(db: DbDep, _actor: AdminDep) -> SmsStatusOut:
    if not await notices.flag_enabled_anywhere(db):
        return SmsStatusOut(enabled=False)
    settings = get_settings()
    now = notices._now()
    pendings = await sms_changes.compute_pending(db, now)
    week_ago = now - timedelta(days=7)
    failed_recent = (
        await db.scalar(
            select(func.count())
            .select_from(SmsMessage)
            .where(SmsMessage.status == "failed", SmsMessage.created_at >= week_ago)
        )
        or 0
    )
    undelivered = (
        await db.scalar(
            select(func.count())
            .select_from(SmsMessage)
            .where(SmsMessage.created_at >= week_ago, _undelivered_condition())
        )
        or 0
    )
    provider = provider_setting(settings)
    return SmsStatusOut(
        enabled=True,
        provider="kddi" if provider == "kddi" else "mock",
        send_mode="live" if notices.is_live_mode(settings) else "test",
        test_allowlist_count=len(notices.parse_allowlist(settings.sms_test_allowlist or "")),
        within_send_hours=notices.within_send_hours(now, settings.sms_send_hours),
        send_hours=notices.send_hours_label(settings.sms_send_hours),
        pending_count=len(pendings),
        urgent_count=sum(1 for p in pendings if p.urgent),
        failed_recent_count=failed_recent,
        provider_ok=provider_ok(settings),
        undelivered_count=undelivered,
    )


def _slot_out(v: dict | None) -> SlotOut | None:
    return SlotOut(date=v["date"], start=v["start"]) if v else None


def _pending_item(v: sms_changes.PendingView, now: datetime) -> PendingItemOut:
    p = v.pending
    return PendingItemOut(
        patient_id=p.notice.patient_id,
        patient_name=p.patient.name,
        week_start=p.notice.week_start,
        urgent=p.urgent,
        detected_at=v.detected_at,
        waiting_minutes=max(0, int((now - v.detected_at).total_seconds() // 60)),
        changes=[
            ChangeOut(
                kind=c["kind"], from_=_slot_out(c["from"]), to=_slot_out(c["to"]), label=c["label"]
            )
            for c in (sms_changes.change_json(x) for x in p.changes)
        ],
        sendable=v.sendable,
        not_sendable_reason=v.reason,
        not_sendable_label=notices.REASON_LABEL.get(v.reason) if v.reason else None,
        token=v.token,
        change_token=p.change_token,
        destinations=[
            DestinationOut(
                contact_id=d.contact_id,
                relation=d.relation,
                relation_label=d.relation_label,
                phone_last4=notices.mask_phone(d.phone) or "",
            )
            for d in v.destinations
        ],
        call_numbers=[CallNumberOut(**c) for c in v.call_numbers],
        body=v.body,
        char_count=v.char_count,
        segments_est=v.segments_est,
        est_yen=v.segments_est * v.message_count * composer.YEN_PER_SEGMENT,
    )


@router.get(
    "/changes/pending",
    response_model=PendingListOut,
    summary="お知らせ待ちの方 (admin only・旗 sms_notify)",
)
async def list_pending_changes(db: DbDep, _actor: AdminDep) -> PendingListOut:
    await _require_enabled(db)
    settings = get_settings()
    now = notices._now()
    pendings = await sms_changes.compute_pending(db, now)
    try:
        views = await sms_changes.build_pending_views(db, pendings, now)
    except notices.SmsNoticeError as exc:
        raise _raise(exc) from None
    return PendingListOut(
        within_send_hours=notices.within_send_hours(now, settings.sms_send_hours),
        send_hours=notices.send_hours_label(settings.sms_send_hours),
        count=len(views),
        urgent_count=sum(1 for v in views if v.pending.urgent),
        items=[_pending_item(v, now) for v in views],
    )


@router.post(
    "/changes/send",
    response_model=ChangeSendResult,
    summary="変更のお知らせを送る (admin only・旗 sms_notify)",
)
async def send_changes(
    payload: ChangeSendRequest,
    background: BackgroundTasks,
    db: DbDep,
    actor: AdminDep,
) -> ChangeSendResult:
    await _require_enabled(db)
    settings = get_settings()
    now = notices._now()
    if not notices.within_send_hours(now, settings.sms_send_hours):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, _outside_hours_message(settings.sms_send_hours)
        )
    wanted = {(i.patient_id, i.week_start): i.token for i in payload.items}
    pendings = [p for p in await sms_changes.compute_pending(db, now) if p.key in wanted]
    try:
        # 本文は画面から受け取らず、ここで作り直して印を比べる。
        views = await sms_changes.build_pending_views(db, pendings, now)
        views = sms_changes.pick_views_to_send(views, wanted)
    except notices.SmsNoticeError as exc:
        raise _raise(exc) from None
    count = sum(v.message_count for v in views)
    if count != payload.expected_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"送る件数が変わりました（確認 {payload.expected_count} 件・いま {count} 件）。"
            "一覧を開き直してください",
        )
    if count == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "送るお知らせがありません")
    try:
        result = await sms_changes.create_change_batch(db, views=views, user_id=actor.id, now=now)
        await db.commit()
    except notices.SmsNoticeError as exc:
        await db.rollback()
        raise _raise(exc) from None
    except IntegrityError as exc:
        # 二重の押下・同時の送信。番号は出さない。
        await db.rollback()
        logger.info("sms change send conflict: %s", db_error_label(exc))
        raise HTTPException(status.HTTP_409_CONFLICT, notices.CONFLICT_MESSAGE) from None
    background.add_task(_run_send_due)
    return ChangeSendResult(
        batch_id=result.batch.id,
        patient_count=result.batch.patient_count,
        message_count=result.batch.message_count,
        segments_est_total=result.batch.segments_est_total,
    )


@router.post(
    "/changes/resolve",
    response_model=ChangeResolveResult,
    summary="送らずに「お伝え済み」にする (admin only・旗 sms_notify)",
)
async def resolve_change(
    payload: ChangeResolveRequest, db: DbDep, actor: AdminDep
) -> ChangeResolveResult:
    await _require_enabled(db)
    note = payload.note.strip()
    if payload.reason not in sms_changes.RESOLVE_REASONS[payload.channel]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "お電話のときは「お電話でお伝えした」、お知らせしないときは"
            "「お申し出」か「その他」を選んでください",
        )
    if payload.reason == "other" and not note:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "「その他」のときは説明を書いてください"
        )
    try:
        version = await sms_changes.resolve_change(
            db,
            patient_id=payload.patient_id,
            week_start=payload.week_start,
            change_token=payload.change_token,
            channel=payload.channel,
            reason=payload.reason,
            note=note or None,
            user_id=actor.id,
            now=notices._now(),
        )
        await db.commit()
    except notices.SmsNoticeError as exc:
        await db.rollback()
        raise _raise(exc) from None
    except IntegrityError as exc:
        await db.rollback()
        logger.info("sms resolve conflict: %s", db_error_label(exc))
        raise HTTPException(status.HTTP_409_CONFLICT, notices.CONFLICT_MESSAGE) from None
    return ChangeResolveResult(ok=True, version=version)


# ---------------------------------------------------------------------------
# cron から呼ぶ口 (管理者のトークン・POST・本文なし)
# 旗が OFF のときは 200 で {"skipped": "disabled"} (cron が赤くならないように)。
# 別の回が動いているときは 200 で {"skipped": "locked"}。
# ---------------------------------------------------------------------------


async def _cron(db, name: str, job) -> dict:
    if not await notices.flag_enabled_anywhere(db):
        return {"skipped": "disabled"}
    try:
        return await job()
    except Exception:  # noqa: BLE001 — cron 向けに失敗を構造化応答へ集約する
        await db.rollback()
        logger.exception("sms %s failed", name)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"sms {name} job failed"
        ) from None


@router.post("/changes/detect/run", summary="cron: 変更の検知とベル (admin only・5 分おき)")
async def run_detect(db: DbDep, _actor: AdminDep) -> dict:
    return await _cron(db, "detect", lambda: sms_changes.run_detect(db, notices._now()))


@router.post("/send-due/run", summary="cron: 送る時刻の来た分を送る (admin only・毎分)")
async def run_send_due(db: DbDep, _actor: AdminDep) -> dict:
    return await _cron(db, "send-due", lambda: delivery.run_send_due(db))


@router.post("/status/poll/run", summary="cron: 届いたかの照会 (admin only・5 分おき)")
async def run_status_poll(db: DbDep, _actor: AdminDep) -> dict:
    return await _cron(db, "status-poll", lambda: delivery.run_poll(db, notices._now()))


# ---------------------------------------------------------------------------
# お知らせの記録 (月のまとめ・患者様ごと・気になる番号)
# ---------------------------------------------------------------------------

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MonthQuery = Annotated[str | None, Query(description="YYYY-MM (日本時間の月・省略はいまの月)")]


def _month(month: str | None) -> reports.MonthRange:
    try:
        return reports.month_range(month, notices._now())
    except notices.SmsNoticeError as exc:
        raise _raise(exc) from None


@router.get("/summary", summary="月のまとめ (admin only・旗 sms_notify)")
async def month_summary(db: DbDep, _actor: AdminDep, month: MonthQuery = None) -> dict:
    await _require_enabled(db)
    return await reports.build_summary(db, _month(month))


@router.get(
    "/summary/export",
    response_class=Response,
    summary="月の 1 通ごとの一覧を Excel で (本文なし・admin only)",
)
async def month_export(db: DbDep, _actor: AdminDep, month: MonthQuery = None) -> Response:
    await _require_enabled(db)
    rng = _month(month)
    content = await reports.build_export(db, rng)
    return Response(
        content=content,
        media_type=EXCEL_MIME,
        headers={"Content-Disposition": f'attachment; filename="sms_notices_{rng.label}.xlsx"'},
    )


@router.get("/patients/summary", summary="患者様ごとのお知らせ (admin only)")
async def patients_summary(db: DbDep, _actor: AdminDep, month: MonthQuery = None) -> dict:
    await _require_enabled(db)
    return await reports.build_patients_summary(db, _month(month))


@router.get("/patients/{patient_id}/recent", summary="患者様の直近のお知らせ (admin only)")
async def patient_recent(
    patient_id: uuid.UUID,
    db: DbDep,
    _actor: AdminDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> dict:
    await _require_enabled(db)
    rows = (
        await db.execute(
            select(SmsMessage, Patient.name)
            .outerjoin(Patient, Patient.id == SmsMessage.patient_id)
            .where(SmsMessage.patient_id == patient_id)
            .order_by(SmsMessage.created_at.desc(), SmsMessage.id)
            .limit(limit)
        )
    ).all()
    names = await _user_names(db, {m.created_by_user_id for m, _ in rows})
    return {
        "items": [
            MessageOut(**_message_out(m, pname, names)).model_dump(mode="json") for m, pname in rows
        ]
    }


@router.get("/numbers/attention", summary="気になる番号 (admin only)")
async def numbers_attention(db: DbDep, _actor: AdminDep) -> dict:
    await _require_enabled(db)
    return await reports.build_attention(db)
