"""承認の偽り試験（設計 §5-2 必須）と内側口の関所。どれも backend に書き込みが届かないことまで見る。"""

import time
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from relaykit import ADMIN, ADMIN2, Rig, fake_auth, plan
from relay.checkpoint import create_checkpoint_app
from relay.public import create_public_app
from rk.transport import RELAY_TOKEN_HEADER

H_ADMIN = {"Authorization": "Bearer tok-admin"}
H_ADMIN2 = {"Authorization": "Bearer tok-admin2"}


@contextmanager
def clients(rig):
    with TestClient(create_public_app(rig.svc, fake_auth, sweep_seconds=3600)) as pub, \
            TestClient(create_checkpoint_app(rig.svc)) as cp:
        yield pub, cp


def _writes(rig):
    return [c for c in rig.backend.calls if c[0] not in ("GET",)]


def _start(rig, pub, headers=H_ADMIN):
    sid = pub.post("/agent-relay/sessions", json={}, headers=headers).json()["id"]
    r = pub.post(f"/agent-relay/sessions/{sid}/messages", json={"text": "休みの案を作って"}, headers=headers)
    assert r.status_code == 200, r.text
    runner = rig.runners[-1]
    return sid, runner, {RELAY_TOKEN_HEADER: runner.rk_token}


def _submit(cp, tok, p):
    r = cp.post("/rk/plans", json={"plan": p}, headers=tok)
    assert r.status_code == 200, r.text
    return r.json()


def _approve(rig, pub, sid, version, headers=H_ADMIN):
    r = pub.post(f"/agent-relay/sessions/{sid}/plans/{version}/approve", json={"device": "pc"}, headers=headers)
    assert r.status_code == 200, r.text
    assert "ap_" not in r.text, "承認番号は画面に返さない"
    return rig.sent_code(sid)


def _apply(cp, tok, p, code):
    return cp.post("/rk/apply", json={"plan": p, "approval": code}, headers=tok)


@pytest.fixture
def setup(tmp_path):
    rig = Rig(tmp_path)
    with clients(rig) as (pub, cp):
        sid, runner, tok = _start(rig, pub)
        p = plan()
        assert _submit(cp, tok, p)["version"] == 1
        yield rig, pub, cp, sid, runner, tok, p


