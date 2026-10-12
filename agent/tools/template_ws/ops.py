"""型の作業場の操作: 作る・解く・もしも・手で動かす・比べる・登録の案。本番には書かない（読むのは rk の読み取り専用 SQL だけ）。"""

from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from tools.courseplan import REPO_ROOT
from tools.courseplan import HERE as CP_HERE
from tools.courseplan import model as M
from tools.courseplan.plan import RED_CLEANUP_BLOCKER, pfv_items, timeline
from tools.template_ws import draft as D
from tools.template_ws import problems as P
from tools.template_ws.draft import TemplateError

Log = Callable[[str], None]


def _unwrap(result: Any) -> Any:
    from tools.courseplan.extract import unwrap

    return unwrap(result)


def _runner(cfg, run_sql):
    if run_sql is not None:
        return run_sql
    from tools.courseplan.extract import rk_runner

    return rk_runner(cfg)


def _days_text(wds) -> str:
    return "".join(M.WD[w] for w in sorted(wds))


# ---------------------------------------------------------------------------
# 作る
# ---------------------------------------------------------------------------


def new(cfg, name: str, *, office: str | None = None, origin: str = D.MASTER, from_raw: str | None = None,
        run_sql=None, force: bool = False, log: Log = print) -> dict:
    """下書きを作る。origin=master なら今のマスター（固定訪問の枠など）を本番から読んで写す（読むだけ）。
    origin=<下書き> ならその下書きを丸ごと写す（材料も同じ・本番は読まない）。"""
    path = D.path_of(cfg, name)
    if path.exists() and not force:
        raise TemplateError(f"下書き {name} はもうあります（作り直すなら --force）")
    if origin != D.MASTER:
        d = D.copy_draft(D.load(cfg, origin), name)
        D.save(cfg, d)
        return d
    if not office:
        raise TemplateError("マスターから作るときは --office（拠点の略称）が要ります")
    if from_raw:
        raw = json.loads(Path(from_raw).read_text(encoding="utf-8"))
    else:
        from tools.courseplan.extract import problem_sql

        log("本番から固定訪問の枠・希望・職員などを読み取り中（読み取り専用）…")
        raw = _unwrap(_runner(cfg, run_sql)(problem_sql(date.today())))
        if not isinstance(raw, dict) or not raw.get("pfv"):
            raise TemplateError("固定訪問の枠が読めませんでした")
    raw.setdefault("candidates", [])
    try:
        ofc = M.office_by_label(raw, office)
    except ValueError as e:
        raise TemplateError(str(e)) from None
    scope = P.scope_patients(raw, ofc["short_label"])
    d = D.new_draft(name, ofc["short_label"], ofc["id"], raw, scope, D.MASTER)
    P.build_week(d)  # 短い番号（sid）を振る
    D.log(d, "new", f"マスター（{raw.get('extracted_at')} に読んだもの）を写しました")
    D.save(cfg, d)
    return d


# ---------------------------------------------------------------------------
# 数える
# ---------------------------------------------------------------------------


def day_metrics(draft: dict, p: dict) -> dict:
    master, mine = P.master_assign(p), P.draft_assign(draft, p)
    chg = M.changes(p, mine)
    new_slots = [s for s in p["slots"] if s.get("new")]
    placed = [s for s in new_slots if s["key"] in mine]
    return {
        "weekday": p["weekday"], "weekday_ja": p["weekday_ja"], "slots": len([s for s in p["slots"] if not s.get("new")]),
        "courses": [c["label"] for c in p["courses"]],
        "master": M.evaluate(p, master), "draft": M.evaluate(p, mine),
        "changes": len(chg), "course_changes": sum(1 for c in chg if c["course_changed"] and not c.get("new")),
        "time_changes": sum(1 for c in chg if c["time_changed"] and not c.get("new")),
        "red_course_changes": sum(1 for c in chg if c["locked"] and c["course_changed"]),
        "red_time_changes": sum(1 for c in chg if c["locked"] and c["time_changed"]),
        "new_placed": len(placed), "new_unplaced": len(new_slots) - len(placed),
        "virtual_used": sum(1 for s in p["slots"] if (mine.get(s["key"]) or {}).get("course")
                            in {c["id"] for c in p["courses"] if c.get("virtual")}),
        "notes": p.get("notes") or [],
    }


