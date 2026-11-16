"""週のお知らせ (SMS) の一覧・送信・送る順番の処理.

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-1・§6・§7。

* :func:`build_week_preview` — 週の一覧 (送れる方 / 送れない方と理由 / 合計)。
* :func:`create_week_batch` — 送る: batch と 1 通ずつの行を作り、「お伝えした内容」
  (``patient_week_notices``) の版を上げる。実際に送るのは :func:`send_due_messages`。
* :func:`send_due_messages` — 送る時刻の来た ``queued`` を 1 件ずつ順に送る
  (毎秒 1 件まで)。応答の後の BackgroundTask と、これから作る cron の両方から呼ぶ。

送る先: ``is_notify_target`` かつ同意「確認済み」かつ使える手段が SMS (携帯の番号) の連絡先。
二重送信の防止: ``sms_messages.dedupe_key`` (種類 + 患者 + 連絡先 + 週 + 版) が UNIQUE。
``sending`` へ移すときは「まだ queued なら」の条件付きで書く (同時に 2 つ動いても 1 回だけ)。
同じ番号へ続けて送るときは ``not_before`` で 600 秒あける (KDDI・au の推奨)。

番号と本文はログに書かない。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.business_profile import BusinessProfile
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient import Patient
from app.models.patient_contact import PatientContact
from app.models.sms import (
    PatientNoticeEvent,
    PatientWeekNotice,
    SmsBatch,
    SmsMessage,
    SmsStatusEvent,
)
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.services.contacts import RELATION_LABEL, effective_channel
from app.services.sms import composer
from app.services.sms.provider import SendResult, get_provider

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
FEATURE_KEY = "sms_notify"

#: 同じ番号へ続けて送るときにあける秒数 (KDDI・au の推奨)。
SAME_NUMBER_GAP_SECONDS = 600
#: 1 件ずつ送るあいだの秒数 (毎秒 1 件まで。KDDI の上限は毎秒 10 件)。試験では 0 にする。
SEND_INTERVAL_SECONDS = 1.0
#: 502/503/504 などで送り直す回数の上限 (これを超えたら failed)。
MAX_ATTEMPTS = 3
#: 送り直すまでの秒数 (回数 × これ)。
RETRY_DELAY_SECONDS = 60
#: 受け付けた後、最初に照会する時刻 (設計 §7-3 の 1 分後。照会の cron は段階 3)。
FIRST_POLL_SECONDS = 60

#: 送れない理由 (画面の文言)。
REASON_LABEL: dict[str, str] = {
    "no_contact": "送り先がありません",
    "consent_unconfirmed": "同意が未確認です",
    "consent_ng": "NG 回答です",
    "not_mobile": "携帯の番号ではありません",
    "too_long": "文面が長すぎます",
}


def _now() -> datetime:
    """いまの時刻 (UTC)。試験で差し替える。"""
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite は時差を落として返すので、時差の無い値は UTC とみなす."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class SmsNoticeError(Exception):
    """API で 4xx にするもの (``status_code`` と日本語の ``message``)."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ---------------------------------------------------------------------------
# 設定・旗
# ---------------------------------------------------------------------------


async def flag_enabled_anywhere(db: AsyncSession) -> bool:
    """``sms_notify`` が ON の拠点が 1 つでもあるか."""
    row = await db.scalar(
        select(OfficeFeatureFlag.id)
        .where(
            OfficeFeatureFlag.feature_key == FEATURE_KEY,
            OfficeFeatureFlag.enabled_at.is_not(None),
        )
        .limit(1)
    )
    return row is not None


def parse_send_hours(spec: str) -> tuple[time, time]:
    """``"08:00-20:00"`` → (8:00, 20:00). 形が違えば既定の 8:00〜20:00."""
    try:
        start_s, end_s = (part.strip() for part in spec.split("-", 1))
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        return time(sh, sm), time(eh, em)
    except (ValueError, AttributeError):
        return time(8, 0), time(20, 0)


def within_send_hours(now: datetime, spec: str) -> bool:
    start, end = parse_send_hours(spec)
    t = now.astimezone(JST).time()
    return start <= t < end


def send_hours_label(spec: str) -> str:
    start, end = parse_send_hours(spec)
    return f"{start.hour}:{start.minute:02d}〜{end.hour}:{end.minute:02d}"


