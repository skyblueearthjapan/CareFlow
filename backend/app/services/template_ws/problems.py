"""agent/tools/template_ws/problems.py の写し（下書き dict → 曜日ごとの問題）。変えたら両方そろえる。"""

# ruff: noqa
from __future__ import annotations

import copy
from collections import Counter, defaultdict

from . import model as M
from .errors import TemplateError

PREF_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
VIRTUAL_SHIFT = (9 * 60, 18 * 60)


def day_index(x) -> int:
    s = str(x).strip().lower()
    if s[:3] in PREF_DAYS:
        return PREF_DAYS[s[:3]]
    if s in M.WD:
        return M.WD.index(s)
    if s.isdigit() and 0 <= int(s) <= 6:
        return int(s)
    raise TemplateError(f"曜日は mon〜sat（または 月〜土・0〜5）で書いてください: {x}")


def parse_hm_range(s: str) -> list[int]:
    try:
        a, b = str(s).split("-")
        lo, hi = M.hm(a.strip()), M.hm(b.strip())
    except (ValueError, IndexError):
        raise TemplateError(f"時間帯は HH:MM-HH:MM で書いてください: {s}") from None
    if lo > hi:
        raise TemplateError(f"時間帯の始まりが終わりより後です: {s}")
    return [lo, hi]


def office_of(draft: dict) -> dict:
    return M.office_by_label(draft["snapshot"]["raw"], draft["office"])


def scenario_raw(draft: dict) -> dict:
    """もしもの条件のうち材料に効くもの（外す職員 = 勤務の型をすべて休みに）を当てた材料。"""
    raw = draft["snapshot"]["raw"]
    drop = set(draft["scenario"].get("drop_staff") or [])
    if not drop:
        return raw
    ids = {s["id"] for s in raw.get("staff") or [] if s.get("code") in drop}
    out = dict(raw)
    out["shifts"] = [({**r, "is_on": False} if r["staff_id"] in ids else r) for r in raw.get("shifts") or []]
    return out


def operating_days(draft: dict) -> list[int]:
    ofc = office_of(draft)
    return [wd for wd in range(6) if M.operating(ofc, wd)]


# ---------------------------------------------------------------------------
# まだ枠の無い方
# ---------------------------------------------------------------------------


def _candidates_by_code(raw: dict) -> dict[str, dict]:
    return {c.get("code"): c for c in raw.get("candidates") or [] if c.get("code")}


def candidate_rows(draft: dict) -> list[dict]:
    """この下書きで置こうとする方: この拠点の「まだ枠の無い方」（希望あり）＋ --add-patient で足した方。"""
    raw = draft["snapshot"]["raw"]
    adds = draft["scenario"].get("add_patients") or {}
    out = []
    for c in raw.get("candidates") or []:
        if c.get("code") in adds or (c.get("primary_office_id") == draft["office_id"] and c.get("weekly_pattern")):
            out.append(c)
    return sorted(out, key=lambda c: c.get("code") or "")


def _day_load(draft: dict, raw: dict) -> dict[int, float]:
    """曜日ごとの混み具合 = この拠点の今の枠の数 ÷ その曜日のコースの数（人の数）。"""
    scope = set(draft["snapshot"]["scope_patients"])
    n = Counter(int(f["weekday"]) for f in raw.get("pfv") or [] if f["patient_id"] in scope)
    out = {}
    for wd in operating_days(draft):
        people = len(M.people_of_day(raw, draft["office_id"], wd))
        out[wd] = n[wd] / max(1, people)
    return out


