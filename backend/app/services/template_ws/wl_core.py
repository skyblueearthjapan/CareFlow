"""週のならし solver.py（docs/tools/week-leveling/solver.py）から、型の作業場の計算に要る部品だけを写したもの。

backend のイメージには docs/ が入らないため、importできない代わりにここへ最小限を写している（重複）。
元の値を変えたら、ここも同じに直すこと（agent/tools/courseplan/solve.py の check_same_rules と同じ考え）。
ortools はここでは読まない（solve.py だけが読む）。
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

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
NOON = 12 * 60  # 午前休 / 午後休の境目 (アプリの allocation / dashboard と同じ 12:00)
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


