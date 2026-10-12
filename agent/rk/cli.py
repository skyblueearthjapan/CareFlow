"""rk のコマンド（設計書 §3-2）。追加のコマンドは rk/commands/*.py の register(subparsers) で足す。"""

from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import re
import sys
from pathlib import Path
from typing import Any

from rk import RkError
from rk.config import load_config


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _kv(pairs: list[str] | None) -> dict[str, str]:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise RkError(f"key=value の形です: {p}")
        k, v = p.split("=", 1)
        out[k] = v
    return out


_MSYS_PATH_RE = re.compile(r"^[A-Za-z]:[/\\]")


def _api_path(path: str) -> str:
    """Git Bash（MSYS）は "/patients/…" を "C:/Program Files/Git/patients/…" に書き換える。分かる形で止める。"""
    if not path.startswith("/") and _MSYS_PATH_RE.match(path):
        raise RkError(f"Git Bash がパスを書き換えました（{path}）。agent/bin/rk を使うか、"
                      "MSYS2_ARG_CONV_EXCL='*' を付けてください")
    return path


def _open_api(args):
    from rk.session import open_api
    return open_api(args.cfg)


# ---------- 各コマンド ----------
def cmd_whoami(args) -> int:
    cfg = args.cfg
    with _open_api(args) as api:
        me = api.get("/auth/me")
    who = {k: me.get(k) for k in ("id", "username", "email", "display_name", "name", "role") if isinstance(me, dict) and k in me}
    print(f"つながりました（RK_ENV={cfg.env}・RK_MODE={cfg.mode}" + (f"・ssh {cfg.ssh}" if cfg.mode == "pc" else "") + "）")
    _print_json(who or me)
    return 0


def cmd_get(args) -> int:
    _api_path(args.path)
    with _open_api(args) as api:
        _print_json(api.get(args.path, _kv(args.query) or None))
    return 0


def cmd_post_read(args) -> int:
    from rk.readonly_posts import READONLY_POSTS, is_readonly_post
    _api_path(args.path)
    if not is_readonly_post(args.path):
        raise RkError(f"{args.path} は読むだけの POST の許可リストにありません。使えるもの: "
                      + "、".join(t for t, _ in READONLY_POSTS))
    raw = sys.stdin.read() if args.json == "-" else Path(args.json).read_text(encoding="utf-8")
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError as e:
        raise RkError(f"--json の中身が JSON ではありません: {e}") from None
    with _open_api(args) as api:
        _print_json(api.call("POST", args.path, body))
    return 0


def cmd_sql(args) -> int:
    from rk import sqlrun
    cfg = args.cfg
    if args.list:
        items = sqlrun.list_sql(cfg)
        if not items:
            print(f"SQL がありません（{cfg.sql_dir}）")
        for it in items:
            print(f"{it['name']:32s} {it['description']}")
        return 0
    params = sqlrun.parse_params(args.param)
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.name:
        text = sqlrun.sql_path(cfg, args.name).read_text(encoding="utf-8")
        params, unknown = sqlrun.fill_params(text, params)
        if unknown:
            raise RkError(f"{args.name}.sql に無い引数です: {', '.join(unknown)}（rk sql --list と sql/README.md を確認）")
    else:
        raise RkError("SQL の名前か --file か --list を指定してください")
    _print_json(sqlrun.run_sql(cfg, text, params, name=args.name if not args.file else None))
    return 0


def cmd_plan_check(args) -> int:
    from rk.plan import check_plan, load_plan, not_undoable_ops
    plan = load_plan(args.plan)
    errs = check_plan(plan)
    from rk.resolve import unresolved_lines
    left = unresolved_lines(plan)
    if errs:
        print(f"案に誤りがあります（{len(errs)} 件）:")
        for e in errs:
            print(f"  - {e}")
        if left:
            print(f"まだ決めていない入らない訪問（{len(left)} 件・rk plan resolve で決める）:")
            for x in left:
                print(f"  - {x}")
        return 1
    print(f"案は正しい形です: {plan['title']}（操作 {len(plan['ops'])} 件）")
    from rk.report_v2 import proposal_warnings
    for w in proposal_warnings(plan):
        print(f"  注意（誤りではありません）: {w}")
    nu = not_undoable_ops(plan)
    if nu:
        print(f"  API で戻せない操作が {len(nu)} 件あります（戻すにはバックアップから）: "
              + "、".join(plan["ops"][i].get("label", "") for i in nu))
    for c in plan.get("checks") or []:
        print(f"  {c}")
    return 0


