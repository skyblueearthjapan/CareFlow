"""取込で取り消された訪問に残った打刻を、同じ日の生きている訪問へ付け替える (一回限り).

## 背景 (2026-09-23 の事故・docs/plans/inbound-fixes-2026-10-01.md §6)

カイポケ取込の delete に「打刻済みなら取り消さない」ガードが無かったため、打刻の
付いた訪問が取り消され、カイポケどおりの別の訪問 (打刻なし) が同じ日に残った。
モニターからは実績が消え、生きている訪問は「未訪問」に見える。
取込側は 2026-10-01 に根治した (打刻済みの訪問は取り消さない)。このスクリプトは
**既に起きてしまった分**を探し、打刻を生きている訪問へ移す。

## 対象の見つけ方

1. ``status='cancelled'`` かつ論理削除されていない訪問のうち、打刻 (visit_checkins) が
   1 件以上あるもの
2. 同じ患者・同じ日に、生きている (cancelled でない・論理削除されていない) 訪問で
   **打刻が 1 件も無い** もの

候補がちょうど 1 件なら自動で組にする。0 件・2 件以上は「要確認」として表示だけ
する (2 件以上は ``--pair 取消ID=移動先ID`` で人が指定したときだけ移す)。

## 移すもの

打刻 (visit_checkins)・写真 (visit_photos)・時刻の調整 (visit_time_adjustments)・
録音 (visit_recordings) の ``visit_id``。レビュー (visit_reviews・visit_id は一意) は
移動先にまだ無いときだけ移す。移動先の状態は、退出の打刻があれば ``completed``・
到着だけなら ``in_progress`` に進める (planned / in_progress のときだけ)。
両方の訪問の note に付け替えの記録を残す。移動先の「未訪問」通知は解消する。

## 使い方 (既定は dry-run・書き込みなし)

    python scripts/reattach_cancelled_checkins.py                       # 一覧のみ
    python scripts/reattach_cancelled_checkins.py --from 2026-09-21 --to 2026-09-27
    python scripts/reattach_cancelled_checkins.py --apply               # 自動の組だけ移す
    python scripts/reattach_cancelled_checkins.py --apply --pair <取消ID>=<移動先ID>

``--apply`` は 1 トランザクションで commit する。実行前に DB のバックアップを取ること。
本番で流す前に PO に確認する (handoff §4-2 #6「PO に一言確認してから」)。
"""

# ruff: noqa: I001
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select, update  # noqa: E402

