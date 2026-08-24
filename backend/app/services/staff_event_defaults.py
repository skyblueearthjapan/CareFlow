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

休み連携 (staff-event-history-design.md §2 Phase 3・PO Q3「自動で不参加」):
その (スタッフ × 対象日) が休みなら展開しない。休みの定義は盤面 / Layer3 /
提案エンジンと **同じソース** を使う (`_load_off_keys` の docstring 参照)。
後から休みになった場合は「休みにする」(staff-off-week) 側が取消印を付ける。

制約 (既知・設計に明記): 展開済みイベントを手で削除しても、既定が残って
いれば次の週生成で復活する。恒久的に止めるには既定そのものを削除する。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.staff import (
    Staff,
    StaffEvent,
    StaffEventDefault,
    StaffShift,
    StaffWeeklyOverride,
)

EVENT_SOURCE_FIXED = "fixed"
# staff_weekly_overrides.override_type の「終日休み」。layer3_assignment /
# propose_slots_service / staff-off-week と同じ値 (DB 正典)。
STAFF_OVERRIDE_TYPE_OFF = "off"


def _week_monday(iso_year: int, iso_week: int) -> date:
    return date.fromisocalendar(iso_year, iso_week, 1)


async def _load_off_keys(
    db: AsyncSession, staff_ids: set[uuid.UUID], iso_year: int, iso_week: int
) -> set[tuple[uuid.UUID, int]]:
    """当該週の「休み」の (staff_id, weekday) 集合を返す (Phase 3 休み連携).

    休みの定義はこのコードベースの既存の正をそのまま使う — 独自定義はしない:

      1. ``StaffShift.is_on = False`` の曜日 = 週間シフトで休みと明示された曜日
         (`layer3_assignment._load_active_staff` / `propose_slots_service` の N-3)。
         **行が無い曜日は休みとみなさない**: 提案系は「シフト未登録=非番」に
         倒しているが、ここでそれを採ると shift を 1 行も持たないスタッフの
         固定イベントが一切展開されなくなる (既存挙動の破壊) ため、
         「明示的に休みと書かれている」場合だけを見る。
      2. ``StaffWeeklyOverride.override_type = 'off'`` の当該週の曜日
         = その週だけの休み。モバイル休み申請 (pending_request ``staff_off``)・
         PC のスタッフ別休み・運転席の「🛌 休みにする」はいずれも最終的に
         この行を書く (= 3 経路とも 1 箇所で拾える)。

    ``custom_time`` (時間変更) / ``am_off`` / ``pm_off`` (半休) は終日休みでは
    ないため対象外 (エンジン側も同じ扱い)。
    """
    if not staff_ids:
        return set()

    off_keys: set[tuple[uuid.UUID, int]] = set()
    shift_rows = await db.execute(
        select(StaffShift.staff_id, StaffShift.weekday).where(
            StaffShift.staff_id.in_(staff_ids),
            StaffShift.is_on.is_(False),
        )
    )
    off_keys.update((sid, wd) for sid, wd in shift_rows.all())

    override_rows = await db.execute(
        select(StaffWeeklyOverride.staff_id, StaffWeeklyOverride.weekday).where(
            StaffWeeklyOverride.staff_id.in_(staff_ids),
            StaffWeeklyOverride.iso_year == iso_year,
            StaffWeeklyOverride.iso_week == iso_week,
            StaffWeeklyOverride.override_type == STAFF_OVERRIDE_TYPE_OFF,
        )
    )
    off_keys.update((sid, wd) for sid, wd in override_rows.all())
    return off_keys


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

    # 休みの日は展開しない (PO Q3: 自動で不参加)。既定を持つスタッフぶんだけ引く。
    off_keys = await _load_off_keys(db, {d.staff_id for d in defaults}, iso_year, iso_week)

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
        if (d.staff_id, d.weekday) in off_keys:
            continue  # その日は休み → 展開しない (Phase 3 休み連携)
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