def refresh_metrics(draft: dict, problems: list[dict] | None = None) -> list[dict]:
    problems = problems if problems is not None else P.build_week(draft)
    ms = [day_metrics(draft, p) for p in problems]
    draft["metrics"] = {str(m["weekday"]): m for m in ms}
    return ms


def short_line(m: dict, side: str) -> str:
    x = m[side]
    return (f"移動 {x['travel_min']} 分・間に合わない {x['conflicts']}・上限超え {x['over_cap']}・勤務の外 {x['outside_shift']}"
            f"・コースなし {x['no_course']}・人のいないコース {x['unstaffed']}・希望の外 {x['outside_window']}"
            f"・件数 {'/'.join(map(str, x['regular_counts']))}（差 {x['count_spread']}）・マネージャー {x['manager_visits']}")


def summary_lines(draft: dict, ms: list[dict]) -> list[str]:
    out = []
    for m in ms:
        head = f"{m['weekday_ja']}曜（枠 {m['slots']}・コース {len(m['courses'])}）"
        out.append(f"{head} マスター: {short_line(m, 'master')}")
        extra = f"・新しい方を置いた枠 {m['new_placed']}" + (f"・置けない {m['new_unplaced']}" if m["new_unplaced"] else "")
        out.append(f"{head} 下書き: {short_line(m, 'draft')}・変える枠 {m['changes'] - m['new_placed']}{extra}")
    return out


# ---------------------------------------------------------------------------
# 解く
# ---------------------------------------------------------------------------


def _work_dir(cfg, draft: dict) -> Path:
    d = D.drafts_dir(cfg) / f"{draft['name']}.work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def solver_cmd_default() -> list[str]:
    from tools.leveling.run import LevelError, solver_prefix

    try:
        return solver_prefix()
    except LevelError as e:
        raise TemplateError(str(e)) from None


def greedy_place(p: dict, assign: dict, keys: list[str]) -> dict:
    """新しい枠を、決まりの破れ（間に合わない・上限・勤務の外・昼休みなど）が増えない所へ置く。
    2 名体制は 2 枠を同じ時刻・別のコースに。候補はコース × 希望の範囲の 5 分刻み。移動の増えが一番少ない所。"""
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


