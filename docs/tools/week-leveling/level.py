"""週のならし — 1 コマンドで「取り出し → 計算 → 検査 → Excel/A4 →（任意）Jev」。本番は読むだけ。

使い方 (リポジトリの直下で):
  uv run --python 3.12 --with ortools --with openpyxl --with httpx \
    python docs/tools/week-leveling/level.py --week 2026-10-12

主なオプション:
  --day 2026-10-13        その日だけ計算 (何度でも指定可)
  --off S004@2026-10-13   「この人が休みなら」の試算 (日付なしなら週全部)。何度でも指定可
  --allow-over 1          正規の上限を 1 名まで超えてよい (超えた分は重み付き)
  --over-before-manager   マネージャーより先に「少しオーバー」で吸収する
  --balance               正規の件数をならす (平均を超える分に重み)
  --drop 3f2a9c1e          この訪問を案から外す (訪問 ID の先頭・今週だけ取消にする予定のもの)。何度でも指定可
  --add-special           特別訪問週間の未配置の○を、その日に訪問の無い日だけ「足す訪問」として案に入れる
  --jev                   変更に Jev の「要確認」の目安を付ける (記号と数字だけ送る)
  --no-app-check          アプリの物差しでの診断 (diagnose.py) を飛ばす (既定は行う)
  --from-dir <dir>        取り出し済みの week.json / history.json を使う (本番に繋がない)
  --seconds 30            1 日あたりの計算時間 (秒)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from extract import extract  # noqa: E402
from report import check, flags_for, new_assignments, write_a4, write_excel  # noqa: E402
from solver import LUNCH_TARGET, run  # noqa: E402

REPO = HERE.parents[2]
CHROME = [
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
]


def parse_off(values):
    out = []
    for v in values or []:
        code, _, day = v.partition("@")
        out.append((code.strip(), date.fromisoformat(day) if day else None))
    return out


def to_pdf(html_path: Path) -> Path | None:
    exe = next((p for p in CHROME if p.exists()), None)
    if exe is None:
        return None
    pdf = html_path.resolve().with_suffix(".pdf")
    # 普段使いの Chrome と別のプロフィールで動かす (同じだと Chrome が終わらず待たされる)
    with tempfile.TemporaryDirectory() as profile:
        try:
            subprocess.run(
                [
                    str(exe),
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--user-data-dir={profile}",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}",
                    html_path.resolve().as_uri(),
                ],
                timeout=90,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            pass
    return pdf if pdf.exists() else None


def main():
    ap = argparse.ArgumentParser(description="週のならし（診断＋案）")
    ap.add_argument("--week", required=True, help="週のどの日でもよい (YYYY-MM-DD)")
    ap.add_argument("--day", action="append", default=[])
    ap.add_argument("--off", action="append", default=[])
    ap.add_argument("--allow-over", type=int, default=0)
    ap.add_argument("--over-before-manager", action="store_true")
    ap.add_argument("--balance", action="store_true")
    ap.add_argument("--drop", action="append", default=[])
    ap.add_argument("--add-special", action="store_true")
    ap.add_argument("--jev", action="store_true")
    ap.add_argument("--no-app-check", action="store_true")
    ap.add_argument("--from-dir")
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--config", default=str(HERE / "config.local.json"))
    ap.add_argument("--out")
    a = ap.parse_args()

    config = json.loads(Path(a.config).read_text(encoding="utf-8"))
    any_day = date.fromisoformat(a.week)
    monday = any_day - timedelta(days=any_day.weekday())
    _, iso_week, _ = monday.isocalendar()
    sunday = monday + timedelta(days=6)
    days = {date.fromisoformat(x) for x in a.day} or None
    off = parse_off(a.off)
    for x in list(days or []) + [dd for _c, dd in off if dd]:
        if not monday <= x <= sunday:
            ap.error(f"{x} は {monday}〜{sunday} の週の外です")
    tag = "".join(
        [
            f"-{min(days):%m%d}" if days else "",
            "-off" if a.off else "",
            f"-over{a.allow_over}" if a.allow_over else "",
            "-bal" if a.balance else "",
            "-drop" if a.drop else "",
            "-sp" if a.add_special else "",
        ]
    )
    out_dir = (
        Path(a.out)
        if a.out
        else REPO
        / "docs"
        / "reports"
        / f"{datetime.now():%Y-%m-%d-%H%M}-week{iso_week}-leveling{tag}"
    )
    reports = (REPO / "docs" / "reports").resolve()
    if out_dir.resolve().is_relative_to(REPO.resolve()) and not out_dir.resolve().is_relative_to(
        reports
    ):
        ap.error(
            "出力先がリポジトリの中なら docs/reports の下にしてください（利用者名を git に入れないため）"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    if a.from_dir:
        week_path, hist_path = Path(a.from_dir) / "week.json", Path(a.from_dir) / "history.json"
    else:
        print(f"本番から読み取り中（{monday} の週・読み取り専用）…", flush=True)
        week_path, hist_path = extract(monday, out_dir, config["server"], config["container"])

    opts = {
        "allow_over": a.allow_over,
        "over_before_manager": a.over_before_manager,
        "balance": a.balance,
        "off": off,
        "drop": [x.strip().lower() for x in a.drop],
        "add_special": a.add_special,
    }
    d, visits, staff, out, hist_by_day, lunch_by_day = run(
        week_path, hist_path, config, a.seconds, opts, days, log=lambda m: print(m, flush=True)
    )
    cap = config.get("cap_regular", 6)
    errors, warnings = check(d, out, cap, a.allow_over)
    flags = flags_for(out, hist_by_day)

    jev, jev_info = {}, {}
    if a.jev:
        from jev_check import jev_labels

        try:
            jev, jev_info = jev_labels(visits, out, flags)
        except Exception as exc:  # noqa: BLE001 — Jev が使えなくても案は出す
            print(f"Jev の確認は飛ばしました: {exc}", flush=True)

    diag = None
    if not a.no_app_check:
        from diagnose import diagnose

        names = {s["id"]: s["name"] for s in d["staff"]}
        try:
            diag = diagnose(
                d, visits, out, hist_by_day, cap, a.allow_over, opts, names,
                log=lambda m: print(m, flush=True),
            )
        except Exception as exc:  # noqa: BLE001 — 診断が使えなくても案は出す
            print(f"アプリの物差しでの診断は飛ばしました: {exc}", flush=True)

    title = f"週{iso_week}（{monday:%Y/%m/%d}〜{monday + timedelta(days=6):%m/%d}）ならし案"
    stats = write_excel(
        out_dir / "leveling.xlsx", title, d, visits, out, errors, warnings, cap, jev, diag,
        lunch_by_day,
    )
    write_a4(
        out_dir / "leveling-a4.html", title, d, visits, out, hist_by_day, jev, diag, lunch_by_day
    )
    pdf = to_pdf(out_dir / "leveling-a4.html")

    placed = new_assignments(out)
    moved = [m for f in flags.values() for k, m in f if k == "move"]
    summary = {
        "week_start": str(monday),
        "days": sorted(str(x) for x in days) if days else "all",
        "options": {
            k: (v if k != "off" else [f"{c}@{dd or '週全部'}" for c, dd in v])
            for k, v in opts.items()
        },
        "visits": len(visits),
        "unplaced": sum(1 for v in visits if v.id not in placed),
        "dropped_visits": len(d.get("dropped_visits") or []),
        "special_added": len(d.get("special_added") or []),
        "special_skipped_has_visit": len(d.get("special_skipped") or []),
        **stats,
        "rule_errors": len(errors),
        # 45 分では入らない訪問が出て、昼休みを 30 分に下げて計算した日
        "lunch_30_days": sorted(str(x) for x, m in lunch_by_day.items() if m < LUNCH_TARGET),
        "warnings": len(warnings),
        "moved_visits": len(moved),
        "max_move_min": max(map(abs, moved)) if moved else 0,
        "manager_overtime_visits": sum(1 for f in flags.values() for k, _ in f if k == "overtime"),
        "cross_office_visits": sum(1 for f in flags.values() for k, _ in f if k == "cross"),
        "same_as_last_visits": sum(1 for f in flags.values() for k, _ in f if k == "same_as_last"),
        "jev": {
            "labelled": len(jev),
            "needs_check": sum(1 for x in jev.values() if x.startswith("要確認")),
            **jev_info,
        }
        if a.jev
        else None,
        # 名前は入れない (数だけ)。中身は Excel の「アプリの物差し」と A4 の 1 ページ目
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
            "cur_high_week": len(diag["cur_high_week"]),
            "new_high_week": len(diag["new_high_week"]),
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
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        print("決まりの検査で問題:", *errors[:20], sep="\n  ")


if __name__ == "__main__":
    main()
