"""中継の中身（会話の管理・案・承認・反映）。公開口（relay.public）と内側口（relay.checkpoint）から呼ぶ。

async のもの = 画面から（CLI と話す）。同期のもの = 内側口から（スレッドで動く: DB・backend・rk の部品）。
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rk import RkError
from rk.applyrun import BASIS_CHANGED, apply_plan, check_basis, undo_run
from rk.backup import recent_backup
from rk.opsbase import Api
from rk.plan import check_plan, not_undoable_ops, readiness_errors
from rk.report import render_html

from relay import approvals
from relay import events as ev
from relay import models
from relay.approvals import Refused, plan_sha256
from relay.auth import User
from relay.bus import EventBus
from relay.config import RelayConfig
from relay.execs import BackupTaker, SqlExecutor, rk_config
from relay.normalize import ClaudeNormalizer
from relay.runner import (AgentSession, ClaudeSession, RunnerSession, claude_argv, claude_env,
                          write_session_files)
from relay.store import Store
from relay import codex_config as cx
from relay.codex_session import CodexRunnerSession, CodexSession, StartRefused, codex_env
from relay.watch import Watcher

RunnerFactory = Callable[[dict, str, "str | None"], AgentSession]
ApiFactory = Callable[["str | None", "str | None"], Api]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mem_available_mb() -> int | None:
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def claude_runner_factory(cfg: RelayConfig) -> RunnerFactory:
    """direct: 中継のプロセスから claude / codex を直接起こす（手元の開発・試験だけ。本番・staging は runner）。"""
    def make(session: dict, rk_token: str, resume_id: str | None) -> AgentSession:
        sdir = cfg.sessions_dir / session["id"]
        home = cfg.cli_home
        cwd = str(cfg.agent_dir)
        if session.get("provider") == "codex":
            work = sdir / "work"
            (work / "scratch").mkdir(parents=True, exist_ok=True)
            env = codex_env(home=home, cwd=cwd, relay_url=cfg.internal_url, rk_token=rk_token, work_dir=str(work),
                            https_proxy=cfg.https_proxy)
            return CodexSession(cx.codex_argv(cfg.codex_bin), cwd, env, work_dir=str(work), resume_id=resume_id)
        files = write_session_files(sdir, home, cwd)
        env = claude_env(home=home, cwd=cwd, relay_url=cfg.internal_url, rk_token=rk_token,
                         work_dir=str(sdir / "work"), https_proxy=cfg.https_proxy)
        return ClaudeSession(claude_argv(cfg.claude_bin, files, resume_id), cwd, env,
                             ClaudeNormalizer(allowed_key_sources=cfg.allowed_key_sources, emit_commands=True))
    return make


def sandbox_runner_factory(cfg: RelayConfig) -> RunnerFactory:
    """runner: 箱の runner（unix ソケット）に spawn を頼む（設計 §1-2 A）。argv・環境・isolation.json は runner が作る。"""
    def make(session: dict, rk_token: str, resume_id: str | None) -> AgentSession:
        if session.get("provider") == "codex":
            # モデルは thread/start で渡す（model/list で選んでから）。spawn の model は runner の形の確かめ用
            spawn = {"session_id": session["id"], "provider": "codex", "model": models.SOL_BASELINE,
                     "relay_token": rk_token}
            if resume_id:
                spawn["resume_id"] = resume_id
            work = f"{cfg.runner_sessions_dir.rstrip('/')}/{session['id']}/work"
            return CodexRunnerSession(cfg.runner_socket, spawn, cwd=cfg.runner_agent_dir, work_dir=work,
                                      resume_id=resume_id)
        spawn: dict[str, Any] = {"session_id": session["id"], "provider": "claude",
                                 "model": models.claude_model_arg(), "relay_token": rk_token}
        if resume_id:
            spawn["resume_id"] = resume_id
        return RunnerSession(cfg.runner_socket, spawn,
                             ClaudeNormalizer(allowed_key_sources=cfg.allowed_key_sources, emit_commands=True))
    return make


def default_runner_factory(cfg: RelayConfig) -> RunnerFactory:
    return sandbox_runner_factory(cfg) if cfg.launch == "runner" else claude_runner_factory(cfg)


@dataclass
class Live:
    runner: AgentSession
    token: str
    task: asyncio.Task | None = None
    last_active: float = field(default_factory=time.monotonic)
    last_send: float = 0.0
    closing: bool = False
    watcher: Watcher = field(default_factory=Watcher)


class RelayService:
    def __init__(self, cfg: RelayConfig, store: Store, *, runner_factory: RunnerFactory | None = None,
                 api_factory: ApiFactory | None = None, sql: SqlExecutor | None = None,
                 backup: BackupTaker | None = None, now: Callable[[], datetime] = utcnow,
                 mem_mb: Callable[[], int | None] = mem_available_mb):
        self.cfg = cfg
        self.store = store
        self.bus = EventBus(store)
        self.rk_cfg = rk_config(cfg)
        self.runner_factory = runner_factory or default_runner_factory(cfg)
        if api_factory is None:
            from relay.execs import backend_api
            api_factory = lambda approval, approver: backend_api(cfg, approval, approver)  # noqa: E731
        self.api_factory = api_factory
        self.sql = sql
        self.backup_taker = backup
        self.now = now
        self.mem_mb = mem_mb
        self.live: dict[str, Live] = {}
        self._tokens: dict[str, str] = {}
        self._sends: dict[str, collections.deque] = {}
        self._apply_lock = threading.Lock()
        #: 最後に会話を始めたときに分かったログインの様子（provider → {state, plan, at}）。トークンは持たない
        self.login_state: dict[str, dict] = {}

    # ================= 会話（画面から） =================
    def create_session(self, user: User, title: str = "", provider: str | None = None) -> dict:
        prov = provider or self.cfg.default_provider
        if prov not in self.cfg.providers:
            raise Refused("その AI はいま使えません", 400)
        model = models.SOL_BASELINE if prov == "codex" else models.claude_model_arg()
        return self.store.create_session(user.id, title=title[:80], provider=prov, model=model)

    def engine_info(self) -> dict:
        """画面用: 既定の AI と、各 AI のログインの様子（トークンは出さない）。"""
        return {"default_provider": self.cfg.default_provider, "providers": list(self.cfg.providers),
                "login": {p: dict(v) for p, v in self.login_state.items()}}

    def owned_session(self, session_id: str, user: User) -> dict:
        s = self.store.get_session(session_id)
        if s is None or str(s["user_id"]) != str(user.id) or s.get("archived_at"):
            raise Refused("相談が見つかりません", 404)  # 他人の相談はあることも見せない（D8）
        return s

    def _rate_check(self, user: User) -> None:
        q = self._sends.setdefault(user.id, collections.deque())
        t = time.monotonic()
        while q and t - q[0] > 3600:
            q.popleft()
        if len(q) >= self.cfg.sends_per_hour:
            raise Refused("1 時間に送れる回数を超えました。少し時間をおいてください", 429)
        q.append(t)

    async def _ensure_live(self, session: dict) -> Live:
        live = self.live.get(session["id"])
        if live is not None and not live.closing:
            return live
        active = [lv for sid, lv in self.live.items() if not lv.closing]
        if len(active) >= self.cfg.max_cli:
            for sid, lv in list(self.live.items()):  # 待っていない CLI があれば先に終える
                if not lv.runner.busy and not lv.runner.waiting_question:
                    await self._close_live(sid)
                    break
            else:
                self.bus.emit(session["id"], ev.status("queued", "ほかの相談が作業中です。終わってから始めます"))
                raise Refused("ほかの相談が作業中です。終わってからもう一度送ってください", 409)
        mem = self.mem_mb()
        if mem is not None and mem < self.cfg.min_mem_mb:
            raise Refused("いまサーバーが混んでいます。少し時間をおいてください", 503)
        token = secrets.token_urlsafe(24)
        runner = self.runner_factory(session, token, session.get("cli_session_id"))
        live = Live(runner=runner, token=token)
        self.live[session["id"]] = live
        self._tokens[token] = session["id"]
        self.bus.emit(session["id"], ev.status("starting"))
        try:
            await runner.start()
        except StartRefused as e:
            self._forget(session["id"], live)
            self._note_login(session.get("provider") or "", e.event.get("code"))
            self.record_tool_call(session, "start-refused", f"{e.event.get('code')}: {e.detail}", 0, 0)
            self.bus.emit(session["id"], e.event)
            self.bus.emit(session["id"], ev.status("error"))
            raise Refused(str(e.event.get("message")), 503) from None
        except RuntimeError:
            self._forget(session["id"], live)
            self.bus.emit(session["id"], ev.error("cli_start", "らく助の作業の係を起こせませんでした。少し時間をおいてください"))
            raise Refused("らく助の作業の係を起こせませんでした。少し時間をおいてください", 503) from None
        live.task = asyncio.create_task(self._pump(session["id"], live))
        return live

    async def _pump(self, session_id: str, live: Live) -> None:
        norm = getattr(live.runner, "norm", None)
        async for e in live.runner.events():
            if norm is not None:
                self.bus.set_turn(session_id, norm.turn_id)
            t = e.get("type")
            if t == "_meta":
                fields = {k: v for k, v in (("cli_session_id", e.get("cli_session_id")),
                                            ("resolved_model", e.get("model"))) if v}
                if fields:
                    self.store.update_session(session_id, **fields)
                if e.get("provider") == "codex":
                    self._note_login("codex", "ok", e.get("plan_type"))
                    for fb in e.get("model_fallbacks") or []:
                        self.record_tool_call({"id": session_id}, "model-fallback", f"{fb.get('from')}->{fb.get('to')}", 0, 0)
                continue
            if t in ("_cmd", "_audit") or (t not in ("_abort", "_exit") and ev.is_internal(e)):
                if t == "_audit":
                    self.record_tool_call({"id": session_id}, "cx-approval",
                                          f"{e.get('kind')}:{e.get('why')}: {e.get('target')}",
                                          1 if e.get("decision") == "accept" else 0, 0)
                await self._watch(session_id, live, e)
                continue
            if t == "_abort":
                asyncio.create_task(self._close_live(session_id))
                continue
            if t == "_exit":
                if not live.closing and e.get("code") not in (0, None):
                    self.bus.emit(session_id, ev.error("cli_exit", "らく助の作業の係が止まりました。もう一度送ると続きから始めます"))
                continue
            live.last_active = time.monotonic()
            await self._watch(session_id, live, e)
            self.bus.emit(session_id, e)
            if t == "status":
                self.store.update_session(session_id, status=e["state"], last_active_at=self.now())
        self._forget(session_id, live)

    def _note_login(self, provider: str, code: str | None, plan: str | None = None) -> None:
        if not provider:
            return
        state = {"ok": "ok", "login": "missing", "auth_source": "not_subscription"}.get(code or "", None)
        if state is None:
            return
        self.login_state[provider] = {"state": state, "plan": plan, "at": self.now().isoformat()}

    async def _watch(self, session_id: str, live: Live, e: dict) -> None:
        """脇道の見張り（relay.watch）。印は監査へ・最初は道案内・重い/重なったら止めて琥珀の 1 行。"""
        for act in live.watcher.observe(e):
            if act.kind == "log":
                self.record_tool_call({"id": session_id}, "watch", f"{act.flag}: {act.detail}", 0, 0)
            elif act.kind == "steer":
                try:
                    await live.runner.send(act.text)
                except RuntimeError:
                    pass
            elif act.kind == "stop":
                asyncio.create_task(live.runner.stop())
            elif act.kind == "card":
                self.bus.emit(session_id, {**ev.error("watch", act.text, retry=True), "level": "warn"})

    def _forget(self, session_id: str, live: Live) -> None:
        if self.live.get(session_id) is live:
            del self.live[session_id]
        self._tokens.pop(live.token, None)

    async def _close_live(self, session_id: str) -> None:
        live = self.live.get(session_id)
        if live is None:
            return
        live.closing = True
        try:
            await live.runner.close()
        finally:
            self._forget(session_id, live)

    async def send_message(self, session: dict, user: User, text: str, device: str = "pc",
                           plan_ref: int | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            raise Refused("空の相談は送れません", 400)
        if len(text) > 8000:
            raise Refused("長すぎます（8,000 字まで）", 400)
        if plan_ref is not None and self.store.get_plan(session["id"], plan_ref) is None:
            raise Refused("その案は見つかりません", 404)
        self._rate_check(user)
        msg = {"type": "user.message", "text": text, "user_id": user.id, "device": device}
        if plan_ref is not None:
            msg["plan_ref"] = plan_ref
        e = self.bus.emit(session["id"], msg)
        if session.get("title") in ("", "新しい相談"):
            self.store.update_session(session["id"], title=text.splitlines()[0][:40])
        cli_text = f"【案 {plan_ref} について】{text}" if plan_ref is not None else text
        await self._send_cli(session, cli_text)
        return e or {}

    async def _send_cli(self, session: dict, text: str) -> None:
        live = await self._ensure_live(session)
        live.last_send = live.last_active = time.monotonic()
        self.store.update_session(session["id"], last_active_at=self.now())
        await live.runner.send(text)

    async def answer(self, session: dict, user: User, question_id: str, answers: dict | str,
                     device: str = "pc") -> None:
        live = self.live.get(session["id"])
        if live is None:
            raise Refused("この質問はもう閉じています", 409)
        try:
            await live.runner.answer(question_id, answers)
        except KeyError:
            raise Refused("この質問はもう答えてあります（ほかの端末で答えた可能性があります）", 409) from None
        except ValueError as e:
            raise Refused(str(e), 400) from None
        live.last_active = time.monotonic()
        norm = answers if isinstance(answers, dict) else {"": answers}
        self.bus.emit(session["id"], {"type": "question.answered", "question_id": question_id,
                                      "answers": {str(k): str(v) for k, v in norm.items()},
                                      "user_id": user.id, "device": device})

    async def stop(self, session: dict) -> None:
        live = self.live.get(session["id"])
        if live is not None:
            await live.runner.stop()

    async def sweep(self) -> None:
        """アイドル 20 分で終える（質問の答え待ちは数えない）・1 ターン 15 分で止める。"""
        t = time.monotonic()
        for sid, live in list(self.live.items()):
            r = live.runner
            if r.busy and live.last_send and t - live.last_send > self.cfg.turn_timeout and not r.waiting_question:
                await r.stop()
                self.bus.emit(sid, ev.error("turn_timeout", "時間がかかりすぎたので止めました", retry=True))
            elif not r.busy and not r.waiting_question and t - live.last_active > self.cfg.idle_seconds:
                await self._close_live(sid)

    async def shutdown(self) -> None:
        for sid in list(self.live):
            await self._close_live(sid)

    # ================= 案の承認（画面から） =================
    def _plan_for_approval(self, session: dict, version: int) -> dict:
        p = self.store.get_plan(session["id"], version)
        if p is None:
            raise Refused("案が見つかりません", 404)
        latest = self.store.latest_plan(session["id"])
        if latest is None or latest["id"] != p["id"]:
            raise Refused("この案より新しい案があります。新しい案を見てください", 409)
        return p

    async def approve_plan(self, session: dict, user: User, version: int, device: str = "pc") -> dict:
        p = self._plan_for_approval(session, version)
        if p["status"] not in ("draft", "approved"):
            raise Refused(f"この案は「{p['status']}」なので承認できません", 409)
        if not p["check_result"].get("ready", False):
            raise Refused("まだ反映できない案です（決めていないことがあります）", 409)
        self.store.set_plan_status(p["id"], "approved")
        ap = approvals.issue(self.store, session=session, user_id=user.id, kind="apply", sha=p["plan_sha256"],
                             now=self.now(), ttl_seconds=self.cfg.approval_ttl, plan=p, device=device)
        self.bus.emit(session["id"], {"type": "approval.result", "version": p["version"], "kind": "apply",
                                      "decision": "approve", "user_id": user.id, "device": device,
                                      "expires_at": ap["expires_at"].isoformat()})
        self.bus.emit(session["id"], ev.status("applying", "反映しています"))
        who = user.username or "管理者"
        code = ap["id"]
        await self._send_cli(session, (
            f"【らく助の画面から】{who}さんが案 {p['version']}（{p['title']}）で反映すると決めました。"
            f"承認番号 {code} で反映してください。\n"
            f"rk apply <案 {p['version']} として rk plan submit した plan.json> --approved {code} で反映し、"
            "rk verify で確かめて、結果を報告してください。"))
        return {"approval_id": code, "expires_at": ap["expires_at"].isoformat(), "version": p["version"]}

    async def reject_plan(self, session: dict, user: User, version: int, device: str = "pc") -> None:
        p = self._plan_for_approval(session, version)
        if p["status"] not in ("draft", "approved"):
            raise Refused(f"この案は「{p['status']}」です", 409)
        self.store.set_plan_status(p["id"], "rejected")
        self.store.replace_open_approvals(session["id"], "apply", p["id"], None)
        self.bus.emit(session["id"], {"type": "approval.result", "version": p["version"], "kind": "apply",
                                      "decision": "reject", "user_id": user.id, "device": device})
        await self._send_cli(session, f"【らく助の画面から】{user.username or '管理者'}さんは案 {p['version']} を"
                                      "やめると決めました。反映しないでください。")

    async def approve_undo(self, session: dict, user: User, run_id: str, device: str = "pc") -> dict:
        run = self.store.get_run(run_id)
        if run is None or run["session_id"] != session["id"]:
            raise Refused("反映の記録が見つかりません", 404)
        if run["status"] == "undone":
            raise Refused("この反映はもう元に戻してあります", 409)
        undo_plan = self._undo_plan(run_id)
        ap = approvals.issue(self.store, session=session, user_id=user.id, kind="undo", sha=plan_sha256(undo_plan),
                             now=self.now(), ttl_seconds=self.cfg.approval_ttl, target_run_id=run_id, device=device)
        self.bus.emit(session["id"], {"type": "approval.result", "version": 0, "kind": "undo", "decision": "approve",
                                      "user_id": user.id, "device": device, "run_id": run_id,
                                      "expires_at": ap["expires_at"].isoformat()})
        code = ap["id"]
        await self._send_cli(session, (
            f"【らく助の画面から】{user.username or '管理者'}さんが反映 {run_id} を元に戻すと決めました。"
            f"承認番号 {code} で元に戻してください。\nrk undo {run_id} --approved {code} で元に戻し、結果を報告してください。"))
        return {"approval_id": code, "expires_at": ap["expires_at"].isoformat()}

    # ================= 内側口（箱の rk から・同期） =================
    def session_for_token(self, token: str | None) -> dict:
        sid = self._tokens.get(token or "")
        s = self.store.get_session(sid) if sid else None
        if s is None:
            raise Refused("合言葉が違います（この会話は終わっています）", 401)
        return s

    def issue_rk_token(self, session_id: str) -> str:
        """試験・手元の確認用: 会話に合言葉を出す（ふだんは CLI を起こすときに出す）。"""
        token = secrets.token_urlsafe(24)
        self._tokens[token] = session_id
        return token

    def progress(self, session: dict, text: str) -> None:
        text = " ".join(str(text or "").split())[:80]
        if text:
            self.bus.emit(session["id"], {"type": "progress", "text": text})

    def submit_plan(self, session: dict, plan: Any) -> dict:
        if not isinstance(plan, dict):
            raise Refused("案は JSON のオブジェクトです", 400)
        errs = check_plan(plan)
        ready_errs = readiness_errors(plan)
        other = [e for e in errs if e not in ready_errs]
        if other:
            raise Refused("案に誤りがあります:\n  " + "\n  ".join(other), 422)
        blockers = [str(b) for b in plan.get("blockers") or [] if str(b).strip()] if ready_errs else []
        ready = not ready_errs
        html_a4 = render_html(plan)
        html_a3 = None
        if plan.get("timelines") or plan.get("timeline"):
            try:
                html_a3 = render_html(plan, a3=True)
            except RkError:
                html_a3 = None
        row = self.store.add_plan(
            session["id"], kind=str(plan["kind"]), title=str(plan["title"]), plan_json=plan,
            plan_sha256=plan_sha256(plan), check_result={"errors": errs, "ready": ready, "blockers": blockers},
            basis_hash=(plan.get("source") or {}).get("basis_hash"), html_a4=html_a4, html_a3=html_a3)
        self.bus.emit(session["id"], self._plan_event(session, row))
        if ready:
            # 承認カード（画面のボタン）。承認番号はまだ無い（人が押した時に公開口で作る・設計 §3-1）
            self.bus.emit(session["id"], {"type": "approval.request", "kind": "apply", "version": row["version"],
                                          "plan_id": row["id"]})
            self.bus.emit(session["id"], ev.status("waiting_approval"))
        return {"plan_id": row["id"], "version": row["version"], "ready": ready, "blockers": blockers}

    def _plan_event(self, session: dict, row: dict) -> dict:
        plan = row["plan_json"]
        nu = not_undoable_ops(plan)
        base = f"/agent-relay/sessions/{session['id']}/plans/{row['version']}/html"
        urls = {"a4": base + "?size=a4"}
        if row.get("html_a3"):
            urls["a3"] = base + "?size=a3"
        return {"type": "plan", "plan_id": row["id"], "version": row["version"], "title": row["title"],
                "summary": [str(x) for x in (plan.get("summary") or [])][:5],
                "counts": {"ops": len(plan.get("ops") or []), "not_undoable": len(nu)},
                "undoable": not nu, "ready": row["check_result"].get("ready", False),
                "blockers": row["check_result"].get("blockers", []), "html_urls": urls, "status": row["status"]}

    def run_backup(self, session: dict, label: str) -> dict:
        if self.backup_taker is None:
            raise Refused("バックアップの係が用意されていません", 503)
        try:
            return self.backup_taker.take(label)
        except RkError as e:
            raise Refused(str(e), 500) from None

    def _ensure_backup(self, label: str) -> dict:
        rec = recent_backup(self.rk_cfg, now=self.now())
        if rec is not None:
            return rec
        if self.backup_taker is None:
            raise Refused("バックアップの係が用意されていないので反映できません", 503)
        return self.backup_taker.take(label)

    def _progress_cb(self, session_id: str, run_kind: str) -> Callable[[int, int, str, bool], None]:
        def cb(i: int, total: int, label: str, ok: bool) -> None:
            self.bus.emit(session_id, {"type": "run.progress", "run_kind": run_kind, "i": i + 1, "total": total,
                                       "label": label, "ok": ok})
        return cb

    def _store_run(self, session: dict, res: dict, *, kind: str, approval: dict, plan_row: dict | None,
                   started: datetime) -> None:
        run_dir = Path(res["run_dir"])
        log = []
        if (run_dir / "log.jsonl").exists():
            log = [json.loads(x) for x in (run_dir / "log.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        undo = None
        if (run_dir / "undo.json").exists():
            undo = json.loads((run_dir / "undo.json").read_text(encoding="utf-8"))
        public = {k: v for k, v in res.items() if k != "run_dir"}
        self.store.add_run(id=res["run_id"], session_id=session["id"], plan_id=plan_row["id"] if plan_row else None,
                           approval_id=approval["id"], kind=kind, backup_path=res.get("backup"),
                           status="ok" if res["ok"] else "stopped", log=log, undo=undo, result=public,
                           started_at=started, finished_at=self.now())
        self.store.set_approval_run(approval["id"], res["run_id"])

    def _run_event(self, kind: str, res: dict) -> dict:
        done = res["applied"] + (res.get("unknown") or 0)
        return {"type": "run.result", "run_kind": kind, "run_id": res["run_id"], "ok": res["ok"],
                "applied": res["applied"], "total": res["total"], "error": res.get("error"),
                "undoable": bool(done) and len(res.get("not_undoable") or []) < done, "backup": res.get("backup")}

    def apply(self, session: dict, plan: Any, code: str) -> dict:
        if not isinstance(plan, dict):
            raise Refused("案は JSON のオブジェクトです", 400)
        sha = plan_sha256(plan)
        with self._apply_lock:
            now = self.now()
            ap, prow = approvals.check(self.store, code=code, session=session, kind="apply", sha=sha, now=now)
            if not self.store.consume_approval(code, now):
                raise Refused("その承認番号はもう使われています（同じ番号で 2 回は反映できません）", 409)
            sid = session["id"]
            self.store.set_plan_status(prow["id"], "applying")
            self.bus.emit(sid, ev.status("applying"))
            try:
                if self.sql is None:
                    raise Refused("読み取りの係が用意されていないので、予定が変わっていないかを確かめられません", 503)
                check_basis(self.rk_cfg, plan, run_sql=lambda q: self.sql.run(q))
            except RkError as e:
                stale = BASIS_CHANGED in str(e)
                self.store.set_plan_status(prow["id"], "stale" if stale else "failed")
                self.bus.emit(sid, {**self._plan_event(session, self.store.get_plan_by_id(prow["id"]))})
                self.bus.emit(sid, ev.error("basis_changed" if stale else "basis_check", str(e), retry=True))
                raise Refused(str(e), 409) from None
            try:
                backup = self._ensure_backup(f"s{sid[:8]}-v{prow['version']}")
            except RkError as e:
                self.store.set_plan_status(prow["id"], "failed")
                raise Refused(f"バックアップを取れないので反映しません: {e}", 500) from None
            api = self.api_factory(code, ap["user_id"])
            approved = f"画面の承認 {code}（user {ap['user_id']}・{ap['device']}・{now.isoformat()}）"
            res = apply_plan(self.rk_cfg, plan, api, approved, backup=backup["path"], now=self.now(),
                             source={"relay_session": sid, "plan_version": prow["version"], "approval": code},
                             progress=self._progress_cb(sid, "apply"))
            self._store_run(session, res, kind="apply", approval=ap, plan_row=prow, started=now)
            self.store.set_plan_status(prow["id"], "applied" if res["ok"] else "failed")
            self.bus.emit(sid, self._run_event("apply", res))
            self.bus.emit(sid, ev.status("working"))
            return {k: v for k, v in res.items() if k != "run_dir"}

    def _undo_plan(self, run_id: str) -> dict:
        p = self.rk_cfg.runs_dir / run_id / "undo.json"
        if not p.exists():
            raise Refused("元に戻す案（undo.json）がありません", 404)
        return json.loads(p.read_text(encoding="utf-8"))

    def undo(self, session: dict, run_id: str, code: str, partial: bool = False) -> dict:
        run = self.store.get_run(run_id)
        if run is None or run["session_id"] != session["id"]:
            raise Refused("この相談の反映の記録ではありません", 404)
        with self._apply_lock:
            now = self.now()
            ap, _ = approvals.check(self.store, code=code, session=session, kind="undo",
                                    sha=plan_sha256(self._undo_plan(run_id)), now=now, target_run_id=run_id)
            if not self.store.consume_approval(code, now):
                raise Refused("その承認番号はもう使われています", 409)
            sid = session["id"]
            self.bus.emit(sid, ev.status("applying", "元に戻しています"))
            backup = self._ensure_backup(f"s{sid[:8]}-undo")
            api = self.api_factory(code, ap["user_id"])
            try:
                res = undo_run(self.rk_cfg, run_id, api, f"画面の承認 {code}（user {ap['user_id']}）",
                               backup=backup["path"], partial=partial, now=self.now(),
                               progress=self._progress_cb(sid, "undo"))
            except RkError as e:
                raise Refused(str(e), 409) from None
            self._store_run(session, res, kind="undo", approval=ap, plan_row=None, started=now)
            if res["ok"]:
                self.store.update_run(run_id, status="undone")
            self.bus.emit(sid, self._run_event("undo", res))
            self.bus.emit(sid, ev.status("working"))
            return {k: v for k, v in res.items() if k != "run_dir"}

    def run_plan(self, session: dict, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run is None or run["session_id"] != session["id"]:
            raise Refused("この相談の反映の記録ではありません", 404)
        p = self.rk_cfg.runs_dir / run_id / "plan.json"
        return json.loads(p.read_text(encoding="utf-8"))

    def record_tool_call(self, session: dict, kind: str, target: str, status: int, ms: int,
                         sql_head: str | None = None, rows: int | None = None) -> None:
        try:
            self.store.add_tool_call(session["id"], kind=kind, target=target[:500], status=status, ms=ms,
                                     sql_head=(sql_head or "")[:300] or None, rows=rows)
        except Exception:
            pass  # 記録の失敗で読み取りを止めない
