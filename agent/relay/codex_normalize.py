"""Codex（ChatGPT）: codex app-server の JSON-RPC → 画面用の共通イベント（設計 §1-5。Claude の relay.normalize と同じ形）。

形は 2026-10-12 に codex-cli 0.153.4（ChatGPT ログイン・gpt-5.6-sol）で取った実物（tests/fixtures/codex_*.jsonl）に
合わせた:
- ``turn/started`` → ``item/started|completed``（userMessage・agentMessage{phase: commentary|final_answer}・reasoning・
  commandExecution{command, commandActions, status, exitCode}・fileChange{changes[].path}）・``item/agentMessage/delta``
  → ``turn/completed{turn{status, error, durationMs}}``。
- 質問: ``item/tool/requestUserInput``（JSON-RPC の問い合わせ・id つき。questions[{id, header, question, options,
  isOther, isSecret}]）→ 答えると ``serverRequest/resolved``。
- ``error{willRetry}``・``account/rateLimits/updated`` は codex の schema（``codex app-server generate-json-schema``）の形。

文の選択肢（「1. … 2. …」）も質問のボタンにするが、**押した答えはモデルへの文として送るだけ**。反映の承認には決して
ならない（承認は中継の承認カードと承認番号だけ・設計 §3）。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Callable

from relay import codex_config as cx
from relay import events as ev
from relay import models
from relay.normalize import JST
from relay.step_labels import GENERIC, label_for

#: この割合（％）を超えたら「このアカウントの利用上限に近づいています」を 1 回だけ出す（人ごとの席＝その人の契約の分）
NEAR_LIMIT_PCT = 80
NEAR_LIMIT_JA = "いまはこのアカウントの利用上限に近づいています（あなたの ChatGPT の契約の分です）"

_CHOICE_LINE = re.compile(r"^\s*(?:[(（]?[1-4１-４][).）．.、:：]|[①②③④])\s*(.+?)\s*$")
_ASKING = re.compile(r"[？?]|ください|しますか|どれ|どちら")
_LABEL_DESC = re.compile(r"^(.{1,40}?)\s*(?:[（(]\s*(.+?)\s*[）)]|[—－:：]\s*(.+))$")


def text_choices(text: str | None) -> dict | None:
    """文の終わりの「1. … 2. …」（2〜4 個・直前に問いかけの行）を質問の形に。拾えなければ None。"""
    lines = (text or "").replace("\r", "").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    items: list[str] = []
    i = len(lines) - 1
    while i >= 0:
        m = _CHOICE_LINE.match(lines[i])
        if not m:
            break
        items.insert(0, m.group(1))
        i -= 1
    if not 2 <= len(items) <= 4:
        return None
    head = next((lines[j].strip() for j in range(i, -1, -1) if lines[j].strip()), "")
    if not head or not _ASKING.search(head):
        return None
    opts = []
    for it in items:
        it = ev.option_label(it.replace("**", "").strip())
        rec = it.endswith(ev.RECOMMENDED_JA)
        if rec:
            it = it[: -len(ev.RECOMMENDED_JA)].rstrip()
        m = _LABEL_DESC.match(it)
        label, desc = (m.group(1), m.group(2) or m.group(3) or "") if m else (it, "")
        label = label.strip()[: 40 - len(ev.RECOMMENDED_JA) if rec else 40] + (ev.RECOMMENDED_JA if rec else "")
        opts.append({"label": label, "description": desc.strip()[:120]})
    return {"question": head[-200:], "header": "", "options": opts, "multiSelect": False}


def command_text(item: dict) -> str:
    """表示・見張り用のコマンド文（commandActions の command を優先・シェルの包みは外す）。"""
    acts = [a.get("command") for a in item.get("commandActions") or [] if isinstance(a, dict) and a.get("command")]
    if acts:
        return " ; ".join(str(a) for a in acts)
    cmd = item.get("command")
    if isinstance(cmd, list):
        cmd = " ".join(str(x) for x in cmd)
    return cx.unwrap_shell(str(cmd or ""))


class CodexNormalizer:
    """codex app-server のサーバー → 中継の 1 行（JSON-RPC）を共通イベントに。

    画面に出さない内部用のイベント（``_`` で始まる）:
    - ``_cmd``: {phase: start|end, step_id, command | ok・exit_code} … 見張り（relay.watch）と監査用
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.turn_id = 0
        self.cli_session_id: str | None = None
        self.model: str | None = None
        self.codex_turn: str | None = None
        self.in_turn = False
        self.tool_steps = 0                    # このターンで動いた作業の数（モデルの下げ直しの判断に使う）
        self._turn_t0 = 0.0
        self._steps: dict[str, dict] = {}
        self._turn_steps: list[dict] = []
        self._thinking: set[str] = set()
        self._last_final = ""
        self._asked = False
        self._pending_error: dict | None = None
        self.rate_limits: dict | None = None
        self._limit_hinted = False
        self.file_changes: dict[str, list[str]] = {}
        #: question_id → {"kind": "native", "rpc_id", "ids": {質問文: id}, "secret": [...]} / {"kind": "text", ...}
        self.questions: dict[str, dict] = {}

    # ---------- 入口 ----------
    def feed(self, msg: dict) -> list[dict]:
        meth = msg.get("method")
        p = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if meth == "turn/started":
            return self._turn_started(p)
        if meth == "item/started":
            return self._item_started(p.get("item") or {})
        if meth == "item/completed":
            return self._item_completed(p.get("item") or {})
        if meth == "item/agentMessage/delta":
            d = p.get("delta")
            return [{"type": "message.delta", "text": d}] if isinstance(d, str) and d else []
        if meth == "item/reasoning/summaryTextDelta":
            iid = str(p.get("itemId"))
            if iid not in self._thinking:
                self._thinking.add(iid)
                return [{"type": "thinking", "phase": "start"}]
            return []
        if meth == "item/tool/requestUserInput" and "id" in msg:
            return self._question(msg["id"], p)
        if meth == "serverRequest/resolved":
            return []  # 質問に答えたときの「調べています」は係（answer）が出す
        if meth == "error":
            return self._error_note(p)
        if meth == "account/rateLimits/updated":
            if isinstance(p.get("rateLimits"), dict):
                self.rate_limits = {**(self.rate_limits or {}), **{k: v for k, v in p["rateLimits"].items()
                                                                    if v is not None}}
            return self.usage_hint() if self.used_percent() >= NEAR_LIMIT_PCT else []
        if meth == "turn/completed":
            return self._turn_completed(p.get("turn") or {})
        return []  # thread/started・tokenUsage・mcpServer/*・warning・deprecationNotice などは画面に出さない

    # ---------- 各種 ----------
    def _turn_started(self, p: dict) -> list[dict]:
        self.codex_turn = str((p.get("turn") or {}).get("id") or "") or None
        if self.in_turn:
            return []
        self.turn_id += 1
        self.in_turn = True
        self.tool_steps = 0
        self._turn_t0 = self.clock()
        self._turn_steps = []
        self._last_final = ""
        self._asked = False
        self._pending_error = None
        return [ev.status("working")]

    def _step_start(self, iid: str, label: str, command: str) -> list[dict]:
        step = {"step_id": "s_" + re.sub(r"[^A-Za-z0-9]", "", iid)[-12:], "label": label, "t0": self.clock()}
        self._steps[iid] = step
        self.tool_steps += 1
        return [{"type": "step.start", "step_id": step["step_id"], "label": label},
                {"type": "_cmd", "phase": "start", "step_id": step["step_id"], "command": command}]

    def _item_started(self, it: dict) -> list[dict]:
        t, iid = it.get("type"), str(it.get("id") or "")
        if t == "reasoning":
            if iid in self._thinking:
                return []
            self._thinking.add(iid)
            return [{"type": "thinking", "phase": "start"}]
        if t == "commandExecution":
            cmd = command_text(it)
            return self._step_start(iid, label_for("Bash", {"command": cmd}), cmd)
        if t == "fileChange":
            paths = [str(c.get("path")) for c in it.get("changes") or [] if isinstance(c, dict) and c.get("path")]
            self.file_changes[iid] = paths
            return self._step_start(iid, "メモを書いています…", "apply_patch " + " ".join(paths))
        if t in ("mcpToolCall", "dynamicToolCall", "webSearch", "collabAgentToolCall", "imageView", "imageGeneration"):
            return self._step_start(iid, GENERIC, str(t))
        return []

    def _item_completed(self, it: dict) -> list[dict]:
        t, iid = it.get("type"), str(it.get("id") or "")
        if t == "agentMessage":
            text = it.get("text") or ""
            if it.get("phase") in ("final_answer", None):
                self._last_final = text
            return [{"type": "message", "text": text}] if text.strip() else []
        if t == "reasoning":
            if iid in self._thinking:
                self._thinking.discard(iid)
                return [{"type": "thinking", "phase": "end"}]
            return [{"type": "thinking", "phase": "start"}, {"type": "thinking", "phase": "end"}]
        step = self._steps.pop(iid, None)
        if step is None:
            return []
        code = it.get("exitCode") if t == "commandExecution" else None
        ok = it.get("status") in ("completed", None) and code in (0, None)
        secs = round(max(0.0, self.clock() - step["t0"]), 1)
        self._turn_steps.append({"ok": ok, "secs": secs})
        return [{"type": "step.end", "step_id": step["step_id"], "label": step["label"], "ok": ok, "secs": secs},
                {"type": "_cmd", "phase": "end", "step_id": step["step_id"], "ok": ok, "exit_code": code}]

    def _question(self, rpc_id: Any, p: dict) -> list[dict]:
        qid = "cq_" + str(rpc_id)
        shown, ids, labels, secret = [], {}, {}, []
        for q in p.get("questions") or []:
            if not isinstance(q, dict):
                continue
            if q.get("isSecret"):
                secret.append(str(q.get("id")))  # 秘密の問いは画面に出さない（空で答える）
                continue
            text = str(q.get("question", ""))
            ids[text] = str(q.get("id"))
            opts = [o for o in q.get("options") or [] if isinstance(o, dict)]
            labels[text] = [str(o.get("label", "")) for o in opts]
            shown.append({"question": text, "header": str(q.get("header", "")),
                          "options": [{"label": ev.option_label(str(o.get("label", ""))),
                                       "description": str(o.get("description", ""))} for o in opts],
                          "multiSelect": False})
        self.questions[qid] = {"kind": "native", "rpc_id": str(rpc_id), "raw_id": rpc_id, "ids": ids, "labels": labels,
                               "secret": secret}
        self._asked = True
        if not shown:
            return []
        return [{"type": "question", "question_id": qid, "questions": shown}, ev.status("waiting_answer")]

    def _error_note(self, p: dict) -> list[dict]:
        if p.get("willRetry"):
            return [ev.status("working", "混み合っています・もう一度試しています")]
        self._pending_error = p.get("error") if isinstance(p.get("error"), dict) else {}
        return []

    def _turn_completed(self, turn: dict) -> list[dict]:
        out: list[dict] = []
        for step in self._steps.values():  # 結果の来なかった作業は ⚠ で閉じる
            out.append({"type": "step.end", "step_id": step["step_id"], "label": step["label"], "ok": False,
                        "secs": round(max(0.0, self.clock() - step["t0"]), 1)})
            self._turn_steps.append({"ok": False, "secs": 0.0})
        self._steps.clear()
        out += [{"type": "thinking", "phase": "end"} for _ in self._thinking]
        self._thinking.clear()
        if self._turn_steps:
            n = len(self._turn_steps)
            bad = sum(1 for s in self._turn_steps if not s["ok"])
            total = round(sum(s["secs"] for s in self._turn_steps), 1)
            label = (f"✓ 調べました（{n} 件・{total:g} 秒）" if not bad
                     else f"⚠ 調べました（{n} 件のうち {bad} 件うまくいきませんでした）")
            out.append({"type": "steps.summary", "count": n, "failed": bad, "secs": total, "label": label})
        status = str(turn.get("status") or "completed")
        secs = round((turn.get("durationMs") or 0) / 1000, 1) or round(max(0.0, self.clock() - self._turn_t0), 1)
        out.append({"type": "turn.end", "secs": secs, "ok": status == "completed", "reason": status})
        if status == "interrupted":
            out.append(ev.status("idle", "止めました"))
        elif status == "failed":
            err = turn.get("error") if isinstance(turn.get("error"), dict) else (self._pending_error or {})
            out += self.failure(err)
        else:
            choice = None if self._asked else text_choices(self._last_final)
            if choice:
                qid = f"txt_{self.turn_id}"
                self.questions[qid] = {"kind": "text", "question": choice["question"]}
                out += [{"type": "question", "question_id": qid, "questions": [choice], "source": "text"},
                        ev.status("waiting_answer")]
            else:
                out.append(ev.status("idle"))
        self.in_turn = False
        self.codex_turn = None
        self._turn_steps = []
        return out

    # ---------- 失敗の言い方 ----------
    @staticmethod
    def error_kind(err: dict | None) -> str:
        info = (err or {}).get("codexErrorInfo")
        if isinstance(info, dict):
            return next(iter(info), "other")
        return str(info or "other")

    def failure(self, err: dict) -> list[dict]:
        kind = self.error_kind(err)
        text = str(err.get("message") or "")
        if kind == "unauthorized" or any(w in text.lower() for w in ("401", "unauthorized", "not logged in")):
            return [ev.error("login", "ChatGPT へのサインインが切れています。⚙ の「AI のつなぎ先」から、サインインし直してください",
                             retry=False),
                    ev.status("error")]
        if kind in ("usageLimitExceeded", "rateLimitExceeded"):
            return [*self.usage_hint(), self.rate_limited()]
        if models.codex_unsupported(text):
            return [ev.error("model", "この ChatGPT の契約では使えない AI でした（らく助の係の方へご連絡ください）",
                             retry=False), ev.status("error")]
        if kind == "contextWindowExceeded":
            return [ev.error("context", "相談が長くなりすぎました。新しい相談で続けてください", retry=False),
                    ev.status("error")]
        if kind in ("serverOverloaded", "internalServerError") or "Connection" in kind or "Stream" in kind:
            return [ev.error("busy", "AI の側が混み合っています。少し時間をおいてもう一度お試しください", retry=True),
                    ev.status("error")]
        return [ev.error("cli", "うまくいきませんでした。もう一度お試しください", retry=True), ev.status("error")]

    def used_percent(self) -> float:
        """いちばん使っている窓（primary・secondary）の使った割合（分からなければ 0）。"""
        snap = self.rate_limits or {}
        pct = [w.get("usedPercent") for w in (snap.get("primary"), snap.get("secondary")) if isinstance(w, dict)]
        nums = [float(x) for x in pct if isinstance(x, (int, float)) and not isinstance(x, bool)]
        return max(nums, default=0.0)

    def usage_hint(self) -> list[dict]:
        """「このアカウントの利用上限に近づいています」（会話につき 1 回・会話は止めない＝level warn）。"""
        if self._limit_hinted:
            return []
        self._limit_hinted = True
        return [{**ev.error("usage_limit", NEAR_LIMIT_JA, retry=False), "level": "warn"}]

    def rate_limited(self) -> dict:
        snap = self.rate_limits or {}
        wins = [w for w in (snap.get("primary"), snap.get("secondary")) if isinstance(w, dict) and w.get("resetsAt")]
        full = [w for w in wins if (w.get("usedPercent") or 0) >= 100]
        pick = full[0] if full else (max(wins, key=lambda w: w["resetsAt"]) if wins else None)
        try:
            when = datetime.fromtimestamp(float(pick["resetsAt"]), JST)  # type: ignore[index]
            return ev.status("rate_limited", f"{when.strftime('%H:%M')} 以降に続けられます", resets_at=when.isoformat())
        except (TypeError, ValueError, KeyError):
            return ev.status("rate_limited", "しばらくしてから続けられます")
