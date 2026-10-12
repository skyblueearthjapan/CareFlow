"""rk courseplan の中身: 読む（rk の読み取り専用 SQL）→ 問題 → 計算（solve.py を uv の別プロセスで）→ 案。本番へは書かない。"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from tools.courseplan import HERE, REPO_ROOT
from tools.courseplan import model as M
from tools.courseplan.plan import build_plan


class CoursePlanError(Exception):
    """利用者に日本語で見せて終わるエラー。"""


def parse_weekdays(values: list[str] | None) -> list[int]:
    if not values:
        return list(range(6))
    out = []
    for v in values:
        for x in str(v).split(","):
            x = x.strip().lower()
            if not x:
                continue
            if x in M.WD_KEYS:
                out.append(M.WD_KEYS.index(x))
            elif x in M.WD:
                out.append(M.WD.index(x))
            elif x.isdigit() and 0 <= int(x) <= 6:
                out.append(int(x))
            else:
                raise CoursePlanError(f"曜日は mon〜sat（または 月〜土・0〜5）で書いてください: {x}")
    if 6 in out:
        raise CoursePlanError("日曜の枠は扱いません")
    return sorted(set(out))


def check_out_dir(out_dir: Path) -> None:
    reports = (REPO_ROOT / "docs" / "reports").resolve()
    o = out_dir.resolve()
    if o.is_relative_to(REPO_ROOT.resolve()) and not o.is_relative_to(reports):
        raise CoursePlanError("出力先がリポジトリの中なら docs/reports の下にしてください（利用者名を git に入れないため）")


def solver_cmd_default() -> list[str]:
    from tools.leveling.run import LevelError, solver_prefix

    try:
        return solver_prefix()
    except LevelError as e:
        raise CoursePlanError(str(e)) from None


def courseplan(
    cfg,
    *,
    office: str | None = None,
    weekdays: list[str] | None = None,
    seconds: int = 30,
    max_changes: int | None = None,
    w_change: int | None = None,
    red: str = "keep",
    red_cleanup_done: bool = False,
    no_pref: str = "fixed",
    cap_regular: int = 6,
    staff_codes: list[str] | None = None,
    from_raw: str | None = None,
    out: str | None = None,
    run_sql: Callable[[str], Any] | None = None,
    solver_cmd: list[str] | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    wds = parse_weekdays(weekdays)
    now = datetime.now()
    tag = f"{office or 'all'}-{''.join(M.WD_KEYS[w][:2] for w in wds)}{'-nored' if red == 'ignore' else ''}"
    out_dir = (Path(out) if out else Path(cfg.reports_dir) / f"{now:%Y-%m-%d}" / f"{now:%H%M}-courseplan-{len(wds)}d").resolve()
    check_out_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if from_raw:
        raw = json.loads(Path(from_raw).read_text(encoding="utf-8"))
    else:
        from tools.courseplan.extract import extract, rk_runner

        log("本番から固定訪問の枠などを読み取り中（読み取り専用）…")
        path = extract(run_sql or rk_runner(cfg), date.today(), out_dir)
        raw = json.loads(path.read_text(encoding="utf-8"))

    offices = [office] if office else sorted({o["short_label"] for o in raw.get("offices") or []})
    opts = {"red": red, "no_pref": no_pref, "cap_regular": cap_regular,
            "staff_codes": [c.strip() for x in staff_codes for c in x.split(",") if c.strip()] if staff_codes else None}
    problems = []
    for o in offices:
        try:
            ofc = M.office_by_label(raw, o)
        except ValueError as e:
            raise CoursePlanError(str(e)) from None
        for wd in wds:
            if not M.operating(ofc, wd):
                log(f"{o} の {M.WD[wd]}曜は営業日ではないので飛ばします")
                continue
            p = M.build_problem(raw, o, wd, opts)
            if p["slots"]:
                problems.append(p)
    if not problems:
        raise CoursePlanError("計算する固定訪問の枠がありません（拠点・曜日を確かめてください）")
    prob_path = out_dir / "problems.json"
    prob_path.write_text(json.dumps(problems, ensure_ascii=False), encoding="utf-8")

    sol_path = out_dir / "solution.json"
    cmd = list(solver_cmd or solver_cmd_default()) + [
        str(HERE / "solve.py"), "--in", str(prob_path), "--out", str(sol_path), "--seconds", str(seconds)]
    if max_changes is not None:
        cmd += ["--max-changes", str(max_changes)]
    if w_change is not None:
        cmd += ["--w-change", str(w_change)]
    log(f"計算中（{len(problems)} 件 × {seconds} 秒ほど・昼休み 45/30 分で最大 2 回）…")
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if res.returncode != 0:
        raise CoursePlanError(f"計算が止まりました（終了コード {res.returncode}）。上の表示を確かめてください")
    solutions = json.loads(sol_path.read_text(encoding="utf-8"))
    by_key = {(p["office"], p["weekday"]): p for p in problems}
    for s in solutions:  # 変えなくてよい枠は今の型に戻す（変える枠をなるべく少なく）
        p = by_key[(s["office"], s["weekday"])]
        s["assign"], s["reverted"] = M.tidy(p, s["assign"], w_change or 20)
        if s["reverted"]:
            log(f"{p['office']} {p['weekday_ja']}: 今の型に戻せた項目 {s['reverted']}")
    sol_path.write_text(json.dumps(solutions, ensure_ascii=False), encoding="utf-8")

    plan = build_plan(raw, problems, solutions, created_at=now.isoformat(timespec="seconds"),
                      files={"dir": str(out_dir)}, red_cleanup_done=red_cleanup_done)
    (out_dir / "metrics.json").write_text(json.dumps(plan["metrics"], ensure_ascii=False, indent=1), encoding="utf-8")
    plans_dir = Path(cfg.runs_dir) / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / f"{now:%Y%m%d-%H%M}-courseplan-{tag}.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"plan_path": str(plan_path), "out_dir": str(out_dir), "plan": plan}
