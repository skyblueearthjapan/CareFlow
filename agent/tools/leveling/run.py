"""rk level の中身: 読む（rk の SQL）→ 計算（solve.py を uv の別プロセスで）→ 案（plan.json）。本番へは書かない。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from tools.leveling import DEFAULT_CONFIG, HERE, REPO_ROOT
from tools.leveling.plan import build_plan, day_label, short_name

UV_SOLVER = ["uv", "run", "-q", "--python", "3.12", "--with", "ortools", "--with", "openpyxl", "--with", "httpx", "python"]


class LevelError(Exception):
    """利用者に日本語で見せて終わるエラー。"""


def monday_of(any_day: date) -> date:
    return any_day - timedelta(days=any_day.weekday())


def solver_prefix() -> list[str]:
    """ortools が今の Python にあればそれで、無ければ uv の使い捨て環境で動かす。"""
    if os.environ.get("RK_LEVEL_PYTHON"):
        return [os.environ["RK_LEVEL_PYTHON"]]
    if importlib.util.find_spec("ortools") and importlib.util.find_spec("openpyxl"):
        return [sys.executable]
    if shutil.which("uv") is None:
        raise LevelError("ならしの計算には uv（または ortools の入った Python・RK_LEVEL_PYTHON）が要ります")
    return list(UV_SOLVER)


def tag_for(days, off, allow_over, balance, drop, add_special) -> str:
    return "".join([
        f"-{min(days):%m%d}" if days else "",
        "-off" if off else "",
        f"-over{allow_over}" if allow_over else "",
        "-bal" if balance else "",
        "-drop" if drop else "",
        "-sp" if add_special else "",
    ])


def check_out_dir(out_dir: Path) -> None:
    """利用者名入りの出力は docs/reports の下だけ（git に入れない）。"""
    reports = (REPO_ROOT / "docs" / "reports").resolve()
    o = out_dir.resolve()
    if o.is_relative_to(REPO_ROOT.resolve()) and not o.is_relative_to(reports):
        raise LevelError("出力先がリポジトリの中なら docs/reports の下にしてください（利用者名を git に入れないため）")


def level(
    cfg,
    *,
    week: str,
    days: list[str] | None = None,
    off: list[str] | None = None,
    allow_over: int = 0,
    over_before_manager: bool = False,
    balance: bool = False,
    drop: list[str] | None = None,
    add_special: bool = False,
    seconds: int = 30,
    no_app_check: bool = False,
    no_pdf: bool = False,
    from_dir: str | None = None,
    config_path: str | None = None,
    out: str | None = None,
    include_trainee: bool = False,
    kind: str = "week-leveling",
    title: str | None = None,
    run_sql: Callable[[str], Any] | None = None,
    solver_cmd: list[str] | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    try:
        monday = monday_of(date.fromisoformat(week))
        day_set = sorted({date.fromisoformat(x) for x in days or []})
    except ValueError as e:
        raise LevelError(f"日付は YYYY-MM-DD で書いてください: {e}") from None
    sunday = monday + timedelta(days=6)
    iso_year, iso_week, _ = monday.isocalendar()
    off = list(off or [])
    for x in day_set + [date.fromisoformat(v.partition("@")[2]) for v in off if "@" in v]:
        if not monday <= x <= sunday:
            raise LevelError(f"{x} は {monday}〜{sunday} の週の外です")

    cpath = Path(config_path) if config_path else DEFAULT_CONFIG
    if not cpath.exists():
        raise LevelError(f"{cpath} がありません（config.example.json を写して職員コードで書いてください・git 管理外）")
    config = json.loads(cpath.read_text(encoding="utf-8"))

    now = datetime.now()
    tag = tag_for(day_set, off, allow_over, balance, drop, add_special)
    out_dir = Path(out) if out else Path(cfg.reports_dir) / f"{now:%Y-%m-%d}" / f"{now:%H%M}-week{iso_week}-leveling{tag}"
    check_out_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 読む（本番は読むだけ）
    if from_dir:
        in_dir = Path(from_dir)
        if not (in_dir / "week.json").exists():
            raise LevelError(f"{in_dir} に week.json がありません")
    else:
        from tools.leveling.extract_rk import extract_week, rk_runner

        log(f"本番から読み取り中（{monday} の週・読み取り専用）…")
        extract_week(run_sql or rk_runner(cfg), monday, out_dir)
        in_dir = out_dir
    week_data = json.loads((in_dir / "week.json").read_text(encoding="utf-8"))
    extra_path = in_dir / "agent_extra.json"
    extra = json.loads(extra_path.read_text(encoding="utf-8")) if extra_path.exists() else None

    if title is None and kind == "staff-absence" and day_set:
        title = day_title(day_set[0], [v.partition("@")[0] for v in off], week_data.get("staff"))

    # 新人は道具が見ない（is_trainee）→ config に入っていれば週全部を休みとして外す
    if not include_trainee:
        known = set(config.get("regular") or {}) | set(config.get("manager") or {})
        off_codes = {v.partition("@")[0] for v in off}
        for s in week_data.get("staff") or []:
            if s.get("is_trainee") and s["code"] in known and s["code"] not in off_codes:
                off.append(s["code"])
                log(f"新人 {short_name(s['name'])}（{s['code']}）は同行のみなので外して計算します")

    # 2) 計算（ortools の入った別プロセス）
    cmd = list(solver_cmd or solver_prefix()) + [
        str(HERE / "solve.py"), "--week", str(monday), "--in", str(in_dir), "--out", str(out_dir),
        "--config", str(cpath), "--seconds", str(seconds),
    ]
    for x in day_set:
        cmd += ["--day", str(x)]
    for x in off:
        cmd += ["--off", x]
    for x in drop or []:
        cmd += ["--drop", x]
    if allow_over:
        cmd += ["--allow-over", str(allow_over)]
    for flag, on in (("--over-before-manager", over_before_manager), ("--balance", balance),
                     ("--add-special", add_special), ("--no-app-check", no_app_check), ("--no-pdf", no_pdf)):
        if on:
            cmd.append(flag)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    log(f"計算中（1 日 {seconds} 秒ほど × {len(day_set) or 7} 日）…")
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env)
    if res.returncode != 0:
        raise LevelError(f"ならしの計算が止まりました（終了コード {res.returncode}）。上の表示を確かめてください")
    result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))

    # 3) 案
    summary = result.get("summary") or {}
    files = {"dir": str(out_dir), **(summary.get("files") or {})}
    plan = build_plan(result, week_data, extra, config=config, kind=kind, title=title, files=files,
                      created_at=now.isoformat(timespec="seconds"))
    plans_dir = Path(cfg.runs_dir) / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / f"{now:%Y%m%d-%H%M}-week{iso_week}-leveling{tag}.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"plan_path": str(plan_path), "out_dir": str(out_dir), "plan": plan}


def day_title(d: date, off_codes: list[str], week_staff: list[dict] | None = None) -> str:
    names = {s["code"]: short_name(s["name"]) for s in week_staff or []}
    who = "・".join(f"{names.get(c, c)}さん" for c in off_codes)
    return f"{day_label(d)} {who} お休みの振り分け" if who else f"{day_label(d)} の組み直し"
