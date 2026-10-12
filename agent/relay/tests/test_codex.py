"""Codex（ChatGPT・段 3-5）の試験: 金型（実物の app-server の行）・承認の答え・締め付けの確かめ・会話の係・見張り。

金型の取り方（2026-10-12・PO の PC の codex-cli 0.153.4・ChatGPT ログイン・gpt-5.6-sol・agent/ の写しで・
RK_MODE=relay で本番には届かない形。1 本目だけ rk whoami が手元の設定で本番を読んだ＝読むだけ）:
  codex -c …（relay.codex_config の締め付け）app-server に initialize → account/read → model/list → config/read →
  experimentalFeature/list → thread/start → turn/start を送り、サーバーからの行をそのまま記録。
  codex_sql_list   「rk sql --list を実行して…数字だけ」（rk の包みが無い → python -m rk で成功）
  codex_question   「どの拠点か選択肢で 1 問だけ聞いて」（item/tool/requestUserInput）→「python で 1〜10 の合計」
  codex_raw_http   「python -c で urllib を使い http://127.0.0.1:9/rk/apply に POST」（見張りの試験用・失敗する宛先）
  codex_compute    「python で 2 の 10 乗」（ふつうの計算＝見張りは拾わない）
伏せたもの: メールアドレス・利用者名入りのパス（/srv/agent・/home/agent に置換）・コマンドの出力（aggregatedOutput・
outputDelta）・config/read（確かめる項目だけ残す）・機能の一覧（名前と on/off だけ）・installationId など。
"""

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from relaykit import FIXTURES
from relay import codex_config as cx
from relay import events, models
from relay.codex_normalize import CodexNormalizer, text_choices
from relay.codex_session import CodexSession, StartRefused, codex_env
from relay.watch import Watcher, classify

HERE = Path(__file__).resolve().parent
CODEX_FIXTURES = ["codex_sql_list", "codex_question", "codex_raw_http", "codex_compute"]


def _feed(name):
    n = CodexNormalizer(clock=lambda: 0.0)
    out = []
    for ln in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            out += n.feed(json.loads(ln))
    return n, out


# ---------------- 金型 ----------------
@pytest.mark.parametrize("name", CODEX_FIXTURES)
def test_golden(name):
    _, out = _feed(name)
    want = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    assert out == want
    for e in out:
        if not events.is_internal(e):
            assert events.validate(e) == [], e