def solve(cfg, name: str, *, weekdays: list[int] | None = None, seconds: int = 20, max_changes: int | None = None,
          w_change: int = 20, solver_cmd: list[str] | None = None, log: Log = print) -> dict:
    """下書きの曜日ごと（省略で週まるごと＝営業日すべて）にコースと時刻を組み直す。手で動かした枠は動かさない。"""
    draft = D.load(cfg, name)
    problems = P.build_week(draft, weekdays)
    if not problems:
        raise TemplateError("計算する枠がありません（拠点・曜日を確かめてください）")
    work = _work_dir(cfg, draft)
    prob_path, sol_path = work / "problems.json", work / "solution.json"
    prob_path.write_text(json.dumps(problems, ensure_ascii=False), encoding="utf-8")
    cmd = list(solver_cmd or solver_cmd_default()) + [
        str(CP_HERE / "solve.py"), "--in", str(prob_path), "--out", str(sol_path), "--seconds", str(seconds),
        "--w-change", str(w_change)]
    if max_changes is not None:
        cmd += ["--max-changes", str(max_changes)]
    log(f"計算中（{_days_text(p['weekday'] for p in problems)}曜・1 曜日 {seconds} 秒ほど × 最大 2 回）…")
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if res.returncode != 0:
        raise TemplateError(f"計算が止まりました（終了コード {res.returncode}）。上の表示を確かめてください")
    sols = json.loads(sol_path.read_text(encoding="utf-8"))
    by_wd = {p["weekday"]: p for p in problems}
    for s in sols:
        p = by_wd[s["weekday"]]
        assign, reverted = M.tidy(p, s["assign"], w_change)
        keys = {x["key"] for x in p["slots"]}
        for k in keys:
            draft["assign"].pop(k, None)
            draft["unplaced"].pop(k, None)
        for k, a in assign.items():
            if k in keys:
                draft["assign"][k] = {"course": a["course"], "start": int(a["start"])}
        dropped_new = [k for k in s.get("dropped") or [] if k not in assign
                       and next(x for x in p["slots"] if x["key"] == k).get("new")]
        if dropped_new:  # 計算で置けなかった新しい枠（特に 2 名体制）は、決まりの破れが増えない所へ 1 つずつ置いてみる
            placed = greedy_place(p, assign, dropped_new)
            for k, a in placed.items():
                draft["assign"][k] = a
            if placed:
                log(f"{p['weekday_ja']}: 新しい枠を後から置いた {len(placed)}")
        for k in dropped_new:
            if k not in draft["assign"]:
                draft["unplaced"][k] = "入りきらない（希望の時間帯・コースの空き・NG/性別を見て置ける所が無い）"
        if reverted:
            log(f"{p['weekday_ja']}: マスターに戻せた項目 {reverted}")
    ms = refresh_metrics(draft, P.build_week(draft))
    D.log(draft, "solve", f"{_days_text(p['weekday'] for p in problems)}曜を解きました（{seconds} 秒・変える枠の目安 {max_changes}）")
    D.save(cfg, draft)
    return {"draft": draft, "metrics": ms}


# ---------------------------------------------------------------------------
# もしも
# ---------------------------------------------------------------------------


def parse_staff_count(text: str) -> dict[str, int]:
    out = {}
    for part in str(text).split(","):
        if not part.strip():
            continue
        try:
            k, v = part.split("=")
            out[str(P.day_index(k))] = int(v)
        except ValueError:
            raise TemplateError(f"職員の数は mon=4,tue=5 の形で書いてください: {part}") from None
        if int(v) < 1:
            raise TemplateError("職員の数は 1 人以上")
    return out


def parse_pref(text: str) -> tuple[str, int, list[int]]:
    """<患者コード>:<曜日>=HH:MM-HH:MM"""
    try:
        code, rest = str(text).split(":", 1)
        wd, rng = rest.split("=", 1)
    except ValueError:
        raise TemplateError(f"希望のもしもは <患者コード>:<曜日>=HH:MM-HH:MM で書いてください: {text}") from None
    return code.strip(), P.day_index(wd), P.parse_hm_range(rng)


def parse_add_patient(text: str) -> tuple[str, dict]:
    """<患者コード>[:mon,thu][@HH:MM-HH:MM]"""
    s = str(text).strip()
    win = None
    if "@" in s:
        s, w = s.split("@", 1)
        win = P.parse_hm_range(w)
    days = None
    if ":" in s:
        s, ds = s.split(":", 1)
        days = sorted({P.day_index(x) for x in ds.split(",") if x.strip()})
    spec = {}
    if days:
        spec["days"] = days
    if win:
        spec["win"] = win
    return s.strip(), spec


