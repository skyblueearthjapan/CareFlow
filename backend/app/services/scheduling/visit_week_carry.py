"""固定訪問の行を作り直すとき、訪問する週 (``visit_week``・0102) を引き継ぐ.

正典: ``docs/plans/biweekly-monthly-design-2026-10-30.md``。
固定訪問の行を消して作り直す・新しい曜日の行を足す経路 (患者マスタの保存・週から固定へ
写す・申請の取り込み・提案の採用・プール投入など) は、ここの決まりで値を決める。
作り直しで 'every' に戻ると、隔週の A/B が黙って消えるため。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.scheduling.visit_frequency import SLOT_EVERY, normalize_slot_visit_week


def visit_week_by_slot_of(rows: Iterable[Any]) -> dict[tuple[int, int], str]:
    """固定訪問の行 (またはその形のもの) を ``(曜日, slot) → visit_week`` にする."""
    return {
        (int(r.weekday), int(getattr(r, "slot_index", 0) or 0)): normalize_slot_visit_week(
            getattr(r, "visit_week", None)
        )
        for r in rows
    }


async def load_visit_week_by_slot(
    db: AsyncSession, *, patient_id: UUID, mode: str = "normal"
) -> dict[tuple[int, int], str]:
    """(weekday, slot_index) → 訪問する週. 行を置き換えても A/B を引き継ぐために使う."""
    rows = (
        await db.execute(
            select(
                PatientFixedVisit.weekday,
                PatientFixedVisit.slot_index,
                PatientFixedVisit.visit_week,
            ).where(
                PatientFixedVisit.patient_id == patient_id,
                PatientFixedVisit.mode == mode,
            )
        )
    ).all()
    return {(wd, si): normalize_slot_visit_week(vw) for wd, si, vw in rows}


def carried_visit_week(
    visit_week_by_slot: dict[tuple[int, int], str], *, weekday: int, slot_index: int
) -> str:
    """作り直す・足す枠に引き継ぐ訪問する週.

    1. 同じ (曜日, slot) の既存行があれば、その値。
    2. 無い (曜日を動かした・新しい曜日) ときは、同じ slot の既存行がすべて同じ値なら
       その値 (例: 枠が全部 B の方の B の枠を別の曜日へ動かしても B のまま)。
    3. 同じ slot の既存行の値がばらばら (A と B が混ざる) か、既存行が無いときは 'every'
       (どちらとも決められないので毎回にし、患者マスタで選び直してもらう)。
    """
    exact = visit_week_by_slot.get((weekday, slot_index))
    if exact is not None:
        return exact
    same_slot = {vw for (_wd, si), vw in visit_week_by_slot.items() if si == slot_index}
    if len(same_slot) == 1:
        return next(iter(same_slot))
    return SLOT_EVERY


__all__ = ["carried_visit_week", "load_visit_week_by_slot", "visit_week_by_slot_of"]
