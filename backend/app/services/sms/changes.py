"""変更のお知らせ: 変更の検知・お知らせ待ち・送る・送らずに済ませる・ベル.

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-2、
``docs/plans/sms-notify-api-contract-2026-11-16.md`` §2〜§5・§12・§13。

比べ方 (設計 §5-2):
* 「お伝えした内容」(``patient_week_notices.visits``) と今の予定を、患者様 × 週ごとに比べる。
  お伝えした内容がある方 (``told_version`` がある方) だけ。一度も受け付けられていない方は
  週の一覧の ``new`` / ``not_delivered`` に出る (契約 §13-1)。
* 比べるのは日と開始時刻だけ (週のお知らせと同じ数え方: 取消・削除・打刻だけの予定外を除き、
  まだ始まっていない訪問)。訪問の番号は使わない (取込や作り直しで番号が替わるため)。
  担当の交代・終わりの時刻だけの変更は違いにならない。過ぎた日・始まった訪問は比べない。
* 違いの分け方: 同じ日の「消えた」と「増えた」を先に組にして ``time``、残りを順に組にして
  ``date``、余りは ``cancel`` / ``add``。
* 違いがある方は「お知らせ待ち」。予定が元に戻ったら待ちは消える。
  いま送っている途中 (待ち・送信中の行がある) の方は、一覧・送る・済ませるの対象にしない。
* 稼働中でない方も一覧に出す (送れない・``patient_inactive``・済ませるだけ)。

ベル (管理者全員・患者様の名前は書かない):
* 違いの印を、後の回 (最初に見てから 4 分以上たった回) でも同じに見て初めて出す
  (取込の途中の揺れで鳴らさない。cron の 5 分おきの揺れで 1 回分遅れないよう 4 分で見る)。
* 待ちのままなら 60 分ごと、今日・明日の訪問が関わる (急ぎ) なら 15 分ごとに出し直す。
* どの方の分でも、変更のベルとベルのあいだは 15 分以上あける。急ぎのベルはこの間隔に
  縛られない (急ぎの方ごとの 15 分ごとは守る)。
* mock (模擬) でお伝えした方は、窓口が mock でなくなったら比べない (まだお伝えしていない)。
* 違いが消えて 30 分の内に戻ったら、最初に気づいた時刻をそのまま使う。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Final

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.patient import Patient
from app.models.patient_contact import PatientContact
from app.models.sms import PatientNoticeEvent, PatientWeekNotice, SmsBatch, SmsMessage
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.services.contacts import RELATION_LABEL
from app.services.sms import composer
from app.services.sms.composer import VisitChange, slots_from_json
from app.services.sms.notices import (
    CHANGE_PENDING_PATH,
    CONFLICT_MESSAGE,
    IN_FLIGHT_STATUSES,
    JST,
    NOTIFY_CHANGE_PENDING,
    NOTIFY_DELIVERY_FAILED,
    RECORDS_PATH,
    STALE_MESSAGE,
    Destination,
    SmsNoticeError,
    add_bell,
    add_queued_message,
    admin_users,
    aware,
    body_reason,
    bump_notice_version,
    classify_destinations,
    clear_pending,
    destinations_key,
    effective_told_version,
    is_upcoming,
    last_bell_at,
    load_profile,
    make_token,
    next_free_by_phone,
)
from app.utils.db import try_advisory_xact_lock

__all__ = [
    "CONFLICT_MESSAGE",
    "NOTIFY_CHANGE_PENDING",
    "NOTIFY_DELIVERY_FAILED",
    "RECORDS_PATH",
    "add_bell",
    "admin_users",
    "slots_from_json",
]

#: ベルを出し直す間隔 (分)。設計 §13 で仮の値。
BELL_INTERVAL_MINUTES: Final = 60
BELL_URGENT_INTERVAL_MINUTES: Final = 15
#: 違いの印を最初に見てから、これだけたった後の回でも同じなら最初のベルを出す (分)。
#: cron は 5 分おきなので、揺れて 4 分 58 秒後に回っても 1 回分遅れないよう 4 分にする。
BELL_STABLE_MINUTES: Final = 4
#: 変更のベルとベルのあいだ (どの方の分でも・分)。
BELL_MIN_GAP_MINUTES: Final = 15
#: 違いが消えてもこの内に戻れば、最初に気づいた時刻をそのまま使う (分)。
PENDING_KEEP_MINUTES: Final = 30

#: pg_try_advisory_xact_lock の鍵 (cron の 3 つの口で別々)。"SMSDETCT" などの 8 文字。
DETECT_LOCK_KEY: Final = 0x534D_5344_4554_4354
SEND_DUE_LOCK_KEY: Final = 0x534D_5353_454E_4444
POLL_LOCK_KEY: Final = 0x534D_5350_4F4C_4C53

#: 送らずに済ませるときの、手段と理由の組 (契約 §13-6)。
RESOLVE_REASONS: Final[dict[str, tuple[str, ...]]] = {
    "phone": ("phoned",),
    "none": ("patient_request", "other"),
}

Slot = tuple[date, time]


# ---------------------------------------------------------------------------
# 比べる (DB を読まない部分)
# ---------------------------------------------------------------------------


def diff_slots(told: Sequence[Slot], current: Sequence[Slot]) -> list[VisitChange]:
    """お伝えした内容と今の予定の違い (契約 §2 の分け方)."""
    told_set, cur_set = set(told), set(current)
    removed = sorted(told_set - cur_set)
    added = sorted(cur_set - told_set)
    changes: list[VisitChange] = []

    # 1) 同じ日の出入りを先に組にする (時刻の変更)。
    rest_removed: list[Slot] = []
    for r in removed:
        hit = next((a for a in added if a[0] == r[0]), None)
        if hit is None:
            rest_removed.append(r)
            continue
        added.remove(hit)
        changes.append(VisitChange("time", r[0], r[1], hit[0], hit[1]))
    # 2) 残りを順に組にする (日の変更)。
    pairs = min(len(rest_removed), len(added))
    for r, a in zip(rest_removed[:pairs], added[:pairs], strict=True):
        changes.append(VisitChange("date", r[0], r[1], a[0], a[1]))
    # 3) 余りはお休み・追加。
    for r in rest_removed[pairs:]:
        changes.append(VisitChange("cancel", old_date=r[0], old_start=r[1]))
    for a in added[pairs:]:
        changes.append(VisitChange("add", new_date=a[0], new_start=a[1]))

    def key(c: VisitChange) -> tuple[date, time]:
        if c.old_date is not None and c.old_start is not None:
            return (c.old_date, c.old_start)
        assert c.new_date is not None and c.new_start is not None
        return (c.new_date, c.new_start)

    return sorted(changes, key=key)


def is_urgent(changes: Sequence[VisitChange], today: date) -> bool:
    """変更に今日か明日 (日本時間) の訪問が関わる."""
    tomorrow = today + timedelta(days=1)
    for c in changes:
        for d in (c.old_date, c.new_date):
            if d is not None and today <= d <= tomorrow:
                return True
    return False


def _slot_json(d: date | None, t: time | None) -> dict[str, str] | None:
    if d is None or t is None:
        return None
    return {"date": d.isoformat(), "start": f"{t.hour:02d}:{t.minute:02d}"}


def change_json(c: VisitChange) -> dict[str, Any]:
    return {
        "kind": c.kind,
        "from": _slot_json(c.old_date, c.old_start),
        "to": _slot_json(c.new_date, c.new_start),
        "label": composer.change_line(c),
    }


def change_items(changes: Sequence[VisitChange]) -> list[list[Any]]:
    """違いの並び (種類・前・後)。印に使う."""
    return [[c["kind"], c["from"], c["to"]] for c in (change_json(x) for x in changes)]


def signature_of(changes: Sequence[VisitChange]) -> str:
    raw = json.dumps(change_items(changes), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def change_token_of(
    changes: Sequence[VisitChange], version: int | None = None, told_version: int | None = None
) -> str:
    """変更の並びと版の印 (契約 §12 の ``change_token``).

    版も入れる: 見た後にほかの管理者が送った・済ませた (版が替わった) ら合わずに 409。
    """
    return make_token("change_set", change_items(changes), version, told_version)


# ---------------------------------------------------------------------------
# 今のお知らせ待ち (DB を読むだけ)
# ---------------------------------------------------------------------------


@dataclass
class PendingChange:
    notice: PatientWeekNotice
    patient: Patient
    changes: list[VisitChange]
    current: list[Slot]
    urgent: bool
    signature: str
    #: 比べたときの ``patient_week_notices.version`` (版を上げるときの条件)。
    version_seen: int = 0
    #: 比べたときの ``patient_week_notices.told_version`` (印に入れる)。
    told_version_seen: int | None = None

    @property
    def key(self) -> tuple[uuid.UUID, date]:
        return (self.notice.patient_id, self.notice.week_start)

    @property
    def change_token(self) -> str:
        return change_token_of(self.changes, self.version_seen, self.told_version_seen)

    @property
    def inactive(self) -> bool:
        return self.patient.status != "active"


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def compute_pending(db: AsyncSession, now: datetime) -> list[PendingChange]:
    """お伝えした内容のある、今日以降の日を含む週と今の予定を比べ、違いのある方を返す.

    いま送っている途中 (待ち・送信中の行がある) の方は返さない。
    """
    today = now.astimezone(JST).date()
    rows = (
        await db.execute(
            select(PatientWeekNotice, Patient)
            .join(Patient, Patient.id == PatientWeekNotice.patient_id)
            .where(
                PatientWeekNotice.week_start >= today - timedelta(days=6),
                PatientWeekNotice.told_version.is_not(None),
                Patient.deleted_at.is_(None),
            )
        )
    ).all()
    if not rows:
        return []
    ids = list({n.patient_id for n, _ in rows})
    first_week = min(n.week_start for n, _ in rows)
    in_flight = {
        (pid, ws)
        for pid, ws in (
            await db.execute(
                select(SmsMessage.patient_id, SmsMessage.week_start)
                .where(
                    SmsMessage.status.in_(IN_FLIGHT_STATUSES),
                    SmsMessage.patient_id.in_(ids),
                    SmsMessage.week_start >= first_week,
                )
                .distinct()
            )
        ).all()
    }
    last_day = max(n.week_start for n, _ in rows) + timedelta(days=6)
    current: dict[tuple[uuid.UUID, date], list[Slot]] = defaultdict(list)
    for pid, d, t in (
        await db.execute(
            select(Visit.patient_id, Visit.visit_date, Visit.start_time).where(
                Visit.patient_id.in_(ids),
                Visit.visit_date >= today,
                Visit.visit_date <= last_day,
                Visit.status != VISIT_STATUS_CANCELLED,
                Visit.deleted_at.is_(None),
                Visit.is_unplanned.is_(False),
            )
        )
    ).all():
        if is_upcoming((d, t), now):
            current[(pid, _monday(d))].append((d, t))

    settings = get_settings()
    out: list[PendingChange] = []
    for notice, patient in rows:
        if (notice.patient_id, notice.week_start) in in_flight:
            continue
        if effective_told_version(notice, settings) is None:
            # mock でお伝えした方 (窓口が mock でなくなった) = まだお伝えしていない。
            continue
        told = [s for s in slots_from_json(notice.visits) if is_upcoming(s, now)]
        cur = composer.unique_slots(current.get((notice.patient_id, notice.week_start), []))
        changes = diff_slots(told, cur)
        if not changes:
            continue
        out.append(
            PendingChange(
                notice=notice,
                patient=patient,
                changes=changes,
                current=cur,
                urgent=is_urgent(changes, today),
                signature=signature_of(changes),
                version_seen=notice.version,
                told_version_seen=notice.told_version,
            )
        )
    return out


def detected_at_of(p: PendingChange, now: datetime) -> datetime:
    """気づいた時刻 (検知の cron がまだ回っていなければ、いま)."""
    since = aware(p.notice.pending_since)
    return now if since is None else since


# ---------------------------------------------------------------------------
# 一覧 (送り先と文面)
# ---------------------------------------------------------------------------


@dataclass
class PendingView:
    pending: PendingChange
    detected_at: datetime
    body: str = ""
    char_count: int = 0
    segments_est: int = 0
    destinations: list[Destination] = field(default_factory=list)
    reason: str | None = None
    call_numbers: list[dict[str, str]] = field(default_factory=list)

    @property
    def sendable(self) -> bool:
        return self.reason is None

    @property
    def message_count(self) -> int:
        return len(self.destinations)

    @property
    def token(self) -> str | None:
        """本文・送り先・変更の並びの印 (送れない方は None・契約 §12)."""
        if not self.sendable:
            return None
        return make_token(
            "change",
            self.body,
            destinations_key(self.destinations),
            change_items(self.pending.changes),
            self.pending.version_seen,
            self.pending.told_version_seen,
        )


def station_head_name(station_name: str) -> str:
    """変更のお知らせの頭の【 】に入れる名前 (設定の短い名前 → 事業所名)."""
    short = (get_settings().sms_station_short_name or "").strip()
    return short or station_name


async def build_pending_views(
    db: AsyncSession, pendings: Sequence[PendingChange], now: datetime
) -> list[PendingView]:
    if not pendings:
        return []
    station, tel = await load_profile(db)
    head = station_head_name(station)
    ids = list({p.notice.patient_id for p in pendings})
    contacts: dict[uuid.UUID, list[PatientContact]] = defaultdict(list)
    for c in (
        await db.scalars(
            select(PatientContact)
            .where(PatientContact.patient_id.in_(ids))
            .order_by(PatientContact.patient_id, PatientContact.sort_order)
        )
    ).all():
        contacts[c.patient_id].append(c)

    views: list[PendingView] = []
    for p in pendings:
        v = PendingView(pending=p, detected_at=detected_at_of(p, now))
        v.body = composer.compose_change_notice(
            station_name=head,
            contact_tel=tel,
            surname=composer.surname_of(p.patient.name),
            changes=p.changes,
        )
        v.char_count = composer.count_chars(v.body)
        v.segments_est = composer.estimate_segments(v.char_count)
        mine = contacts.get(p.notice.patient_id, [])
        if p.inactive:
            v.destinations, v.reason = [], "patient_inactive"
        else:
            v.destinations, v.reason = classify_destinations(mine)
            if v.reason is None:
                v.reason = body_reason(v.body)
                if v.reason is not None:
                    v.destinations = []
        v.call_numbers = [
            {
                "relation_label": RELATION_LABEL.get(c.relation, c.relation),
                "phone_display": c.phone_display or c.phone or "",
            }
            for c in mine
            if c.phone
        ]
        views.append(v)
    views.sort(
        key=lambda x: (
            not x.pending.urgent,
            x.detected_at,
            x.pending.patient.kana or x.pending.patient.name or "",
        )
    )
    return views


def pick_views_to_send(
    views: Sequence[PendingView], wanted: dict[tuple[uuid.UUID, date], str]
) -> list[PendingView]:
    """選ばれた方の行 (1 名でも合わなければ 409 で、だれにも送らない・契約 §12)."""
    by_key = {v.pending.key: v for v in views}
    out: list[PendingView] = []
    for key, token in wanted.items():
        v = by_key.get(key)
        if v is None or not v.sendable or v.token != token:
            raise SmsNoticeError(409, STALE_MESSAGE)
        out.append(v)
    return out


# ---------------------------------------------------------------------------
# 送る・送らずに済ませる
# ---------------------------------------------------------------------------


def _event(
    p: PendingChange,
    version: int,
    *,
    channel: str,
    reason: str | None,
    note: str | None,
    user_id: uuid.UUID | None,
    now: datetime,
    told_at: datetime | None,
    batch_id: uuid.UUID | None,
) -> PatientNoticeEvent:
    notice = p.notice
    return PatientNoticeEvent(
        notice_id=notice.id,
        patient_id=notice.patient_id,
        week_start=notice.week_start,
        version=version,
        kind="change",
        channel=channel,
        reason=reason,
        note=note or None,
        urgent=p.urgent,
        visits=composer.slots_to_json(p.current),
        detected_at=detected_at_of(p, now),
        told_at=told_at,
        told_by_user_id=user_id,
        batch_id=batch_id,
    )


@dataclass
class ChangeBatchResult:
    batch: SmsBatch
    messages: list[SmsMessage]


async def create_change_batch(
    db: AsyncSession,
    *,
    views: Sequence[PendingView],
    user_id: uuid.UUID | None,
    now: datetime,
) -> ChangeBatchResult:
    """変更のお知らせの batch・1 通ずつの行・版の記録を作り、版を上げる (commit しない).

    「お伝えした内容」は替えない (受け付けられたときに替わる・契約 §13-1)。
    同時の操作と重なったら :class:`SmsNoticeError` 409。
    """
    batch = SmsBatch(
        id=uuid.uuid4(),
        kind="change",
        week_start=min(v.pending.notice.week_start for v in views),
        created_by_user_id=user_id,
        patient_count=len(views),
        message_count=sum(v.message_count for v in views),
        segments_est_total=sum(v.segments_est * v.message_count for v in views),
        created_at=now,
    )
    db.add(batch)
    await db.flush()
    next_free = await next_free_by_phone(db, {d.phone for v in views for d in v.destinations}, now)
    messages: list[SmsMessage] = []
    for v in views:
        p = v.pending
        version = await bump_notice_version(db, p.notice, p.version_seen)
        db.add(
            _event(
                p,
                version,
                channel="sms",
                reason=None,
                note=None,
                user_id=user_id,
                now=now,
                told_at=None,
                batch_id=batch.id,
            )
        )
        for d in v.destinations:
            messages.append(
                add_queued_message(
                    db,
                    batch=batch,
                    week_start=p.notice.week_start,
                    patient_id=p.notice.patient_id,
                    destination=d,
                    body=v.body,
                    char_count=v.char_count,
                    segments_est=v.segments_est,
                    notice_version=version,
                    user_id=user_id,
                    now=now,
                    next_free=next_free,
                )
            )
    await db.flush()
    return ChangeBatchResult(batch=batch, messages=messages)


async def resolve_change(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    week_start: date,
    change_token: str,
    channel: str,
    reason: str,
    note: str | None,
    user_id: uuid.UUID | None,
    now: datetime,
) -> int:
    """SMS を送らずに「お伝え済み」にする (commit しない)。新しい版を返す.

    見た変更の並び (``change_token``) と今の並びが違えば 409 (見ていない変更を済みにしない)。
    """
    if reason not in RESOLVE_REASONS.get(channel, ()):
        raise SmsNoticeError(422, "お伝えした手段と理由の組み合わせが合いません")
    in_flight = await db.scalar(
        select(SmsMessage.id)
        .where(
            SmsMessage.patient_id == patient_id,
            SmsMessage.week_start == week_start,
            SmsMessage.status.in_(IN_FLIGHT_STATUSES),
        )
        .limit(1)
    )
    if in_flight is not None:
        # いま送っている途中 (ほかの管理者が送った) → 済ませない。
        raise SmsNoticeError(409, CONFLICT_MESSAGE)
    pendings = await compute_pending(db, now)
    hit = next((p for p in pendings if p.key == (patient_id, week_start)), None)
    if hit is None or hit.change_token != change_token:
        raise SmsNoticeError(409, STALE_MESSAGE)
    version = await bump_notice_version(db, hit.notice, hit.version_seen)
    notice = hit.notice
    notice.visits = composer.slots_to_json(hit.current)
    notice.told_version = version
    notice.last_channel = channel
    notice.told_via = channel
    notice.told_by_user_id = user_id
    notice.told_at = now
    db.add(
        _event(
            hit,
            version,
            channel=channel,
            reason=reason,
            note=note,
            user_id=user_id,
            now=now,
            told_at=now,
            batch_id=None,
        )
    )
    clear_pending(notice)
    await db.flush()
    return version


# ---------------------------------------------------------------------------
# ベル
# ---------------------------------------------------------------------------


def _stable(notice: PatientWeekNotice, now: datetime, *, seen_before: bool) -> bool:
    """違いの印が落ち着いた (前の回で見た印を、4 分以上たった後の回でも同じに見た)."""
    since = aware(notice.pending_signature_at)
    return (
        seen_before and since is not None and now - since >= timedelta(minutes=BELL_STABLE_MINUTES)
    )


def _bell_due(notice: PatientWeekNotice, now: datetime) -> bool:
    last = aware(notice.last_bell_at)
    if last is None:
        return True
    minutes = BELL_URGENT_INTERVAL_MINUTES if notice.pending_urgent else BELL_INTERVAL_MINUTES
    return now - last >= timedelta(minutes=minutes)


async def run_detect(db: AsyncSession, now: datetime) -> dict[str, Any]:
    """変更を見つけて気づいた時刻を残し、ベルを出す (cron 5 分おき・自前で commit)."""
    if not await try_advisory_xact_lock(db, DETECT_LOCK_KEY):
        return {"skipped": "locked"}
    pendings = await compute_pending(db, now)
    by_id = {p.notice.id: p for p in pendings}
    keep = timedelta(minutes=PENDING_KEEP_MINUTES)

    # 違いが消えた方: 30 分は気づいた時刻を残す (取込の途中で消えて戻ることがある)。
    stale = (
        await db.scalars(
            select(PatientWeekNotice).where(
                or_(
                    PatientWeekNotice.pending_signature.is_not(None),
                    PatientWeekNotice.pending_since.is_not(None),
                )
            )
        )
    ).all()
    for n in stale:
        if n.id in by_id:
            continue
        cleared = aware(n.pending_cleared_at)
        if cleared is None:
            n.pending_cleared_at = now
            n.pending_urgent = False
        elif now - cleared > keep:
            clear_pending(n)

    new = 0
    #: この回で初めて見た印 (前の回では見ていない = まだ落ち着いていない)。
    new_this_run: set[uuid.UUID] = set()
    for p in pendings:
        n = p.notice
        cleared = aware(n.pending_cleared_at)
        returned_in_time = cleared is not None and now - cleared <= keep
        if cleared is not None and not returned_in_time:
            clear_pending(n)
        n.pending_cleared_at = None
        if n.pending_signature != p.signature:
            new += 1
            new_this_run.add(n.id)
            if n.pending_since is None:
                n.pending_since = now
            n.pending_signature = p.signature
            n.pending_signature_at = now
            # 中身の違う変更 = 新しい変更 (後の回でも同じ印ならベル)。
            n.last_bell_at = None
            n.bell_count = 0
        n.pending_urgent = p.urgent

    bells = 0
    stable = [
        p for p in pendings if _stable(p.notice, now, seen_before=p.notice.id not in new_this_run)
    ]
    due = [p for p in stable if _bell_due(p.notice, now)]
    if due:
        gap_since = now - timedelta(minutes=BELL_MIN_GAP_MINUTES)
        recent = await last_bell_at(db, NOTIFY_CHANGE_PENDING, gap_since)
        gap_ok = recent is None or now - recent >= timedelta(minutes=BELL_MIN_GAP_MINUTES)
        # 急ぎ (今日・明日の訪問) のベルは、ほかの方のベルとの 15 分の間隔に縛られない。
        if gap_ok or any(p.urgent for p in due):
            urgent = sum(1 for p in pendings if p.urgent)
            body = (
                f"今日・明日の訪問の変更が {urgent} 名あります。お電話でもお伝えください。"
                if urgent
                else "［予定のお知らせ］の一覧で文面を確かめて、お知らせしてください。"
            )
            await add_bell(
                db,
                type_=NOTIFY_CHANGE_PENDING,
                title=f"予定が変わった方が {len(pendings)} 名います（お知らせ待ち）",
                body=body,
                now=now,
                href=CHANGE_PENDING_PATH,
            )
            bells = 1
            for p in stable:
                p.notice.last_bell_at = now
                p.notice.bell_count = (p.notice.bell_count or 0) + 1

    await db.commit()
    return {
        "pending": len(pendings),
        "urgent": sum(1 for p in pendings if p.urgent),
        "new": new,
        "bells": bells,
    }
