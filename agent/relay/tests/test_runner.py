"""CLI の会話の係: 道具の許可・質問の答え・起こし方・偽の CLI（本物のサブプロセス）との往復。"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from relaykit import FIXTURES
from relay.normalize import ClaudeNormalizer
from relay.runner import (ClaudeSession, FakeSession, answer_reply, claude_env, control_reply, isolation_settings,
                          write_session_files)

HERE = Path(__file__).parent


def _fixture(name):
    return [json.loads(x) for x in (FIXTURES / name).read_text(encoding="utf-8").splitlines() if x.strip()]


def _requests():
    return [m for m in _fixture("claude_ask_then_tool.jsonl") if m.get("type") == "control_request"]


def test_control_reply_matches_what_real_cli_accepted():
    """実物の CLI に送って通った返事（*.sent.jsonl）と同じ形を作る。"""
    sent = _fixture("claude_ask_then_tool.sent.jsonl")
    ask, ps = _requests()
    assert control_reply(ask) is None  # 質問は画面の答えを待つ
    assert control_reply(ps) == sent[3]
    q = ask["request"]["input"]["questions"][0]["question"]
    assert answer_reply(ask, {q: "青"}) == sent[1]
    assert answer_reply(ask, "青") == sent[1]  # 1 問だけなら答えだけでよい


def test_control_reply_denies_web_and_unknown():
    for name, word in (("WebFetch", "Web を見られません"), ("WebSearch", "検索できません"), ("mcp__x__y", "使えません")):
        r = control_reply({"type": "control_request", "request_id": "r1",
                           "request": {"subtype": "can_use_tool", "tool_name": name, "input": {}}})
        assert r["response"]["response"]["behavior"] == "deny" and word in r["response"]["response"]["message"]
    r = control_reply({"type": "control_request", "request_id": "r2", "request": {"subtype": "hook_callback"}})
    assert r["response"]["subtype"] == "error"


def test_answer_reply_rejects_unknown_question_and_joins_multi():
    ask = _requests()[0]
    with pytest.raises(ValueError):
        answer_reply(ask, {"ほかの質問": "x"})
    q = ask["request"]["input"]["questions"][0]["question"]
    assert answer_reply(ask, {q: ["赤", "青"]})["response"]["response"]["updatedInput"]["answers"][q] == "赤, 青"


def test_claude_recommended_label_shown_in_japanese_and_answer_maps_back():
    ask = {"type": "control_request", "request_id": "q1", "request": {
        "subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": [
            {"question": "どれにしますか？", "header": "案", "multiSelect": False,
             "options": [{"label": "この案で反映する (Recommended)", "description": ""},
                         {"label": "やめる", "description": ""}]}]}}}
    ev = next(e for e in ClaudeNormalizer().feed(ask) if e["type"] == "question")
    assert [o["label"] for o in ev["questions"][0]["options"]] == ["この案で反映する（おすすめ）", "やめる"]
    r = answer_reply(ask, "この案で反映する（おすすめ）")
    assert r["response"]["response"]["updatedInput"]["answers"] == {"どれにしますか？": "この案で反映する (Recommended)"}
    assert answer_reply(ask, "やめる")["response"]["response"]["updatedInput"]["answers"] == {"どれにしますか？": "やめる"}
    free = answer_reply(ask, "自由に書いた答え")["response"]["response"]["updatedInput"]
    assert free["answers"]["どれにしますか？"] == "自由に書いた答え"
    multi = answer_reply(ask, {"どれにしますか？": ["この案で反映する（おすすめ）", "やめる"]})["response"]["response"]["updatedInput"]
    assert multi["answers"]["どれにしますか？"] == "この案で反映する (Recommended), やめる"


def test_isolation_and_env():
    iso = isolation_settings("/home/agent", "/srv/agent")
    assert iso["autoMemoryEnabled"] is False and iso["forceLoginMethod"] == "claudeai"
    assert iso["env"]["ANTHROPIC_BASE_URL"] == "" and iso["apiKeyHelper"] == ""
    assert "/home/agent/.claude/CLAUDE.md" in iso["claudeMdExcludes"]
    assert "/srv/CLAUDE.md" in iso["claudeMdExcludes"] and "/CLAUDE.md" in iso["claudeMdExcludes"]
    assert "/srv/agent/CLAUDE.md" not in iso["claudeMdExcludes"]  # 自分の CLAUDE.md（@AGENTS.md）は効かせる
    env = claude_env(home="/home/agent", cwd="/srv/agent", relay_url="http://carelink-agent:8091", rk_token="t",
                     work_dir="/data/sessions/x/work", https_proxy="http://carelink-agent:3128")
    assert env["RK_MODE"] == "relay" and env["RK_RELAY_TOKEN"] == "t"
    assert not any(k.startswith("ANTHROPIC") or k in ("RK_PASSWORD", "RK_IDENTIFIER", "DATABASE_URL") for k in env)


def test_fake_session_flow():
    async def go():
        s = FakeSession(_fixture("claude_ask_then_tool.jsonl"), ClaudeNormalizer(check_model=False, clock=lambda: 0.0))
        await s.start()
        got = []

        async def collect():
            async for e in s.events():
                got.append(e)
        task = asyncio.create_task(collect())
        await s.send("色を聞いて")
        for _ in range(100):
            await asyncio.sleep(0)
            if any(e["type"] == "question" for e in got):
                break
        assert s.waiting_question and s.busy
        qid = next(e for e in got if e["type"] == "question")["question_id"]
        await s.answer(qid, "青")
        for _ in range(100):
            await asyncio.sleep(0)
        assert not s.busy
        await s.send("数えて")
        for _ in range(100):
            await asyncio.sleep(0)
        await s.close()
        await task
        return s, got
    s, got = asyncio.run(go())
    assert [e["type"] for e in got].count("turn.end") == 2
    assert s.sent[1]["response"]["response"]["updatedInput"]["answers"] == {"どちらの色が好きですか？": "青"}


def test_claude_session_with_real_subprocess(tmp_path):
    """ClaudeSession の pipe の扱いを、偽の claude（本物のサブプロセス）で確かめる。"""
    received = tmp_path / "received.jsonl"
    argv = [sys.executable, str(HERE / "fake_claude.py"), str(FIXTURES / "claude_ask_then_tool.jsonl"), str(received)]

    async def go():
        import os
        s = ClaudeSession(argv, str(tmp_path), {**os.environ, "PYTHONIOENCODING": "utf-8"}, ClaudeNormalizer(check_model=False), stop_wait=5)
        await s.start()
        got = []

        async def collect():
            async for e in s.events():
                got.append(e)
                if e["type"] == "question":
                    await s.answer(e["question_id"], "赤")
                if e["type"] == "turn.end" and sum(1 for x in got if x["type"] == "turn.end") == 1:
                    await s.send("数えて")
        task = asyncio.create_task(collect())
        await s.send("色を聞いて")
        for _ in range(200):
            await asyncio.sleep(0.05)
            if sum(1 for x in got if x["type"] == "turn.end") == 2:
                break
        await s.close()
        await asyncio.wait_for(task, 10)
        return got
    got = asyncio.run(go())
    assert [e["type"] for e in got].count("turn.end") == 2
    assert got[-1]["type"] == "_exit"
    lines = [json.loads(x) for x in received.read_text(encoding="utf-8").splitlines()]
    assert [x["type"] for x in lines] == ["user", "control_response", "user", "control_response"]
    assert lines[1]["response"]["response"]["updatedInput"]["answers"] == {"どちらの色が好きですか？": "赤"}
    assert lines[3]["response"]["response"]["behavior"] == "allow"


def test_write_session_files(tmp_path):
    files = write_session_files(tmp_path / "s1", "/home/agent", "/srv/agent")
    assert json.loads(files["mcp"].read_text()) == {"mcpServers": {}}
    assert "rk plan submit" in files["prompt"].read_text(encoding="utf-8")
    assert (tmp_path / "s1" / "work").is_dir()
