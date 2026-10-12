"""コースのならしの結果 → rk の案（plan.json）。標準ライブラリだけ。

操作は pfv-update（PUT /patients/{id}/fixed-visits・change_scope=pattern_only）を患者ごとに 1 件。
items は**その患者の固定訪問の枠の全件**（計算しなかった曜日の枠もそのまま入れる。PUT は全件の置き換えのため）。
movability（赤ピン）は今の値をそのまま写す（付け外しはしない）。赤は曜日・時刻だけを縛るので、赤の枠のコース替えは反映できる。
目的と決まりは PO 了承済み（2026-10-12・設計書 §8 Q-A〜F・Q-I）。ready は止める理由（blockers）が無いときだけ true。
止める理由: 赤ピンの整理（本番）が終わっていない（red_cleanup_done=False が既定）／赤の枠の時刻を変える案（--red ignore）／入りきらない枠。
"""

from __future__ import annotations

from collections import defaultdict

from tools.courseplan import model as M

RED_CLEANUP_BLOCKER = "赤ピンの整理（本番）が終わるまで反映しない"


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def pfv_items(rows: list[dict], new: dict[str, dict]) -> list[dict]:
    """患者の枠の全件 → PUT の items。new = pfv_id → {"course", "start"}（変える枠だけ）。"""
    items = []
    for r in sorted(rows, key=lambda r: (int(r["weekday"]), int(r.get("slot_index") or 0))):
        mv = r.get("movability") or ("locked" if r.get("is_pinned") else "unknown")
        ch = new.get(r["id"])
        items.append({
            "weekday": int(r["weekday"]),
            "start_time": _hhmm(ch["start"]) if ch else str(r["start_time"])[:5],
            "duration_min": int(r.get("duration_min") or 35),
            "course_template_id": ch["course"] if ch else r.get("course_template_id"),
            "sub_office_id": r.get("sub_office_id"),
            "slot_index": int(r.get("slot_index") or 0),
            "is_pinned": mv == "locked",
            "movability": mv,
        })
    return items


def timeline(problem: dict, assign: dict, title: str, changed: set[str] | None = None) -> dict:
    rows = []
    for c in problem["courses"]:
        route = M.course_route(problem, assign, c["id"])
        rows.append({
            "name": f"{c['label']} {M.short_name((c['staff'] or {}).get('name')) or ''}".strip(),
            "blocks": [{"start": M.fmt(assign[s["key"]]["start"]),
                        "end": M.fmt(assign[s["key"]]["start"] + s["dur"]),
                        "label": f"{M.short_name(s['patient'])}様" + ("🔴" if s["locked"] else ""),
                        "changed": bool(changed and s["key"] in changed)} for s in route],
        })
    open_ids = {c["id"] for c in problem["courses"]}
    loose = defaultdict(list)
    for s in problem["slots"]:
        if s["key"] not in assign:  # 置けなかった新しい枠（まだ枠の無い方）は時間割に出さない
            continue
        cid = assign[s["key"]].get("course")
        if cid not in open_ids:
            loose["コースなし" if cid is None else f"{s['cur_course_label'] or ''}（人なし）"].append(s)
    for name, ss in sorted(loose.items()):
        rows.append({"name": name, "blocks": [
            {"start": M.fmt(assign[s["key"]]["start"]), "end": M.fmt(assign[s["key"]]["start"] + s["dur"]),
             "label": f"{M.short_name(s['patient'])}様", "changed": False}
            for s in sorted(ss, key=lambda s: assign[s["key"]]["start"])]})
    return {"date": f"{problem['weekday']} {problem['weekday_ja']}曜 {title}", "rows": rows}


