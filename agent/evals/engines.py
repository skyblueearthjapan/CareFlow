"""CLI（Claude Code・Codex）を起こして、出てきた記録を共通の形（transcript）にそろえる。

transcript の 1 件:
  {"turn": i, "kind": "message", "text": ...}                     らく助の文（途中の 1 行も含む）
  {"turn": i, "kind": "final", "text": ...}                       そのターンの最後の文
  {"turn": i, "kind": "command", "tool": "Bash"|"exec"|"Read"|..., "text": <コマンドかパス>,
   "ok": bool|None, "output": <先頭 2000 字>, "denied": bool}
  {"turn": i, "kind": "question", "questions": [...]}             AskUserQuestion（Claude）
  {"turn": i, "kind": "error", "text": ...}
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

OUT_MAX = 2000


def _which(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"{name} が見つかりません（PATH）")
    return p


def latest_codex_sol(default: str = "gpt-5.6-sol") -> str:
    """Codex の models キャッシュから gpt-<版>-sol の一番新しいもの（relay/models.py と同じ考え）。"""
    import re
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
        items = data.get("models", data) if isinstance(data, dict) else data
        best = None
        for m in items:
            slug = m.get("slug") if isinstance(m, dict) else None
            mm = re.match(r"^gpt-(\d+(?:\.\d+)*)-sol$", slug or "")
            if mm:
                ver = tuple(int(x) for x in mm.group(1).split("."))
                if best is None or ver > best[0]:
                    best = (ver, slug)
        return best[1] if best else default
    except Exception:
        return default


# ---------- Claude ----------
CLAUDE_ALLOW = ["Bash", "Read", "Glob", "Grep", "Write", "Edit", "Skill", "TodoWrite", "AskUserQuestion"]
CLAUDE_DENY = [
    # 本物のサーバー・データベースへの直の道（rk の写しを通らない）は評価でも閉じる。試みは記録に残る
    "Bash(ssh:*)", "Bash(scp:*)", "Bash(sftp:*)", "Bash(curl:*)", "Bash(wget:*)", "Bash(psql:*)",
    "Bash(docker:*)", "Bash(cloudflared:*)", "WebFetch", "WebSearch", "PowerShell",
    "Read(~/.rakusuke/**)", "Read(~/.ssh/**)", "Read(~/.codex/auth.json)", "Read(~/.claude/.credentials.json)",
]


def _claude_abs(p: str) -> str:
    """C:/x/y → //c/x/y（Claude の許可の書き方で絶対パス）"""
    q = Path(p).resolve().as_posix()
    if len(q) > 1 and q[1] == ":":
        q = "/" + q[0].lower() + q[2:]
    return "/" + q


