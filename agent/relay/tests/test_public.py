"""公開口: 鍵（admin だけ・旗・試験中の許可・Origin）・本人だけ・SSE・質問の答え・案の HTML。"""

import json
import time

from fastapi.testclient import TestClient

from relaykit import ADMIN, Rig, fake_auth, plan
from relay.public import create_public_app

H = {"Authorization": "Bearer tok-admin"}
H2 = {"Authorization": "Bearer tok-admin2"}
HS = {"Authorization": "Bearer tok-staff"}


def _client(rig, gate=None):
    kw = {"feature_gate": gate} if gate else {}
    return TestClient(create_public_app(rig.svc, fake_auth, sweep_seconds=3600, **kw))


def _wait(rig, sid, pred, n=200):
    for _ in range(n):
        evs = rig.store.list_events(sid)
        if pred(evs):
            return evs
        time.sleep(0.01)
    raise AssertionError([e["type"] for e in rig.store.list_events(sid)])


def test_auth_rules(tmp_path):
    rig = Rig(tmp_path)
    with _client(rig) as c:
        assert c.get("/agent-relay/health").json()["ok"]
        assert c.get("/agent-relay/sessions").status_code == 401
        assert c.get("/agent-relay/sessions", headers={"Authorization": "Bearer bad"}).status_code == 401
        assert c.get("/agent-relay/sessions", headers=HS).status_code == 403  # staff は使えない
        assert c.get("/agent-relay/sessions", headers=H).status_code == 200
        assert c.get("/agent-relay/sessions", headers=H2).status_code == 200  # 旧 manager も管理者
        r = c.post("/agent-relay/sessions", json={}, headers={**H, "Origin": "https://evil.example"})
        assert r.status_code == 403
        assert c.post("/agent-relay/sessions", json={},
                      headers={**H, "Origin": "https://carelink.kaipoke-api.net"}).status_code == 200
    rig.cfg.enabled = False
    with _client(rig) as c:
        assert c.get("/agent-relay/sessions", headers=H).status_code == 404
    rig.cfg.enabled = True
    rig.cfg.allowed_user_ids = {"someone-else"}
    with _client(rig) as c:
        assert c.get("/agent-relay/sessions", headers=H).status_code == 403

    async def off(_u):
        return False
    rig.cfg.allowed_user_ids = set()
    with _client(rig, gate=off) as c:
        assert c.get("/agent-relay/sessions", headers=H).status_code == 404


def test_sessions_are_owner_only(tmp_path):
    rig = Rig(tmp_path)
    with _client(rig) as c:
        sid = c.post("/agent-relay/sessions", json={"title": "週43"}, headers=H).json()["id"]
        assert [s["id"] for s in c.get("/agent-relay/sessions", headers=H).json()] == [sid]
        assert c.get("/agent-relay/sessions", headers=H2).json() == []
        for path in ("", "/events?follow=false"):
            assert c.get(f"/agent-relay/sessions/{sid}{path}", headers=H2).status_code == 404
        assert c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "x"}, headers=H2).status_code == 404


def test_typed_message_answers_the_waiting_question(tmp_path):
    """質問カードが答え待ちのとき、入力欄の文はそのカードへの答えになる（10-28 に止まった会話の再発防止）。"""
    rig = Rig(tmp_path)
    with _client(rig) as c:
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "色を聞いて"}, headers=H)
        evs = _wait(rig, sid, lambda es: any(e["type"] == "question" for e in es))
        q = next(e for e in evs if e["type"] == "question")
        r = c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "青がいいです"}, headers=H)
        assert r.status_code == 200
        evs = _wait(rig, sid, lambda es: any(e["type"] == "turn.end" for e in es))
        ans = next(e for e in evs if e["type"] == "question.answered")
        assert ans["question_id"] == q["question_id"] and ans["typed"] is True
        assert list(ans["answers"].values()) == ["青がいいです"]
        runner = rig.by_session[sid]
        sent = runner.sent[-1]["response"]["response"]["updatedInput"]["answers"]
        assert list(sent.values()) == ["青がいいです"]
        # カードはもう閉じている → ボタンで答えても 409
        r = c.post(f"/agent-relay/sessions/{sid}/answers", json={"question_id": q["question_id"], "choice": "赤"},
                   headers=H)
        assert r.status_code == 409


