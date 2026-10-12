"""ならしの結果（solve.py の result.json）を rk の案（plan.json・設計書 §3-3）に直す。標準ライブラリだけ。

docs/tools/week-leveling/apply/generic/build_ops.py と同じ手順を、Excel ではなく計算の結果から直接作る:
  step 0  手の上書き（manual_staff_override）の訪問の担当を外す … assign-staff-week（staff_id=null）
  step 1  入らない訪問を保留プールへ                          … return-to-pool（DELETE /visits）
  step 3  特別訪問週間の○を置く（拠点が違えば同じ拠点の M に置いて移す）… special-visit-place（＋ move-visit-week）
          今週だけ移動（コース・時刻）                         … move-visit-week（visit-move-week-only）
  step 4  コースに担当（日ごと・職員 1 人 = コース 1 つ）       … patch-course-staff（PATCH /courses）
build_ops.py との違い（お客様固有の決め打ちをやめた）:
  - コースのテンプレート ID は本番から読む（agent_extra.json）。使うのは 1 文字のコード（A〜E・M）だけ（臨時・M2 へは移さない）。
  - 「ある正規職員は月〜金だけ自拠点の A コース」→「正規の職員は、自分の拠点が開いている曜日は自分の拠点のコース」（config の拠点で決める）。
  - 拠点が開いていない曜日のテンプレートは使わない。
フィールド名は PLAN_FIELDS.md（I4 の rk/ops/visits.py と合わせる）。
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

WD = "月火水木金土日"
COURSE_CODE_RE = re.compile(r"^[A-Z]$")  # 通常の A〜E と M。M2・臨時などは使わない
MAX_PERMUTATIONS = 300_000

# report.check の文言 → 決まりの名前（rule, 見分ける言葉）
RULES = [
    ("勤務日（休みの日に入れない）", ("休みの日に訪問がある",)),
    ("1 人の上限", ("上限超え", "名を超える")),
    ("勤務時刻", ("勤務開始前", "終わりが遅すぎる", "勤務時刻を超える")),
    ("移動が間に合う", ("間に合わない",)),
    ("予定（朝会など・前後 15 分）", ("予定と重なる",)),
    ("同じ建物の 2 名（90 分の枠）", ("同じ建物の 2 名",)),
    ("NG の職員", ("NG の職員",)),
    ("女性限定", ("女性限定に男性",)),
    ("希望の時間帯", ("希望の範囲外",)),
    ("5 分刻み", ("5 分刻み",)),
    ("昼休み（30 分は必ず・45 分をなるべく）", ("昼休み",)),
    ("全部の訪問が入る", ("入らない訪問",)),
]


def fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def hm(t: str) -> int:
    h, m = str(t)[:5].split(":")
    return int(h) * 60 + int(m)


def day_label(d: date) -> str:
    return f"{d.month}/{d.day}（{WD[d.weekday()]}）"


def short_name(name: str | None) -> str:
    return (name or "").replace("　", " ").split(" ")[0]


def disp(name: str | None) -> str:
    """表示名（姓だけだと同じ姓の人を見分けられないので、姓名を半角の空白でつなぐ）。"""
    return " ".join((name or "").replace("　", " ").split())


# ---------------------------------------------------------------------------
# 決まりの検査の一覧
# ---------------------------------------------------------------------------
def build_checks(errors: list[str], warnings: list[str], trainee_hits: list[str], flags: dict) -> list[dict]:
    out = []
    for rule, keys in RULES:
        es = [e for e in errors if any(k in e for k in keys)]
        ws = [w for w in warnings if any(k in w for k in keys)]
        out.append({"rule": rule, "ok": not es, "errors": len(es), "warnings": len(ws), "details": es + ws})
    out.append({"rule": "新人を担当にしない（同行のみ）", "ok": not trainee_hits, "errors": len(trainee_hits),
                "warnings": 0, "details": list(trainee_hits)})
    known = [k for _r, keys in RULES for k in keys]
    other = [e for e in errors if not any(k in e for k in known)]
    if other:
        out.append({"rule": "その他", "ok": False, "errors": len(other), "warnings": 0, "details": other})
    cross = sum(1 for fl in flags.values() for k, _ in fl if k == "cross")
    same = sum(1 for fl in flags.values() for k, _ in fl if k == "same_as_last")
    over = sum(1 for fl in flags.values() for k, _ in fl if k == "overtime")
    # 重みで避ける決まり（守れなくても違反ではない。数を見せる）
    out.append({"rule": "拠点またぎ（なるべく避ける）", "ok": True, "errors": 0, "warnings": cross,
                "details": [f"{cross} 件"] if cross else []})
    out.append({"rule": "ローテーション（前回と同じ職員を避ける）", "ok": True, "errors": 0, "warnings": same,
                "details": [f"前回と同じ {same} 件"] if same else []})
    out.append({"rule": "マネージャーの勤務時刻超え（受け皿）", "ok": True, "errors": 0, "warnings": over,
                "details": [f"{over} 件"] if over else []})
    return out


UNPLACED_BLOCKER_PREFIX = "入らない訪問が"  # rk/resolve.py と同じ言葉（この言葉で始まる blocker を resolve が置き換える）
CHECK_DETAIL_MAX = 10  # checks の 1 行に並べる詳しい内容の数（残りは「ほか N 件」）


def check_line(c: dict) -> str:
    """rk の案の checks（文字列）: ○ 守れている／× 問題あり／△ 気を付ける点あり。"""
    if not c["ok"]:
        head = f"× {c['rule']}（問題 {c['errors']} 件）"
    elif c["warnings"]:
        head = f"△ {c['rule']}（気を付ける点 {c['warnings']} 件）"
    else:
        return f"○ {c['rule']}"
    det = c["details"]
    if not det:
        return head
    more = f"／ほか {len(det) - CHECK_DETAIL_MAX} 件" if len(det) > CHECK_DETAIL_MAX else ""
    return head + ": " + "／".join(det[:CHECK_DETAIL_MAX]) + more


# ---------------------------------------------------------------------------
# 入らない訪問の理由
# ---------------------------------------------------------------------------
def unplaced_reason(v: dict, staff: list[dict], counts: Counter, cap: int, allow_over: int) -> tuple[str, str]:
    day = date.fromisoformat(v["day"])
    wd = str(day.weekday())
    lo, hi = v["win"]
    why = Counter()
    for s in staff:
        if int(wd) in s["off"] or wd not in s["shift"]:
            why["休み"] += 1
            continue
        if s["id"] in v["ng"]:
            why["NG"] += 1
            continue
        if v["female_only"] and s["sex"] == "male":
            why["女性限定"] += 1
            continue
        a, b = s["shift"][wd]
        end_limit = max(b, 18 * 60) if s["manager"] else b
        if hi < a or lo + v["dur"] > end_limit:
            why["勤務時刻の外"] += 1
            continue
        if not s["manager"] and counts[s["id"]] >= cap + allow_over:
            why["上限"] += 1
            continue
        why["時刻が空かない（他の訪問・予定・移動・昼休み）"] += 1
    want = fmt(lo) if lo == hi else f"{fmt(lo)}〜{fmt(hi)}"
    reason = f"開始 {want} に入れる人がいない（" + "・".join(f"{k} {n}" for k, n in why.most_common()) + "）"
    if why.get("上限") or why.get("時刻が空かない（他の訪問・予定・移動・昼休み）"):
        hint = "相談先: ご本人に時刻・日を動かせるか／マネージャーの延長・少しオーバー（--allow-over 1）で試算"
    elif why.get("勤務時刻の外"):
        hint = "相談先: ご本人に時刻を動かせるか／勤務時刻の延長"
    else:
        hint = "相談先: 休みの人の調整か応援"
    return reason, hint


# ---------------------------------------------------------------------------
# 職員 → コース（日ごと）
# ---------------------------------------------------------------------------
def choose_courses(people, keep, candidates, cur_courses, staff, course_office, office_days, wd):
    """people を candidates のコースへ 1 対 1 で。点 = (今のコースに残る訪問, -新しく作るコース, マネージャーは M)。
    正規の職員は、自分の拠点が開いている曜日は自分の拠点のコースだけ。"""
    if len(people) > len(candidates):
        return None, f"コースが足りません（職員 {len(people)} 人・使えるコース {len(candidates)}）"

    def allowed(sid, c):
        s = staff[sid]
        if s["manager"] or len(s["offices"]) != 1:
            return True
        (o,) = s["offices"]
        return wd not in office_days.get(o, set(range(7))) or course_office.get(c) == o

    def pair_score(sid, c):
        is_m = c.endswith("M")
        return (keep[(sid, c)], -(c not in cur_courses), int(staff[sid]["manager"] == is_m))

    n, k = len(candidates), len(people)
    best = None
    if math.perm(n, k) <= MAX_PERMUTATIONS:
        for perm in itertools.permutations(candidates, k):
            if not all(allowed(p, c) for p, c in zip(people, perm)):
                continue
            sc = [pair_score(p, c) for p, c in zip(people, perm)]
            score = tuple(sum(x[i] for x in sc) for i in range(3))
            if best is None or score > best[0]:
                best = (score, dict(zip(people, perm)))
    else:  # 大きいときは欲張り法（今の訪問が多い組から）
        left, chosen = set(candidates), {}
        for p in sorted(people, key=lambda p: -max(keep[(p, c)] for c in candidates)):
            opts = [c for c in left if allowed(p, c)]
            if not opts:
                break
            c = max(opts, key=lambda c: pair_score(p, c))
            chosen[p] = c
            left.discard(c)
        if len(chosen) == k:
            best = ((0, 0, 0), chosen)
    if best is None:
        return None, "決まり（正規の職員は自分の拠点のコース）を満たす組み合わせがありません"
    return best[1], None


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def build_plan(result: dict, week: dict, extra: dict | None = None, *, config: dict | None = None,
               kind: str = "week-leveling", title: str | None = None, files: dict | None = None,
               created_at: str | None = None) -> dict:
    config = config or {}
    cap = int(config.get("cap_regular", 6))
    allow_over = int((result.get("options") or {}).get("allow_over") or 0)
    monday = date.fromisoformat(result["week_start"])
    iso_year, iso_week, _ = monday.isocalendar()
    created_at = created_at or datetime.now().isoformat(timespec="seconds")
    group_main = str(uuid.uuid5(uuid.NAMESPACE_URL, f"rakusuke-agent-leveling-{iso_year}-W{iso_week}-{created_at}"))
    group_pool = str(uuid.uuid5(uuid.NAMESPACE_URL, f"rakusuke-agent-leveling-pool-{iso_year}-W{iso_week}-{created_at}"))

    vis = {v["id"]: v for v in result["visits"]}
    staff = {s["id"]: s for s in result["staff"]}
    week_staff = {s["id"]: s for s in week.get("staff") or []}
    raw = {v["id"]: v for v in week.get("visits") or []}
    flags = result.get("flags") or {}
    offices = week.get("offices") or []
    office_days = {o["short_label"]: set(o.get("operating_weekdays") or range(7)) for o in offices}
    office_by_id = {o["id"]: o["short_label"] for o in offices}
    marks = {m["id"]: m for m in week.get("special_marks") or []}
    pats = {p["id"]: p for p in week.get("patients") or []}

    warnings: list[str] = []
    blockers: list[str] = []
    extra = extra or {}
    templates = {}  # "稲A" -> template
    for t in extra.get("templates") or []:
        if COURSE_CODE_RE.match(t["label"] or ""):
            templates[(t["office"] or "") + t["label"]] = t
    if not templates:
        blockers.append("コースのテンプレートが読めていません（agent_extra.json が無い）。コースの付け替えの操作を作れません")
    courses_by_day = defaultdict(dict)  # 曜日 -> "稲A" -> course
    for c in extra.get("courses") or []:
        courses_by_day[int(c["weekday"])][(c["office"] or "") + c["code"]] = c
    course_office = {k: t["office"] for k, t in templates.items()}

    # --- 計算の結果を 1 件ずつに ---
    new_assign: dict[str, tuple[str, int]] = {}
    dropped_ids: list[str] = []
    for _day, dd in sorted(result["days"].items()):
        for sid, seq in dd["routes"].items():
            for item in seq:
                for vid, off in zip(item["visit_ids"], item["offsets"]):
                    new_assign[vid] = (sid, item["start"] + off)
        for ids in dd["dropped"]:
            dropped_ids.extend(ids)

    items = []  # build_ops の plan と同じ: kind = pool / visit / add
    unplaced: list[dict] = []  # 入らない訪問（rk plan resolve で 1 件ずつ決める）
    counts_by_day = defaultdict(Counter)
    for vid, (sid, _t) in new_assign.items():
        counts_by_day[vis[vid]["day"]][sid] += 1
    for vid in dropped_ids:
        v = vis[vid]
        if v["new"]:
            warnings.append(f"{day_label(date.fromisoformat(v['day']))} {v['patient']} 特別訪問週間の○が入りません（○はプールに残ります）")
            continue
        r = raw.get(vid, {})
        if r.get("status", "planned") != "planned":
            warnings.append(f"{day_label(date.fromisoformat(v['day']))} {v['patient']} {fmt(v['fixed_start'])} は入りませんが、"
                            f"予定でない（{r.get('status')}）のでプールへ戻しません")
            continue
        reason, hint = unplaced_reason(v, list(staff.values()), counts_by_day[v["day"]], cap, allow_over)
        warnings.append(f"入らない: {day_label(date.fromisoformat(v['day']))} {v['patient']} {fmt(v['fixed_start'])}"
                        f"（{v['dur']} 分）— {reason}。{hint}")
        items.append({"kind": "pool", "visit": v})
        cur = staff.get(v.get("cur_staff")) or {}
        unplaced.append({"visit_id": v["id"], "patient_id": v["patient_id"], "date": v["day"],
                         "weekday": date.fromisoformat(v["day"]).weekday(), "start_time": fmt(v["fixed_start"]),
                         "duration_min": v["dur"], "course": v["cur_course"] or None,
                         "staff_id": v.get("cur_staff"), "staff_code": cur.get("code"), "staff_name": disp(cur.get("name")),
                         "label": f"{day_label(date.fromisoformat(v['day']))} {v['patient']} {fmt(v['fixed_start'])}",
                         "reason": reason, "hint": hint})
    for vid, (sid, t) in new_assign.items():
        v = vis[vid]
        if v["new"]:
            items.append({"kind": "add", "visit": v, "staff": sid, "start": t, "mark_id": vid[len("add-"):]})
            continue
        r = raw.get(vid, {})
        if r.get("status", "planned") != "planned":
            if sid != v["cur_staff"] or t != v["fixed_start"]:
                warnings.append(f"{day_label(date.fromisoformat(v['day']))} {v['patient']} {fmt(v['fixed_start'])} は"
                                f"予定でない（{r.get('status')}）ので動かせません。案では {disp(staff[sid]['name'])} "
                                f"{fmt(t)} — 手で確かめてください")
            items.append({"kind": "fixed", "visit": v, "staff": sid, "start": t})
            continue
        items.append({"kind": "visit", "visit": v, "staff": sid, "start": t})

    # --- 日ごとに 職員 → コース ---
    cmap: dict[str, dict[str, str]] = {}
    for d in sorted({x["visit"]["day"] for x in items if x["kind"] != "pool"}):
        wd = date.fromisoformat(d).weekday()
        xs = [x for x in items if x["visit"]["day"] == d and x["kind"] != "pool"]
        people = sorted({x["staff"] for x in xs})
        keep = Counter((x["staff"], x["visit"]["cur_course"]) for x in xs)
        cur_courses = set(courses_by_day[wd]) | {x["visit"]["cur_course"] for x in xs if x["visit"]["cur_course"]}
        candidates = sorted(k for k, t in templates.items() if wd in office_days.get(t["office"], set(range(7))))
        if not candidates:
            continue
        m, err = choose_courses(people, keep, candidates, cur_courses, staff, course_office, office_days, wd)
        if err:
            blockers.append(f"{day_label(date.fromisoformat(d))}: {err}")
            continue
        cmap[d] = m

    # --- 操作 ---
    ops: list[dict] = []
    expect: list[dict] = []
    overrides = {o["id"]: o for o in extra.get("manual_overrides") or []}
    for x in items:
        vid = x["visit"]["id"]
        if x["kind"] in ("visit",) and vid in overrides and overrides[vid].get("primary_staff_id") != x["staff"]:
            v = x["visit"]
            ops.append({"op": "assign-staff-week", "step": 0, "visit_id": vid, "staff_id": None,
                        "patient_id": v["patient_id"], "date": v["day"], "op_group_id": group_main,
                        "label": f"{day_label(date.fromisoformat(v['day']))} {v['patient']} {fmt(v['fixed_start'])} "
                                 f"手の上書きを外す（コースの担当に合わせるため）"})
    cancelled = list(result.get("cancelled_visits") or [])
    for vid in cancelled:  # 急遽キャンセル (--cancel): 案から外した訪問を「今週だけ取消」にする (プールへは戻さない)
        r = raw.get(vid)
        if r is None:
            continue
        p = pats.get(r["patient_id"]) or {}
        label = f"{day_label(date.fromisoformat(r['visit_date']))} {p.get('name') or ''} {r['start_time'][:5]}"
        if r.get("status", "planned") != "planned":
            warnings.append(f"急遽キャンセル: {label} は予定でない（{r.get('status')}）ので取消の操作は出しません")
            continue
        ops.append({"op": "cancel-visit-week", "step": 2, "visit_id": vid, "cancel": True,
                    "reason": "急遽キャンセル", "patient_id": r["patient_id"], "date": r["visit_date"],
                    "start_time": r["start_time"][:5], "op_group_id": group_main,
                    "label": f"{label} → 今週だけ取消（急遽キャンセル）"})
    for x in items:
        if x["kind"] != "pool":
            continue
        v = x["visit"]
        r = raw.get(v["id"], {})
        ops.append({"op": "return-to-pool", "step": 1, "visit_id": v["id"], "patient_id": v["patient_id"],
                    "date": v["day"], "start_time": fmt(v["fixed_start"]), "course": v["cur_course"] or None,
                    "cascade_partner": False, "op_group_id": group_pool,
                    "label": f"{day_label(date.fromisoformat(v['day']))} {v['patient']} {fmt(v['fixed_start'])} → 保留プール",
                    "required_staff_count": r.get("required_staff_count")})
    for x in sorted((x for x in items if x["kind"] in ("visit", "add")), key=lambda x: (x["visit"]["day"], x["start"])):
        v = x["visit"]
        d = v["day"]
        dt = date.fromisoformat(d)
        wd = dt.weekday()
        tgt = (cmap.get(d) or {}).get(x["staff"])
        sname = disp(staff[x["staff"]]["name"])
        expect.append({"visit_id": v["id"] if x["kind"] == "visit" else None, "patient_id": v["patient_id"],
                       "date": d, "start": fmt(x["start"]), "staff_id": x["staff"], "course": tgt})
        if tgt is None:
            continue  # コースが決まらない日（blockers に出ている）
        if x["kind"] == "add":
            p = pats.get(v["patient_id"]) or {}
            p_off = office_by_id.get(p.get("primary_office_id"), v["office"])
            place = tgt if course_office.get(tgt) == p_off else p_off + "M"
            if place not in templates:
                blockers.append(f"{day_label(dt)} {v['patient']}: 置く先のコース {place} のテンプレートがありません")
                continue
            ops.append({"op": "special-visit-place", "step": 3, "mark_id": x["mark_id"], "patient_id": v["patient_id"],
                        "date": d, "weekday": wd, "course_template_id": templates[place]["id"], "course": place,
                        "start_time": fmt(x["start"]), "duration_min": (marks.get(x["mark_id"]) or {}).get("duration_min", v["dur"]),
                        "staff_id": x["staff"],
                        "label": f"{day_label(dt)} {v['patient']} {place} {fmt(x['start'])}（特別訪問週間の○を置く・{sname}）"})
            if place != tgt:
                ops.append(_move(iso_year, iso_week, None, v, wd, x["start"], x["start"], templates[tgt]["id"], place, tgt,
                                 group_main, f"{day_label(dt)} {v['patient']} {place} → {tgt}（置いた後の移動・{sname}）"))
            continue
        cur_c, cur_t = v["cur_course"] or "", v["fixed_start"]
        if tgt == cur_c and x["start"] == cur_t:
            continue
        ops.append(_move(iso_year, iso_week, v["id"], v, wd, cur_t, x["start"],
                         templates[tgt]["id"] if tgt != cur_c else None, cur_c or None, tgt, group_main,
                         f"{day_label(dt)} {v['patient']} {cur_c or '（コースなし）'} {fmt(cur_t)} → {tgt} {fmt(x['start'])}（{sname}）"))

    # 最後の盤面でコースに残る訪問（プールへ戻すもの以外）
    final_course = defaultdict(int)
    for x in items:
        if x["kind"] == "pool":
            continue
        tgt = (cmap.get(x["visit"]["day"]) or {}).get(x["staff"]) if x["kind"] != "fixed" else x["visit"]["cur_course"]
        final_course[(x["visit"]["day"], tgt)] += 1
    planned_days = {x["visit"]["day"] for x in items}
    for vid, r in raw.items():  # 案に入っていない訪問（--drop・計算しない日）は今のコースに残る
        if vid not in vis and vid not in cancelled and r["visit_date"] in planned_days:
            final_course[(r["visit_date"], (r.get("course_office") or "") + (r.get("course_code") or ""))] += 1

    for d, m in sorted(cmap.items()):
        dt = date.fromisoformat(d)
        wd = dt.weekday()
        for sid, c in sorted(m.items(), key=lambda kv: kv[1]):
            course = courses_by_day[wd].get(c)
            ops.append({"op": "patch-course-staff", "step": 4, "iso_year": iso_year, "iso_week": iso_week,
                        "weekday": wd, "date": d, "office": course_office.get(c), "course_code": templates[c]["label"],
                        "course": c, "course_id": course["id"] if course else None,
                        "course_template_id": templates[c]["id"], "staff_id": sid,
                        "staff_code": staff[sid]["code"], "op_group_id": group_main,
                        "label": f"{day_label(dt)} {c} ← {disp(staff[sid]['name'])}"})
        used = set(m.values())
        for c, course in sorted(courses_by_day[wd].items()):
            if c in used or not course.get("assigned_staff_id"):
                continue
            if final_course.get((d, c)):
                warnings.append(f"{day_label(dt)} {c}: 案に担当がいないのに訪問が残ります（今の担当のまま）")
                continue
            ops.append({"op": "patch-course-staff", "step": 4, "iso_year": iso_year, "iso_week": iso_week,
                        "weekday": wd, "date": d, "office": course["office"], "course_code": course["code"],
                        "course": c, "course_id": course["id"], "course_template_id": course.get("template_id"),
                        "staff_id": None, "staff_code": None, "op_group_id": group_main,
                        "label": f"{day_label(dt)} {c} の担当を外す（訪問が無くなるため）"})

    warnings.extend(_simulate_moves(ops, raw, vis))

    # --- 決まりの検査・新人 ---
    trainee_hits = []
    for vid, (sid, t) in new_assign.items():
        if (week_staff.get(sid) or {}).get("is_trainee"):
            trainee_hits.append(f"{vis[vid]['day']} {disp(staff[sid]['name'])}: 新人が担当になっている（--off で外す）")
    errors = list(result.get("errors") or [])
    check_details = build_checks(errors, list(result.get("warnings") or []), trainee_hits, flags)
    for w in result.get("warnings") or []:
        warnings.append("気を付ける点: " + w)
    conditions = list(result.get("conditions") or [])
    for c in conditions:  # 決まりをゆるめる条件は「気を付ける点」にも出す (全部は A4 の「今回だけの条件」)
        if c.get("relax"):
            warnings.append("今回だけの条件（決まりをゆるめる）: " + c["text"])
    lunch30 = sorted(d for d, dd in result["days"].items() if (dd.get("lunch_min") or 45) < 45)
    if lunch30:
        warnings.append("昼休みを 30 分に下げて計算した日: " + "・".join(day_label(date.fromisoformat(d)) for d in lunch30))
    # 入らない訪問は「決まりの検査の問題」と分けて、rk plan resolve で決めれば外れる blocker にする
    other_errors = [e for e in errors if not (unplaced and "入らない訪問" in e)]
    if other_errors:
        blockers.append(f"決まりの検査で問題が {len(other_errors)} 件あります（案を反映しない）")
    if unplaced:
        blockers.append(f"{UNPLACED_BLOCKER_PREFIX} {len(unplaced)} 件あります（rk plan resolve で 1 件ずつ決めると反映できます）")
    if trainee_hits:
        blockers.append("新人が担当になっています（アプリが 422 で断ります）")

    summary = _summary(result, items, new_assign, ops, errors, flags, staff)
    timelines = build_timeline(result, staff, vis, cmap)
    target = {"iso_year": iso_year, "iso_week": iso_week, "week_start": str(monday)}
    solved_days = sorted(result["days"])
    if len(solved_days) == 1:
        target["date"] = solved_days[0]
    plan = {
        "kind": kind,
        "title": title or f"週{iso_week}（{monday.month}/{monday.day}〜{(monday + timedelta(days=6)).month}/"
                          f"{(monday + timedelta(days=6)).day}）ならし案",
        "created_at": created_at,
        "target": target,
        "source": {"tool": "docs/tools/week-leveling", "options": result.get("options"),
                   "extracted_at": week.get("extracted_at"), "basis_hash": basis_hash(week)},
        "summary": summary,
        "ops": ops,
        "checks": [check_line(c) for c in check_details],
        "check_details": check_details,
        "warnings": warnings,
        "conditions": conditions,
        "blockers": blockers,
        "ready": not blockers,
        "unplaced": unplaced,
        "resolutions": [],
        "expect": expect,
        "course_map": cmap,
        "timeline": pick_timeline(timelines),
        "timelines": timelines,
        "files": files or {},
        "undo_note": "移動・担当・コースの担当は API で戻せる見込み（rk undo）。"
                     + (f"保留プールへ戻す {n_pool} 件は API で戻せない（rk undo は --partial で残りだけ戻す）。"
                        if (n_pool := sum(1 for o in ops if o["op"] == "return-to-pool")) else "")
                     + "特別訪問の○の配置と、まとめて戻す必要があるときは反映前の rk backup から戻す。",
    }
    return plan


def _move(iso_year, iso_week, visit_id, v, wd, old_start, new_start, new_tpl, from_course, to_course, group, label):
    return {"op": "move-visit-week", "step": 3, "visit_id": visit_id, "patient_id": v["patient_id"], "date": v["day"],
            "iso_year": iso_year, "iso_week": iso_week, "old_weekday": wd, "old_start_time": fmt(old_start) + ":00",
            "new_weekday": wd, "new_start_time": fmt(new_start) + ":00", "new_course_template_id": new_tpl,
            "from_course": from_course, "to_course": to_course, "op_group_id": group, "label": label}


def _simulate_moves(ops, raw, vis) -> list[str]:
    """流す順に追って、移動の元（利用者・曜日・開始）が 1 件に決まるかを確かめる（build_ops の README 手順 2）。"""
    state = Counter()
    for r in raw.values():
        if r.get("status", "planned") == "planned":
            state[(r["patient_id"], r["visit_date"], str(r["start_time"])[:5])] += 1
    out = []
    for o in ops:
        if o["op"] == "return-to-pool":
            state[(o["patient_id"], o["date"], o["start_time"])] -= 1
        elif o["op"] == "special-visit-place":
            state[(o["patient_id"], o["date"], o["start_time"])] += 1
        elif o["op"] == "move-visit-week":
            old = (o["patient_id"], o["date"], o["old_start_time"][:5])
            new = (o["patient_id"], o["date"], o["new_start_time"][:5])
            if state[old] != 1:
                out.append(f"移動の元が {state[old]} 件あります（1 件に決まらない）: {o['label']}")
            state[old] -= 1
            state[new] += 1
    return out


def basis_hash(week: dict) -> str:
    """案を作った時点の週の形（反映の前に週が変わっていないかを比べる）。"""
    rows = sorted(
        f"{v['id']}|{v['visit_date']}|{str(v['start_time'])[:5]}|{v.get('status')}|{v.get('course_office')}{v.get('course_code')}"
        f"|{v.get('primary_staff_id')}"
        for v in week.get("visits") or []
    )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:16]


def _summary(result, items, new_assign, ops, errors, flags, staff) -> list[str]:
    total = len(result["visits"])
    placed = len(new_assign)
    pool = sum(1 for x in items if x["kind"] == "pool")
    staff_changed = sum(1 for x in items if x["kind"] == "visit" and x["staff"] != x["visit"]["cur_staff"])
    moved = [m for fl in flags.values() for k, m in fl if k == "move"]
    added = sum(1 for x in items if x["kind"] == "add")
    lines = [
        f"訪問 {total} 件のうち 入る {placed} 件・入らない {total - placed} 件（保留プールへ {pool} 件）",
        f"担当が変わる {staff_changed} 件・時刻が変わる {len(moved)} 件"
        + (f"（いちばん大きいずれ {max(map(abs, moved))} 分）" if moved else ""),
    ]
    if added:
        lines.append(f"特別訪問週間の○を {added} 件置く")
    app = (result.get("summary") or {}).get("app_check")
    if app:
        lines.append(f"移動（アプリの物差し）{app['cur_travel_minutes']} 分 → {app['new_travel_minutes']} 分・"
                     f"距離 {app['cur_travel_km']} km → {app['new_travel_km']} km")
    st = result.get("summary") or {}
    for key, word in (("cur_overlaps", "今の重なり"), ("new_overlaps", "案の重なり")):
        if key in st:
            lines.append(f"{word} {st[key]} 件")
    c = Counter(o["op"] for o in ops)
    lines.append("操作 {} 件（{}）".format(len(ops), "・".join(f"{k} {n}" for k, n in sorted(c.items()))))
    lines.append(f"決まりの検査: 問題 {len(errors)} 件・気を付ける点 {len(result.get('warnings') or [])} 件")
    if result.get("conditions"):
        lines.append(f"今回だけの条件 {len(result['conditions'])} 件（A4 の「今回だけの条件」・warnings を見る）")
    off = (result.get("options") or {}).get("off") or []
    if off:
        by_code = {s["code"]: short_name(s["name"]) for s in staff.values()}
        lines.append("休みとして計算: " + "・".join(f"{by_code.get(c, c)}（{d or '週全部'}）" for c, d in off))
    return lines


def build_timeline(result, staff, vis, cmap) -> list[dict]:
    """A3 の時間割: 日ごとに 職員の行 → ブロック（開始・終了・名前・変わったか）。"""
    out = []
    for d, dd in sorted(result["days"].items()):
        wd = str(date.fromisoformat(d).weekday())
        rows = []
        for sid in dd["vehicles"]:
            s = staff.get(sid)
            if s is None:
                continue
            blocks = []
            for item in dd["routes"].get(sid) or []:
                for vid, off in zip(item["visit_ids"], item["offsets"]):
                    v = vis[vid]
                    st = item["start"] + off
                    changed = bool(v["new"] or v["cur_staff"] != sid or st != v["fixed_start"])
                    blocks.append({"start": fmt(st), "end": fmt(st + v["dur"]), "label": v["patient"],
                                   "changed": changed, "kind": "add" if v["new"] else "visit", "visit_id": vid,
                                   "from": None if not changed else _from_text(v, staff)})
            for a, b in s.get("events", {}).get(wd, []):
                blocks.append({"start": fmt(a), "end": fmt(min(b, 24 * 60 - 1)), "label": "予定", "changed": False,
                               "kind": "event"})
            blocks.sort(key=lambda b: b["start"])
            shift = s["shift"].get(wd)
            rows.append({"name": disp(s["name"]), "staff_code": s["code"],
                         "course": (cmap.get(d) or {}).get(sid), "manager": s["manager"],
                         "hours": [fmt(shift[0]), fmt(shift[1])] if shift and int(wd) not in s["off"] else None,
                         "blocks": blocks})
        dropped = [vid for ids in dd["dropped"] for vid in ids]
        if dropped:
            rows.append({"name": "入らない", "staff_code": None, "course": None, "manager": False, "hours": None,
                         "blocks": [{"start": fmt(vis[v]["fixed_start"]), "end": fmt(vis[v]["fixed_start"] + vis[v]["dur"]),
                                     "label": vis[v]["patient"], "changed": True, "kind": "unplaced", "visit_id": v}
                                    for v in dropped]})
        out.append({"date": d, "lunch_min": dd.get("lunch_min"), "rows": rows})
    return out


def pick_timeline(timelines: list[dict]) -> dict | None:
    """rk plan report --a3 は 1 日分（dict）を描く。週の案では変わるところがいちばん多い日を選ぶ（全日は timelines）。"""
    if not timelines:
        return None
    return max(timelines, key=lambda t: sum(1 for r in t["rows"] for b in r["blocks"] if b.get("changed")))


def _from_text(v, staff) -> str:
    if v["new"]:
        return "追加（特別訪問週間）"
    who = disp((staff.get(v["cur_staff"]) or {}).get("name")) if v["cur_staff"] else "担当なし"
    return f"{who} {fmt(v['fixed_start'])}"
