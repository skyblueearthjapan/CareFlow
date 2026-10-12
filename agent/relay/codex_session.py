"""Codex（ChatGPT）の会話の係 — codex app-server（stdio の JSON-RPC）。段 3-5。

形は relay.runner の係と同じ: ``await s.start(); await s.send(text); await s.answer(qid, answers); await s.stop();
await s.close()``・``async for e in s.events()``。起こし方（プロセス／箱の runner）は ClaudeSession / RunnerSession の
ものをそのまま使い、話す中身（JSON-RPC）だけをここで持つ。

- start: initialize → initialized → account/read（ChatGPT のログインだけ。無い・API キーは StartRefused）→
  model/list（SolPicker で最新の Sol）→ config/read・experimentalFeature/list（締め付けの確かめ・
  relay.codex_config.lockdown_problems。効いていなければ StartRefused）→ thread/start | thread/resume
- 送る: ターンが無ければ turn/start、作業中なら turn/steer（間に合わなければ次のターン）
- 質問（item/tool/requestUserInput）: 画面の質問カード → answer() で JSON-RPC の答えを返す。
  文の選択肢（「1. … 2. …」）のカードは、選んだ言葉をモデルへの文として送るだけ（反映の承認には決してならない）
- 承認の問い合わせ（来たときだけ。sandbox は danger-full-access なのでふだんは来ない）: 中継が
  relay.codex_config の物差しで答える。人の許可カードは出さない（D13）。答えは ``_audit`` に残す
- 「このアカウントでは使えないモデル」: まだ何もしていないターンなら一つ下の Sol でやり直す（SolPicker）
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from relay import codex_config as cx
from relay import events as ev
from relay import models
from relay.codex_normalize import CodexNormalizer
from relay.runner import FORBIDDEN_ENV, RELAY_PROMPT, ClaudeSession, RunnerSession, claude_env


class StartRefused(RuntimeError):
    """始める前の確かめで断った（ログインなし・API キー・締め付けが効いていない）。``event`` を画面へ出す。"""

    def __init__(self, event: dict, detail: str = ""):
        super().__init__(str(event.get("message", "start refused")))
        self.event = event
        self.detail = detail


class CodexRpcError(RuntimeError):
    def __init__(self, method: str, error: dict | None):
        self.method, self.error = method, (error or {})
        super().__init__(f"{method}: {str(self.error.get('message', ''))[:200]}")


LOGIN_MISSING = ev.error("login", "ChatGPT にサインインすると、相談を始められます（⚙ の「AI のつなぎ先」から）", retry=False)
NOT_CHATGPT = ev.error("auth_source", "ChatGPT のログイン以外の方法で AI につながろうとしたので止めました"
                                      "（らく助の係の方へご連絡ください）", retry=False)
LOCKDOWN_OFF = ev.error("lockdown", "安全の設定が効いていないので始めませんでした（らく助の係の方へご連絡ください）",
                        retry=False)


def codex_env(*, home: str, cwd: str, relay_url: str, rk_token: str, work_dir: str, https_proxy: str,
              no_proxy: str = "carelink-agent", path: str = "/usr/local/bin:/usr/bin:/bin") -> dict[str, str]:
    """Codex の環境（許可リストで作り直す）。CODEX_HOME は箱の volume（ログイン情報）。rk の包み（bin/rk）を PATH に。"""
    env = claude_env(home=home, cwd=cwd, relay_url=relay_url, rk_token=rk_token, work_dir=work_dir,
                     https_proxy=https_proxy, no_proxy=no_proxy, path=f"{cwd}/bin:{path}")
    env.pop("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", None)
    env["CODEX_HOME"] = f"{home}/.codex"
    env["TMPDIR"] = f"{work_dir}/scratch"
    assert not set(env) & set(FORBIDDEN_ENV)
    return env


class _CodexProtocol:
    """codex app-server と JSON-RPC で話す部分（ClaudeSession / RunnerSession の上に重ねる）。"""

    norm: CodexNormalizer
    _questions: dict
    _busy: bool
    _queue: asyncio.Queue
    exit_code: int | None
    stop_wait: float

    def _codex_setup(self, *, cwd: str, work_dir: str, resume_id: str | None, developer_instructions: str,
                     rpc_timeout: float) -> None:
        self.thread_cwd = cwd
        self.work_dir = work_dir
        self.resume_id = resume_id
        self.developer_instructions = developer_instructions
        self.rpc_timeout = rpc_timeout
        self.picker: models.SolPicker | None = None
        self.thread_id: str | None = None
        self.plan_type: str | None = None
        self.lockdown: list[str] = []
        self.audit: list[dict] = []
        self._rpc_seq = 0
        self._pending: dict[str, tuple[str, asyncio.Future | None]] = {}
        self._last_input: list[dict] | None = None
        self._last_steer: list[dict] | None = None
        self._next_inputs: list[dict] = []

    # ---------- JSON-RPC ----------
    async def _rpc_write(self, msg: dict) -> None:
        await self._write(msg)  # type: ignore[attr-defined]

    async def _notify(self, method: str, params: dict | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._rpc_write(msg)

    async def _send_call(self, method: str, params: dict | None, want: bool) -> asyncio.Future | None:
        self._rpc_seq += 1
        rid = self._rpc_seq
        fut = asyncio.get_running_loop().create_future() if want else None
        self._pending[str(rid)] = (method, fut)
        msg: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        await self._rpc_write(msg)
        return fut

    async def _call(self, method: str, params: dict | None = None) -> dict:
        fut = await self._send_call(method, params, True)
        assert fut is not None
        loop = asyncio.get_running_loop()
        end = loop.time() + self.rpc_timeout
        while not fut.done():
            if self.exit_code is not None:
                raise CodexRpcError(method, {"message": "codex exited"})
            if loop.time() > end:
                raise CodexRpcError(method, {"message": "timeout"})
            await asyncio.wait({fut}, timeout=0.2)
        return fut.result()

    async def _reply(self, rid: Any, result: dict | None = None, error: dict | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        await self._rpc_write(msg)

    async def _paged(self, method: str, params: dict, pages: int = 5) -> list[dict]:
        out: list[dict] = []
        cursor = None
        for _ in range(pages):
            r = await self._call(method, {**params, **({"cursor": cursor} if cursor else {})})
            data = r.get("data")
            if not isinstance(data, list):
                raise CodexRpcError(method, {"message": "no data"})
            out += [d for d in data if isinstance(d, dict)]
            cursor = r.get("nextCursor")
            if not cursor:
                return out
        return out

    # ---------- 始める ----------
    async def start(self) -> None:
        await super().start()  # type: ignore[misc]
        try:
            await self._handshake()
        except StartRefused:
            await self._kill()  # type: ignore[attr-defined]
            raise
        except CodexRpcError as e:
            await self._kill()  # type: ignore[attr-defined]
            raise RuntimeError(f"codex app-server: {e}") from None

    async def _handshake(self) -> None:
        await self._call("initialize", {"clientInfo": {"name": "rakusuke-relay", "title": "rakusuke", "version": "3.5"},
                                        "capabilities": {"experimentalApi": True}})
        await self._notify("initialized")
        acct = await self._call("account/read", {"refreshToken": False})
        a = acct.get("account")
        if not isinstance(a, dict):
            raise StartRefused(LOGIN_MISSING, "no account")
        if a.get("type") != "chatgpt":  # API キー・Bedrock などは使わない（サブスクのログインだけ）
            raise StartRefused(NOT_CHATGPT, f"account type {a.get('type')}")
        self.plan_type = str(a.get("planType") or "") or None
        try:
            listed = await self._paged("model/list", {"limit": 100})
            ids = [str(d.get("model") or d.get("id")) for d in listed if d.get("model") or d.get("id")]
        except CodexRpcError:
            ids = []
        self.picker = models.SolPicker(ids)
        try:
            cfg = (await self._call("config/read", {"includeLayers": False, "cwd": self.thread_cwd})).get("config")
        except CodexRpcError:
            cfg = None
        try:
            feats: list[dict] | None = await self._paged("experimentalFeature/list", {"limit": 200})
        except CodexRpcError:
            feats = None
        self.lockdown = cx.lockdown_problems(cfg if isinstance(cfg, dict) else None, feats)
        if self.lockdown:
            raise StartRefused(LOCKDOWN_OFF, "; ".join(self.lockdown))
        params = {"cwd": self.thread_cwd, "model": self.picker.current, "approvalPolicy": cx.APPROVAL_POLICY,
                  "sandbox": cx.SANDBOX_MODE, "approvalsReviewer": cx.APPROVALS_REVIEWER,
                  "developerInstructions": self.developer_instructions}
        r: dict | None = None
        if self.resume_id:
            try:
                r = await self._call("thread/resume", {**params, "threadId": self.resume_id})
            except CodexRpcError:
                r = None  # 続きが読めない（消えた・壊れた）→ 新しい会話で始める
        if r is None:
            r = await self._call("thread/start", params)
        reviewer = r.get("approvalsReviewer")
        if reviewer is not None and reviewer != cx.APPROVALS_REVIEWER:
            raise StartRefused(LOCKDOWN_OFF, f"approvalsReviewer={reviewer}")
        tid = (r.get("thread") or {}).get("id")
        if not isinstance(tid, str) or not tid:
            raise CodexRpcError("thread/start", {"message": "no thread id"})
        self.thread_id = tid
        self.norm.cli_session_id = tid
        self.norm.model = self.picker.current
        await self._queue.put(self._meta())

    def _meta(self) -> dict:
        model = self.picker.current if self.picker else None
        return {"type": "_meta", "cli_session_id": self.thread_id, "model": model,
                "model_display": models.display_name(model), "api_key_source": "none", "provider": "codex",
                "plan_type": self.plan_type, "model_fallbacks": list(self.picker.fallbacks) if self.picker else []}

    # ---------- 受け取る ----------
    async def _handle(self, msg: dict) -> None:
        meth = msg.get("method")
        if "id" in msg and meth is None:
            await self._on_response(msg)
            return
        if "id" in msg and isinstance(meth, str):
            await self._on_request(msg)
            return
        if meth == "turn/completed" and await self._maybe_retry_model(msg):
            return
        for e in self.norm.feed(msg):
            await self._queue.put(e)
        if meth == "turn/completed":
            if self._next_inputs:  # 作業中に送られて steer できなかった文は、次のターンで
                nxt, self._next_inputs = self._next_inputs, []
                await self._start_turn(nxt)
            else:
                self._busy = False

    async def _on_response(self, msg: dict) -> None:
        method, fut = self._pending.pop(str(msg.get("id")), ("", None))
        err = msg.get("error") if isinstance(msg.get("error"), dict) else None
        if fut is not None:
            if not fut.done():
                if err is not None:
                    fut.set_exception(CodexRpcError(method, err))
                else:
                    fut.set_result(msg.get("result") if isinstance(msg.get("result"), dict) else {})
            return
        if err is None:
            return
        if method == "turn/steer" and self._last_steer:
            self._next_inputs += self._last_steer  # 間に合わなかった → 次のターンで
            self._last_steer = None
            if not self.norm.in_turn:
                nxt, self._next_inputs = self._next_inputs, []
                await self._start_turn(nxt)
        elif method == "turn/start":
            text = str(err.get("message") or "")
            if self.picker and self._last_input and self.picker.on_error(text):
                await self._queue.put(self._meta())
                await self._start_turn(self._last_input)
                return
            for e in [{"type": "turn.end", "secs": 0.0, "ok": False, "reason": "failed"}, *self.norm.failure(err)]:
                await self._queue.put(e)
            self._busy = False

    async def _maybe_retry_model(self, msg: dict) -> bool:
        """「このアカウントでは使えないモデル」で、まだ何もしていないターンなら一つ下の Sol でやり直す。"""
        turn = (msg.get("params") or {}).get("turn") or {}
        if turn.get("status") != "failed" or self.norm.tool_steps or not self._last_input or self.picker is None:
            return False
        text = str((turn.get("error") or {}).get("message") or "")
        if not self.picker.on_error(text):
            return False
        await self._queue.put(self._meta())
        await self._start_turn(self._last_input)
        return True

    async def _on_request(self, msg: dict) -> None:
        rid, meth = msg["id"], msg["method"]
        p = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if meth == "item/tool/requestUserInput":
            events = self.norm.feed(msg)
            qid = "cq_" + str(rid)
            if not events:  # 見せられる問いが無い（秘密の問いだけ）→ 空で答える
                q = self.norm.questions.pop(qid, {})
                await self._reply(rid, {"answers": {i: {"answers": []} for i in q.get("secret", [])}})
                return
            self._questions[qid] = msg
            for e in events:
                await self._queue.put(e)
            return
        if meth == "item/commandExecution/requestApproval":
            decision, why = cx.command_decision(p.get("command"))
            await self._reply(rid, {"decision": decision})
            await self._audit("command", str(p.get("command") or ""), decision, why)
        elif meth == "item/fileChange/requestApproval":
            paths = self.norm.file_changes.get(str(p.get("itemId")), [])
            decision, why = cx.file_change_decision(paths, [self.work_dir])
            await self._reply(rid, {"decision": decision})
            await self._audit("file", " ".join(paths), decision, why)
        elif meth == "item/permissions/requestApproval":
            await self._reply(rid, {"permissions": {}, "scope": "turn"})
            await self._audit("permissions", str(p.get("reason") or ""), "decline", "never")
        elif meth == "mcpServer/elicitation/request":
            await self._reply(rid, {"action": "decline"})
            await self._audit("elicitation", str(p.get("serverName") or ""), "decline", "never")
        elif meth in ("applyPatchApproval", "execCommandApproval"):
            await self._reply(rid, {"decision": "denied"})
            await self._audit("legacy", meth, "decline", "legacy")
        else:  # item/tool/call・account/chatgptAuthTokens/refresh・attestation/generate・知らないもの
            await self._reply(rid, error={"code": -32601, "message": "not supported by rakusuke relay"})
            await self._audit("unsupported", meth, "decline", "unsupported")

    async def _audit(self, kind: str, target: str, decision: str, why: str) -> None:
        rec = {"type": "_audit", "kind": kind, "target": target[:300], "decision": decision, "why": why}
        self.audit.append(rec)
        await self._queue.put(rec)

    # ---------- 送る・答える・止める ----------
    async def _start_turn(self, inputs: list[dict]) -> None:
        self._last_input = inputs
        await self._send_call("turn/start", {"threadId": self.thread_id, "input": inputs,
                                             "model": self.picker.current if self.picker else models.SOL_BASELINE},
                              False)

    async def send(self, text: str) -> None:
        if self.thread_id is None or self.exit_code is not None:
            raise RuntimeError("CLI が動いていません")
        inputs = [{"type": "text", "text": text}]
        self._busy = True
        if self.norm.in_turn and self.norm.codex_turn:
            self._last_steer = inputs
            await self._send_call("turn/steer", {"threadId": self.thread_id, "input": inputs,
                                                 "expectedTurnId": self.norm.codex_turn}, False)
            return
        await self._start_turn(inputs)

    async def answer(self, question_id: str, answers: dict[str, Any] | str) -> None:
        q = self.norm.questions.get(question_id)
        if q is None:
            raise KeyError("この質問はもう答えてあるか、ありません")
        if q["kind"] == "text":
            # 文の選択肢: 選んだ言葉をモデルへの文として送るだけ（反映の承認には決してならない）
            label = answers if isinstance(answers, str) else next(iter(answers.values()), "")
            label = ", ".join(map(str, label)) if isinstance(label, list) else str(label)
            if not label.strip():
                raise ValueError("答えが空です")
            self.norm.questions.pop(question_id, None)
            await self.send(f"（選択肢から）{label.strip()}")
            return
        ids: dict[str, str] = q["ids"]
        if isinstance(answers, str):
            if len(ids) != 1:
                raise ValueError("質問が複数あるときは {質問: 答え} で渡してください")
            answers = {next(iter(ids)): answers}
        unknown = [k for k in answers if k not in ids]
        if unknown:
            raise ValueError("知らない質問への答えです: " + "、".join(map(str, unknown)))
        labels: dict[str, list[str]] = q.get("labels") or {}
        answers = {k: ev.option_original(v, labels.get(k, [])) for k, v in answers.items()}  # 「（おすすめ）」→ 元の名前
        out = {ids[k]: {"answers": [str(x) for x in (v if isinstance(v, list) else [v])]} for k, v in answers.items()}
        for sid in q.get("secret", []):
            out.setdefault(sid, {"answers": []})
        self.norm.questions.pop(question_id, None)
        self._questions.pop(question_id, None)
        await self._reply(q["raw_id"], {"answers": out})
        await self._queue.put(ev.status("working"))

    async def stop(self) -> None:
        """止める = 答え待ちの質問を閉じて turn/interrupt。10 秒で終わらなければ終わらせる（次の送信で thread/resume）。"""
        if self.exit_code is not None or self.thread_id is None:
            return
        self._next_inputs = []
        for qid in list(self._questions):
            q = self.norm.questions.pop(qid, None)
            self._questions.pop(qid, None)
            if q and q.get("kind") == "native":
                await self._reply(q["raw_id"], error={"code": -32000, "message": "The user stopped this request."})
        if self.norm.codex_turn:
            await self._send_call("turn/interrupt", {"threadId": self.thread_id, "turnId": self.norm.codex_turn},
                                  False)
        for _ in range(int(self.stop_wait * 10)):
            if not self._busy:
                return
            await asyncio.sleep(0.1)
        await self._kill()  # type: ignore[attr-defined]


class CodexSession(_CodexProtocol, ClaudeSession):
    """codex app-server を中継のプロセスから直接起こす（手元の開発・試験だけ。本番・staging は CodexRunnerSession）。"""

    def __init__(self, argv: list[str], cwd: str, env: dict[str, str], *, work_dir: str, resume_id: str | None = None,
                 developer_instructions: str = RELAY_PROMPT, normalizer: CodexNormalizer | None = None,
                 spawn: Callable[..., Awaitable[Any]] | None = None, stop_wait: float = 10.0,
                 rpc_timeout: float = 30.0):
        ClaudeSession.__init__(self, argv, cwd, env, normalizer or CodexNormalizer(),  # type: ignore[arg-type]
                               spawn=spawn, stop_wait=stop_wait)
        self._codex_setup(cwd=cwd, work_dir=work_dir, resume_id=resume_id,
                          developer_instructions=developer_instructions, rpc_timeout=rpc_timeout)


class CodexRunnerSession(_CodexProtocol, RunnerSession):
    """箱の runner（unix ソケット）に codex app-server を起こしてもらう（本番・staging）。"""

    def __init__(self, socket_path: str, spawn: dict, *, cwd: str, work_dir: str, resume_id: str | None = None,
                 developer_instructions: str = RELAY_PROMPT, normalizer: CodexNormalizer | None = None,
                 stop_wait: float = 10.0, connect: Callable[[str], Awaitable[tuple[Any, Any]]] | None = None,
                 rpc_timeout: float = 30.0):
        RunnerSession.__init__(self, socket_path, spawn, normalizer or CodexNormalizer(),  # type: ignore[arg-type]
                               stop_wait=stop_wait, connect=connect)
        self._codex_setup(cwd=cwd, work_dir=work_dir, resume_id=resume_id,
                          developer_instructions=developer_instructions, rpc_timeout=rpc_timeout)
