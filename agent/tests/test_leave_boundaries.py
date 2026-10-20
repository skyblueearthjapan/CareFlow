"""休みの種類と境目が backend の正典（backend/app/core/leave.py）とそろっているか（休み設計 2026-10-20 付録 A-3）。

ならしの道具（docs/tools/week-leveling/solver.py）は backend の無い箱で動くので、値を写して持っている。
ここでは道具を import せず（OR-Tools が要るため）文で値を読み、backend の値と比べる。
エージェントの SQL が終日の休みを ``= 'off'`` だけで見ていない（有給が漏れない）ことも確かめる。
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
REPO = AGENT.parent
SOLVER = REPO / "docs" / "tools" / "week-leveling" / "solver.py"
CORE_LEAVE = REPO / "backend" / "app" / "core" / "leave.py"


def _core():
    spec = importlib.util.spec_from_file_location("careflow_core_leave", CORE_LEAVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 標準ライブラリだけで読める
    return mod


def _solver_constants() -> dict:
    out = {}
    for node in ast.parse(SOLVER.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("AM_OFF_UNTIL", "PM_OFF_FROM"):
                out[name] = eval(compile(ast.Expression(node.value), str(SOLVER), "eval"))  # noqa: S307 — 数式だけ
            elif name == "FULL_DAY_OFF":
                out[name] = frozenset(ast.literal_eval(node.value.args[0]))
    return out


def _minutes(t) -> int:
    return t.hour * 60 + t.minute


def test_leveling_boundaries_match_backend():
    core, got = _core(), _solver_constants()
    assert got["AM_OFF_UNTIL"] == _minutes(core.AM_OFF_UNTIL) == 13 * 60
    assert got["PM_OFF_FROM"] == _minutes(core.PM_OFF_FROM) == 12 * 60 + 30
    assert got["FULL_DAY_OFF"] == core.FULL_DAY_OFF == {"off", "paid_off"}


def test_leveling_has_no_noon_boundary_left():
    text = SOLVER.read_text(encoding="utf-8")
    assert "NOON" not in text
    report = (SOLVER.parent / "report.py").read_text(encoding="utf-8")
    assert "12 * 60" not in report and '== "off"' not in report


def test_agent_sql_full_day_includes_paid_leave():
    bad = []
    for f in sorted((AGENT / "sql").glob("*.sql")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("--"):
                continue
            if re.search(r"(=|<>|!=)\s*'off'", line):
                bad.append(f"{f.name}:{i}: {line.strip()}")
    assert bad == [], "終日の休みは in ('off', 'paid_off') で見る:\n" + "\n".join(bad)


def test_agent_sql_owner_does_not_read_explicit_unassigned_as_course_staff():
    """担当の式で、コースの担当へ読み替えるときは必ず manual_staff_override が偽のときだけ（設計 §3-2）。"""
    bad = []
    for f in sorted((AGENT / "sql").glob("*.sql")):
        text = re.sub(r"\s+", " ", f.read_text(encoding="utf-8"))
        for m in re.finditer(r"coalesce\(v\.primary_staff_id,(.{0,160}?)\)", text):
            if "assigned_staff_id" in m.group(1) and "not v.manual_staff_override" not in m.group(1):
                bad.append(f"{f.name}: {m.group(0)}")
    assert bad == []