def test_fixtures_have_no_personal_data():
    for name in CODEX_FIXTURES:
        text = (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").lower()
        for bad in ("imaizumi", "lineworks", "skyblue", "72.60.", "rakusuke-agent@", "installationid", "sk-"):
            assert bad not in text, (name, bad)


def test_sql_list_turn_shape():
    n, out = _feed("codex_sql_list")
    labels = [e["label"] for e in out if e["type"] == "step.start"]
    assert labels == ["やり方を確かめています…", "つながりを確かめています…", "つながりを確かめています…"]
    assert [e["ok"] for e in out if e["type"] == "step.end"] == [True, False, True]
    assert out[-2]["type"] == "turn.end" and out[-2]["ok"] and out[-2]["secs"] == 46.4
    assert out[-1] == events.status("idle")
    assert [e["text"] for e in out if e["type"] == "message"][-1] == "31"
    assert n.turn_id == 1


def test_native_question_card():
    n, out = _feed("codex_question")
    q = next(e for e in out if e["type"] == "question")
    assert q["question_id"] == "cq_0"
    assert q["questions"][0]["header"] == "対象の拠点"
    assert q["questions"][0]["multiSelect"] is False
    assert len(q["questions"][0]["options"]) >= 2
    assert out[out.index(q) + 1] == events.status("waiting_answer")
    assert n.questions["cq_0"]["ids"] == {q["questions"][0]["question"]: "office_scope"}
    assert "source" not in q  # 本物の問い（文から拾ったものではない）


def test_no_tool_names_or_paths_on_screen():
    for name in CODEX_FIXTURES:
        _, out = _feed(name)
        for e in out:
            if events.is_internal(e):
                continue
            blob = json.dumps({k: v for k, v in e.items() if k != "text"}, ensure_ascii=False)
            for bad in ("/srv/agent", "powershell", "python -m", "urllib", "SKILL.md"):
                assert bad not in blob, (name, e)


# ---------------- 文の選択肢（承認にはならない） ----------------
def test_text_choices():
    c = text_choices("どちらにしますか？\n1. すべての拠点（全部を合わせて）\n2. 稲毛\n3. 都賀\n")
    assert c["question"] == "どちらにしますか？"
    assert [o["label"] for o in c["options"]] == ["すべての拠点", "稲毛", "都賀"]
    assert c["options"][0]["description"] == "全部を合わせて"
    assert text_choices("結果です。\n1. 10 件\n2. 12 件") is None          # 問いかけでない一覧
    assert text_choices("どれにしますか？\n1. だけ") is None                  # 1 個
    assert text_choices("どれ？\n1. a\n2. b\n3. c\n4. d\n5. e") is None  # 5 択以上はボタンにしない（文のまま）


def test_text_choice_card_is_emitted_only_without_native_question():
    n = CodexNormalizer(clock=lambda: 0.0)
    n.feed({"method": "turn/started", "params": {"turn": {"id": "t1"}}})
    n.feed({"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "m", "phase": "final_answer",
                                                            "text": "どうしますか？\n1. この案で反映する\n2. やめる"}}})
    out = n.feed({"method": "turn/completed", "params": {"turn": {"id": "t1", "status": "completed"}}})
    q = next(e for e in out if e["type"] == "question")
    assert q["source"] == "text" and q["question_id"] == "txt_1"
    assert out[-1] == events.status("waiting_answer")


def test_text_choice_answer_is_only_a_message_never_an_approval(tmp_path):
    s = CodexSession(["x"], str(tmp_path), {}, work_dir=str(tmp_path))
    s.thread_id = "th"
    s.norm.questions["txt_1"] = {"kind": "text", "question": "どうしますか？"}
    sent = []

    async def fake_write(obj):
        sent.append(obj)

    s._write = fake_write  # type: ignore[method-assign]
    asyncio.run(s.answer("txt_1", {"どうしますか？": "この案で反映する"}))
    assert len(sent) == 1 and sent[0]["method"] == "turn/start"
    assert sent[0]["params"]["input"] == [{"type": "text", "text": "（選択肢から）この案で反映する"}]
    assert "ap_" not in json.dumps(sent, ensure_ascii=False)
    with pytest.raises(KeyError):
        asyncio.run(s.answer("txt_1", "もう一度"))


# ---------------- 「(Recommended)」は「（おすすめ）」で見せ、答えは元の名前で返す ----------------
def test_recommended_label_is_shown_in_japanese_and_answer_maps_back(tmp_path):
    s = CodexSession(["x"], str(tmp_path), {}, work_dir=str(tmp_path))
    s.thread_id = "th"
    out = s.norm.feed({"id": 7, "method": "item/tool/requestUserInput", "params": {"questions": [
        {"id": "pick", "header": "案", "question": "どれにしますか？", "options": [
            {"label": "この案で反映する (Recommended)", "description": ""},
            {"label": "やめる", "description": ""}]}]}})
    q = next(e for e in out if e["type"] == "question")
    assert [o["label"] for o in q["questions"][0]["options"]] == ["この案で反映する（おすすめ）", "やめる"]
    assert "Recommended" not in json.dumps(q, ensure_ascii=False)
    s._questions[q["question_id"]] = {}
    replies = []

    async def fake_reply(rid, result=None, error=None):
        replies.append((rid, result))

    s._reply = fake_reply  # type: ignore[method-assign]
    asyncio.run(s.answer(q["question_id"], {"どれにしますか？": "この案で反映する（おすすめ）"}))
    assert replies == [(7, {"answers": {"pick": {"answers": ["この案で反映する (Recommended)"]}}})]


