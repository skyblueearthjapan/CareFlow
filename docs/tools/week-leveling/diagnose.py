"""ならし案を「アプリの物差し」で診断する (反映する前の確認)。本番には繋がない・書かない。

アプリの「スケジュール診断」の計算 (schedule_health._compute_course_metrics) を backend の環境で
呼び (app_yardstick.py)、今の盤面とならし案を「職員 × 日 = 1 本の順路」として数えて比べる。
アプリの画面はコースごとに数えるが、案は職員ごとに組むので、職員ごとに数える。

診断の中身:
  1. 移動 (分・km)・ゆとり・待ち時間の合計 (今 / 案)
  2. 移動が平均の 1.5 倍を超える順路 (アプリの診断画面の「要対応」と同じ基準)。日ごと・職員の週合計
  3. 物差しの一致: 道具の移動計算とアプリの計算が全順路で同じか
  4. あと 1 手: 案から 1 件だけ担当・時刻を変えるか 2 件を入れ替えて、決まりを守ったまま
     移動＋ゆとりがアプリの改善提案の最小幅 (10 分) 以上減る手が残っていないか。
     残っていれば「見落とし」(道具の点数でも良くなる = 計算の打ち切り) か
     「わざと選ばなかった」(マネージャー・拠点またぎ・前回と同じ・時刻ずらしが増える) かを分けて示す。
     訪問の無い人へ 1 件だけ移す手は数えない (1 件目への移動を数えないので移動 0 に見えるだけ)。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path

from report import check, day_label, new_assignments
from solver import (
    BUFFER_MIN,
    W_BALANCE_PER_PATIENT,
    W_CROSS_OFFICE,
    W_MANAGER_OVERTIME_PER_MIN,
    W_MANAGER_VISIT,
    W_MOVE_PER_MIN,
    W_OVER_PER_PATIENT,
    W_ROTATION,
    fmt,
    km,
    travel_min,
)

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2] / "backend"
HIGH_TRAVEL_RATIO = 1.5  # アプリの診断画面 (ScheduleHealthDialog) と同じ


def backend_python() -> Path | None:
    for p in (BACKEND / ".venv" / "Scripts" / "python.exe", BACKEND / ".venv" / "bin" / "python"):
        if p.exists():
            return p
    return None


def app_metrics(routes: dict[str, list[dict]], settings: dict | None = None) -> dict:
    """routes: key -> [{"patient_id", "start", "end", "lat", "lng"}] (分)。アプリの計算の結果を返す。"""
    py = backend_python()
    if py is None:
        raise RuntimeError("backend の環境がありません（cd backend && uv sync で作れます）")
    res = subprocess.run(
        [str(py), str(HERE / "app_yardstick.py")],
        input=json.dumps({"settings": settings, "routes": routes}).encode("utf-8"),
        capture_output=True,
        cwd=BACKEND,
        env={**os.environ, "PYTHONPATH": str(BACKEND), "PYTHONIOENCODING": "utf-8"},
        timeout=300,
    )
    if res.returncode != 0:
        raise RuntimeError(
            "アプリの診断の呼び出しに失敗: " + res.stderr.decode("utf-8", "replace")[-800:]
        )
    return json.loads(res.stdout.decode("utf-8"))


def _payload(items) -> list[dict]:
    """[(Visit, 開始)] -> アプリへ渡す形。"""
    return [
        {
            "patient_id": v.patient_id,
            "start": t,
            "end": t + v.dur,
            "lat": v.loc[0],
            "lng": v.loc[1],
        }
        for v, t in items
    ]


def _tool_split(items) -> tuple[int, int, float]:
    """道具の式での (移動, ゆとり, km)。アプリと同じ並べ方 (開始の早い順)。"""
    s = sorted(items, key=lambda x: x[1])
    trav, buf, dist = 0, 0, 0.0
    for (a, _), (b, _) in zip(s, s[1:]):  # noqa: B905
        tr = travel_min(a.loc, b.loc)
        if tr:
            trav += tr
            buf += BUFFER_MIN
            dist += km(a.loc, b.loc)
    return trav, buf, dist


def _tool_cost(items) -> tuple[int, float]:
    """道具の式での (移動＋ゆとり, km)。"""
    trav, buf, dist = _tool_split(items)
    return trav + buf, dist


def _routes(visits, assign) -> dict:
    """(日, 職員) -> [(Visit, 開始)]"""
    by_id = {v.id: v for v in visits}
    out = defaultdict(list)
    for vid, (sid, t) in assign.items():
        if sid is not None and vid in by_id:
            out[(by_id[vid].day, sid)].append((by_id[vid], t))
    return out


def _longest_leg(items, names) -> str:
    s = sorted(items, key=lambda x: x[1])
    best = None
    for (a, ta), (b, tb) in zip(s, s[1:]):  # noqa: B905
        tr = travel_min(a.loc, b.loc)
        if best is None or tr > best[0]:
            best = (tr, f"{fmt(ta)} {a.patient} → {fmt(tb)} {b.patient}（{tr} 分）")
    return best[1] if best and best[0] else ""


def _high(rows, key_fn):
    """rows: [(key, travel)]。グループ (key_fn) ごとの平均の 1.5 倍を超えるもの。"""
    groups = defaultdict(list)
    for k, tr in rows:
        groups[key_fn(k)].append(tr)
    avg = {g: sum(x) / len(x) for g, x in groups.items()}
    return [
        (k, tr, round(avg[key_fn(k)], 1))
        for k, tr in rows
        if tr > avg[key_fn(k)] * HIGH_TRAVEL_RATIO
    ], avg


# ---------------------------------------------------------------- あと 1 手


class _Scorer:
    """道具の点数 (solver の目的関数と同じ重み) を順路ごとに数え直す。"""

    def __init__(self, day, vehicles, routes, dropped, hist, opts, cap):
        self.wd = day.weekday()
        self.hist = hist
        self.opts = opts
        self.cap = cap
        regulars = [s for s in vehicles if not s.manager]
        # solver と同じく「入らない」訪問も数える
        total = sum(j.count for seq in routes.values() for j, _ in seq) + sum(j.count for j in dropped)
        self.fair = -(-total // len(regulars)) if regulars else 0

    def rot(self, s, j) -> int:
        c = 0
        for v in j.visits:
            h = self.hist.get(v.patient_id) or []
            for rank, w in enumerate(W_ROTATION):
                if rank < len(h) and h[rank] == s.id:
                    c = max(c, w)
                    break
        return c

    def parts(self, s, seq) -> dict:
        seq = sorted(seq, key=lambda x: x[1])
        p = defaultdict(int)
        for (a, _), (b, _) in zip(seq, seq[1:]):  # noqa: B905
            p["travel"] += travel_min(a.visits[0].loc, b.visits[0].loc)
        cnt = 0
        for j, t in seq:
            cnt += j.count
            p["rotation"] += self.rot(s, j)
            if s.manager:
                p["manager"] += W_MANAGER_VISIT * j.count
            if any(v.office not in s.offices for v in j.visits):
                p["cross"] += W_CROSS_OFFICE * j.count
            p["shift"] += W_MOVE_PER_MIN * abs(t - j.start)
        if seq and s.manager:
            en = s.shift[self.wd][1]
            last = seq[-1][1] + seq[-1][0].dur
            p["overtime"] += W_MANAGER_OVERTIME_PER_MIN * max(0, last - en)
        if not s.manager:
            if self.opts.get("allow_over"):
                w = 120 if self.opts.get("over_before_manager") else W_OVER_PER_PATIENT
                p["over"] += w * max(0, cnt - self.cap)
            if self.opts.get("balance"):
                p["balance"] += W_BALANCE_PER_PATIENT * max(0, cnt - min(self.fair, self.cap))
        return p


_REASON = {
    "rotation": "前回と同じ職員になる",
    "manager": "マネージャーの担当が増える",
    "cross": "拠点またぎが増える",
    "shift": "固定の時刻からのずれが大きくなる",
    "overtime": "マネージャーの勤務超えが増える",
    "over": "上限超えが増える",
    "balance": "件数の偏りが増える",
}


def _job_cost(seq) -> int:
    return _tool_cost([(j.visits[0], t) for j, t in seq])[0] if len(seq) > 1 else 0


def one_more_move(d, out, hist_by_day, cap, allow_over, opts, threshold, names):
    """案から 1 手で、決まりを守ったまま 移動＋ゆとり が threshold 分以上減る手を探す。"""
    found = {}  # 同じ訪問 (または同じ組) の手は一番効くものだけ残す
    for day, (vehicles, routes, dropped) in sorted(out.items()):
        vmap = {s.id: s for s in vehicles}
        sc = _Scorer(day, vehicles, routes, dropped, hist_by_day.get(day, {}), opts, cap)
        base = {sid: _job_cost(seq) for sid, seq in routes.items()}

        def ok(new_routes):
            mini = {day: ([vmap[s] for s in new_routes], new_routes, [])}
            return not check(d, mini, cap, allow_over)[0]

        def judge(key, desc, new_routes):
            delta = sum(_job_cost(seq) - base[sid] for sid, seq in new_routes.items())
            if delta > -threshold or not ok(new_routes):
                return
            before = defaultdict(int)
            after = defaultdict(int)
            for sid, seq in new_routes.items():
                for k, x in sc.parts(vmap[sid], routes[sid]).items():
                    before[k] += x
                for k, x in sc.parts(vmap[sid], seq).items():
                    after[k] += x
            score = sum(after.values()) - sum(before.values())
            worse = [_REASON[k] for k in _REASON if after[k] > before[k]]
            if score >= 0 and not worse:
                # 道具の点数は移動だけを数え、ゆとり (別の住所へ移る 8 分) は数えない
                worse = ["道具の点数では得にならない（減るのは主にゆとり）"]
            kind = "見落とし" if score < 0 else "わざと選ばなかった"
            old = found.get(key)
            if old and (old["kind"] == "見落とし", old["saving_min"]) >= (
                kind == "見落とし",
                -delta,
            ):
                return  # 見落としを先に・同じ種類なら減る分の大きい方を残す
            found[key] = (
                {
                    "day": str(day),
                    "label": day_label(day),
                    "move": desc,
                    "saving_min": -delta,
                    "tool_score_delta": score,
                    "kind": kind,
                    "why_not": "・".join(worse) if score >= 0 else "",
                }
            )

        items = [(sid, j, t) for sid, seq in routes.items() for j, t in seq]
        # 1) 1 件 (同じ建物の 2 名はまとめて) の担当・時刻を変える
        for sid, j, t in items:
            rest = [(x, u) for x, u in routes[sid] if x is not j]
            times = sorted({t} | set(range(j.lo - j.lo % 5 + (5 if j.lo % 5 else 0), j.hi + 1, 5)))
            who = "・".join(v.patient for v in j.visits)
            for b in vehicles:
                if b.id != sid and not routes.get(b.id):
                    continue  # 見かけだけ減る (1 件目への移動は数えない)
                for t2 in times:
                    if b.id == sid and t2 == t:
                        continue
                    if b.id == sid:
                        new_routes = {sid: sorted(rest + [(j, t2)], key=lambda x: x[1])}
                    else:
                        new_routes = {
                            sid: rest,
                            b.id: sorted(routes[b.id] + [(j, t2)], key=lambda x: x[1]),
                        }
                    desc = (
                        f"{who} {fmt(t)} {names.get(sid, '')} → {fmt(t2)} {names.get(b.id, '')}"
                    )
                    judge((day, "mv", id(j)), desc, new_routes)
        # 2) 2 件の担当を入れ替える (時刻はそのまま)
        for i, (sa, ja, ta) in enumerate(items):
            for sb, jb, tb in items[i + 1 :]:
                if sa == sb:
                    continue
                ra = sorted(
                    [(x, u) for x, u in routes[sa] if x is not ja] + [(jb, tb)], key=lambda x: x[1]
                )
                rb = sorted(
                    [(x, u) for x, u in routes[sb] if x is not jb] + [(ja, ta)], key=lambda x: x[1]
                )
                desc = (
                    f"入れ替え: {fmt(ta)} {'・'.join(v.patient for v in ja.visits)}（{names.get(sa, '')}）"
                    f" ⇄ {fmt(tb)} {'・'.join(v.patient for v in jb.visits)}（{names.get(sb, '')}）"
                )
                judge((day, "sw", id(ja), id(jb)), desc, {sa: ra, sb: rb})
    return sorted(found.values(), key=lambda x: (x["kind"] != "見落とし", -x["saving_min"]))


# ---------------------------------------------------------------- 本体


def diagnose(d, visits, out, hist_by_day, cap, allow_over, opts, names, log=print) -> dict:
    """今の盤面とならし案をアプリの物差しで比べる。names: 職員 id -> 表示名。"""
    cur = _routes(visits, {v.id: (v.cur_staff, v.fixed_start) for v in visits})
    new = _routes(visits, new_assignments(out))
    req = {}
    for tag, rs in (("cur", cur), ("new", new)):
        for (day, sid), items in rs.items():
            req[f"{tag}|{day}|{sid}"] = _payload(items)
    res = app_metrics(req, d.get("scheduling_settings"))
    m = res["metrics"]

    # 物差しの一致 (道具の式 = アプリの式)
    mismatch = []
    for tag, rs in (("cur", cur), ("new", new)):
        for (day, sid), items in rs.items():
            a = m[f"{tag}|{day}|{sid}"]
            trav, buf, dist = _tool_split(items)
            if (
                a["travel_minutes"] != trav
                or a["buffer_minutes"] != buf
                or abs(a["travel_km"] - dist) > 1e-6
            ):
                mismatch.append(f"{tag} {day} {names.get(sid, sid)}")

    def totals(tag, rs):
        keys = [f"{tag}|{day}|{sid}" for day, sid in rs]
        return {
            "routes": len(keys),
            "visits": sum(m[k]["visit_count"] for k in keys),
            "travel_minutes": sum(m[k]["travel_minutes"] for k in keys),
            "travel_km": round(sum(m[k]["travel_km"] for k in keys), 1),
            "buffer_minutes": sum(m[k]["buffer_minutes"] for k in keys),
            "gap_minutes": sum(m[k]["gap_minutes"] for k in keys),
        }

    def highs(tag, rs):
        day_rows = [((day, sid), m[f"{tag}|{day}|{sid}"]["travel_minutes"]) for day, sid in rs]
        by_day, _ = _high(day_rows, lambda k: k[0])
        week = defaultdict(int)
        for (_day, sid), tr in day_rows:
            week[sid] += tr
        by_week, week_avg = _high(list(week.items()), lambda _k: "all")
        return (
            [
                {
                    "day": day_label(day),
                    "staff": names.get(sid, sid),
                    "travel_minutes": tr,
                    "travel_km": round(m[f"{tag}|{day}|{sid}"]["travel_km"], 1),
                    "day_avg": avg,
                    "longest_leg": _longest_leg(rs[(day, sid)], names),
                }
                for (day, sid), tr, avg in sorted(by_day, key=lambda x: (x[0][0], -x[1]))
            ],
            [
                {"staff": names.get(sid, sid), "travel_minutes": tr, "week_avg": avg}
                for sid, tr, avg in sorted(by_week, key=lambda x: -x[1])
            ],
            round(week_avg.get("all", 0), 1),
        )

    cur_day_hi, cur_week_hi, cur_week_avg = highs("cur", cur)
    new_day_hi, new_week_hi, new_week_avg = highs("new", new)
    rows = []
    for day, sid in sorted(set(cur) | set(new), key=lambda k: (k[0], names.get(k[1], ""))):
        a, b = m.get(f"cur|{day}|{sid}"), m.get(f"new|{day}|{sid}")
        rows.append(
            {
                "day": day_label(day),
                "staff": names.get(sid, sid),
                "cur_visits": a["visit_count"] if a else 0,
                "cur_travel": a["travel_minutes"] if a else 0,
                "cur_km": round(a["travel_km"], 1) if a else 0,
                "new_visits": b["visit_count"] if b else 0,
                "new_travel": b["travel_minutes"] if b else 0,
                "new_km": round(b["travel_km"], 1) if b else 0,
                "new_gap": b["gap_minutes"] if b else 0,
            }
        )

    # 案そのものが決まりの検査に落ちるなら、落ちる訪問を外すだけの手が「良くなる」と出てしまうので探さない
    plan_ok = not check(d, out, cap, allow_over)[0]
    moves = []
    if plan_ok:
        log("あと 1 手の確認中…")
        moves = one_more_move(
            d, out, hist_by_day, cap, allow_over, opts, res["threshold_min"], names
        )
    return {
        "config": res["config"],
        "threshold_min": res["threshold_min"],
        "yardstick_match": not mismatch,
        "yardstick_mismatch": mismatch[:20],
        "cur": totals("cur", cur),
        "new": totals("new", new),
        "cur_high_days": cur_day_hi,
        "new_high_days": new_day_hi,
        "cur_high_week": cur_week_hi,
        "new_high_week": new_week_hi,
        "cur_week_avg": cur_week_avg,
        "new_week_avg": new_week_avg,
        "rows": rows,
        "moves": moves,
        "moves_checked": plan_ok,
        "missed": sum(1 for x in moves if x["kind"] == "見落とし"),
        "declined": sum(1 for x in moves if x["kind"] != "見落とし"),
    }
