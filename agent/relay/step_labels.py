"""作業の言葉（step の label）。道具の名前・パス・SQL は画面に出さない（PERSONA §3・設計 §1-5）。"""

from __future__ import annotations

import re
import shlex

GENERIC = "作業しています…"

_RK_RE = re.compile(r"(?:python3?\s+-m\s+rk|(?:^|[\s/;&|(])rk(?:\.cmd)?)\s+(.*)$")

_RK_FIXED = {
    "whoami": "つながりを確かめています…",
    "post-read": "候補を調べています…",
    "level": "振り分け方を考えています…",
    "courseplan": "コースの組み方を考えています…",
    "backup": "バックアップを取っています…",
    "apply": "反映しています…",
    "verify": "反映を確かめています…",
    "undo": "元に戻しています…",
    "progress": "進み具合を知らせています…",
}
_RK_PLAN = {
    "check": "案を確かめています…",
    "report": "案を紙にまとめています…",
    "submit": "案をまとめています…",
    "resolve": "入らない訪問の扱いを書き込んでいます…",
}
#: rk get のパスの頭 → 言葉（先に一致したもの）。
_GET_PATHS = [
    ("/auth/me", "つながりを確かめています…"),
    ("/visit-history", "打刻の記録を見ています…"),
    ("/visits", "予定を見ています…"),
    ("/schedule", "予定を見ています…"),
    ("/staff", "職員の予定を見ています…"),
    ("/patients", "利用者さんの情報を見ています…"),
    ("/courses", "コースを見ています…"),
    ("/dashboard", "数字を集めています…"),
    ("/integrations", "カイポケとの違いを見ています…"),
    ("/kaipoke", "カイポケとの違いを見ています…"),
    ("/offices", "拠点を見ています…"),
]
#: rk sql の名前に含まれる語 → 言葉。
_SQL_WORDS = [
    ("staff_week_overrides", "職員のお休みを確かめています…"),
    ("leave", "職員のお休みを確かめています…"),
    ("checkin", "打刻の記録を見ています…"),
    ("accompan", "同行を見ています…"),
    ("special", "特別訪問週間を見ています…"),
    ("pool", "保留プールを見ています…"),
    ("kaipoke", "カイポケとの違いを見ています…"),
    ("staff", "職員を見ています…"),
    ("patient", "利用者さんの情報を見ています…"),
    ("visit", "予定を見ています…"),
    ("fixed", "固定訪問の枠を見ています…"),
]


def _rk_label(rest: str) -> str:
    try:
        argv = shlex.split(rest, posix=True)
    except ValueError:
        argv = rest.split()
    if not argv:
        return "らく助の記録を見ています…"
    sub = argv[0]
    if sub in _RK_FIXED:
        return _RK_FIXED[sub]
    if sub == "plan":
        return _RK_PLAN.get(argv[1] if len(argv) > 1 else "", "案を確かめています…")
    if sub == "get":
        path = argv[1] if len(argv) > 1 else ""
        for head, label in _GET_PATHS:
            if path.startswith(head) or path.startswith("/api/v1" + head):
                return label
        return "らく助の記録を見ています…"
    if sub == "sql":
        if "--list" in argv:
            return "調べ方の一覧を見ています…"
        name = next((a for a in argv[1:] if not a.startswith("-")), "")
        for word, label in _SQL_WORDS:
            if word in name:
                return label
        return "らく助の記録を見ています…"
    return GENERIC


def label_for(tool_name: str, tool_input: dict | None) -> str:
    inp = tool_input or {}
    if tool_name in ("Bash", "PowerShell"):
        cmd = str(inp.get("command") or "")
        for part in re.split(r"[\r\n]+|&&|;", cmd):
            m = _RK_RE.search(part.strip())
            if m:
                return _rk_label(m.group(1))
        # rk 以外（Codex はシェルでスキルを読み、python で計算する）。道具名・パスは出さない
        if re.search(r"(SKILL|AGENTS|PERSONA|README|questions)\.md|[/\\]skills[/\\]", cmd):
            return "やり方を確かめています…"
        head = re.split(r"\s+", cmd.strip(), maxsplit=1)[0].rsplit("/", 1)[-1].lower() if cmd.strip() else ""
        if head in ("cat", "head", "tail", "sed", "less", "get-content", "type", "nl", "wc", "jq"):
            return "資料を読んでいます…"
        if head in ("ls", "find", "rg", "grep", "tree", "get-childitem", "dir", "select-string"):
            return "資料を探しています…"
        if head.startswith("python") or head in ("node", "awk"):
            return "計算しています…"
        return GENERIC
    if tool_name == "Read":
        path = str(inp.get("file_path") or "")
        if re.search(r"(SKILL|AGENTS|PERSONA|README|questions)\.md$", path) or "/skills/" in path.replace("\\", "/"):
            return "やり方を確かめています…"
        return "資料を読んでいます…"
    if tool_name in ("Glob", "Grep"):
        return "資料を探しています…"
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return "メモを書いています…"
    if tool_name == "TodoWrite":
        return "段取りを整えています…"
    if tool_name == "Skill":
        return "やり方を確かめています…"
    if tool_name in ("Task", "Agent"):
        return "手分けして調べています…"
    return GENERIC