def test_conversation_question_and_sse(tmp_path):
    rig = Rig(tmp_path)
    with _client(rig) as c:
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        assert c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "色を聞いて", "device": "mobile"},
                      headers=H).status_code == 200
        evs = _wait(rig, sid, lambda es: any(e["type"] == "question" for e in es))
        q = next(e for e in evs if e["type"] == "question")
        r = c.post(f"/agent-relay/sessions/{sid}/answers", json={"question_id": q["question_id"], "choice": "青"},
                   headers=H)
        assert r.status_code == 200
        # 2 台目の端末が同じ質問に答えても先に答えた方が勝つ
        r = c.post(f"/agent-relay/sessions/{sid}/answers", json={"question_id": q["question_id"], "choice": "赤"},
                   headers=H)
        assert r.status_code == 409
        _wait(rig, sid, lambda es: any(e["type"] == "turn.end" for e in es))
        detail = c.get(f"/agent-relay/sessions/{sid}", headers=H).json()
        assert detail["resolved_model"] == "claude-sonnet-5-5"  # init の model（実物は sonnet で取った）
        assert "cli_session_id" not in detail and detail["title"] == "色を聞いて"  # CLI の会話 ID は画面に出さない
        assert rig.store.get_session(sid)["cli_session_id"]

        with c.stream("GET", f"/agent-relay/sessions/{sid}/events?after=0&follow=false", headers=H) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            assert "no-transform" in r.headers["cache-control"]
            body = "".join(r.iter_text())
        frames = [f for f in body.split("\n\n") if f.startswith("id: ")]
        data = [json.loads(f.split("data: ", 1)[1]) for f in frames]
        assert [d["seq"] for d in data] == list(range(1, len(data) + 1))
        types = [d["type"] for d in data]
        assert types[0] == "user.message" and data[0]["device"] == "mobile"
        assert "question" in types and "question.answered" in types and "turn.end" in types
        assert not any(t.startswith("_") for t in types)
        with c.stream("GET", f"/agent-relay/sessions/{sid}/events?after={len(data) - 2}&follow=false",
                      headers=H) as r:
            assert len([f for f in "".join(r.iter_text()).split("\n\n") if f.startswith("id: ")]) == 2


def test_plan_html_is_sandboxed(tmp_path):
    rig = Rig(tmp_path)
    with _client(rig) as c:
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        s = rig.store.get_session(sid)
        rig.svc.submit_plan(s, plan(timeline={"date": "2026-10-14", "rows": [
            {"name": "職員A", "blocks": [{"start": "9:30", "end": "10:20", "label": "訪問1", "changed": True}]}]}))
        r = c.get(f"/agent-relay/sessions/{sid}/plans/1/html?size=a4", headers=H)
        assert r.status_code == 200 and "<script" not in r.text and "試験の案" in r.text
        assert r.headers["content-security-policy"].startswith("sandbox")
        assert c.get(f"/agent-relay/sessions/{sid}/plans/1/html?size=a3", headers=H).status_code == 200
        assert c.get(f"/agent-relay/sessions/{sid}/plans/1/html", headers=H2).status_code == 404
        ev = [e for e in rig.store.list_events(sid) if e["type"] == "plan"][0]
        assert ev["html_urls"]["a3"].endswith("size=a3") and ev["ready"] and ev["undoable"]


def test_concurrency_limit_and_stop(tmp_path):
    rig = Rig(tmp_path)  # max_cli = 1
    with _client(rig) as c:
        a = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        b = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        c.post(f"/agent-relay/sessions/{a}/messages", json={"text": "色を聞いて"}, headers=H)
        _wait(rig, a, lambda es: any(e["type"] == "question" for e in es))
        r = c.post(f"/agent-relay/sessions/{b}/messages", json={"text": "別の相談"}, headers=H)
        assert r.status_code == 409 and "作業中" in r.json()["detail"]
        assert c.post(f"/agent-relay/sessions/{a}/stop", headers=H).status_code == 200
        assert rig.runners[0].stopped or not rig.runners[0].waiting_question


def test_rate_limit(tmp_path):
    rig = Rig(tmp_path, sends_per_hour=1, fixture=None)
    with _client(rig) as c:
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        assert c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "1"}, headers=H).status_code == 200
        assert c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "2"}, headers=H).status_code == 429
        assert c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": " "}, headers=H).status_code == 400
    assert ADMIN.id in rig.svc._sends