def parse_allowlist(spec: str) -> set[str]:
    return {"".join(ch for ch in part if ch.isdigit()) for part in spec.split(",") if part.strip()}


def mask_phone(phone: str | None) -> str | None:
    """末尾 4 桁だけ (一覧に出す形)."""
    if not phone:
        return None
    return phone[-4:]


# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------


@dataclass
class Destination:
    contact_id: uuid.UUID
    relation: str
    phone: str

    @property
    def relation_label(self) -> str:
        return RELATION_LABEL.get(self.relation, self.relation)


@dataclass
class PreviewRow:
    patient_id: uuid.UUID
    patient_name: str
    surname: str
    slots: list[tuple[date, time]]
    destinations: list[Destination] = field(default_factory=list)
    body: str = ""
    char_count: int = 0
    segments_est: int = 0
    #: new / already_sent_same / changed_since_sent
    state: str = "new"
    #: 送れないときの理由 (None = 送れる)。
    reason: str | None = None

    @property
    def sendable(self) -> bool:
        return self.reason is None

    @property
    def message_count(self) -> int:
        return len(self.destinations)

    @property
    def est_yen(self) -> int:
        return self.segments_est * composer.YEN_PER_SEGMENT * self.message_count

    @property
    def visits_json(self) -> list[dict[str, str]]:
        return composer.slots_to_json(self.slots)


@dataclass
class WeekPreview:
    week_start: date
    today: date
    week_label: str
    rows: list[PreviewRow]

    @property
    def sendable(self) -> list[PreviewRow]:
        return [r for r in self.rows if r.sendable]

    @property
    def not_sendable(self) -> list[PreviewRow]:
        return [r for r in self.rows if not r.sendable]

    def totals(self) -> dict[str, int]:
        """既定で送る方 (送れる・同じ内容を送り済みでない) の合計."""
        rows = [r for r in self.sendable if r.state != "already_sent_same"]
        return {
            "people": len(rows),
            "messages": sum(r.message_count for r in rows),
            "segments": sum(r.segments_est * r.message_count for r in rows),
            "yen": sum(r.est_yen for r in rows),
        }


def classify_destinations(
    contacts: Sequence[PatientContact],
) -> tuple[list[Destination], str | None]:
    """送る先と、送れないときの理由.

    送る先 = ``is_notify_target`` かつ同意「確認済み」かつ使える手段が SMS。
    同じ番号の連絡先が 2 つあっても 1 通にする。理由は次の順で決める:
    送る先の印が無い (NG 回答の連絡先があれば consent_ng・無ければ no_contact) →
    同意が確認済みでない (consent_unconfirmed) → SMS が使えない (not_mobile)。
    """
    targets = [c for c in contacts if c.is_notify_target]
    if not targets:
        if any(c.consent_status == "ng" for c in contacts):
            return [], "consent_ng"
        return [], "no_contact"
    confirmed = [c for c in targets if c.consent_status == "confirmed"]
    if not confirmed:
        if any(c.consent_status == "ng" for c in targets):
            return [], "consent_ng"
        return [], "consent_unconfirmed"
    out: list[Destination] = []
    seen: set[str] = set()
    for c in confirmed:
        if effective_channel(c.notify_channel, c.phone_kind, c.email) != "sms" or not c.phone:
            continue
        if c.phone in seen:
            continue
        seen.add(c.phone)
        out.append(Destination(contact_id=c.id, relation=c.relation, phone=c.phone))
    if not out:
        return [], "not_mobile"
    return out, None


async def load_profile(db: AsyncSession) -> tuple[str, str]:
    """事業所名と電話 (``business_profile``)。どちらかが無ければ 422."""
    row = await db.scalar(select(BusinessProfile).where(BusinessProfile.is_singleton.is_(True)))
    station = (row.station_name or "").strip() if row else ""
    tel = (row.contact_tel or "").strip() if row else ""
    if not station or not tel:
        raise SmsNoticeError(
            422, "事業所の情報（事業所名と電話）を設定してから、お知らせを使ってください"
        )
    return station, tel


def validate_week_start(week_start: date) -> None:
    if week_start.weekday() != 0:
        raise SmsNoticeError(422, "週の始まり（月曜日）の日付を指定してください")


