"""CLI の会話の係（設計 §1-3・§2-5）。

形（どの係も同じ）:
    await s.start(); await s.send(text); await s.answer(question_id, answers); await s.stop(); await s.close()
    async for e in s.events(): ...   # 画面用の共通イベント（relay.normalize）。_meta / _abort は中継の内部用

- ``ClaudeSession``: ``claude -p --input-format stream-json --output-format stream-json --permission-prompt-tool stdio …``
  を起こす。段 3 の本番では箱の中の runner が起こす（設計 §1-2 A）ので、起こし方は ``spawn`` で差し替える。
- ``RunnerSession``: 同じことを箱の runner（unix ソケット）に頼む（本番・staging。AGENT_RUNNER_SOCKET）。
- ``FakeSession``: 取った実物の stream-json を流し直す（試験用）。送信 1 回でターン 1 つ、質問は answer まで止まる。
- 道具の許可（can_use_tool）は ``control_reply`` が決める（D13: 管理者に許可カードは出さない。質問だけ画面へ）。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from relay import models
from relay.normalize import ClaudeNormalizer

#: 箱の中は鍵も道も無いので、これらは自動で通す（設計 §1-3）。
ALLOW_TOOLS = {"Bash", "PowerShell", "Read", "Glob", "Grep", "Write", "Edit", "MultiEdit", "NotebookEdit",
               "TodoWrite", "Skill", "Task", "Agent", "ToolSearch", "BashOutput", "KillShell"}
#: 外へ出る道具はモデルに無駄をさせないよう断る（出口のプロキシでも止まる）。
DENY_MESSAGE = {
    "WebFetch": "この相談では Web を見られません。",
    "WebSearch": "この相談では Web を検索できません。",
}

#: 画面の約束（承認は画面のボタン・rk plan submit 等）。正は relay-prompt.md（箱の runner も同じファイルを
#: /srv/agent/relay/relay-prompt.md として --append-system-prompt-file に渡す）。
RELAY_PROMPT_FILE = Path(__file__).with_name("relay-prompt.md")
RELAY_PROMPT = RELAY_PROMPT_FILE.read_text(encoding="utf-8")


# ---------- 起こし方の材料 ----------
def isolation_settings(home: str, cwd: str) -> dict:
    """--settings に渡す isolation.json（設計 §1-3・§2-5）。自動メモリは切る。CLAUDE.md は cwd のものだけ効かせる。"""
    excludes = [f"{home}/.claude/CLAUDE.md", f"{home}/.claude/rules/**"]
    p = PurePosixPath(cwd)
    for anc in list(p.parents):
        base = str(anc).rstrip("/")
        excludes += [f"{base}/CLAUDE.md", f"{base}/CLAUDE.local.md", f"{base}/.claude/CLAUDE.md"]
    return {
        "forceLoginMethod": "claudeai",
        "apiKeyHelper": "",
        "env": {k: "" for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                                "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                                "CLAUDE_CODE_USE_FOUNDRY")},
        "autoMemoryEnabled": False,
        "claudeMdExcludes": list(dict.fromkeys(excludes)),
    }


def write_session_files(session_dir: Path, home: str, cwd: str) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "work").mkdir(exist_ok=True)
    files = {
        "isolation": session_dir / "isolation.json",
        "mcp": session_dir / "empty-mcp.json",
        "prompt": session_dir / "relay-prompt.md",
    }
    files["isolation"].write_text(json.dumps(isolation_settings(home, cwd), ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    files["mcp"].write_text('{"mcpServers": {}}', encoding="utf-8")
    files["prompt"].write_text(RELAY_PROMPT, encoding="utf-8")
    return files


def claude_argv(claude_bin: str, files: dict[str, Path], resume_id: str | None = None,
                max_turns: int = 80) -> list[str]:
    argv = [
        claude_bin, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--include-partial-messages",
        "--permission-prompt-tool", "stdio", "--permission-mode", "default",
        "--setting-sources", "project",
        "--strict-mcp-config", "--mcp-config", str(files["mcp"]),
        "--settings", str(files["isolation"]),
        "--append-system-prompt-file", str(files["prompt"]),
        "--max-turns", str(max_turns),
        "--model", models.claude_model_arg(),  # --resume の時も毎回（付けないと既定に戻る）
    ]
    if resume_id:
        argv += ["--resume", resume_id]
    return argv


#: CLI に決して渡さない環境変数（サブスクのログインだけで動かす・偽のつなぎ先を防ぐ）。箱の runner と同じ一覧。
FORBIDDEN_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "CODEX_API_KEY",
)


def claude_env(*, home: str, cwd: str, relay_url: str, rk_token: str, work_dir: str, https_proxy: str,
               no_proxy: str = "carelink-agent", path: str = "/usr/local/bin:/usr/bin:/bin") -> dict[str, str]:
    """CLI の環境は許可リストで作り直す（親の環境を継がない・設計 §2-5）。"""
    env = {
        "PATH": path, "HOME": home, "LANG": "C.UTF-8", "TZ": "Asia/Tokyo",
        "HTTPS_PROXY": https_proxy, "NO_PROXY": no_proxy,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1",
        "PYTHONPATH": cwd, "PYTHONIOENCODING": "utf-8",
        "RK_MODE": "relay", "RK_RELAY_URL": relay_url, "RK_RELAY_TOKEN": rk_token, "RK_WORK_DIR": work_dir,
    }
    assert not set(env) & set(FORBIDDEN_ENV)
    return env


# ---------- control_request への返事 ----------
def control_reply(msg: dict) -> dict | None:
    """受けた control_request への control_response。AskUserQuestion は None（画面の答えを待つ）。"""
    rid = msg.get("request_id")
    req = msg.get("request") or {}
    st = req.get("subtype")
    if st == "can_use_tool":
        name = req.get("tool_name") or ""
        if name == "AskUserQuestion":
            return None
        if name in ALLOW_TOOLS:
            resp = {"behavior": "allow", "updatedInput": req.get("input") or {}}
        else:
            resp = {"behavior": "deny", "message": DENY_MESSAGE.get(name, f"この相談では {name} は使えません。")}
        return {"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": resp}}
    if st == "elicitation":
        return {"type": "control_response",
                "response": {"subtype": "success", "request_id": rid, "response": {"action": "decline"}}}
    return {"type": "control_response",
            "response": {"subtype": "error", "request_id": rid, "error": f"unsupported request: {st}"}}


def answer_reply(request: dict, answers: dict[str, Any] | str) -> dict:
    """AskUserQuestion の control_request に、画面の答えで返す（AGENT_PLAN §3.3 の形）。"""
    req = request.get("request") or {}
    qs = (req.get("input") or {}).get("questions") or []
    if isinstance(answers, str):
        if len(qs) != 1:
            raise ValueError("質問が複数あるときは {質問: 答え} で渡してください")
        answers = {qs[0]["question"]: answers}
    norm = {str(k): (", ".join(v) if isinstance(v, list) else str(v)) for k, v in answers.items()}
    unknown = [k for k in norm if k not in {q.get("question") for q in qs}]
    if unknown:
        raise ValueError("知らない質問への答えです: " + "、".join(unknown))
    return {"type": "control_response", "response": {
        "subtype": "success", "request_id": request.get("request_id"),
        "response": {"behavior": "allow", "updatedInput": {"questions": qs, "answers": norm}}}}


def user_line(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def interrupt_line() -> dict:
    return {"type": "control_request", "request_id": "int_" + uuid.uuid4().hex[:12], "request": {"subtype": "interrupt"}}


# ---------- 係の形 ----------
class AgentSession(Protocol):
    async def start(self) -> None: ...
    async def send(self, text: str) -> None: ...
    async def answer(self, question_id: str, answers: dict[str, Any] | str) -> None: ...
    async def stop(self) -> None: ...
    async def close(self) -> None: ...
    def events(self) -> AsyncIterator[dict]: ...
    @property
    def busy(self) -> bool: ...
    @property
    def waiting_question(self) -> bool: ...


class _Base:
    def __init__(self, normalizer: ClaudeNormalizer):
        self.norm = normalizer
        self._queue: asyncio.Queue = asyncio.Queue()
        self._questions: dict[str, dict] = {}
        self._busy = False
        self.sent: list[dict] = []  # 送った行（試験・監査用）

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def waiting_question(self) -> bool:
        return bool(self._questions)

    async def _write(self, obj: dict) -> None:
        raise NotImplementedError

    async def _handle(self, msg: dict) -> None:
        """CLI の 1 行を処理: 正規化して流し、control_request に返事をする。"""
        if msg.get("type") == "control_request":
            reply = control_reply(msg)
            if reply is None:
                self._questions[str(msg.get("request_id"))] = msg
            else:
                await self._write(reply)
        for e in self.norm.feed(msg):
            await self._queue.put(e)
        if msg.get("type") == "result":
            self._busy = False

    async def send(self, text: str) -> None:
        self._busy = True
        await self._write(user_line(text))

    async def answer(self, question_id: str, answers: dict[str, Any] | str) -> None:
        req = self._questions.get(question_id)
        if req is None:
            raise KeyError("この質問はもう答えてあるか、ありません")
        reply = answer_reply(req, answers)  # 形が違えば ValueError（質問は残す）
        self._questions.pop(question_id, None)
        await self._write(reply)

    async def events(self) -> AsyncIterator[dict]:
        while True:
            e = await self._queue.get()
            if e is None:
                return
            yield e


class ClaudeSession(_Base):
    """claude CLI を 1 本起こして stdin/stdout で話す。"""

    def __init__(self, argv: list[str], cwd: str, env: dict[str, str], normalizer: ClaudeNormalizer,
                 spawn: Callable[..., Awaitable[Any]] | None = None, stop_wait: float = 10.0):
        super().__init__(normalizer)
        self.argv, self.cwd, self.env = argv, cwd, env
        self.spawn = spawn or asyncio.create_subprocess_exec
        self.stop_wait = stop_wait
        self.proc = None
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.exit_code: int | None = None

    async def start(self) -> None:
        self.proc = await self.spawn(*self.argv, cwd=self.cwd, env=self.env, stdin=asyncio.subprocess.PIPE,
                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                                     limit=16 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            while True:
                raw = await self.proc.stdout.readline()
                if not raw:
                    break
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    await self._handle(msg)
        finally:
            self.exit_code = await self.proc.wait()
            self._busy = False
            self._questions.clear()
            await self._queue.put({"type": "_exit", "code": self.exit_code})
            await self._queue.put(None)

    async def _write(self, obj: dict) -> None:
        if self.proc is None or self.proc.stdin is None or self.proc.stdin.is_closing():
            raise RuntimeError("CLI が動いていません")
        self.sent.append(obj)
        async with self._lock:
            self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            await self.proc.stdin.drain()

    async def stop(self) -> None:
        """止める = interrupt。10 秒で result が来なければ終わらせる（次の送信で --resume）。"""
        if self.proc is None or self.proc.returncode is not None:
            return
        for qid in list(self._questions):  # 答え待ちの質問は「答えない」で閉じる
            req = self._questions.pop(qid)
            await self._write({"type": "control_response", "response": {
                "subtype": "success", "request_id": req.get("request_id"),
                "response": {"behavior": "deny", "message": "The user stopped this request."}}})
        await self._write(interrupt_line())
        for _ in range(int(self.stop_wait * 10)):
            if not self._busy:
                return
            await asyncio.sleep(0.1)
        await self._kill()

    async def close(self) -> None:
        """アイドル終了: stdin を閉じる → 10 秒待つ → 終わらなければ kill。"""
        if self.proc is None or self.proc.returncode is not None:
            return
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(self.proc.wait(), self.stop_wait)
        except asyncio.TimeoutError:
            await self._kill()

    async def _kill(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()


class RunnerSession(_Base):
    """箱の runner（agent/deploy/runner/agent_runner.py）に unix ソケットで頼んで CLI を起こす（本番・staging）。

    中継 → runner: {"op":"spawn",...} / {"op":"stdin","data":"<1 行>"} / {"op":"close_stdin"} / {"op":"kill"}
    runner → 中継: {"ev":"spawned"} / {"ev":"out","line":"<CLI の stdout 1 行>"} / {"ev":"exit","code":..} / {"ev":"error"}
    runner が返す行は箱の中から来る＝信用しない入力（1 行の大きさ・JSON の形を確かめ、合わなければ捨てる）。
    起こし方の引数（argv・環境・isolation.json）は runner が決まった形で作る。中継から渡すのは spawn の 5 項目だけ。
    """

    MAX_LINE = 32 << 20  # runner の MAX_OUT_LINE と同じ

    def __init__(self, socket_path: str, spawn: dict, normalizer: ClaudeNormalizer, stop_wait: float = 10.0,
                 connect: Callable[[str], Awaitable[tuple[Any, Any]]] | None = None):
        super().__init__(normalizer)
        self.socket_path = socket_path
        self.spawn_msg = {"op": "spawn", **spawn}
        self.stop_wait = stop_wait
        self._connect = connect or (lambda p: asyncio.open_unix_connection(p, limit=self.MAX_LINE))
        self._r: Any = None
        self._w: Any = None
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.exit_code: int | None = None
        self.pid: int | None = None

    async def _op(self, obj: dict) -> None:
        if self._w is None:
            raise RuntimeError("CLI が動いていません")
        async with self._lock:
            self._w.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._w.drain()

    async def _line(self) -> dict | None:
        raw = await self._r.readline()
        if not raw:
            return None
        try:
            msg = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return {}
        return msg if isinstance(msg, dict) else {}

    async def start(self) -> None:
        try:
            self._r, self._w = await self._connect(self.socket_path)
        except OSError as e:
            raise RuntimeError(f"箱の runner につながりません: {e.__class__.__name__}") from None
        await self._op(self.spawn_msg)
        first = await self._line()
        if not first or first.get("ev") != "spawned":
            why = str((first or {}).get("message") or "no answer")[:200]
            await self._close_conn()
            raise RuntimeError(f"箱の runner が CLI を起こせませんでした: {why}")
        self.pid = first.get("pid") if isinstance(first.get("pid"), int) else None
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            while True:
                try:
                    m = await self._line()
                except (ValueError, asyncio.LimitOverrunError, ConnectionError):
                    break  # 大きすぎる行・切断
                if m is None:
                    break
                kind = m.get("ev")
                if kind == "out":
                    line = m.get("line")
                    if not isinstance(line, str):
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        await self._handle(msg)
                elif kind == "exit":
                    code = m.get("code")
                    self.exit_code = code if isinstance(code, int) else -1
                    break
                elif kind == "error":
                    self.exit_code = self.exit_code if self.exit_code is not None else -1
        finally:
            if self.exit_code is None:
                self.exit_code = -1
            self._busy = False
            self._questions.clear()
            await self._close_conn()
            await self._queue.put({"type": "_exit", "code": self.exit_code})
            await self._queue.put(None)

    async def _close_conn(self) -> None:
        w, self._w = self._w, None
        if w is not None:
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass

    async def _write(self, obj: dict) -> None:
        self.sent.append(obj)
        await self._op({"op": "stdin", "data": json.dumps(obj, ensure_ascii=False)})

    async def stop(self) -> None:
        """止める = interrupt。10 秒で result が来なければ runner に kill を頼む（次の送信で --resume）。"""
        if self._w is None:
            return
        for qid in list(self._questions):
            req = self._questions.pop(qid)
            await self._write({"type": "control_response", "response": {
                "subtype": "success", "request_id": req.get("request_id"),
                "response": {"behavior": "deny", "message": "The user stopped this request."}}})
        await self._write(interrupt_line())
        for _ in range(int(self.stop_wait * 10)):
            if not self._busy:
                return
            await asyncio.sleep(0.1)
        await self._kill()

    async def close(self) -> None:
        """アイドル終了: stdin を閉じてもらう → 10 秒待つ → 終わらなければ kill。"""
        if self._w is None:
            return
        try:
            await self._op({"op": "close_stdin"})
        except (RuntimeError, ConnectionError):
            pass
        if self._reader is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._reader), self.stop_wait)
                return
            except asyncio.TimeoutError:
                pass
        await self._kill()

    async def _kill(self) -> None:
        try:
            await self._op({"op": "kill"})
        except (RuntimeError, ConnectionError):
            pass
        if self._reader is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._reader), 7.0)
            except asyncio.TimeoutError:
                await self._close_conn()  # runner は接続が切れると CLI を止める


class FakeSession(_Base):
    """取った stream-json を流し直す係（試験用）。1 回の send で次のターン（result まで）を流す。

    can_use_tool(AskUserQuestion) では answer() まで止まる。ほかの control_request には control_reply で返事をし、
    その返事は ``sent`` に残る（実物の CLI に送った行 ``*.sent.jsonl`` と比べられる）。
    """

    def __init__(self, lines: list[dict], normalizer: ClaudeNormalizer):
        super().__init__(normalizer)
        self.turns: list[list[dict]] = []
        cur: list[dict] = []
        for m in lines:
            cur.append(m)
            if m.get("type") == "result":
                self.turns.append(cur)
                cur = []
        if cur:
            self.turns.append(cur)
        self._go = asyncio.Queue()
        self._answered = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.stopped = False
        self.closed = False

    @classmethod
    def from_file(cls, path: str | os.PathLike, normalizer: ClaudeNormalizer) -> "FakeSession":
        lines = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
        return cls(lines, normalizer)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._play())

    async def _play(self) -> None:
        for turn in self.turns:
            await self._go.get()
            for msg in turn:
                if self.stopped:
                    break
                await self._handle(msg)
                if msg.get("type") == "control_request" and str(msg.get("request_id")) in self._questions:
                    self._answered.clear()
                    await self._answered.wait()
                await asyncio.sleep(0)
            if self.stopped:
                self._busy = False
                await self._queue.put({"type": "turn.end", "secs": 0.0, "ok": False, "reason": "aborted_tools"})
                self.stopped = False
        await self._go.get()  # 流すものが無くなったら次の送信で終わる
        await self._queue.put({"type": "_exit", "code": 0})
        await self._queue.put(None)

    async def _write(self, obj: dict) -> None:
        self.sent.append(obj)
        if obj.get("type") == "user":
            self._go.put_nowait(True)
        if obj.get("type") == "control_response":
            self._answered.set()

    async def stop(self) -> None:
        self.stopped = True
        self._questions.clear()
        self._answered.set()

    async def close(self) -> None:
        self.closed = True
        if self._task and not self._task.done():
            self._task.cancel()
        await self._queue.put({"type": "_exit", "code": 0})
        await self._queue.put(None)