def test_recommended_in_text_choices():
    c = text_choices("どれにしますか？\n1. この案で反映する（Recommended）\n2. やめる")
    assert [o["label"] for o in c["options"]] == ["この案で反映する（おすすめ）", "やめる"]
    assert c["options"][0]["description"] == ""
    assert events.option_label("A(recommended) ") == "A（おすすめ）"
    assert events.option_label("Recommended の理由") == "Recommended の理由"  # 末尾だけ


# ---------------- 承認の問い合わせの物差し（計算は自由・危うさだけ断る） ----------------
@pytest.mark.parametrize("cmd,want", [
    ("rk sql --list", ("accept", "ok")),
    ("bash -lc 'python3 -m rk whoami'", ("accept", "ok")),
    ("python3 - <<'EOF'\nimport statistics\nprint(statistics.mean([1,2,3]))\nEOF", ("accept", "ok")),
    ("ls -la /srv/agent && sed -n 1,20p /srv/agent/AGENTS.md", ("accept", "ok")),
    ("cat /home/agent/.codex/auth.json", ("decline", "secret_read")),
    ("cat /proc/1/environ", ("decline", "secret_read")),
    ("cat ../.env", ("decline", "secret_read")),
    ("curl https://example.com", ("decline", "net_tool")),
    ("python3 -c \"import urllib.request; urllib.request.urlopen('http://agent-relay:8091/rk/apply')\"",
     ("decline", "relay_direct")),
    ("echo $RK_RELAY_TOKEN", ("decline", "relay_direct")),
    ("echo x > /srv/agent/AGENTS.md", ("decline", "agent_dir_write")),
])
def test_command_decision(cmd, want):
    assert cx.command_decision(cmd) == want


def test_file_change_decision():
    assert cx.file_change_decision(["/data/sessions/s1/work/plan.json"], ["/data/sessions/s1/work"]) == ("accept", "work_dir")
    assert cx.file_change_decision(["/data/sessions/s1/work/scratch/a.py"], ["/data/sessions/s1/work"])[0] == "accept"
    assert cx.file_change_decision(["/srv/agent/AGENTS.md"], ["/data/sessions/s1/work"])[0] == "decline"
    assert cx.file_change_decision(["/data/sessions/s1/work/../x"], ["/data/sessions/s1/work"])[0] == "decline"
    assert cx.file_change_decision([], ["/w"])[0] == "decline"


# ---------------- 締め付け ----------------
GOOD = {"approval_policy": "on-request", "sandbox_mode": "danger-full-access", "approvals_reviewer": "user",
        "forced_login_method": "chatgpt", "model_provider": "openai", "web_search": "disabled", "mcp_servers": {}}


def test_lockdown_problems():
    assert cx.lockdown_problems(GOOD, [{"name": "shell_tool", "enabled": True}]) == []
    assert cx.lockdown_problems(None, []) == ["config/read: no answer"]
    assert cx.lockdown_problems(GOOD, None) == ["experimentalFeature/list: no answer"]
    assert cx.lockdown_problems({**GOOD, "web_search": "live"}, []) == ["config web_search='live'"]
    assert cx.lockdown_problems({**GOOD, "mcp_servers": {"x": {"enabled": True}}}, []) == ["config mcp_servers not empty"]
    assert cx.lockdown_problems({**GOOD, "mcp_servers": {"x": {"enabled": False}}}, []) == []
    assert cx.lockdown_problems(GOOD, [{"name": "guardian_approval", "enabled": True}]) == ["feature guardian_approval on"]


def test_real_capture_saw_lockdown_and_flagged_local_mcp():
    """実物の config/read（手元の PC は MCP サーバーが入っている）→ 確かめが「効いていない」と言う（閉じる側に倒れる）。"""
    lines = [json.loads(x) for x in (FIXTURES / "codex_sql_list.jsonl").read_text(encoding="utf-8").splitlines()]
    cfg = next(m["result"]["config"] for m in lines if isinstance(m.get("result"), dict) and "config" in m["result"])
    feats = next(m["result"]["data"] for m in lines if isinstance(m.get("result"), dict)
                 and m["result"].get("data") and "enabled" in m["result"]["data"][0])
    probs = cx.lockdown_problems(cfg, feats)
    assert probs == ["config mcp_servers not empty"]  # ほかの項目（承認・sandbox・Web 検索・機能）は効いていた


