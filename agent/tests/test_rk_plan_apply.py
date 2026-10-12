import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError  # noqa: E402
from rk.applyrun import apply_plan, undo_run, verify_plan  # noqa: E402
from rk.backup import recent_backup, run_backup  # noqa: E402
from rk.config import Config  # noqa: E402
from rk.opsbase import ApiError, Op, Registry  # noqa: E402
from rk.plan import check_plan  # noqa: E402

NOW = datetime(2026, 10, 12, 1, 0, 0, tzinfo=timezone.utc)


class FakeApi:
    """訪問の担当を辞書で持つだけの偽の API。"""

    def __init__(self, fail_on=None):
        self.staff = {"v1": "S1", "v2": "S2"}
        self.calls = []
        self.fail_on = fail_on

    def get(self, path, params=None):
        self.calls.append(("GET", path, None))
        return {"staff": self.staff[path.rsplit("/", 1)[-1]]}

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        vid = path.rsplit("/", 1)[-1]
        if vid == self.fail_on:
            raise ApiError(422, {"detail": "だめ"})
        self.staff[vid] = body["staff"]
        return {"ok": True}


class SetStaff(Op):
    name = "t-set-staff"
    summary = "担当を替える（試験）"

    def validate(self, op):
        return [] if op.get("visit_id") else ["visit_id がありません"]

    def request(self, op):
        return ("PATCH", f"/t/{op['visit_id']}", {"staff": op["to"]})

    def snapshot(self, api, op):
        return api.get(f"/t/{op['visit_id']}")

    def inverse(self, op, before):
        return [{"op": "t-set-staff", "visit_id": op["visit_id"], "to": before["staff"]}]

    def verify(self, api, op):
        got = api.get(f"/t/{op['visit_id']}")["staff"]
        return [] if got == op["to"] else [f"担当が {got}"]


class OneWay(SetStaff):
    name = "t-one-way"
    undoable = False

    def inverse(self, op, before):
        return None


@pytest.fixture
def reg():
    r = Registry()
    r.add(SetStaff)
    r.add(OneWay)
    return r


