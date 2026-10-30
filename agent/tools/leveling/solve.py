"""ならしの計算（docs/tools/week-leveling の solver/report/diagnose をそのまま使う）＋結果を result.json に残す。

ortools と openpyxl が要るので、rk からは uv の別プロセスで動かす（run.py が組み立てる）:
  uv run -q --python 3.12 --with ortools --with openpyxl --with httpx \
    python agent/tools/leveling/solve.py --week 2026-10-19 --in <dir> --out <dir> [level.py と同じオプション]

本番には繋がない（--in の week.json / history.json だけを読む）。level.py の main と同じ順で
計算 → 決まりの数え直し → アプリの物差しの診断 → Excel/A4/summary.json を出し、加えて
案に直すための result.json（日ごとの順路・入らない訪問・職員の勤務）を書く。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

LEVELING_DIR = Path(__file__).resolve().parents[3] / "docs" / "tools" / "week-leveling"
sys.path.insert(0, str(LEVELING_DIR))

import level_knobs  # noqa: E402
from level import parse_off, to_pdf  # noqa: E402
from report import check, flags_for, new_assignments, write_a4, write_excel  # noqa: E402
from solver import LUNCH_TARGET, run  # noqa: E402


def visit_dict(v) -> dict:
    return {
        "id": v.id,
        "patient_id": v.patient_id,
        "patient": v.patient,
        "office": v.office,
        "day": str(v.day),
        "fixed_start": v.fixed_start,
        "dur": v.dur,
        "win": list(v.win),
        "time_type": v.time_type,
        "female_only": v.female_only,
        "male_only": v.male_only,
        "ng_weekdays": sorted(v.ng_weekdays),
        "ng": sorted(v.ng),
        "cur_staff": v.cur_staff,
        "cur_course": v.cur_course,
        "new": v.new,
    }


def staff_dict(s) -> dict:
    return {
        "id": s.id,
        "code": s.code,
        "name": s.name,
        "sex": s.sex,
        "offices": sorted(s.offices),
        "manager": s.manager,
        "shift": {str(k): list(v) for k, v in s.shift.items()},
        "off": sorted(s.off),
        "events": {str(k): [list(x) for x in v] for k, v in s.raw_events.items()},
    }


def result_dict(d, visits, staff, out, hist_by_day, lunch_by_day, errors, warnings, flags, opts, conditions=None) -> dict:
    days = {}
    for day, (vehicles, routes, dropped) in sorted(out.items()):
        days[str(day)] = {
            "vehicles": [s.id for s in vehicles],
            "routes": {
                sid: [
                    {"visit_ids": [v.id for v in j.visits], "offsets": list(j.offsets), "start": t, "dur": j.dur}
                    for j, t in seq
                ]
                for sid, seq in routes.items()
            },
            "dropped": [[v.id for v in j.visits] for j in dropped],
            "lunch_min": lunch_by_day.get(day),
            "history": hist_by_day.get(day, {}),
        }
    return {
        "week_start": d["week_start"],
        "options": {k: (v if k != "off" else [[c, str(dd) if dd else None] for c, dd in v])
                    for k, v in opts.items() if k != "extra_constraints"},
        "visits": [visit_dict(v) for v in visits],
        "staff": [staff_dict(s) for s in staff.values()],
        "days": days,
        "errors": errors,
        "warnings": warnings,
        "flags": {vid: [[k, m] for k, m in fl] for vid, fl in flags.items()},
        "dropped_visits": d.get("dropped_visits") or [],
        "cancelled_visits": d.get("cancelled_visits") or [],
        "conditions": conditions or [],
        "special_added": d.get("special_added") or [],
        "special_skipped": d.get("special_skipped") or [],
    }




def compute(in_dir, out_dir, config: dict, week: str, *, days=None, opts: dict | None = None, seconds: int = 30,
            no_app_check: bool = False, no_pdf: bool = False, custom_notes: list[str] | None = None,
            log: Callable[[str], None] = lambda m: print(m, flush=True)) -> tuple[dict, dict]:
    """計算 → 決まりの数え直し → 診断 → Excel/A4/summary.json/result.json。(summary, result) を返す。
    opts = solver.run の opts（allow_over など + level_knobs の「今回だけの条件」+ weights / extra_constraints）。
    days = 計算する日 (date の集合・None = 週全部)。custom_notes = 自作の条件の説明（A4 の「今回だけの条件」に出す）。"""
    in_dir, out_dir = Path(in_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    any_day = date.fromisoformat(week)
    monday = any_day - timedelta(days=any_day.weekday())
    _, iso_week, _ = monday.isocalendar()
    opts = {"allow_over": 0, "over_before_manager": False, "balance": False, "off": [], "drop": [],
            "add_special": False, **(opts or {})}
    d, visits, staff, out, hist_by_day, lunch_by_day = run(
        in_dir / "week.json", in_dir / "history.json", config, seconds, opts, days, log=log,
    )
    cap = config.get("cap_regular", 6)
    allow_over = opts["allow_over"]
    errors, warnings = check(d, out, cap, allow_over)
    flags = flags_for(out, hist_by_day)
    names = {s["code"]: s["name"].replace("　", " ").split(" ")[0] for s in d["staff"]}
    conditions = level_knobs.describe(opts, names, days, custom_notes)
    warnings = warnings + [f"今回だけの条件（決まりをゆるめる）: {c['text']}" for c in conditions if c["relax"]]

    diag = None
    if not no_app_check:
        from diagnose import diagnose

        names_by_id = {s["id"]: s["name"] for s in d["staff"]}
        try:
            diag = diagnose(d, visits, out, hist_by_day, cap, allow_over, opts, names_by_id, log=log)
        except Exception as exc:  # noqa: BLE001 — 診断が使えなくても案は出す
            log(f"アプリの物差しでの診断は飛ばしました: {exc}")

    title = f"週{iso_week}（{monday:%Y/%m/%d}〜{monday + timedelta(days=6):%m/%d}）ならし案"
    stats = write_excel(out_dir / "leveling.xlsx", title, d, visits, out, errors, warnings, cap, {}, diag,
                        lunch_by_day, conditions)
    write_a4(out_dir / "leveling-a4.html", title, d, visits, out, hist_by_day, {}, diag, lunch_by_day, conditions)
    pdf = None if no_pdf else to_pdf(out_dir / "leveling-a4.html")

    placed = new_assignments(out)
    moved = [m for f in flags.values() for k, m in f if k == "move"]
    summary = {
        "week_start": str(monday),
        "days": sorted(str(x) for x in days) if days else "all",
        "visits": len(visits),
        "unplaced": sum(1 for v in visits if v.id not in placed),
        "dropped_visits": len(d.get("dropped_visits") or []),
        "cancelled_visits": len(d.get("cancelled_visits") or []),
        "special_added": len(d.get("special_added") or []),
        "special_skipped_has_visit": len(d.get("special_skipped") or []),
        **stats,
        "rule_errors": len(errors),
        "lunch_30_days": sorted(str(x) for x, m in lunch_by_day.items() if m < LUNCH_TARGET),
        "warnings": len(warnings),
        "conditions": [c["text"] for c in conditions],
        "moved_visits": len(moved),
        "max_move_min": max(map(abs, moved)) if moved else 0,
        "manager_overtime_visits": sum(1 for f in flags.values() for k, _ in f if k == "overtime"),
        "cross_office_visits": sum(1 for f in flags.values() for k, _ in f if k == "cross"),
        "same_as_last_visits": sum(1 for f in flags.values() for k, _ in f if k == "same_as_last"),
        "app_check": {
            "yardstick_match": diag["yardstick_match"],
            "cur_travel_minutes": diag["cur"]["travel_minutes"],
            "new_travel_minutes": diag["new"]["travel_minutes"],
            "cur_travel_km": diag["cur"]["travel_km"],
            "new_travel_km": diag["new"]["travel_km"],
            "cur_gap_minutes": diag["cur"]["gap_minutes"],
            "new_gap_minutes": diag["new"]["gap_minutes"],
            "cur_high_days": len(diag["cur_high_days"]),
            "new_high_days": len(diag["new_high_days"]),
            "one_more_move_checked": diag["moves_checked"],
            "one_more_move_missed": diag["missed"],
            "one_more_move_declined": diag["declined"],
        }
        if diag
        else None,
        "files": {
            "excel": str(out_dir / "leveling.xlsx"),
            "a4": str(pdf or out_dir / "leveling-a4.html"),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    res = result_dict(d, visits, staff, out, hist_by_day, lunch_by_day, errors, warnings, flags, opts, conditions)
    res["summary"] = summary
    (out_dir / "result.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return summary, res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ならしの計算（rk level から呼ぶ）")
    ap.add_argument("--week", required=True)
    ap.add_argument("--in", dest="in_dir", required=True, help="week.json / history.json のある所")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--day", action="append", default=[])
    ap.add_argument("--off", action="append", default=[])
    ap.add_argument("--allow-over", type=int, default=0)
    ap.add_argument("--over-before-manager", action="store_true")
    ap.add_argument("--balance", action="store_true")
    ap.add_argument("--drop", action="append", default=[])
    ap.add_argument("--add-special", action="store_true")
    ap.add_argument("--no-app-check", action="store_true")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--seconds", type=int, default=None)
    level_knobs.add_args(ap)
    a = ap.parse_args(argv)

    config = json.loads(Path(a.config).read_text(encoding="utf-8"))
    any_day = date.fromisoformat(a.week)
    monday = any_day - timedelta(days=any_day.weekday())
    try:
        knob_opts = level_knobs.from_args(a)
        weekdays = level_knobs.parse_weekdays(a.days) if a.days else []
    except ValueError as e:
        ap.error(str(e))
    days = {date.fromisoformat(x) for x in a.day} | {monday + timedelta(days=w) for w in weekdays} or None
    seconds = a.seconds or (level_knobs.FAST_SECONDS if a.fast else 30)
    opts = {
        "allow_over": a.allow_over,
        "over_before_manager": a.over_before_manager,
        "balance": a.balance,
        "off": parse_off(a.off),
        "drop": [x.strip().lower() for x in a.drop],
        "add_special": a.add_special,
        **knob_opts,
    }
    summary, _res = compute(a.in_dir, a.out, config, a.week, days=days, opts=opts, seconds=seconds,
                            no_app_check=a.no_app_check, no_pdf=a.no_pdf)
    print(json.dumps({k: summary[k] for k in ("visits", "unplaced", "rule_errors", "warnings")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