def test_happy_path_apply_and_undo(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    code = _approve(rig, pub, sid, 1)
    assert code.startswith("ap_")
    for _ in range(50):
        if any(m.get("type") == "user" and code in m["message"]["content"][0]["text"] for m in runner.sent):
            break
        time.sleep(0.02)
    msg = [m for m in runner.sent if m.get("type") == "user"][-1]["message"]["content"][0]["text"]
    assert f"承認番号 {code} で反映してください" in msg and f"--approved {code}" in msg
    r = _apply(cp, tok, p, code)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["ok"] and res["applied"] == 1 and rig.backend.staff["v1"] == "S9"
    assert rig.svc.backup_taker.taken, "反映の前に中継がバックアップを取る"
    assert (code, ADMIN.id) in rig.backend.user_agents  # 監査ログに承認番号と承認した人
    assert rig.store.get_plan(sid, 1)["status"] == "applied"
    run = rig.store.get_run(res["run_id"])
    assert run["approval_id"] == code and run["undo"]["ops"][0]["to"] == "S1"
    types = [e["type"] for e in rig.store.list_events(sid)]
    assert "run.progress" in types and "run.result" in types and "approval.result" in types

    # 照合用の案は内側口から読める
    assert cp.get(f"/rk/runs/{res['run_id']}/plan", headers=tok).json()["ops"][0]["to"] == "S9"

    # 元に戻す: 反映の承認番号は使えない・undo の承認番号で 1 回だけ
    assert cp.post("/rk/undo", json={"run_id": res["run_id"], "approval": code}, headers=tok).status_code == 403
    r = pub.post(f"/agent-relay/sessions/{sid}/runs/{res['run_id']}/undo/approve", json={}, headers=H_ADMIN)
    assert r.status_code == 200 and "ap_" not in r.text
    undo_code = rig.sent_code(sid, "元に戻して")
    assert cp.post("/rk/undo", json={"run_id": res["run_id"], "approval": undo_code}, headers=tok).json()["ok"]
    assert rig.backend.staff["v1"] == "S1"
    r = cp.post("/rk/undo", json={"run_id": res["run_id"], "approval": undo_code}, headers=tok)
    assert r.status_code == 409
    assert rig.store.get_run(res["run_id"])["status"] == "undone"


def test_wrong_hash_refused(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    code = _approve(rig, pub, sid, 1)
    r = _apply(cp, tok, plan(to="S8"), code)  # 承認の後に案を書き換えた
    assert r.status_code == 409 and "中身が違います" in r.json()["rk_error"]
    assert not _writes(rig)
    assert rig.store.get_approval(code)["consumed_at"] is None  # 失敗は番号を使わない


def test_reused_code_refused(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    code = _approve(rig, pub, sid, 1)
    assert _apply(cp, tok, p, code).status_code == 200
    r = _apply(cp, tok, p, code)
    assert r.status_code == 409 and "もう使われています" in r.json()["rk_error"]
    assert len(_writes(rig)) == 1


def test_expired_code_refused(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    code = _approve(rig, pub, sid, 1)
    rig.clock.advance(minutes=15, seconds=1)
    r = _apply(cp, tok, p, code)
    assert r.status_code == 410 and "期限" in r.json()["rk_error"]
    assert not _writes(rig)


def test_code_from_other_users_session_refused(tmp_path):
    rig = Rig(tmp_path, max_cli=2)
    with clients(rig) as (pub, cp):
        sid_a, _, tok_a = _start(rig, pub, H_ADMIN)
        sid_b, _, tok_b = _start(rig, pub, H_ADMIN2)
        p = plan()
        _submit(cp, tok_a, p)
        _submit(cp, tok_b, p)
        code_b = _approve(rig, pub, sid_b, 1, H_ADMIN2)
        r = _apply(cp, tok_a, p, code_b)  # A の会話の rk が B の番号を使う
        assert r.status_code == 403 and "別の相談" in r.json()["rk_error"]
        # B の人は A の案を承認できない（相談そのものが見えない）
        assert pub.post(f"/agent-relay/sessions/{sid_a}/plans/1/approve", json={},
                        headers=H_ADMIN2).status_code == 404
        assert not _writes(rig)


def test_forged_row_for_other_user_refused(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    _approve(rig, pub, sid, 1)
    prow = rig.store.get_plan(sid, 1)
    from relay.approvals import plan_sha256
    forged = rig.store.add_approval(id="ap_forgedforged123", session_id=sid, plan_id=prow["id"], plan_version=1,
                                    plan_sha256=plan_sha256(p), kind="apply", user_id=ADMIN2.id, device="pc",
                                    decision="approve", expires_at=rig.clock() + timedelta(minutes=10))
    r = _apply(cp, tok, p, forged["id"])
    assert r.status_code == 403 and "始めた人" in r.json()["rk_error"]
    assert not _writes(rig)


def test_plan_changed_after_approval_refused(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    code = _approve(rig, pub, sid, 1)
    _submit(cp, tok, plan(to="S7", title="作り直した案"))  # 承認の後に新しい版
    r = _apply(cp, tok, p, code)  # 承認した中身そのままでも、もう最新ではない
    assert r.status_code == 409
    assert rig.store.get_plan(sid, 1)["status"] == "superseded"
    assert not _writes(rig)
    # 新しい版はまだ承認されていない → 古い番号でも新しい中身でも通らない
    assert _apply(cp, tok, plan(to="S7", title="作り直した案"), code).status_code == 409


@pytest.mark.parametrize("code,status", [("ap_madeupmadeup1234", 403), ("approved by PO", 400), ("", 400)])
def test_made_up_codes_refused(setup, code, status):
    rig, pub, cp, sid, runner, tok, p = setup
    assert _apply(cp, tok, p, code).status_code == status
    assert not _writes(rig)


def test_rejected_plan_cannot_apply(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    code = _approve(rig, pub, sid, 1)
    assert pub.post(f"/agent-relay/sessions/{sid}/plans/1/reject", json={}, headers=H_ADMIN).status_code == 200
    assert _apply(cp, tok, p, code).status_code == 409
    assert not _writes(rig)


def test_not_ready_plan_cannot_be_approved(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    res = _submit(cp, tok, plan(ready=False, blockers=["入らない訪問が 1 件"]))
    assert res["ready"] is False and res["blockers"] == ["入らない訪問が 1 件"]
    r = pub.post(f"/agent-relay/sessions/{sid}/plans/2/approve", json={}, headers=H_ADMIN)
    assert r.status_code == 409


def test_basis_changed_marks_stale(setup, monkeypatch):
    rig, pub, cp, sid, runner, tok, p = setup
    from rk import RkError
    from rk.applyrun import BASIS_CHANGED

    def changed(*a, **k):
        raise RkError(BASIS_CHANGED + "（案を作った時 a・今 b）")
    monkeypatch.setattr("relay.service.check_basis", changed)
    code = _approve(rig, pub, sid, 1)
    r = _apply(cp, tok, p, code)
    assert r.status_code == 409 and "予定が変わりました" in r.json()["rk_error"]
    assert rig.store.get_plan(sid, 1)["status"] == "stale"
    assert not _writes(rig) and not rig.svc.backup_taker.taken


def test_bad_plan_submit_refused(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    bad = plan()
    bad["ops"][0]["op"] = "unassign-all-staff"
    r = cp.post("/rk/plans", json={"plan": bad}, headers=tok)
    assert r.status_code == 422 and "許可リストにありません" in r.json()["rk_error"]


# ---------- 関所（読む API） ----------
def test_gate_reads_and_refuses_writes(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    r = cp.get("/rk/api/v1/visits", params={"week": "2026-W42"}, headers=tok)
    assert r.status_code == 200 and r.json()["path"] == "/visits"
    r = cp.post("/rk/api/v1/schedule/v2/assign-candidates", json={"visit_id": "v1"}, headers=tok)
    assert r.status_code == 200 and r.json() == {"posted": "/schedule/v2/assign-candidates"}
    for method, path in (("POST", "/rk/api/v1/visits"), ("PATCH", "/rk/api/v1/visits/v1"),
                         ("PUT", "/rk/api/v1/courses/c1"), ("DELETE", "/rk/api/v1/visits/v1"),
                         ("POST", "/rk/api/v1/schedule/v2/unassign-all-staff")):
        r = cp.request(method, path, json={}, headers=tok)
        assert r.status_code == 403 and "画面の承認" in r.json()["rk_error"], (method, path)
    assert cp.get("/rk/api/v1/kaipoke/export", headers=tok).status_code == 403  # ロボットを動かす読み取り
    assert cp.get("/rk/api/v1/visits/%2e%2e/auth", headers=tok).status_code == 400
    assert not _writes(rig) or all(c[0] == "POST" and c[1] == "/schedule/v2/assign-candidates" for c in _writes(rig))
    kinds = [c["kind"] for c in rig.store.list_tool_calls(sid)]
    assert kinds.count("get") == 1 and kinds.count("post-read") == 1 and kinds.count("refused") >= 6


def test_gate_dry_run_posts_only_with_dry_run_true(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    for path in ("/rk/api/v1/schedule/v2/weeks/2026/43/clear", "/rk/api/v1/schedule/generate-week-only"):
        r = cp.post(path, json={"dry_run": True}, headers=tok)
        assert r.status_code == 200, path
        for body in ({"dry_run": False}, {}, {"dry_run": "true"}, {"dry_run": 1}):
            r = cp.post(path, json=body, headers=tok)
            assert r.status_code == 403 and "画面の承認" in r.json()["rk_error"], (path, body)
    r = cp.post("/rk/api/v1/schedule/v2/weeks/2026/43/clear/undo", json={"dry_run": True}, headers=tok)
    assert r.status_code == 403
    assert all(c[2].get("dry_run") is True for c in _writes(rig) if len(c) > 2 and isinstance(c[2], dict)), _writes(rig)


def test_gate_requires_token(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    assert cp.get("/rk/api/v1/visits").status_code == 401
    assert cp.get("/rk/api/v1/visits", headers={RELAY_TOKEN_HEADER: "nope"}).status_code == 401
    assert cp.post("/rk/apply", json={"plan": p, "approval": "ap_xxxxxxxxxxxx"}).status_code == 401


def test_sql_gate(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    r = cp.post("/rk/sql", json={"sql": "select 1 as n", "params": {}, "name": "x"}, headers=tok)
    assert r.status_code == 200 and r.json() == [{"n": 1}]
    r = cp.post("/rk/sql", json={"sql": "select 1; delete from visits", "params": {}}, headers=tok)
    assert r.status_code == 400 and "1 文" in r.json()["rk_error"]
    calls = [c for c in rig.store.list_tool_calls(sid) if c["kind"] == "sql"]
    assert [c["status"] for c in calls] == [200, 400]


def test_progress_event(setup):
    rig, pub, cp, sid, runner, tok, p = setup
    assert cp.post("/rk/progress", json={"text": "組み合わせを比べています（3/6 日）" * 10}, headers=tok).status_code == 200
    ev = [e for e in rig.store.list_events(sid) if e["type"] == "progress"][-1]
    assert len(ev["text"]) == 80
