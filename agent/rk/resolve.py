"""入らない訪問の決めごと（rk plan resolve）。

ならし案（rk level）は入らない訪問を plan["unplaced"] に並べ、blockers に「入らない訪問が N 件…」を入れて
ready:false にする。管理者が 1 件ずつ（または全部まとめて）次のどれかを選ぶと、その訪問は「決めた」になる:

  pool        保留プールへ戻す（案の return-to-pool をそのまま使う。API では戻せない → バックアップから）
  allow-over  --staff の職員へ今週だけ付ける（assign-staff-week。その職員は上限を超える＝承知の上）
  shift       --start の時刻へ今週だけずらす（move-visit-week・同じ日・同じコース）。--staff があれば先に付け替える
  keep        いまの担当のまま残す（休みの職員に訪問が残る＝承知の上）

決めたことは plan["resolutions"]（誰が・いつ）に残す。全部決まり、ほかの blockers が無ければ ready:true。
ほかの計算（basis_hash・時間割・要点）はやり直さない。
"""

from __future__ import annotations

import re
from datetime import datetime

from rk import RkError
from rk.opsbase import Registry, load_all

UNPLACED_BLOCKER_PREFIX = "入らない訪問が"  # tools/leveling/plan.py と同じ言葉
CHOICES = {"pool": "保留プールへ戻す", "allow-over": "上限を超えて付ける（承知の上）",
           "shift": "時刻をずらす", "keep": "いまの担当のまま残す（承知の上）"}
OVER_CHECK_PREFIX = "△ 1 人の上限（承知の上で超過"
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def unresolved(plan: dict) -> list[dict]:
    done = {r.get("visit_id") for r in plan.get("resolutions") or []}
    return [u for u in plan.get("unplaced") or [] if u.get("visit_id") not in done]


def find_unplaced(plan: dict, key: str) -> dict:
    """visit_id の頭か、label の一部で 1 件を選ぶ。"""
    items = plan.get("unplaced") or []
    hits = [u for u in items if str(u.get("visit_id", "")).startswith(key)] or \
           [u for u in items if key and key in str(u.get("label", ""))]
    if not hits:
        raise RkError(f"入らない訪問に「{key}」はありません。候補: "
                      + "、".join(f"{u['label']}（{str(u['visit_id'])[:8]}）" for u in items) if items else
                      "この案には入らない訪問がありません")
    if len(hits) > 1:
        raise RkError(f"「{key}」に当たる訪問が {len(hits)} 件あります。visit id の頭で 1 件に絞ってください: "
                      + "、".join(f"{u['label']}（{str(u['visit_id'])[:8]}）" for u in hits))
    return hits[0]


def _pool_op(plan: dict, vid: str) -> dict | None:
    return next((o for o in plan["ops"] if o.get("op") == "return-to-pool" and o.get("visit_id") == vid), None)


def _new_ops(plan: dict, u: dict, choice: str, staff: str | None, start: str | None) -> list[dict]:
    vid = u["visit_id"]
    tag = {"resolution_of": vid, "step": 5}
    if choice == "pool":
        return []
    if choice == "keep":  # コースの担当が step 4 で替わっても動かないよう、いまの担当で留める
        if not u.get("staff_id"):
            return []
        return [{"op": "assign-staff-week", "visit_id": vid, "staff_id": u["staff_id"], "patient_id": u.get("patient_id"),
                 "date": u.get("date"), "label": f"{u['label']} はいまの担当のまま（承知の上）", **tag}]
    if choice == "allow-over":
        return [{"op": "assign-staff-week", "visit_id": vid, "staff_id": staff, "patient_id": u.get("patient_id"),
                 "date": u.get("date"), "label": f"{u['label']} → {staff}（上限を超えて付ける・承知の上）", **tag}]
    target = plan.get("target") or {}
    out = []
    if staff:
        out.append({"op": "assign-staff-week", "visit_id": vid, "staff_id": staff, "patient_id": u.get("patient_id"),
                    "date": u.get("date"), "label": f"{u['label']} → {staff}（時刻をずらす前に付け替える）", **tag})
    out.append({"op": "move-visit-week", "visit_id": vid, "patient_id": u.get("patient_id"), "date": u.get("date"),
                "iso_year": target.get("iso_year"), "iso_week": target.get("iso_week"),
                "old_weekday": u.get("weekday"), "old_start_time": f"{u['start_time']}:00",
                "new_weekday": u.get("weekday"), "new_start_time": f"{start}:00", "new_course_template_id": None,
                "label": f"{u['label']} → {start}（時刻をずらす）", **tag})
    return out


def _check_args(choice: str, staff: str | None, start: str | None) -> None:
    if choice not in CHOICES:
        raise RkError(f"--choice は {'・'.join(CHOICES)} のどれかです")
    if choice == "allow-over" and not staff:
        raise RkError("allow-over には --staff（付ける職員のコードか UUID）が要ります")
    if choice == "shift":
        if not start or not HHMM_RE.match(start):
            raise RkError("shift には --start HH:MM（例 13:30）が要ります")
    elif start:
        raise RkError("--start は shift のときだけ使います")
    if choice in ("pool", "keep") and staff:
        raise RkError(f"--staff は {choice} では使いません")


