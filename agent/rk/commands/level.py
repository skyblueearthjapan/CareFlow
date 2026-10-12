"""rk level — 週のならし（docs/tools/week-leveling）を rk のつなぎ方で動かす。本番は読むだけ。

  rk level week --week 2026-10-19 [--day D] [--off S004@2026-10-20] [--allow-over 1] [--over-before-manager]
                [--balance] [--drop <訪問 ID の先頭>] [--add-special] [--seconds 30] [--no-app-check]
                今回だけの条件: [--days thu,fri] [--prefer-staff CODE[:重み]] [--avoid-staff CODE[:重み]]
                [--keep-current [重み]] [--cancel <訪問 ID の先頭>] [--fast]
                [--only-staff CODE[,CODE] --window HH:MM-HH:MM] [--not-same-day A,B] [--pair-staff 利用者:CODE]
                [--forbid-pair 利用者:CODE]   （詳しくは docs/tools/week-leveling/level_knobs.py の先頭）
                [--from-dir <前回の出力>] [--config <config.local.json>] [--out <dir>]
  rk level day  --date 2026-10-22 --off S004 [--off S007] [同じオプション]   … 急な休みの 1 日の組み直し

出力: Excel/A4 = docs/reports/agent/<日付>/<時刻>-week<NN>-leveling*/（利用者名入り・git 管理外）
      案      = agent/runs/plans/<日時>-week<NN>-leveling*.json（rk plan check / report / apply に渡す）
"""

from __future__ import annotations

import json
import sys

from rk import RkError
from rk.config import AGENT_DIR

# 「今回だけの条件」の引数は docs の道具と同じ部品を使う（標準ライブラリだけ）
sys.path.insert(0, str(AGENT_DIR.parent / "docs" / "tools" / "week-leveling"))
import level_knobs  # noqa: E402


def _common(p) -> None:
    p.add_argument("--day", action="append", default=[], help="その日だけ計算（何度でも）")
    p.add_argument("--off", action="append", default=[], metavar="CODE[@YYYY-MM-DD]",
                   help="この人が休みなら（日付なし = 週全部）。何度でも")
    p.add_argument("--allow-over", type=int, default=0, help="正規の上限を N 名まで超えてよい（重み付き）")
    p.add_argument("--over-before-manager", action="store_true", help="マネージャーより先に少しオーバーで吸収")
    p.add_argument("--balance", action="store_true", help="正規の件数をならす")
    p.add_argument("--drop", action="append", default=[], metavar="訪問IDの先頭", help="案から外す訪問（今週だけ取消の予定）")
    p.add_argument("--add-special", action="store_true", help="特別訪問週間の未配置の○を足す訪問として入れる")
    p.add_argument("--seconds", type=int, default=None, help="1 日あたりの計算時間（秒・既定 30・--fast なら 10）")
    p.add_argument("--no-app-check", action="store_true", help="アプリの物差しでの診断を飛ばす")
    p.add_argument("--no-pdf", action="store_true", help="A4 の PDF を作らない（HTML だけ）")
    p.add_argument("--from-dir", help="取り出し済みの week.json / history.json を使う（本番に繋がない）")
    p.add_argument("--config", help="計算の設定（既定 docs/tools/week-leveling/config.local.json。無いとき・中継の箱の中は職員の記録から作る）")
    p.add_argument("--out", help="出力先（docs/reports の下）")
    p.add_argument("--include-trainee", action="store_true", help="新人（is_trainee）も担当として計算する（ふつうは外す）")
    level_knobs.add_args(p)  # --days / --prefer-staff / --avoid-staff / --keep-current / --cancel / --fast / --only-staff --window ...


def register(subparsers) -> None:
    p = subparsers.add_parser("level", help="ならし案（本番は読むだけ・反映用の案を出す）")
    sub = p.add_subparsers(dest="level_command", metavar="<week|day>")
    q = sub.add_parser("week", help="週（または指定日）のならし案")
    q.add_argument("--week", required=True, help="週のどの日でもよい（YYYY-MM-DD）")
    _common(q)
    q.set_defaults(func=cmd_level_week)
    q = sub.add_parser("day", help="急な休みの 1 日の組み直し（--day と --off の近道）")
    q.add_argument("--date", required=True, help="その日（YYYY-MM-DD）")
    _common(q)
    q.set_defaults(func=cmd_level_day)
    p.set_defaults(func=lambda args: (p.print_help(), 1)[1])


def _run(args, *, week: str, days: list[str], off: list[str], kind: str) -> int:
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    from tools.leveling.run import LevelError, level

    try:
        level_knobs.from_args(args)  # 書き方の間違いは計算の前に知らせる
        weekdays = level_knobs.parse_weekdays(args.days) if args.days else []
    except ValueError as e:
        raise RkError(str(e)) from None
    try:
        res = level(
            args.cfg, week=week, days=days, off=off, allow_over=args.allow_over,
            over_before_manager=args.over_before_manager, balance=args.balance, drop=args.drop,
            add_special=args.add_special, seconds=args.seconds, weekdays=weekdays,
            knob_argv=level_knobs.to_argv(args), no_app_check=args.no_app_check,
            no_pdf=args.no_pdf, from_dir=args.from_dir, config_path=args.config, out=args.out,
            include_trainee=args.include_trainee, kind=kind,
            log=lambda m: print(m, file=sys.stderr, flush=True),
        )
    except LevelError as e:
        raise RkError(str(e)) from None
    plan = res["plan"]
    print(json.dumps({
        "plan": res["plan_path"],
        "reports": res["out_dir"],
        "title": plan["title"],
        "ready": plan["ready"],
        "blockers": plan["blockers"],
        "summary": plan["summary"],
        "checks": plan["checks"],
        "warnings": len(plan["warnings"]),
        "conditions": [c["text"] for c in plan.get("conditions") or []],
        "ops": len(plan["ops"]),
        "next": [f"rk plan check {res['plan_path']}", f"rk plan report {res['plan_path']} --a3"],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_level_week(args) -> int:
    return _run(args, week=args.week, days=args.day, off=args.off, kind="week-leveling")


def cmd_level_day(args) -> int:
    if not args.off:
        raise RkError("rk level day には --off <職員コード> が要ります（休む人）。組み直しだけなら rk level week --day")
    off = [o if "@" in o else f"{o}@{args.date}" for o in args.off]
    days = sorted(set(args.day) | {args.date})
    return _run(args, week=args.date, days=days, off=off, kind="staff-absence")
