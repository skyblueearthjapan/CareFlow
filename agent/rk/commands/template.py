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

アプリの画面と同じ下書き（バックエンドの template_ws API・人とらく助エージェントの共同作業）を触る道具:
  rk template draft-list
  rk template draft-new <名前> --office <拠点の略称か ID|all>   （all = 全拠点の下書き。稲毛と都賀を一緒に）
  rk template draft-show <名前か ID> [--slots]
  rk template explain <名前か ID> [--seq N]   （最適化で何をどう変えたかの報告。画面の［くわしく］と同じ中身）
  rk template draft-move <名前か ID> --slot <枠のキー> --course <コース ID> --start HH:MM --reason "..."
  rk template draft-optimise <名前か ID> [--weekday mon,tue] [--seconds 20] --reason "..."
  rk template draft-whatif <名前か ID> [--staff-count mon=4] [--drop-staff S004] [--pref P1:mon=09:00-11:00] [--clear] --reason "..."
  rk template draft-undo <名前か ID> / draft-redo <名前か ID>
下書きの編集に承認番号は要らない（actor=agent・理由つきで履歴に残り、人が ↶ で戻せる）。登録（出口）は rk plan の案と了承のあと。

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

    # --- アプリの画面と同じ下書き（バックエンド API）---
    q = sub.add_parser("draft-list", help="画面の下書きの一覧（バックエンド）")
    q.set_defaults(func=cmd_draft_list)

    q = sub.add_parser("draft-new", help="画面の下書きを作る（いまの型を写す。--office all で全拠点）")
    q.add_argument("name")
    q.add_argument("--office", required=True, help="拠点の略称か ID。all = 全拠点（型の作業場が使える拠点をまとめて）")
    q.add_argument("--from", dest="origin", help="写す元の画面の下書き（名前か ID・同じ拠点の組み合わせだけ）")
    q.set_defaults(func=cmd_draft_new)

    q = sub.add_parser("explain", help="最適化で何をどう変えたかの報告（種類ごとの件数・曜日ごとの数・移動が増えた理由）")
    q.add_argument("draft", help="下書きの名前か ID")
    q.add_argument("--seq", type=int, help="履歴の番号（省略でいちばん新しい最適化）")
    q.set_defaults(func=cmd_explain)

    q = sub.add_parser("draft-show", help="画面の下書きの要点（数の物差し・最新の編集）")
    q.add_argument("draft", help="下書きの名前か ID")
    q.add_argument("--slots", action="store_true", help="枠の一覧も出す")
    q.set_defaults(func=cmd_draft_show)

    q = sub.add_parser("draft-move", help="画面の下書きの 1 枠を動かす（コース・時刻。曜日は動かせない）")
    q.add_argument("draft")
    q.add_argument("--slot", required=True, help="枠のキー（draft-show --slots の key）")
    q.add_argument("--course", required=True, help="コースの ID（draft-show の courses）")
    q.add_argument("--start", required=True, help="開始の時刻 HH:MM")
    q.add_argument("--reason", required=True, help="理由（履歴に残り、人に見える）")
    q.set_defaults(func=cmd_draft_move)

    q = sub.add_parser("draft-optimise", help="画面の下書きを最適化する（曜日・全部）")
    q.add_argument("draft")
    q.add_argument("--weekday", action="append", default=[], help="mon〜sat（, 区切り・省略で全部）")
    q.add_argument("--seconds", type=int, default=20)
    q.add_argument("--reason", required=True)
    q.set_defaults(func=cmd_draft_optimise)

    q = sub.add_parser("draft-whatif", help="画面の下書きにもしもの条件を書く")
    q.add_argument("draft")
    q.add_argument("--staff-count", help="曜日ごとの職員の数 例 mon=4,tue=5")
    q.add_argument("--drop-staff", action="append", default=[], metavar="CODE")
    q.add_argument("--pref", action="append", default=[], metavar="CODE:mon=HH:MM-HH:MM")
    q.add_argument("--red", choices=["keep", "ignore"])
    q.add_argument("--clear", action="store_true")
    q.add_argument("--reason", required=True)
    q.set_defaults(func=cmd_draft_whatif)

    q = sub.add_parser("draft-undo", help="画面の下書きを一つ前に戻す")
    q.add_argument("draft")
    q.set_defaults(func=cmd_draft_undo)
    q = sub.add_parser("draft-redo", help="画面の下書きをやり直す")
    q.add_argument("draft")
    q.set_defaults(func=cmd_draft_redo)


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


# ---------------------------------------------------------------------------
# アプリの画面と同じ下書き（バックエンド API）。人もらく助エージェントも同じ下書きを編集する。
# 編集に承認番号は要らない（actor=agent・理由つき）。登録（出口）はここにはない（rk plan の案と了承のあと）。
# ---------------------------------------------------------------------------


def _api_open(args):
    from rk.session import open_api

    return open_api(args.cfg)


