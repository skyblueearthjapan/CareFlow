"""stream-json → 画面用イベントの金型試験（実物: 2026-10-12 Claude Code 2.1.296 で取得・個人情報なし）。

取り方（agent/ で・PO のローカルのログイン）:
  claude -p --output-format stream-json --verbose --include-partial-messages "rk sql --list を…"   → claude_sql_list_partial
  --input-format stream-json --permission-prompt-tool stdio で AskUserQuestion → PowerShell の 2 ターン → claude_ask_then_tool
伏せたもの: フックの行（手元のメモリの文脈）・cwd/パス・道具の出力（SQL の一覧）・init の大きな一覧。
注意: 手元の init は apiKeySource=ANTHROPIC_API_KEY だった（PC の環境変数）。箱では none のはずなので伏せて none にし、
      ANTHROPIC_API_KEY のときに止まることは下の試験で確かめる。また手元は Windows なので道具は PowerShell（箱では Bash）。
"""

import json

import pytest

from relaykit import FIXTURES
from relay import events
from relay.normalize import ClaudeNormalizer
from relay.step_labels import GENERIC, label_for


def _run(name, **kw):
    n = ClaudeNormalizer(check_model=False, clock=lambda: 0.0, **kw)
    out = []
    for ln in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            out += n.feed(json.loads(ln))
    return n, out


@pytest.mark.parametrize("name", ["claude_sql_list_partial", "claude_ask_then_tool"])
def test_golden(name):
    _, out = _run(name)
    want = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    assert out == want
    for e in out:
        if not events.is_internal(e):
            assert events.validate(e) == [], e


def test_golden_shapes_are_plain_japanese():
    _, out = _run("claude_ask_then_tool")
    shown = json.dumps([e for e in out if not events.is_internal(e)], ensure_ascii=False)
    for bad in ("PowerShell", "rk sql", "python", "toolu_", "/srv/agent"):
        assert bad not in shown  # 道具名・コマンド・パスは画面に出さない
    q = next(e for e in out if e["type"] == "question")
    assert q["questions"][0]["options"][1]["label"] == "青"
    assert sum(1 for e in out if e["type"] == "turn.end") == 2


def test_turns_counted_from_init():
    n, _ = _run("claude_ask_then_tool")
    assert n.turn_id == 2
    assert n.cli_session_id == "d85dde79-9a8f-47cf-8084-8b158602fa81"


def test_model_policy_stops_non_opus_session():
    """実物は sonnet で取った → 決まり（いつも Opus）では止める。"""
    n = ClaudeNormalizer(clock=lambda: 0.0)
    first = json.loads((FIXTURES / "claude_ask_then_tool.jsonl").read_text(encoding="utf-8").splitlines()[0])
    out = n.feed(first)
    assert [e["type"] for e in out] == ["_meta", "error", "_abort"]
    assert out[1]["code"] == "model" and out[1]["retry"] is False
    ok = n.feed({**first, "model": "claude-opus-5-5"})
    assert ok[0]["model_display"] == "Opus 5.5" and ok[1]["type"] == "status"


def test_api_key_source_stops():
    n = ClaudeNormalizer(check_model=False)
    out = n.feed({"type": "system", "subtype": "init", "session_id": "x", "model": "claude-opus-5-5",
                  "apiKeySource": "ANTHROPIC_API_KEY"})
    assert [e["type"] for e in out] == ["_meta", "error", "_abort"]
    assert out[1]["code"] == "auth_source"


def _init(n):
    n.feed({"type": "system", "subtype": "init", "session_id": "s", "model": "claude-opus-5-5", "apiKeySource": "none"})


