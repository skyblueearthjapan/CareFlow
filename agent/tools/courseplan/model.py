"""コースのならし — 問題の組み立てと、いま／案の数え方（標準ライブラリだけ・ortools なし）。

用語: 枠（slot）= 固定訪問の枠 1 行（患者 × 曜日 × slot_index）。コース = course_templates の 1 本（1 人 1 日の列）。
前提（PO 2026-10-12 の二段の考え方）: 全員が休みなく稼働する。その曜日に出勤の型（staff_shifts）がある職員
（主担当拠点がこの拠点・新人を除く）1 人に 1 コースを当てる（直近の週にその人が多く持ったコースから・M はマネージャーだけ）。
コースの勤務時刻・性別・NG・固定イベントはその人で見る（担当は週ごとに入れ替わるので、この当て方は仮。設計書の Q を参照）。

決まり（docs/plans/week-leveling-rules-2026-10-02.md と同じ値。solver.py は ortools を読み込むので数え方だけここに写す。
solve.py は実行時に solver.py の値と同じかを確かめる）:
  直線 20km/h・別住所へ移るとき 8 分・約 100m 以内は同じ建物（移動 0・2 名で 90 分の枠）・昼休み 11:30〜13:30 で 30 分は必ず
  （45 分をなるべく）・予定の前後 15 分・正規のコースは 1 日 6 名まで（config の cap_regular）・M（あふれ）は上限なし。
赤ピン（movability=locked）: 曜日・時刻を動かさない。コースは縛らない（red-pin-simplify-design.md K-2）。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

WD = "月火水木金土日"
WD_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
COURSE_CODE_RE = re.compile(r"^[A-Z]$")  # A〜E と M（臨時・M2 などは使わない）
MANAGER_LABEL = "M"

SPEED_KMH = 20.0
BUFFER_MIN = 8
SAME_ADDR_DEG = 0.001
PAIR_BLOCK_MIN = 90
LUNCH_WIN = (11 * 60 + 30, 13 * 60 + 30)
LUNCH_MIN_HARD = 30
LUNCH_TARGET = 45
EVENT_BUFFER_MIN = 15
BUSINESS = (9 * 60 + 30, 18 * 60)  # 営業 9:30〜18:00（scheduling_settings の既定）
BUSINESS_END = BUSINESS[1]
FIXED_JOIN_MAX_GAP = 30


def hm(t: str) -> int:
    h, m = str(t)[:5].split(":")
    return int(h) * 60 + int(m)


def fmt(m: int) -> str:
    return f"{m // 60}:{m % 60:02d}"


def short_name(name: str | None) -> str:
    return (name or "").replace("　", " ").split(" ")[0]


def km(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def bucket(loc):
    return (round(loc[0] / SAME_ADDR_DEG), round(loc[1] / SAME_ADDR_DEG))


def travel_min(a, b) -> int:
    if bucket(a) == bucket(b):
        return 0
    return max(1, round(km(a, b) / SPEED_KMH * 60))


def pref_window(wp: dict | None, dur: int) -> tuple[str, tuple[int, int] | None]:
    """希望の時間帯（開始してよい範囲）。solver.pref_window と同じ。固定は None（その時刻だけ）。
    希望が無い（weekly_pattern が空・time_type が無い）ときは ("なし", None)。"""
    wp = wp or {}
    tt = wp.get("time_type")
    if not tt:
        return "なし", None
    if tt == "固定":
        return tt, None
    if tt == "時間帯" and wp.get("preferred_start") and wp.get("preferred_end"):
        return tt, (hm(wp["preferred_start"]), hm(wp["preferred_end"]))
    if tt == "午前":
        return tt, (9 * 60, 12 * 60 - 5)
    if tt == "午後":
        return tt, (13 * 60, BUSINESS_END - dur)
    return tt, (9 * 60, BUSINESS_END - dur)


def join_fixed(win: tuple[int, int], fs: int) -> tuple[int, int]:
    """solver.join_fixed と同じ: 今の時刻はいつでも候補。希望の外でも 30 分以内なら範囲をつなぐ。"""
    lo, hi = win
    if lo <= fs <= hi:
        return win
    if fs < lo - FIXED_JOIN_MAX_GAP or fs > hi + FIXED_JOIN_MAX_GAP:
        return (fs, fs)
    return (min(lo, fs), max(hi, fs))


# ---------------------------------------------------------------------------
# 問題の組み立て
# ---------------------------------------------------------------------------


def people_of_day(raw: dict, office_id: str, wd: int, codes: list[str] | None = None) -> list[dict]:
    """全員稼働の前提で、その曜日にコースを持つ人: 主担当拠点がこの拠点・新人でない・勤務の型でその曜日が出勤。
    codes を渡せばその職員コードの人だけ（config で人を決めたいとき）。"""
    shifts = {(r["staff_id"], int(r["weekday"])): r for r in raw.get("shifts") or []}
    out = []
    for s in sorted(raw.get("staff") or [], key=lambda s: s.get("code") or ""):
        if codes is not None:
            if s.get("code") not in codes:
                continue
        elif s.get("primary_office_id") != office_id or s.get("is_trainee"):
            continue
        sh = shifts.get((s["id"], wd))
        if not (sh and sh.get("is_on") and sh.get("start_time") and sh.get("end_time")):
            continue
        out.append({**s, "shift": (hm(sh["start_time"]), hm(sh["end_time"])),
                    "manager": s.get("role") == "manager"})
    return out


def match_people_to_templates(raw: dict, people: list[dict], templates: list[dict], wd: int) -> dict[str, str]:
    """人 → コース（テンプレート）を 1 対 1 で決める。直近の週にその人がそのコースを持った回数（同じ曜日を優先・
    次に全曜日）が多い組から。M はマネージャーだけ。残りはラベル順に空いたコースへ（職員は A〜E、マネージャーは M から）。"""
    same, alld = Counter(), Counter()
    for r in raw.get("usual") or []:
        k = (r["staff_id"], r["template_id"])
        alld[k] += int(r["n"])
        if int(r["weekday"]) == wd:
            same[k] += int(r["n"])
    ok = lambda p, t: p["manager"] or t["label"] != MANAGER_LABEL  # noqa: E731
    pairs = sorted(((same[(p["id"], t["id"])], alld[(p["id"], t["id"])], p, t) for p in people for t in templates
                    if ok(p, t) and alld[(p["id"], t["id"])]), key=lambda x: (-x[0], -x[1], x[2]["code"], x[3]["label"]))
    out: dict[str, str] = {}
    used: set[str] = set()
    for _a, _b, p, t in pairs:
        if p["id"] not in out and t["id"] not in used:
            out[p["id"]] = t["id"]
            used.add(t["id"])
    for p in people:
        if p["id"] in out:
            continue
        order = sorted(templates, key=lambda t: (t["label"] != MANAGER_LABEL, t["label"])) if p["manager"] \
            else sorted(templates, key=lambda t: t["label"])
        for t in order:
            if ok(p, t) and t["id"] not in used:
                out[p["id"]] = t["id"]
                used.add(t["id"])
                break
    return out


def office_by_label(raw: dict, label: str) -> dict:
    for o in raw.get("offices") or []:
        if o["short_label"] == label or o["id"] == label:
            return o
    raise ValueError(f"拠点 {label} が見つかりません（offices.short_label で指定）")


def operating(office: dict, wd: int) -> bool:
    ow = office.get("operating_weekdays")
    if ow is None:
        return wd < 6
    if isinstance(ow, list):
        return wd in [int(x) if str(x).isdigit() else WD_KEYS.index(str(x).lower()[:3]) for x in ow
                      if str(x).isdigit() or str(x).lower()[:3] in WD_KEYS]
    if isinstance(ow, dict):
        return bool(ow.get(str(wd), ow.get(WD_KEYS[wd], False)))
    return True


def build_problem(raw: dict, office_label: str, wd: int, opts: dict | None = None) -> dict:
    """1 拠点 × 1 曜日の問題（JSON にできる dict）。

    opts: cap_regular（6）/ red: keep（赤ピンの時刻を守る・既定）| ignore（赤を全部外したら＝K-4 の試算）/
          no_pref: fixed（希望の無い方は今の時刻のまま・既定）| free（終日の範囲で動かしてよい）。
          staff_codes: コースを持つ人を職員コードで決める（省略で拠点・新人・勤務の型から）。
    """
    opts = opts or {}
    cap = int(opts.get("cap_regular", 6))
    red = opts.get("red", "keep")
    no_pref = opts.get("no_pref", "fixed")
    office = office_by_label(raw, office_label)
    office_label_of = {o["id"]: o["short_label"] for o in raw.get("offices") or []}
    events = defaultdict(list)
    for e in raw.get("event_defaults") or []:
        events[(e["staff_id"], int(e["weekday"]))].append((hm(e["start_time"]), hm(e["end_time"])))
    ng = defaultdict(set)
    for r in raw.get("ng") or []:
        ng[r["patient_id"]].add(r["staff_id"])
    pats = {p["id"]: p for p in raw.get("patients") or []}
    templates = {t["id"]: t for t in raw.get("templates") or []}
    notes: list[str] = []

    pfv_today = [f for f in raw.get("pfv") or [] if int(f["weekday"]) == wd and f["patient_id"] in pats]
    mine = []
    for f in pfv_today:
        t = templates.get(f.get("course_template_id"))
        p = pats[f["patient_id"]]
        if (t and t["office_id"] == office["id"]) or (t is None and p["primary_office_id"] == office["id"]):
            mine.append(f)
    office_templates = sorted((t for t in templates.values()
                               if t["office_id"] == office["id"] and COURSE_CODE_RE.match(t["label"] or "")),
                              key=lambda t: t["label"])
    people = people_of_day(raw, office["id"], wd, opts.get("staff_codes"))
    match = match_people_to_templates(raw, people, office_templates, wd)
    courses = []
    for p in people:
        tid = match.get(p["id"])
        if tid is None:
            notes.append(f"{short_name(p['name'])}さん（{WD[wd]}）に割り当てるコースが足りません（コースの数を確かめてください）")
            continue
        t = templates[tid]
        courses.append({
            "id": tid,
            "label": f"{office['short_label']}{t['label']}",
            "manager": p["manager"],
            "staff": {"id": p["id"], "code": p["code"], "name": p["name"], "sex": p.get("sex")},
            "shift": list(p["shift"]),
            "events": sorted([list(x) for x in events.get((p["id"], wd), [])]),
            "cap": 99 if p["manager"] else cap,
        })
    courses.sort(key=lambda c: (c["manager"], c["label"]))
    open_ids = {c["id"] for c in courses}
    closed = sorted({templates[f["course_template_id"]]["label"] for f in mine
                     if f.get("course_template_id") in templates and f["course_template_id"] not in open_ids})
    if closed:
        notes.append(f"{office['short_label']} {WD[wd]}曜: 人の数よりコースが多いので、コース {'・'.join(closed)} の枠は別のコースへ移す前提です")

    slots = []
    by_patient = defaultdict(list)
    for f in mine:
        by_patient[f["patient_id"]].append(f)
    for f in mine:
        p = pats[f["patient_id"]]
        if p.get("lat") is None or p.get("lng") is None:
            notes.append(f"{p['name']}様（{WD[wd]}）は住所の座標が無いので、今のまま動かしません")
            continue
        start, dur = hm(f["start_time"]), int(f["duration_min"] or 35)
        locked = (f.get("movability") == "locked") or bool(f.get("is_pinned"))
        tt, win = pref_window(p.get("weekly_pattern"), dur)
        if locked and red == "keep":
            win, why = (start, start), "赤ピン"
        elif win is None and tt == "なし" and no_pref == "free":
            win, why = (9 * 60, BUSINESS_END - dur), "希望なし（終日）"
        elif win is None:
            win, why = (start, start), ("希望が固定" if tt == "固定" else "希望なし")
        else:
            win, why = join_fixed(win, start), f"希望 {tt}"
        pair = None
        same_day = by_patient[f["patient_id"]]
        if len(same_day) >= 2 and len({int(x.get("slot_index") or 0) for x in same_day}) >= 2:
            pair = f["patient_id"]  # 2 名体制（同じ曜日に slot 0 と 1）
        allowed = []
        for c in courses:
            s = c["staff"]
            if s and (s["id"] in ng[p["id"]] or (p.get("sex_restriction") == "female_only" and s.get("sex") == "male")):
                continue
            allowed.append(c["id"])
        cur = f.get("course_template_id")
        slots.append({
            "key": f["id"],
            "pfv_id": f["id"],
            "patient_id": p["id"],
            "patient": p["name"],
            "patient_code": p.get("code"),
            "patient_office": office_label_of.get(p["primary_office_id"], ""),
            "slot_index": int(f.get("slot_index") or 0),
            "start": start,
            "dur": dur,
            "cur_course": cur,
            "cur_course_label": (f"{office_label_of.get(templates[cur]['office_id'], '')}{templates[cur]['label']}"
                                 if cur in templates else None),
            "cur_open": cur in open_ids,
            "locked": locked,
            "time_type": tt,
            "win": list(win),
            "why": why,
            "loc": [float(p["lat"]), float(p["lng"])],
            "female_only": p.get("sex_restriction") == "female_only",
            "ng": sorted(ng[p["id"]]),
            "pair": pair,
            "allowed": allowed,
        })
    return {
        "office": office["short_label"],
        "office_id": office["id"],
        "weekday": wd,
        "weekday_ja": WD[wd],
        "operating": operating(office, wd),
        "courses": courses,
        "slots": slots,
        "notes": notes,
        "cap_regular": cap,
        "opts": {"red": red, "no_pref": no_pref, "cap_regular": cap},
    }


# ---------------------------------------------------------------------------
# 数える（いま／案）
# ---------------------------------------------------------------------------


def current_assignment(problem: dict) -> dict[str, dict]:
    """いまの型。人のいないコース（その曜日の人の数よりコースが多い）の枠もそのコースのまま（unstaffed に数える）。
    まだ枠の無い方の新しい枠（new）は、いまの型には無いので入れない。"""
    return {s["key"]: {"course": s["cur_course"], "start": s["start"]} for s in problem["slots"] if not s.get("new")}


def _lunch_gap(intervals: list[tuple[int, int]]) -> int:
    """11:30〜13:30 の中で、訪問・予定の無い一番長い空き（分）。"""
    lo, hi = LUNCH_WIN
    busy = sorted((max(a, lo), min(b, hi)) for a, b in intervals if b > lo and a < hi)
    best, t = 0, lo
    for a, b in busy:
        best = max(best, a - t)
        t = max(t, b)
    return max(best, hi - t)


def course_route(problem: dict, assign: dict, cid: str) -> list[dict]:
    return sorted((s for s in problem["slots"] if assign.get(s["key"], {}).get("course") == cid),
                  key=lambda s: (assign[s["key"]]["start"], s["slot_index"]))


def evaluate(problem: dict, assign: dict) -> dict:
    """コースごと・全体の数（移動・件数・重なり・上限・勤務時刻・昼休み・NG/性別・希望の外・コースなし）。"""
    out_courses = []
    total = Counter()
    issues: list[str] = []
    slot_by_key = {s["key"]: s for s in problem["slots"]}
    for c in problem["courses"]:
        route = course_route(problem, assign, c["id"])
        st = lambda s: assign[s["key"]]["start"]  # noqa: E731
        travel = conflicts = 0
        for a, b in zip(route, route[1:]):
            same = bucket(a["loc"]) == bucket(b["loc"])
            if same and st(b) + b["dur"] <= st(a) + max(PAIR_BLOCK_MIN, a["dur"]):
                continue  # 同じ建物の 2 名（1 人で 90 分の枠）
            t = 0 if same else travel_min(a["loc"], b["loc"])
            travel += t
            if st(b) < st(a) + a["dur"] + t + (BUFFER_MIN if t > 0 else 0):
                conflicts += 1
                issues.append(f"{c['label']} {fmt(st(a))} {short_name(a['patient'])}様 → {fmt(st(b))} {short_name(b['patient'])}様 が間に合わない")
        ev_hits = 0
        for s in route:
            a, b = st(s), st(s) + s["dur"]
            if any(a < eb + EVENT_BUFFER_MIN and b > ea - EVENT_BUFFER_MIN for ea, eb in c["events"]):
                ev_hits += 1
                issues.append(f"{c['label']} {fmt(a)} {short_name(s['patient'])}様 が固定イベントと重なる")
        sh_a, sh_b = c["shift"]
        end_ok = max(sh_b, BUSINESS_END) if c["manager"] else sh_b
        outside = [s for s in route if st(s) < sh_a or st(s) + s["dur"] > end_ok]
        for s in outside:
            issues.append(f"{c['label']} {fmt(st(s))} {short_name(s['patient'])}様 が勤務時刻（{fmt(sh_a)}〜{fmt(sh_b)}）の外")
        lunch = None
        if sh_a <= LUNCH_WIN[0] and sh_b >= LUNCH_WIN[1]:
            lunch = _lunch_gap([(st(s), st(s) + s["dur"]) for s in route] + [tuple(e) for e in c["events"]])
            if lunch < LUNCH_MIN_HARD:
                issues.append(f"{c['label']} 昼休みが {lunch} 分しか取れない")
        n = len(route)
        over = max(0, n - c["cap"])
        if over:
            issues.append(f"{c['label']} {n} 名（上限 {c['cap']} 名）")
        bad = [s for s in route if c["id"] not in s["allowed"]]
        for s in bad:
            issues.append(f"{c['label']} {short_name(s['patient'])}様 にふだんの担当が NG か性別の制限")
        out_courses.append({
            "id": c["id"], "label": c["label"], "manager": c["manager"],
            "staff": short_name((c["staff"] or {}).get("name")) or "（不明）",
            "count": n, "travel_min": travel, "conflicts": conflicts, "event_hits": ev_hits,
            "outside_shift": len(outside), "lunch_gap": lunch, "over_cap": over, "ng_or_sex": len(bad),
        })
        total.update(travel_min=travel, conflicts=conflicts, event_hits=ev_hits, outside_shift=len(outside),
                     over_cap=over, ng_or_sex=len(bad), lunch_short=int(lunch is not None and lunch < LUNCH_MIN_HARD),
                     lunch_under_target=int(lunch is not None and lunch < LUNCH_TARGET))
    no_course = [k for k, a in assign.items() if a.get("course") is None]
    open_ids = {c["id"] for c in problem["courses"]}
    unstaffed = [k for k, a in assign.items() if a.get("course") is not None and a["course"] not in open_ids]
    out_win = [k for k, a in assign.items()
               if k in slot_by_key and not (slot_by_key[k]["win"][0] <= a["start"] <= slot_by_key[k]["win"][1])]
    regular = [c["count"] for c in out_courses if not c["manager"]]
    manager_n = sum(c["count"] for c in out_courses if c["manager"])
    return {
        "courses": out_courses,
        "slots": len(problem["slots"]),
        "travel_min": total["travel_min"],
        "conflicts": total["conflicts"],
        "event_hits": total["event_hits"],
        "outside_shift": total["outside_shift"],
        "over_cap": total["over_cap"],
        "ng_or_sex": total["ng_or_sex"],
        "lunch_short": total["lunch_short"],
        "lunch_under_target": total["lunch_under_target"],
        "no_course": len(no_course),
        "unstaffed": len(unstaffed),
        "outside_window": len(out_win),
        "regular_counts": regular,
        "count_spread": (max(regular) - min(regular)) if regular else 0,
        "manager_visits": manager_n,
        "issues": issues,
    }


def changes(problem: dict, assign: dict) -> list[dict]:
    """いまの枠と違う枠（コース・時刻）。"""
    out = []
    labels = {c["id"]: c["label"] for c in problem["courses"]}
    for s in problem["slots"]:
        a = assign.get(s["key"])
        if a is None:
            continue
        new = bool(s.get("new"))
        cc = new or a["course"] != s["cur_course"]
        tc = new or a["start"] != s["start"]
        if cc or tc:
            out.append({
                "key": s["key"], "patient_id": s["patient_id"], "patient": s["patient"],
                "weekday": problem["weekday"], "slot_index": s["slot_index"],
                "from_course": s["cur_course"],
                "from_label": "（まだ枠なし）" if new else (s["cur_course_label"] or "（コースなし）"),
                "to_course": a["course"], "to_label": labels.get(a["course"], "（コースなし）"),
                "from_start": s["start"], "to_start": a["start"], "course_changed": cc, "time_changed": tc,
                "locked": s["locked"], "new": new,
            })
    return out


def badness(m: dict) -> tuple:
    """守る決まりの破れの合計（小さいほどよい）と、昼休み 45 分が取れないコースの数。"""
    hard = (m["conflicts"] + m["event_hits"] + m["outside_shift"] + m["over_cap"] + m["ng_or_sex"]
            + m["lunch_short"] + m["no_course"] + m["unstaffed"] + m["outside_window"])
    return hard, m["lunch_under_target"]


def tidy(problem: dict, assign: dict, w_change: int = 20) -> tuple[dict, int]:
    """計算の後の片付け: 変えた枠を 1 つずつ今の型に戻してみて、決まりの破れが増えず、移動の増え方が
    戻す 1 項目あたり w_change 分以内なら戻す（変える枠をなるべく少なく）。2 名体制の枠は触らない。
    戻り値: (片付けた割り当て, 戻した項目の数)"""
    assign = {k: dict(v) for k, v in assign.items()}
    slots = {s["key"]: s for s in problem["slots"]}
    reverted = 0
    improved = True
    while improved:
        improved = False
        base = evaluate(problem, assign)
        for c in changes(problem, assign):
            s = slots[c["key"]]
            if s["pair"] or s.get("new") or s.get("held"):
                continue
            tries = []
            if c["course_changed"] and c["time_changed"] and s["cur_open"]:
                tries.append(({"course": s["cur_course"], "start": s["start"]}, 2))
            if c["time_changed"]:
                tries.append(({"course": c["to_course"], "start": s["start"]}, 1))
            if c["course_changed"] and s["cur_open"]:
                tries.append(({"course": s["cur_course"], "start": c["to_start"]}, 1))
            for cand, gain in tries:
                trial = {**assign, c["key"]: cand}
                m = evaluate(problem, trial)
                if badness(m) <= badness(base) and m["travel_min"] <= base["travel_min"] + w_change * gain:
                    assign, reverted, improved = trial, reverted + gain, True
                    break
            if improved:
                break
    return assign, reverted


def metrics_line(m: dict) -> str:
    return (f"移動 {m['travel_min']} 分・間に合わない {m['conflicts']}・上限超え {m['over_cap']}・勤務時刻の外 {m['outside_shift']}"
            f"・昼休み 30 分未満 {m['lunch_short']}・NG/性別 {m['ng_or_sex']}・コースなし {m['no_course']}・人のいないコース {m['unstaffed']}"
            f"・件数 {'/'.join(map(str, m['regular_counts']))}（差 {m['count_spread']}）・マネージャー {m['manager_visits']}")
