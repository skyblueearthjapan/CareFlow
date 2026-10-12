"""rk template — 型の作業場（日付の無い仮の 1 週間で、コースの型＝固定訪問の枠を試してから登録する）。本番は読むだけ。

  rk template new <名前> --office <拠点の略称> [--from master|<下書き>] [--from-raw <raw.json>] [--force]
  rk template solve <名前> [--weekday mon,tue | --all] [--seconds 20] [--max-changes N] [--w-change 20]
  rk template whatif <名前> [--staff-count mon=4,tue=5] [--add-patient P123[:mon,thu][@09:00-12:00]]
                     [--pref P123:mon=09:00-11:00] [--drop-staff S004] [--red keep|ignore] [--clear] [--as <新しい名前>]
  rk template move <名前> --slot mon03 --course B [--start 10:30]
  rk template compare <名前> [<別の下書き>|master]
  rk template report <名前> [--vs master|<別の下書き>] [--out <file.html>]
  rk template commit <名前> [--red-cleanup-done] [--no-master-check]
  rk template list

下書き = agent/runs/template-drafts/<名前>.json（利用者名入り・git 管理外）。帳票 = docs/reports/agent/<日付>/template-*.html。
登録の案 = agent/runs/plans/<日時>-template-<名前>.json（pfv-update を患者ごと・型だけ）。反映は rk plan check → report → 了承 → rk backup → rk apply。
"""

from __future__ import annotations

import json
import sys

from rk import RkError
from rk.config import AGENT_DIR


def _path():
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))


def _err(e):
    raise RkError(str(e)) from None


def _log(m: str) -> None:
    print(m, file=sys.stderr, flush=True)


def _out(obj) -> int:
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("template", help="型の作業場（仮の 1 週間でコースの型を試す・本番は読むだけ）")
    sub = p.add_subparsers(dest="template_cmd", required=True)

    q = sub.add_parser("new", help="下書きを作る（マスターを写す・本番は読むだけ）")
    q.add_argument("name")
    q.add_argument("--office", help="拠点（offices.short_label）。マスターから作るときに要る")
    q.add_argument("--from", dest="origin", default="master", help="master（既定）か、写す元の下書きの名前")
    q.add_argument("--from-raw", help="取り出し済みの raw.json を使う（本番に繋がない）")
    q.add_argument("--force", action="store_true", help="同じ名前の下書きを作り直す")
    q.set_defaults(func=cmd_new)

    q = sub.add_parser("solve", help="コースと時刻を組み直す（曜日ごと・省略で週まるごと）")
    q.add_argument("name")
    q.add_argument("--weekday", action="append", default=[], help="mon〜sat（, 区切り・何度でも）")
    q.add_argument("--all", action="store_true", help="週まるごと（既定）")
    q.add_argument("--seconds", type=int, default=20, help="1 曜日あたりの計算時間（秒）")
    q.add_argument("--max-changes", type=int, help="1 曜日で変える枠の数の目安（超えたら重みを上げて解き直す）")
    q.add_argument("--w-change", type=int, default=20, help="コースを替える 1 枠の重さ（移動の分と同じ単位）")
    q.set_defaults(func=cmd_solve)

    q = sub.add_parser("whatif", help="もしもの条件を書く（解き直しは solve）")
    q.add_argument("name")
    q.add_argument("--staff-count", help="曜日ごとの職員（コース）の数 例 mon=4,tue=5")
    q.add_argument("--add-patient", action="append", default=[], metavar="CODE[:mon,thu][@HH:MM-HH:MM]",
                   help="枠の無い方を足す（曜日・時間帯を指定できる）")
    q.add_argument("--pref", action="append", default=[], metavar="CODE:mon=HH:MM-HH:MM", help="希望の時間帯のもしも")
    q.add_argument("--drop-staff", action="append", default=[], metavar="CODE", help="その職員がいないとしたら")
    q.add_argument("--red", choices=["keep", "ignore"], help="赤ピンを守る（既定）／全部外したら（試算）")
    q.add_argument("--clear", action="store_true", help="もしもの条件を外す")
    q.add_argument("--as", dest="as_name", help="元の下書きは残し、写した新しい下書きに書く")
    q.set_defaults(func=cmd_whatif)

    q = sub.add_parser("move", help="1 枠を手で動かす（決まりを確かめる・解き直しても動かさない）")
    q.add_argument("name")
    q.add_argument("--slot", required=True, help="枠の番号（例 mon03・帳票の右下）")
    q.add_argument("--course", required=True, help="コース（例 B・稲B）")
    q.add_argument("--start", help="開始の時刻 HH:MM（省略で今の時刻）")
    q.set_defaults(func=cmd_move)

    q = sub.add_parser("compare", help="下書きを別の下書きかマスターと比べる")
    q.add_argument("name")
    q.add_argument("other", nargs="?", default="master")
    q.set_defaults(func=cmd_compare)

    q = sub.add_parser("report", help="A3 横の時間割（行 = コース・列 = 月〜土）＋ A4 の要点")
    q.add_argument("name")
    q.add_argument("--vs", default="master", help="比べる相手（master か別の下書き）")
    q.add_argument("--out", help="出力先の HTML（docs/reports の下）")
    q.set_defaults(func=cmd_report)

    q = sub.add_parser("commit", help="固定訪問に登録する案（pfv-update）を作る。反映はしない")
    q.add_argument("name")
    q.add_argument("--red-cleanup-done", action="store_true",
                   help="赤ピンの整理（本番で赤を一度ゼロに戻す K-4）が終わったと PO が確認したときだけ。付けないと ready:false")
    q.add_argument("--no-master-check", action="store_true", help="マスターが変わっていないかを確かめない（案は ready:false）")
    q.set_defaults(func=cmd_commit)

    q = sub.add_parser("list", help="下書きの一覧")
    q.set_defaults(func=cmd_list)


