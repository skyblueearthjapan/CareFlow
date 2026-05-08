"""W34: visits 重複論理削除スクリプト.

## 背景 (本番障害 2026-W34)

VPS 本番 DB で同一 ``(patient_id, visit_date, start_time)`` の visits 行が
複数積み上がる現象を観測した。例 (石塚 麻衣 / 2026-05-04 月曜):

    10:00 source=auto    <- Layer 1 が誤って 10:00 にも展開
    10:00 source=auto    <- 同 (p,d,t) が auto 重複
    10:00 source=manual  <- 手動配置の残骸
    10:15 source=manual
    10:15 source=manual  <- 同時刻 manual 重複
    11:30 source=auto    <- 正しい (固定枠由来)
    11:30 source=manual  <- 手動配置の残骸

本来は **auto 1 件 (固定枠由来)** + **ユーザーが意図的に追加した manual
複数件** のみで、(patient,date,start_time) が同じ行は最大 1 つに収束する
べき。Layer 1 の auto 削除漏れと UI 側の手動再投入が複合した結果と推定。

## このスクリプトの責務

``visits`` テーブルから ``(patient_id, visit_date, start_time)`` で
重複 (= 2 件以上) しているグループを抽出し、各グループから「残す 1 件」を
選んで残りを **論理削除 (deleted_at = now())** する。

「残す 1 件」のルール:
    1. ``source = 'manual'`` が 1 件以上 → manual の最古 1 件 (created_at 昇順)
    2. manual 0 件かつ ``source = 'auto'`` 複数 → auto の最古 1 件
    3. その他 (kaipoke / ai 等) → 最古 1 件

## 物理 vs 論理削除

本スクリプトは **論理削除 (soft delete)** のみを行う:
    - 監査ログ / 履歴の整合性 (``visit_staff_assignments`` 等の参照を保護)
    - 万一誤判定があった場合の復旧手段 (``deleted_at = NULL`` で戻せる)
    - migration 0026 の partial UNIQUE は ``deleted_at IS NULL`` が条件なので、
      論理削除した行は UNIQUE 違反にならない

## --dry-run / --apply

* ``--dry-run`` (default): 重複グループと削除対象行を表示するのみ
* ``--apply``           : 実際に ``deleted_at = now()`` を発行 (1 TX で commit)

冪等。再実行時は既に ``deleted_at IS NOT NULL`` の行は除外されるので
重複グループが解消されていれば何もしない。

## 注意 (デプロイ手順)

migration 0026 (visits partial UNIQUE) を本番に適用する **前に**
このスクリプト ``--apply`` を必ず実行すること。重複が残っていると
UNIQUE 違反でマイグレーションが失敗する。

Usage:
    python scripts/cleanup_visits_duplicate.py            # dry-run (default)
    python scripts/cleanup_visits_duplicate.py --apply    # actually soft-delete
"""

# ruff: noqa: I001
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import UUID

# Ensure backend/ is importable when invoked as `python scripts/cleanup_visits_duplicate.py`.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.models.visit import Visit  # noqa: E402


# 残す 1 件を選ぶときの source の優先度 (小さいほど優先).
# manual が最優先 (人手入力を尊重). auto は L1 で再生成可能なので後回し.
_SOURCE_PRIORITY: dict[str, int] = {
    "manual": 0,
    "kaipoke": 1,
    "import": 1,
    "ai": 2,
    "auto": 3,
}


def _source_rank(source: str | None) -> int:
    """source 優先度を返す. 未知の source は auto と同等扱い."""
    if source is None:
        return _SOURCE_PRIORITY["auto"]
    return _SOURCE_PRIORITY.get(source, _SOURCE_PRIORITY["auto"])


@dataclass
class _DuplicateGroup:
    """同一 ``(patient_id, visit_date, start_time)`` の重複グループ."""

    patient_id: UUID
    patient_name: str | None
    visit_date: date
    start_time: time
    visits: list[Visit] = field(default_factory=list)

    def select_keeper(self) -> Visit:
        """残す 1 件を選ぶ.

        優先順位:
            1. source の優先度 (manual < kaipoke < ai < auto)
            2. created_at の最古
            3. 念のため id (tie-breaker)
        """
        return min(
            self.visits,
            key=lambda v: (
                _source_rank(v.source),
                v.created_at or datetime.min.replace(tzinfo=UTC),
                str(v.id),
            ),
        )

    def to_delete(self) -> list[Visit]:
        keeper = self.select_keeper()
        return [v for v in self.visits if v.id != keeper.id]


@dataclass
class _Report:
    groups: int = 0
    kept: int = 0
    soft_deleted: int = 0


