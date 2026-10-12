"""コースの作業場の計算まわり (DB に触らない). agent/tools/template_ws/ops.py の純粋な部分を、DB 版の下書き dict 用に移したもの.

下書き dict (engine draft):
  office / office_id / snapshot{raw, scope_patients} / scenario / assign{枠のキー: {course, start}} /
  held[枠のキー] (手で動かした枠) / unplaced{枠のキー: 理由} / sids
枠のキー: 今ある枠 = ``<患者ID>:<曜日>:<slot>``・新しい枠 = ``new:<患者ID>:<曜日>:<slot>``.
"""

# ruff: noqa: N812, N806, N803, N818, B905

from __future__ import annotations

from . import model as M
from . import problems as P
from .errors import TemplateError


def default_scenario() -> dict:
    return {
        "red": "keep",
        "no_pref": "fixed",
        "cap": 6,
        "staff_count": {},
        "drop_staff": [],
        "add_patients": {},
        "prefs": {},
    }


def make_draft(office_label: str, office_id: str, raw: dict, scope: list[str], scenario: dict,
               assign: dict, held: list[str], unplaced: dict) -> dict:
    return {
        "office": office_label,
        "office_id": office_id,
        "snapshot": {"raw": raw, "scope_patients": list(scope)},
        "scenario": {**default_scenario(), **(scenario or {})},
        "assign": assign,
        "held": list(held),
        "unplaced": dict(unplaced),
        "sids": {},
    }


def master_assign(p: dict) -> dict:
    return P.master_assign(p)


def draft_assign(draft: dict, p: dict) -> dict:
    return P.draft_assign(draft, p)


def day_metrics(draft: dict, p: dict) -> dict:
    master, mine = master_assign(p), draft_assign(draft, p)
    chg = M.changes(p, mine)
    new_slots = [s for s in p["slots"] if s.get("new")]
    placed = [s for s in new_slots if s["key"] in mine]
    virt = {c["id"] for c in p["courses"] if c.get("virtual")}
    return {
        "weekday": p["weekday"],
        "weekday_ja": p["weekday_ja"],
        "slots": len([s for s in p["slots"] if not s.get("new")]),
        "courses": [c["label"] for c in p["courses"]],
        "master": M.evaluate(p, master),
        "draft": M.evaluate(p, mine),
        "changes": len(chg),
        "course_changes": sum(1 for c in chg if c["course_changed"] and not c.get("new")),
        "time_changes": sum(1 for c in chg if c["time_changed"] and not c.get("new")),
        "red_course_changes": sum(1 for c in chg if c["locked"] and c["course_changed"]),
        "red_time_changes": sum(1 for c in chg if c["locked"] and c["time_changed"]),
        "new_placed": len(placed),
        "new_unplaced": len(new_slots) - len(placed),
        "virtual_used": sum(1 for s in p["slots"] if (mine.get(s["key"]) or {}).get("course") in virt),
        "notes": p.get("notes") or [],
    }


def slim_eval(m: dict) -> dict:
    keys = ("travel_min", "conflicts", "over_cap", "no_course", "unstaffed", "outside_shift", "outside_window",
            "ng_or_sex", "count_spread", "manager_visits", "lunch_short", "event_hits", "regular_counts")
    out = {k: m[k] for k in keys}
    out["issues"] = m["issues"]
    return out


def metrics_of(draft: dict, problems: list[dict] | None = None) -> list[dict]:
    problems = problems if problems is not None else P.build_week(draft)
    return [day_metrics(draft, p) for p in problems]


def totals(ms: list[dict]) -> dict:
    keys = ("travel_min", "conflicts", "over_cap", "no_course", "unstaffed", "outside_shift", "outside_window",
            "ng_or_sex", "manager_visits", "lunch_short", "event_hits")
    return {side: {k: sum(m[side][k] for m in ms) for k in keys} for side in ("master", "draft")}


def greedy_place(p: dict, assign: dict, keys: list[str]) -> dict:
    """新しい枠を、決まりの破れが増えない所へ 1 つずつ置く (ops.greedy_place と同じ)."""
    slots = {s["key"]: s for s in p["slots"]}
    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(slots[k]["pair"] or k, []).append(k)
    cur = {k: dict(v) for k, v in assign.items()}
    placed = {}
    for g in groups.values():
        g.sort(key=lambda k: slots[k]["slot_index"])
        base = M.evaluate(p, cur)
        bad0 = M.badness(base)
        s0 = slots[g[0]]
        allowed = [c["id"] for c in p["courses"] if all(c["id"] in slots[k]["allowed"] for k in g)]
        combos = [[a] for a in allowed] if len(g) == 1 else [[a, b] for a in allowed for b in allowed if a != b]
        best = None
        for t in range(s0["win"][0], s0["win"][1] + 1, 5):
            for combo in combos:
                trial = {**cur, **{k: {"course": c, "start": t} for k, c in zip(g, combo)}}
                m = M.evaluate(p, trial)
                bad = M.badness(m)
                if bad[0] > bad0[0]:
                    continue
                score = (bad[1], m["travel_min"], t)
                if best is None or score < best[0]:
                    best = (score, trial, combo, t)
        if best:
            cur = best[1]
            for k, c in zip(g, best[2]):
                placed[k] = {"course": c, "start": best[3]}
    return placed