from app.models.patient import Patient  # noqa: E402
from app.models.staff import Staff  # noqa: E402
from app.models.visit import (  # noqa: E402
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_COMPLETED,
    VISIT_STATUS_IN_PROGRESS,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_checkin import VisitCheckin  # noqa: E402
from app.models.visit_photo import VisitPhoto  # noqa: E402
from app.models.visit_recording import VisitRecording  # noqa: E402
from app.models.visit_review import VisitReview  # noqa: E402
from app.models.visit_time_adjustment import VisitTimeAdjustment  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
NOTE_PREFIX = "打刻の付け替え"


@dataclass
class Case:
    """取り消された打刻付き訪問 1 件と、付け替え先の候補。"""

    cancelled: Visit
    patient_name: str
    checkins: list[VisitCheckin]
    candidates: list[Visit] = field(default_factory=list)
    target: Visit | None = None  # 自動 or --pair で決まった移動先

    @property
    def status(self) -> str:
        if self.target is not None:
            return "ready"
        if not self.candidates:
            return "no_candidate"
        return "ambiguous"


def _hm(v: Visit) -> str:
    return v.start_time.strftime("%H:%M")


async def find_cases(
    db, *, date_from: date | None = None, date_to: date | None = None
) -> list[Case]:
    """取り消された打刻付き訪問と、同じ患者・同じ日の打刻なし訪問を探す (読み取りのみ)。"""
    stmt = (
        select(Visit)
        .join(VisitCheckin, VisitCheckin.visit_id == Visit.id)
        .where(Visit.status == VISIT_STATUS_CANCELLED, Visit.deleted_at.is_(None))
        .distinct()
    )
    if date_from is not None:
        stmt = stmt.where(Visit.visit_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Visit.visit_date <= date_to)
    cancelled = sorted((await db.scalars(stmt)).all(), key=lambda v: (v.visit_date, v.start_time))

    cases: list[Case] = []
    for cv in cancelled:
        checkins = list(
            (
                await db.scalars(
                    select(VisitCheckin)
                    .where(VisitCheckin.visit_id == cv.id)
                    .order_by(VisitCheckin.scanned_at)
                )
            ).all()
        )
        live = (
            await db.scalars(
                select(Visit).where(
                    Visit.patient_id == cv.patient_id,
                    Visit.visit_date == cv.visit_date,
                    Visit.status != VISIT_STATUS_CANCELLED,
                    Visit.deleted_at.is_(None),
                    Visit.id != cv.id,
                )
            )
        ).all()
        candidates = []
        for lv in sorted(live, key=lambda v: v.start_time):
            n = await db.scalar(select(VisitCheckin.id).where(VisitCheckin.visit_id == lv.id))
            if n is None:
                candidates.append(lv)
        name = await db.scalar(select(Patient.name).where(Patient.id == cv.patient_id)) or "?"
        case = Case(cancelled=cv, patient_name=name, checkins=checkins, candidates=candidates)
        if len(candidates) == 1:
            case.target = candidates[0]
        cases.append(case)
    return cases


def apply_pairs(cases: list[Case], pairs: dict[UUID, UUID]) -> list[str]:
    """``--pair 取消ID=移動先ID`` を候補の中から当てる。当たらなければエラー文を返す。"""
    errors: list[str] = []
    by_id = {c.cancelled.id: c for c in cases}
    for cid, tid in pairs.items():
        case = by_id.get(cid)
        if case is None:
            errors.append(f"--pair: 取消済みの打刻付き訪問 {cid} が見つかりません")
            continue
        target = next((v for v in case.candidates if v.id == tid), None)
        if target is None:
            errors.append(f"--pair: {tid} は {cid} の付け替え先の候補ではありません")
            continue
        case.target = target
    return errors


async def reattach(db, case: Case, *, now: datetime) -> dict[str, int]:
    """1 件分を付け替える (commit は呼び出し側)。移した行数を返す。"""
    assert case.target is not None
    src, dst = case.cancelled.id, case.target.id
    moved: dict[str, int] = {}
    for label, model in (
        ("checkins", VisitCheckin),
        ("photos", VisitPhoto),
        ("time_adjustments", VisitTimeAdjustment),
        ("recordings", VisitRecording),
    ):
        res = await db.execute(update(model).where(model.visit_id == src).values(visit_id=dst))
        moved[label] = int(res.rowcount or 0)
    # レビューは visit_id が一意。移動先にまだ無いときだけ移す。
    has_dst_review = await db.scalar(select(VisitReview.id).where(VisitReview.visit_id == dst))
    if has_dst_review is None:
        res = await db.execute(
            update(VisitReview).where(VisitReview.visit_id == src).values(visit_id=dst)
        )
        moved["reviews"] = int(res.rowcount or 0)
    else:
        moved["reviews"] = 0

    target = case.target
    if any(c.kind == "departure" for c in case.checkins):
        if target.status in (VISIT_STATUS_PLANNED, VISIT_STATUS_IN_PROGRESS):
            target.status = VISIT_STATUS_COMPLETED
    elif target.status == VISIT_STATUS_PLANNED:
        target.status = VISIT_STATUS_IN_PROGRESS

    stamp = f"{NOTE_PREFIX} {now.astimezone(JST):%m/%d}"
    target_line = f"{stamp}: 取消済みの訪問 {_hm(case.cancelled)} の打刻をこの訪問へ移しました"
    cancelled_line = f"{stamp}: 打刻をこの日の訪問 {_hm(target)} へ移しました（取込の取消のため）"
    target.note = f"{target.note}\n{target_line}" if target.note else target_line
    cv = case.cancelled
    cv.note = f"{cv.note}\n{cancelled_line}" if cv.note else cancelled_line

    from app.services.checkin.notify import resolve_checkin_missing

    await resolve_checkin_missing(db, target.id)
    await db.flush()
    return moved


async def _staff_name(db, sid: UUID | None) -> str:
    if sid is None:
        return "担当なし"
    return await db.scalar(select(Staff.name).where(Staff.id == sid)) or "?"


async def describe(db, case: Case) -> str:
    cv = case.cancelled
    reads = "・".join(
        f"{'到着' if c.kind == 'arrival' else '退出'} {c.scanned_at.astimezone(JST):%H:%M}"
        for c in case.checkins
    )
    head = (
        f"- {cv.visit_date} {case.patient_name} 取消 {_hm(cv)}"
        f"（{await _staff_name(db, cv.primary_staff_id)}・{cv.id}）打刻: {reads}"
    )
    if case.status == "ready" and case.target is not None:
        t = case.target
        return (
            f"{head}\n    → 移動先 {_hm(t)}（{await _staff_name(db, t.primary_staff_id)}"
            f"・{t.status}・{t.id}）"
        )
    if case.status == "no_candidate":
        return f"{head}\n    → 要確認: 同じ日に打刻の無い訪問がありません（移しません）"
    lines = [f"{head}\n    → 要確認: 候補が複数あります（--pair で指定してください）"]
    for v in case.candidates:
        lines.append(f"      候補 {_hm(v)}（{await _staff_name(db, v.primary_staff_id)}・{v.id}）")
    return "\n".join(lines)


def _parse_pairs(raw: list[str]) -> dict[UUID, UUID]:
    pairs: dict[UUID, UUID] = {}
    for item in raw:
        left, sep, right = item.partition("=")
        if not sep:
            raise SystemExit(f"--pair の形式は 取消ID=移動先ID です: {item}")
        pairs[UUID(left.strip())] = UUID(right.strip())
    return pairs


async def _main(args: argparse.Namespace) -> int:
    from app.db.session import dispose_engine, get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as db:
            cases = await find_cases(db, date_from=args.date_from, date_to=args.date_to)
            errors = apply_pairs(cases, _parse_pairs(args.pair or []))
            print("=== 取り消された訪問に残った打刻 ===")
            if not cases:
                print("該当なし — nothing to do.")
                return 0
            for case in cases:
                print(await describe(db, case))
            for e in errors:
                print(f"[error] {e}")
            if errors:
                return 2
            ready = [c for c in cases if c.status == "ready"]
            print(f"\n件数: {len(cases)}（移せる {len(ready)}・要確認 {len(cases) - len(ready)}）")
            if not args.apply:
                print("[dry-run] 書き込みはしていません。移すときは --apply を付けてください。")
                return 0
            now = datetime.now(UTC)
            for case in ready:
                moved = await reattach(db, case, now=now)
                print(f"applied {case.cancelled.id} -> {case.target.id}: {moved}")  # type: ignore[union-attr]
            await db.commit()
            print(f"[apply] {len(ready)} 件を付け替えました（1 トランザクションで commit）。")
    finally:
        await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="取込で取り消された訪問に残った打刻を、同じ日の生きている訪問へ付け替える"
    )
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, default=None)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--pair",
        action="append",
        help="候補が複数あるときの指定 (取消ID=移動先ID・複数可)",
    )
    parser.add_argument(
        "--apply", action="store_true", help="実際に付け替える (既定は dry-run・書き込みなし)"
    )
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
