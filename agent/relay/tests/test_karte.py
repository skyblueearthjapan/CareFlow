"""カルテの下書きを決めた管理者の ChatGPT の席で作る（relay.karte・relay.service_port・PO 2026-10-13）。

- サーバーどうしの口: 合言葉・箱の網から断る・設定が無ければ 404・形の誤りは 400
- 席の選び方: designated（karte_seat_user_id）/ presser（録音した本人）・サインインしていない席は使わない
- 打ち切り（timeout）・道具が動いたら打ち切る・答えの形（backend の要約と同じ）・記録に本文を書かない
- 本物の JSON-RPC（偽の app-server）: thread/start に read-only・never・ephemeral が載る
"""

import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient

from relaykit import ADMIN, ADMIN2, FIXTURES, Rig, fake_auth
from relay import events as ev
from relay.codex_session import CodexSession, StartRefused
from relay.engines import seat_key
from relay.karte import (SETTING_MODE, SETTING_SEAT, THREAD_EXTRA, KarteDrafter, KarteFailed, instructions,
                         parse_summary, turn_text, validate_request)
from relay.public import create_public_app
from relay.service_port import SERVICE_TOKEN_HEADER, create_service_app
from test_codex import HERE

TOKEN = "s" * 48
REC = "3f1c2a9e-0000-4000-8000-000000000001"
SUMMARY = {"主訴・様子": ["腰の痛みは楽"], "バイタル": {"血圧": "132/78", "体温": "36.5", "脈拍": "", "SpO2": "", "血糖": ""},
           "処置・ケア": ["清拭"], "申し送り": [], "次回": "来週火曜", "free": ""}
BODY = {"recording_id": REC, "transcript": "看護師: 血圧は132の78です。\n患者: 腰は楽です。",
        "context": {"age_band": "80代", "sex": "女性", "visit_date": "2026-10-13", "visit_time": "10:30",
                    "staff_role": "看護師", "patient_name": "山田花子"},
        "presser_user_id": ADMIN2.id}


class FakeKarteSession:
    """会話の係の代わり（start / send / events / close）。events は決まった並びを流す。"""

    def __init__(self, events=None, *, start_exc=None, hang=False):
        self._events = events if events is not None else [
            {"type": "_meta"}, ev.status("working"), {"type": "message", "text": json.dumps({"summary": SUMMARY},
                                                                                           ensure_ascii=False)},
            {"type": "turn.end", "secs": 1.0, "ok": True, "reason": "completed"}]
        self.start_exc, self.hang = start_exc, hang
        self.sent, self.closed = [], False

        class P:
            current = "gpt-6.1-sol"
        self.picker = P()

    async def start(self):
        if self.start_exc:
            raise self.start_exc

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        if self.hang:
            await asyncio.sleep(30)
        for e in self._events:
            yield e

    async def close(self):
        self.closed = True


def _rig(tmp_path, sessions=None, states=None, timeout=5.0, **cfg_kw):
    rig = Rig(tmp_path, service_token=TOKEN, sandbox_subnet="172.31.250.0/24", **cfg_kw)
    states = states if states is not None else {}
    asked = []

    async def status(provider, key, fresh=False):
        asked.append((provider, key))
        return states.get(key, "ok")

    rig.svc.engines.status = status
    rig.svc.engines.forget = lambda *a, **k: None
    made = []

    def factory(session_id, key):
        s = (sessions or [FakeKarteSession()]).pop(0) if sessions is not None else FakeKarteSession()
        made.append((session_id, key, s))
        return s

    rig.karte = KarteDrafter(rig.svc, session_factory=factory, timeout=timeout)
    rig.made, rig.asked = made, asked

    class Sql:
        def run(self, sql, params=None):
            return [{"id": ADMIN.id, "name": "今泉"}, {"id": ADMIN2.id, "name": "佐藤"}]

    rig.svc.sql = Sql()
    return rig


def _svc_client(rig, client=("10.0.0.5", 50000)):
    return TestClient(create_service_app(rig.svc, rig.karte), client=client)


def _pub_client(rig):
    return TestClient(create_public_app(rig.svc, fake_auth, sweep_seconds=3600, karte=rig.karte))