def build_plan(raw: dict, problems: list[dict], solutions: list[dict], *, created_at: str,
               files: dict | None = None, red_cleanup_done: bool = False) -> dict:
    sol_by = {(s["office"], s["weekday"]): s for s in solutions}
    rows_by_patient = defaultdict(list)
    for r in raw.get("pfv") or []:
        rows_by_patient[r["patient_id"]].append(r)

    all_changes, metrics, timelines, summary, warnings = [], [], [], [], []
    dropped_total = 0
    for p in problems:
        sol = sol_by.get((p["office"], p["weekday"]))
        if sol is None:
            continue
        cur = M.current_assignment(p)
        after = sol["assign"]
        before_m, after_m = M.evaluate(p, cur), M.evaluate(p, after)
        chg = M.changes(p, after)
        all_changes += chg
        dropped_total += len(sol.get("dropped") or [])
        metrics.append({"office": p["office"], "weekday": p["weekday"], "before": before_m, "after": after_m,
                        "changes": len(chg), "dropped": len(sol.get("dropped") or []), "lunch": sol.get("lunch")})
        head = f"{p['office']} {p['weekday_ja']}曜（枠 {len(p['slots'])}・コース {len(p['courses'])}）"
        summary.append(f"{head} いま: {M.metrics_line(before_m)}")
        summary.append(f"{head} 案: {M.metrics_line(after_m)}・変える枠 {len(chg)}")
        timelines.append(timeline(p, cur, "いまの型"))
        timelines.append(timeline(p, after, "案", {c["key"] for c in chg}))
        warnings += p.get("notes") or []
        warnings += [f"案でも残る: {x}" for x in after_m["issues"]]
        for k in sol.get("dropped") or []:
            s = next(x for x in p["slots"] if x["key"] == k)
            warnings.append(f"入りきらないので今のまま: {p['weekday_ja']} {M.fmt(s['start'])} {s['patient']}様")

    by_patient = defaultdict(list)
    for c in all_changes:
        by_patient[c["patient_id"]].append(c)
    ops = []
    for pid, chs in sorted(by_patient.items(), key=lambda kv: kv[1][0]["patient"]):
        new = {c["key"]: {"course": c["to_course"], "start": c["to_start"]} for c in chs}
        what = "・".join(
            f"{M.WD[c['weekday']]} {M.fmt(c['from_start'])}"
            + (f"→{M.fmt(c['to_start'])}" if c["time_changed"] else "")
            + (f" {c['from_label']}→{c['to_label']}" if c["course_changed"] else "")
            for c in sorted(chs, key=lambda c: (c["weekday"], c["slot_index"])))
        ops.append({
            "op": "pfv-update",
            "patient_id": pid,
            "mode": "normal",
            "change_scope": "pattern_only",
            "items": pfv_items(rows_by_patient[pid], new),
            "label": f"{chs[0]['patient']}様 固定訪問の枠 {what}",
        })

    locked_time = [c for c in all_changes if c["locked"] and c["time_changed"]]
    blockers = []
    if not red_cleanup_done:
        blockers.append(RED_CLEANUP_BLOCKER)
    if locked_time:
        blockers.append(
            f"赤ピンの枠の時刻を変える操作が {len(locked_time)} 件あります（--red ignore の試算）。"
            "赤の枠の曜日・時刻の変更は pfv-update が断ります。赤を外す整理が先です")
    if dropped_total:
        blockers.append(f"入りきらない枠が {dropped_total} 件あります（今のまま残しています）")

    n_course = sum(1 for c in all_changes if c["course_changed"])
    n_time = sum(1 for c in all_changes if c["time_changed"])
    summary.insert(0, f"変える枠 {len(all_changes)}（コース {n_course}・時刻 {n_time}）／患者 {len(ops)} 名。週の予定は「週を作る」まで変わりません（型だけ）")
    office = sorted({p["office"] for p in problems})
    days = "".join(p["weekday_ja"] for p in sorted(problems, key=lambda p: p["weekday"]))
    opts = problems[0]["opts"] if problems else {}
    return {
        "kind": "course-plan",
        "title": f"コースのならし{'・'.join(office)} {days}曜",
        "target": {"office": "・".join(office), "weekdays": days, "red": opts.get("red"), "no_pref": opts.get("no_pref")},
        "created_at": created_at,
        "ready": not blockers,
        "blockers": blockers,
        "summary": summary,
        "ops": ops,
        "checks": [
            "全員稼働の前提（休みは見ない）。その曜日に出勤の型がある職員（新人を除く）1 人に 1 コース（直近の週に多く持ったコースから当てる）。勤務時刻・性別・NG・固定イベントはその人で見る",
            "1 コース 1 日 6 名まで（マネージャーのコースはあふれの受け皿・1 名ごとに重み・勤務時刻を超えてよいが重み）",
            "移動（直線 20km/h・別住所へ 8 分）が間に合う／同じ建物の 2 名は 1 人で 90 分の枠",
            "昼休み 11:30〜13:30 の中で 30 分は必ず（45 分をなるべく）／固定イベントの前後 15 分は入れない",
            "赤ピンの枠は曜日・時刻を動かさない（コースは替えてよい）" if opts.get("red", "keep") == "keep"
            else "赤ピンを全部外した試算（K-4）",
            "希望の時間帯の中だけで時刻を動かす（固定・希望なしの方は今の時刻のまま）" if opts.get("no_pref", "fixed") == "fixed"
            else "希望の時間帯の中だけで時刻を動かす（希望なしの方は終日の範囲）",
            "曜日は変えない／2 名体制は同じ時刻に別のコース",
        ],
        "warnings": warnings,
        "timelines": timelines,
        "metrics": metrics,
        "changes": [{**c, "from_start": M.fmt(c["from_start"]), "to_start": M.fmt(c["to_start"])} for c in all_changes],
        "source": {"tool": "courseplan", "extracted_at": raw.get("extracted_at"), "files": files or {}},
    }