def _resolve_draft(api, ref: str) -> dict:
    rows = api.get("/template-ws/drafts") or []
    hits = [r for r in rows if r["id"] == ref or r["id"].startswith(ref) or r["name"] == ref]
    if not hits:
        have = "・".join(r["name"] for r in rows) or "なし"
        raise RkError(f"下書き {ref} がありません（今ある下書き: {have}）。型の作業場が使える拠点か確かめてください")
    if len(hits) > 1:
        raise RkError(f"{ref} に当てはまる下書きが複数あります。ID で指定してください")
    return hits[0]


def _version(api, did: str) -> int:
    return int(api.get(f"/template-ws/drafts/{did}/version")["version"])


def _post(api, did: str, tail: str, body: dict):
    from rk.opsbase import ApiError

    try:
        return api.call("POST", f"/template-ws/drafts/{did}/{tail}", {**body, "actor": "agent"})
    except ApiError as e:
        detail = e.body.get("detail") if isinstance(e.body, dict) else e.body
        if e.status == 409:
            raise RkError(f"下書きがほかで変わりました（{detail}）。draft-show で読み直してからもう一度") from None
        if e.status in (404, 422):
            raise RkError(str(detail)) from None
        raise


def _summary(view: dict) -> dict:
    tot = view.get("totals") or {}
    m, d = tot.get("master") or {}, tot.get("draft") or {}
    keys = {"conflicts": "間に合わない組", "over_cap": "上限超え", "no_course": "コースの無い枠",
            "unstaffed": "人のいないコースの枠", "travel_min": "移動の合計（分）"}
    last = view.get("latest_step") or {}
    return {"draft": view["name"], "version": view["version"],
            "数の物差し（いまの型→下書き）": {v: f"{m.get(k)}→{d.get(k)}" for k, v in keys.items()},
            "最新の編集": {"誰": last.get("actor_label"), "種類": last.get("kind"), "理由": last.get("reason"),
                        "変えた枠": last.get("changed_slots")} if last else None}


def _scope_label(row: dict, offices: dict[str, str]) -> str:
    if row.get("all_offices"):
        return "全拠点（" + "・".join(offices.get(o, o[:8]) for o in row.get("offices") or []) + "）"
    return offices.get(str(row.get("office_id")), str(row.get("office_id")))


def _office_labels(api) -> dict[str, str]:
    rows = api.get("/offices", {"limit": 200}) or []
    return {str(o["id"]): (o.get("short_label") or o.get("name") or "") for o in rows}


def cmd_draft_list(args) -> int:
    with _api_open(args) as api:
        rows = api.get("/template-ws/drafts") or []
        offices = _office_labels(api)
    return _out([{"id": r["id"], "name": r["name"], "拠点": _scope_label(r, offices), "status": r["status"],
                  "version": r["version"], "updated_at": r["updated_at"]} for r in rows])


def cmd_draft_new(args) -> int:
    body: dict = {"name": args.name}
    with _api_open(args) as api:
        if str(args.office).lower() in ("all", "全拠点"):
            body["all_offices"] = True
        else:
            offices = _office_labels(api)
            hits = [oid for oid, lab in offices.items() if args.office in (oid, lab)]
            if len(hits) != 1:
                raise RkError(f"拠点 {args.office} を 1 つに決められません（略称・ID・all のどれかで）")
            body["office_id"] = hits[0]
        if args.origin:
            body["from_draft_id"] = _resolve_draft(api, args.origin)["id"]
        from rk.opsbase import ApiError

        try:
            v = api.call("POST", "/template-ws/drafts", body)
        except ApiError as e:
            detail = e.body.get("detail") if isinstance(e.body, dict) else e.body
            raise RkError(str(detail)) from None
    out = _summary(v)
    out["id"] = v["id"]
    out["拠点"] = "・".join(o["label"] for o in v.get("offices") or []) + ("（全拠点）" if v.get("all_offices") else "")
    return _out(out)


def explain_summary(rep: dict) -> dict:
    """GET /template-ws/drafts/{id}/report → 要約（短い日本語＋種類ごとの件数＋曜日ごとの数）。"""
    days = []
    for d in rep.get("days") or []:
        m, a = d["master"], d["after"]
        days.append({"曜日": d["name"], "変えた枠": d["changes"],
                     "間に合わない組": f"{m['conflicts']}→{a['conflicts']}", "上限超え": f"{m['over_cap']}→{a['over_cap']}",
                     "コースの無い枠": f"{m['no_course']}→{a['no_course']}",
                     "人のいないコース": f"{m['unstaffed']}→{a['unstaffed']}",
                     "移動の合計（分）": f"{m['travel_min']}→{a['travel_min']}",
                     "一般職員の件数": f"{'・'.join(map(str, m['regular_counts']))} → {'・'.join(map(str, a['regular_counts']))}",
                     "管理者の訪問": f"{m['manager_visits']}→{a['manager_visits']}",
                     "移動の増減の内訳": {"道順に新しく入った訪問": d["travel"]["routed"], "並べ替え": d["travel"]["reorder"]}})
    return {
        "draft": rep.get("draft"), "履歴の番号": rep.get("seq"), "誰が": rep.get("actor_label"),
        "要約": rep.get("text") or [], "気になる点": rep.get("cautions") or [],
        "種類ごと": [{"種類": c["label"], "件数": c["count"],
                    "枠": [f"{i['weekday_ja']}曜 {i['patient']} {i['from_course']}"
                          f"{(' ' + i['from_start']) if i.get('from_start') else ''} → {i['to_course']}"
                          f"{(' ' + i['to_start']) if i.get('to_start') else ''}" for i in c["items"][:20]]}
                   for c in rep.get("categories") or []],
        "曜日ごと（いまの型→下書き）": days,
    }