H = {"Authorization": "Bearer tok-admin"}
H2 = {"Authorization": "Bearer tok-admin2"}
HS = {"Authorization": "Bearer tok-staff"}
SVC = {SERVICE_TOKEN_HEADER: TOKEN}


# ---------------- サーバーどうしの口の守り ----------------
def test_service_port_needs_token(tmp_path):
    rig = _rig(tmp_path)
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    with _svc_client(rig) as c:
        assert c.post("/svc/karte/draft", json=BODY).status_code == 401
        assert c.post("/svc/karte/draft", json=BODY, headers={SERVICE_TOKEN_HEADER: "x" * 48}).status_code == 401
        assert c.get("/svc/healthz", headers=SVC).json() == {"ok": True}
        r = c.post("/svc/karte/draft", json=BODY, headers=SVC)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_service_port_refuses_sandbox_and_is_off_without_token(tmp_path):
    rig = _rig(tmp_path)
    with _svc_client(rig, client=("172.31.250.7", 40000)) as c:
        assert c.post("/svc/karte/draft", json=BODY, headers=SVC).status_code == 404
    rig.svc.cfg.service_token = ""
    with _svc_client(rig) as c:
        assert c.post("/svc/karte/draft", json=BODY, headers=SVC).status_code == 404


def test_service_port_bad_body_is_400(tmp_path):
    rig = _rig(tmp_path)
    with _svc_client(rig) as c:
        assert c.post("/svc/karte/draft", json={"recording_id": REC, "transcript": ""}, headers=SVC).status_code == 400
        assert c.post("/svc/karte/draft", json={"recording_id": "../x", "transcript": "a"},
                      headers=SVC).status_code == 400
        assert c.post("/svc/karte/draft", json={**BODY, "transcript": "あ" * 60_001},
                      headers=SVC).status_code == 400


def test_public_app_has_no_karte_draft(tmp_path):
    rig = _rig(tmp_path)
    with _pub_client(rig) as c:
        assert c.post("/agent-relay/karte/draft", json=BODY, headers=H).status_code in (404, 405)
        assert c.post("/svc/karte/draft", json=BODY, headers=SVC).status_code == 404


# ---------------- 席の選び方 ----------------
def test_designated_seat_is_used_and_names_are_dropped(tmp_path):
    rig = _rig(tmp_path)
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"] and r["summary"] == SUMMARY and r["model"] == "gpt-6.1-sol" and r["prompt_version"] == "seat-v1"
    assert r["seat_user_id"] == ADMIN.id and r["mode"] == "designated"
    sid, key, sess = rig.made[0]
    assert key == seat_key(ADMIN.id) and sid.startswith("karte-") and sess.closed
    sent = sess.sent[0]
    assert "山田花子" not in sent and "80代" in sent and "女性" in sent and "看護師: 血圧は132の78です。" in sent
    ev_ = rig.store.list_admin_events()[0]
    assert ev_["action"] == "karte_draft" and ev_["user_id"] == ADMIN.id and f"rec={REC}" in ev_["detail"]
    assert "ok model=gpt-6.1-sol" in ev_["detail"]
    assert "血圧" not in ev_["detail"] and "腰" not in ev_["detail"]  # 本文は書かない


def test_presser_mode_uses_the_pressers_seat(tmp_path):
    rig = _rig(tmp_path)
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    rig.store.set_setting(SETTING_MODE, "presser")
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"] and r["seat_user_id"] == ADMIN2.id and r["mode"] == "presser"
    assert rig.made[0][1] == seat_key(ADMIN2.id)


@pytest.mark.parametrize("setup,kind", [
    ("none", "no_seat"), ("signed_out", "signed_out"), ("disabled", "disabled"), ("lowmem", "busy")])
def test_fallback_kinds_before_starting(tmp_path, setup, kind):
    states = {seat_key(ADMIN.id): "missing"} if setup == "signed_out" else {}
    rig = _rig(tmp_path, states=states)
    if setup != "none":
        rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    if setup == "disabled":
        rig.svc.cfg.enabled = False
    if setup == "lowmem":
        rig.svc.mem_mb = lambda: 100
    r = asyncio.run(rig.karte.draft(BODY))
    assert r == {"ok": False, "kind": kind, "seat_user_id": r["seat_user_id"], "mode": "designated",
                 "secs": r["secs"]}
    assert rig.made == []  # CLI は起こさない
    assert f"fallback={kind}" in rig.store.list_admin_events()[0]["detail"]