def candidate_specs(draft: dict) -> list[dict]:
    """置こうとする方ごとの {patient, days, win, dur, pair, reason}。reason があれば置けない（理由）。"""
    raw = scenario_raw(draft)
    adds = draft["scenario"].get("add_patients") or {}
    prefs = draft["scenario"].get("prefs") or {}
    load = _day_load(draft, raw)
    specs = []
    for c in candidate_rows(draft):
        wp = c.get("weekly_pattern") or {}
        add = adds.get(c.get("code")) or {}
        dur = int(wp.get("service_minutes") or 35) or 35
        reason = None
        if c.get("lat") is None or c.get("lng") is None:
            reason = "住所の座標が無い"
        days_pref, ng_days = [], set()
        for d in wp.get("preferred_weekdays") or []:
            try:
                days_pref.append(day_index(d))
            except TemplateError:
                pass
        for d in wp.get("ng_weekdays") or []:
            try:
                ng_days.add(day_index(d))
            except TemplateError:
                pass
        entries = {}  # 曜日ごとの細かい希望（entries: [{weekday, time_type, preferred_start/end, service_minutes, staff_count}]）
        for e in wp.get("entries") or []:
            try:
                entries[day_index(e.get("weekday"))] = e
            except (TemplateError, AttributeError):
                pass
        open_days = [d for d in operating_days(draft) if d not in ng_days]
        if add.get("days"):
            days = sorted(int(d) for d in add["days"])
        else:
            days_pref = days_pref or sorted(entries)
            freq = int(wp.get("frequency_per_week") or 0) or len(days_pref)
            pool = [d for d in (days_pref or open_days) if d in open_days]
            if not freq:
                days, reason = [], reason or "希望の回数・曜日が無い（--add-patient で曜日を指定できます）"
            elif len(pool) <= freq:
                days = sorted(pool)
                if len(pool) < freq:
                    reason = None if pool else (reason or "希望の曜日が営業日にない")
            else:
                days = []
                for _ in range(freq):
                    best = min((d for d in pool if d not in days),
                               key=lambda d: (load.get(d, 0) + sum(0.5 for x in days if abs(x - d) == 1), d))
                    days.append(best)
                days.sort()
        for d in days:  # 選んだ曜日は混み具合に足す（次の方は別の曜日へ）
            load[d] = load.get(d, 0) + 1 / max(1, len(M.people_of_day(raw, draft["office_id"], d)))
        tt, win = M.pref_window(wp, dur)
        if add.get("win"):
            win, why = tuple(add["win"]), "もしも（足した方の時間帯）"
        elif tt == "固定" and wp.get("preferred_start"):
            s = M.hm(wp["preferred_start"])
            win, why = (s, s), "希望 固定"
        elif win is None:
            win, why = (9 * 60, M.BUSINESS_END - dur), "時間の希望なし（終日）"
        else:
            why = f"希望 {tt}"
        win = (max(win[0], 8 * 60), min(win[1], M.BUSINESS_END - dur)) if win[0] <= win[1] else win
        pair = bool(c.get("requires_multiple_staff"))
        per_day = {}
        for d in days:
            w = (prefs.get(c.get("code")) or {}).get(str(d))
            e = entries.get(d)
            if w:
                per_day[d] = (tuple(w), "もしも（希望の時間帯）", dur, pair)
            elif e and not add.get("win"):
                ed = int(e.get("service_minutes") or dur) or dur
                ett, ew = M.pref_window({**wp, **{k: v for k, v in e.items() if v is not None}}, ed)
                if ett == "固定" and e.get("preferred_start"):
                    ew = (M.hm(e["preferred_start"]),) * 2
                ew = ew or (9 * 60, M.BUSINESS_END - ed)
                per_day[d] = (tuple(ew), f"希望 {ett}（曜日ごと）", ed, pair or int(e.get("staff_count") or 1) >= 2)
            else:
                per_day[d] = (win, why, dur, pair)
        specs.append({"patient": c, "days": days, "per_day": per_day, "dur": dur, "time_type": tt or "なし",
                      "pair": pair, "reason": reason})
    return specs


def new_key(pid: str, wd: int, slot_index: int) -> str:
    return f"new:{pid}:{wd}:{slot_index}"