def cmd_explain(args) -> int:
    from rk.opsbase import ApiError

    with _api_open(args) as api:
        d = _resolve_draft(api, args.draft)
        try:
            rep = api.get(f"/template-ws/drafts/{d['id']}/report", {"seq": args.seq} if args.seq else None)
        except ApiError as e:
            detail = e.body.get("detail") if isinstance(e.body, dict) else e.body
            raise RkError(str(detail)) from None
    return _out(explain_summary(rep))


def cmd_draft_show(args) -> int:
    with _api_open(args) as api:
        d = _resolve_draft(api, args.draft)
        v = api.get(f"/template-ws/drafts/{d['id']}")
    out = _summary(v)
    out["拠点"] = "・".join(o["label"] for o in v.get("offices") or []) + ("（全拠点）" if v.get("all_offices") else "")
    out["courses"] = [{"id": c["id"], "label": c["label"]} for c in v["courses"]]
    out["枠の無い方"] = [{"code": t["code"], "state": t["state"], "reason": t["reason"]} for t in v["tray"]]
    if args.slots:
        out["slots"] = [{"key": s["key"], "曜日": day["name"], "patient": s["patient"], "start": s["start"],
                         "course_id": s["course_id"], "changed": s["changed"], "red": s["locked"],
                         "win": s["win"]} for day in v["days"] for s in day["slots"]]
    return _out(out)


def cmd_draft_move(args) -> int:
    with _api_open(args) as api:
        d = _resolve_draft(api, args.draft)
        res = _post(api, d["id"], "move", {"key": args.slot, "course_id": args.course, "start": args.start,
                                           "reason": args.reason, "expected_version": _version(api, d["id"])})
    out = _summary(res["view"])
    out["増えた気をつけること"] = res.get("new_issues")
    return _out(out)


def cmd_draft_optimise(args) -> int:
    import time

    wds = [x for w in args.weekday for x in str(w).split(",") if x] or None
    with _api_open(args) as api:
        d = _resolve_draft(api, args.draft)
        job = _post(api, d["id"], "optimise", {"weekdays": wds, "seconds": args.seconds, "reason": args.reason,
                                              "expected_version": _version(api, d["id"])})
        _log("最適化を始めました（計算の間、画面にも出ています）…")
        for _ in range(int(args.seconds * (len(wds) if wds else 6)) + 120):
            job = api.get(f"/template-ws/jobs/{job['id']}")
            if job["status"] in ("done", "failed"):
                break
            time.sleep(2)
        if job["status"] != "done":
            raise RkError(job.get("error") or "最適化が終わりませんでした")
        v = api.get(f"/template-ws/drafts/{d['id']}")
    out = _summary(v)
    out["結果"] = job.get("result")
    try:
        out["何をどう変えたか"] = explain_summary(api_report(args, d["id"]))
    except Exception:  # noqa: BLE001 — 報告が読めなくても最適化の結果は返す
        pass
    return _out(out)


def api_report(args, did: str) -> dict:
    with _api_open(args) as api:
        return api.get(f"/template-ws/drafts/{did}/report")


def cmd_draft_whatif(args) -> int:
    body: dict = {"reason": args.reason, "clear": args.clear}
    if args.staff_count:
        body["staff_count"] = {k.strip(): int(v) for k, v in (x.split("=") for x in args.staff_count.split(","))}
    if args.drop_staff:
        body["drop_staff"] = [c for x in args.drop_staff for c in x.split(",") if c]
    if args.pref:
        prefs: dict = {}
        for x in args.pref:
            code, rest = x.split(":", 1)
            wd, rng = rest.split("=", 1)
            prefs.setdefault(code, {})[wd] = rng
        body["prefs"] = prefs
    if args.red:
        body["red"] = args.red
    with _api_open(args) as api:
        d = _resolve_draft(api, args.draft)
        res = _post(api, d["id"], "whatif", {**body, "expected_version": _version(api, d["id"])})
    return _out(_summary(res["view"]))


def _hist(args, tail: str) -> int:
    with _api_open(args) as api:
        d = _resolve_draft(api, args.draft)
        res = _post(api, d["id"], tail, {"expected_version": _version(api, d["id"])})
    return _out(_summary(res["view"]))


def cmd_draft_undo(args) -> int:
    return _hist(args, "undo")


def cmd_draft_redo(args) -> int:
    return _hist(args, "redo")