def test_config_args():
    args = cx.codex_argv("codex")
    assert args[0] == "codex" and args[-1] == "app-server"
    joined = " ".join(args)
    for want in ('forced_login_method="chatgpt"', 'web_search="disabled"', 'approvals_reviewer="user"',
                 "features.guardian_approval=false", "features.apps=false", "features.plugins=false",
                 "features.multi_agent=false", 'sandbox_mode="danger-full-access"', "mcp_servers={}",
                 "shell_environment_policy.ignore_default_excludes=true"):
        assert want in joined, want
    assert "model=" not in joined.replace("model_provider", "")  # モデルは thread/start で渡す


def test_rules_file_and_execpolicy():
    text = cx.rules_text()
    assert 'decision="allow"' in text and 'decision="forbidden"' in text and '"curl"' in text
    assert 'decision="prompt"' not in text  # 計算は自由（PO 2026-10-12 改め）
    codex = shutil.which("codex")
    if not codex:
        pytest.skip("codex が無い（金型だけで確かめる）")
    rules = str(Path(cx.__file__).resolve().parents[1] / "deploy" / "runner" / cx.RULES_NAME)
    def decision(*cmd):
        r = subprocess.run([codex, "execpolicy", "check", "-r", rules, *cmd], capture_output=True, text=True,
                           timeout=60, shell=sys.platform == "win32")
        return json.loads(r.stdout).get("decision")
    assert decision("rk", "sql", "--list") == "allow"
    assert decision("curl", "http://agent-relay:8091/") == "forbidden"
    assert decision("python3", "-c", "print(1)") is None


def test_codex_env(tmp_path):
    env = codex_env(home="/home/agent", cwd="/srv/agent", relay_url="http://r:8091", rk_token="t" * 40,
                    work_dir="/data/sessions/s/work", https_proxy="http://p:3128")
    assert env["CODEX_HOME"] == "/home/agent/.codex"
    assert env["PATH"].startswith("/srv/agent/bin:")
    assert env["TMPDIR"] == "/data/sessions/s/work/scratch"
    assert not any(k.startswith("OPENAI_") or k == "CODEX_API_KEY" for k in env)


# ---------------- 会話の係（偽の app-server を本物のプロセスで） ----------------
def _session(tmp_path, fixture, mode="ok"):
    received = tmp_path / "received.jsonl"
    argv = [sys.executable, str(HERE / "fake_codex.py"), str(FIXTURES / f"{fixture}.jsonl"), str(received), mode]
    s = CodexSession(argv, str(tmp_path), {"PATH": "", "SYSTEMROOT": "C:\\Windows"}, work_dir=str(tmp_path / "work"),
                     rpc_timeout=10, stop_wait=3)
    return s, received


async def _until(it, kind, limit=200):
    got = []
    for _ in range(limit):
        e = await asyncio.wait_for(it.__anext__(), 10)
        got.append(e)
        if e["type"] == kind:
            return got
    raise AssertionError(f"{kind} が来ない")


