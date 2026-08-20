"""イベント outbound (楽スケ→カイポケ・Phase 3) のプラン構築と送信後の昇格.

正典 = docs/plans/kaipoke-event-two-way-design.md §3-① / §7-b。

送信対象 = 当該週 (月〜土) の ``source='manual'`` の staff_events。
(将来 Phase 2 の ``'fixed'`` もここに加わる。) ``source='kaipoke'`` は
そもそもカイポケ由来なので送らない。

職員内部IDの供給源 = 取込済み行の external_id
(``{個別業務ID}:{職員内部ID}:{日付}``) からの逆引きのみ (Staff に列なし)。
一度も取込に現れない職員は「送信不可」としてプレビューで明示する。

二重化対策 (§7-b) = 送信成功後の**昇格**:
  - RPA が返す external_key を当該行に刻み ``source='kaipoke'`` へ変更
    → 以後は取込の update/delete 管理下に入る (1 予定 1 行を維持)。
  - 同じ key の kaipoke 行が既に居る場合 (過去の取込と衝突) は、同一予定が
    既に楽スケに 2 本ある状態なので **manual 行を削除**して重複を解消する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.staff import Staff, StaffEvent

EVENT_SOURCE_MANUAL = "manual"
EVENT_SOURCE_KAIPOKE = "kaipoke"

EVENT_SOURCE_FIXED = "fixed"

# 送信対象の source: 手動登録 + 固定イベント展開分 (Phase 2)
OUTBOUND_SOURCES: tuple[str, ...] = (EVENT_SOURCE_MANUAL, EVENT_SOURCE_FIXED)


@dataclass
class OutboundItem:
    """送信プレビューの 1 行。"""

    event_id: UUID
    staff_id: UUID
    staff_name: str
    target_date: date
    start: str  # HH:MM
    end: str  # HH:MM
    title: str
    is_memo: bool
    sendable: bool
    reason: str | None = None
    staff_internal_id: str | None = None


@dataclass
class OutboundPlan:
    week_start: date
    week_end: date  # 土曜
    items: list[OutboundItem] = field(default_factory=list)

    @property
    def sendable_count(self) -> int:
        return sum(1 for i in self.items if i.sendable)


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


async def build_staff_internal_map(db: AsyncSession) -> dict[UUID, str]:
    """staff_id → カイポケ職員内部ID (取込済み external_id の第2フィールドから逆引き)。"""
    rows = (
        await db.execute(
            select(StaffEvent.staff_id, StaffEvent.external_id).where(
                StaffEvent.source == EVENT_SOURCE_KAIPOKE,
                StaffEvent.external_id.is_not(None),
            )
        )
    ).all()
    out: dict[UUID, str] = {}
    for staff_id, ext in rows:
        parts = (ext or "").split(":")
        if len(parts) >= 2 and parts[1].isdigit():
            out[staff_id] = parts[1]
    return out


async def build_outbound_plan(db: AsyncSession, week_start: date) -> OutboundPlan:
    """当該週 (月〜日で走査・日曜は送信不可扱い) の送信プランを組む。read-only。"""
    if week_start.weekday() != 0:
        raise ValueError(f"week_start must be a Monday: {week_start}")
    saturday = week_start + timedelta(days=5)
    range_start = datetime.combine(week_start, time.min)
    range_end = datetime.combine(week_start + timedelta(days=7), time.min)

    internal_map = await build_staff_internal_map(db)
    staff_names = {
        s.id: s.name
        for s in (await db.scalars(select(Staff).where(Staff.deleted_at.is_(None)))).all()
    }

    events = (
        await db.scalars(
            select(StaffEvent)
            .where(
                StaffEvent.source.in_(OUTBOUND_SOURCES),
                StaffEvent.starts_at >= range_start,
                StaffEvent.starts_at < range_end,
            )
            .order_by(StaffEvent.starts_at)
        )
    ).all()

    plan = OutboundPlan(week_start=week_start, week_end=saturday)
    for ev in events:
        d = ev.starts_at.date()
        internal = internal_map.get(ev.staff_id)
        sendable = True
        reason: str | None = None
        if d.weekday() == 6:
            sendable = False
            reason = "日曜日はカイポケ取込対象外のため送信しません"
        elif internal is None:
            sendable = False
            reason = "カイポケの職員IDが未対応です（この職員の取込実績がありません）"
        plan.items.append(
            OutboundItem(
                event_id=ev.id,
                staff_id=ev.staff_id,
                staff_name=staff_names.get(ev.staff_id, "(不明)"),
                target_date=d,
                start=_hhmm(ev.starts_at.time()),
                end=_hhmm(ev.ends_at.time()),
                title=(ev.title or ev.event_type or "").strip() or "(無題)",
                is_memo=ev.starts_at == ev.ends_at,
                sendable=sendable,
                reason=reason,
                staff_internal_id=internal,
            )
        )
    return plan


def to_rpa_items(items: list[OutboundItem]) -> list[dict[str, Any]]:
    """sendable なプラン行を RPA /api/individual-tasks-apply の items へ変換。"""
    return [
        {
            "external_ref": str(i.event_id),
            "staff_internal_id": i.staff_internal_id,
            "date": i.target_date.isoformat(),
            "start": i.start,
            "end": i.end,
            "title": i.title,
        }
        for i in items
        if i.sendable and i.staff_internal_id
    ]


async def promote_sent_events(db: AsyncSession, results: list[dict[str, Any]]) -> dict[str, int]:
    """RPA の書き込み結果を楽スケ側へ反映する (昇格 / 重複解消)。commit しない。

    outcome 'added' / 'skipped_duplicate' (= カイポケ側に同一予定が既にある) は
    どちらも external_key を持つ → 当該 manual 行を kaipoke 系へ昇格。
    同じ key の kaipoke 行が既に楽スケに居るなら manual 行を削除 (重複解消)。
    """
    promoted = 0
    deduped = 0
    failed = 0
    missing = 0
    for r in results:
        outcome = r.get("outcome")
        if outcome == "failed":
            failed += 1
            continue
        key = r.get("external_key")
        ref = r.get("external_ref")
        try:
            event_id = UUID(str(ref))
        except (ValueError, TypeError):
            missing += 1
            continue
        ev = await db.get(StaffEvent, event_id)
        if ev is None or not key:
            missing += 1
            continue
        existing = await db.scalar(
            select(StaffEvent.id).where(
                StaffEvent.source == EVENT_SOURCE_KAIPOKE,
                StaffEvent.external_id == str(key),
                StaffEvent.id != ev.id,
            )
        )
        if existing is not None:
            # 取込済みの同一予定が既に居る → manual 行は重複なので消す
            await db.delete(ev)
            deduped += 1
        else:
            ev.source = EVENT_SOURCE_KAIPOKE
            ev.external_id = str(key)
            promoted += 1
    await db.flush()
    return {"promoted": promoted, "deduped": deduped, "failed": failed, "missing": missing}
