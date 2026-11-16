"""送った後: 届いたかの照会・送っている途中で止まった行・届かなかったときのベル.

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-3・§7-3、
``docs/plans/sms-notify-api-contract-2026-11-16.md`` §5。

* 照会は受け付けた時刻 (``accepted_at``) から 1 分・5 分・30 分・3 時間・24 時間・72 時間の
  6 回まで。届いた / 届かなかったに決まったら止める。6 回目でも決まらなければ ``unknown``。
* 状態が変わるたびに ``sms_status_events`` に残す (KDDI の値はそのまま)。
* ``sending`` のまま 10 分たった行は ``unknown`` にする (送ったか分からないので送り直さない)。
* 届かなかった・分からない (``failed``・``unknown``) 1 通ごとに 1 回だけ、まとめてベルを出す
  (``sms_messages.fail_notified_at``)。``unknown`` も「決まった時刻」(``status_decided_at``) を持つ。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.sms import SmsMessage, SmsStatusEvent
from app.services.sms import notices
from app.services.sms.changes import POLL_LOCK_KEY, SEND_DUE_LOCK_KEY
from app.services.sms.notices import NOTIFY_DELIVERY_FAILED, add_bell, aware
from app.services.sms.provider import SmsProviderError, get_provider
from app.utils.db import try_advisory_xact_lock

logger = logging.getLogger(__name__)

#: 受け付けた時刻から照会する時刻 (秒)。設計 §7-3。
POLL_OFFSETS_SECONDS: Final[tuple[int, ...]] = (60, 300, 1800, 3 * 3600, 24 * 3600, 72 * 3600)
#: ``sending`` のまま、これより長くたった行は ``unknown`` にする。
STUCK_SENDING_MINUTES: Final = 10


def next_poll_after(accepted_at: datetime, poll_count: int) -> datetime | None:
    """``poll_count`` 回照会した後の次の照会の時刻 (6 回終えたら None)."""
    if poll_count >= len(POLL_OFFSETS_SECONDS):
        return None
    return accepted_at + timedelta(seconds=POLL_OFFSETS_SECONDS[poll_count])


def _event(msg: SmsMessage, at: datetime, status: str, raw: str | None, detail: str | None):
    return SmsStatusEvent(
        message_id=msg.id,
        at=at,
        status=status,
        raw_status=raw[:32] if raw else None,
        detail=detail[:255] if detail else None,
    )


async def fix_stuck_sending(db: AsyncSession, now: datetime) -> int:
    """``sending`` へ取ってから 10 分たった行を ``unknown`` にする (commit しない).

    取った時刻 (``claimed_at``) で見る。送り直しで待ちに戻った行の古い ``sent_at`` では見ない。
    """
    limit = now - timedelta(minutes=STUCK_SENDING_MINUTES)
    rows = (
        await db.scalars(
            select(SmsMessage).where(
                SmsMessage.status == "sending",
                or_(
                    SmsMessage.claimed_at <= limit,
                    and_(SmsMessage.claimed_at.is_(None), SmsMessage.updated_at <= limit),
                ),
            )
        )
    ).all()
    fixed = 0
    for m in rows:
        since = aware(m.claimed_at) or aware(m.updated_at)
        if since is None or since > limit:
            continue
        m.status = "unknown"
        m.status_decided_at = now
        m.updated_at = now
        db.add(_event(m, now, "unknown", None, "stuck_sending"))
        fixed += 1
    return fixed


async def bell_new_failures(db: AsyncSession, now: datetime) -> int:
    """まだベルを出していない ``failed``・``unknown`` の行をまとめて 1 つのベルにする (commit しない)."""
    await db.flush()  # この回で failed にした行も数える (session は autoflush しない)。
    rows = (
        await db.scalars(
            select(SmsMessage).where(
                SmsMessage.status.in_(("failed", "unknown")),
                SmsMessage.fail_notified_at.is_(None),
            )
        )
    ).all()
    if not rows:
        return 0
    any_unknown = any(m.status == "unknown" for m in rows)
    what = "届かなかった・届いたか分からない" if any_unknown else "届かなかった"
    await add_bell(
        db,
        type_=NOTIFY_DELIVERY_FAILED,
        title=f"{what}お知らせが {len(rows)} 件あります",
        body="お知らせの記録で理由を確かめ、お電話でもお伝えください。",
        now=now,
    )
    for m in rows:
        m.fail_notified_at = now
    return len(rows)


async def run_send_due(db: AsyncSession) -> dict[str, Any]:
    """cron: 送る時刻の来た分を送る・止まった行を unknown に・届かなかったらベル.

    advisory lock は ``db`` の transaction のあいだ持つ (送るのは別の session。1 通ずつ
    「まだ queued なら」で取るので、lock が無くても二重には送らない)。
    """
    if not await try_advisory_xact_lock(db, SEND_DUE_LOCK_KEY):
        return {"skipped": "locked"}
    sent = await notices.send_due_messages()
    now = notices._now()
    stuck = await fix_stuck_sending(db, now)
    await bell_new_failures(db, now)
    await db.commit()
    return {"sent": sent, "stuck_fixed": stuck}


async def poll_statuses(db: AsyncSession, now: datetime, *, limit: int = 200) -> dict[str, int]:
    """照会の時刻が来た ``accepted`` の行を照会する (commit しない)."""
    provider = get_provider(get_settings())
    due = (
        await db.scalars(
            select(SmsMessage)
            .where(
                SmsMessage.status == "accepted",
                SmsMessage.provider_msg_id.is_not(None),
                SmsMessage.next_poll_at.is_not(None),
                SmsMessage.next_poll_at <= now,
            )
            .order_by(SmsMessage.next_poll_at, SmsMessage.id)
            .limit(limit)
        )
    ).all()
    polled = delivered = failed = 0
    for m in due:
        polled += 1
        m.poll_count = (m.poll_count or 0) + 1
        m.status_checked_at = now
        m.updated_at = now
        try:
            res = await provider.get_status(m.provider_msg_id or "")
        except SmsProviderError as exc:
            logger.warning("sms status poll failed: %s", exc.kind)
            res = None
        if res is not None:
            if res.transaction_id:
                m.transaction_id = res.transaction_id
            if res.carrier:
                m.carrier = res.carrier
            if res.split_num is not None:
                m.split_num = res.split_num
            if res.reason:
                m.fail_reason_raw = res.reason
            changed = (res.status_raw or None) != (m.status_raw or None)
            m.status_raw = res.status_raw or m.status_raw
            if res.status in ("delivered", "failed"):
                m.status = res.status
                m.status_decided_at = now
                m.next_poll_at = None
                db.add(_event(m, now, res.status, res.status_raw, res.reason))
                if res.status == "delivered":
                    delivered += 1
                else:
                    failed += 1
                continue
            if changed:
                db.add(_event(m, now, res.status, res.status_raw, None))
        accepted_at = aware(m.accepted_at) or aware(m.sent_at) or now
        nxt = next_poll_after(accepted_at, m.poll_count)
        if nxt is None:
            # 72 時間たっても決まらない → 分からない (KDDI の課金は 72 時間以内に届いた分)。
            m.status = "unknown"
            m.status_decided_at = now
            m.next_poll_at = None
            db.add(_event(m, now, "unknown", m.status_raw, "poll_exhausted"))
        else:
            m.next_poll_at = nxt
    return {"polled": polled, "delivered": delivered, "failed": failed}


async def run_poll(db: AsyncSession, now: datetime) -> dict[str, Any]:
    """cron: 届いたかの照会 (5 分おき・自前で commit)。届かなかったらベル."""
    if not await try_advisory_xact_lock(db, POLL_LOCK_KEY):
        return {"skipped": "locked"}
    out = await poll_statuses(db, now)
    await bell_new_failures(db, now)
    await db.commit()
    return out