@pytest.fixture
def cfg(tmp_path):
    return Config(runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports")


def _plan(*ops):
    return {"kind": "staff-absence", "title": "試験の案", "target": {"date": "2026-10-22"},
            "summary": ["2 件"], "ops": list(ops), "checks": ["NG"], "warnings": []}


def _op(v, to, name="t-set-staff"):
    return {"op": name, "visit_id": v, "to": to, "label": f"{v} → {to}"}


def _backup(cfg, minutes_ago=5):
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    at = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (cfg.runs_dir / "backups.jsonl").write_text(json.dumps({"label": "t", "path": "/x.sql.gz", "at_utc": at}) + "\n")


# ---------- plan check ----------
def test_plan_check_ok_and_errors(reg):
    assert check_plan(_plan(_op("v1", "S9")), reg) == []
    errs = check_plan({"kind": "x"}, reg)
    assert any("title" in e for e in errs)
    bad = _plan({"op": "nope", "label": "a"}, {"op": "t-set-staff", "label": "b"}, {"op": "t-set-staff", "visit_id": "v"})
    errs = check_plan(bad, reg)
    assert any("許可リストにありません" in e for e in errs)
    assert any("visit_id がありません" in e for e in errs)
    assert any("label がありません" in e for e in errs)
    p = _plan(_op("v1", "S9"))
    p["summary"] = "文字列"
    assert any("summary" in e for e in check_plan(p, reg))


def test_default_registry_has_noop():
    from rk.opsbase import load_all
    assert load_all().get("noop") is not None


# ---------- apply ----------
def test_apply_requires_approval_and_backup(cfg, reg):
    api = FakeApi()
    with pytest.raises(RkError, match="approved"):
        apply_plan(cfg, _plan(_op("v1", "S9")), api, approved="", registry=reg, now=NOW)
    with pytest.raises(RkError, match="バックアップ"):
        apply_plan(cfg, _plan(_op("v1", "S9")), api, approved="今泉 10/12", registry=reg, now=NOW)
    _backup(cfg, minutes_ago=45)  # 古い
    with pytest.raises(RkError, match="バックアップ"):
        apply_plan(cfg, _plan(_op("v1", "S9")), api, approved="今泉 10/12", registry=reg, now=NOW)
    assert api.calls == []
    # --backup でも 30 分の決まりは外せない（記録に無い・古い記録は断る）
    with pytest.raises(RkError, match="記録"):
        apply_plan(cfg, _plan(_op("v1", "S9")), api, approved="今泉", backup="/opt/x.sql.gz", registry=reg, now=NOW)
    with pytest.raises(RkError, match="記録"):
        apply_plan(cfg, _plan(_op("v1", "S9")), api, approved="今泉", backup="/x.sql.gz", registry=reg, now=NOW)
    assert api.calls == []


def test_explicit_backup_must_match_fresh_record(cfg, reg):
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    recs = [{"label": "a", "path": "/opt/carelink/backups/agent-a-1.sql.gz",
             "at_utc": (NOW - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            {"label": "b", "path": "/opt/carelink/backups/agent-b-2.sql.gz",
             "at_utc": (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}]
    (cfg.runs_dir / "backups.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    api = FakeApi()
    res = apply_plan(cfg, _plan(_op("v1", "S9")), api, approved="今泉",
                     backup="/opt/carelink/backups/agent-a-1.sql.gz", registry=reg, now=NOW)
    assert res["ok"]
    meta = json.loads((Path(res["run_dir"]) / "meta.json").read_text(encoding="utf-8"))
    assert meta["backup"]["label"] == "a" and meta["backup"]["explicit"] is True
    # ファイル名だけでも可
    assert apply_plan(cfg, _plan(_op("v1", "S8")), api, approved="今泉", backup="agent-b-2.sql.gz",
                      registry=reg, now=NOW)["ok"]
    # 同じパスでも 30 分を過ぎたら断る
    with pytest.raises(RkError, match="30 分以内"):
        apply_plan(cfg, _plan(_op("v1", "S7")), api, approved="今泉",
                   backup="/opt/carelink/backups/agent-a-1.sql.gz", registry=reg, now=NOW + timedelta(minutes=15))
    with pytest.raises(RkError):
        apply_plan(cfg, _plan(_op("v1", "S7")), api, approved="今泉",
                   backup="/opt/carelink/backups/other.sql.gz", registry=reg, now=NOW)


def test_transport_failure_counts_as_unknown_and_stops(cfg, reg):
    from rk.applyrun import summary_ja

    class Flaky(FakeApi):
        def call(self, method, path, body=None):
            if path.endswith("v2"):
                self.calls.append((method, path, body))
                raise ConnectionResetError("切れました")
            return super().call(method, path, body)
    _backup(cfg)
    api = Flaky()
    res = apply_plan(cfg, _plan(_op("v1", "S9"), _op("v2", "S8"), _op("v1", "S7")), api, approved="今泉",
                     registry=reg, now=NOW)
    assert not res["ok"] and res["applied"] == 1 and res["unknown"] == 1
    assert api.staff["v1"] == "S9"  # 3 件目は呼ばれていない
    d = Path(res["run_dir"])
    log = [json.loads(x) for x in (d / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["status"] == 0 and not log[-1]["ok"]
    undo = json.loads((d / "undo.json").read_text(encoding="utf-8"))
    assert [o["visit_id"] for o in undo["ops"]] == ["v2", "v1"]  # 分からない分も戻し方は残す
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["applied"] == 1 and meta["unknown"] == 1
    text = summary_ja(res)
    assert "1 / 3 件を反映" in text and "分からない操作: 1 件" in text and "rk undo" in text


def test_apply_then_undo_roundtrip(cfg, reg):
    _backup(cfg)
    api = FakeApi()
    res = apply_plan(cfg, _plan(_op("v1", "S9"), _op("v2", "S8")), api, approved="今泉 10/12 14:00", registry=reg, now=NOW)
    assert res["ok"] and res["applied"] == 2
    assert api.staff == {"v1": "S9", "v2": "S8"}
    d = Path(res["run_dir"])
    assert d.name == "20261012-010000-staff-absence"
    for f in ("plan.json", "meta.json", "before.jsonl", "log.jsonl", "undo.json"):
        assert (d / f).exists(), f
    before = [json.loads(x) for x in (d / "before.jsonl").read_text(encoding="utf-8").splitlines()]
    assert before[0]["before"] == {"staff": "S1"}
    undo = json.loads((d / "undo.json").read_text(encoding="utf-8"))
    assert [o["visit_id"] for o in undo["ops"]] == ["v2", "v1"]  # 逆順
    assert undo["not_undoable"] == []
    assert json.loads((d / "meta.json").read_text(encoding="utf-8"))["approved"] == "今泉 10/12 14:00"
    assert all(r["ok"] for r in verify_plan(json.loads((d / "plan.json").read_text(encoding="utf-8")), api, reg))

    res2 = undo_run(cfg, res["run_id"], api, approved="今泉 10/12 14:10", registry=reg,
                    now=NOW + timedelta(minutes=10))
    assert res2["ok"] and api.staff == {"v1": "S1", "v2": "S2"}
    assert (d / "undone.json").exists()
    with pytest.raises(RkError, match="もう元に戻して"):
        undo_run(cfg, res["run_id"], api, approved="x y", registry=reg, now=NOW + timedelta(minutes=11))


def test_apply_stops_at_first_error(cfg, reg):
    _backup(cfg)
    api = FakeApi(fail_on="v2")
    plan = _plan(_op("v1", "S9"), _op("v2", "S8"), _op("v1", "S7"))
    res = apply_plan(cfg, plan, api, approved="今泉", registry=reg, now=NOW)
    assert not res["ok"] and res["applied"] == 1 and "2 件目" in res["error"]
    assert api.staff["v1"] == "S9"  # 3 件目は呼ばれていない
    d = Path(res["run_dir"])
    log = [json.loads(x) for x in (d / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["ok"] for r in log] == [True, False] and log[1]["status"] == 422
    undo = json.loads((d / "undo.json").read_text(encoding="utf-8"))
    assert [o["visit_id"] for o in undo["ops"]] == ["v1"]


def test_check_response_problem_stops(cfg, reg):
    class Picky(SetStaff):
        name = "t-picky"

        def check_response(self, op, response):
            return ["visits_moved が 0"]
    reg.add(Picky)
    _backup(cfg)
    res = apply_plan(cfg, _plan(_op("v1", "S9", "t-picky"), _op("v2", "S8")), FakeApi(), approved="今泉",
                     registry=reg, now=NOW)
    assert not res["ok"] and res["applied"] == 1 and "visits_moved" in res["error"]


def test_undo_refuses_not_undoable_unless_partial(cfg, reg):
    _backup(cfg)
    api = FakeApi()
    res = apply_plan(cfg, _plan(_op("v1", "S9"), _op("v2", "S8", "t-one-way")), api, approved="今泉",
                     registry=reg, now=NOW)
    assert res["ok"] and len(res["not_undoable"]) == 1
    later = NOW + timedelta(minutes=3)
    with pytest.raises(RkError, match="--partial"):
        undo_run(cfg, res["run_id"], api, approved="今泉", registry=reg, now=later)
    res2 = undo_run(cfg, res["run_id"], api, approved="今泉", partial=True, registry=reg, now=later)
    assert res2["ok"] and api.staff == {"v1": "S1", "v2": "S8"}


def test_undo_rejects_bad_run_id(cfg, reg):
    with pytest.raises(RkError):
        undo_run(cfg, "../etc", FakeApi(), approved="今泉", registry=reg)


# ---------- backup ----------
def test_backup_command_and_record(cfg):
    calls = []

    def runner(cmd, input_bytes=None, timeout=0):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"3800000\n-rw-r--r-- 1 root root 3800000 x.sql.gz\n", b"")
    rec = run_backup(cfg, "w43-absence", runner=runner, now=NOW)
    assert rec["path"] == "/opt/carelink/backups/agent-w43-absence-20261012-0100.sql.gz"
    script = calls[0][-1]
    assert calls[0][0] == "ssh"
    assert "pg_dump -U carelink -d carelink" in script and "gzip > backups/agent-w43-absence-20261012-0100.sql.gz" in script
    assert "pipefail" in script and "cd /opt/carelink" in script
    assert recent_backup(cfg, now=NOW + timedelta(minutes=10))["label"] == "w43-absence"
    assert recent_backup(cfg, now=NOW + timedelta(minutes=31)) is None
    with pytest.raises(RkError):
        run_backup(cfg, "Bad Label", runner=runner, now=NOW)


def test_backup_too_small_or_failed(cfg):
    def small(cmd, input_bytes=None, timeout=0):
        return subprocess.CompletedProcess(cmd, 0, b"20\n", b"")

    def fail(cmd, input_bytes=None, timeout=0):
        return subprocess.CompletedProcess(cmd, 1, b"", b"pg_dump: error")
    with pytest.raises(RkError, match="小さすぎ"):
        run_backup(cfg, "x", runner=small, now=NOW)
    with pytest.raises(RkError, match="失敗"):
        run_backup(cfg, "x", runner=fail, now=NOW)
    assert recent_backup(cfg, now=NOW) is None


# ---------- ready: false・作った後に予定が変わった ----------
def test_plan_not_ready_is_refused_with_blockers(cfg, reg):
    p = _plan(_op("v1", "S9"))
    p["ready"] = False
    p["blockers"] = ["新人が担当になっています", "決まりの検査で問題が 2 件あります"]
    errs = check_plan(p, reg)
    assert any("反映できません" in e for e in errs)
    assert "反映できない理由: 新人が担当になっています" in errs
    assert "反映できない理由: 決まりの検査で問題が 2 件あります" in errs
    _backup(cfg)
    api = FakeApi()
    with pytest.raises(RkError, match="新人が担当"):
        apply_plan(cfg, p, api, approved="今泉", registry=reg, now=NOW)
    assert api.calls == []
    p["ready"] = True
    p["blockers"] = []
    assert check_plan(p, reg) == []


def test_main_plan_check_prints_blockers(tmp_path, capsys):
    from rk import cli
    p = _plan({"op": "noop", "label": "見本"})
    p.update(ready=False, blockers=["コースが決まらない日があります"])
    f = tmp_path / "p.json"
    f.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["plan", "check", str(f)]) == 1
    assert "コースが決まらない日があります" in capsys.readouterr().out


def _week_json(start="09:30"):
    return {"extracted_at": "x", "visits": [
        {"id": "v1", "visit_date": "2026-10-20", "start_time": start + ":00", "status": "planned",
         "course_office": "稲", "course_code": "A", "primary_staff_id": "s1"}]}


def test_check_basis_detects_change(cfg):
    pytest.importorskip("tools.leveling.plan")
    from rk.applyrun import BASIS_CHANGED, check_basis
    from tools.leveling.plan import basis_hash
    p = _plan(_op("v1", "S9"))
    p["target"] = {"iso_year": 2026, "iso_week": 43, "week_start": "2026-10-19"}
    assert check_basis(cfg, p, run_sql=lambda sql: 1 / 0) is None  # basis_hash が無い案は読まない
    p["source"] = {"basis_hash": basis_hash(_week_json())}
    seen = []

    def run_sql(sql):
        seen.append(sql)
        return [{"json_build_object": json.dumps(_week_json(state["start"]))}]
    state = {"start": "09:30"}
    assert check_basis(cfg, p, run_sql=run_sql) == p["source"]["basis_hash"]
    assert "2026-10-19" in seen[0] and "2026-10-25" in seen[0]
    state["start"] = "10:00"
    with pytest.raises(RkError, match=BASIS_CHANGED):
        check_basis(cfg, p, run_sql=run_sql)


def test_main_apply_refuses_changed_basis_before_connecting(tmp_path, monkeypatch, capsys):
    from rk import applyrun, cli
    monkeypatch.setenv("RK_RUNS_DIR", str(tmp_path / "runs"))
    p = _plan({"op": "noop", "label": "見本"})
    p["source"] = {"basis_hash": "aaaa"}
    f = tmp_path / "p.json"
    f.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(applyrun, "current_basis_hash", lambda cfg, plan, run_sql=None: "bbbb")
    _rec = tmp_path / "runs"
    _rec.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt, timezone as _tz
    (_rec / "backups.jsonl").write_text(json.dumps({"label": "t", "path": "/x.sql.gz", "at_utc": _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + chr(10), encoding="utf-8")  # rk backup の記録

    def boom(args):
        raise AssertionError("つないではいけない")
    monkeypatch.setattr(cli, "_open_api", boom)
    assert cli.main(["apply", str(f), "--approved", "今泉", "--backup", "/x.sql.gz"]) == 1
    assert "案を作った後に予定が変わりました" in capsys.readouterr().err


def test_summary_ja_undoable_counts():
    from rk.applyrun import summary_ja
    base = {"ok": True, "run_id": "20261012-062217-copy-week", "total": 1, "applied": 1, "unknown": 0,
            "backup": "/opt/carelink/backups/agent-x.sql.gz"}
    none = summary_ja({**base, "not_undoable": [{"i": 0, "op": "copy-week", "label": "週を写す"}]})
    assert "rk undo" not in none
    assert "この反映は API では元に戻せません（戻すにはバックアップから: /opt/carelink/backups/agent-x.sql.gz）" in none
    part = summary_ja({**base, "total": 3, "applied": 3, "not_undoable": [{"i": 2, "op": "return-to-pool", "label": "x"}]})
    assert "rk undo" in part and "戻せる操作: 2 件・戻せない操作: 1 件" in part and "--partial" in part
    full = summary_ja({**base, "not_undoable": []})
    assert "rk undo" in full and "戻せない" not in full
