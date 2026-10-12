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
