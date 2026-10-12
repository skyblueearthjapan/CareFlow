"""偽のモデル（試験用）— 認証情報の隔離の試験で、本物の CLI に「モデルの返事」を返す小さな HTTP サーバー。

Anthropic Messages（claude）と OpenAI Responses（codex）の両方の SSE を話す。最初の要求には
「道具を 1 つ呼ぶ」返事（FAKE_SCENARIO の JSON: {"tool": "Bash", "input": {...}} / {"shell": "<コマンド>"}）、
道具の結果が付いた次の要求には「終わり」の返事を返し、道具の結果を FAKE_LOG（JSONL）に書く。
標準ライブラリだけ。本番では使わない。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCENARIO = json.loads(os.environ.get("FAKE_SCENARIO", "{}"))
LOG = os.environ.get("FAKE_LOG", "/tmp/fake_model.log")
_lock = threading.Lock()


def log(rec: dict) -> None:
    with _lock, open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def sse(events: list[tuple[str, dict]]) -> bytes:
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events).encode("utf-8")


def anthropic_reply(body: dict) -> bytes:
    msgs = body.get("messages") or []
    last = msgs[-1] if msgs else {}
    content = last.get("content")
    results = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_result"] if isinstance(content, list) else []
    head = [("message_start", {"type": "message_start", "message": {
        "id": "msg_fake", "type": "message", "role": "assistant", "model": body.get("model", "claude"),
        "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 1}}})]
    if results:
        text = json.dumps(results[0].get("content"), ensure_ascii=False)
        log({"kind": "tool_result", "provider": "claude", "result": text})
        blocks = [("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}}),
                  ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": "done"}}),
                  ("content_block_stop", {"type": "content_block_stop", "index": 0})]
        stop = "end_turn"
    elif body.get("tools") and SCENARIO.get("tool"):
        blocks = [("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                      "type": "tool_use", "id": "toolu_fake1", "name": SCENARIO["tool"], "input": {}}}),
                  ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
                      "type": "input_json_delta", "partial_json": json.dumps(SCENARIO["input"])}}),
                  ("content_block_stop", {"type": "content_block_stop", "index": 0})]
        stop = "tool_use"
    else:  # 道具なしの付随の要求（題名づくりなど）
        blocks = [("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}}),
                  ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": "ok"}}),
                  ("content_block_stop", {"type": "content_block_stop", "index": 0})]
        stop = "end_turn"
    tail = [("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                               "usage": {"output_tokens": 5}}),
            ("message_stop", {"type": "message_stop"})]
    return sse(head + blocks + tail)


def responses_reply(body: dict) -> bytes:
    inputs = body.get("input") or []
    outs = [i for i in inputs if isinstance(i, dict) and i.get("type") in ("function_call_output", "custom_tool_call_output")]
    created = ("response.created", {"type": "response.created", "response": {"id": "resp_fake", "object": "response",
                                                                            "status": "in_progress", "output": []}})

    def done(items: list[dict]) -> tuple[str, dict]:
        return ("response.completed", {"type": "response.completed", "response": {
            "id": "resp_fake", "object": "response", "status": "completed", "output": items,
            "usage": {"input_tokens": 5, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1,
                      "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 6}}})

    if outs:
        log({"kind": "tool_result", "provider": "codex", "result": json.dumps(outs[-1].get("output"), ensure_ascii=False)})
        msg = {"type": "message", "id": "msg_fake", "role": "assistant", "status": "completed",
               "content": [{"type": "output_text", "text": "done"}]}
        return sse([created, ("response.output_item.done", {"type": "response.output_item.done", "item": msg}), done([msg])])
    names = [t.get("name") for t in (body.get("tools") or []) if isinstance(t, dict)]
    log({"kind": "tools", "names": names, "types": [t.get("type") for t in (body.get("tools") or []) if isinstance(t, dict)]})
    if SCENARIO.get("patch"):  # apply_patch（自由形式の道具か関数のどちらでも）
        tool = next((t for t in (body.get("tools") or []) if isinstance(t, dict) and t.get("name") == "apply_patch"), None)
        if tool is None:
            log({"kind": "no_apply_patch", "names": names})
        elif tool.get("type") == "custom":
            item = {"type": "custom_tool_call", "id": "ct_fake", "call_id": "call_fake2", "name": "apply_patch",
                    "input": SCENARIO["patch"], "status": "completed"}
        else:
            item = {"type": "function_call", "id": "fc_fake", "call_id": "call_fake2", "name": "apply_patch",
                    "arguments": json.dumps({"input": SCENARIO["patch"]}), "status": "completed"}
        if tool is not None:
            return sse([created, ("response.output_item.done", {"type": "response.output_item.done", "item": item}), done([item])])
    name = "shell_command" if "shell_command" in names else ("exec_command" if "exec_command" in names else "shell")
    cmd = SCENARIO.get("shell", "id")
    args = {"shell_command": {"command": cmd}, "exec_command": {"cmd": cmd}}.get(name, {"command": ["bash", "-lc", cmd]})
    item = {"type": "function_call", "id": "fc_fake", "call_id": "call_fake1", "name": name,
            "arguments": json.dumps(args), "status": "completed"}
    return sse([created, ("response.output_item.done", {"type": "response.output_item.done", "item": item}), done([item])])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:  # noqa: D102
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, b'{"data": [], "models": []}')

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        path = self.path.split("?")[0]
        log({"kind": "req", "path": path})
        if path.endswith("/count_tokens"):
            return self._send(200, b'{"input_tokens": 10}')
        if path.endswith("/messages"):
            return self._send(200, anthropic_reply(body), "text/event-stream")
        if path.endswith("/responses"):
            return self._send(200, responses_reply(body), "text/event-stream")
        self._send(404, b"{}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18999
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