def whatif(cfg, name: str, *, staff_count: str | None = None, add_patient: list[str] | None = None,
           pref: list[str] | None = None, drop_staff: list[str] | None = None, red: str | None = None,
           clear: bool = False, as_name: str | None = None) -> dict:
    """もしもの条件を下書きに書く（解き直しは rk template solve）。as_name なら先に写してからそちらに書く。"""
    src = D.load(cfg, name)
    if as_name and D.path_of(cfg, as_name).exists():
        raise TemplateError(f"下書き {as_name} はもうあります")
    draft = D.copy_draft(src, as_name) if as_name else src
    sc = draft["scenario"]
    raw = draft["snapshot"]["raw"]
    said = []
    if clear:
        keep = {k: sc[k] for k in ("red", "no_pref", "cap")}
        draft["scenario"] = sc = {**D.default_scenario(), **keep}
        said.append("もしもの条件を外しました")
    if staff_count:
        sc["staff_count"].update(parse_staff_count(staff_count))
        said.append(f"職員の数 {staff_count}")
    codes = {s.get("code") for s in raw.get("staff") or []}
    for c in drop_staff or []:
        for x in str(c).split(","):
            x = x.strip()
            if not x:
                continue
            if x not in codes:
                raise TemplateError(f"職員コード {x} が見つかりません")
            if x not in sc["drop_staff"]:
                sc["drop_staff"].append(x)
            said.append(f"{x} を外す")
    cands = P._candidates_by_code(raw)
    pat_codes = {p.get("code") for p in raw.get("patients") or []} | set(cands)
    for a in add_patient or []:
        code, spec = parse_add_patient(a)
        if code not in cands:
            if code in pat_codes:
                raise TemplateError(f"{code} の方にはもう固定訪問の枠があります（枠の無い方だけ足せます。今ある枠は rk template move で）")
            raise TemplateError(f"{code} の方が見つかりません（稼働中か開始前で、枠の無い方だけ）")
        sc["add_patients"][code] = spec
        said.append(f"{code} の方を足す")
    for x in pref or []:
        code, wd, rng = parse_pref(x)
        if code not in pat_codes:
            raise TemplateError(f"患者コード {code} が見つかりません")
        sc["prefs"].setdefault(code, {})[str(wd)] = rng
        said.append(f"{code} の{M.WD[wd]}曜の希望を {M.fmt(rng[0])}〜{M.fmt(rng[1])} に")
    if red:
        sc["red"] = red
        said.append("赤ピンを守る" if red == "keep" else "赤ピンを全部外したら（試算）")
    if not said:
        raise TemplateError("もしもの条件がありません（--staff-count・--add-patient・--pref・--drop-staff・--red・--clear）")
    D.log(draft, "whatif", "・".join(said))
    D.save(cfg, draft)
    return {"draft": draft, "said": said}


# ---------------------------------------------------------------------------
# 手で動かす
# ---------------------------------------------------------------------------


def _find_slot(draft: dict, sid: str) -> tuple[str, int]:
    for k, v in (draft.get("sids") or {}).items():
        if v == sid or k == sid:
            wd = int(k.split(":")[2]) if k.startswith("new:") else None
            if wd is None:
                row = next((r for r in draft["snapshot"]["raw"].get("pfv") or [] if r["id"] == k), None)
                if row is None:
                    break
                wd = int(row["weekday"])
            return k, wd
    raise TemplateError(f"枠 {sid} が見つかりません（番号は rk template report の時間割や compare に出ます。例 mon03）")