def apply_solutions(draft: dict, problems: list[dict], sols: list[dict], w_change: int = 20) -> list[str]:
    """計算の結果を下書きの割り当てへ (片付け・新しい枠の後置きまで)。戻り値は経過のメモ."""
    notes = []
    by_wd = {p["weekday"]: p for p in problems}
    for s in sols:
        p = by_wd[s["weekday"]]
        assign, reverted = M.tidy(p, s["assign"], w_change)
        keys = {x["key"] for x in p["slots"]}
        for k in keys:
            if k in (draft.get("held") or []):
                continue  # 手で動かした枠は動かさない
            draft["assign"].pop(k, None)
            draft["unplaced"].pop(k, None)
        for k, a in assign.items():
            if k in keys and k not in (draft.get("held") or []):
                draft["assign"][k] = {"course": a["course"], "start": int(a["start"])}
        dropped_new = [k for k in s.get("dropped") or [] if k not in assign
                       and next(x for x in p["slots"] if x["key"] == k).get("new")]
        if dropped_new:
            placed = greedy_place(p, assign, dropped_new)
            draft["assign"].update(placed)
            if placed:
                notes.append(f"{p['weekday_ja']}曜: 新しい枠を後から {len(placed)} 件置きました")
        for k in dropped_new:
            if k not in draft["assign"]:
                draft["unplaced"][k] = "入りきらない（希望の時間帯・コースの空き・NG/性別を見て置ける所が無い）"
        if reverted:
            notes.append(f"{p['weekday_ja']}曜: マスターに戻せた項目 {reverted}")
    return notes


def check_move(draft: dict, key: str, course_id: str | None, start_min: int) -> dict:
    """1 枠を動かせるか確かめる (決まりを破るなら TemplateError)。戻り値は {course, start, new_issues, travel}."""
    wd = int(key.split(":")[2]) if key.startswith("new:") else int(key.split(":")[1])
    p = P.build_day(draft, wd, exclude_hold={key})
    if p is None:
        raise TemplateError("その曜日は営業日ではありません（曜日は動かせません）")
    s = next((x for x in p["slots"] if x["key"] == key), None)
    if s is None:
        raise TemplateError("その枠は今の条件では下書きにありません")
    if course_id is None:
        raise TemplateError("コースを指定してください")
    c = next((c for c in p["courses"] if c["id"] == course_id), None)
    if c is None:
        raise TemplateError(f"{p['weekday_ja']}曜にそのコースはありません（人のいるコースだけに動かせます）")
    if c["id"] not in s["allowed"]:
        who = M.short_name((c["staff"] or {}).get("name")) or "仮の職員"
        raise TemplateError(f"{c['label']}（{who}さん）は {M.short_name(s['patient'])}様に当てられません（NG か性別の制限）")
    if start_min % 5:
        raise TemplateError("時刻は 5 分刻みです")
    if s["locked"] and draft["scenario"].get("red", "keep") == "keep" and start_min != s["start"]:
        raise TemplateError(f"赤ピン（完全固定）の枠は曜日・時刻を変えません（今 {M.fmt(s['start'])}）。"
                            "コースだけ替えられます")
    if not (s["win"][0] <= start_min <= s["win"][1]):
        raise TemplateError(f"{M.fmt(start_min)} は希望の範囲（{M.fmt(s['win'][0])}〜{M.fmt(s['win'][1])} に開始）の外です")
    if s["pair"]:
        raise TemplateError("2 名体制の枠は 2 枠を一緒に動かすので、手では動かせません（最適化に任せてください）")
    before = draft_assign(draft, p)
    after = {**before, key: {"course": c["id"], "start": start_min}}
    m0, m1 = M.evaluate(p, before), M.evaluate(p, after)
    return {"course": c["id"], "course_label": c["label"], "start": start_min, "weekday": wd,
            "new_issues": [x for x in m1["issues"] if x not in m0["issues"]],
            "travel": [m0["travel_min"], m1["travel_min"]]}


def slot_cards(draft: dict, p: dict, other: tuple[dict, dict] | None = None) -> list[dict]:
    """1 曜日の枠 (画面用). other = (比べる相手の問題, 割り当て)。無ければマスター。"""
    mine = draft_assign(draft, p)
    base_p, base_a = other if other else (p, master_assign(p))
    base_slots = {s["key"]: s for s in base_p["slots"]}
    out = []
    unplaced = draft.get("unplaced") or {}
    held = set(draft.get("held") or [])
    for s in sorted(p["slots"], key=lambda s: (mine.get(s["key"], {}).get("start", s["start"]), s["patient_code"] or "")):
        a = mine.get(s["key"])
        b = base_a.get(s["key"])
        if s.get("new") and a is None and s["key"] not in unplaced:
            continue  # まだ置いていない新しい枠は盤面に出さない（トレイに出る）
        placed = a is not None
        bs = base_slots.get(s["key"], s)
        course_changed = bool(placed and (b is None or a["course"] != b["course"]))
        time_changed = bool(placed and (b is None or a["start"] != b["start"]))
        out.append({
            "key": s["key"], "patient_id": s["patient_id"], "patient": s["patient"], "code": s["patient_code"],
            "slot_index": s["slot_index"], "weekday": p["weekday"],
            "start": a["start"] if placed else None, "dur": s["dur"],
            "course_id": a["course"] if placed else None,
            "base_course_id": b["course"] if b else None, "base_start": b["start"] if b else None,
            "locked": s["locked"], "new": bool(s.get("new")), "held": s["key"] in held, "pair": bool(s["pair"]),
            "course_changed": course_changed, "time_changed": time_changed, "changed": course_changed or time_changed,
            "win": s["win"], "why": s["why"], "time_type": s["time_type"],
            "placed": placed, "unplaced_reason": unplaced.get(s["key"]),
            "base_why": bs.get("why"),
        })
    return out
