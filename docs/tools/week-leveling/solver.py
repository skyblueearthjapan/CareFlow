"""週のならし — 計算の本体 (OR-Tools)。DB には書かない。

決まりの正典 = docs/plans/week-leveling-rules-2026-10-02.md
  * アプリの既定: 直線 20km/h・別住所へ 8 分・昼休み 11:30〜13:30 (45 分を目指し、入らない日だけ 30 分)・5 分刻み・
    始点 (事業所/自宅) からの移動は数えない・NG と女性限定・男性限定は絶対・ローテーション。
  * NG 曜日 (weekly_pattern.ng_weekdays・PO 2026-10-30「全経路で守る」): 曜日は変えないので、
    NG 曜日に訪問を足すのは特別訪問週間の○だけ。○の日が NG 曜日なら足さない (skipped に入れる)。
  * PO 2026-10-02: 曜日は変えない／固定の時刻が第 1、だめなら希望の範囲の中／正規は 1 日 cap_regular 名まで／
    勤務時刻を守る (マネージャーはあふれの受け皿で、必要なら勤務時刻を超えて受ける)／同住所 2 名は 1 人で 90 分枠。
  * 誰が正規・マネージャーで、どの拠点を回るかは config (職員コード) で決める。コードに名前を書かない。
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import SimpleNamespace

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

SPEED_KMH = 20.0
BUFFER_MIN = 8
LUNCH_WIN = (11 * 60 + 30, 13 * 60 + 30)
LUNCH_MIN_HARD = 30  # 必ず取る昼休み (PO 2026-10-03)
LUNCH_TARGET = 45  # なるべく取る昼休み。日ごとに 45 分で解き、入らない訪問が出た日だけ 30 分で解き直す
# 予定の時間は昼休みに数えない (PO 2026-10-03: 会議の時間を昼休みにしない)。
# OR-Tools は休憩どうしの重なりを防がない (空きに合計で収まるかだけを見る) ので、昼休みの開始の候補から
# 予定そのものと重なる時刻を除く (lunch_starts・検査の report.lunch_free と同じ予定の時間)。
# 合計で収まるかは前後 15 分込みの予定で数えるので、予定の近くでは控えめになる。
# その人が予定のために 45 分を置けなければ 30 分、30 分も置けない (予定で昼の窓が埋まる) なら昼休みを置かない
# (検査も同じ人・日を「気を付ける点」に出す)。
BUSINESS_END = 18 * 60
# 休みの種類と境目 (休み設計 2026-10-20 §2-1)。値の正典は backend/app/core/leave.py
# (FULL_DAY_OFF・AM_OFF_UNTIL・PM_OFF_FROM)。この道具は backend の無い箱 (agent-sandbox) や uv の別環境で動くので
# import せず、同じ値をここ 1 か所に写す (report.py もここを読む)。値が同じことは agent/tests/test_leave_boundaries.py が確かめる。
FULL_DAY_OFF = frozenset({"off", "paid_off"})  # 終日の休み (有給も終日休み)
AM_OFF_UNTIL = 13 * 60  # 午前休の人はこの時刻から勤務
PM_OFF_FROM = 12 * 60 + 30  # 午後休の人はこの時刻から休み
EVENT_BUFFER_MIN = 15  # 予定の前後 (アプリの自動スタッフ割当 layer3 と同じ。blocking の印は見ない)
PAIR_BLOCK_MIN = 90  # 同住所 2 名 = 90 分の枠
SAME_ADDR_DEG = 0.001  # 約 100m (アプリと同じ)
DRIFT_OK = 0  # 希望の範囲の外へは出さない (PO: 固定の時刻 → 希望の範囲の中だけ)
FIXED_JOIN_MAX_GAP = 30  # 固定の時刻が希望の範囲からこれより離れていたら、間の時刻は選ばない (join_fixed)


# 目的関数の重み (移動 1 分 = 1)
W_MANAGER_VISIT = 240
W_MANAGER_OVERTIME_PER_MIN = 20
W_CROSS_OFFICE = 480
W_MOVE_PER_MIN = 3  # 固定の時刻から 1 分ずらす
W_ROTATION = (200, 100, 50)  # 直前・2 回前・3 回前と同じ職員
W_DROP = 1_000_000
W_OVER_PER_PATIENT = 300  # 上限を超えて持つ 1 名 (allow_over のとき)。over_before_manager なら 120
W_BALANCE_PER_PATIENT = 60  # ならし: 正規の平均 (切り上げ) を超えて持つ 1 名
W_PREFER = 100  # --prefer-staff: 寄せたい人以外へ回す 1 名ごと (寄せたい人の重みとの差)
W_AVOID = 600  # --avoid-staff: 使いたくない人へ回す 1 名ごと
W_KEEP_CURRENT = 300  # --keep-current: 今の担当から変える 1 名ごと

# opts["weights"] で上書きできる重み (名前 → 既定値)。自作の指定 (agent/tools/leveling/api.py) 用
WEIGHTS = {
    "manager_visit": W_MANAGER_VISIT,
    "manager_overtime_per_min": W_MANAGER_OVERTIME_PER_MIN,
    "cross_office": W_CROSS_OFFICE,
    "move_per_min": W_MOVE_PER_MIN,
    "rotation": W_ROTATION,
    "drop": W_DROP,
    "over_per_patient": W_OVER_PER_PATIENT,
    "balance_per_patient": W_BALANCE_PER_PATIENT,
    "prefer": W_PREFER,
    "avoid": W_AVOID,
    "keep_current": W_KEEP_CURRENT,
}


def weights_for(opts: dict) -> dict:
    over = opts.get("weights") or {}
    unknown = sorted(set(over) - set(WEIGHTS))
    if unknown:
        raise ValueError(f"重みの名前が違います: {', '.join(unknown)}（使えるのは {', '.join(WEIGHTS)}）")
    return {**WEIGHTS, **over}


def hm(t: str) -> int:
    h, m = t[:5].split(":")
    return int(h) * 60 + int(m)


def fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


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


@dataclass
class Visit:
    id: str
    patient_id: str
    patient: str
    office: str
    day: date
    fixed_start: int
    dur: int
    win: tuple[int, int]  # 希望の範囲 (開始)
    time_type: str
    loc: tuple[float, float]
    female_only: bool
    ng: set[str]
    cur_staff: str | None
    cur_course: str | None
    new: bool = False  # 特別訪問週間の○から足す訪問 (まだ週に無い。fixed_start は希望の範囲の始め)
    time_fixed: bool = False  # 赤ピン (完全固定) の枠の訪問: 曜日・時刻を動かさない (担当は自由)
    male_only: bool = False  # 性別制限「男性のみ」: 女性の職員に付けない (女性限定と同じく絶対)
    ng_weekdays: frozenset[int] = frozenset()  # NG 曜日 (0=月)。この曜日に訪問を足さない


@dataclass
class Job:
    """ソルバーの 1 ノード。ふつうは訪問 1 件、同住所 2 名は 2 件をまとめて 1 件。"""

    visits: list[Visit]
    offsets: list[int]  # 先頭の開始からのずれ
    start: int  # 固定の時刻 (先頭)
    dur: int
    lo: int
    hi: int  # 開始してよい範囲 (希望の範囲 + 押してよい分)
    pref_lo: int
    pref_hi: int  # 希望の範囲 (越えると重み)

    @property
    def count(self):
        return len(self.visits)


@dataclass
class Staff:
    id: str
    key: str
    name: str
    sex: str | None
    offices: set[str]
    manager: bool
    shift: dict[int, tuple[int, int]] = field(default_factory=dict)
    off: set[int] = field(default_factory=set)
    code: str = ""
    events: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # 曜日 -> [(開始, 終了)] 前後 15 分込み
    raw_events: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # 予定そのものの時間 (昼休みの判定)


WEEKDAY_CODES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def ng_weekdays_of(wp) -> frozenset[int]:
    """weekly_pattern.ng_weekdays (["Mon", ...] / 0〜6) → 曜日の集合。読めない値は無視。

    アプリの ``app.services.scheduling.ng_weekdays.ng_weekdays_of`` と同じ読み方
    (この道具はアプリを import しないので写しを持つ)。"""
    raw = (wp or {}).get("ng_weekdays") if isinstance(wp, dict) else None
    out = set()
    for x in raw if isinstance(raw, list) else []:
        if isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 6:
            out.add(x)
        elif isinstance(x, str) and x in WEEKDAY_CODES:
            out.add(WEEKDAY_CODES.index(x))
    return frozenset(out)


def pref_window(wp: dict, dur: int) -> tuple[str, tuple[int, int] | None]:
    """希望の時間帯 (開始してよい範囲)。固定は None (その訪問の時刻だけ)。"""
    tt = wp.get("time_type") or "終日"
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
    """固定の時刻はいつでも候補。希望の時間帯の外なら、近い (FIXED_JOIN_MAX_GAP 分以内) ときだけ範囲をつなぐ。
    離れているときは間の時刻 (固定でも希望でもない時刻) を選ばせず、固定の時刻のままにする
    (2026-10-04: 固定 11:00・希望 17:00〜17:35 の訪問が 14:00 になった)。"""
    lo, hi = win
    if lo <= fs <= hi:
        return win
    if fs < lo - FIXED_JOIN_MAX_GAP or fs > hi + FIXED_JOIN_MAX_GAP:
        return (fs, fs)
    return (min(lo, fs), max(hi, fs))


def is_time_fixed(v: dict, locked: list[dict]) -> bool:
    """訪問が赤ピン (完全固定) の枠のものか。赤は曜日・時刻だけを縛る (red-pin-simplify-design K-2/K-5)。
    - その患者・曜日の赤の枠と開始時刻が同じ → 固定
    - 今週だけ動かした訪問 (source=manual_week) は、その曜日の枠がすべて赤なら今週の時刻のまま固定 (Q-1)
    固定の時刻は訪問の今の時刻 (= 枠の時刻、または Q-1 で今週の時刻)。希望の時間帯より赤を優先 (Q-4)。"""
    wd = date.fromisoformat(v["visit_date"]).weekday()
    mine = [r for r in locked if r["patient_id"] == v["patient_id"] and r["weekday"] == wd]
    if not mine:
        return False
    if any(hm(r["start_time"]) == hm(v["start_time"]) for r in mine):
        return True
    return v.get("source") == "manual_week" and not any(r.get("has_unlocked") for r in mine)


def leave_hours(kind: str | None, hours: tuple[int, int]) -> tuple[int, int]:
    """半休の日に働ける時間 (分)。午前休 = 13:00 から・午後休 = 12:30 まで。ほかの種類はそのまま。
    終日の休み (FULL_DAY_OFF) は呼び出し側が休みとして扱う。開始 >= 終了なら呼び出し側が休みにする。"""
    a, b = hours
    if kind == "am_off":
        return (max(a, AM_OFF_UNTIL), b)
    if kind == "pm_off":
        return (a, min(b, PM_OFF_FROM))
    return (a, b)


def load(path, hist_path, config: dict, opts: dict | None = None):
    """opts: drop = 案から外す訪問 ID (先頭一致・今週だけ取消にする予定のもの) /
    add_special = 特別訪問週間の未配置の○を、その日に訪問の無い日だけ「足す訪問」として入れる。"""
    opts = opts or {}
    d = json.load(open(path, encoding="utf-8"))
    hist = json.load(open(hist_path, encoding="utf-8"))
    office_label = {o["id"]: o["short_label"] for o in d["offices"]}
    office_loc = {o["short_label"]: (float(o["lat"]), float(o["lng"])) for o in d["offices"]}
    ng = defaultdict(set)
    for r in d["ng"] or []:
        ng[r["patient_id"]].add(r["staff_id"])
    pats = {p["id"]: p for p in d["patients"]}

    staff: dict[str, Staff] = {}
    regular, managers = config["regular"], config["manager"]
    for s in d["staff"]:
        key = s["name"].replace("　", " ").split(" ")[0]
        if s["code"] in regular:
            staff[s["id"]] = Staff(
                s["id"], key, s["name"], s["sex"], {regular[s["code"]]}, False, code=s["code"]
            )
        elif s["code"] in managers:
            staff[s["id"]] = Staff(
                s["id"], key, s["name"], s["sex"], set(managers[s["code"]]), True, code=s["code"]
            )
    for r in d["shifts"] or []:
        st = staff.get(r["staff_id"])
        if st and r["is_on"] and r["start_time"] and r["end_time"]:
            st.shift[r["weekday"]] = (hm(r["start_time"]), hm(r["end_time"]))
    for r in d["weekly_overrides"] or []:
        st = staff.get(r["staff_id"])
        if st is None:
            continue
        wd, kind = r["weekday"], r["override_type"]
        if kind in FULL_DAY_OFF:
            st.off.add(wd)
        elif kind == "custom_time" and r.get("start_time") and r.get("end_time"):
            st.shift[wd] = (hm(r["start_time"]), hm(r["end_time"]))
        elif wd in st.shift:
            st.shift[wd] = leave_hours(kind, st.shift[wd])
        if wd in st.shift and st.shift[wd][0] >= st.shift[wd][1]:
            st.off.add(wd)
    monday = date.fromisoformat(d["week_start"])
    for e in d.get("events") or []:  # 時刻は壁時計 (UTC として保存されたまま読む・抽出で変換済み)
        st = staff.get(e["staff_id"])
        if st is None:
            continue
        for day, a, b in event_days(e):  # 日をまたぐ予定は日ごとに分ける
            if monday <= day <= monday + timedelta(days=6) and b > a:
                st.events.setdefault(day.weekday(), []).append(
                    (max(0, a - EVENT_BUFFER_MIN), min(24 * 60, b + EVENT_BUFFER_MIN))
                )
                st.raw_events.setdefault(day.weekday(), []).append((a, b))

    no_loc = [p for p in d["patients"] if p["lat"] is None or p["lng"] is None]
    if no_loc:
        raise ValueError(
            f"住所の座標が無い利用者が {len(no_loc)} 名います（先に住所を整えてください）"
        )
    no_office = [p for p in d["patients"] if p["primary_office_id"] not in office_label]
    if no_office:
        raise ValueError(f"主担当拠点の無い利用者が {len(no_office)} 名います")
    cancel = tuple(opts.get("cancel") or ())  # 急遽キャンセル = 外す + 今週だけ取消の操作 (plan.py)
    drop = tuple(opts.get("drop") or ()) + cancel
    dropped_ids = [v["id"] for v in d["visits"] if drop and v["id"].startswith(drop)]
    unknown = [x for x in drop if not any(i.startswith(x) for i in dropped_ids)]
    if unknown:
        raise ValueError(f"外す訪問が週に見つかりません: {', '.join(unknown)}")
    d["dropped_visits"] = dropped_ids
    d["cancelled_visits"] = [v["id"] for v in d["visits"] if cancel and v["id"].startswith(cancel)]
    visits: list[Visit] = []
    for v in d["visits"]:
        if v["id"] in dropped_ids:
            continue
        p = pats[v["patient_id"]]
        wp = p["weekly_pattern"] or {}
        fs = hm(v["start_time"])
        dur = hm(v["end_time"]) - fs
        tt, win = pref_window(wp, dur)
        tfix = is_time_fixed(v, d.get("pfv_locked") or [])
        win = (fs, fs) if (win is None or tfix) else join_fixed(win, fs)
        visits.append(
            Visit(
                id=v["id"],
                patient_id=p["id"],
                patient=p["name"],
                office=office_label[p["primary_office_id"]],
                day=date.fromisoformat(v["visit_date"]),
                fixed_start=fs,
                dur=dur,
                win=win,
                time_type=tt,
                loc=(float(p["lat"]), float(p["lng"])),
                female_only=p["sex_restriction"] == "female_only",
                male_only=p["sex_restriction"] == "male_only",
                ng_weekdays=ng_weekdays_of(wp),
                ng=ng[p["id"]],
                # 訪問の主担当が無ければコースの担当 (アプリの表示の正典)。主担当が空で manual_staff_override が
                # 真なら「明示の担当なし」(休みの時間帯から外した訪問・休み設計 §3-2) なのでコースの担当に読み替えない
                cur_staff=v["primary_staff_id"]
                or (None if v.get("manual_staff_override") else v.get("course_staff_id")),
                cur_course=(v["course_office"] or "") + (v["course_code"] or ""),
                time_fixed=tfix,
            )
        )

    # 特別訪問週間の○ → まだ置いていない日だけ「足す訪問」(その日に訪問がある日は足さない)
    added, skipped_marks = [], []
    if opts.get("add_special"):
        has_day = {(v.patient_id, v.day) for v in visits}
        for m in d.get("special_marks") or []:
            p = pats[m["patient_id"]]
            day = monday + timedelta(days=m["weekday"])
            if (p["id"], day) in has_day or day.weekday() in ng_weekdays_of(p["weekly_pattern"]):
                skipped_marks.append(m["id"])  # その日に訪問がある日 / NG 曜日は足さない
                continue
            dur = int(m["duration_min"])
            tt, win = pref_window(p["weekly_pattern"] or {}, dur)
            if win is None:  # 固定でも○には時刻が無いので終日の範囲で探す
                win = (9 * 60, BUSINESS_END - dur)
            visits.append(
                Visit(
                    id=f"add-{m['id']}",
                    patient_id=p["id"],
                    patient=p["name"],
                    office=office_label[p["primary_office_id"]],
                    day=day,
                    fixed_start=win[0],
                    dur=dur,
                    win=win,
                    time_type=tt,
                    loc=(float(p["lat"]), float(p["lng"])),
                    female_only=p["sex_restriction"] == "female_only",
                    male_only=p["sex_restriction"] == "male_only",
                    ng_weekdays=ng_weekdays_of(p["weekly_pattern"]),
                    ng=ng[p["id"]],
                    cur_staff=None,
                    cur_course=None,
                    new=True,
                )
            )
            has_day.add((p["id"], day))
            added.append(m["id"])
    d["special_added"], d["special_skipped"] = added, skipped_marks

    # 担当歴 (患者ごとに新しい順) — ローテーション用
    history = defaultdict(list)
    for h in sorted(hist, key=lambda h: (h["visit_date"], h["start_time"])):
        history[h["patient_id"]].insert(0, h["primary_staff_id"])
    return d, visits, staff, office_loc, history


def event_days(e) -> list[tuple[date, int, int]]:
    """予定を日ごとの (日, 開始分, 終了分) に分ける。初日は開始〜24:00、間の日は終日、最終日は 0:00〜終了。"""
    first = date.fromisoformat(e["date"])
    last = date.fromisoformat(e.get("end_date") or e["date"])
    out, day = [], first
    while day <= last:
        a = hm(e["start"]) if day == first else 0
        b = hm(e["end"]) if day == last else 24 * 60
        out.append((day, a, b))
        day += timedelta(days=1)
    return out


def make_jobs(day_visits: list[Visit]) -> list[Job]:
    """同じ住所・同じ日の 2 名をまとめる (2 名まで・1 人で 90 分の枠)。"""
    groups = defaultdict(list)
    for v in day_visits:
        groups[bucket(v.loc)].append(v)
    jobs = []
    for vs in groups.values():
        vs.sort(key=lambda v: v.fixed_start)
        while vs:
            if (
                len(vs) >= 2
                and vs[0].dur <= PAIR_BLOCK_MIN
                and vs[1].fixed_start - vs[0].fixed_start <= PAIR_BLOCK_MIN - vs[1].dur
            ):
                a, b = vs[0], vs[1]
                off = b.fixed_start - a.fixed_start
                dur = max(PAIR_BLOCK_MIN, a.dur, off + b.dur)
                pref_lo = max(a.win[0], b.win[0] - off)
                pref_hi = min(a.win[1], b.win[1] - off)
                if pref_lo > pref_hi:
                    pref_lo = pref_hi = a.fixed_start
                jobs.append(_job([a, b], [0, off], a.fixed_start, dur, pref_lo, pref_hi))
                vs = vs[2:]
            else:
                v = vs.pop(0)
                jobs.append(_job([v], [0], v.fixed_start, v.dur, v.win[0], v.win[1]))
    return jobs


def _job(visits, offsets, start, dur, pref_lo, pref_hi) -> Job:
    # 開始してよい範囲 = 希望の範囲 (DRIFT_OK = 0: PO 決定で範囲の外へは押さない)
    return Job(visits, offsets, start, dur, pref_lo, pref_hi + DRIFT_OK, pref_lo, pref_hi)


def find_patient(d: dict, token: str) -> str:
    """利用者を 利用者 ID の先頭 か 名前の一部 (空白なし) で 1 名に決める。"""
    sp = " \u3000"

    def squeeze(x: str) -> str:
        return "".join(c for c in (x or "") if c not in sp).lower()

    t = squeeze(token)
    hits = [p["id"] for p in d["patients"] if p["id"].lower().startswith(t) or t in squeeze(p["name"])]
    if not hits:
        raise ValueError(f"利用者が見つかりません: {token}")
    if len(hits) > 1:
        raise ValueError(f"利用者が {len(hits)} 名に当たります（もう少し長く書いてください）: {token}")
    return hits[0]


def resolve_knobs(opts: dict, d: dict, staff: dict[str, Staff]) -> dict:
    """「今回だけの条件」の利用者・職員コードを id に直す (見つからなければ ValueError)。"""
    by_code = {s.code: s for s in staff.values()}

    def sid(code: str) -> str:
        if code not in by_code:
            raise ValueError(f"職員コード {code} は config に無いか、稼働していません")
        return by_code[code].id

    k: dict = {"only": [], "not_same_day": [], "pair": {}, "forbid": {}}
    for x in opts.get("only_staff") or []:
        lo, hi = x["window"]
        k["only"].append(({sid(c) for c in x["codes"]}, int(lo), int(hi)))
    for a, b in opts.get("not_same_day") or []:
        pa, pb = find_patient(d, a), find_patient(d, b)
        if pa == pb:
            raise ValueError(f"--not-same-day に同じ利用者が 2 回: {a}, {b}")
        k["not_same_day"].append((pa, pb))
    for key, src in (("pair", "pair_staff"), ("forbid", "forbid_pair")):
        for p, c in opts.get(src) or []:
            k[key].setdefault(find_patient(d, p), set()).add(sid(c))
    for c in list(opts.get("prefer") or {}) + list(opts.get("avoid") or {}):
        sid(c)
    return k


def knob_ok(st: Staff, job: Job, k: dict) -> bool:
    for v in job.visits:
        if v.patient_id in k["pair"] and st.id not in k["pair"][v.patient_id]:
            return False
        if st.id in k["forbid"].get(v.patient_id, ()):
            return False
    return True


def covered_all_day(st: Staff, wd: int) -> bool:
    """勤務時間が丸ごと予定で埋まっている (研修で終日など) = その日は回れない。"""
    a, b = st.shift[wd]
    end = max(b, BUSINESS_END) if st.manager else b
    return any(ea <= a and eb >= end for ea, eb in st.events.get(wd, []))


def allowed(st: Staff, job: Job) -> bool:
    for v in job.visits:
        if st.id in v.ng:
            return False
        if v.female_only and st.sex == "male":
            return False
        if v.male_only and st.sex == "female":
            return False
    return True


def solve_day(
    day,
    jobs: list[Job],
    staff: list[Staff],
    history,
    seconds: int,
    opts: dict,
    lunch: int = LUNCH_MIN_HARD,
):
    """1 日を解く。lunch = その日の昼休みの長さ (分・11:30〜13:30 の中で必ず取る)。"""
    cap_regular = opts.get("cap_regular", 6)
    allow_over = opts.get("allow_over", 0)
    W = weights_for(opts)
    knobs = opts.get("_knobs") or {"only": [], "not_same_day": [], "pair": {}, "forbid": {}}
    wd = day.weekday()
    vehicles = [
        s for s in staff if wd in s.shift and wd not in s.off and not covered_all_day(s, wd)
    ]
    n, V = len(jobs), len(vehicles)
    if V == 0:  # 出勤者がいない日は全部「入らない」
        return vehicles, {}, list(jobs)
    locs = [j.visits[0].loc for j in jobs]
    starts = [n + i for i in range(V)]
    mgr = pywrapcp.RoutingIndexManager(n + V, V, starts, starts)
    routing = pywrapcp.RoutingModel(mgr)

    def tmin(a, b):  # 始点・終点 (事業所/自宅) との行き来は数えない (アプリと同じ)
        if a >= n or b >= n:
            return 0
        return travel_min(locs[a], locs[b])

    tm = [[tmin(a, b) for b in range(n + V)] for a in range(n + V)]
    service = [j.dur for j in jobs] + [0] * V
    # 休憩の判定に使う「その場所で過ごす時間」(routing の index ごと・終点は 0)
    visit_transits = [service[mgr.IndexToNode(i)] for i in range(routing.Size())] + [0] * V

    def time_cb(i, j):
        a, b = mgr.IndexToNode(i), mgr.IndexToNode(j)
        t = service[a] + tm[a][b]
        if a < n and b < n and tm[a][b] > 0:
            t += BUFFER_MIN
        return t

    T_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(T_idx, 600, 24 * 60, False, "Time")
    T = routing.GetDimensionOrDie("Time")

    for k, j in enumerate(jobs):
        idx = mgr.NodeToIndex(k)
        T.CumulVar(idx).SetRange(j.lo, j.hi)
        # まず固定の時刻 (ずらすと 1 分ごとに重み)。範囲 lo〜hi = 希望の範囲 (固定はその時刻だけ)
        # ○から足す訪問は固定の時刻が無いので、希望の範囲の中ならどこでも同じ
        if not all(v.new for v in j.visits):
            T.SetCumulVarSoftLowerBound(idx, j.start, W["move_per_min"])
            T.SetCumulVarSoftUpperBound(idx, j.start, W["move_per_min"])
        routing.AddDisjunction([idx], W["drop"])
        if j.lo < j.hi:  # 開始は 5 分刻み (アプリと同じ)
            slot = routing.solver().IntVar(j.lo // 5, j.hi // 5 + 1, f"slot{k}")
            routing.solver().Add(T.CumulVar(idx) == slot * 5)
        for i, s in enumerate(vehicles):
            if not allowed(s, j) or not knob_ok(s, j, knobs):
                routing.VehicleVar(idx).RemoveValue(i)

    solver = routing.solver()
    for i, s in enumerate(vehicles):
        st, en = s.shift[wd]
        hard_end = max(en, BUSINESS_END) if s.manager else en
        T.CumulVar(routing.Start(i)).SetRange(st, hard_end)
        T.CumulVar(routing.End(i)).SetRange(st, hard_end)
        if s.manager and en < hard_end:
            T.SetCumulVarSoftUpperBound(routing.End(i), en, W["manager_overtime_per_min"])
        routing.AddVariableMaximizedByFinalizer(T.CumulVar(routing.Start(i)))
        routing.AddVariableMinimizedByFinalizer(T.CumulVar(routing.End(i)))
        breaks = []
        evs = list(s.events.get(wd, []))
        raw = list(s.raw_events.get(wd, []))
        for ok_ids, lo, hi in knobs["only"]:  # その時間帯は指定の人だけ = 他の人にはその時間の予定を置く
            if s.id not in ok_ids:
                evs.append((lo, hi))
                raw.append((lo, hi))
        my_lunch, starts_ok = lunch, []
        if needs_lunch(st, hard_end):
            starts_ok = lunch_starts(lunch, raw)
            if not starts_ok:  # 予定のため 45 分を置けない → この人は 30 分
                my_lunch, starts_ok = LUNCH_MIN_HARD, lunch_starts(LUNCH_MIN_HARD, raw)
        if starts_ok:
            iv = solver.FixedDurationIntervalVar(
                starts_ok[0], starts_ok[-1], my_lunch, False, f"lunch{i}"
            )
            sv = iv.StartExpr().Var()
            ok = set(starts_ok)
            for x in range(starts_ok[0], starts_ok[-1] + 1):
                if x not in ok:
                    sv.RemoveValue(x)
            breaks.append(iv)
        for k, (ea, eb) in enumerate(evs):  # 予定 (前後 15 分込み) には入れない
            breaks.append(solver.FixedDurationIntervalVar(ea, ea, eb - ea, False, f"ev{i}_{k}"))
        if breaks:
            T.SetBreakIntervalsOfVehicle(breaks, i, visit_transits)

    counts = [j.count for j in jobs] + [0] * V
    C_idx = routing.RegisterUnaryTransitCallback(lambda i: counts[mgr.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(
        C_idx, 0, [99 if s.manager else cap_regular + allow_over for s in vehicles], True, "Count"
    )
    C = routing.GetDimensionOrDie("Count")
    regulars = [i for i, s in enumerate(vehicles) if not s.manager]
    if allow_over:  # 少しオーバー: 上限を超えた 1 名ごとに重み (マネージャーより先に使うかは設定)
        w_over = 120 if opts.get("over_before_manager") else W["over_per_patient"]
        for i in regulars:
            C.SetCumulVarSoftUpperBound(routing.End(i), cap_regular, w_over)
    for pa, pb in knobs["not_same_day"]:  # 同じ職員が同じ日に両方を持たない
        ja = [k for k, j in enumerate(jobs) if any(v.patient_id == pa for v in j.visits)]
        jb = [k for k, j in enumerate(jobs) if any(v.patient_id == pb for v in j.visits)]
        for a_ in ja:
            for b_ in jb:
                if a_ == b_:
                    raise ValueError(f"{day}: 同じ建物の 2 名は 1 人が持つので --not-same-day にできません")
                va, vb = routing.VehicleVar(mgr.NodeToIndex(a_)), routing.VehicleVar(mgr.NodeToIndex(b_))
                solver.Add((va != vb).Var() + (va == -1).Var() >= 1)
    if opts.get("balance") and regulars:  # ならし: 正規の平均 (切り上げ) を超える分に重み
        total = sum(j.count for j in jobs)
        fair = -(-total // len(regulars))
        routing.AddDimensionWithVehicleCapacity(C_idx, 0, [99] * V, True, "Balance")
        B = routing.GetDimensionOrDie("Balance")
        for i in regulars:
            B.SetCumulVarSoftUpperBound(
                routing.End(i), min(fair, cap_regular), W["balance_per_patient"]
            )

    def rot_cost(s: Staff, j: Job) -> int:
        c = 0
        for v in j.visits:
            h = history[v.patient_id]
            for rank, w in enumerate(W["rotation"]):
                if rank < len(h) and h[rank] == s.id:
                    c = max(c, w)
                    break
        return c

    prefer = opts.get("prefer") or {}
    avoid = opts.get("avoid") or {}
    keep_w = opts.get("keep_current") or 0
    prefer_top = max(prefer.values(), default=0)
    for i, s in enumerate(vehicles):
        extra = []
        for j in jobs:
            e = rot_cost(s, j)
            if prefer:  # 寄せたい人 (重みが大きいほど有利) 以外へは、差の分だけ高くする
                e += (prefer_top - prefer.get(s.code, 0)) * j.count
            if s.code in avoid:
                e += avoid[s.code] * j.count
            if keep_w:
                e += keep_w * sum(1 for v in j.visits if v.cur_staff and v.cur_staff != s.id)
            if s.manager:
                e += W["manager_visit"] * j.count
            if any(v.office not in s.offices for v in j.visits):
                e += W["cross_office"] * j.count
            extra.append(e)

        def cb(a, b, extra=extra):
            na, nb = mgr.IndexToNode(a), mgr.IndexToNode(b)
            return tm[na][nb] + (extra[nb] if nb < n else 0)

        routing.SetArcCostEvaluatorOfVehicle(routing.RegisterTransitCallback(cb), i)

    # 自作の条件 (agent/tools/leveling/api.py): 日ごとに fn(model=routing, vars=道具の箱, data=その日の情報)
    if opts.get("extra_constraints"):
        by_visit = {v.id: mgr.NodeToIndex(k) for k, j in enumerate(jobs) for v in j.visits}
        box = {
            "routing": routing, "manager": mgr, "solver": solver, "time": T, "count": C,
            "jobs": jobs, "vehicles": vehicles, "day": day,
            "index_of_visit": by_visit,
            "vehicle_of_code": {s.code: i for i, s in enumerate(vehicles)},
            "vehicle_of_staff": {s.id: i for i, s in enumerate(vehicles)},
        }
        data = SimpleNamespace(day=day, jobs=jobs, staff=staff, history=history, opts=opts,
                               visits=[v for j in jobs for v in j.visits])
        for fn in opts["extra_constraints"]:
            fn(routing, box, data)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.seconds = seconds
    sol = routing.SolveWithParameters(params)
    if sol is None:
        raise RuntimeError(f"{day}: 解が見つかりません")

    routes = {}
    for i, s in enumerate(vehicles):
        idx = routing.Start(i)
        seq = []
        while not routing.IsEnd(idx):
            node = mgr.IndexToNode(idx)
            if node < n:
                seq.append((jobs[node], sol.Value(T.CumulVar(idx))))
            idx = sol.Value(routing.NextVar(idx))
        routes[s.id] = seq
    placed = {id(j) for seq in routes.values() for j, _ in seq}
    dropped = [j for j in jobs if id(j) not in placed]
    return vehicles, routes, dropped


def lunch_starts(lunch: int, events) -> list[int]:
    """昼休み (lunch 分) を始めてよい時刻の一覧。11:30〜13:30 に収まり、予定 (events) と重ならない。"""
    return [
        x
        for x in range(LUNCH_WIN[0], LUNCH_WIN[1] - lunch + 1)
        if all(x + lunch <= ea or x >= eb for ea, eb in events)
    ]


def needs_lunch(start: int, end: int) -> bool:
    """昼休みを取る人 = 11:30 より前から 13:30 より後まで働く人 (検査も同じ条件)。"""
    return start <= LUNCH_WIN[0] and end >= LUNCH_WIN[1]


def run(
    week_path, hist_path, config: dict, seconds: int, opts: dict | None = None, days=None, log=print
):
    """opts: cap_regular / allow_over / over_before_manager / balance /
    off = [(職員コード, date|None)] … 「もしこの人が休みなら」の試算 (None = 週全部)。
    days: 計算する日 (None = 週全部)。"""
    opts = {"cap_regular": config.get("cap_regular", 6), **(opts or {})}
    d, visits, staff, _office_loc, history = load(week_path, hist_path, config, opts)
    by_code = {s.code: s for s in staff.values()}
    opts["_knobs"] = resolve_knobs(opts, d, staff)
    for code, day in opts.get("off", []):
        if code not in by_code:
            raise ValueError(f"職員コード {code} は config に無いか、稼働していません")
        by_code[code].off |= {day.weekday()} if day else set(range(7))
    # 計算しない日 (同じ週) の今の担当は、その日の順番でローテーションの担当歴に積む
    skipped = sorted(
        (v for v in visits if days and v.day not in days and v.cur_staff),
        key=lambda v: (v.day, v.fixed_start),
    )
    if days:
        visits = [v for v in visits if v.day in days]
    hist_by_day = {}  # 日 -> その日を計算する直前の担当歴 (「前回と同じ」の判定用)
    lunch_by_day = {}  # 日 -> その日に必ず取った昼休みの長さ (45 か、入らない日だけ 30)
    by_day = defaultdict(list)
    for v in visits:
        by_day[v.day].append(v)
    out = {}
    for day in sorted(by_day):
        while skipped and skipped[0].day < day:
            v = skipped.pop(0)
            history[v.patient_id].insert(0, v.cur_staff)
        jobs = make_jobs(by_day[day])
        hist_by_day[day] = {v.patient_id: list(history[v.patient_id][:3]) for v in by_day[day]}
        lunch = LUNCH_TARGET
        try:
            res = solve_day(day, jobs, list(staff.values()), history, seconds, opts, lunch)
        except RuntimeError:
            res = None  # 45 分では解けない → 30 分で解き直す
        if res is None or res[2]:
            # 45 分では入らない訪問が出る → この日だけ 30 分で解き直し、入る件数が増えたら採る
            try:
                alt = solve_day(
                    day, jobs, list(staff.values()), history, seconds, opts, LUNCH_MIN_HARD
                )
            except RuntimeError:
                if res is None:
                    raise
                alt = None
            if alt is not None and (
                res is None or sum(j.count for j in alt[2]) < sum(j.count for j in res[2])
            ):
                res, lunch = alt, LUNCH_MIN_HARD
        vehicles, routes, dropped = res
        lunch_by_day[day] = lunch
        for sid, seq in routes.items():  # その日の結果を担当歴へ積む (翌日以降のローテーション)
            for j, _ in seq:
                for v in j.visits:
                    history[v.patient_id].insert(0, sid)
        out[day] = (vehicles, routes, dropped)
        cnt = {staff[sid].key: sum(j.count for j, _ in seq) for sid, seq in routes.items() if seq}
        log(
            f"{day} 訪問 {len(by_day[day])} 入らない {sum(j.count for j in dropped)} "
            f"昼休み {lunch} 分 {cnt}"
        )
    return d, visits, staff, out, hist_by_day, lunch_by_day