def test_thinking_without_partials_and_error_result():
    n = ClaudeNormalizer(check_model=False, clock=lambda: 0.0)
    _init(n)
    out = n.feed({"type": "assistant", "message": {"id": "m1", "content": [{"type": "thinking", "thinking": "秘密"}]}})
    assert out == [{"type": "thinking", "phase": "start"}, {"type": "thinking", "phase": "end"}]
    assert "秘密" not in json.dumps(out, ensure_ascii=False)  # 考えの中身は送らない
    out = n.feed({"type": "assistant", "message": {"id": "m2", "content": [
        {"type": "tool_use", "id": "toolu_abc123456789xyz", "name": "Bash",
         "input": {"command": "PYTHONPATH=. python -m rk level day --date 2026-10-14"}}]}})
    assert out[0]["label"] == "振り分け方を考えています…"
    out = n.feed({"type": "result", "subtype": "error_during_execution", "is_error": True, "duration_ms": 1200,
                  "result": "OAuth token has expired (401)"})
    types = [e["type"] for e in out]
    assert types == ["step.end", "steps.summary", "turn.end", "error", "status"]
    assert out[0]["ok"] is False and out[3]["code"] == "login" and out[4]["state"] == "error"


def test_thinking_partials_and_abort():
    n = ClaudeNormalizer(check_model=False)
    _init(n)
    a = n.feed({"type": "stream_event", "api_message_id": "m1",
                "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}})
    b = n.feed({"type": "stream_event", "api_message_id": "m1", "event": {"type": "content_block_stop", "index": 0}})
    c = n.feed({"type": "assistant", "message": {"id": "m1", "content": [{"type": "thinking", "thinking": "x"}]}})
    assert a == [{"type": "thinking", "phase": "start"}] and b == [{"type": "thinking", "phase": "end"}] and c == []
    out = n.feed({"type": "result", "subtype": "error_during_execution", "terminal_reason": "aborted_streaming",
                  "is_error": True})
    assert out[-1] == events.status("idle", "止めました")


def test_rate_limit_and_retry():
    n = ClaudeNormalizer(check_model=False)
    out = n.feed({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1791800000}})
    assert out[0]["state"] == "rate_limited" and "以降に続けられます" in out[0]["line"]
    assert n.feed({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}) == []
    assert n.feed({"type": "system", "subtype": "api_retry", "attempt": 2})[0]["line"].endswith("（2 回目）")


def test_subagent_lines_hidden():
    n = ClaudeNormalizer(check_model=False)
    assert n.feed({"type": "assistant", "parent_tool_use_id": "toolu_x",
                   "message": {"content": [{"type": "text", "text": "中"}]}}) == []


@pytest.mark.parametrize("cmd,label", [
    ("PYTHONPATH=. python -m rk get /visits -q week=2026-W42", "予定を見ています…"),
    ("agent/bin/rk get /staff/abc", "職員の予定を見ています…"),
    ("python -m rk sql staff_week_overrides -p monday=2026-10-12", "職員のお休みを確かめています…"),
    ("python -m rk sql visits_valid", "予定を見ています…"),
    ("python -m rk level week --week 2026-W43", "振り分け方を考えています…"),
    ("cd /srv/agent && python -m rk plan report runs/plans/x.json --a3", "案を紙にまとめています…"),
    ("python -m rk plan submit x.json", "案をまとめています…"),
    ("python -m rk apply x.json --approved ap_abcdefgh123", "反映しています…"),
    ("python -m rk verify x.json", "反映を確かめています…"),
    ("python -m rk whoami", "つながりを確かめています…"),
    ("ls -la", "資料を探しています…"),
    ("echo hi", GENERIC),
    ("python3 -c \"print(1)\"", "計算しています…"),
    ("cat .agents/skills/rakusuke-ask/SKILL.md", "やり方を確かめています…"),
])
def test_step_labels_bash(cmd, label):
    assert label_for("Bash", {"command": cmd}) == label


def test_step_labels_other_tools():
    assert label_for("Read", {"file_path": "/srv/agent/.claude/skills/rakusuke-ask/SKILL.md"}) == "やり方を確かめています…"
    assert label_for("Read", {"file_path": "/data/sessions/x/work/plan.json"}) == "資料を読んでいます…"
    assert label_for("Grep", {}) == "資料を探しています…"
    assert label_for("SomethingNew", {}) == GENERIC