def test_timeout_falls_back_and_closes(tmp_path):
    s = FakeKarteSession(hang=True)
    rig = _rig(tmp_path, sessions=[s], timeout=0.3)
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"] is False and r["kind"] == "timeout" and s.closed


@pytest.mark.parametrize("events,kind", [
    ([{"type": "step.start", "step_id": "s1", "label": "…"}], "tool_use"),
    ([{"type": "_audit", "kind": "command"}], "tool_use"),
    ([ev.status("rate_limited", "12:00 以降に続けられます")], "rate_limited"),
    ([ev.error("login", "切れています")], "signed_out"),
    ([{"type": "turn.end", "ok": False, "reason": "failed"}], "failed"),
    ([{"type": "message", "text": "すみません、作れませんでした"}, {"type": "turn.end", "ok": True}], "parse"),
    ([{"type": "_exit", "code": 1}], "failed"),
])
def test_fallback_kinds_while_running(tmp_path, events, kind):
    s = FakeKarteSession(events)
    rig = _rig(tmp_path, sessions=[s])
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"] is False and r["kind"] == kind and s.closed


def test_usage_warning_does_not_stop(tmp_path):
    s = FakeKarteSession([{**ev.error("usage_limit", "近づいています", retry=False), "level": "warn"},
                          {"type": "message", "text": "```json\n" + json.dumps({"summary": SUMMARY}) + "\n```"},
                          {"type": "turn.end", "ok": True}])
    rig = _rig(tmp_path, sessions=[s])
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    assert asyncio.run(rig.karte.draft(BODY))["ok"] is True


@pytest.mark.parametrize("exc,kind", [
    (StartRefused(ev.error("login", "x")), "signed_out"),
    (RuntimeError("箱の runner が CLI を起こせませんでした: busy"), "busy"),
    (RuntimeError("箱の runner につながりません: FileNotFoundError"), "unreachable"),
])
def test_start_failures(tmp_path, exc, kind):
    rig = _rig(tmp_path, sessions=[FakeKarteSession(start_exc=exc)])
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    assert asyncio.run(rig.karte.draft(BODY))["kind"] == kind


def test_default_factory_needs_runner(tmp_path):
    rig = Rig(tmp_path)
    k = KarteDrafter(rig.svc)
    with pytest.raises(KarteFailed) as ei:
        k._runner_session("karte-" + "0" * 24, seat_key(ADMIN.id))
    assert ei.value.kind == "unreachable"


def test_default_factory_spawn_shape(tmp_path):
    rig = Rig(tmp_path, launch="runner", runner_socket="/run/agent/runner.sock")
    s = KarteDrafter(rig.svc)._runner_session("karte-" + "a" * 24, seat_key(ADMIN.id))
    assert s.spawn_msg["provider"] == "codex" and s.spawn_msg["user_key"] == seat_key(ADMIN.id)
    assert s.spawn_msg["relay_token"] not in rig.svc._tokens  # 内側口は断る（使い捨て）
    assert s.thread_cwd == s.work_dir == "/data/sessions/karte-" + "a" * 24 + "/work"
    assert s.thread_extra == THREAD_EXTRA
    assert "道具" in s.developer_instructions


# ---------------- 形 ----------------
def test_parse_summary_fills_and_drops():
    got = parse_summary(json.dumps({"summary": {"主訴・様子": "眠れている", "バイタル": {"血圧": "120/70", "x": "1"},
                                                "処置・ケア": ["清拭", " "], "余計": 1, "次回": ["月曜", "10時"]}}))
    assert got == {"主訴・様子": ["眠れている"], "処置・ケア": ["清拭"], "申し送り": [],
                   "バイタル": {"血圧": "120/70", "体温": "", "脈拍": "", "SpO2": "", "血糖": ""},
                   "次回": "月曜\n10時", "free": ""}
    with pytest.raises(KarteFailed):
        parse_summary("[1, 2]")
    with pytest.raises(KarteFailed):
        parse_summary(json.dumps({"other": 1}))


def test_request_validation_and_prompt():
    req = validate_request(BODY)
    assert "patient_name" not in req["context"]
    assert "［不明］" in instructions() and "推測・創作しない" in instructions()
    assert "看護師: 血圧" in turn_text(req["transcript"], req["context"])
    with pytest.raises(ValueError):
        validate_request({**BODY, "presser_user_id": "a b"})