def move(cfg, name: str, *, slot: str, course: str, start: str | None = None) -> dict:
    """1 枠を手で動かす（コース・時刻）。決まりを確かめてから。動かした枠は解き直しても動かさない。"""
    draft = D.load(cfg, name)
    key, wd = _find_slot(draft, slot)
    p = P.build_day(draft, wd, exclude_hold={key})
    if p is None:
        raise TemplateError("その曜日は営業日ではありません")
    s = next((x for x in p["slots"] if x["key"] == key), None)
    if s is None:
        raise TemplateError(f"枠 {slot} は今の条件ではこの下書きにありません")
    c = P.find_course(p, course)
    if c["id"] not in s["allowed"]:
        who = M.short_name((c["staff"] or {}).get("name")) or "仮の職員"
        raise TemplateError(f"{c['label']}（{who}さん）は {M.short_name(s['patient'])}様に当てられません（NG か性別の制限）")
    before = P.draft_assign(draft, p)
    cur = before.get(key) or {"course": None, "start": s["start"]}
    st = cur["start"] if start is None else M.hm(start)
    if st % 5:
        raise TemplateError("時刻は 5 分刻みで書いてください")
    if s["locked"] and draft["scenario"].get("red", "keep") == "keep" and st != s["start"]:
        raise TemplateError(f"赤ピン（完全固定）の枠は曜日・時刻を変えません（今 {M.fmt(s['start'])}）。"
                            "コースだけ替えられます。時刻を変えるなら、先に患者マスタで赤を外す（PO の判断）")
    if not (s["win"][0] <= st <= s["win"][1]):
        raise TemplateError(f"{M.fmt(st)} は{s['why']}の範囲（{M.fmt(s['win'][0])}〜{M.fmt(s['win'][1])} に開始）の外です")
    if s["pair"]:
        raise TemplateError("2 名体制の枠は 2 枠を一緒に動かすので、手では動かせません（solve に任せてください）")
    after = {**before, key: {"course": c["id"], "start": st}}
    m0, m1 = M.evaluate(p, before), M.evaluate(p, after)
    draft["assign"][key] = after[key]
    draft["unplaced"].pop(key, None)
    if key not in draft["held"]:
        draft["held"].append(key)
    new_issues = [x for x in m1["issues"] if x not in m0["issues"]]
    refresh_metrics(draft)
    D.log(draft, "move", f"{slot}: {M.short_name(s['patient'])}様 → {c['label']} {M.fmt(st)}")
    D.save(cfg, draft)
    return {"draft": draft, "slot": slot, "to": f"{c['label']} {M.fmt(st)}", "new_issues": new_issues,
            "travel": [m0["travel_min"], m1["travel_min"]]}


# ---------------------------------------------------------------------------
# 比べる
# ---------------------------------------------------------------------------


def _side(cfg, draft: dict, other: str) -> tuple[str, Callable[[dict], tuple[dict, dict]]]:
    """比べる相手 → (名前, 曜日の問題 → (その相手の問題, 割り当て))。"""
    if other == D.MASTER:
        return "マスター", lambda p: (p, P.master_assign(p))
    o = D.load(cfg, other)  # 材料の時点が違っても比べられる（数はそれぞれの材料で数える）

    def f(p):
        q = P.build_day(o, p["weekday"])
        return (q, P.draft_assign(o, q)) if q else (p, {})
    return other, f


def slot_label(p: dict, s: dict, a: dict | None) -> str:
    if not a:
        return "（置いていない）"
    labels = {c["id"]: c["label"] for c in p["courses"]}
    return f"{labels.get(a['course'], '（コースなし）' if a['course'] is None else '（人のいないコース）')} {M.fmt(a['start'])}"


def compare(cfg, name: str, other: str = D.MASTER) -> dict:
    draft = D.load(cfg, name)
    oname, side = _side(cfg, draft, other)
    rows, days = [], []
    for p in P.build_week(draft):
        mine = P.draft_assign(draft, p)
        q, theirs = side(p)
        ma, mb = M.evaluate(p, mine), M.evaluate(q, theirs)
        n = 0
        slots_q = {s["key"]: s for s in q["slots"]}
        for s in p["slots"]:
            a, b = mine.get(s["key"]), theirs.get(s["key"])
            if (a or None) != (b or None):
                n += 1
                rows.append({"sid": s.get("sid"), "weekday": M.WD[p["weekday"]], "patient": s["patient"],
                             "a": slot_label(p, s, a), "b": slot_label(q, slots_q.get(s["key"], s), b),
                             "red": s["locked"], "new": bool(s.get("new"))})
        days.append({"weekday": M.WD[p["weekday"]], "a": ma, "b": mb, "differ": n})
    lines = []
    for d in days:
        a, b = d["a"], d["b"]
        lines.append(f"{d['weekday']}曜: 違う枠 {d['differ']}／間に合わない {b['conflicts']}→{a['conflicts']}・上限超え {b['over_cap']}→{a['over_cap']}"
                     f"・コースなし {b['no_course']}→{a['no_course']}・移動 {b['travel_min']}→{a['travel_min']} 分"
                     f"・件数の差 {b['count_spread']}→{a['count_spread']}・マネージャー {b['manager_visits']}→{a['manager_visits']}")
    return {"a": name, "b": oname, "days": days, "changed": rows,
            "lines": [f"{oname} → {name}（左が {oname}・右が {name}）"] + lines}


