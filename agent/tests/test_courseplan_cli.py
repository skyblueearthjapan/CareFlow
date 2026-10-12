"""rk courseplan のコマンドの登録と、読む → 計算 → 片付け → 案 の流れ（架空のデータ・本番に触らない）。

計算は偽のプロセス（今の型をそのまま返す）で流す。OR-Tools の本物の計算は uv か ortools があるときだけ流す。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
FIX = AGENT / "tools" / "courseplan" / "fixtures"

from rk import cli  # noqa: E402
from rk.config import Config  # noqa: E402
from tools.courseplan.run import CoursePlanError, courseplan, parse_weekdays  # noqa: E402

# 今の型（コースが開いていれば）をそのまま返す偽の計算
FAKE = r"""
import json, sys
a = sys.argv
inp, out = a[a.index('--in') + 1], a[a.index('--out') + 1]
ps = json.load(open(inp, encoding='utf-8'))
sols = []
for p in ps:
    ids = {c['id'] for c in p['courses']}
    asg = {s['key']: {'course': s['cur_course'] if s['cur_course'] in ids else p['courses'][0]['id'], 'start': s['start']}
           for s in p['slots']}
    sols.append({'office': p['office'], 'weekday': p['weekday'], 'assign': asg, 'dropped': [], 'lunch': 45, 'tries': []})
json.dump(sols, open(out, 'w', encoding='utf-8'), ensure_ascii=False)
"""


def cfg(tmp_path):
    return Config(runs_dir=tmp_path / "runs", reports_dir=tmp_path / "reports")


def test_registered_and_parsed():
    a = cli.build_parser().parse_args(["courseplan", "--office", "甲", "--weekday", "mon", "--weekday", "tue,wed",
                                       "--seconds", "5", "--max-changes", "4", "--red", "ignore"])
    assert a.func.__name__ == "cmd_courseplan"
    assert a.weekday == ["mon", "tue,wed"] and a.max_changes == 4 and a.red == "ignore" and a.no_pref == "fixed"


def test_parse_weekdays():
    assert parse_weekdays(["mon", "tue,wed"]) == [0, 1, 2]
    assert parse_weekdays(["土"]) == [5] and parse_weekdays(None) == [0, 1, 2, 3, 4, 5]
    with pytest.raises(CoursePlanError):
        parse_weekdays(["sun"])
    with pytest.raises(CoursePlanError):
        parse_weekdays(["xyz"])


def test_flow_with_fake_solver(tmp_path):
    res = courseplan(cfg(tmp_path), office="甲", weekdays=["mon"], from_raw=str(FIX / "sample_raw.json"),
                     out=str(tmp_path / "out"), solver_cmd=[sys.executable, "-c", FAKE], log=lambda m: None)
    plan = res["plan"]
    assert Path(res["plan_path"]).exists() and (tmp_path / "out" / "metrics.json").exists()
    assert plan["ready"] is False and plan["target"]["weekdays"] == "月"
    # 偽の計算はコースなしの枠だけを A に置く → 変えるのはその 1 名だけ
    assert [o["patient_id"] for o in plan["ops"]] == ["P4"]


def test_out_dir_inside_repo_must_be_reports(tmp_path):
    with pytest.raises(CoursePlanError):
        courseplan(cfg(tmp_path), office="甲", weekdays=["mon"], from_raw=str(FIX / "sample_raw.json"),
                   out=str(AGENT / "tmp-out"), solver_cmd=[sys.executable, "-c", FAKE], log=lambda m: None)


def test_unknown_office(tmp_path):
    with pytest.raises(CoursePlanError):
        courseplan(cfg(tmp_path), office="無い", weekdays=["mon"], from_raw=str(FIX / "sample_raw.json"),
                   out=str(tmp_path / "o"), solver_cmd=[sys.executable, "-c", FAKE], log=lambda m: None)


HAS_SOLVER = bool(importlib.util.find_spec("ortools")) or shutil.which("uv") is not None


@pytest.mark.skipif(not HAS_SOLVER, reason="OR-Tools も uv も無い環境")
def test_real_solver_resolves_conflicts(tmp_path):
    res = courseplan(cfg(tmp_path), office="甲", weekdays=["mon"], from_raw=str(FIX / "sample_raw.json"),
                     out=str(tmp_path / "out"), seconds=2, log=lambda m: None)
    (m,) = res["plan"]["metrics"]
    assert m["before"]["conflicts"] == 2 and m["after"]["conflicts"] == 0
    assert m["after"]["ng_or_sex"] == 0 and m["after"]["no_course"] == 0 and m["dropped"] == 0
    sol = json.loads((tmp_path / "out" / "solution.json").read_text(encoding="utf-8"))[0]
    assert sol["assign"]["F4"]["start"] == 630  # 赤ピンの時刻は動かない
    assert sol["assign"]["F8"]["start"] == sol["assign"]["F9"]["start"]  # 2 名体制は同じ時刻
    assert sol["assign"]["F8"]["course"] != sol["assign"]["F9"]["course"]  # 別のコース
    assert m["changes"] <= 4


def test_red_cleanup_done_flag_defaults_false():
    p = cli.build_parser()
    assert p.parse_args(["courseplan"]).red_cleanup_done is False
    assert p.parse_args(["courseplan", "--red-cleanup-done"]).red_cleanup_done is True
