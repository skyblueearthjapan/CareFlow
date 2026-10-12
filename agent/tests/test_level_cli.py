"""rk level のコマンドの登録と、読む → 計算 → 案 の流れ（計算は偽のプロセス・本番に触らない）。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
FIX = AGENT / "tools" / "leveling" / "fixtures"

cli = pytest.importorskip("rk.cli")


def test_level_commands_registered():
    ap = cli.build_parser()
    a = ap.parse_args(["level", "week", "--week", "2026-10-19", "--off", "T02@2026-10-20", "--allow-over", "1",
                       "--add-special", "--seconds", "60"])
    assert a.func.__name__ == "cmd_level_week"
    assert a.off == ["T02@2026-10-20"] and a.allow_over == 1 and a.add_special and a.seconds == 60
    a = ap.parse_args(["level", "day", "--date", "2026-10-22", "--off", "T02"])
    assert a.func.__name__ == "cmd_level_day" and a.date == "2026-10-22"


def test_level_day_needs_off(capsys):
    assert cli.main(["level", "day", "--date", "2026-10-22"]) == 1
    assert "--off" in capsys.readouterr().err


def test_level_day_passes_day_and_off(monkeypatch, capsys):
    import tools.leveling.run as run

    got = {}

    def fake_level(cfg, **kw):
        got.update(kw)
        return {"plan_path": "x.json", "out_dir": "d", "plan": {"title": "t", "ready": True, "blockers": [],
                                                                 "summary": [], "checks": [], "warnings": [], "ops": []}}

    monkeypatch.setattr(run, "level", fake_level)
    assert cli.main(["level", "day", "--date", "2026-10-22", "--off", "T02"]) == 0
    assert got["days"] == ["2026-10-22"] and got["off"] == ["T02@2026-10-22"] and got["kind"] == "staff-absence"
    out = json.loads(capsys.readouterr().out)
    assert out["plan"] == "x.json" and out["ready"] is True


FAKE_SOLVER = r"""
import json, shutil, sys
args = sys.argv[1:]
out = args[args.index('--out') + 1]
assert '--off' in args and 'T05' in args  # 新人は自動で外す
shutil.copy(%r, out + '/result.json')
"""


def test_level_end_to_end_with_fake_solver(tmp_path):
    from rk.config import Config
    from tools.leveling.run import level

    src = tmp_path / "in"
    src.mkdir()
    shutil.copy(FIX / "sample_week.json", src / "week.json")
    shutil.copy(FIX / "sample_extra.json", src / "agent_extra.json")
    (src / "history.json").write_text("[]", encoding="utf-8")
    week = json.loads((src / "week.json").read_text(encoding="utf-8"))
    conf = {"cap_regular": 6, "regular": {"T01": "X", "T02": "X", "T03": "Y", "T05": "X"}, "manager": {"T09": ["X", "Y"]}}
    (tmp_path / "config.json").write_text(json.dumps(conf), encoding="utf-8")
    cfg = Config(runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports")
    code = FAKE_SOLVER % str(FIX / "sample_result.json")
    res = level(cfg, week="2026-10-21", from_dir=str(src), config_path=str(tmp_path / "config.json"),
                solver_cmd=[sys.executable, "-c", code], log=lambda m: None)
    p = Path(res["plan_path"])
    assert p.parent == tmp_path / "runs" / "plans" and p.exists()
    plan = json.loads(p.read_text(encoding="utf-8"))
    assert plan["kind"] == "week-leveling" and plan["target"]["iso_week"] == 43
    assert plan["source"]["extracted_at"] == week["extracted_at"]
    assert Path(res["out_dir"]).is_relative_to(tmp_path / "reports")


def test_level_rejects_day_outside_week(tmp_path):
    from rk.config import Config
    from tools.leveling.run import LevelError, level

    cfg = Config(runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports")
    with pytest.raises(LevelError):
        level(cfg, week="2026-10-19", days=["2026-10-27"], config_path=str(tmp_path / "none.json"))


# ---- config.local.json が無いとき（中継の箱の中）: 職員の記録から作る ----

ROSTER = [  # rk の形（json_agg を 1 行 1 列で返す）・架空の職員
    {"code": "T01", "role": "staff", "is_trainee": False, "office": "X", "support_offices": []},
    {"code": "T02", "role": "staff", "is_trainee": False, "office": "X", "support_offices": ["X"]},
    {"code": "T03", "role": "staff", "is_trainee": False, "office": "Y", "support_offices": []},
    {"code": "T05", "role": "staff", "is_trainee": True, "office": "X", "support_offices": []},
    {"code": "T09", "role": "manager", "is_trainee": False, "office": "X", "support_offices": ["Y", "X"]},
    {"code": "T10", "role": "staff", "is_trainee": False, "office": None, "support_offices": []},  # 拠点なし → 入れない
]


def test_config_from_staff_matches_config_local_shape():
    from tools.leveling.extract_rk import config_from_staff

    conf = config_from_staff(ROSTER, 5)
    assert conf == {"cap_regular": 5, "regular": {"T01": "X", "T02": "X", "T03": "Y", "T05": "X"},
                    "manager": {"T09": ["X", "Y"]}}


def test_config_from_db_reads_one_select():
    from tools.leveling.extract_rk import config_from_db

    seen = []

    def fake_sql(sql):
        seen.append(sql)
        return [{"staff": json.dumps(ROSTER)}]

    conf = config_from_db(fake_sql)
    assert len(seen) == 1 and seen[0].lstrip().lower().startswith("select") and ";" not in seen[0]
    assert conf["cap_regular"] == 6 and conf["manager"] == {"T09": ["X", "Y"]}


FAKE_SOLVER_CFG = r"""
import json, shutil, sys
args = sys.argv[1:]
out = args[args.index('--out') + 1]
conf = json.load(open(args[args.index('--config') + 1], encoding='utf-8'))
assert conf['regular']['T03'] == 'Y' and conf['manager'] == {'T09': ['X', 'Y']} and conf['cap_regular'] == 7
assert 'T05' in args  # 新人は職員の記録から作った config でも外す
shutil.copy(%r, out + '/result.json')
"""


def test_level_relay_mode_builds_config_from_staff(tmp_path, monkeypatch):
    from rk.config import Config
    from tools.leveling.run import level

    monkeypatch.setenv("RK_MODE", "relay")
    monkeypatch.setenv("RK_LEVEL_CAP_REGULAR", "7")
    src = tmp_path / "in"
    src.mkdir()
    shutil.copy(FIX / "sample_week.json", src / "week.json")
    shutil.copy(FIX / "sample_extra.json", src / "agent_extra.json")
    (src / "history.json").write_text("[]", encoding="utf-8")
    cfg = Config(runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports")
    out = tmp_path / "out"
    res = level(cfg, week="2026-10-21", from_dir=str(src), out=str(out),
                run_sql=lambda sql: [{"staff": ROSTER}],
                solver_cmd=[sys.executable, "-c", FAKE_SOLVER_CFG % str(FIX / "sample_result.json")], log=lambda m: None)
    assert json.loads((out / "config.json").read_text(encoding="utf-8"))["regular"]["T01"] == "X"
    assert "server" not in json.loads((out / "config.json").read_text(encoding="utf-8"))
    assert Path(res["plan_path"]).exists()


def test_level_explicit_config_wins_in_relay_mode(tmp_path, monkeypatch):
    from tools.leveling.run import resolve_config

    monkeypatch.setenv("RK_MODE", "relay")
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"cap_regular": 6, "regular": {"A": "X"}, "manager": {}}), encoding="utf-8")

    def no_sql(sql):
        raise AssertionError("--config があるときは職員の記録を読まない")

    conf, path = resolve_config(str(p), tmp_path / "out", None, no_sql, log=lambda m: None)
    assert path == p and conf["regular"] == {"A": "X"}


def test_level_relay_mode_ignores_config_local(tmp_path, monkeypatch):
    import tools.leveling.run as run

    local = tmp_path / "config.local.json"
    local.write_text(json.dumps({"server": "root@x", "regular": {}, "manager": {}}), encoding="utf-8")
    monkeypatch.setattr(run, "DEFAULT_CONFIG", local)
    monkeypatch.setenv("RK_MODE", "relay")
    monkeypatch.delenv("RK_LEVEL_CAP_REGULAR", raising=False)
    conf, path = run.resolve_config(None, tmp_path / "out", None, lambda sql: [{"staff": ROSTER}], log=lambda m: None)
    assert path == tmp_path / "out" / "config.json" and conf["cap_regular"] == 6 and "server" not in conf
    monkeypatch.setenv("RK_MODE", "pc")
    conf, path = run.resolve_config(None, tmp_path / "out", None, None, log=lambda m: None)
    assert path == local  # PC で config.local.json があれば今までどおり


def test_level_config_needs_some_staff(tmp_path, monkeypatch):
    from tools.leveling.run import LevelError, resolve_config

    monkeypatch.setenv("RK_MODE", "relay")
    with pytest.raises(LevelError):
        resolve_config(None, tmp_path / "out", None, lambda sql: [{"staff": []}], log=lambda m: None)