# ---------------------------------------------------------------------------
# 登録の案（pfv-update）
# ---------------------------------------------------------------------------


def _reason(chs: list[dict]) -> str:
    """患者 1 名の変わる枠の理由（平易に）。"""
    out = []
    for c in sorted(chs, key=lambda c: (c["weekday"], c["slot_index"])):
        if c.get("new"):
            r = "まだ枠が無いので、希望の曜日・時間帯の中に新しい枠を置く"
        elif c["from_course"] is None:
            r = "コースの空いた枠をコースに入れる"
        elif c["course_changed"] and c["time_changed"]:
            r = "希望の時間帯の中で時刻とコースを合わせ、移動と件数をならす"
        elif c["course_changed"]:
            r = "時刻はそのまま、コースを替えて移動と件数をならす" + ("（赤ピンの枠・曜日と時刻は動かさない）" if c["locked"] else "")
        else:
            r = "コースはそのまま、希望の時間帯の中で時刻を合わせる"
        out.append(f"{M.WD[c['weekday']]}: {r}")
    return "／".join(dict.fromkeys(out))


def check_master(cfg, draft: dict, run_sql=None) -> list[str]:
    """写した後にマスター（その方々の固定訪問の枠）が変わっていないか。変わった方の名前の一覧（空なら変わっていない）。"""
    ids = draft["snapshot"]["scope_patients"]
    rows = _unwrap(_runner(cfg, run_sql)(D.pfv_rows_sql(ids)))
    if isinstance(rows, str):
        rows = json.loads(rows)
    now = D.master_hashes(rows or [], ids)
    was = draft["snapshot"]["master_hash"]
    names = {p["id"]: p["name"] for p in (draft["snapshot"]["raw"].get("patients") or [])
             + (draft["snapshot"]["raw"].get("candidates") or [])}
    return [names.get(pid, pid) for pid in ids if now.get(pid) != was.get(pid)]


