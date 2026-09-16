"""手入力イベント × カイポケ取込イベントの二重行を片付ける一回限りの整理スクリプト。

背景 (2026-09-16 本番実測): イベント取込は external_id だけで突合し source='kaipoke'
の行しか見ていなかったため、8/24 に UI で手入力した「朝会」(source='manual'・
external_id NULL) と 9/11・9/15 の取込で入った「朝会」(source='kaipoke') が
ほぼ全職員で同時刻に 2 行並んだ (W38 で 26 組)。未送信サマリにも手入力行が
「未送信イベント」として出続ける。

取込側の根治は `backend/app/services/kaipoke/events_inbound.py` の吸収 (absorb)。
これは **既に二重になってしまった分** を消すための後始末で、以後は不要。

突合キーは吸収と同じ = 同一 staff × 同一 starts_at × 正規化タイトル
(NFKC → 空白全除去)。manual 行と kaipoke 行が併存する組だけを対象にする。

使い方 (backend コンテナ内):

    docker compose cp docs/tools/kaipoke-ops/dedupe_manual_events.py backend:/tmp/
    docker compose exec backend sh -lc \\
      'cd /app && PYTHONPATH=. python /tmp/dedupe_manual_events.py --from 2026-09-07 --to 2026-10-04'
    # 内容を確認してから
    docker compose exec backend sh -lc \\
      'cd /app && PYTHONPATH=. python /tmp/dedupe_manual_events.py --from 2026-09-07 --to 2026-10-04 --apply'

既定は dry-run (週別件数 + 明細を表示するだけ・**ファイルは一切書かない**)。
`--apply` で manual 側を削除する。staff_events に deleted_at 列は無いため物理削除
だが、**削除前に必ず JSON へ書き出す** (既定 /tmp/dedupe_manual_events_backup_
{UTC日時}.json・`--backup` で変更可。`--backup` を明示したパスが既に在る場合は
上書きせず中止する)。
manual 側にだけ「今週だけ外す」の取消印 (cancelled_at) や占有フラグ (blocking)
が付いている場合は、削除する前に kaipoke 側へ移す (人が入れた意味を落とさない)。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import unicodedata
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.staff import Staff, StaffEvent

KAIPOKE = "kaipoke"
MANUAL = "manual"


def normalize_title_key(title: str | None) -> str:
    """events_inbound.normalize_title_key と同一規則 (NFKC → 空白全除去)。"""
    if not title:
        return ""
    return "".join(unicodedata.normalize("NFKC", title).split())


def naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def row_to_json(row: StaffEvent, staff_name: str) -> dict:
    """復元用のダンプ (INSERT を手で組み直せるだけの情報を残す)。"""
    return {
        "id": str(row.id),
        "staff_id": str(row.staff_id),
        "staff_name": staff_name,
        "event_type": row.event_type,
        "starts_at": row.starts_at.isoformat(),
        "ends_at": row.ends_at.isoformat(),
        "title": row.title,
        "note": row.note,
        "source": row.source,
        "external_id": row.external_id,
        "blocking": row.blocking,
        "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
    }


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="date_from", required=True, help="開始日 YYYY-MM-DD (含む)")
    p.add_argument("--to", dest="date_to", required=True, help="終了日 YYYY-MM-DD (含む)")
    p.add_argument("--apply", action="store_true", help="実際に削除する (既定は dry-run)")
    p.add_argument("--backup", default=None, help="削除対象の JSON 出力先")
    args = p.parse_args()

    d_from = date.fromisoformat(args.date_from)
    d_to = date.fromisoformat(args.date_to)
    if d_from > d_to:
        p.error(f"--from ({d_from}) が --to ({d_to}) より後です")
    # --backup を明示したのに既存ファイルが在る = 前回のダンプを潰す事故。先に止める。
    if args.backup and os.path.exists(args.backup):
        p.error(f"--backup の出力先が既に存在します: {args.backup}")
    range_start = datetime.combine(d_from, time.min)
    range_end = datetime.combine(d_to + timedelta(days=1), time.min)

    factory = get_session_factory()
    async with factory() as db:
        staff_names = dict(
            (await db.execute(select(Staff.id, Staff.name))).all()  # type: ignore[arg-type]
        )
        rows = list(
            (
                await db.scalars(
                    select(StaffEvent)
                    .where(
                        StaffEvent.starts_at >= range_start,
                        StaffEvent.starts_at < range_end,
                    )
                    # 出力と「残す 1 行」の選び方を実行ごとにぶれさせない。
                    .order_by(StaffEvent.starts_at, StaffEvent.external_id)
                )
            ).all()
        )

        # (staff_id, 開始日時(naive UTC), 正規化タイトル) → source 別の行
        buckets: dict[tuple, dict[str, list[StaffEvent]]] = defaultdict(
            lambda: {MANUAL: [], KAIPOKE: []}
        )
        for r in rows:
            if r.source not in (MANUAL, KAIPOKE):
                continue  # fixed 行は週生成の冪等に使われるため触らない
            if r.source == MANUAL and r.external_id is not None:
                continue
            key = (r.staff_id, naive_utc(r.starts_at), normalize_title_key(r.title))
            buckets[key][r.source].append(r)

        victims: list[tuple[StaffEvent, StaffEvent]] = []  # (消す manual, 残す kaipoke)
        for (_sid, _sa, _t), by_source in buckets.items():
            keepers = by_source[KAIPOKE]
            if not keepers or not by_source[MANUAL]:
                continue
            # 同キーに kaipoke 行が複数居ても「残す 1 行」を external_id で固定する
            # (dry-run の出力と --apply の結果を一致させるため)。
            keep = sorted(keepers, key=lambda r: r.external_id or "")[0]
            for dup in by_source[MANUAL]:
                victims.append((dup, keep))

        if not victims:
            print(f"対象なし ({d_from} 〜 {d_to})")
            return

        per_week: dict[date, int] = defaultdict(int)
        for dup, _keep in victims:
            per_week[week_monday(naive_utc(dup.starts_at).date())] += 1

        print(f"=== 二重行 (manual × kaipoke) {len(victims)} 件 / {d_from} 〜 {d_to} ===")
        print("--- 週別 ---")
        for mon in sorted(per_week):
            print(f"  {mon.isoformat()} の週: {per_week[mon]} 件")
        print("--- 明細 (消す manual 行) ---")
        for dup, keep in sorted(victims, key=lambda v: (naive_utc(v[0].starts_at), str(v[0].id))):
            sa = naive_utc(dup.starts_at)
            ea = naive_utc(dup.ends_at)
            marks = ""
            if dup.cancelled_at is not None and keep.cancelled_at is None:
                marks += " [取消印を移設]"
            if dup.blocking and not keep.blocking:
                marks += " [占有フラグを移設]"
            print(
                f"  {sa.date()} {sa.strftime('%H:%M')}〜{ea.strftime('%H:%M')} "
                f"{staff_names.get(dup.staff_id, '(不明)')}: {dup.title or '(無題)'}"
                f"{marks} → 残す external_id={keep.external_id}"
            )

        if not args.apply:
            # dry-run は読むだけ (ダンプも書かない)。
            print("dry-run のため削除していません (--apply で実行)")
            return

        backup_path = args.backup or (
            f"/tmp/dedupe_manual_events_backup_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
        )
        payload = [
            {
                "deleted": row_to_json(dup, staff_names.get(dup.staff_id, "")),
                "kept_external_id": keep.external_id,
            }
            for dup, keep in victims
        ]
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"復元用のダンプ: {backup_path}")

        moved_cancel = 0
        moved_blocking = 0
        for dup, keep in victims:
            # 人が入れた意味 (「今週だけ外す」の取消印・占有フラグ) は残す側へ移す
            if dup.cancelled_at is not None and keep.cancelled_at is None:
                keep.cancelled_at = dup.cancelled_at
                moved_cancel += 1
            if dup.blocking and not keep.blocking:
                keep.blocking = True
                moved_blocking += 1
            await db.delete(dup)
        await db.commit()
        print(
            f"削除 {len(victims)} 件 / 取消印の移設 {moved_cancel} 件 / "
            f"占有フラグの移設 {moved_blocking} 件 — 完了"
        )


if __name__ == "__main__":
    asyncio.run(main())
