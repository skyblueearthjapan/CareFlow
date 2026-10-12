"""追加のコマンド。このフォルダの各モジュールが register(subparsers) を持ち、cli が自動で読み込む。

決まり: register の中で `p = subparsers.add_parser("name", help=...)` を作り、
`p.set_defaults(func=handler)` を付ける。handler(args) -> int（終了コード）。
args.cfg に rk.config.Config が入る。API は `with rk.session.open_api(args.cfg) as api:`、
SQL は `rk.sqlrun.run_sql(args.cfg, sql_text, params)` を使う。
"""
