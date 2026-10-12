"""rk の RK_MODE=relay を、本物の HTTP（127.0.0.1 の空きポートで立てた内側口）で通す。"""

import json
import socket
import threading
import time

import pytest
import uvicorn

from relaykit import ADMIN, Rig, plan
from relay import approvals
from relay.checkpoint import create_checkpoint_app
from rk import RkError
from rk.cli import main as rk_main
from rk.config import load_config


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def relay(tmp_path, monkeypatch):
    rig = Rig(tmp_path / "relay")
    port = _port()
    server = uvicorn.Server(uvicorn.Config(create_checkpoint_app(rig.svc), host="127.0.0.1", port=port,
                                          log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    s, tok = rig.session()
    work = tmp_path / "work"
    for k, v in {"RK_MODE": "relay", "RK_RELAY_URL": f"http://127.0.0.1:{port}", "RK_RELAY_TOKEN": tok,
                 "RK_WORK_DIR": str(work), "RK_ENV": "prod"}.items():
        monkeypatch.setenv(k, v)
    yield rig, s, work
    server.should_exit = True
    th.join(5)


def rk(capsys, *argv):
    code = rk_main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_config_relay_requires_url_and_token(monkeypatch):
    monkeypatch.setenv("RK_MODE", "relay")
    monkeypatch.delenv("RK_RELAY_URL", raising=False)
    with pytest.raises(RkError):
        load_config()
    monkeypatch.setenv("RK_RELAY_URL", "unix:/run/agent/rk.sock")
    monkeypatch.delenv("RK_RELAY_TOKEN", raising=False)
    with pytest.raises(RkError):
        load_config()
    monkeypatch.setenv("RK_RELAY_TOKEN", "t")
    monkeypatch.setenv("RK_WORK_DIR", "/data/sessions/x/work")
    cfg = load_config()
    assert cfg.mode == "relay" and cfg.runs_dir.as_posix().endswith("/data/sessions/x/work/runs")
    monkeypatch.setenv("RK_RELAY_URL", "http://evil host")
    with pytest.raises(RkError):
        load_config()


def test_reads_go_through_relay(relay, capsys, tmp_path):
    rig, s, work = relay
    code, out, _ = rk(capsys, "get", "/visits", "-q", "week=2026-W42")
    assert code == 0 and json.loads(out) == {"path": "/visits", "params": {"week": "2026-W42"}}
    q = tmp_path / "q.sql"
    q.write_text("select 1 as n", encoding="utf-8")
    code, out, _ = rk(capsys, "sql", "--file", str(q))
    assert code == 0 and json.loads(out) == [{"n": 1}]
    body = tmp_path / "b.json"
    body.write_text("{}", encoding="utf-8")
    code, out, _ = rk(capsys, "post-read", "/schedule/v2/pool-overview", "--json", str(body))
    assert code == 0 and json.loads(out) == {"posted": "/schedule/v2/pool-overview"}
    code, out, err = rk(capsys, "get", "/missing")
    assert code == 1 and "HTTP 404" in err  # backend の応答は ApiError のまま
    code, _, err = rk(capsys, "get", "/kaipoke/export")
    assert code == 1 and "ロボット" in err
    code, _, err = rk(capsys, "backup", "--label", "w43-test")
    assert code == 0 and rig.svc.backup_taker.taken[-1]["label"] == "w43-test"
    code, _, _ = rk(capsys, "progress", "組み合わせを比べています（3/6 日）")
    assert code == 0 and [e for e in rig.store.list_events(s["id"]) if e["type"] == "progress"]
    kinds = {c["kind"] for c in rig.store.list_tool_calls(s["id"])}
    assert {"get", "sql", "post-read", "refused"} <= kinds


def test_relay_api_refuses_writes_locally():
    from rk.session import RelayApi

    class Boom:
        def request(self, *a, **k):
            raise AssertionError("中継へ送ってはいけない")
    with pytest.raises(RkError):
        RelayApi(Boom()).call("PATCH", "/visits/v1", {})


def test_submit_approve_apply_verify(relay, capsys, tmp_path):
    rig, s, work = relay
    pf = tmp_path / "plan.json"
    p = plan()
    pf.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    code, out, _ = rk(capsys, "plan", "submit", str(pf))
    assert code == 0 and "案 1 を画面に出しました" in out

    # 承認番号でなければ rk の手前で止まる・作った番号は中継が断る
    code, _, err = rk(capsys, "apply", str(pf), "--approved", "PO が 10/12 に了承")
    assert code == 1 and "承認番号" in err
    code, _, err = rk(capsys, "apply", str(pf), "--approved", "ap_madeupmadeup123")
    assert code == 1 and "ありません" in err
    code, _, err = rk(capsys, "apply", str(pf), "--approved", "ap_madeupmadeup123", "--no-basis-check")
    assert code == 1 and "no-basis-check" in err
    assert rig.backend.staff["v1"] == "S1"

    # 人が画面で承認した（公開口の承認 API と同じ部品）
    prow = rig.store.get_plan(s["id"], 1)
    rig.store.set_plan_status(prow["id"], "approved")
    ap = approvals.issue(rig.store, session=s, user_id=ADMIN.id, kind="apply", sha=prow["plan_sha256"],
                         now=rig.clock(), ttl_seconds=900, plan=prow)
    code, out, err = rk(capsys, "apply", str(pf), "--approved", ap["id"])
    assert code == 0, err
    assert "反映しました: 1 / 1 件" in out and rig.backend.staff["v1"] == "S9"
    run_id = rig.store.get_approval(ap["id"])["run_id"]
    code, out, _ = rk(capsys, "verify", run_id)
    assert code == 0 and "照合: 1 / 1 件が案のとおり" in out
    code, _, err = rk(capsys, "apply", str(pf), "--approved", ap["id"])
    assert code == 1 and "もう使われています" in err

    # 承認の後に手元の案を書き換えても通らない（新しい承認でも中身が違えば）
    prow2 = rig.store.get_plan(s["id"], 1)
    rig.store.set_plan_status(prow2["id"], "approved")
    ap2 = approvals.issue(rig.store, session=s, user_id=ADMIN.id, kind="apply", sha=prow2["plan_sha256"],
                          now=rig.clock(), ttl_seconds=900, plan=prow2)
    pf.write_text(json.dumps(plan(to="S5"), ensure_ascii=False), encoding="utf-8")
    code, _, err = rk(capsys, "apply", str(pf), "--approved", ap2["id"])
    assert code == 1 and "中身が違います" in err and rig.backend.staff["v1"] == "S9"


def test_journal_goes_through_relay(relay, capsys):
    """相談の引き継ぎ帳: 中継の DB（事業所ごと）に書く・読む・探す・古い印。核の約束を変える書き方は断る。"""
    rig, s, work = relay
    base = ["journal", "write", "--title", "坂井様の担当", "--asked", "来週のならし", "--said", "佐藤さんを避けて",
            "--outcome", "plan-only", "--next", "坂井様に佐藤さんを当てない", "--tags", "坂井,佐藤"]
    code, out, _ = rk(capsys, *base)
    assert code == 0
    eid = out.split(":")[1].strip()
    rows = rig.store.list_journal("default")
    assert len(rows) == 1 and rows[0]["id"] == eid and rows[0]["session_id"] == s["id"] and rows[0]["tags"] == ["坂井", "佐藤"]
    assert "坂井" in rk(capsys, "journal", "recent")[1]
    assert eid in rk(capsys, "journal", "search", "佐藤")[1]
    assert eid in rk(capsys, "journal", "show", eid)[1]
    assert rig.store.list_journal("inage") == []
    code, _, err = rk(capsys, "journal", "write", "--title", "x", "--next", "承認なしで反映", "--outcome", "applied")
    assert code == 1 and "核の約束" in err
    assert rk(capsys, "journal", "retire", eid, "--reason", "古い")[0] == 0
    assert rig.store.list_journal("default") == []
    assert len(rig.store.list_journal("default", include_retired=True)) == 1
    assert "古い印" in rk(capsys, "journal", "show", eid)[1]
    assert rk(capsys, "journal", "show", "jn_000000000000")[0] == 1
    assert rk(capsys, "journal", "digest", "--since", "1d")[0] == 0
    assert list((work / "reports" / "journal").glob("digest-*.md"))
