"""rk week — 週を空にする／型から作る の「確認だけ」（dry_run:true 固定・DB は変わらない）。

  rk week preview-clear    --year 2026 --week 43 [--office <拠点 ID>]   … 消える件数・カイポケとつながっている件数・残る打刻済み
  rk week preview-generate --year 2026 --week 43 [--office <拠点 ID>]   … 型から足す件数（予定がある週は 409 のメッセージ）

反映は案（clear-week → generate-week）にして了承を取ってから rk apply。ここでは書かない。
"""

from __future__ import annotations

import json

from rk.opsbase import ApiError


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _body(args) -> dict:
    # dry_run は常に true（引数で変えられない）
    return {"dry_run": True, **({"office_id": args.office} if args.office else {})}


def cmd_preview_clear(args) -> int:
    from rk.session import open_api
    path = f"/schedule/v2/weeks/{int(args.year)}/{int(args.week)}/clear"
    with open_api(args.cfg) as api:
        res = api.call("POST", path, _body(args)) or {}
    out = {k: res.get(k) for k in ("to_delete", "kaipoke_linked", "kept_checked_in")}
    print(f"週{args.week} を空にすると、消える予定は {out['to_delete']} 件"
          f"（うちカイポケとつながっているもの {out['kaipoke_linked']} 件）。"
          f"打刻・実績のある訪問 {out['kept_checked_in']} 件は残ります。まだ何も変えていません。")
    _print(out)
    return 0


def cmd_preview_generate(args) -> int:
    from rk.session import open_api
    body = {"iso_year": int(args.year), "iso_week": int(args.week), **_body(args)}
    with open_api(args.cfg) as api:
        try:
            res = api.call("POST", "/schedule/generate-week-only", body) or {}
        except ApiError as e:
            if e.status != 409:
                raise
            msg = "この週にはもう予定があります。作り直すなら、先に週を空にする（clear-week）案が要ります。"
            print(msg + " まだ何も変えていません。")
            _print({"conflict": True, "status": 409, "message": msg, "detail": e.body})
            return 0
    print(f"週{args.week} を型から作ると、{res.get('to_add')} 件の訪問が増えます（担当は付きません）。まだ何も変えていません。")
    _print({"to_add": res.get("to_add")})
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("week", help="週を空にする／型から作る の確認だけ（dry_run 固定・書かない）")
    sub = p.add_subparsers(dest="week_cmd", metavar="<preview-clear|preview-generate>")
    for name, fn, h in (("preview-clear", cmd_preview_clear, "週を空にしたとき消える件数"),
                        ("preview-generate", cmd_preview_generate, "型から週を作ったとき増える件数")):
        q = sub.add_parser(name, help=h)
        q.add_argument("--year", type=int, required=True, help="ISO 年")
        q.add_argument("--week", type=int, required=True, help="ISO 週")
        q.add_argument("--office", help="拠点 ID（省略 = 全拠点）")
        q.set_defaults(func=fn)
    p.set_defaults(func=lambda args: (p.print_help(), 1)[1])
