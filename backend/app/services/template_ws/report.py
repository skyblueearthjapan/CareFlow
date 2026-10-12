"""最適化の報告: 「何をどう変えたか」を 1 か所で作る (画面の［くわしく］とらく助エージェントの両方が使う).

* 変わった枠を種類ごとに分ける (コースを付けた・コースだけ・コースと時刻・時刻だけ・新しく置いた・外した)。
* 曜日ごとの物差し (いまの型 → 最適化の前 → 最適化の後)。
* 移動の合計の増減を「道順に新しく入った訪問による分」と「同じ訪問の並べ替えによる分」に分ける。
  コースの無い枠・人のいないコースの枠・新しく置いた方は、前は道順に入っておらず移動に数えられていなかった。
* 平易な説明文と、気になる点。

DB に触らない (材料は呼ぶ側が渡す)。最適化の手順 (kind=optimise) の ``summary`` に保存し、後から開き直せる。
"""

# ruff: noqa: N812, N806, N803, N818, B905

from __future__ import annotations

from . import engine as E
from . import model as M

KINDS = (
    ("course_added", "コースが無かった枠にコースを付けた"),
    ("course_only", "コースだけ変えた"),
    ("course_and_time", "コースと時刻を変えた"),
    ("time_only", "時刻だけ変えた"),
    ("new_placed", "新しく置いた（まだ枠の無かった方）"),
    ("removed", "外した"),
    ("not_placed", "置けなかった（まだ枠の無かった方）"),
)
LABEL = dict(KINDS)
DAY_KEYS = ("conflicts", "over_cap", "no_course", "unstaffed", "travel_min", "regular_counts", "manager_visits",
            "outside_window", "outside_shift", "ng_or_sex", "lunch_short")


def _fmt(m) -> str | None:
    return M.fmt(int(m)) if m is not None else None


def classify(change: dict) -> str | None:
    """1 枠の変化 (before/after の状態) → 種類。変化が無い・置けないままは None。"""
    b, a = change.get("before") or {}, change.get("after") or {}
    is_new = str(change.get("key", "")).startswith("new:")
    bc, ac = b.get("course"), a.get("course")
    bs, as_ = b.get("start"), a.get("start")
    if not a or ac is None:
        if bc is not None:
            return "removed"
        if is_new and a and not b:
            return "not_placed"
        return None
    if is_new and bc is None:
        return "new_placed"
    if bc is None:
        return "course_added"
    if bc != ac:
        return "course_only" if bs == as_ else "course_and_time"
    if bs != as_:
        return "time_only"
    return None


def categorize(changes: list[dict], labels: dict[str, str], names: dict[str, str],
               office_of: dict[str, str] | None = None) -> list[dict]:
    """変わった枠 → 種類ごとの {kind, label, count, items[曜日・患者様・前→後]}。0 件の種類は出さない。"""
    groups: dict[str, list[dict]] = {k: [] for k, _ in KINDS}
    for c in changes:
        kind = classify(c)
        if kind is None:
            continue
        b, a = c.get("before") or {}, c.get("after") or {}
        groups[kind].append({
            "key": c["key"], "weekday": int(c["weekday"]), "weekday_ja": M.WD[int(c["weekday"])],
            "patient_id": c.get("patient_id"), "patient": names.get(c.get("patient_id") or "", "（不明）"),
            "office": (office_of or {}).get(c.get("patient_id") or ""),
            "from_course": labels.get(b.get("course") or "", "（コースなし）") if b else "（まだ枠なし）",
            "to_course": labels.get(a.get("course") or "", "（コースなし）") if a else "（外した）",
            "from_start": _fmt(b.get("start")) if b and kind != "new_placed" else None,
            "to_start": _fmt(a.get("start")) if a else None,
            "red": bool(c.get("locked")),
        })
    out = []
    for k, label in KINDS:
        items = sorted(groups[k], key=lambda x: (x["weekday"], x["from_start"] or x["to_start"] or "", x["patient"]))
        if items:
            out.append({"kind": k, "label": label, "count": len(items), "items": items})
    return out


def _slim(m: dict) -> dict:
    return {k: m[k] for k in DAY_KEYS}


def _sum(evs: list[dict]) -> dict:
    out: dict = {}
    for k in DAY_KEYS:
        if k == "regular_counts":
            out[k] = [x for e in evs for x in e[k]]
        else:
            out[k] = sum(e[k] for e in evs)
    return out


def _routed(p: dict, assign: dict) -> set[str]:
    """道順に入っている枠 (その曜日に人のいるコースに置いた枠)。"""
    open_ids = {c["id"] for c in p["courses"]}
    return {k for k, a in assign.items() if a.get("course") in open_ids}