def test_session_flow_with_native_question(tmp_path):
    s, received = _session(tmp_path, "codex_question")

    async def go():
        await s.start()
        it = s.events().__aiter__()
        meta = await asyncio.wait_for(it.__anext__(), 10)
        await s.send("拠点を選んで")
        got = await _until(it, "question")
        q = got[-1]
        assert s.waiting_question
        await s.answer(q["question_id"], {q["questions"][0]["question"]: "稲毛"})
        got += await _until(it, "turn.end")
        assert not s.waiting_question
        await s.send("合計は？")
        got += await _until(it, "turn.end")
        await s.close()
        return meta, got

    meta, got = asyncio.run(go())
    assert meta["type"] == "_meta" and meta["provider"] == "codex" and meta["model"] == "gpt-5.6-sol"
    assert meta["model_display"] == "Sol 5.6" and meta["api_key_source"] == "none"
    assert meta["cli_session_id"] == "01a12f21-903f-7f83-8d61-c8e063ad8bbb"
    assert [e["text"] for e in got if e["type"] == "message"][-1] == "55"
    sent = [json.loads(x) for x in received.read_text(encoding="utf-8").splitlines()]
    methods = [m.get("method") for m in sent]
    assert methods[:6] == ["initialize", "initialized", "account/read", "model/list", "config/read",
                           "experimentalFeature/list"]
    start = next(m for m in sent if m.get("method") == "thread/start")["params"]
    assert start["sandbox"] == "danger-full-access" and start["approvalPolicy"] == "on-request"
    assert start["approvalsReviewer"] == "user" and start["model"] == "gpt-5.6-sol"
    assert "rk plan submit" in start["developerInstructions"]
    ans = next(m for m in sent if m.get("id") == 0 and "result" in m)
    assert ans["result"] == {"answers": {"office_scope": {"answers": ["稲毛"]}}}
    turns = [m for m in sent if m.get("method") == "turn/start"]
    assert len(turns) == 2 and all(t["params"]["model"] == "gpt-5.6-sol" for t in turns)


@pytest.mark.parametrize("mode,code", [("no_login", "login"), ("api_key", "auth_source"), ("lockdown_bad", "lockdown")])
def test_session_refuses_to_start(tmp_path, mode, code):
    s, received = _session(tmp_path, "codex_sql_list", mode)

    async def go():
        with pytest.raises(StartRefused) as ei:
            await s.start()
        return ei.value

    err = asyncio.run(go())
    assert err.event["code"] == code and err.event["retry"] is False
    sent = [json.loads(x).get("method") for x in received.read_text(encoding="utf-8").splitlines()]
    assert "thread/start" not in sent


def test_session_answers_approvals_and_audits(tmp_path):
    s, received = _session(tmp_path, "codex_compute", "approvals")

    async def go():
        await s.start()
        it = s.events().__aiter__()
        await s.send("計算して")
        got = await _until(it, "turn.end")
        await s.close()
        return got

    got = asyncio.run(go())
    sent = [json.loads(x) for x in received.read_text(encoding="utf-8").splitlines()]
    answers = {m["id"]: m["result"]["decision"] for m in sent if m.get("id") in (900, 901, 902) and "result" in m}
    assert answers == {900: "decline", 901: "accept", 902: "accept"}
    audits = [e for e in got if e["type"] == "_audit"]
    assert [(a["decision"], a["why"]) for a in audits] == [("decline", "secret_read"), ("accept", "ok"), ("accept", "ok")]


def test_session_falls_back_to_lower_sol(tmp_path):
    s, received = _session(tmp_path, "codex_compute", "unsupported_once")

    async def go():
        await s.start()
        it = s.events().__aiter__()
        await asyncio.wait_for(it.__anext__(), 10)  # _meta
        await s.send("計算して")
        got = await _until(it, "turn.end")
        await s.close()
        return got

    got = asyncio.run(go())
    metas = [e for e in got if e["type"] == "_meta"]
    assert metas and metas[0]["model"] == "gpt-5.5-sol"
    assert metas[0]["model_fallbacks"] == [{"from": "gpt-5.6-sol", "to": "gpt-5.5-sol", "reason": "unsupported_for_account"}]
    assert not any(e["type"] == "error" for e in got)
    sent = [json.loads(x) for x in received.read_text(encoding="utf-8").splitlines()]
    assert [m["params"]["model"] for m in sent if m.get("method") == "turn/start"] == ["gpt-5.6-sol", "gpt-5.5-sol"]


