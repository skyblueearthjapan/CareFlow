"""agent/tools/courseplan/solve.py の写し（OR-Tools）。ortools はここだけで読む。"""

# ruff: noqa
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import model as M  # noqa: E402
from . import wl_core as WL  # noqa: E402
from ortools.constraint_solver import pywrapcp, routing_enums_pb2  # noqa: E402

W_CHANGE_COURSE = 20  # コースを替える 1 枠（移動 20 分と同じ重さ）
W_DROP_NEW = 100_000  # まだ枠の無い方の新しい枠を置けない（今ある枠を押し出すより軽い）


def check_same_rules() -> None:
    """model.py に写した決まりの値が solver.py と同じか（違えば止める）。"""
    pairs = [("SPEED_KMH", M.SPEED_KMH, WL.SPEED_KMH), ("BUFFER_MIN", M.BUFFER_MIN, WL.BUFFER_MIN),
             ("SAME_ADDR_DEG", M.SAME_ADDR_DEG, WL.SAME_ADDR_DEG), ("PAIR_BLOCK_MIN", M.PAIR_BLOCK_MIN, WL.PAIR_BLOCK_MIN),
             ("LUNCH_WIN", M.LUNCH_WIN, WL.LUNCH_WIN), ("LUNCH_MIN_HARD", M.LUNCH_MIN_HARD, WL.LUNCH_MIN_HARD),
             ("LUNCH_TARGET", M.LUNCH_TARGET, WL.LUNCH_TARGET), ("EVENT_BUFFER_MIN", M.EVENT_BUFFER_MIN, WL.EVENT_BUFFER_MIN),
             ("BUSINESS_END", M.BUSINESS_END, WL.BUSINESS_END), ("FIXED_JOIN_MAX_GAP", M.FIXED_JOIN_MAX_GAP, WL.FIXED_JOIN_MAX_GAP)]
    bad = [n for n, a, b in pairs if a != b]
    if bad:
        raise SystemExit(f"model.py の決まりの値が solver.py と違います: {bad}")


def make_jobs(problem: dict) -> list:
    """同じ建物の 2 名は solver.make_jobs でまとめる。2 名体制の枠（pair）はまとめずに 1 枠ずつ。"""
    singles, pairs = [], []
    for s in problem["slots"]:
        v = WL.Visit(id=s["key"], patient_id=s["patient_id"], patient=s["patient"], office=s["patient_office"],
                     day=date(2000, 1, 3), fixed_start=s["start"], dur=s["dur"], win=tuple(s["win"]),
                     time_type=s["time_type"], loc=tuple(s["loc"]), female_only=s["female_only"], ng=set(s["ng"]),
                     cur_staff=None, cur_course=s["cur_course"])
        (pairs if (s["pair"] or s.get("new")) else singles).append(v)  # 新しい枠は同じ建物でまとめない
    jobs = WL.make_jobs(singles)
    jobs += [WL._job([v], [0], v.fixed_start, v.dur, v.win[0], v.win[1]) for v in pairs]
    return jobs