def resolve(plan: dict, choice: str, *, approved: str, key: str | None = None, all_: bool = False,
            staff: str | None = None, start: str | None = None, now: datetime | None = None,
            registry: Registry | None = None) -> list[dict]:
    """plan を書き換える。戻り値: 今回決めた resolutions。"""
    if not approved or len(approved.strip()) < 2:
        raise RkError('決めた人の記録 --approved "<誰が>" が要ります')
    _check_args(choice, staff, start)
    if not plan.get("unplaced"):
        raise RkError("この案には入らない訪問がありません（rk level で作った案だけが対象です）")
    if all_ == bool(key):
        raise RkError("--visit か --all のどちらか 1 つを指定してください")
    targets = unresolved(plan) if all_ else [find_unplaced(plan, key)]
    if not targets:
        raise RkError("入らない訪問はもう全部決めてあります（決め直すときは --visit で 1 件ずつ）")
    if all_ and choice == "shift" and len(targets) > 1:
        raise RkError("shift（時刻）は訪問ごとに違うので --all では使えません。--visit で 1 件ずつ決めてください")
    reg = registry or load_all()
    at = (now or datetime.now()).isoformat(timespec="seconds")
    made = []
    for u in targets:
        vid = u["visit_id"]
        new_ops = _new_ops(plan, u, choice, staff, start)
        errs = []
        for o in new_ops:
            impl = reg.get(o["op"])
            errs += [f"{o['label']}: {m}" for m in (impl.validate(o) if impl else [f"操作 {o['op']} がありません"])]
        if errs:
            raise RkError("決めた内容を操作にできません:\n  " + "\n  ".join(errs))
        # 決め直しに備えて、前に決めた分の操作を外す
        plan["ops"] = [o for o in plan["ops"] if o.get("resolution_of") != vid]
        pool = _pool_op(plan, vid)
        if pool is not None:
            u.setdefault("pool_op", dict(pool))
        if choice == "pool":
            if pool is None and u.get("pool_op"):
                at_i = next((i for i, o in enumerate(plan["ops"]) if (o.get("step") or 0) > 1), len(plan["ops"]))
                plan["ops"].insert(at_i, dict(u["pool_op"]))
        elif pool is not None:
            plan["ops"].remove(pool)
        plan["ops"].extend(new_ops)
        rec = {"visit_id": vid, "label": u["label"], "choice": choice, "choice_ja": CHOICES[choice],
               "staff": staff, "start": start, "approved": approved.strip(), "at": at}
        plan["resolutions"] = [r for r in plan.get("resolutions") or [] if r.get("visit_id") != vid] + [rec]
        made.append(rec)
    refresh(plan)
    return made


def resolution_warning(r: dict, u: dict | None) -> str:
    who = (u or {}).get("staff_name") or (u or {}).get("staff_code") or "いまの担当"
    if r["choice"] == "pool":
        return f"決めたこと: {r['label']} は保留プールへ戻す（API では戻せない・戻すにはバックアップから）"
    if r["choice"] == "allow-over":
        return f"承知の上で超過: {r['label']} を {r['staff']} へ付ける（{r['staff']} はこの日の上限を超えます）"
    if r["choice"] == "shift":
        return (f"決めたこと: {r['label']} を {r['start']} へずらす" + (f"（{r['staff']} へ付け替え）" if r.get("staff") else
                "（担当はコースの担当のまま）") + "。ご本人への連絡が要ります")
    return f"承知の上で残す: {r['label']} — {who} はお休みなのに訪問が残ります（当日までに担当を決めてください）"


def refresh(plan: dict) -> None:
    """resolutions から blockers・ready・warnings・checks を作り直す（ほかの計算はしない）。"""
    by_id = {u["visit_id"]: u for u in plan.get("unplaced") or []}
    res = plan.get("resolutions") or []
    left = unresolved(plan)
    blockers = [b for b in plan.get("blockers") or [] if not str(b).startswith(UNPLACED_BLOCKER_PREFIX)]
    if left:
        blockers.append(f"{UNPLACED_BLOCKER_PREFIX} {len(left)} 件あります（rk plan resolve で 1 件ずつ決めると反映できます）")
    plan["blockers"] = blockers
    plan["ready"] = not blockers
    plan["warnings"] = [w for w in plan.get("warnings") or []
                        if not str(w).startswith(("決めたこと:", "承知の上で超過:", "承知の上で残す:"))]
    plan["warnings"] += [resolution_warning(r, by_id.get(r["visit_id"])) for r in res]
    checks = [c for c in plan.get("checks") or [] if not str(c).startswith(OVER_CHECK_PREFIX)]
    over = [r for r in res if r["choice"] == "allow-over"]
    if over:
        checks.append(f"{OVER_CHECK_PREFIX} {len(over)} 件）: " + "／".join(f"{r['label']} → {r['staff']}" for r in over))
    if res:
        line = ("× " if left else "△ ") + "全部の訪問が入る（入らない訪問 " + f"{len(by_id)} 件のうち {len(res)} 件は決めた" + \
               (f"・{len(left)} 件は未定）" if left else "）")
        checks = [c for c in checks if not str(c).startswith(("× 全部の訪問が入る", "△ 全部の訪問が入る"))]
        checks.append(line)
    plan["checks"] = checks


def unresolved_lines(plan: dict) -> list[str]:
    return [f"未定: {u['label']}（{str(u['visit_id'])[:8]}）— {u.get('reason', '')}。{u.get('hint', '')}"
            for u in unresolved(plan)]
