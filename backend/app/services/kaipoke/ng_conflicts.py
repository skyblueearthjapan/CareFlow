"""カイポケ取込 dry-run の NG スタッフ衝突警告 (Phase 3).

正典設計書: ``docs/plans/patient-ng-staff-design.md`` §6 末尾 / §11。

**カイポケは請求と紐づく最終的な「正」** であり、らく助はそれを受け入れる
(2026-07-26 PO確定)。したがって NG スタッフ (``patient_ng_staff``) に該当する
組み合わせが取り込まれても **ブロックしない**。dry-run (プレビュー) の段階で
「この取り込みを実行すると NG の組が生まれます」とだけ可視化し、現場が
カイポケ側を直すか NG 設定を見直すかを判断できるようにする。

* 取り込みの **適用 (apply) 側の挙動は一切変えない**。集計は dry-run のときだけ
  行う (呼出側が ``dry_run`` で分岐する)。
* N+1 禁止: NG 行 / 患者名 / スタッフ名をそれぞれ 1 クエリで一括ロードする。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.patient import Patient
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 取込計画から抜き出した「この日、この患者をこのスタッフが訪問する」1 組。
# (patient_id, staff_id, 訪問日, コース記号 or None)。コース記号は臨時コースを
# dry-run 時点で確定できない経路 (未作成) では None になる。
NgPair = tuple[uuid.UUID, uuid.UUID, date, str | None]


@dataclass(frozen=True)
class NgConflict:
    """取込適用後に生まれる NG ペア 1 件 (警告のみ・ブロックしない)。"""

    patient_id: uuid.UUID
    patient_name: str
    staff_id: uuid.UUID
    staff_name: str
    target_date: date
    weekday: int
    course_code: str | None


async def collect_ng_conflicts(
    db: AsyncSession,
    pairs: Iterable[NgPair],
) -> list[NgConflict]:
    """取込計画の (患者 × スタッフ) 組から NG 該当分だけを列挙する。

    同一の (患者, スタッフ, 日, コース) は 1 件にまとめる。並びは
    (日付, 患者名, スタッフ名) の決定論的順序。
    """
    unique_pairs = sorted(set(pairs), key=lambda p: (p[2], str(p[0]), str(p[1]), p[3] or ""))
    if not unique_pairs:
        return []

    patient_ids = {p[0] for p in unique_pairs}
    staff_ids = {p[1] for p in unique_pairs}

    ng_rows = (
        await db.execute(
            select(PatientNgStaff.patient_id, PatientNgStaff.staff_id).where(
                PatientNgStaff.patient_id.in_(patient_ids),
                PatientNgStaff.staff_id.in_(staff_ids),
            )
        )
    ).all()
    ng_set = {(pid, sid) for pid, sid in ng_rows}
    if not ng_set:
        return []

    hit_pairs = [p for p in unique_pairs if (p[0], p[1]) in ng_set]
    patient_names = dict(
        (
            await db.execute(
                select(Patient.id, Patient.name).where(Patient.id.in_({p[0] for p in hit_pairs}))
            )
        ).all()
    )
    staff_names = dict(
        (
            await db.execute(
                select(Staff.id, Staff.name).where(Staff.id.in_({p[1] for p in hit_pairs}))
            )
        ).all()
    )

    conflicts = [
        NgConflict(
            patient_id=pid,
            patient_name=patient_names.get(pid, ""),
            staff_id=sid,
            staff_name=staff_names.get(sid, ""),
            target_date=d,
            weekday=d.weekday(),
            course_code=code,
        )
        for pid, sid, d, code in hit_pairs
    ]
    conflicts.sort(key=lambda c: (c.target_date, c.patient_name, c.staff_name))
    return conflicts