def day_part(p: dict, master: dict, before: dict, after: dict) -> dict:
    """1 拠点 × 1 曜日の数と、移動の増減の内訳。"""
    mm, mb, ma = M.evaluate(p, master), M.evaluate(p, before), M.evaluate(p, after)
    rb = _routed(p, before)
    same = {k: v for k, v in after.items() if k in rb}
    travel_same = M.evaluate(p, same)["travel_min"] if same else 0
    open_ids = {c["id"] for c in p["courses"]}
    slots = {s["key"]: s for s in p["slots"]}
    newly = {"no_course": 0, "unstaffed": 0, "new": 0}
    for k in _routed(p, after) - rb:
        s = slots.get(k) or {}
        b = before.get(k)
        if s.get("new") or b is None:
            newly["new"] += 1
        elif b.get("course") is None:
            newly["no_course"] += 1
        elif b.get("course") not in open_ids:
            newly["unstaffed"] += 1
    return {"master": _slim(mm), "before": _slim(mb), "after": _slim(ma),
            "travel": {"reorder": travel_same - mb["travel_min"], "routed": ma["travel_min"] - travel_same},
            "newly": newly}


def optimise_summary(items: list[tuple[dict, dict, dict]], changes: list[dict], labels: dict[str, str],
                     names: dict[str, str], office_of: dict[str, str] | None = None) -> dict:
    """最適化 1 回の報告。items = [(曜日の問題, 最適化の前の割り当て, 後の割り当て)]（拠点 × 曜日）。"""
    by_wd: dict[int, dict] = {}
    for p, before, after in items:
        d = day_part(p, E.master_assign(p), before, after)
        x = by_wd.setdefault(p["weekday"], {"parts": [], "reorder": 0, "routed": 0,
                                            "newly": {"no_course": 0, "unstaffed": 0, "new": 0}})
        x["parts"].append(d)
        x["reorder"] += d["travel"]["reorder"]
        x["routed"] += d["travel"]["routed"]
        for k, v in d["newly"].items():
            x["newly"][k] += v
    cats = categorize(changes, labels, names, office_of)
    per_day_changes: dict[int, int] = {}
    for c in cats:
        for it in c["items"]:
            per_day_changes[it["weekday"]] = per_day_changes.get(it["weekday"], 0) + 1
    days = []
    for wd, x in sorted(by_wd.items()):
        days.append({
            "weekday": wd, "name": M.WD[wd],
            "master": _sum([d["master"] for d in x["parts"]]),
            "before": _sum([d["before"] for d in x["parts"]]),
            "after": _sum([d["after"] for d in x["parts"]]),
            "changes": per_day_changes.get(wd, 0),
            "travel": {"delta": x["reorder"] + x["routed"], "reorder": x["reorder"], "routed": x["routed"],
                       "newly": x["newly"]},
        })
    travel = {
        "before": sum(d["before"]["travel_min"] for d in days),
        "after": sum(d["after"]["travel_min"] for d in days),
        "master": sum(d["master"]["travel_min"] for d in days),
        "reorder": sum(d["travel"]["reorder"] for d in days),
        "routed": sum(d["travel"]["routed"] for d in days),
        "newly": {k: sum(d["travel"]["newly"][k] for d in days) for k in ("no_course", "unstaffed", "new")},
    }
    travel["delta"] = travel["after"] - travel["before"]
    out = {"changed": len(changes), "categories": cats, "days": days, "travel": travel}
    out["text"] = explain(out)
    out["cautions"] = cautions(out)
    return out


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def _newly_words(newly: dict) -> str:
    parts = []
    if newly.get("no_course"):
        parts.append(f"コースの無い枠 {newly['no_course']} 件")
    if newly.get("unstaffed"):
        parts.append(f"人のいないコースの枠 {newly['unstaffed']} 件")
    if newly.get("new"):
        parts.append(f"新しく置いた方 {newly['new']} 件")
    return "・".join(parts)


