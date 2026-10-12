"""ならしの部品箱（Python から呼ぶ入口）。knobs（rk level の --prefer-staff など）で言えない注文を、
作業用の小さなスクリプトで書くための 3 つの関数。使い方と例は CUSTOM.md。

    from tools.leveling.api import load_week, solve, to_plan

    week = load_week("2026-10-19", out_dir="scratch/w43")      # 本番を読む（読み取り専用）か、前の出力フォルダを使う
    res = solve(week, days=["2026-10-22"], extra_constraints=[my_rule], notes=["〇〇さんは午後だけ"])
    out = to_plan(res, week)                                    # → agent/runs/plans/…json（rk plan check に渡す）

共有の道具（docs/tools/week-leveling・agent/tools/leveling）は書き換えず、スクリプトは scratch に置く。
solve() は ortools と openpyxl が要る: uv run --python 3.12 --with ortools --with openpyxl --with httpx python <script>
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from tools.leveling import DEFAULT_CONFIG, HERE, LEVELING_DIR, REPO_ROOT  # noqa: F401
from tools.leveling.plan import build_plan
from tools.leveling.run import LevelError, check_out_dir, monday_of, resolve_config, trainee_off

sys.path.insert(0, str(LEVELING_DIR))
import level_knobs  # noqa: E402

# solve(knobs=...) に渡せるキー（level_knobs.from_args が作るものと同じ形）
KNOB_KEYS = ("prefer", "avoid", "keep_current", "cancel", "only_staff", "not_same_day", "pair_staff",
             "forbid_pair", "fast")


@dataclass
class Week:
    monday: date
    in_dir: Path  # week.json / history.json / agent_extra.json のある所
    out_dir: Path
    week: dict
    extra: dict | None
    config: dict
    config_path: Path
    cfg: Any = None
    notes: list[str] = field(default_factory=list)


def load_week(week: str, out_dir: str | Path | None = None, *, from_dir: str | Path | None = None,
              config_path: str | Path | None = None, cfg=None, run_sql: Callable[[str], Any] | None = None,
              log: Callable[[str], None] = print) -> Week:
    """週のデータを用意する。from_dir（rk level の出力フォルダなど）があればそれを使い、無ければ本番から読む（読み取り専用）。
    out_dir = Excel/A4/result.json の出力先（リポジトリの中なら docs/reports の下）。省略すると docs/reports/agent の下。"""
    monday = monday_of(date.fromisoformat(week))
    if cfg is None and from_dir is None:
        from rk.config import load_config

        cfg = load_config()
    iso_week = monday.isocalendar()[1]
    now = datetime.now()
    if out_dir is None:
        if cfg is None:
            raise LevelError("out_dir を指定してください")
        out = Path(cfg.reports_dir) / f"{now:%Y-%m-%d}" / f"{now:%H%M}-week{iso_week}-custom"
    else:
        out = Path(out_dir)
    check_out_dir(out)
    out.mkdir(parents=True, exist_ok=True)
    if config_path is None and cfg is None and run_sql is None and (
            os.environ.get("RK_MODE", "").lower() == "relay" or not DEFAULT_CONFIG.exists()):
        from rk.config import load_config  # 職員の記録から config を作るつなぎ先

        cfg = load_config()
    config, cpath = resolve_config(config_path, out, cfg, run_sql, log)
    if from_dir is not None or (out / "week.json").exists():
        in_dir = Path(from_dir) if from_dir is not None else out
        if not (in_dir / "week.json").exists():
            raise LevelError(f"{in_dir} に week.json がありません")
    else:
        from tools.leveling.extract_rk import extract_week, rk_runner

        log(f"本番から読み取り中（{monday} の週・読み取り専用）…")
        extract_week(run_sql or rk_runner(cfg), monday, out)
        in_dir = out
    extra_path = in_dir / "agent_extra.json"
    return Week(
        monday=monday, in_dir=in_dir, out_dir=out,
        week=json.loads((in_dir / "week.json").read_text(encoding="utf-8")),
        extra=json.loads(extra_path.read_text(encoding="utf-8")) if extra_path.exists() else None,
        config=config, config_path=cpath, cfg=cfg,
    )


def solve(week: Week, *, days: list[str | date] | None = None, weekdays: list[int] | None = None,
          off: list[str] | None = None, allow_over: int = 0, over_before_manager: bool = False,
          balance: bool = False, drop: list[str] | None = None, add_special: bool = False,
          knobs: dict | None = None, weights: dict | None = None,
          extra_constraints: list[Callable] | None = None, notes: list[str] | None = None,
          seconds: int | None = None, no_app_check: bool = False, no_pdf: bool = True,
          include_trainee: bool = False, log: Callable[[str], None] = print) -> dict:
    """計算して Excel/A4/result.json を out_dir に書き、result（dict）を返す。

    days / weekdays … 計算する日（None = 週全部）。off = ["CODE@YYYY-MM-DD" または "CODE"]。
    knobs … rk level の指定と同じ形: {"prefer": {"A3": 150}, "avoid": {"A1": 600}, "keep_current": 300,
            "cancel": ["訪問ID先頭"], "only_staff": [{"codes": ["A1"], "window": [540, 720]}],
            "not_same_day": [["A", "B"]], "pair_staff": [["利用者", "CODE"]], "forbid_pair": [["利用者", "CODE"]], "fast": True}
    weights … 重みの上書き（solver.WEIGHTS の名前: manager_visit / cross_office / rotation / move_per_min / ...）。
    extra_constraints … 日ごとに fn(model, vars, data) を呼ぶ。model = OR-Tools の RoutingModel、vars = 道具の箱の dict
            （solver, time, count, jobs, vehicles, day, index_of_visit{訪問ID→index}, vehicle_of_code{職員コード→番号}）、
            data = その日の訪問・職員・担当歴（CUSTOM.md）。
    notes … 自作の条件の説明（日本語・A4 の「今回だけの条件」と案の warnings に出る）。自作の条件や weights を使ったら必ず書く。"""
    from tools.leveling.solve import compute

    knobs = dict(knobs or {})
    bad = sorted(set(knobs) - set(KNOB_KEYS))
    if bad:
        raise LevelError(f"knobs の名前が違います: {', '.join(bad)}（使えるのは {', '.join(KNOB_KEYS)}）")
    if (extra_constraints or weights) and not notes:
        raise LevelError("extra_constraints / weights を使うときは notes=[…] に日本語で条件を書いてください（A4 に出します）")
    sunday = week.monday + timedelta(days=6)
    day_set = {date.fromisoformat(str(x)) for x in days or []} | {week.monday + timedelta(days=w) for w in weekdays or []}
    off = list(off or [])
    for x in sorted(day_set) + [date.fromisoformat(v.partition("@")[2]) for v in off if "@" in v]:
        if not week.monday <= x <= sunday:
            raise LevelError(f"{x} は {week.monday}〜{sunday} の週の外です")
    if not include_trainee:
        off = trainee_off(week.config, week.week, off, log)
    from level import parse_off  # noqa: PLC0415 — docs/tools/week-leveling（LEVELING_DIR は import 済み）

    opts = {
        "allow_over": allow_over, "over_before_manager": over_before_manager, "balance": balance,
        "off": parse_off(off), "drop": [x.strip().lower() for x in drop or []], "add_special": add_special,
        **knobs,
    }
    if weights:
        opts["weights"] = dict(weights)
    if extra_constraints:
        opts["extra_constraints"] = list(extra_constraints)
    if seconds is None:
        seconds = level_knobs.FAST_SECONDS if knobs.get("fast") else 30
    summary, result = compute(
        week.in_dir, week.out_dir, week.config, str(week.monday), days=day_set or None, opts=opts,
        seconds=seconds, no_app_check=no_app_check, no_pdf=no_pdf, custom_notes=notes, log=log,
    )
    return result


def to_plan(result: dict, week: Week, *, kind: str = "week-leveling", title: str | None = None,
            save: bool = True, log: Callable[[str], None] = print) -> dict:
    """solve() の結果を案（plan.json）にする。save=True なら agent/runs/plans に書く。
    返り値 {"plan": dict, "plan_path": str|None, "out_dir": str}。次は rk plan check <plan_path>。"""
    now = datetime.now()
    summary = result.get("summary") or {}
    files = {"dir": str(week.out_dir), **(summary.get("files") or {})}
    plan = build_plan(result, week.week, week.extra, config=week.config, kind=kind, title=title, files=files,
                      created_at=now.isoformat(timespec="seconds"))
    path = None
    if save:
        if week.cfg is None:
            from rk.config import load_config

            week.cfg = load_config()
        plans_dir = Path(week.cfg.runs_dir) / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        iso_week = week.monday.isocalendar()[1]
        path = plans_dir / f"{now:%Y%m%d-%H%M}-week{iso_week}-leveling-custom.json"
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"案を書きました: {path}（次: rk plan check {path}）")
    return {"plan": plan, "plan_path": str(path) if path else None, "out_dir": str(week.out_dir)}
