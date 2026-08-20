"""毎週の固定イベント既定の週展開 (kaipoke-event-two-way-design.md §3-②).

`accompaniment.expand_accompaniment_defaults` と同じ作法:
  - **冪等・週全体走査・commit しない (flush のみ)**
  - 休職・退職スタッフの既定は展開しない (展開ゲートはこの 1 箇所)
  - 呼び出し = 週生成 / 割付のみ / 固定枠戻し の 3 地点 (best-effort)

冪等判定は 2 段:
  1. `external_id = "{default_id}:{YYYY-MM-DD}"` (source='fixed') の既存行
  2. **内容一致** (同 staff×日×時刻×名称) の既存行 — source 不問。
     カイポケへ送信すると fixed 行は source='kaipoke' へ昇格し fixed キーが
     空くため、キーだけで判定すると再展開で二重になる。取込で同内容が
     入ってくるケースも同様にこれで吸収する。

制約 (既知・設計に明記): 展開済みイベントを手で削除しても、既定が残って
いれば次の週生成で復活する。恒久的に止めるには既定そのものを削除する。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.staff import Staff, StaffEvent, StaffEventDefault

EVENT_SOURCE_FIXED = "fixed"


def _week_monday(iso_year: int, iso_week: int) -> date:
    return date.fromisocalendar(iso_year, iso_week, 1)


async def expand_staff_event_defaults(db: AsyncSession, iso_year: int, iso_week: int) -> int:
    """当該週の固定イベントを staff_events へ物質化する。返却 = 新規作成数。"""
    defaults = list(
        (
            await db.scalars(
                select(StaffEventDefault)
                .join(Staff, StaffEventDefault.staff_id == Staff.id)
                .where(
                    # 休職・退職スタッフの既定は展開しない (同行既定と同じゲート)
                    Staff.status == "active",
                    Staff.deleted_at.is_(None),
                )
            )
        ).all()
    )
    if not defaults:
        return 0

    monday = _week_monday(iso_year, iso_week)
    range_start = datetime.combine(monday, time.min)
    range_end = datetime.combine(monday + timedelta(days=7), time.min)

    # 当該週の既存イベントを 1 度だけロードして冪等判定に使う
    existing = list(
        (
            await db.scalars(
                select(StaffEvent).where(
                    StaffEvent.starts_at >= range_start,
                    StaffEvent.starts_at < range_end,
                )
            )
        ).all()
    )
    existing_keys: set[str] = {
        e.external_id for e in existing if e.source == EVENT_SOURCE_FIXED and e.external_id
    }
    # 内容一致キー (source 不問): staff × 開始 × 終了 × 正規化タイトル
    content_keys: set[tuple] = {
        (e.staff_id, e.starts_at, e.ends_at, (e.title or "").strip()) for e in existing
    }

    created = 0
    for d in defaults:
        if not (0 <= d.weekday <= 5):
            continue  # 日曜(6)・不正値は展開しない (API 側でも 0-5 に制限)
        target = monday + timedelta(days=d.weekday)
        key = f"{d.id}:{target.isoformat()}"
        if key in existing_keys:
            continue
        starts_at = datetime.combine(target, d.start_time)
        ends_at = datetime.combine(target, d.end_time)
        if (d.staff_id, starts_at, ends_at, d.title.strip()) in content_keys:
            continue  # 昇格済み/取込済みの同内容が既に居る
        db.add(
            StaffEvent(
                staff_id=d.staff_id,
                event_type="event",
                starts_at=starts_at,
                ends_at=ends_at,
                title=d.title,
                note=d.note,
                source=EVENT_SOURCE_FIXED,
                external_id=key,
                blocking=d.blocking,
            )
        )
        existing_keys.add(key)
        content_keys.add((d.staff_id, starts_at, ends_at, d.title.strip()))
        created += 1

    if created:
        await db.flush()
    return created