async def _scan_duplicates() -> list[_DuplicateGroup]:
    """``visits`` から (patient_id, visit_date, start_time) 重複グループを抽出.

    ``deleted_at IS NULL`` の行のみを対象とする (既に論理削除済みの行は
    対象外). 1 件しか無いグループは結果に含めない.
    """
    factory = get_session_factory()
    groups: list[_DuplicateGroup] = []
    async with factory() as session:
        # 重複している (patient_id, visit_date, start_time) を先に絞り込み
        dup_keys_q = (
            select(
                Visit.patient_id,
                Visit.visit_date,
                Visit.start_time,
                func.count(Visit.id).label("cnt"),
            )
            .where(Visit.deleted_at.is_(None))
            .group_by(Visit.patient_id, Visit.visit_date, Visit.start_time)
            .having(func.count(Visit.id) > 1)
            .order_by(Visit.visit_date, Visit.start_time, Visit.patient_id)
        )
        dup_rows = (await session.execute(dup_keys_q)).all()

        for patient_id, visit_date, start_time, _cnt in dup_rows:
            visits = list(
                await session.scalars(
                    select(Visit)
                    .where(
                        Visit.patient_id == patient_id,
                        Visit.visit_date == visit_date,
                        Visit.start_time == start_time,
                        Visit.deleted_at.is_(None),
                    )
                    .order_by(Visit.created_at)
                )
            )
            if len(visits) < 2:
                # 取得時点で 1 件しか残っていない (race) → skip
                continue

            patient_name = await session.scalar(
                select(Patient.name).where(Patient.id == patient_id)
            )
            groups.append(
                _DuplicateGroup(
                    patient_id=patient_id,
                    patient_name=patient_name,
                    visit_date=visit_date,
                    start_time=start_time,
                    visits=visits,
                )
            )
    return groups


async def _apply_soft_delete(groups: list[_DuplicateGroup], report: _Report) -> None:
    """全重複グループに対して残す 1 件以外を論理削除する (1 TX)."""
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        for group in groups:
            keeper = group.select_keeper()
            report.kept += 1
            for visit in group.visits:
                if visit.id == keeper.id:
                    continue
                # 同じ TX で再フェッチ (scan 時とは別セッションのため)
                target = await session.get(Visit, visit.id)
                if target is None or target.deleted_at is not None:
                    # 既に消えている / 論理削除済み
                    continue
                target.deleted_at = now
                report.soft_deleted += 1
        await session.commit()


def _format_group(g: _DuplicateGroup) -> str:
    keeper = g.select_keeper()
    weekday_jp = "月火水木金土日"[g.visit_date.weekday()]
    name = g.patient_name or "(unknown)"
    sources = [v.source or "?" for v in g.visits]
    return (
        f"  - {name} {g.visit_date} ({weekday_jp}) "
        f"{g.start_time.strftime('%H:%M')} "
        f"({len(g.visits)} 件 → 1 件残す、{len(g.visits) - 1} 件論理削除) "
        f"sources={sources} keep={keeper.source}/{keeper.id}"
    )


async def _main(*, apply: bool) -> int:
    report = _Report()
    try:
        groups = await _scan_duplicates()
        report.groups = len(groups)

        print("=== visits 重複クリーンアップ ===")
        if not groups:
            print("重複グループ: 0 組 — nothing to do.")
            return 0

        total_delete = sum(len(g.visits) - 1 for g in groups)
        print(f"重複グループ: {len(groups)} 組")
        print(f"削除対象 visit: {total_delete} 件")
        for g in groups:
            print(_format_group(g))

        if not apply:
            print()
            print("[dry-run] no changes applied. re-run with --apply to soft-delete.")
            print()
            print("[Summary]")
            print(f"- groups: {len(groups)}, kept: {len(groups)}, soft_deleted: 0 (dry-run)")
            return 0

        print()
        print("applying soft-deletes (single transaction)...")
        await _apply_soft_delete(groups, report)
    finally:
        await dispose_engine()

    print()
    print("[Summary]")
    print(f"- groups: {report.groups}, kept: {report.kept}, soft_deleted: {report.soft_deleted}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "W34: visits 重複 (patient_id, visit_date, start_time) の "
            "残す 1 件以外を論理削除する (本番 visits 重複恒久対策の (A))."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="(default) print duplicate groups only; no UPDATE",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="actually soft-delete (deleted_at = now()) duplicates",
    )
    args = parser.parse_args()
    return asyncio.run(_main(apply=bool(args.apply)))


if __name__ == "__main__":
    raise SystemExit(main())
