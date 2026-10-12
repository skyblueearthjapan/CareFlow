"""読み取り専用の SQL（ssh → docker exec psql。extract.py と同じ道）。

守り: ① begin transaction read only ② PGOPTIONS=default_transaction_read_only=on ③ 最後は rollback
④ 1 文だけ（途中の ; を拒む）⑤ ON_ERROR_STOP。結果は json_agg で 1 つの JSON 配列。
パラメータ: rk sql name -p key=value → psql -v key=value。SQL では :'key'（文字列）/ :key（そのまま）で使う。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rk import RkError
from rk.config import Config
from rk.transport import Runner, default_runner, remote_cmd

PARAM_KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
SQL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def parse_params(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs or []:
        if "=" not in p:
            raise RkError(f"-p は key=value の形です: {p}")
        k, v = p.split("=", 1)
        validate_param(k, v)
        out[k] = v
    return out


def validate_param(key: str, value: str) -> None:
    if not PARAM_KEY_RE.match(key):
        raise RkError(f"パラメータ名は英小文字・数字・_ です: {key}")
    if "\n" in value or "\r" in value or "\0" in value:
        raise RkError(f"パラメータ {key} の値に改行は使えません")


_IDENT = re.compile(r"[A-Za-z0-9_$]")
_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def _scan(sql: str) -> str:
    r"""1 回の走査で SQL を調べ、末尾の ; とそれ以降のコメントを除いた本体を返す。

    文字列 '...'（'' の重ね）・E'...'（\ の逃がし）・$tag$...$tag$・"識別子"・-- と /* 入れ子 */ の
    コメントの外にある ; は末尾の 1 つだけ許す。外にある \（psql のメタコマンド）と、
    :'name' 以外の psql 変数（:name・:"name"・:{?name}）は拒む（::型 の変換は可）。
    """
    n = len(sql)
    i = 0
    semi = None       # 末尾の ; の位置
    after_semi = False  # ; の後に意味のある字が出たか
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if c == "-" and nxt == "-":
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "/" and nxt == "*":
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise RkError("SQL のコメント /* が閉じていません")
            continue
        if c.isspace():
            i += 1
            continue
        if semi is not None:
            after_semi = True
            break
        if c == "'":
            esc = i > 0 and sql[i - 1] in "Ee" and not (i > 1 and _IDENT.match(sql[i - 2]))
            i += 1
            while True:
                if i >= n:
                    raise RkError("SQL の文字列 ' が閉じていません")
                ch = sql[i]
                if esc and ch == "\\":
                    i += 2
                    continue
                if ch == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if c == '"':
            i += 1
            while True:
                if i >= n:
                    raise RkError('SQL の識別子 " が閉じていません')
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if c == "$" and not (i > 0 and _IDENT.match(sql[i - 1])):
            m = _DOLLAR_TAG.match(sql, i)
            if m:
                tag = m.group(0)
                j = sql.find(tag, m.end())
                if j < 0:
                    raise RkError(f"SQL の {tag} が閉じていません")
                i = j + len(tag)
                continue
        if c == "\\":
            raise RkError("SQL に \\ （psql のメタコマンド）は使えません")
        if c == ":":
            if nxt == ":":
                i += 2
                continue
            if nxt == "'":
                m = re.match(r":'([a-z_][a-z0-9_]*)'", sql[i:])
                if not m:
                    raise RkError("psql の変数は :'name'（英小文字・数字・_）の形で書いてください")
                i += m.end()
                continue
            if nxt and (nxt.isalpha() or nxt in '_"{'):
                raise RkError("psql の変数は :'name' の形だけ使えます（:name・:\"name\" は不可）")
            i += 1
            continue
        if c == ";":
            semi = i
            i += 1
            continue
        i += 1
    if after_semi:
        raise RkError("SQL は 1 文だけにしてください（; の後に続きがあります）")
    return (sql[:semi] if semi is not None else sql).strip()


def wrap_sql(sql: str) -> str:
    body = _scan(sql)
    if not body:
        raise RkError("SQL が空です")
    return (
        "set default_transaction_read_only = on;\n"
        "begin transaction read only;\n"
        "select coalesce(json_agg(t), '[]'::json) from (\n"
        f"{body}\n"
        ") t;\n"
        "rollback;\n"
    )


def psql_argv(cfg: Config, params: dict[str, str]) -> list[str]:
    argv = [
        "docker", "exec", "-i", "-e", "PGOPTIONS=-c default_transaction_read_only=on",
        cfg.pg_container, "psql", "-U", cfg.pg_user, "-d", cfg.pg_db,
        "-X", "-q", "-v", "ON_ERROR_STOP=1", "-At",
    ]
    for k, v in params.items():
        validate_param(k, v)
        argv += ["-v", f"{k}={v}"]
    return argv


def run_sql(cfg: Config, sql: str, params: dict[str, str] | None = None, runner: Runner = default_runner,
            name: str | None = None) -> Any:
    script = wrap_sql(sql)  # relay でも先にここで形を確かめる（中継も同じ検査をする）
    if cfg.mode == "relay":
        from rk.transport import relay_client
        for k, v in (params or {}).items():
            validate_param(k, v)
        return relay_client(cfg).request("POST", "/rk/sql", {"sql": sql, "params": params or {}, "name": name})
    cmd = remote_cmd(cfg, psql_argv(cfg, params or {}))
    res = runner(cmd, script.encode("utf-8"), 600)
    out = (res.stdout or b"").decode("utf-8", "replace")
    if res.returncode != 0:
        err = (res.stderr or b"").decode("utf-8", "replace")
        raise RkError("SQL の実行に失敗しました: " + err.strip()[:800])
    return parse_output(out)


def parse_output(out: str) -> Any:
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("["):
            text = "\n".join(lines[i:]).strip()
            # rollback などの表示が後ろに付いた場合に備え、最後の ] までを読む
            text = text[: text.rfind("]") + 1]
            try:
                return json.loads(text)
            except ValueError as e:
                raise RkError(f"SQL の結果を JSON として読めません: {e}") from None
    raise RkError("SQL の結果に JSON がありません: " + out.strip()[:300])


def sql_path(cfg: Config, name: str) -> Path:
    if not SQL_NAME_RE.match(name):
        raise RkError(f"SQL の名前は英小文字・数字・-・_ です: {name}")
    p = cfg.sql_dir / f"{name}.sql"
    if not p.exists():
        raise RkError(f"{name}.sql がありません（rk sql --list で一覧）")
    return p


def declared_params(text: str) -> list[str]:
    """先頭のコメント行「… | params: a, b」から引数名を読む（「(なし)」は 0 個）。"""
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if not s.startswith("--") or "params:" not in s:
            return []
        names = s.split("params:", 1)[1]
        return [n.strip() for n in names.split(",") if n.strip() and not n.strip().startswith("(")]
    return []


def fill_params(text: str, params: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """宣言された引数で渡されていないものを空文字で補う（psql の :'x' は未定義だと失敗する）。
    戻り値: (補った引数, 宣言に無い引数名の一覧)"""
    declared = declared_params(text)
    filled = {k: "" for k in declared}
    filled.update(params)
    unknown = [k for k in params if declared and k not in declared]
    return filled, unknown


def list_sql(cfg: Config) -> list[dict]:
    out = []
    if not cfg.sql_dir.exists():
        return out
    for p in sorted(cfg.sql_dir.glob("*.sql")):
        desc = ""
        for ln in p.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s.startswith("--"):
                desc = s.lstrip("-").strip()
                break
            if s:
                break
        out.append({"name": p.stem, "description": desc})
    return out