def _new_slots(draft: dict, problem: dict, raw: dict) -> list[dict]:
    wd = problem["weekday"]
    ng = defaultdict(set)
    for r in raw.get("ng") or []:
        ng[r["patient_id"]].add(r["staff_id"])
    office_label_of = {o["id"]: o["short_label"] for o in raw.get("offices") or []}
    out = []
    for sp in candidate_specs(draft):
        if sp["reason"] and not sp["days"]:
            continue
        if wd not in sp["days"] or sp["patient"].get("lat") is None or sp["patient"].get("lng") is None:
            continue
        p = sp["patient"]
        win, why, dur, pair = sp["per_day"][wd]
        if win[0] > win[1]:
            continue
        for si in ((0, 1) if pair else (0,)):
            allowed = []
            for c in problem["courses"]:
                s = c["staff"]
                if s is None:  # 仮の職員（性別が分からない）は女性限定の方に当てない
                    if p.get("sex_restriction") != "female_only":
                        allowed.append(c["id"])
                    continue
                if s["id"] in ng[p["id"]] or (p.get("sex_restriction") == "female_only" and s.get("sex") == "male"):
                    continue
                allowed.append(c["id"])
            out.append({
                "key": new_key(p["id"], wd, si), "pfv_id": None, "patient_id": p["id"], "patient": p["name"],
                "patient_code": p.get("code"), "patient_office": office_label_of.get(p.get("primary_office_id"), ""),
                "slot_index": si, "start": int(win[0]), "dur": dur, "cur_course": None, "cur_course_label": None,
                "cur_open": False, "locked": False, "time_type": sp["time_type"], "win": [int(win[0]), int(win[1])],
                "why": why, "loc": [float(p["lat"]), float(p["lng"])],
                "female_only": p.get("sex_restriction") == "female_only", "ng": sorted(ng[p["id"]]),
                "pair": p["id"] if pair else None, "allowed": allowed, "new": True,
            })
    return out


# ---------------------------------------------------------------------------
# 曜日ごとの問題
# ---------------------------------------------------------------------------


def _apply_staff_count(draft: dict, problem: dict, n: int) -> None:
    """もしもの職員の数。減らすときは正規のコースをラベルの後ろから閉じる（マネージャーのコースは最後まで残す）。
    増やすときは空いたコース（無ければ仮のコース）に「仮の職員」（9:00〜18:00・性別不明）を当てる。"""
    courses = problem["courses"]
    raw = draft["snapshot"]["raw"]
    if n < len(courses):
        regular = sorted((c for c in courses if not c["manager"]), key=lambda c: c["label"], reverse=True)
        managers = sorted((c for c in courses if c["manager"]), key=lambda c: c["label"], reverse=True)
        drop = (regular + managers)[: len(courses) - n]
        ids = {c["id"] for c in drop}
        problem["courses"] = [c for c in courses if c["id"] not in ids]
        problem["notes"].append(f"もしも: {problem['weekday_ja']}曜は {n} 人（{'・'.join(c['label'] for c in drop)} を閉じた）")
    elif n > len(courses):
        used = {c["id"] for c in courses}
        free = sorted((t for t in raw.get("templates") or [] if t["office_id"] == draft["office_id"]
                       and M.COURSE_CODE_RE.match(t["label"] or "") and t["label"] != M.MANAGER_LABEL
                       and t["id"] not in used), key=lambda t: t["label"])
        added = n - len(courses)
        for k in range(added):
            t = free[k] if k < len(free) else None
            courses.append({
                "id": t["id"] if t else f"virtual-{k + 1}",
                "label": f"{draft['office']}{t['label'] if t else f'仮{k + 1}'}",
                "manager": False, "staff": None, "virtual": True, "shift": list(VIRTUAL_SHIFT), "events": [],
                "cap": problem["cap_regular"],
            })
        problem["notes"].append(f"もしも: {problem['weekday_ja']}曜は {n} 人（仮の職員 {added} 人を足した）")
        problem["courses"] = sorted(courses, key=lambda c: (c["manager"], c["label"]))
    open_ids = {c["id"] for c in problem["courses"]}
    for s in problem["slots"]:
        s["cur_open"] = s["cur_course"] in open_ids
        if any(c.get("virtual") for c in problem["courses"]):
            for c in problem["courses"]:
                if c.get("virtual") and c["id"] not in s["allowed"] and not s["female_only"]:
                    s["allowed"].append(c["id"])