def commit(cfg, name: str, *, red_cleanup_done: bool = False, run_sql=None, no_master_check: bool = False,
           log: Log = print) -> dict:
    """下書きとマスターの違いを、患者ごとの pfv-update（型だけ・枠の全件の置き換え）の案にする。反映はしない。"""
    draft = D.load(cfg, name)
    raw = draft["snapshot"]["raw"]
    problems = P.build_week(draft)
    rows_by_patient = defaultdict(list)
    for r in raw.get("pfv") or []:
        rows_by_patient[r["patient_id"]].append(r)
    all_chg, timelines, warnings, virtual = [], [], [], 0
    ms = refresh_metrics(draft, problems)
    for p, m in zip(problems, ms):
        mine = P.draft_assign(draft, p)
        chg = M.changes(p, mine)
        for c in chg:
            c["dur"] = next(s["dur"] for s in p["slots"] if s["key"] == c["key"])
        all_chg += chg
        timelines.append(timeline(p, P.master_assign(p), "マスター"))
        timelines.append(timeline(p, mine, f"下書き {name}", {c["key"] for c in chg}))
        warnings += m["notes"]
        warnings += [f"下書きでも残る: {x}" for x in m["draft"]["issues"]]
        virtual += m["virtual_used"]
    for k, why in sorted((draft.get("unplaced") or {}).items()):
        warnings.append(f"まだ枠の無い方で置けなかった枠: {draft['sids'].get(k, k)}（{why}）")
    for sp in P.candidate_specs(draft):
        if sp["reason"]:
            warnings.append(f"まだ枠の無い方 {sp['patient'].get('code')}: {sp['reason']}")

    by_patient = defaultdict(list)
    for c in all_chg:
        by_patient[c["patient_id"]].append(c)
    ops = []
    for pid, chs in sorted(by_patient.items(), key=lambda kv: kv[1][0]["patient"]):
        changed = {c["key"]: {"course": c["to_course"], "start": c["to_start"]} for c in chs if not c.get("new")}
        items = pfv_items(rows_by_patient.get(pid, []), changed)
        for c in sorted((c for c in chs if c.get("new")), key=lambda c: (c["weekday"], c["slot_index"])):
            items.append({"weekday": c["weekday"], "start_time": f"{c['to_start'] // 60:02d}:{c['to_start'] % 60:02d}",
                          "duration_min": c["dur"], "course_template_id": c["to_course"], "sub_office_id": None,
                          "slot_index": c["slot_index"], "is_pinned": False, "movability": "unknown"})
        items.sort(key=lambda it: (it["weekday"], it["slot_index"]))
        what = "・".join(
            f"{M.WD[c['weekday']]} " + (f"新しい枠 {M.fmt(c['to_start'])} {c['to_label']}" if c.get("new") else
                                        f"{M.fmt(c['from_start'])}" + (f"→{M.fmt(c['to_start'])}" if c["time_changed"] else "")
                                        + (f" {c['from_label']}→{c['to_label']}" if c["course_changed"] else ""))
            for c in sorted(chs, key=lambda c: (c["weekday"], c["slot_index"])))
        ops.append({"op": "pfv-update", "patient_id": pid, "mode": "normal", "change_scope": "pattern_only",
                    "items": items, "label": f"{chs[0]['patient']}様 固定訪問の枠 {what}", "reason": _reason(chs)})

    blockers = []
    if not red_cleanup_done:
        blockers.append(RED_CLEANUP_BLOCKER)
    if no_master_check:
        blockers.append("下書きを作った後にマスターが変わっていないかを確かめていません（--no-master-check）")
    else:
        log("下書きを作った後にマスターが変わっていないかを確かめています（読むだけ）…")
        moved = check_master(cfg, draft, run_sql)
        if moved:
            blockers.append(f"下書きを作った後にマスターが変わった方がいます（{len(moved)} 名: {'・'.join(M.short_name(x) + '様' for x in moved[:8])}"
                            f"{'…' if len(moved) > 8 else ''}）。マスターから下書きを作り直してください")
    red_time = [c for c in all_chg if c["locked"] and c["time_changed"]]
    if red_time:
        blockers.append(f"赤ピンの枠の時刻を変える枠が {len(red_time)} 件あります（赤を外した試算）。赤の時刻の変更は登録できません")
    if virtual:
        blockers.append(f"仮の職員のコースに入れた枠が {virtual} 件あります（もしもの職員の数の試算）。本当の人の数で解き直してください")
    sc = draft["scenario"]
    if sc.get("prefs"):
        blockers.append("希望の時間帯のもしも（--pref）で作った下書きです。希望はマスター（rakusuke-master の希望の変更）で先に変えてから")
    if sc.get("drop_staff") or sc.get("staff_count"):
        warnings.insert(0, "もしもの職員の条件（外した職員・職員の数）で作った型です。本当にその人数で回すときだけ登録してください")
    if not ops:
        blockers.append("マスターと違う枠がありません（登録するものがない）")

    n_new = sum(1 for c in all_chg if c.get("new"))
    n_course = sum(1 for c in all_chg if c["course_changed"] and not c.get("new"))
    n_time = sum(1 for c in all_chg if c["time_changed"] and not c.get("new"))
    summary = [f"マスターと違う枠 {len(all_chg) - n_new}（コース {n_course}・時刻 {n_time}）＋新しい枠 {n_new}／患者 {len(ops)} 名。"
               "型だけ（週の予定は次に「週を作る」ときから）。赤ピンを付けるのは登録の後に患者マスタで"]
    summary += summary_lines(draft, ms)
    tot = lambda k, side: sum(m[side][k] for m in ms)  # noqa: E731
    headline = [
        f"{draft['office']}の型（{_days_text(p['weekday'] for p in problems)}曜）を {len(ops)} 名分変えます"
        f"（変える枠 {len(all_chg) - n_new}・新しい枠 {n_new}）。予定は次に週を作るときから",
        f"間に合わない組 {tot('conflicts', 'master')}→{tot('conflicts', 'draft')}・上限超え {tot('over_cap', 'master')}→{tot('over_cap', 'draft')}"
        f"・コースの空いた枠 {tot('no_course', 'master')}→{tot('no_course', 'draft')}",
    ]
    if blockers:
        headline.append(f"まだ登録できません（{blockers[0]}）")
    issues = [{"severity": "warn", "text": f"まだ枠の無い方の {draft['sids'].get(k, k)} が置けません（{why}）",
               "options": ["希望の時間帯を相談する", "応援を 1 名", "保留"]}
              for k, why in sorted((draft.get("unplaced") or {}).items())]
    issues += [{"severity": "warn", "text": f"{m['weekday_ja']}曜 下書きでも残る: {x}", "options": ["手で動かす", "このまま", "保留"]}
               for m in ms for x in m["draft"]["issues"]]
    issues += [{"severity": "info", "text": f"まだ枠の無い方 {sp['patient'].get('code')}: {sp['reason']}", "options": []}
               for sp in P.candidate_specs(draft) if sp["reason"]]
    conditions = []
    if sc.get("staff_count"):
        conditions.append("もしも: 職員の数 " + "・".join(f"{M.WD[int(k)]} {v} 人" for k, v in sorted(sc["staff_count"].items())))
    if sc.get("drop_staff"):
        conditions.append("もしも: 外した職員 " + "・".join(sc["drop_staff"]))
    if sc.get("add_patients"):
        conditions.append("足した方 " + "・".join(sc["add_patients"]))
    if sc.get("prefs"):
        conditions.append("もしも: 希望の時間帯を仮に変えた方 " + "・".join(sc["prefs"]))
    if sc.get("red") == "ignore":
        conditions.append("赤ピンを全部外した試算")
    now = datetime.now()
    plan = {
        "kind": "course-template",
        "headline": headline[:3],
        "issues": issues,
        "conditions": conditions,
        "title": f"型の作業場 {name}（{draft['office']}）の登録",
        "target": {"office": draft["office"], "draft": name, "weekdays": _days_text(p["weekday"] for p in problems)},
        "created_at": now.isoformat(timespec="seconds"),
        "ready": not blockers,
        "blockers": blockers,
        "summary": summary,
        "ops": ops,
        "checks": [
            "全員稼働の前提。その曜日に出勤の型がある職員（新人を除く・マネージャーを含む）1 人に 1 コース。勤務時刻・性別・NG・固定イベントはその人で見る",
            "1 コース 1 日 6 名まで（マネージャーのコースはあふれの受け皿）／移動（直線 20km/h・別住所へ 8 分）が間に合う",
            "昼休み 11:30〜13:30 の中で 30 分は必ず／固定イベントの前後 15 分は入れない",
            "赤ピンの枠は曜日・時刻を動かさない（コースは替えてよい）。赤の付け外しはしない（患者マスタで人がする）",
            "希望の時間帯の中だけで時刻を動かす（固定・希望なしの方は今の時刻のまま）・曜日は変えない",
            "まだ枠の無い方は希望の曜日・時間帯の中に新しい枠を置く（赤ピンは付けない）",
        ],
        "warnings": warnings,
        "timelines": timelines,
        "metrics": ms,
        "changes": [{**c, "from_start": M.fmt(c["from_start"]), "to_start": M.fmt(c["to_start"])} for c in all_chg],
        "source": {"tool": "template_ws", "draft": name, "extracted_at": draft["snapshot"].get("extracted_at"),
                   "master_hash": D.combined_hash(draft["snapshot"]["master_hash"]),
                   "scope_patients": len(draft["snapshot"]["scope_patients"])},
    }
    plans_dir = Path(cfg.runs_dir) / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = plans_dir / f"{now:%Y%m%d-%H%M}-template-{name}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    D.log(draft, "commit", f"登録の案を作りました（{path.name}・ready={plan['ready']}）")
    D.save(cfg, draft)
    return {"plan_path": str(path), "plan": plan}
