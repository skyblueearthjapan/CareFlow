"""rk week — 週を空にする／型から作る の「確認だけ」（dry_run:true 固定・DB は変わらない）。

  rk week preview-clear    --year 2026 --week 43 [--office <拠点 ID>] [--from D --to D]   … 消える件数・カイポケとつながっている件数・残る打刻済み
  rk week preview-generate --year 2026 --week 43 [--office <拠点 ID>] [--from D --to D]   … 型から足す件数（予定がある週は 409 のメッセージ）
--from / --to（YYYY-MM-DD・組で・その週の中）を付けると、その日だけを見る（期間で組む・範囲の外の日はそのまま）。

反映は案（clear-week → generate-week）にして了承を取ってから rk apply。ここでは書かない。
"""

from __future__ import annotations

import json

from rk import RkError
from rk.opsbase import ApiError


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _range(args) -> dict:
    """--from / --to を週の範囲として検査して本文に足す（操作 clear-week・generate-week と同じ検査）。"""
    if args.date_from is None and args.date_to is None:
        return {}
    from rk.ops.week import _range_errors
    op = {"iso_year": int(args.year), "iso_week": int(args.week), "date_from": args.date_from, "date_to": args.date_to}
    errs = _range_errors(op)
    if errs:
        raise RkError("／".join(e.replace("date_from", "--from").replace("date_to", "--to") for e in errs))
    return {"date_from": args.date_from, "date_to": args.date_to}


def _where(args) -> str:
    r = _range(args)
    return f"週{args.week} の {r['date_from']}〜{r['date_to']}" if r else f"週{args.week}"


def _body(args) -> dict:
    # dry_run は常に true（引数で変えられない）
    return {"dry_run": True, **({"office_id": args.office} if args.office else {}), **_range(args)}


def cmd_preview_clear(args) -> int:
    from rk.session import open_api
    path = f"/schedule/v2/weeks/{int(args.year)}/{int(args.week)}/clear"
    body = _body(args)  # 範囲の誤りはつなぐ前に知らせる
    with open_api(args.cfg) as api:
        res = api.call("POST", path, body) or {}
    out = {k: res.get(k) for k in ("to_delete", "kaipoke_linked", "kept_checked_in")}
    out.update(_range(args))
    print(f"{_where(args)} を空にすると、消える予定は {out['to_delete']} 件"
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
            msg = (f"{_where(args)} にはもう予定があります。作り直すなら、先に同じ範囲を空にする（clear-week）案が要ります。"
                   if _range(args) else "この週にはもう予定があります。作り直すなら、先に週を空にする（clear-week）案が要ります。")
            print(msg + " まだ何も変えていません。")
            _print({"conflict": True, "status": 409, "message": msg, "detail": e.body})
            return 0
    print(f"{_where(args)} を型から作ると、{res.get('to_add')} 件の訪問が増えます（担当は付きません）。まだ何も変えていません。")
    _print({"to_add": res.get("to_add"), **_range(args)})
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
        q.add_argument("--from", dest="date_from", help="範囲の始めの日 YYYY-MM-DD（--to と組・その週の中。省略 = 週全体）")
        q.add_argument("--to", dest="date_to", help="範囲の終わりの日 YYYY-MM-DD（--from と組）")
        q.set_defaults(func=fn)
    p.set_defaults(func=lambda args: (p.print_help(), 1)[1])
