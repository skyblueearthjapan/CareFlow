"""カルテ（看護記録）の下書きを、決めた管理者の ChatGPT の席で作る（PO 2026-10-13）。

設計: docs/plans/karte-via-seat-2026-10-13.md。

- 呼ぶのは backend だけ（サーバーどうしの口 relay.service_port・合言葉つき）。画面からは呼ばない。
- 入力 = 文字起こしの文＋名前の無い文脈（年代・性別・訪問日時・職種）。出力 = backend の Gemini の要約と同じ形の
  ``summary``（主訴・様子 / バイタル / 処置・ケア / 申し送り / 次回 / free）。形は backend
  ``app/services/voice/prompts.py`` の SUMMARY_SECTIONS・VITAL_KEYS と同じ（変えるときは両方そろえる）。
- 動かす席: ``agent.settings`` の ``karte_mode``（designated = ``karte_seat_user_id`` の管理者の席・既定 /
  presser = 録音した本人の席〔全員が ChatGPT の席を持ったら〕）。
- 1 回きりの codex app-server（箱の runner・その席の CODEX_HOME）: thread は ``ephemeral``（席に会話の写しを残さない）・
  ``sandbox=read-only``・``approvalPolicy=never``・作業フォルダーは空（AGENTS.md を読ませない）。rk の合言葉は
  中継に登録しない使い捨て（内側口は断る）。道具（コマンド・ファイル・質問）が 1 つでも動いたら打ち切る。
- 失敗したら ``kind`` を返すだけ（backend が Gemini の要約に戻る）。記録（agent.admin_events）に文字起こしは書かない。
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from typing import Any, Callable

from relay import models
from relay.approvals import Refused
from relay.auth import User
from relay.codex_session import CodexRunnerSession, StartRefused
from relay.engines import seat_key

PROMPT_VERSION = "seat-v1"
MODES = ("designated", "presser")
SETTING_MODE = "karte_mode"
SETTING_SEAT = "karte_seat_user_id"
MAX_TRANSCRIPT_CHARS = 60_000

#: backend の prompts.SUMMARY_SECTIONS・VITAL_KEYS と同じ（変えるときは両方）
SUMMARY_SECTIONS = ("主訴・様子", "バイタル", "処置・ケア", "申し送り", "次回", "free")
LIST_SECTIONS = ("主訴・様子", "処置・ケア", "申し送り")
VITAL_KEYS = ("血圧", "体温", "脈拍", "SpO2", "血糖")
UNKNOWN = "［不明］"

#: 文脈として受け取るもの（名前は受け取らない）。値は短い文字列だけ
CONTEXT_KEYS = {"age_band": "年代", "sex": "性別", "visit_date": "訪問日", "visit_time": "訪問の時刻",
                "staff_role": "訪問した職種"}

#: thread/start に足すもの: 道具を動かさせない・席に会話を残さない
THREAD_EXTRA = {"sandbox": "read-only", "approvalPolicy": "never", "ephemeral": True}

#: backend へ返す失敗の種類（backend は error_kind に "seat_" を付けて残す）
FAIL_KINDS = ("disabled", "no_seat", "signed_out", "busy", "unreachable", "timeout", "rate_limited", "tool_use",
              "failed", "parse")

UUIDISH = re.compile(r"^[0-9A-Za-z-]{1,64}$")


class KarteFailed(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind if kind in FAIL_KINDS else "failed"


def instructions() -> str:
    """developerInstructions（ルールと出力の形）。backend の看護記録のテンプレ（prompts.py）を、文字起こしの文から
    作る形に合わせたもの。"""
    return f"""あなたは訪問看護の記録を補助するアシスタントです。
渡されるのは、日本の訪問看護師が利用者宅で行った訪問の会話を文字起こししたものです（話者ラベル付き）。
これを読んで、看護記録の下書きを作ってください。