# ---------------- ⚙ の設定（管理者だけ） ----------------
def test_settings_admin_only_and_lists_signed_in_state(tmp_path):
    rig = _rig(tmp_path, states={seat_key(ADMIN2.id): "missing"})
    with _pub_client(rig) as c:
        assert c.get("/agent-relay/karte/settings", headers=HS).status_code == 403
        v = c.get("/agent-relay/karte/settings", headers=H).json()
    assert v["mode"] == "designated" and v["seat_user_id"] is None and v["modes"] == ["designated", "presser"]
    assert v["seats"] == [{"user_id": ADMIN.id, "name": "今泉", "me": True, "state": "ok"},
                          {"user_id": ADMIN2.id, "name": "佐藤", "me": False, "state": "missing"}]


def test_settings_update_validates_and_audits(tmp_path):
    rig = _rig(tmp_path, states={seat_key(ADMIN2.id): "missing"})
    origin = {"Origin": "https://carelink.kaipoke-api.net"}
    with _pub_client(rig) as c:
        assert c.put("/agent-relay/karte/settings", json={"seat_user_id": "u-nobody"},
                     headers={**H, **origin}).status_code == 400
        assert c.put("/agent-relay/karte/settings", json={"seat_user_id": ADMIN2.id},
                     headers={**H, **origin}).status_code == 409
        assert c.put("/agent-relay/karte/settings", json={"mode": "everyone"},
                     headers={**H, **origin}).status_code == 400
        v = c.put("/agent-relay/karte/settings", json={"seat_user_id": ADMIN.id, "mode": "presser"},
                  headers={**H2, **origin}).json()
    assert v["seat_user_id"] == ADMIN.id and v["mode"] == "presser" and v["seat_known"] is True
    assert rig.store.get_setting(SETTING_SEAT) == ADMIN.id
    e = rig.store.list_admin_events()[0]
    assert e["action"] == "karte_settings" and e["user_id"] == ADMIN2.id
    assert e["detail"] == f"mode=designated->presser seat=-->{ADMIN.id}"


# ---------------- 本物の JSON-RPC（偽の app-server） ----------------
def _codex(tmp_path, fixture):
    received = tmp_path / "received.jsonl"
    argv = [sys.executable, str(HERE / "fake_codex.py"), str(FIXTURES / f"{fixture}.jsonl"), str(received), "ok"]
    work = str(tmp_path / "work")
    s = CodexSession(argv, work, {"PATH": "", "SYSTEMROOT": "C:\\Windows"}, work_dir=work,
                     developer_instructions=instructions(), rpc_timeout=10, stop_wait=3)
    s.thread_extra = dict(THREAD_EXTRA)
    return s, received


def test_real_protocol_draft(tmp_path):
    (tmp_path / "work").mkdir()
    s, received = _codex(tmp_path, "codex_karte")
    rig = _rig(tmp_path, sessions=[s], timeout=20)
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"], r
    assert r["summary"]["バイタル"]["血圧"] == "132/78" and r["summary"]["申し送り"] == ["［不明］ 薬の残数の確認"]
    sent = [json.loads(x) for x in received.read_text(encoding="utf-8").splitlines()]
    start = next(m for m in sent if m.get("method") == "thread/start")["params"]
    assert start["sandbox"] == "read-only" and start["approvalPolicy"] == "never" and start["ephemeral"] is True
    assert start["cwd"] == str(tmp_path / "work") and "看護記録" in start["developerInstructions"]
    turn = next(m for m in sent if m.get("method") == "turn/start")["params"]
    assert "山田花子" not in json.dumps(turn, ensure_ascii=False)


def test_real_protocol_tool_use_is_cut(tmp_path):
    (tmp_path / "work").mkdir()
    s, _ = _codex(tmp_path, "codex_sql_list")  # コマンドを動かす実物のターン
    rig = _rig(tmp_path, sessions=[s], timeout=20)
    rig.store.set_setting(SETTING_SEAT, ADMIN.id)
    r = asyncio.run(rig.karte.draft(BODY))
    assert r["ok"] is False and r["kind"] == "tool_use"