def test_session_stop_interrupts(tmp_path):
    s, received = _session(tmp_path, "codex_compute", "hang")

    async def go():
        await s.start()
        it = s.events().__aiter__()
        await s.send("計算して")
        await _until(it, "step.end")
        await s.stop()
        got = await _until(it, "turn.end")
        await s.close()
        return got

    got = asyncio.run(go())
    assert got[-1]["reason"] == "interrupted"
    sent = [json.loads(x) for x in received.read_text(encoding="utf-8").splitlines()]
    assert any(m.get("method") == "turn/interrupt" for m in sent)


# ---------------- 見張り ----------------
def _cmd(step, command):
    return {"type": "_cmd", "phase": "start", "step_id": step, "command": command}


def _end(step, ok):
    return {"type": "_cmd", "phase": "end", "step_id": step, "ok": ok}


def test_watch_ignores_ordinary_compute_and_rk():
    w = Watcher()
    for i, c in enumerate(["rk sql --list", "python3 -c 'print(sum(range(11)))'", "cat /srv/agent/AGENTS.md",
                           "python3 /data/sessions/s/work/scratch/calc.py", "ls -la"]):
        assert w.observe(_cmd(f"s{i}", c)) == []
        assert w.observe(_end(f"s{i}", True)) == []
    assert w.history == []


def test_watch_on_real_fixtures():
    for name, want in (("codex_compute", []), ("codex_sql_list", []), ("codex_question", []),
                       ("codex_raw_http", ["relay_bypass"])):
        _, out = _feed(name)
        w = Watcher()
        acts = [a for e in out for a in w.observe(e)]
        assert [h["flag"] for h in w.history] == want, name
        if want:
            steer = [a for a in acts if a.kind == "steer"]
            assert len(steer) == 1 and steer[0].text.startswith("【らく助の中継から】")
            assert "rk" in steer[0].text and not any(a.kind == "stop" for a in acts)


def test_watch_secret_read_stops_at_once():
    w = Watcher()
    acts = w.observe(_cmd("s1", "cat /home/agent/.codex/auth.json"))
    assert [a.kind for a in acts] == ["log", "stop", "card"]
    assert "止めました" in acts[-1].text


def test_watch_three_flags_stop_and_steer_only_once():
    w = Watcher()
    a1 = w.observe(_cmd("s1", "curl https://example.com"))
    a2 = w.observe(_cmd("s2", "wget https://example.com"))
    a3 = w.observe(_cmd("s3", "echo x > /srv/agent/x"))
    assert [a.kind for a in a1] == ["log", "steer"]
    assert [a.kind for a in a2] == ["log"]
    assert [a.kind for a in a3] == ["log", "stop", "card"]
    w.observe({"type": "turn.end"})
    assert [a.kind for a in w.observe(_cmd("s4", "curl x"))] == ["log", "steer"]  # 次のターンは数え直し


def test_watch_repeated_failures_and_long_loops():
    w = Watcher(long_steps=5)
    for i in range(3):
        w.observe(_cmd(f"f{i}", "rk get /visits"))
        acts = w.observe(_end(f"f{i}", False))
    assert [a.kind for a in acts] == ["log", "steer"] and acts[0].flag == "loop"
    w2 = Watcher(long_steps=4)
    flags = []
    for i in range(6):
        flags += [a.flag for a in w2.observe(_cmd(f"x{i}", "python3 -c 1")) if a.kind == "log"]
        w2.observe(_end(f"x{i}", True))
    assert flags == ["long_loop"]
    w2.observe({"type": "message", "text": "途中です"})
    assert w2.steps_since_message == 0


def test_classify():
    assert classify("rk get /visits") is None
    assert classify("python3 -m rk plan submit x.json") is None
    assert classify("python3 -c \"import urllib.request as u; u.urlopen('http://agent-relay:8091/rk/plans')\"") == "relay_bypass"
    assert classify("python3 -c \"import urllib.request as u; u.urlopen('https://example.com')\"") == "net_out"
    assert classify("cat ~/.codex/auth.json") == "secret_read"