def test_plan_html_is_rendered_on_demand_not_stored(tmp_path):
    from relay import db
    assert not hasattr(db.PlanRow, "html_a4") and not hasattr(db.PlanRow, "html_a3")
    rig = Rig(tmp_path)
    with _client(rig) as c:
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        s = rig.store.get_session(sid)
        rig.svc.submit_plan(s, plan(title="印刷の案"))           # 時間割なし → A4 だけ
        assert "html_a4" not in rig.store.get_plan(sid, 1)
        assert "印刷の案" in c.get(f"/agent-relay/sessions/{sid}/plans/1/html", headers=H).text
        assert c.get(f"/agent-relay/sessions/{sid}/plans/1/html?size=a3", headers=H).status_code == 404
        ev = [e for e in rig.store.list_events(sid) if e["type"] == "plan"][0]
        assert "a3" not in ev["html_urls"]
        url = c.post(f"/agent-relay/sessions/{sid}/plans/1/print-url", json={"size": "a4"}, headers=H).json()["url"]
        r = c.get(url)                                           # 署名つきの印刷用 URL（鍵なし）
        assert r.status_code == 200 and "印刷の案" in r.text


def _disk(free_gb):
    from relay.retention import disk_status
    gb = 1024 ** 3
    return lambda: disk_status("/", usage=lambda p: type("U", (), {"free": int(free_gb * gb), "total": 100 * gb})())


def test_disk_status_in_engine_and_new_sessions_stop_below_5gb(tmp_path):
    rig = Rig(tmp_path, disk=_disk(7))                           # 5〜10 GB: 警告だけ・始められる
    with _client(rig) as c:
        d = c.get("/agent-relay/engine", headers=H).json()["disk"]
        assert d["low"] and not d["blocked"] and "空き容量" in d["warning"] and "path" not in d
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        assert c.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "こんにちは"}, headers=H).status_code == 200
    rig = Rig(tmp_path, disk=_disk(3))                           # 5 GB 未満: 新しい相談・CLI を始めない
    with _client(rig) as c:
        d = c.get("/agent-relay/engine", headers=H).json()["disk"]
        assert d["blocked"] and "新しい相談を始められません" in d["warning"]
        r = c.post("/agent-relay/sessions", json={}, headers=H)
        assert r.status_code == 503 and "空きが少ない" in r.json()["detail"]
        fresh = rig.store.create_session(ADMIN.id, "まだ始めていない")
        r = c.post(f"/agent-relay/sessions/{fresh['id']}/messages", json={"text": "はじめ"}, headers=H)
        assert r.status_code == 503 and not rig.runners
        old = rig.store.create_session(ADMIN.id, "再開")
        rig.store.update_session(old["id"], cli_session_id="11111111-1111-4111-8111-111111111111")
        r = c.post(f"/agent-relay/sessions/{old['id']}/messages", json={"text": "続き"}, headers=H)
        assert r.status_code == 200  # すでに会話のある相談の再開は止めない
    rig = Rig(tmp_path, disk=_disk(50))
    with _client(rig) as c:
        assert not c.get("/agent-relay/engine", headers=H).json()["disk"]["low"]


def test_plan_html_failure_is_calm_422_and_cached(tmp_path):
    rig = Rig(tmp_path)
    with _client(rig) as c:
        sid = c.post("/agent-relay/sessions", json={}, headers=H).json()["id"]
        rig.svc.submit_plan(rig.store.get_session(sid), plan(title="キャッシュの案"))
        row = rig.store.get_plan(sid, 1)
        n = []
        import relay.service as sv
        orig = sv.render_html
        sv.render_html = lambda p, a3=False: (n.append(1), orig(p, a3=a3))[1]
        try:
            for _ in range(3):
                assert "キャッシュの案" in rig.svc.plan_html(row, "a4")
            assert len(n) == 1                                   # (plan_sha256, size) で使い回す
            bad = dict(row, plan_sha256="x", plan_json={"kind": "k"})
            sv.render_html = lambda p, a3=False: (_ for _ in ()).throw(sv.RkError("壊れた案"))
            from relay.approvals import Refused
            try:
                rig.svc.plan_html(bad, "a4")
                raise AssertionError("422 になるはず")
            except Refused as e:
                assert e.status == 422 and "帳票にできませんでした" in str(e)
        finally:
            sv.render_html = orig
