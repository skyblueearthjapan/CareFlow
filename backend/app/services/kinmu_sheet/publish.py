"""勤務表を作って ``kinmu_sheets`` に記録する (予定版・確定版・設計 §7-2・#13).

* 予定版 (``plan``): 「この月を確定」で作る。作ったときの職員×日の休みと型の写し
  (``snapshot_plan``) を ``plan_snapshot`` に置く (確定版の「予定」の行はこの写し)。
* 確定版 (``final``): 「実績を記録する」で作る。予定は最新の予定版の写し、実績はいまの休み。
* 作り直すたびに新しい版 (``version`` を 1 つ増やす・別ファイル・前の版は残す)。

Google ドライブに繋がっていない・繋ぎ直しが要る・勤務すべき時間数が未入力などで
ファイルを作れないときも、行は「未作成」(``url`` が空) として残し、理由を返す
(確定・実績の記録は止めない・§7-2 の 5)。**commit しない** (呼び出し側が commit する。
繋ぎ直しが要るときの ``needs_reconnect`` と通知も同じ commit で残る)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leave import KinmuSheet
from app.services.google_drive import GoogleDriveError, create_spreadsheet
from app.services.kinmu_sheet.build import (
    build_holiday_summary,
    build_kinmu_month,
    snapshot_plan,
)
from app.services.kinmu_sheet.load import load_holiday_counts_by_month, load_kinmu_inputs
from app.services.kinmu_sheet.sheet import to_sheet_data

logger = logging.getLogger(__name__)

KinmuKind = Literal["plan", "final"]
_KIND_LABEL: Final = {"plan": "予定", "final": "確定"}
MSG_DRIVE_FAILED: Final = "勤務表を Google ドライブに作れませんでした"


@dataclass
class KinmuSheetOutcome:
    """作った (または「未作成」で残した) 勤務表の行と、作れなかった理由."""

    sheet: KinmuSheet
    created: bool
    reason: str | None = None


def sheet_title(month: date, kind: KinmuKind, version: int) -> str:
    """``勤務形態一覧表_2026年11月_予定`` (第 2 版から ``_第2版``)."""
    base = f"勤務形態一覧表_{month.year}年{month.month:02d}月_{_KIND_LABEL[kind]}"
    return base if version == 1 else f"{base}_第{version}版"


async def _next_version(db: AsyncSession, month: date, kind: KinmuKind) -> int:
    current = await db.scalar(
        select(func.max(KinmuSheet.version)).where(
            KinmuSheet.month == month, KinmuSheet.kind == kind
        )
    )
    return int(current or 0) + 1


async def create_kinmu_sheet(
    db: AsyncSession,
    *,
    month: date,
    kind: KinmuKind,
    user_id: UUID | None,
    reason: str | None = None,
) -> KinmuSheetOutcome:
    """その月の勤務表を新しい版で作り、``kinmu_sheets`` に行を足す (commit しない).

    同じ種類の最新の版が「未作成」(ファイルが無い) なら、新しい行は足さずにその行で作り直す
    (確定のたびに未作成の行が増えないように・ドライブにつないだ後の「作る」)。
    """
    first = month.replace(day=1)
    inputs = await load_kinmu_inputs(db, first, with_actual=kind == "final")
    snapshot = snapshot_plan(inputs.staff) if kind == "plan" else None
    latest = await db.scalar(
        select(KinmuSheet)
        .where(KinmuSheet.month == first, KinmuSheet.kind == kind)
        .order_by(KinmuSheet.version.desc())
        .limit(1)
        # 同時に「未作成」の行を使い直すと、ドライブのファイルが 2 つできる。行を鍵で押さえ、
        # 後から来た方は先の commit を待って (作り済みなら) 新しい版にする。
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if latest is not None and latest.drive_file_id is None:
        row = latest
        row.plan_snapshot = snapshot
        row.reason = reason or row.reason
        row.created_by = user_id
    else:
        row = KinmuSheet(
            month=first,
            kind=kind,
            version=await _next_version(db, first, kind),
            reason=reason,
            plan_snapshot=snapshot,
            created_by=user_id,
        )
        db.add(row)
    version = row.version
    await db.flush()
    try:
        km = build_kinmu_month(first, inputs.staff, inputs.required_hours, inputs.closed_days)
        summary = build_holiday_summary(first, await load_holiday_counts_by_month(db, first))
        sheets = to_sheet_data(km, summary)
    except ValueError as exc:
        # 勤務すべき時間数が未入力など (勤務表の形を作れない)。
        return KinmuSheetOutcome(sheet=row, created=False, reason=str(exc))
    try:
        file_id, url = await create_spreadsheet(db, sheet_title(first, kind, version), None, sheets)
    except GoogleDriveError as exc:
        # 未接続・繋ぎ直しが要る (status と通知は済み・同じ commit で残す)・フォルダが無いなど。
        return KinmuSheetOutcome(sheet=row, created=False, reason=exc.message)
    except Exception:  # noqa: BLE001 — 勤務表が作れなくても確定・実績の記録は止めない
        logger.exception("kinmu sheet %s %s v%s failed", first, kind, version)
        return KinmuSheetOutcome(sheet=row, created=False, reason=MSG_DRIVE_FAILED)
    row.drive_file_id = file_id
    row.url = url
    await db.flush()
    return KinmuSheetOutcome(sheet=row, created=True)


__all__ = ["MSG_DRIVE_FAILED", "KinmuSheetOutcome", "create_kinmu_sheet", "sheet_title"]