def solve_problem(problem: dict, seconds: int, scale: float, w_change: int, lunch: int, use_hint: bool = True):
    slots = {s["key"]: s for s in problem["slots"]}
    courses = problem["courses"]
    jobs = make_jobs(problem)
    n, V = len(jobs), len(courses)
    if V == 0 or n == 0:
        return {}, [s for j in jobs for s in (v.id for v in j.visits)]
    locs = [j.visits[0].loc for j in jobs]
    starts = [n + i for i in range(V)]
    mgr = pywrapcp.RoutingIndexManager(n + V, V, starts, starts)
    routing = pywrapcp.RoutingModel(mgr)
    tm = [[0 if (a >= n or b >= n) else WL.travel_min(locs[a], locs[b]) for b in range(n + V)] for a in range(n + V)]
    service = [j.dur for j in jobs] + [0] * V
    visit_transits = [service[mgr.IndexToNode(i)] for i in range(routing.Size())] + [0] * V

    def time_cb(i, j):
        a, b = mgr.IndexToNode(i), mgr.IndexToNode(j)
        t = service[a] + tm[a][b]
        if a < n and b < n and tm[a][b] > 0:
            t += WL.BUFFER_MIN
        return t

    routing.AddDimension(routing.RegisterTransitCallback(time_cb), 600, 24 * 60, False, "Time")
    T = routing.GetDimensionOrDie("Time")
    solver = routing.solver()
    w_move = max(1, round(WL.W_MOVE_PER_MIN * scale))
    for k, j in enumerate(jobs):
        idx = mgr.NodeToIndex(k)
        is_new = any(slots[v.id].get("new") for v in j.visits)
        T.CumulVar(idx).SetRange(j.lo, j.hi)
        if not is_new:  # 新しい枠（まだ枠の無い方）には「今の時刻」が無い → 希望の範囲のどこでもよい
            T.SetCumulVarSoftLowerBound(idx, j.start, w_move)
            T.SetCumulVarSoftUpperBound(idx, j.start, w_move)
        routing.AddDisjunction([idx], W_DROP_NEW if is_new else WL.W_DROP)
        if j.lo < j.hi:
            slot = solver.IntVar(j.lo // 5, j.hi // 5 + 1, f"slot{k}")
            solver.Add(T.CumulVar(idx) == slot * 5)
        for i, c in enumerate(courses):
            if any(c["id"] not in slots[v.id]["allowed"] for v in j.visits):
                routing.VehicleVar(idx).RemoveValue(i)

    # 2 名体制: 同じ時刻・別のコース・両方入るか両方入らないか
    by_pair: dict[str, list[int]] = {}
    for k, j in enumerate(jobs):
        p = slots[j.visits[0].id]["pair"]
        if p:
            by_pair.setdefault(p, []).append(mgr.NodeToIndex(k))
    for idxs in by_pair.values():
        for a, b in zip(idxs, idxs[1:]):
            solver.Add(routing.ActiveVar(a) == routing.ActiveVar(b))
            solver.Add(T.CumulVar(a) == T.CumulVar(b))
            solver.Add(solver.IsDifferentVar(routing.VehicleVar(a), routing.VehicleVar(b)) + 1 - routing.ActiveVar(a) >= 1)

    for i, c in enumerate(courses):
        st, en = c["shift"]
        hard_end = max(en, WL.BUSINESS_END) if c["manager"] else en
        T.CumulVar(routing.Start(i)).SetRange(st, hard_end)
        T.CumulVar(routing.End(i)).SetRange(st, hard_end)
        if c["manager"] and en < hard_end:
            T.SetCumulVarSoftUpperBound(routing.End(i), en, WL.W_MANAGER_OVERTIME_PER_MIN)
        routing.AddVariableMaximizedByFinalizer(T.CumulVar(routing.Start(i)))
        routing.AddVariableMinimizedByFinalizer(T.CumulVar(routing.End(i)))
        raw = [tuple(e) for e in c["events"]]
        evs = [(max(0, a - WL.EVENT_BUFFER_MIN), min(24 * 60, b + WL.EVENT_BUFFER_MIN)) for a, b in raw]
        breaks = []
        if WL.needs_lunch(st, hard_end):
            my_lunch, ok = lunch, WL.lunch_starts(lunch, raw)
            if not ok:
                my_lunch, ok = WL.LUNCH_MIN_HARD, WL.lunch_starts(WL.LUNCH_MIN_HARD, raw)
            if ok:
                iv = solver.FixedDurationIntervalVar(ok[0], ok[-1], my_lunch, False, f"lunch{i}")
                sv, okset = iv.StartExpr().Var(), set(ok)
                for x in range(ok[0], ok[-1] + 1):
                    if x not in okset:
                        sv.RemoveValue(x)
                breaks.append(iv)
        for k, (ea, eb) in enumerate(evs):
            breaks.append(solver.FixedDurationIntervalVar(ea, ea, eb - ea, False, f"ev{i}_{k}"))
        if breaks:
            T.SetBreakIntervalsOfVehicle(breaks, i, visit_transits)

    counts = [j.count for j in jobs] + [0] * V
    C_idx = routing.RegisterUnaryTransitCallback(lambda i: counts[mgr.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(C_idx, 0, [c["cap"] for c in courses], True, "Count")

    w_chg = max(1, round(w_change * scale))
    for i, c in enumerate(courses):
        extra = []
        for j in jobs:
            e = w_chg * sum(1 for v in j.visits if slots[v.id]["cur_course"] != c["id"])
            if c["manager"]:
                e += WL.W_MANAGER_VISIT * j.count
            extra.append(e)

        def cb(a, b, extra=extra):
            na, nb = mgr.IndexToNode(a), mgr.IndexToNode(b)
            return tm[na][nb] + (extra[nb] if nb < n else 0)

        routing.SetArcCostEvaluatorOfVehicle(routing.RegisterTransitCallback(cb), i)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = seconds
    sol = None
    if use_hint:  # いまの型を出発点にする（入れられれば変える枠が少ない解から探せる）
        col = {c["id"]: i for i, c in enumerate(courses)}
        routes = [[] for _ in courses]
        for k, j in sorted(enumerate(jobs), key=lambda kj: kj[1].start):
            cur = {slots[v.id]["cur_course"] for v in j.visits}
            cid = next(iter(cur))
            if len(cur) == 1 and cid in col and all(cid in slots[v.id]["allowed"] for v in j.visits):
                routes[col[cid]].append(k)
        routing.CloseModelWithParameters(params)
        hint = routing.ReadAssignmentFromRoutes(routes, True)
        if hint is not None:
            sol = routing.SolveFromAssignmentWithParameters(hint, params)
    if sol is None:
        sol = routing.SolveWithParameters(params)
    if sol is None:
        raise RuntimeError(f"{problem['office']} {problem['weekday_ja']}: 解が見つかりません")
    assign, placed = {}, set()
    for i, c in enumerate(courses):
        idx = routing.Start(i)
        while not routing.IsEnd(idx):
            node = mgr.IndexToNode(idx)
            if node < n:
                j, t = jobs[node], sol.Value(T.CumulVar(idx))
                for v, off in zip(j.visits, j.offsets):
                    assign[v.id] = {"course": c["id"], "start": t + off}
                placed.add(node)
            idx = sol.Value(routing.NextVar(idx))
    dropped = [v.id for k, j in enumerate(jobs) if k not in placed for v in j.visits]
    return assign, dropped


def count_changes(problem: dict, assign: dict) -> int:
    return len(M.changes(problem, assign))


def solve_day(problem: dict, seconds: int, max_changes: int | None, w_change: int, log=print) -> dict:
    cur = M.current_assignment(problem)
    scale, tries = 1.0, []
    for attempt in range(4):
        best = None
        for lunch in (WL.LUNCH_TARGET, WL.LUNCH_MIN_HARD):
            try:
                a, d = solve_problem(problem, seconds, scale, w_change, lunch)
            except RuntimeError:
                continue
            if best is None or len(d) < len(best[1]):
                best = (a, d, lunch)
            if not d:
                break
        if best is None:
            raise RuntimeError(f"{problem['office']} {problem['weekday_ja']}: 解が見つかりません")
        assign, dropped, lunch = best
        for k in dropped:  # 入らない枠は今のまま（新しい枠は置かない）
            if k in cur:
                assign[k] = dict(cur[k])
        n_chg = count_changes(problem, assign)
        tries.append({"scale": scale, "changes": n_chg, "dropped": len(dropped), "lunch": lunch})
        log(f"{problem['office']} {problem['weekday_ja']}: 変える枠 {n_chg}・入らない {len(dropped)}・昼休み {lunch} 分（重み ×{scale:g}）")
        if max_changes is None or n_chg <= max_changes:
            break
        scale *= 3
    return {"office": problem["office"], "weekday": problem["weekday"], "assign": assign, "dropped": dropped,
            "lunch": lunch, "tries": tries}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="コースのならしの計算（rk courseplan から呼ぶ）")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--max-changes", type=int)
    ap.add_argument("--w-change", type=int, default=W_CHANGE_COURSE)
    a = ap.parse_args(argv)
    check_same_rules()
    problems = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    out = [solve_day(p, a.seconds, a.max_changes, a.w_change, log=lambda m: print(m, flush=True)) for p in problems]
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
