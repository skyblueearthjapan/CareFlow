"""休みの種類と境目が backend の正典（backend/app/core/leave.py）とそろっているか（休み設計 2026-10-20 付録 A-3）。

ならしの道具（docs/tools/week-leveling/solver.py）と rk（agent/rk/ops/staff.py）は backend の無い箱で動くので、
値を写して持っている。ここでは道具を import せず（OR-Tools が要るため）文で値を読み、backend の値と比べる
（backend が無い構成では skip）。
エージェントの SQL・道具が終日の休みを ``'off'`` だけで見ていない（有給が漏れない）こと、
明示の担当なしをコースの担当に読み替えていないことも確かめる。
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
REPO = AGENT.parent
LEVELING = REPO / "docs" / "tools" / "week-leveling"
SOLVER = LEVELING / "solver.py"
RK_STAFF = AGENT / "rk" / "ops" / "staff.py"
CORE_LEAVE = REPO / "backend" / "app" / "core" / "leave.py"


@pytest.fixture(scope="module")
def core():
    if not CORE_LEAVE.exists():
        pytest.skip("backend/app/core/leave.py が無い構成（エージェントの箱など）")
    spec = importlib.util.spec_from_file_location("careflow_core_leave", CORE_LEAVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 標準ライブラリだけで読める
    return mod


def _constants(path: Path, names: set[str]) -> dict:
    out = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name not in names:
                continue
            if isinstance(node.value, ast.Call):  # frozenset({...})
                out[name] = frozenset(ast.literal_eval(node.value.args[0]))
            elif isinstance(node.value, ast.Constant):
                out[name] = node.value.value
            else:
                out[name] = eval(compile(ast.Expression(node.value), str(path), "eval"))  # noqa: S307 — 数式だけ
    return out


def _minutes(t) -> int:
    return t.hour * 60 + t.minute


def test_leveling_boundaries_match_backend(core):
    got = _constants(SOLVER, {"AM_OFF_UNTIL", "PM_OFF_FROM", "FULL_DAY_OFF"})
    assert got["AM_OFF_UNTIL"] == _minutes(core.AM_OFF_UNTIL) == 13 * 60
    assert got["PM_OFF_FROM"] == _minutes(core.PM_OFF_FROM) == 12 * 60 + 30
    assert got["FULL_DAY_OFF"] == core.FULL_DAY_OFF == {"off", "paid_off"}


def test_rk_boundaries_match_backend(core):
    got = _constants(RK_STAFF, {"AM_OFF_UNTIL", "PM_OFF_FROM", "LEAVE_TYPE_API"})
    assert got["AM_OFF_UNTIL"] == core.AM_OFF_UNTIL.strftime("%H:%M")
    assert got["PM_OFF_FROM"] == core.PM_OFF_FROM.strftime("%H:%M")


def test_rk_half_day_rule_matches_backend(core):
    from datetime import time

    from rk.ops.staff import hits_half_day_leave

    label = {"am_off": "午前休", "pm_off": "午後休"}
    cases = [(time(h, m), e) for h in range(8, 18) for m in (0, 29, 30, 31, 59)
             for e in (None, time(12, 29), time(12, 30), time(12, 31), time(13, 0), time(18, 0))]
    for kind, lab in label.items():
        for st, en in cases:
            want = core.visit_hits_leave(kind, st, en)
            got = hits_half_day_leave(lab, st.strftime("%H:%M:%S"), en.strftime("%H:%M:%S") if en else None)
            assert got is want, (kind, st, en)


def _py_files():
    yield from sorted(LEVELING.glob("**/*.py"))
    yield from sorted((AGENT / "tools").glob("**/*.py"))


def test_leveling_has_no_noon_boundary_left():
    assert "NOON" not in SOLVER.read_text(encoding="utf-8")
    report = (LEVELING / "report.py").read_text(encoding="utf-8")
    assert "12 * 60" not in report


def test_tools_full_day_includes_paid_leave():
    """docs/tools/week-leveling と agent/tools の Python で、休みの種類を "off" だけで比べていない。"""
    pat = re.compile(r"""override_type["']?\]?\s*(==|!=)\s*["']off["']""")
    bad = [f"{f.relative_to(REPO)}:{i}: {line.strip()}"
           for f in _py_files() for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
           if pat.search(line)]
    assert bad == [], "終日の休みは FULL_DAY_OFF（公休・有給）で見る:\n" + "\n".join(bad)


def test_tools_owner_does_not_read_explicit_unassigned_as_course_staff():
    """主担当が空のときコースの担当へ読み替える式は、manual_staff_override を見ていること（設計 §3-2）。"""
    pat = re.compile(r"""primary_staff_id["']?\]?\s*or\s*[^\n]*course_staff_id""")
    bad = []
    for f in _py_files():
        text = f.read_text(encoding="utf-8")
        for m in pat.finditer(text):
            if "manual_staff_override" not in m.group(0):
                bad.append(f"{f.relative_to(REPO)}: {m.group(0).strip()}")
    assert bad == []


def test_agent_sql_full_day_includes_paid_leave():
    bad = []
    for f in sorted((AGENT / "sql").glob("*.sql")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("--"):
                continue
            if re.search(r"(=|<>|!=)\s*'off'", line) or re.search(r"\bin\s*\(\s*'off'\s*\)", line, re.I):
                bad.append(f"{f.name}:{i}: {line.strip()}")
    assert bad == [], "終日の休みは in ('off', 'paid_off') で見る:\n" + "\n".join(bad)


def test_agent_sql_owner_does_not_read_explicit_unassigned_as_course_staff():
    """担当の式で、コースの担当へ読み替えるときは必ず manual_staff_override が偽のときだけ（設計 §3-2）。"""
    bad = []
    for f in sorted((AGENT / "sql").glob("*.sql")):
        text = re.sub(r"\s+", " ", f.read_text(encoding="utf-8"))
        for m in re.finditer(r"coalesce\((\w+)\.primary_staff_id,(.{0,160}?)\)", text):
            alias, rest = m.group(1), m.group(2)
            if "assigned_staff_id" in rest and f"not {alias}.manual_staff_override" not in rest:
                bad.append(f"{f.name}: {m.group(0)}")
    assert bad == []