# 絶対に守るルール
- 道具（コマンド・ファイルの読み書き・検索・質問）は一切使わない。渡された文だけを読んで、すぐに答えを書く。
- 文字起こしに無いことを推測・創作しない。分からないことは書かない。
- 聞き取れなかった所（{UNKNOWN}）や、言い方があいまいで確かでない所は、その項目の中で {UNKNOWN} を付けて残す（補わない）。
- 数値（血圧・体温・脈拍・SpO2・血糖・服薬量など）は文字起こしどおりに写す。丸めない・単位を足さない。
- 人名は文字起こしのとおりの呼称のまま書く（フルネームに直さない・敬称を足さない）。
- 出力は JSON のオブジェクト 1 つだけ。前置き・説明・コードフェンス（```）を付けない。

# 出力する JSON の形
{{
  "summary": {{
    "主訴・様子": ["利用者の訴え・全身状態・生活の様子を短い箇条書きで"],
    "バイタル": {{"血圧": "", "体温": "", "脈拍": "", "SpO2": "", "血糖": ""}},
    "処置・ケア": ["実施した処置・ケアを箇条書きで"],
    "申し送り": ["次の担当者・主治医・ケアマネに伝えるべきことを箇条書きで"],
    "次回": "次回訪問の予定や約束（無ければ空文字）",
    "free": "上のどれにも入らない補足（無ければ空文字）"
  }}
}}

# 補足
- 配列は該当が無ければ空配列 []、文字列は空文字 "" にする。
- バイタルは測定値が語られた項目だけ埋める。語られていない項目は空文字にする（{UNKNOWN} を入れない）。
"""


def turn_text(transcript: str, context: dict[str, str]) -> str:
    lines = [f"- {CONTEXT_KEYS[k]}: {v}" for k, v in context.items() if v]
    ctx = "\n".join(lines) if lines else "- （不明。推測して補わないこと）"
    return (f"# 文脈\n{ctx}\n\n# 文字起こし（ここから）\n{transcript}\n# 文字起こし（ここまで）\n\n"
            "上の文字起こしから、決められた JSON だけを返してください。")


def validate_request(payload: Any) -> dict:
    """backend からの頼み。形が違えば ValueError（400）。"""
    if not isinstance(payload, dict):
        raise ValueError("body must be an object")
    rid = payload.get("recording_id")
    if not isinstance(rid, str) or not UUIDISH.match(rid):
        raise ValueError("bad recording_id")
    tr = payload.get("transcript")
    if not isinstance(tr, str) or not tr.strip():
        raise ValueError("empty transcript")
    if len(tr) > MAX_TRANSCRIPT_CHARS:
        raise ValueError("transcript too long")
    presser = payload.get("presser_user_id")
    if presser is not None and (not isinstance(presser, str) or not UUIDISH.match(presser)):
        raise ValueError("bad presser_user_id")
    raw = payload.get("context") or {}
    if not isinstance(raw, dict):
        raise ValueError("bad context")
    ctx = {k: str(raw[k]).strip()[:20] for k in CONTEXT_KEYS if isinstance(raw.get(k), (str, int)) and str(raw[k]).strip()}
    return {"recording_id": rid, "transcript": tr, "presser_user_id": presser, "context": ctx}


def _strip_fence(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if m:
        return m.group(1)
    a, b = t.find("{"), t.rfind("}")
    return t[a:b + 1] if a >= 0 and b > a else t


def parse_summary(text: str) -> dict:
    """モデルの答え → backend と同じ形の summary（足りない項目は空で補う・余計な項目は捨てる）。"""
    try:
        data = json.loads(_strip_fence(text or ""))
    except ValueError:
        raise KarteFailed("parse") from None
    if not isinstance(data, dict):
        raise KarteFailed("parse")
    src = data.get("summary") if isinstance(data.get("summary"), dict) else data
    if not any(k in src for k in SUMMARY_SECTIONS):
        raise KarteFailed("parse")
    out: dict[str, Any] = {}
    for k in LIST_SECTIONS:
        v = src.get(k)
        if isinstance(v, str):
            v = [v] if v.strip() else []
        out[k] = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    vit = src.get("バイタル") if isinstance(src.get("バイタル"), dict) else {}
    out["バイタル"] = {k: str(vit.get(k) or "").strip() for k in VITAL_KEYS}
    for k in ("次回", "free"):
        v = src.get(k)
        out[k] = "\n".join(map(str, v)) if isinstance(v, list) else str(v or "").strip()
    return out


SessionFactory = Callable[[str, str], Any]


class KarteDrafter:
    """1 件ずつ（同時に 1 つ）下書きを作る。svc = RelayService（設定・記録・席の様子・メモリ）。"""

    def __init__(self, svc, *, session_factory: SessionFactory | None = None, timeout: float | None = None):
        self.svc = svc
        self.session_factory = session_factory or self._runner_session
        self.timeout = float(timeout if timeout is not None else svc.cfg.karte_timeout)
        self._lock = asyncio.Lock()

    # ---------- 設定（⚙ の「カルテの下書きに使う席」） ----------
    def mode(self) -> str:
        v = self.svc._setting(SETTING_MODE)
        return v if v in MODES else "designated"

    def seat_user_id(self) -> str:
        v = self.svc._setting(SETTING_SEAT)
        return str(v) if v else ""

    async def settings_view(self, user: User) -> dict:
        """管理者だけ: いまの決まりと、選べる管理者（その人の ChatGPT のサインインの様子）。"""
        if not user.is_admin:
            raise Refused("管理者だけが見られます", 403)
        rows = await self.svc.admin_users(user)
        states = await asyncio.gather(*(self.svc.engines.status("codex", seat_key(str(r["id"]))) for r in rows))
        seat = self.seat_user_id()
        seats = [{"user_id": str(r["id"]), "name": str(r.get("name") or "（名前なし）")[:60],
                  "me": str(r["id"]) == str(user.id), "state": st} for r, st in zip(rows, states)]
        return {"mode": self.mode(), "modes": list(MODES), "seat_user_id": seat or None,
                "seat_known": any(s["user_id"] == seat for s in seats), "seats": seats}

    async def update_settings(self, user: User, payload: dict) -> dict:
        if not user.is_admin:
            raise Refused("管理者だけが変えられます", 403)
        mode_before, seat_before = self.mode(), self.seat_user_id()
        mode = payload.get("mode", mode_before)
        if mode not in MODES:
            raise Refused("その決め方はありません", 400)
        seat = payload.get("seat_user_id", seat_before) or ""
        if seat and seat != seat_before:
            rows = await self.svc.admin_users(user)
            if not any(str(r["id"]) == str(seat) for r in rows):
                raise Refused("らく助の管理者から選んでください", 400)
            state = await self.svc.engines.status("codex", seat_key(str(seat)), fresh=True)
            if state in ("missing", "api_key"):
                raise Refused("その方は ChatGPT にサインインしていません（⚙ の「あなたの ChatGPT」からサインインしてもらってください）", 409)
        if mode != mode_before:
            self.svc.store.set_setting(SETTING_MODE, mode, user.id)
        if seat != seat_before:
            self.svc.store.set_setting(SETTING_SEAT, seat or None, user.id)
        self.svc._audit(user.id, "karte_settings", "codex",
                        f"mode={mode_before}->{mode} seat={seat_before or '-'}->{seat or '-'}")
        return await self.settings_view(user)

    # ---------- 下書き（backend から） ----------
    def _seat_for(self, req: dict) -> str:
        if self.mode() == "presser":
            return req.get("presser_user_id") or ""
        return self.seat_user_id()

    async def draft(self, payload: Any) -> dict:
        """成功: {ok: True, summary, model, prompt_version, seat_user_id, mode, secs}。
        失敗: {ok: False, kind, seat_user_id, mode, secs}（backend は Gemini の要約に戻る）。形の誤りは ValueError。"""
        req = validate_request(payload)
        t0 = time.monotonic()
        mode = self.mode()
        seat = ""
        result: dict
        try:
            if not self.svc.cfg.enabled:
                raise KarteFailed("disabled")
            seat = self._seat_for(req)
            if not seat:
                raise KarteFailed("no_seat")
            key = seat_key(seat)
            if await self.svc.engines.status("codex", key) in ("missing", "api_key"):
                raise KarteFailed("signed_out")
            mem = self.svc.mem_mb()
            if mem is not None and mem < self.svc.cfg.min_mem_mb:
                raise KarteFailed("busy")
            summary, model = await asyncio.wait_for(self._locked_run(key, req), self.timeout)
            result = {"ok": True, "summary": summary, "model": model, "prompt_version": PROMPT_VERSION}
        except asyncio.TimeoutError:
            result = {"ok": False, "kind": "timeout"}
        except KarteFailed as e:
            result = {"ok": False, "kind": e.kind}
        secs = round(time.monotonic() - t0, 1)
        result.update({"seat_user_id": seat or None, "mode": mode, "secs": secs})
        how = f"ok model={result.get('model')}" if result["ok"] else f"fallback={result['kind']}"
        # 文字起こし・下書きの本文は書かない（録音の id と結果だけ）
        self.svc._audit(seat or "-", "karte_draft", "codex", f"rec={req['recording_id']} mode={mode} {how} secs={secs}")
        return result

    async def _locked_run(self, key: str, req: dict) -> tuple[dict, str | None]:
        async with self._lock:
            return await self._run(key, req)

    async def _run(self, key: str, req: dict) -> tuple[dict, str | None]:
        sid = "karte-" + secrets.token_hex(12)
        sess = self.session_factory(sid, key)
        try:
            try:
                await sess.start()
            except StartRefused as e:
                code = e.event.get("code")
                if code == "login":
                    self.svc.engines.forget(key)
                raise KarteFailed("signed_out" if code in ("login", "auth_source") else "failed") from None
            except RuntimeError as e:
                raise KarteFailed("busy" if "busy" in str(e) else "unreachable") from None
            await sess.send(turn_text(req["transcript"], req["context"]))
            text = ""
            async for e in sess.events():
                t = e.get("type")
                if t in ("step.start", "_audit", "_cmd") or (t == "question" and e.get("source") != "text"):
                    raise KarteFailed("tool_use")  # 道具が動いた・動こうとした → 使わない
                if t == "message":
                    text = str(e.get("text") or "") or text
                elif t == "status" and e.get("state") == "rate_limited":
                    raise KarteFailed("rate_limited")
                elif t == "error" and e.get("level") != "warn":
                    code = e.get("code")
                    raise KarteFailed({"login": "signed_out", "busy": "busy"}.get(code, "failed"))
                elif t == "_exit":
                    raise KarteFailed("failed")
                elif t == "turn.end":
                    if not e.get("ok"):
                        raise KarteFailed("failed")
                    break
            picker = getattr(sess, "picker", None)
            model = getattr(picker, "current", None) if picker is not None else None
            return parse_summary(text), model
        finally:
            try:
                await asyncio.wait_for(sess.close(), 15)
            except Exception:  # noqa: BLE001  後始末の失敗で結果を捨てない（runner は接続が切れると CLI を止める）
                pass

    def _runner_session(self, session_id: str, key: str):
        cfg = self.svc.cfg
        if cfg.launch != "runner" or not cfg.runner_socket:
            raise KarteFailed("unreachable")
        # rk の合言葉は使い捨て（中継に登録しない＝内側口は断る）
        spawn = {"session_id": session_id, "provider": "codex", "model": models.SOL_BASELINE,
                 "relay_token": secrets.token_urlsafe(24), "user_key": key}
        work = f"{cfg.runner_sessions_dir.rstrip('/')}/{session_id}/work"  # 空のフォルダー（AGENTS.md が無い）
        return CodexRunnerSession(cfg.runner_socket, spawn, cwd=work, work_dir=work,
                                  developer_instructions=instructions(), thread_extra=THREAD_EXTRA, stop_wait=3.0)