def cmd_plan_resolve(args) -> int:
    from rk.plan import load_plan
    from rk.resolve import resolve, unresolved_lines
    plan = load_plan(args.plan)
    made = resolve(plan, args.all or args.choice, approved=args.approved, key=args.visit, all_=bool(args.all),
                   staff=args.staff, start=args.start)
    Path(args.plan).write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    for r in made:
        print(f"決めました: {r['label']} → {r['choice_ja']}"
              + (f"（{r['staff']}）" if r.get("staff") else "") + (f"（{r['start']}）" if r.get("start") else ""))
    left = unresolved_lines(plan)
    if left:
        print(f"まだ決めていない入らない訪問: {len(left)} 件")
        for x in left:
            print(f"  - {x}")
    print("この案は反映できる形になりました（次に rk plan check → rk plan report → 了承 → rk backup → rk apply）"
          if plan["ready"] else "まだ反映できません: " + "／".join(plan["blockers"]))
    return 0


def cmd_plan_report(args) -> int:
    from rk.plan import check_plan, load_plan
    from rk.report import write_report as write_report_v1
    from rk.report_v2 import write_report as write_report_v2
    write_report = write_report_v1 if getattr(args, "v1", False) else write_report_v2  # 既定は v2（--v1 で旧形式）
    plan = load_plan(args.plan)
    errs = check_plan(plan)
    if errs:
        print("注意: 案に誤りがあります（帳票は作ります）:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
    path = write_report(args.cfg, plan, a3=args.a3, out=Path(args.out) if args.out else None)
    print(f"帳票を作りました: {path}")
    return 0


def cmd_plan_submit(args) -> int:
    from rk.plan import check_plan, load_plan, readiness_errors
    from rk.transport import relay_client
    plan = load_plan(args.plan)
    errs = [e for e in check_plan(plan) if e not in readiness_errors(plan)]
    if errs:
        raise RkError("案に誤りがあります（画面には出しません）:\n  " + "\n  ".join(errs))
    res = relay_client(args.cfg).request("POST", "/rk/plans", {"plan": plan})
    print(f"案 {res.get('version')} を画面に出しました: {plan['title']}")
    if res.get("ready") is False:
        print("まだ反映できない案です（決めていないことがあります）: " + "／".join(res.get("blockers") or []))
    else:
        print("反映するかどうかは、人が画面のボタンで決めます。承認番号（ap_…）が届くまで反映しないでください。")
    return 0


def cmd_progress(args) -> int:
    text = " ".join(args.text).strip()[:80]
    if args.cfg.mode != "relay":
        print(f"（進み具合）{text}", file=sys.stderr)  # 画面が無いときは表示だけ
        return 0
    from rk.transport import relay_client
    relay_client(args.cfg).request("POST", "/rk/progress", {"text": text})
    return 0


def cmd_backup(args) -> int:
    from rk.backup import run_backup
    rec = run_backup(args.cfg, args.label)
    print(f"バックアップを取りました: {rec['path']}（{rec['size']:,} バイト・{rec['at_utc']}）")
    if rec.get("ls"):
        print(rec["ls"])
    return 0


APPROVAL_CODE_RE = re.compile(r"^ap_[A-Za-z0-9_-]{8,64}$")


def _relay_approval(args) -> str:
    code = (args.approved or "").strip()
    if not APPROVAL_CODE_RE.match(code):
        raise RkError("中継の中では --approved に画面の承認番号（ap_…）だけを書きます。"
                      "承認番号は人が画面の［この案で反映する］を押したときに届きます")
    if getattr(args, "backup", None):
        raise RkError("中継の中では --backup は使えません（バックアップは中継が取ります）")
    return code


def _relay_apply(args, plan: dict) -> int:
    from rk.applyrun import summary_ja
    from rk.transport import relay_client
    code = _relay_approval(args)
    if args.no_basis_check:
        raise RkError("中継の中では --no-basis-check は使えません（予定が変わっていないかは中継が必ず確かめます）")
    print("中継に反映を頼みました（承認番号を確かめ、バックアップを取ってから 1 件ずつ反映します）…", file=sys.stderr)
    res = relay_client(args.cfg).request("POST", "/rk/apply", {"plan": plan, "approval": code})
    print(summary_ja(res))
    return 0 if res.get("ok") else 2


def cmd_apply(args) -> int:
    from rk.applyrun import apply_plan, require_approval_and_backup, summary_ja
    from rk.plan import check_plan, load_plan
    plan = load_plan(args.plan)
    errs = check_plan(plan)
    if errs:
        raise RkError("案に誤りがあります:\n  " + "\n  ".join(errs))
    if args.cfg.mode == "relay":
        return _relay_apply(args, plan)
    require_approval_and_backup(args.cfg, args.approved, args.backup)  # つなぐ前に確かめる
    basis = None
    if (plan.get("source") or {}).get("basis_hash"):
        if args.no_basis_check:
            print("注意: 案を作った後に予定が変わっていないかを確かめずに反映します（--no-basis-check）", file=sys.stderr)
        else:
            from rk.applyrun import check_basis
            print("案を作った後に予定が変わっていないかを確かめています（読むだけ）…", file=sys.stderr)
            basis = check_basis(args.cfg, plan)
    with _open_api(args) as api:
        res = apply_plan(args.cfg, plan, api, args.approved, args.backup,
                         source={"plan_path": str(Path(args.plan).resolve()), "basis_checked": basis,
                                 "basis_check_skipped": bool(args.no_basis_check)})
    print(summary_ja(res))
    return 0 if res["ok"] else 2


def cmd_verify(args) -> int:
    from rk.applyrun import RUN_ID_RE, verify_plan
    from rk.plan import load_plan
    src = args.plan
    if args.cfg.mode == "relay" and RUN_ID_RE.match(src) and not Path(src).exists():
        from rk.transport import relay_client
        plan = relay_client(args.cfg).request("GET", f"/rk/runs/{src}/plan")
    else:
        if RUN_ID_RE.match(src) and (args.cfg.runs_dir / src / "plan.json").exists():
            src = args.cfg.runs_dir / src / "plan.json"
        plan = load_plan(src)
    with _open_api(args) as api:
        results = verify_plan(plan, api)
    bad = [r for r in results if not r["ok"]]
    for r in results:
        mark = "OK" if r["ok"] else "NG"
        print(f"{r['i'] + 1:3d} {mark} {r['label']}" + ("" if r["ok"] else "  → " + "・".join(r["problems"])))
    print(f"照合: {len(results) - len(bad)} / {len(results)} 件が案のとおり" + ("" if not bad else f"・{len(bad)} 件が食い違い"))
    return 0 if not bad else 2


def cmd_undo(args) -> int:
    from rk.applyrun import require_approval_and_backup, resolve_run_dir, summary_ja, undo_run
    if args.cfg.mode == "relay":
        from rk.applyrun import RUN_ID_RE
        from rk.transport import relay_client
        code = _relay_approval(args)
        if not RUN_ID_RE.match(args.run_id or ""):
            raise RkError(f"run-id の形が違います: {args.run_id}")
        res = relay_client(args.cfg).request("POST", "/rk/undo",
                                             {"run_id": args.run_id, "approval": code, "partial": bool(args.partial)})
        print(summary_ja(res))
        return 0 if res.get("ok") else 2
    resolve_run_dir(args.cfg, args.run_id)
    require_approval_and_backup(args.cfg, args.approved, args.backup)
    with _open_api(args) as api:
        res = undo_run(args.cfg, args.run_id, api, args.approved, args.backup, partial=args.partial)
    print(summary_ja(res))
    return 0 if res["ok"] else 2


# ---------- 組み立て ----------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rk", description="らく助エージェントの道具（本番は読むのが基本・書くのは案と了承のあと）")
    sub = ap.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("whoami", help="つながるか・誰として動くか")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser("get", help="GET（JSON を出力）")
    p.add_argument("path")
    p.add_argument("-q", "--query", action="append", metavar="k=v")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("post-read", help="読むだけの POST（許可リストのものだけ）")
    p.add_argument("path")
    p.add_argument("--json", required=True, metavar="FILE|-")
    p.set_defaults(func=cmd_post_read)

    p = sub.add_parser("sql", help="名前つきの読み取り SQL（読み取り専用トランザクション）")
    p.add_argument("name", nargs="?")
    p.add_argument("-p", "--param", action="append", metavar="key=value")
    p.add_argument("--list", action="store_true")
    p.add_argument("--file")
    p.set_defaults(func=cmd_sql)

    p = sub.add_parser("plan", help="案の検査・帳票")
    psub = p.add_subparsers(dest="plan_command", metavar="<check|report|resolve|submit>")
    q = psub.add_parser("check", help="案の形と操作の許可を検査")
    q.add_argument("plan")
    q.set_defaults(func=cmd_plan_check)
    q = psub.add_parser("resolve", help="入らない訪問をどうするか決めて案に書き込む（決めた人の記録が要る）")
    q.add_argument("plan")
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--visit", metavar="ID の頭|名前の一部", help="決める訪問（visit id の頭か、表示名の一部）")
    g.add_argument("--all", choices=["pool", "allow-over", "keep"], help="まだ決めていない全部を同じ選択で")
    q.add_argument("--choice", choices=["pool", "allow-over", "keep", "shift"])
    q.add_argument("--staff", metavar="職員コード|UUID")
    q.add_argument("--start", metavar="HH:MM")
    q.add_argument("--approved", required=True, metavar="誰が・いつ")
    q.set_defaults(func=cmd_plan_resolve)
    q = psub.add_parser("report", help="案を A4（--a3 で A3 横の時間割）の HTML に")
    q.add_argument("plan")
    q.add_argument("--a3", action="store_true")
    q.add_argument("--v1", action="store_true", help="旧形式の帳票（既定は結論・理由つきの v2）")
    q.add_argument("--out")
    q.set_defaults(func=cmd_plan_report)

    q = psub.add_parser("submit", help="（中継の中だけ）案を画面に出す。反映は人が画面で承認してから")
    q.add_argument("plan")
    q.set_defaults(func=cmd_plan_submit)

    p = sub.add_parser("progress", help="長い作業の途中経過を 1 行で画面に出す（中継の中だけ。ほかでは表示だけ）")
    p.add_argument("text", nargs="+")
    p.set_defaults(func=cmd_progress)

    p = sub.add_parser("backup", help="VPS で pg_dump")
    p.add_argument("--label", required=True)
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("apply", help="案を反映（了承とバックアップが要る）")
    p.add_argument("plan")
    p.add_argument("--approved", required=True, metavar="誰が・いつ")
    p.add_argument("--backup", metavar="PATH", help="rk backup で記録した 30 分以内のバックアップのうち、使うもののパス（記録に無いものは断る）")
    p.add_argument("--no-basis-check", action="store_true",
                   help="案の source.basis_hash（作った時の週の形）を読み直して比べるのを飛ばす（PO の了承のうえで）")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("verify", help="反映後に読み直して案と照合（plan.json か run-id）")
    p.add_argument("plan")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("undo", help="反映を元に戻す（了承とバックアップが要る）")
    p.add_argument("run_id")
    p.add_argument("--approved", required=True, metavar="誰が・いつ")
    p.add_argument("--backup", metavar="PATH")
    p.add_argument("--partial", action="store_true", help="API で戻せない操作を残して戻せる分だけ戻す")
    p.set_defaults(func=cmd_undo)

    load_plugins(sub)
    return ap


def load_plugins(sub) -> list[str]:
    """rk/commands/*.py の register(subparsers) を呼ぶ。壊れた 1 つで全体を止めない。"""
    import rk.commands as pkg
    loaded = []
    for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if m.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"rk.commands.{m.name}")
            reg = getattr(mod, "register", None)
            if reg is None:
                print(f"警告: rk/commands/{m.name}.py に register がありません", file=sys.stderr)
                continue
            reg(sub)
            loaded.append(m.name)
        except Exception as e:
            print(f"警告: rk/commands/{m.name}.py を読み込めません: {type(e).__name__}: {e}", file=sys.stderr)
    return loaded


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    try:
        args.cfg = load_config()
        return int(args.func(args) or 0)
    except RkError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("中断しました", file=sys.stderr)
        return 130
    except Exception as e:
        from rk.opsbase import ApiError
        if isinstance(e, ApiError):
            print(f"エラー: API が HTTP {e.status} を返しました", file=sys.stderr)
            _print_json(e.body) if isinstance(e.body, (dict, list)) else print(str(e.body)[:1500], file=sys.stderr)
            return 1
        raise