def build_day(draft: dict, wd: int, *, hold: bool = True, exclude_hold: set[str] | None = None) -> dict | None:
    """下書きの 1 曜日の問題。営業日でなければ None。"""
    if not M.operating(office_of(draft), wd):
        return None
    sc = draft["scenario"]
    raw = scenario_raw(draft)
    p = M.build_problem(raw, draft["office"], wd, {"red": sc.get("red", "keep"), "no_pref": sc.get("no_pref", "fixed"),
                                                  "cap_regular": int(sc.get("cap", 6))})
    n = (sc.get("staff_count") or {}).get(str(wd))
    if n is not None:
        _apply_staff_count(draft, p, int(n))
    p["slots"] += _new_slots(draft, p, raw)
    prefs = sc.get("prefs") or {}
    for s in p["slots"]:
        if s.get("new"):
            continue
        w = (prefs.get(s.get("patient_code")) or {}).get(str(wd))
        if w and not (s["locked"] and sc.get("red", "keep") == "keep"):
            s["win"], s["why"] = [int(w[0]), int(w[1])], "もしも（希望の時間帯）"
    exclude_hold = exclude_hold or set()
    if hold:
        held = set(draft.get("held") or []) - exclude_hold
        for s in p["slots"]:
            a = draft["assign"].get(s["key"])
            if s["key"] in held and a:
                s["win"], s["why"], s["held"] = [a["start"], a["start"]], "手で動かした枠", True
                s["allowed"] = [a["course"]] if a["course"] in s["allowed"] else []
    sids = draft.setdefault("sids", {})
    for s in sorted(p["slots"], key=lambda s: (s["start"], s.get("patient_code") or "", s["slot_index"])):
        if s["key"] not in sids:
            if s.get("new"):
                sids[s["key"]] = f"{M.WD_KEYS[wd]}-{s.get('patient_code') or 'new'}-{s['slot_index']}"
            else:
                taken = {v for v in sids.values() if v.startswith(M.WD_KEYS[wd]) and v[3:].isdigit()}
                i = len(taken) + 1
                while f"{M.WD_KEYS[wd]}{i:02d}" in taken:
                    i += 1
                sids[s["key"]] = f"{M.WD_KEYS[wd]}{i:02d}"
        s["sid"] = sids[s["key"]]
    return p


def build_week(draft: dict, weekdays: list[int] | None = None, **kw) -> list[dict]:
    out = []
    for wd in (weekdays if weekdays is not None else range(6)):
        p = build_day(draft, wd, **kw)
        if p is not None and p["slots"]:
            out.append(p)
    return out


def scope_patients(raw: dict, office: str) -> list[str]:
    """この拠点の型に関わる方（どれかの曜日でこの拠点の問題に入る方）＋この拠点のまだ枠の無い方。"""
    ofc = M.office_by_label(raw, office)
    ids = set()
    for wd in range(6):
        p = M.build_problem(raw, office, wd)
        ids |= {s["patient_id"] for s in p["slots"]}
    ids |= {c["id"] for c in raw.get("candidates") or [] if c.get("primary_office_id") == ofc["id"]}
    return sorted(ids)


def draft_assign(draft: dict, problem: dict) -> dict:
    """下書きのその曜日の割り当て（無い枠はマスターのまま・新しい枠は置けたものだけ）。"""
    out = {}
    for s in problem["slots"]:
        a = draft["assign"].get(s["key"])
        if a is not None:
            out[s["key"]] = {"course": a["course"], "start": int(a["start"])}
        elif not s.get("new"):
            out[s["key"]] = {"course": s["cur_course"], "start": s["start"]}
    return out


def master_assign(problem: dict) -> dict:
    return M.current_assignment(problem)


def find_course(problem: dict, label: str) -> dict:
    """コースを「A」「稲A」かテンプレート ID で探す（その曜日に人のいるコースだけ）。"""
    lab = str(label).strip()
    for c in problem["courses"]:
        if lab in (c["id"], c["label"], c["label"][len(problem["office"]):]):
            return c
    have = "・".join(c["label"] for c in problem["courses"])
    raise TemplateError(f"{problem['weekday_ja']}曜にコース {label} はありません（人のいるコース: {have}）")


def deep(x):
    return copy.deepcopy(x)
