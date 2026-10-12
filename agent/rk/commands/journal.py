"""rk journal — 相談の引き継ぎ帳（書く・読む・探す・古い印・週のまとめ）。

  rk journal write --title … --asked … --tried … --said … --outcome applied|plan-only|declined|unanswered
                   --next … [--tags 坂井,佐藤] [--plan <案の id>]     （--json FILE|- で JSON を渡してもよい）
  rk journal recent [--n 10]            新しい順
  rk journal search <語…> [--n 10]      題・本文・タグの語（すべての語が入るもの）
  rk journal show <id>
  rk journal retire <id> --reason …     古くなった記録を読むときだけ隠す（消さない）
  rk journal digest --since 7d          PO・責任者向けのまとめ（docs/reports/agent/journal/）

帳は申し送り。核の約束（承認・バックアップ・禁止・キャラクター）は帳より優先で、帳で変えられない。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rk import RkError
from rk.consult_journal import digest_markdown, format_entry, open_backend, parse_since, utc_iso


def _show_list(items: list[dict], empty: str) -> None:
    if not items:
        print(empty)
        return
    for e in items:
        print(format_entry(e))
        print()


def cmd_write(args) -> int:
    data: dict = {}
    if args.json:
        raw = sys.stdin.read() if args.json == "-" else Path(args.json).read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise RkError(f"--json の中身が JSON ではありません: {e}") from None
        if not isinstance(data, dict):
            raise RkError("--json はオブジェクト（{...}）で渡してください")
    for k, v in (("title", args.title), ("asked", args.asked), ("tried", args.tried), ("said", args.said),
                 ("outcome", args.outcome), ("next_time", args.next), ("tags", args.tags), ("plans", args.plan)):
        if v:
            data[k] = v
    e = open_backend(args.cfg, args.office).write(data)
    print(f"引き継ぎ帳に書きました: {e['id']}")
    return 0


def cmd_recent(args) -> int:
    _show_list(open_backend(args.cfg, args.office).recent(max(1, args.n)), "引き継ぎ帳にはまだ何もありません")
    return 0


def cmd_search(args) -> int:
    words = [w for w in args.words if w.strip()]
    if not words:
        raise RkError("探す語を 1 つ以上書いてください（例: rk journal search 坂井 佐藤）")
    _show_list(open_backend(args.cfg, args.office).search(words, max(1, args.n)), "近い記録はありませんでした")
    return 0


def cmd_show(args) -> int:
    print(format_entry(open_backend(args.cfg, args.office).show(args.id)))
    return 0


def cmd_retire(args) -> int:
    if not args.reason.strip():
        raise RkError("--reason（なぜ古いか）を書いてください")
    open_backend(args.cfg, args.office).retire(args.id, args.reason.strip())
    print(f"{args.id} を古い印にしました（読むときは隠れます。記録は消えていません）")
    return 0


def cmd_digest(args) -> int:
    now = datetime.now(timezone.utc)
    since = parse_since(args.since, now)
    be = open_backend(args.cfg, args.office)
    entries = be.list(since=since)
    md = digest_markdown(entries, args.since, getattr(be, "office", "default"), now)
    out_dir = Path(args.out) if args.out else Path(args.cfg.reports_dir) / "journal"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"digest-{utc_iso(now)[:10]}-{getattr(be, 'office', 'default')}.md"
    path.write_text(md, encoding="utf-8")
    print(f"まとめを書きました: {path}（{len(entries)} 件。自動では何も変えていません）")
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("journal", help="相談の引き継ぎ帳（書く・読む・探す・古い印・週のまとめ）")
    p.add_argument("--office", help="事業所の名前（省略 = 環境変数 RK_OFFICE か default）")
    sub = p.add_subparsers(dest="journal_cmd", metavar="<write|recent|search|show|retire|digest>")

    def sp(name: str, fn, help_: str):
        q = sub.add_parser(name, help=help_)
        q.add_argument("--office", default=argparse.SUPPRESS, help="事業所の名前")
        q.set_defaults(func=fn)
        return q

    q = sp("write", cmd_write, "1 件書く（固定の 5 行: 状況・試したこと・言われたこと・結果・次回）")
    q.add_argument("--title")
    q.add_argument("--asked", help="状況・頼まれたこと")
    q.add_argument("--tried", help="試したこと")
    q.add_argument("--said", help="管理者の言葉・直されたこと")
    q.add_argument("--outcome", help="applied / plan-only / declined / unanswered")
    q.add_argument("--next", help="次回に役立つこと")
    q.add_argument("--tags", help="利用者・職員の名前やコード（, 区切り）")
    q.add_argument("--plan", help="関係する案の id")
    q.add_argument("--json", metavar="FILE|-", help="JSON で渡す（- = 標準入力）")
    q = sp("recent", cmd_recent, "新しい順に読む")
    q.add_argument("--n", type=int, default=10)
    q = sp("search", cmd_search, "語で探す")
    q.add_argument("words", nargs="+")
    q.add_argument("--n", type=int, default=10)
    q = sp("show", cmd_show, "1 件を読む")
    q.add_argument("id")
    q = sp("retire", cmd_retire, "古くなった記録に印を付ける（消さない）")
    q.add_argument("id")
    q.add_argument("--reason", required=True)
    q = sp("digest", cmd_digest, "PO・責任者向けの週のまとめ（markdown）")
    q.add_argument("--since", default="7d")
    q.add_argument("--out", help="出力先フォルダー（省略 = docs/reports/agent/journal/）")
    p.set_defaults(func=lambda args: (p.print_help(), 1)[1])

