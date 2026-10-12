"""偽の codex app-server（試験用）: 取った実物（tests/fixtures/codex_*.jsonl・サーバー → 中継の行）を JSON-RPC で返す。

使い方: python fake_codex.py <fixture.jsonl> <received.jsonl> [mode]
- 始める前の問い（initialize・account/read・model/list・config/read・experimentalFeature/list・thread/start|resume）
  には決まった答えを返す（model/list は fixture の答え）。
- turn/start を受けるたびに、fixture の次のターン（turn/started 〜 turn/completed）を流す。fixture の中の問い合わせ
  （id つき・item/tool/requestUserInput など）は、中継の答えが来るまで待つ。
- mode: ok（既定）/ no_login / api_key / lockdown_bad / unsupported_once / approvals / hang
受け取った行はすべて received に書く（試験で中継の答えを確かめる）。
"""
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
fixture, received_path = sys.argv[1], sys.argv[2]
mode = sys.argv[3] if len(sys.argv) > 3 else "ok"
lines = [json.loads(x) for x in open(fixture, encoding="utf-8") if x.strip()]
models = next((m["result"] for m in lines if isinstance(m.get("result"), dict) and m["result"].get("data")
               and "displayName" in m["result"]["data"][0]), {"data": []})
if mode == "unsupported_once":  # 一つ下の Sol があるアカウント
    models = {"data": models["data"] + [{"id": "gpt-5.5-sol", "model": "gpt-5.5-sol", "displayName": "GPT-5.5-Sol",
                                         "hidden": False, "isDefault": False}], "nextCursor": None}
turns, cur, on = [], [], False
for m in lines:
    if m.get("method") == "turn/started":
        on, cur = True, []
    if on and not ("id" in m and "method" not in m):
        cur.append(m)
    if m.get("method") == "turn/completed":
        turns.append(cur)
        on = False
received = open(received_path, "w", encoding="utf-8")
THREAD = "01a12f21-903f-7f83-8d61-c8e063ad8bbb"
GOOD_CFG = {"approval_policy": "on-request", "sandbox_mode": "danger-full-access", "approvals_reviewer": "user",
            "forced_login_method": "chatgpt", "model_provider": "openai", "web_search": "disabled", "mcp_servers": {}}


def out(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read():
    ln = sys.stdin.readline()
    if not ln:
        sys.exit(0)
    received.write(ln)
    received.flush()
    return json.loads(ln)


def wait_reply(rid):
    while True:
        m = read()
        if m.get("id") == rid and "method" not in m:
            return m
        handle(m)


turn_no = 0
unsupported_done = False


def play(turn_id):
    global turn_no
    if mode == "approvals":
        for i, cmd in enumerate(["cat /home/agent/.codex/auth.json", "rk sql --list", "python3 -c 'print(1)'"]):
            rid = 900 + i
            out({"method": "item/commandExecution/requestApproval", "id": rid,
                 "params": {"threadId": THREAD, "turnId": turn_id, "itemId": f"exec-{i}", "command": cmd,
                            "startedAtMs": 0}})
            wait_reply(rid)
    if turn_no >= len(turns):
        out({"method": "turn/completed", "params": {"threadId": THREAD, "turn": {"id": turn_id, "items": [],
                                                                                "status": "completed"}}})
        return
    t = turns[turn_no]
    turn_no += 1
    for m in t:
        if mode == "hang" and m.get("method") == "turn/completed":
            return  # 止めるまで終わらない
        out(m)
        if "id" in m and "method" in m:
            wait_reply(m["id"])


def handle(m):
    global unsupported_done
    meth, rid = m.get("method"), m.get("id")
    if rid is None or meth is None:
        return
    if meth == "initialize":
        out({"id": rid, "result": {"userAgent": "fake-codex/0.162.1"}})
    elif meth == "account/read":
        acct = None if mode == "no_login" else ({"type": "apiKey"} if mode == "api_key" else
                                                 {"type": "chatgpt", "email": "admin@example.invalid",
                                                  "planType": "pro"})
        out({"id": rid, "result": {"account": acct, "requiresOpenaiAuth": True}})
    elif meth == "model/list":
        out({"id": rid, "result": models})
    elif meth == "config/read":
        cfg = dict(GOOD_CFG, web_search="live") if mode == "lockdown_bad" else GOOD_CFG
        out({"id": rid, "result": {"config": cfg, "origins": {}}})
    elif meth == "experimentalFeature/list":
        out({"id": rid, "result": {"data": [{"name": "apps", "enabled": mode == "lockdown_bad"},
                                            {"name": "shell_tool", "enabled": True}], "nextCursor": None}})
    elif meth in ("thread/start", "thread/resume"):
        out({"id": rid, "result": {"thread": {"id": THREAD}, "approvalsReviewer": "user"}})
    elif meth == "turn/start":
        tid = f"turn-{turn_no + 1}"
        out({"id": rid, "result": {"turn": {"id": tid, "items": [], "status": "inProgress"}}})
        if mode == "unsupported_once" and not unsupported_done:
            unsupported_done = True
            out({"method": "turn/started", "params": {"threadId": THREAD, "turn": {"id": "turn-x"}}})
            out({"method": "turn/completed", "params": {"threadId": THREAD, "turn": {
                "id": "turn-x", "items": [], "status": "failed",
                "error": {"message": "The 'gpt-5.6-sol' model is not supported when using Codex with a ChatGPT account.",
                          "codexErrorInfo": "badRequest"}}}})
            return
        play(tid)
    elif meth == "turn/interrupt":
        out({"id": rid, "result": {}})
        out({"method": "turn/completed", "params": {"threadId": THREAD, "turn": {"id": m["params"]["turnId"],
                                                                                "items": [], "status": "interrupted"}}})
    else:
        out({"id": rid, "result": {}})


while True:
    handle(read())