async def _week_slots(
    db: AsyncSession, week_start: date, today: date
) -> dict[uuid.UUID, list[tuple[date, time]]]:
    """週の中で今日 (日本時間) 以降の、取消でない・削除されていない訪問 (打刻だけの予定外は除く)."""
    first = max(week_start, today)
    last = week_start + timedelta(days=6)
    if first > last:
        return {}
    rows = (
        await db.execute(
            select(Visit.patient_id, Visit.visit_date, Visit.start_time).where(
                Visit.visit_date >= first,
                Visit.visit_date <= last,
                Visit.status != VISIT_STATUS_CANCELLED,
                Visit.deleted_at.is_(None),
                Visit.is_unplanned.is_(False),
            )
        )
    ).all()
    out: dict[uuid.UUID, list[tuple[date, time]]] = defaultdict(list)
    for patient_id, d, t in rows:
        out[patient_id].append((d, t))
    return {pid: composer.unique_slots(slots) for pid, slots in out.items()}


async def build_week_preview(db: AsyncSession, week_start: date, now: datetime) -> WeekPreview:
    """週のお知らせの一覧 (訪問のある、稼働中の患者様だけ)."""
    validate_week_start(week_start)
    station, tel = await load_profile(db)
    today = now.astimezone(JST).date()
    slots_by_patient = await _week_slots(db, week_start, today)
    if not slots_by_patient:
        return WeekPreview(week_start, today, composer.week_label(week_start, today), [])

    patients = (
        await db.scalars(
            select(Patient).where(
                Patient.id.in_(list(slots_by_patient)),
                Patient.status == "active",
                Patient.deleted_at.is_(None),
            )
        )
    ).all()
    ids = [p.id for p in patients]
    contacts_by_patient: dict[uuid.UUID, list[PatientContact]] = defaultdict(list)
    for c in (
        await db.scalars(
            select(PatientContact)
            .where(PatientContact.patient_id.in_(ids))
            .order_by(PatientContact.patient_id, PatientContact.sort_order)
        )
    ).all():
        contacts_by_patient[c.patient_id].append(c)
    notices = {
        n.patient_id: n
        for n in (
            await db.scalars(
                select(PatientWeekNotice).where(
                    PatientWeekNotice.patient_id.in_(ids),
                    PatientWeekNotice.week_start == week_start,
                )
            )
        ).all()
    }

    rows: list[PreviewRow] = []
    for p in sorted(patients, key=lambda x: ((x.kana or x.name or ""), x.name or "")):
        slots = slots_by_patient[p.id]
        surname = composer.surname_of(p.name)
        row = PreviewRow(patient_id=p.id, patient_name=p.name, surname=surname, slots=slots)
        row.body = composer.compose_week_notice(
            station_name=station,
            contact_tel=tel,
            surname=surname,
            week_start=week_start,
            today=today,
            slots=slots,
        )
        row.char_count = composer.count_chars(row.body)
        row.segments_est = composer.estimate_segments(row.char_count)
        row.destinations, row.reason = classify_destinations(contacts_by_patient.get(p.id, []))
        if row.reason is None and len(row.body) > composer.MAX_SMS_CHARS:
            row.destinations, row.reason = [], "too_long"
        notice = notices.get(p.id)
        if notice is not None:
            row.state = (
                "already_sent_same"
                if list(notice.visits or []) == row.visits_json
                else "changed_since_sent"
            )
        rows.append(row)
    return WeekPreview(week_start, today, composer.week_label(week_start, today), rows)


# ---------------------------------------------------------------------------
# 送る (batch を作る)
# ---------------------------------------------------------------------------


def dedupe_key(
    kind: str, patient_id: uuid.UUID, contact_id: uuid.UUID, week_start: date, version: int
) -> str:
    return f"{kind}:{patient_id}:{contact_id}:{week_start.isoformat()}:v{version}"


def select_rows_to_send(
    preview: WeekPreview, patient_ids: Sequence[uuid.UUID], *, resend: bool
) -> list[PreviewRow]:
    """選ばれた方のうち、送れる方 (同じ内容を送り済みの方は ``resend`` のときだけ)."""
    wanted = set(patient_ids)
    return [
        r
        for r in preview.sendable
        if r.patient_id in wanted and (resend or r.state != "already_sent_same")
    ]