def explain(s: dict) -> list[str]:
    """平易な説明文 (数行)。移動の合計が増えたときは、その理由を必ず書く。"""
    lines = []
    cats = s["categories"]
    if cats:
        lines.append(f"変わった枠は {sum(c['count'] for c in cats)} 件です（"
                     + "・".join(f"{c['label']} {c['count']} 件" for c in cats) + "）。")
    else:
        lines.append("変わった枠はありません。")
    t = s["travel"]
    if t.get("before") is None:
        return lines
    newly = _newly_words(t["newly"])
    if t["delta"] > 0:
        line = f"移動の合計は {t['before']} 分 → {t['after']} 分（{_signed(t['delta'])} 分）に増えました。"
        if t["routed"] > 0 and newly:
            line += (f"このうち {_signed(t['routed'])} 分は、前は道順に入っておらず移動に数えられていなかった訪問"
                     f"（{newly}）を道順に入れた分です。")
        line += f"同じ訪問の並べ替えによる分は {_signed(t['reorder'])} 分です。"
        lines.append(line)
    elif t["delta"] < 0:
        line = f"移動の合計は {t['before']} 分 → {t['after']} 分（{_signed(t['delta'])} 分）に減りました。"
        line += f"並べ替えによる分 {_signed(t['reorder'])} 分"
        if t["routed"]:
            line += f"・道順に新しく入った訪問（{newly or 'なし'}）の分 {_signed(t['routed'])} 分"
        lines.append(line + "です。")
    else:
        lines.append(f"移動の合計は {t['after']} 分で変わりません。")
    big = [d for d in s["days"] if d["travel"]["delta"] >= 15]
    for d in sorted(big, key=lambda d: -d["travel"]["delta"])[:3]:
        why = _newly_words(d["travel"]["newly"])
        reason = (f"{why}を道順に入れたためです" if why and d["travel"]["routed"] > 0
                  else "同じ訪問の並べ替えで、間に合わない組や上限を合わせるためです")
        lines.append(f"{d['name']}曜は移動が {_signed(d['travel']['delta'])} 分: {reason}"
                     f"（並べ替えの分 {_signed(d['travel']['reorder'])} 分）。")
    before = {k: sum(d["before"][k] for d in s["days"]) for k in ("conflicts", "over_cap", "no_course", "unstaffed")}
    after = {k: sum(d["after"][k] for d in s["days"]) for k in ("conflicts", "over_cap", "no_course", "unstaffed")}
    words = {"conflicts": "間に合わない組", "over_cap": "上限超え", "no_course": "コースの無い枠",
             "unstaffed": "人のいないコースの枠"}
    moved = [f"{words[k]} {before[k]} → {after[k]}" for k in words if before[k] != after[k]]
    if moved:
        lines.append("守る決まりの数: " + "・".join(moved) + "。")
    return lines


def cautions(s: dict) -> list[str]:
    """気になる点 (管理者の訪問が増えた・まだ残る間に合わない組・件数の差が広がった など)。"""
    out = []
    days = s["days"]
    if not days:
        return out
    tot = lambda side, k: sum(d[side][k] for d in days)  # noqa: E731
    if tot("after", "manager_visits") > tot("before", "manager_visits"):
        up = [f"{d['name']}曜 {d['before']['manager_visits']}→{d['after']['manager_visits']}" for d in days
              if d["after"]["manager_visits"] > d["before"]["manager_visits"]]
        out.append("管理者（M のコース）の訪問が増えました（" + "・".join(up) + "）。管理者の時間が足りるか確かめてください")
    for k, w in (("conflicts", "移動が間に合わない組"), ("over_cap", "上限超え"), ("ng_or_sex", "NG・性別の制限に当たる枠"),
                 ("outside_shift", "勤務時刻の外の枠"), ("lunch_short", "昼休みが 30 分取れないコース")):
        n = tot("after", k)
        if n:
            ds = "・".join(f"{d['name']}曜 {d['after'][k]}" for d in days if d["after"][k])
            out.append(f"{w}がまだ {n} 件あります（{ds}）")
    if tot("after", "no_course"):
        out.append(f"コースの無い枠がまだ {tot('after', 'no_course')} 件あります")
    np = next((c for c in s["categories"] if c["kind"] == "not_placed"), None)
    if np:
        out.append(f"まだ枠の無かった方のうち {np['count']} 件は置けませんでした（希望の時間帯・コースの空きを確かめてください）")
    for d in days:
        b, a = d["before"]["regular_counts"], d["after"]["regular_counts"]
        if a and b and (max(a) - min(a)) > (max(b) - min(b)) + 1:
            out.append(f"{d['name']}曜は一般職員の件数の差が広がりました（{'・'.join(map(str, b))} → {'・'.join(map(str, a))}）")
    red = sum(1 for c in s["categories"] for it in c["items"] if it["red"] and c["kind"] in ("course_only", "course_and_time"))
    if red:
        out.append(f"赤ピンの枠のコースを {red} 件替えています（曜日と時刻はそのままです）")
    return out


def fallback_summary(changes: list[dict], labels: dict[str, str], names: dict[str, str]) -> dict:
    """報告を残す前に作った最適化の手順 (summary が無い) 用: 種類ごとの分け方と説明だけ。"""
    cats = categorize(changes, labels, names)
    out = {"changed": len(changes), "categories": cats, "days": [], "travel": {}}
    out["text"] = explain({**out, "travel": {}}) + ["（この最適化は報告を残す前のものなので、曜日ごとの数と移動の内訳はありません）"]
    out["cautions"] = []
    return out
