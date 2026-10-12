"""Claude の stream-json（1 行 1 JSON）→ 画面用の共通イベント（設計 §1-5）。

形は 2026-10-12 に Claude Code 2.1.296 で取った実物（tests/fixtures/claude_*.jsonl）に合わせた:
- ``system/init`` は**毎ターンの最初**に来る（cwd・session_id・model・apiKeySource）。
- ``stream_event``（--include-partial-messages の時）: content_block_start/delta/stop・message_start/stop。
- ``assistant``: 確定した content（text・thinking・tool_use）。``user``: tool_result。
- ``control_request{subtype:"can_use_tool"}``（--permission-prompt-tool stdio の時）。AskUserQuestion は
  ``requires_user_interaction: true``・``input.questions``。
- ``result``（type が後ろの方にある行もある）: duration_ms・is_error・subtype・terminal_reason。
- ``rate_limit_event``・``system/api_retry`` は 3-0 で実物を取る（ここは AGENT_PLAN §3.3 の形で書いた）。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from relay import events as ev
from relay import models
from relay.step_labels import label_for

JST = timezone(timedelta(hours=9))


class ClaudeNormalizer:
    def __init__(self, *, allowed_key_sources: set[str] | None = None, check_model: bool = True,
                 clock: Callable[[], float] = time.monotonic, emit_commands: bool = False):
        #: True なら Bash/PowerShell のコマンドを内部用の ``_cmd`` でも出す（中継の見張り relay.watch 用）
        self.emit_commands = emit_commands
        self.allowed_key_sources = allowed_key_sources if allowed_key_sources is not None else {"none"}
        self.check_model = check_model
        self.clock = clock
        self.turn_id = 0
        self.cli_session_id: str | None = None
        self.model: str | None = None
        self._in_turn = False
        self._steps: dict[str, dict] = {}       # tool_use_id → {step_id, label, t0}
        self._turn_steps: list[dict] = []       # このターンの終わった作業
        self._turn_t0 = 0.0
        self._thinking_index: int | None = None
        self._partial_msgs: set[str] = set()    # partial で text/thinking を流した message id
        self._ask_tool_ids: set[str] = set()

    # ---------- 入口 ----------
    def feed(self, msg: dict) -> list[dict]:
        t = msg.get("type")
        if t == "system":
            return self._system(msg)
        if msg.get("parent_tool_use_id"):
            return []  # サブエージェントの中身は出さない（親の作業行にまとまる）
        if t == "stream_event":
            return self._stream(msg)
        if t == "assistant":
            return self._assistant(msg)
        if t == "user":
            return self._user(msg)
        if t == "control_request":
            return self._control(msg)
        if t == "result":
            return self._result(msg)
        if t == "rate_limit_event":
            return self._rate_limit(msg)
        return []

    # ---------- 各種 ----------
    def _system(self, msg: dict) -> list[dict]:
        st = msg.get("subtype")
        if st == "init":
            out: list[dict] = []
            self.turn_id += 1
            self.cli_session_id = msg.get("session_id") or self.cli_session_id
            self.model = msg.get("model") or self.model
            out.append({"type": "_meta", "cli_session_id": self.cli_session_id, "model": self.model,
                        "model_display": models.display_name(self.model),
                        "api_key_source": msg.get("apiKeySource")})
            src = msg.get("apiKeySource")
            # サブスクのログイン（apiKeySource: "none"）だけを通す。無い・API キー・ヘルパー経由は止める
            if src not in self.allowed_key_sources:
                out.append(ev.error("auth_source", "思っていない方法で AI につながろうとしたので止めました"
                                                   "（らく助の係の方へご連絡ください）", retry=False))
                out.append({"type": "_abort", "reason": "auth_source"})
                return out
            if self.check_model:
                why = models.claude_init_problem(self.model)
                if why:
                    out.append(ev.error("model", why, retry=False))
                    out.append({"type": "_abort", "reason": "model"})
                    return out
            if not self._in_turn:
                self._in_turn = True
                self._turn_t0 = self.clock()
                self._turn_steps = []
                out.append(ev.status("working"))
            return out
        if st == "api_retry":
            n = msg.get("attempt") or msg.get("retry_attempt") or ""
            return [ev.status("working", f"混み合っています・もう一度試しています（{n} 回目）" if n else
                              "混み合っています・もう一度試しています")]
        return []  # status・hook_*・compact_boundary などは画面に出さない

    def _stream(self, msg: dict) -> list[dict]:
        e = msg.get("event") or {}
        et = e.get("type")
        mid = msg.get("api_message_id") or ""
        if et == "content_block_start":
            cb = e.get("content_block") or {}
            if cb.get("type") in ("thinking", "redacted_thinking"):
                self._thinking_index = e.get("index")
                self._partial_msgs.add(mid + ":thinking")
                return [{"type": "thinking", "phase": "start"}]
            return []
        if et == "content_block_delta":
            d = e.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                self._partial_msgs.add(mid + ":text")
                return [{"type": "message.delta", "text": d["text"]}]
            return []
        if et == "content_block_stop" and self._thinking_index is not None and e.get("index") == self._thinking_index:
            self._thinking_index = None
            return [{"type": "thinking", "phase": "end"}]
        return []

    def _assistant(self, msg: dict) -> list[dict]:
        m = msg.get("message") or {}
        mid = m.get("id") or ""
        out: list[dict] = []
        for c in m.get("content") or []:
            ct = c.get("type")
            if ct in ("thinking", "redacted_thinking"):
                if mid + ":thinking" not in self._partial_msgs:
                    out += [{"type": "thinking", "phase": "start"}, {"type": "thinking", "phase": "end"}]
            elif ct == "text" and (c.get("text") or "").strip():
                out.append({"type": "message", "text": c["text"]})
            elif ct == "tool_use":
                tid = c.get("id") or ""
                if c.get("name") == "AskUserQuestion":
                    self._ask_tool_ids.add(tid)
                    continue
                step = {"step_id": "s_" + tid[-12:], "label": label_for(c.get("name") or "", c.get("input")),
                        "t0": self.clock()}
                self._steps[tid] = step
                out.append({"type": "step.start", "step_id": step["step_id"], "label": step["label"]})
                if self.emit_commands and c.get("name") in ("Bash", "PowerShell"):
                    step["cmd"] = True
                    out.append({"type": "_cmd", "phase": "start", "step_id": step["step_id"],
                                "command": str((c.get("input") or {}).get("command") or "")})
        return out

    def _user(self, msg: dict) -> list[dict]:
        content = (msg.get("message") or {}).get("content")
        if not isinstance(content, list):
            return []
        out = []
        for c in content:
            if not isinstance(c, dict) or c.get("type") != "tool_result":
                continue
            tid = c.get("tool_use_id") or ""
            if tid in self._ask_tool_ids:
                self._ask_tool_ids.discard(tid)
                out.append(ev.status("working"))
                continue
            step = self._steps.pop(tid, None)
            if step is None:
                continue
            ok = not c.get("is_error")
            secs = round(max(0.0, self.clock() - step["t0"]), 1)
            self._turn_steps.append({"ok": ok, "secs": secs})
            out.append({"type": "step.end", "step_id": step["step_id"], "label": step["label"], "ok": ok,
                        "secs": secs})
            if step.get("cmd"):
                out.append({"type": "_cmd", "phase": "end", "step_id": step["step_id"], "ok": ok})
        return out

    def _control(self, msg: dict) -> list[dict]:
        req = msg.get("request") or {}
        if req.get("subtype") == "can_use_tool" and req.get("tool_name") == "AskUserQuestion":
            qs = (req.get("input") or {}).get("questions") or []
            questions = [{
                "question": str(q.get("question", "")),
                "header": str(q.get("header", "")),
                "options": [{"label": ev.option_label(str(o.get("label", ""))),
                             "description": str(o.get("description", ""))}
                            for o in q.get("options") or []],
                "multiSelect": bool(q.get("multiSelect")),
            } for q in qs]
            return [{"type": "question", "question_id": str(msg.get("request_id")), "questions": questions},
                    ev.status("waiting_answer")]
        return []

    def _result(self, msg: dict) -> list[dict]:
        out: list[dict] = []
        for tid, step in list(self._steps.items()):  # 結果の来なかった作業は ⚠ で閉じる
            out.append({"type": "step.end", "step_id": step["step_id"], "label": step["label"], "ok": False,
                        "secs": round(max(0.0, self.clock() - step["t0"]), 1)})
            self._turn_steps.append({"ok": False, "secs": 0.0})
        self._steps.clear()
        if self._thinking_index is not None:
            self._thinking_index = None
            out.append({"type": "thinking", "phase": "end"})
        secs = round((msg.get("duration_ms") or 0) / 1000, 1)
        if self._turn_steps:
            n = len(self._turn_steps)
            bad = sum(1 for s in self._turn_steps if not s["ok"])
            total = round(sum(s["secs"] for s in self._turn_steps), 1)
            label = (f"✓ 調べました（{n} 件・{total:g} 秒）" if not bad
                     else f"⚠ 調べました（{n} 件のうち {bad} 件うまくいきませんでした）")
            out.append({"type": "steps.summary", "count": n, "failed": bad, "secs": total, "label": label})
        reason = str(msg.get("terminal_reason") or msg.get("subtype") or "")
        is_error = bool(msg.get("is_error")) or str(msg.get("subtype", "")).startswith("error")
        aborted = reason.startswith("aborted")
        out.append({"type": "turn.end", "secs": secs, "ok": not is_error and not aborted, "reason": reason})
        if aborted:
            out.append(ev.status("idle", "止めました"))
        elif is_error:
            out.append(self._error_of(msg))
            out.append(ev.status("error"))
        else:
            out.append(ev.status("idle"))
        self._in_turn = False
        self._turn_steps = []
        return out

    @staticmethod
    def _error_of(msg: dict) -> dict:
        text = " ".join(str(x) for x in (msg.get("result"), msg.get("errors")) if x).lower()
        st = str(msg.get("subtype", ""))
        if any(w in text for w in ("authentication", "login", "401", "oauth", "credentials")):
            return ev.error("login", "AI へのログインが切れています（らく助の係の方へご連絡ください）", retry=False)
        if st == "error_max_turns":
            return ev.error("max_turns", "作業が長くなりすぎたので止めました。分けて頼んでください", retry=True)
        return ev.error("cli", "うまくいきませんでした。もう一度お試しください", retry=True)

    def _rate_limit(self, msg: dict) -> list[dict]:
        info = msg.get("rate_limit_info") or msg
        if str(info.get("status", "")).startswith("allowed") and info.get("status") != "allowed_warning":
            return []
        if info.get("status") == "allowed_warning":
            return []
        resets = info.get("resetsAt") or info.get("resets_at")
        try:
            at = datetime.fromtimestamp(float(resets), JST)
            hhmm = at.strftime("%H:%M")
            return [ev.status("rate_limited", f"{hhmm} 以降に続けられます", resets_at=at.isoformat())]
        except (TypeError, ValueError):
            return [ev.status("rate_limited", "しばらくしてから続けられます")]