async def _next_free_by_phone(
    db: AsyncSession, phones: set[str], now: datetime
) -> dict[str, datetime]:
    """番号ごとに、次に送ってよい時刻 (直近 600 秒に送った・送る予定の行から)."""
    if not phones:
        return {}
    window = now - timedelta(seconds=SAME_NUMBER_GAP_SECONDS)
    out: dict[str, datetime] = {}
    rows = (
        await db.execute(
            select(
                SmsMessage.phone, SmsMessage.sent_at, SmsMessage.not_before, SmsMessage.created_at
            ).where(
                SmsMessage.phone.in_(list(phones)),
                SmsMessage.status.in_(("queued", "sending", "accepted", "delivered", "unknown")),
            )
        )
    ).all()
    for phone, sent_at, not_before, created_at in rows:
        at = aware(sent_at) or aware(not_before) or aware(created_at)
        if at is None or at <= window:
            continue
        nxt = at + timedelta(seconds=SAME_NUMBER_GAP_SECONDS)
        if phone not in out or nxt > out[phone]:
            out[phone] = nxt
    return out


@dataclass
class BatchResult:
    batch: SmsBatch
    messages: list[SmsMessage]
    patient_ids: list[uuid.UUID]


async def create_week_batch(
    db: AsyncSession,
    *,
    preview: WeekPreview,
    rows: Sequence[PreviewRow],
    user_id: uuid.UUID | None,
    now: datetime,
) -> BatchResult:
    """batch・1 通ずつの行・お伝えした内容 (版を上げる)・その記録を作る (commit しない)."""
    week_start = preview.week_start
    batch = SmsBatch(
        id=uuid.uuid4(),
        kind="week",
        week_start=week_start,
        created_by_user_id=user_id,
        patient_count=len(rows),
        message_count=sum(r.message_count for r in rows),
        segments_est_total=sum(r.segments_est * r.message_count for r in rows),
        created_at=now,
    )
    db.add(batch)
    await db.flush()

    ids = [r.patient_id for r in rows]
    notices = {
        n.patient_id: n
        for n in (
            await db.scalars(
                select(PatientWeekNotice).where(
                    PatientWeekNotice.patient_id.in_(ids),
                    PatientWeekNotice.week_start == week_start,
                )
            )
        ).all()
    }
    next_free = await _next_free_by_phone(db, {d.phone for r in rows for d in r.destinations}, now)

    messages: list[SmsMessage] = []
    for r in rows:
        notice = notices.get(r.patient_id)
        if notice is None:
            notice = PatientWeekNotice(
                id=uuid.uuid4(), patient_id=r.patient_id, week_start=week_start, version=1
            )
            db.add(notice)
        else:
            notice.version = notice.version + 1
        notice.visits = r.visits_json
        notice.last_channel = "sms"
        notice.told_by_user_id = user_id
        notice.told_at = now
        db.add(
            PatientNoticeEvent(
                notice_id=notice.id,
                patient_id=r.patient_id,
                week_start=week_start,
                version=notice.version,
                kind="week",
                channel="sms",
                visits=r.visits_json,
                told_at=now,
                told_by_user_id=user_id,
                batch_id=batch.id,
            )
        )
        for d in r.destinations:
            free = next_free.get(d.phone)
            not_before = free if free is not None and free > now else None
            next_free[d.phone] = (not_before or now) + timedelta(seconds=SAME_NUMBER_GAP_SECONDS)
            msg = SmsMessage(
                id=uuid.uuid4(),
                batch_id=batch.id,
                kind="week",
                week_start=week_start,
                patient_id=r.patient_id,
                contact_id=d.contact_id,
                relation=d.relation,
                phone=d.phone,
                body=r.body,
                char_count=r.char_count,
                segments_est=r.segments_est,
                notice_version=notice.version,
                status="queued",
                dedupe_key=dedupe_key(
                    "week", r.patient_id, d.contact_id, week_start, notice.version
                ),
                not_before=not_before,
                created_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            db.add(msg)
            db.add(SmsStatusEvent(message_id=msg.id, at=now, status="queued"))
            messages.append(msg)
    await db.flush()
    return BatchResult(batch=batch, messages=messages, patient_ids=ids)


# ---------------------------------------------------------------------------
# 送る順番の処理 (1 件ずつ)
# ---------------------------------------------------------------------------


def _event(
    msg_id: uuid.UUID, at: datetime, status: str, raw: str | None, detail: str | None
) -> SmsStatusEvent:
    return SmsStatusEvent(
        message_id=msg_id,
        at=at,
        status=status,
        raw_status=raw,
        detail=detail[:255] if detail else None,
    )


def _apply_result(
    msg: SmsMessage, result: SendResult, provider_name: str, now: datetime
) -> SmsStatusEvent:
    msg.provider = provider_name
    if result.transaction_id:
        msg.transaction_id = result.transaction_id
    detail = result.error_code
    if result.needs_human and detail:
        detail = f"{detail} needs_human"
    if result.outcome == "accepted":
        msg.status = "accepted"
        msg.status_raw = result.status_raw
        msg.provider_msg_id = result.msg_id
        msg.accepted_at = now
        msg.next_poll_at = now + timedelta(seconds=FIRST_POLL_SECONDS)
        msg.error_code = None
        msg.error_message = None
        return _event(msg.id, now, "accepted", result.status_raw, None)
    msg.error_code = result.error_code
    msg.error_message = result.error_message
    if result.outcome == "retry" and (msg.attempt_count or 0) < MAX_ATTEMPTS:
        msg.status = "queued"
        msg.not_before = now + timedelta(
            seconds=RETRY_DELAY_SECONDS * max(1, msg.attempt_count or 1)
        )
        return _event(msg.id, now, "queued", None, f"retry {detail}")
    if result.outcome == "unknown":
        msg.status = "unknown"
        return _event(msg.id, now, "unknown", None, detail)
    msg.status = "failed"
    msg.status_decided_at = now
    return _event(msg.id, now, "failed", None, detail)


async def send_due_messages(
    *,
    limit: int = 200,
    interval: float | None = None,
    session_factory: Any = None,
) -> int:
    """送る時刻の来た ``queued`` を 1 件ずつ順に送る. 処理した件数を返す.

    送れる時間帯の外では何もしない (queued のまま残し、次に呼ばれたときに送る)。
    試験の守り (``sms_test_allowlist``) に無い番号は送らずに ``skipped`` にする。
    """
    from app.db.session import get_session_factory

    settings = get_settings()
    gap = SEND_INTERVAL_SECONDS if interval is None else interval
    factory = session_factory or get_session_factory()
    start = _now()
    if not within_send_hours(start, settings.sms_send_hours):
        return 0
    allow = parse_allowlist(settings.sms_test_allowlist)
    provider = get_provider(settings)

    async with factory() as db:
        candidates = (
            await db.execute(
                select(SmsMessage.id, SmsMessage.not_before)
                .where(SmsMessage.status == "queued")
                .order_by(SmsMessage.created_at, SmsMessage.id)
            )
        ).all()
        due = [mid for mid, nb in candidates if aware(nb) is None or aware(nb) <= start][:limit]

        done = 0
        for mid in due:
            if done and gap > 0:
                await asyncio.sleep(gap)
            now = _now()
            claimed = await db.execute(
                update(SmsMessage)
                .where(SmsMessage.id == mid, SmsMessage.status == "queued")
                .values(
                    status="sending",
                    attempt_count=SmsMessage.attempt_count + 1,
                    sent_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
            if claimed.rowcount != 1:
                continue
            msg = await db.get(SmsMessage, mid, populate_existing=True)
            if msg is None:
                continue
            if allow and (msg.phone or "") not in allow:
                msg.status = "skipped"
                msg.skip_reason = "not_in_test_allowlist"
                msg.provider = provider.name
                msg.status_decided_at = now
                db.add(_event(msg.id, now, "skipped", None, "not_in_test_allowlist"))
                await db.commit()
                done += 1
                continue
            try:
                result = await provider.send(msg.phone or "", msg.body or "")
            except Exception as exc:  # pragma: no cover - 窓口の思わぬ失敗でも順番の処理は止めない
                logger.warning("sms send failed unexpectedly: %s", type(exc).__name__)
                result = SendResult(outcome="unknown", error_code="exception", needs_human=True)
            db.add(_apply_result(msg, result, provider.name, _now()))
            await db.commit()
            done += 1
        return done