def _metrics_out(ms):
    from tools.template_ws.ops import short_line

    return [{"曜日": m["weekday_ja"], "マスター": short_line(m, "master"), "下書き": short_line(m, "draft"),
             "変える枠": m["changes"] - m["new_placed"], "赤ピンの枠のコース替え": m["red_course_changes"],
             "新しい方を置いた枠": m["new_placed"], "置けない新しい枠": m["new_unplaced"]} for m in ms]


def cmd_new(args) -> int:
    _path()
    from tools.template_ws import ops
    from tools.template_ws.draft import TemplateError

    try:
        d = ops.new(args.cfg, args.name, office=args.office, origin=args.origin, from_raw=args.from_raw,
                    force=args.force, log=_log)
        ms = ops.refresh_metrics(d)
    except TemplateError as e:
        _err(e)
    from tools.template_ws.problems import candidate_specs
    cands = candidate_specs(d)
    return _out({"draft": args.name, "office": d["office"], "origin": d["origin"],
                 "extracted_at": d["snapshot"].get("extracted_at"), "patients_in_scope": len(d["snapshot"]["scope_patients"]),
                 "まだ枠の無い方": [{"code": c["patient"].get("code"), "days": "".join("月火水木金土日"[x] for x in c["days"]),
                                 "置けない理由": c["reason"]} for c in cands],
                 "metrics": _metrics_out(ms), "next": [f"rk template solve {args.name}", f"rk template report {args.name}"]})


def cmd_solve(args) -> int:
    _path()
    from tools.courseplan.run import CoursePlanError, parse_weekdays
    from tools.template_ws import ops
    from tools.template_ws.draft import TemplateError

    try:
        wds = None if (args.all or not args.weekday) else parse_weekdays(args.weekday)
        res = ops.solve(args.cfg, args.name, weekdays=wds, seconds=args.seconds, max_changes=args.max_changes,
                        w_change=args.w_change, log=_log)
    except (TemplateError, CoursePlanError) as e:
        _err(e)
    return _out({"draft": args.name, "metrics": _metrics_out(res["metrics"]),
                 "next": [f"rk template compare {args.name}", f"rk template report {args.name}"]})


def cmd_whatif(args) -> int:
    _path()
    from tools.template_ws import ops
    from tools.template_ws.draft import TemplateError

    try:
        res = ops.whatif(args.cfg, args.name, staff_count=args.staff_count, add_patient=args.add_patient,
                         pref=args.pref, drop_staff=args.drop_staff, red=args.red, clear=args.clear, as_name=args.as_name)
    except TemplateError as e:
        _err(e)
    name = res["draft"]["name"]
    return _out({"draft": name, "もしも": res["said"], "next": [f"rk template solve {name}", f"rk template compare {name}"]})


def cmd_move(args) -> int:
    _path()
    from tools.template_ws import ops
    from tools.template_ws.draft import TemplateError

    try:
        res = ops.move(args.cfg, args.name, slot=args.slot, course=args.course, start=args.start)
    except TemplateError as e:
        _err(e)
    return _out({"draft": args.name, "slot": res["slot"], "to": res["to"], "移動（分）": res["travel"],
                 "増えた気をつけること": res["new_issues"]})


def cmd_compare(args) -> int:
    _path()
    from tools.template_ws import ops
    from tools.template_ws.draft import TemplateError

    try:
        res = ops.compare(args.cfg, args.name, args.other)
    except TemplateError as e:
        _err(e)
    return _out({"lines": res["lines"], "違う枠": len(res["changed"]),
                 "changed": [f"{r['sid']} {r['weekday']} {r['patient']}様{'🔴' if r['red'] else ''}: {r['b']} → {r['a']}"
                             for r in res["changed"][:40]]})


def cmd_report(args) -> int:
    _path()
    from tools.template_ws import report
    from tools.template_ws.draft import TemplateError

    try:
        path = report.write(args.cfg, args.name, args.vs, args.out)
    except TemplateError as e:
        _err(e)
    return _out({"report": str(path), "note": "1 枚目 A3 横（時間割）・2 枚目 A4 縦（要点）。利用者名入り・公開しない"})


def cmd_commit(args) -> int:
    _path()
    from tools.template_ws import ops
    from tools.template_ws.draft import TemplateError

    try:
        res = ops.commit(args.cfg, args.name, red_cleanup_done=args.red_cleanup_done,
                         no_master_check=args.no_master_check, log=_log)
    except TemplateError as e:
        _err(e)
    plan = res["plan"]
    return _out({"plan": res["plan_path"], "ready": plan["ready"], "blockers": plan["blockers"], "ops": len(plan["ops"]),
                 "summary": plan["summary"][:1],
                 "next": [f"rk plan report {res['plan_path']}", f"rk plan report {res['plan_path']} --a3"]})


def cmd_list(args) -> int:
    _path()
    from tools.template_ws.draft import drafts_dir

    out = []
    d = drafts_dir(args.cfg)
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            x = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"name": x.get("name"), "office": x.get("office"), "origin": x.get("origin"),
                    "updated_at": x.get("updated_at"), "extracted_at": (x.get("snapshot") or {}).get("extracted_at"),
                    "last": ((x.get("history") or [{}])[-1]).get("detail")})
    return _out(out)