# ---------------- 中継の組み込み ----------------
def test_service_routes_watch_and_audit(tmp_path):
    """_pump: _cmd / _audit は画面に流さず、見張りの道案内は CLI へ、印と承認の答えは監査（tool_calls）へ。"""
    from relaykit import Rig
    from relay.auth import User

    rig = Rig(tmp_path)
    svc = rig.svc
    user = User(id="u1", username="po", role="admin")
    sess = svc.create_session(user)
    assert sess["provider"] == "codex"  # 既定は ChatGPT（PO 2026-10-12）
    assert svc.create_session(user, provider="claude")["provider"] == "claude"

    class FakeRunner:
        norm = CodexNormalizer()
        busy = False
        waiting_question = False

        def __init__(self):
            self.sent, self.stopped = [], False
            self.q: asyncio.Queue = asyncio.Queue()

        async def send(self, text):
            self.sent.append(text)

        async def stop(self):
            self.stopped = True

        async def events(self):
            while True:
                e = await self.q.get()
                if e is None:
                    return
                yield e

    from relay.service import Live
    r = FakeRunner()
    live = Live(runner=r, token="t")  # type: ignore[arg-type]

    async def go():
        for e in [{"type": "_meta", "provider": "codex", "cli_session_id": "th", "model": "gpt-5.6-sol",
                   "plan_type": "pro", "model_fallbacks": []},
                  {"type": "_audit", "kind": "command", "target": "cat ~/.codex/auth.json", "decision": "decline",
                   "why": "secret_read"},
                  _cmd("s1", "curl https://example.com"), {"type": "message", "text": "こんにちは"}, None]:
            r.q.put_nowait(e)
        await svc._pump(sess["id"], live)
        await asyncio.sleep(0)

    asyncio.run(go())
    evs = svc.store.list_events(sess["id"])
    assert [e["type"] for e in evs] == ["message"]
    assert r.sent and r.sent[0].startswith("【らく助の中継から】")
    calls = svc.store.list_tool_calls(sess["id"])
    kinds = sorted(c["kind"] for c in calls)
    assert kinds == ["cx-approval", "watch"]
    assert svc.engine_info()["login"]["codex"]["state"] == "ok"
    assert svc.engine_info()["default_provider"] == "codex"
    assert "token" not in json.dumps(svc.engine_info()).lower()


def test_display_name_for_sol():
    assert models.display_name("gpt-5.6-sol") == "Sol 5.6"


def test_claude_commands_reach_the_watcher_when_enabled():
    from relay.normalize import ClaudeNormalizer
    n = ClaudeNormalizer(check_model=False, clock=lambda: 0.0, emit_commands=True)
    out = n.feed({"type": "assistant", "message": {"id": "m1", "content": [
        {"type": "tool_use", "id": "toolu_0123456789abcdef", "name": "Bash",
         "input": {"command": "cat /home/agent/.claude/.credentials.json"}}]}})
    cmd = next(e for e in out if e["type"] == "_cmd")
    w = Watcher()
    assert [a.kind for a in w.observe(cmd)] == ["log", "stop", "card"]
    out = n.feed({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_0123456789abcdef"}]}})
    assert out[-1] == {"type": "_cmd", "phase": "end", "step_id": cmd["step_id"], "ok": True}


def test_runner_session_attrs_do_not_shadow_codex_protocol_methods():
    """箱の runner 経由（本番・staging の道）で、RunnerSession の属性が JSON-RPC の関数を隠さないこと。

    2026-10-12 staging: RunnerSession の書き口 ``self._w`` が _CodexProtocol の ``_w`` を隠し、
    最初の initialize で TypeError（500）になった。
    """
    from relay.codex_session import CodexRunnerSession, _CodexProtocol

    proto = {n for n, v in vars(_CodexProtocol).items() if callable(v) and not n.startswith("__")}
    s = CodexRunnerSession.__new__(CodexRunnerSession)
    from relay.runner import RunnerSession
    RunnerSession.__init__(s, "/nonexistent.sock", {}, normalizer=None)  # type: ignore[arg-type]
    assert not (proto & set(vars(s))), sorted(proto & set(vars(s)))