def claude_files(root: Path, hidden: list[str] | None = None) -> dict[str, Path]:
    mcp = root / "empty-mcp.json"
    mcp.write_text('{"mcpServers": {}}', encoding="utf-8")
    settings = root / "eval-settings.json"
    deny = CLAUDE_DENY + [f"Read({_claude_abs(h)}/**)" for h in hidden or []]
    settings.write_text(json.dumps({"permissions": {"allow": CLAUDE_ALLOW, "deny": deny},
                                    "env": {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return {"mcp": mcp, "settings": settings}


def claude_argv(root: Path, model: str, max_turns: int, resume: str | None,
                hidden: list[str] | None = None) -> list[str]:
    f = claude_files(root, hidden)
    argv = [_which("claude"), "-p", "--output-format", "stream-json", "--verbose",
            "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", str(f["mcp"]),
            "--settings", str(f["settings"]), "--permission-mode", "default",
            "--max-turns", str(max_turns), "--model", model]
    if resume:
        argv += ["--resume", resume]
    return argv


def parse_claude(lines: list[str], turn: int) -> tuple[list[dict], dict]:
    out: list[dict] = []
    meta: dict = {}
    pending: dict[str, dict] = {}
    last_text = ""
    for line in lines:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        t = m.get("type")
        if t == "system" and m.get("subtype") == "init":
            meta["session_id"] = m.get("session_id")
            meta["model"] = m.get("model")
        elif t == "assistant" and not m.get("parent_tool_use_id"):
            for c in (m.get("message") or {}).get("content") or []:
                if c.get("type") == "text" and c.get("text", "").strip():
                    out.append({"turn": turn, "kind": "message", "text": c["text"]})
                    last_text = c["text"]
                elif c.get("type") == "tool_use":
                    name, inp = c.get("name") or "", c.get("input") or {}
                    if name == "AskUserQuestion":
                        out.append({"turn": turn, "kind": "question", "questions": inp.get("questions") or []})
                        continue
                    text = (inp.get("command") or inp.get("file_path") or inp.get("path") or inp.get("pattern")
                            or inp.get("skill") or json.dumps(inp, ensure_ascii=False)[:300])
                    rec = {"turn": turn, "kind": "command", "tool": name, "text": str(text), "ok": None,
                           "output": "", "denied": False}
                    if name in ("Write", "Edit"):
                        rec["content"] = str(inp.get("content") or inp.get("new_string") or "")[:OUT_MAX]
                    out.append(rec)
                    pending[c.get("id") or ""] = rec
        elif t == "user":
            content = (m.get("message") or {}).get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        rec = pending.pop(c.get("tool_use_id") or "", None)
                        if rec is None:
                            continue
                        body = c.get("content")
                        if isinstance(body, list):
                            body = "\n".join(str(x.get("text", "")) for x in body if isinstance(x, dict))
                        body = str(body or "")
                        rec["ok"] = not c.get("is_error")
                        rec["output"] = body[:OUT_MAX]
                        if c.get("is_error") and ("permission" in body.lower() or "denied" in body.lower()
                                                  or "許可" in body):
                            rec["denied"] = True
        elif t == "result":
            meta["session_id"] = m.get("session_id") or meta.get("session_id")
            meta["duration_ms"] = m.get("duration_ms")
            meta["num_turns"] = m.get("num_turns")
            meta["cost_usd"] = m.get("total_cost_usd")
            meta["is_error"] = bool(m.get("is_error"))
            meta["subtype"] = m.get("subtype")
            for d in m.get("permission_denials") or []:
                inp = d.get("tool_input") or {}
                for rec in out:
                    if rec.get("kind") == "command" and rec.get("text") == (inp.get("command") or inp.get("file_path")):
                        rec["denied"] = True
            if m.get("result"):
                last_text = m["result"]
    out.append({"turn": turn, "kind": "final", "text": last_text})
    return out, meta


# ---------- Codex ----------
def codex_argv(root: Path, model: str, effort: str, resume: str | None, network: bool) -> list[str]:
    base = [_which("codex"), "exec"]
    if resume:
        base += ["resume", resume]
    argv = base + ["--json", "--skip-git-repo-check", "--ignore-user-config",
                   "-c", 'windows.sandbox="elevated"', "-c", 'approval_policy="never"',
                   "-c", f'model_reasoning_effort="{effort}"', "-m", model]
    if not resume:
        argv += ["-s", "workspace-write", "-C", str(root / "agent")]
    else:
        argv += ["-c", 'sandbox_mode="workspace-write"']
    if network:
        argv += ["-c", "sandbox_workspace_write.network_access=true"]
    return argv + ["-"]


def parse_codex(lines: list[str], turn: int) -> tuple[list[dict], dict]:
    out: list[dict] = []
    meta: dict = {}
    last_text = ""
    for line in lines:
        try:
            m = json.loads(line)
        except ValueError:
            if "rejected" in line or "blocked by policy" in line:
                out.append({"turn": turn, "kind": "command", "tool": "exec", "text": line[:500], "ok": False,
                            "output": "blocked by policy", "denied": True})
            continue
        t = m.get("type")
        if t == "thread.started":
            meta["session_id"] = m.get("thread_id")
        elif t == "item.completed":
            it = m.get("item") or {}
            ty = it.get("type")
            if ty == "agent_message" and (it.get("text") or "").strip():
                out.append({"turn": turn, "kind": "message", "text": it["text"]})
                last_text = it["text"]
            elif ty == "command_execution":
                out.append({"turn": turn, "kind": "command", "tool": "exec", "text": it.get("command") or "",
                            "ok": it.get("exit_code") == 0, "output": (it.get("aggregated_output") or "")[:OUT_MAX],
                            "denied": False})
            elif ty == "file_change":
                for ch in it.get("changes") or []:
                    out.append({"turn": turn, "kind": "command", "tool": "Write", "text": ch.get("path") or "",
                                "ok": True, "output": "", "denied": False})
            elif ty == "mcp_tool_call":
                out.append({"turn": turn, "kind": "command", "tool": "mcp", "text": json.dumps(it, ensure_ascii=False)[:300],
                            "ok": None, "output": "", "denied": False})
            elif ty == "error":
                out.append({"turn": turn, "kind": "error", "text": it.get("message") or ""})
        elif t == "turn.completed":
            meta["usage"] = m.get("usage")
        elif t in ("turn.failed", "error"):
            out.append({"turn": turn, "kind": "error", "text": json.dumps(m, ensure_ascii=False)[:500]})
            meta["is_error"] = True
    out.append({"turn": turn, "kind": "final", "text": last_text})
    return out, meta


# ---------- 実行 ----------
def run_turn(engine: str, root: Path, prompt: str, *, model: str, env: dict, turn: int, resume: str | None,
             timeout: int, max_turns: int = 40, effort: str = "medium", network: bool = False,
             hidden: list[str] | None = None) -> dict:
    if engine == "claude":
        argv = claude_argv(root, model, max_turns, resume, hidden)
        cwd = root / "agent"
    elif engine == "codex":
        argv = codex_argv(root, model, effort, resume, network)
        cwd = root / "agent"
    else:
        raise ValueError(engine)
    t0 = time.monotonic()
    raw_path = root / f"raw-{engine}-turn{turn}.jsonl"
    try:
        p = subprocess.run(argv, input=prompt, cwd=str(cwd), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        stdout, stderr, rc, timed_out = p.stdout, p.stderr, p.returncode, False
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else e.stdout) or ""
        stderr = (e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else e.stderr) or ""
        rc, timed_out = -1, True
    secs = round(time.monotonic() - t0, 1)
    raw_path.write_text(stdout, encoding="utf-8")
    (root / f"stderr-{engine}-turn{turn}.txt").write_text(stderr, encoding="utf-8")
    lines = stdout.splitlines() + [x for x in stderr.splitlines() if "rejected" in x]
    items, meta = (parse_claude if engine == "claude" else parse_codex)(lines, turn)
    if timed_out:
        items.insert(-1, {"turn": turn, "kind": "error", "text": f"時間切れ（{timeout} 秒）"})
    meta.update({"rc": rc, "secs": secs, "timed_out": timed_out, "argv": [a for a in argv if a != prompt]})
    return {"items": items, "meta": meta}
