"""rk courseplan — コースのならし（固定訪問の枠＝コーススケジュールの最適化）。本番は読むだけ。

  rk courseplan [--office <拠点の略称>] [--weekday mon] [--weekday tue,wed] [--seconds 30] [--max-changes 8]
                [--w-change 20] [--red keep|ignore] [--no-pref fixed|free] [--staff S004,S005]
                [--cap 6] [--red-cleanup-done] [--from-raw <raw.json>] [--out <dir>]

全員稼働の前提で、各曜日のコース（1 人 1 日の列）へ固定訪問の枠を割り振り直し、時刻は希望の幅の中だけで動かす。
赤ピンの枠は曜日・時刻を動かさない（コースは替えてよい）。
出力: 材料・数（いま／案）= docs/reports/agent/<日付>/<時刻>-courseplan-*/（利用者名入り・git 管理外）
      案 = agent/runs/plans/<日時>-courseplan-*.json（pfv-update を患者ごと・ready は「赤ピンの整理（本番）が終わるまで反映しない」を外すまで false）
"""

from __future__ import annotations

import json
import sys

from rk import RkError
from rk.config import AGENT_DIR


def register(subparsers) -> None:
    p = subparsers.add_parser("courseplan", help="コースのならし（固定訪問の枠の最適化・本番は読むだけ）")
    p.add_argument("--office", help="拠点（offices.short_label）。省略で全拠点")
    p.add_argument("--weekday", action="append", default=[], help="曜日 mon〜sat（, 区切り・何度でも）。省略で月〜土")
    p.add_argument("--seconds", type=int, default=30, help="1 拠点 1 曜日あたりの計算時間（秒）")
    p.add_argument("--max-changes", type=int, help="1 拠点 1 曜日で変える枠の数の目安（超えたら重みを上げて解き直す）")
    p.add_argument("--w-change", type=int, help="コースを替える 1 枠の重さ（移動の分と同じ単位・既定 20）")
    p.add_argument("--red", choices=["keep", "ignore"], default="keep",
                   help="keep = 赤ピンの時刻を守る（既定）／ignore = 赤を全部外したらの試算")
    p.add_argument("--no-pref", choices=["fixed", "free"], default="fixed",
                   help="希望の無い方: fixed = 今の時刻のまま（既定）／free = 終日の範囲で動かしてよい")
    p.add_argument("--staff", action="append", default=[], metavar="CODE[,CODE]",
                   help="コースを持つ人を職員コードで決める（省略で拠点・新人でない・勤務の型から）")
    p.add_argument("--red-cleanup-done", action="store_true",
                   help="赤ピンの整理（本番で赤を一度ゼロに戻す K-4）が終わったと確認できたときだけ付ける。付けないと ready:false")
    p.add_argument("--cap", type=int, default=6, help="正規のコースの 1 日の上限（既定 6）")
    p.add_argument("--from-raw", help="取り出し済みの raw.json を使う（本番に繋がない）")
    p.add_argument("--out", help="出力先（docs/reports の下）")
    p.set_defaults(func=cmd_courseplan)


def cmd_courseplan(args) -> int:
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    from tools.courseplan.run import CoursePlanError, courseplan

    try:
        res = courseplan(
            args.cfg, office=args.office, weekdays=args.weekday, seconds=args.seconds,
            max_changes=args.max_changes, w_change=args.w_change, red=args.red, no_pref=args.no_pref,
            cap_regular=args.cap, red_cleanup_done=args.red_cleanup_done, staff_codes=args.staff or None, from_raw=args.from_raw, out=args.out,
            log=lambda m: print(m, file=sys.stderr, flush=True),
        )
    except CoursePlanError as e:
        raise RkError(str(e)) from None
    plan = res["plan"]
    print(json.dumps({
        "plan": res["plan_path"],
        "reports": res["out_dir"],
        "title": plan["title"],
        "ready": plan["ready"],
        "blockers": plan["blockers"],
        "summary": plan["summary"],
        "ops": len(plan["ops"]),
        "warnings": len(plan["warnings"]),
        "next": [f"rk plan report {res['plan_path']} --a3"],
    }, ensure_ascii=False, indent=2))
    return 0
