"""案（plan.json）の読み込みと検査（設計書 §3-3）。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from rk import RkError
from rk.opsbase import IMPORT_ERRORS, Registry, load_all

REQUIRED_KEYS = ("kind", "title", "target", "summary", "ops", "checks", "warnings")


def load_plan(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise RkError(f"案のファイルがありません: {p}")
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise RkError(f"案の JSON を読めません: {e}") from None
    if not isinstance(plan, dict):
        raise RkError("案は JSON のオブジェクトです")
    return plan


def readiness_errors(plan: dict) -> list[str]:
    """"ready": false の案（rk level が「反映してはいけない」と判断した案）は反映しない。理由は blockers。"""
    if plan.get("ready") is not False:
        return []
    blockers = [str(b) for b in plan.get("blockers") or [] if str(b).strip()]
    out = ["この案は反映できません（作った道具が「反映してはいけない」と判断しました）"]
    out += [f"反映できない理由: {b}" for b in blockers] or ["反映できない理由: （blockers に書かれていません）"]
    return out


def check_plan(plan: dict, registry: Registry | None = None) -> list[str]:
    """誤りの一覧（空なら OK）。"""
    reg = registry or load_all()
    errs: list[str] = []
    for k in REQUIRED_KEYS:
        if k not in plan:
            errs.append(f"{k} がありません")
    if errs:
        return errs
    errs.extend(readiness_errors(plan))
    if not isinstance(plan["kind"], str) or not plan["kind"].strip():
        errs.append("kind は空でない文字列です")
    if not isinstance(plan["title"], str) or not plan["title"].strip():
        errs.append("title は空でない文字列です")
    if not isinstance(plan["target"], dict):
        errs.append("target はオブジェクトです")
    for k in ("summary", "checks", "warnings"):
        v = plan[k]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            errs.append(f"{k} は文字列のリストです")
    from rk.report_v2 import proposal_errors  # 任意の項目（headline・reason・issues・conditions・compare_to）は、書かれていれば形を見る
    errs.extend(proposal_errors(plan))
    ops = plan["ops"]
    if not isinstance(ops, list) or not ops:
        errs.append("ops は 1 件以上のリストです")
        return errs
    for i, op in enumerate(ops):
        where = f"ops[{i}]"
        if not isinstance(op, dict):
            errs.append(f"{where} はオブジェクトです")
            continue
        name = op.get("op")
        label = op.get("label")
        if isinstance(label, str) and label.strip():
            where = f"ops[{i}]（{label}）"
        else:
            errs.append(f"{where}: label がありません")
        if not isinstance(name, str) or not name:
            errs.append(f"{where}: op がありません")
            continue
        impl = reg.get(name)
        if impl is None:
            errs.append(f"{where}: 操作 {name} は許可リストにありません")
            continue
        try:
            for m in impl.validate(op) or []:
                errs.append(f"{where}: {m}")
        except Exception as e:  # 部品の誤りも検査の失敗として見せる
            errs.append(f"{where}: 検査で例外 {type(e).__name__}: {e}")
    if IMPORT_ERRORS:
        errs.extend(f"操作の部品を読み込めません: {m}" for m in IMPORT_ERRORS)
    # 同じ文が blockers にあれば readiness_errors が出している。ただしそれは "ready": false のときだけなので、
    # ready が false でない案（手でまとめた案・ready を true にした案）では必ず出す
    blockers = {str(b) for b in plan.get("blockers") or []} if plan.get("ready") is False else set()
    errs.extend(c for c in course_staff_conflicts(plan) if c not in blockers)
    errs.extend(out_of_days_errors(plan))
    return errs


# ---------------------------------------------------------------------------
# 日を絞った案（target.days）の操作が、その日の外に触れていないか（agent-period-build-design §3-2）
# ---------------------------------------------------------------------------
# 反映の前の確かめ（basis_hash）は target.days の日だけを見るので、範囲の外の日に触れる操作があると、
# その日の変化を見ないまま書いてしまう。日の分かる操作（date・曜日＋週・date_from〜date_to）を全部見る。
def _iso_day(op: dict, plan: dict, wd) -> str | None:
    t = plan.get("target") or {}
    y, w = op.get("iso_year", t.get("iso_year")), op.get("iso_week", t.get("iso_week"))
    try:
        return date.fromisocalendar(int(y), int(w), int(wd) + 1).isoformat()
    except (TypeError, ValueError):
        return None


def op_days(op: dict, plan: dict) -> set[str]:
    """操作が触れる日（分かるものだけ）。"""
    out: set[str] = set()
    if op.get("date"):
        out.add(str(op["date"])[:10])
    for k in ("weekday", "old_weekday", "new_weekday"):
        if isinstance(op.get(k), int) and not isinstance(op.get(k), bool):
            d = _iso_day(op, plan, op[k])
            if d:
                out.add(d)
    if op.get("date_from") and op.get("date_to"):
        try:
            d, end = date.fromisoformat(str(op["date_from"])[:10]), date.fromisoformat(str(op["date_to"])[:10])
        except ValueError:
            return out
        while d <= end and len(out) < 400:
            out.add(d.isoformat())
            d += timedelta(days=1)
    return out


def out_of_days_errors(plan: dict) -> list[str]:
    days = (plan.get("target") or {}).get("days") if isinstance(plan.get("target"), dict) else None
    if not isinstance(days, list) or not days:
        return []
    keep = {str(d)[:10] for d in days}
    shown = "・".join(sorted(keep))
    out = []
    for i, o in enumerate(plan.get("ops") or []):
        if not isinstance(o, dict):
            continue
        extra = sorted(op_days(o, plan) - keep)
        if extra:
            out.append(f"ops[{i}]（{o.get('label', '')}）: この案は {shown} だけを組む案ですが、"
                       f"範囲の外の日（{'・'.join(extra)}）に触れる操作があります")
    return out


# ---------------------------------------------------------------------------
# コースの担当（patch-course-staff）と、そのコースに残る訪問の組み合わせ（agent-period-build-design §3-4）
# ---------------------------------------------------------------------------
# 本体（backend/app/api/v1/courses.py update_course）は、コースの担当を付けるとき、そのコースに載っている
# 訪問（削除・取消を除く・手の上書きや打刻済みも含む）の利用者すべてと新しい担当を比べ、NG の職員・性別の限定に
# 触れると 422（constraint_confirmation_required）で断る。新人は必ず 422。
# 案の操作を順に追い、各 patch-course-staff の時点でコースに残る訪問を求めて、本体と同じ判定をする。
# 材料は op["course_guard"]（rk level の build_plan が案を作った時の週から書く）:
#   patch-course-staff: {"staff": {id, name, sex, is_trainee, full_day_off}, "visits": [{visit_id, patient_id, start_time, label,
#                        sex_restriction, ng_staff_ids}]}   … 案を作った時点でそのコースに載っている訪問
#   move-visit-week・special-visit-place: {"patient": {sex_restriction, ng_staff_ids, label}}   … 入ってくる方
# 材料の無い patch-course-staff（古い案・手で書いた操作）は見ない。手でまとめた案（月の案など）でも、
# 操作をそのまま写していれば同じように見られる。
COURSE_GUARD_PREFIX = "コースの担当を付けると本体が断る組み合わせ"


def _sex_ng(info: dict, staff: dict) -> list[str]:
    out = []
    sid = staff.get("id")
    if sid and sid in (info.get("ng_staff_ids") or []):
        out.append("NG の職員")
    r = info.get("sex_restriction")
    sex = staff.get("sex")
    if r and sex and str(r).removesuffix("_only") != sex:  # 性別が分からない職員は本体も警告しない
        out.append("女性限定" if str(r).startswith("female") else "男性限定" if str(r).startswith("male") else f"性別の限定（{r}）")
    return out


def _course_key(op: dict, course: str | None = None, day: str | None = None) -> tuple | None:
    c = course if course is not None else op.get("course")
    d = day if day is not None else op.get("date")
    if c and d:
        return (str(d)[:10], str(c))
    if op.get("course_id") and course is None:
        return ("id", str(op["course_id"]))
    return None


def _new_day(op: dict) -> str | None:
    try:
        return (date.fromisocalendar(int(op["iso_year"]), int(op["iso_week"]), 1)
                + timedelta(days=int(op["new_weekday"]))).isoformat()
    except (KeyError, TypeError, ValueError):
        return op.get("date")


def _walk_courses(plan: dict):
    """案の操作を順に追い、patch-course-staff（担当あり・材料あり）ごとに (i, op, そのときコースに載っている訪問) を返す。
    最後に (None, None, 最後のコースの中身 {コース: [訪問]}) を返す。"""
    ops = [o for o in plan.get("ops") or [] if isinstance(o, dict)]
    members: dict[tuple, dict[str, dict]] = {}  # コース -> 訪問のキー -> 材料
    for o in ops:  # 案を作った時点のコースの中身（最初に出てきた patch の材料）
        g = o.get("course_guard") if o.get("op") == "patch-course-staff" else None
        k = _course_key(o)
        if isinstance(g, dict) and k and k not in members:
            members[k] = {str(v.get("visit_id") or f"{v.get('patient_id')}|{v.get('start_time')}"): dict(v)
                          for v in g.get("visits") or [] if isinstance(v, dict)}

    def take(visit_id, patient_id=None, day=None, start=None) -> tuple[tuple | None, dict | None]:
        for k, vs in members.items():
            for key, v in list(vs.items()):
                hit = (visit_id and str(v.get("visit_id")) == str(visit_id)) or (
                    not visit_id and patient_id and str(v.get("patient_id")) == str(patient_id)
                    and k[0] == str(day)[:10] and str(v.get("start_time") or "")[:5] == str(start or "")[:5])
                if hit:
                    return k, vs.pop(key)
        return None, None

    for i, o in enumerate(ops):
        name = o.get("op")
        if name in ("return-to-pool", "cancel-visit-week"):
            if name == "cancel-visit-week" and o.get("cancel") is False:
                continue
            take(o.get("visit_id"), o.get("patient_id"), o.get("date"), o.get("start_time"))
        elif name == "move-visit-week":
            was, got = take(o.get("visit_id"), o.get("patient_id"), o.get("date"), o.get("old_start_time"))
            info = got or dict(((o.get("course_guard") or {}).get("patient") or {}), patient_id=o.get("patient_id"))
            if o.get("new_course_template_id"):  # 別のコースへ
                to, day = o.get("to_course"), _new_day(o)
                k = _course_key(o, to, day) if to and day else None
            else:  # コースは据え置き。曜日を変えても本体は course_id を変えない（schedule_v2.py
                # _apply_visit_move_week_only）ので、本体の判定（course_id で絞る）と同じく元のコースに残す
                k = was
            if k in members:
                info = {**info, "visit_id": o.get("visit_id") or info.get("visit_id"),
                        "start_time": str(o.get("new_start_time") or "")[:5]}
                members[k][str(info.get("visit_id") or f"{o.get('patient_id')}|{info['start_time']}")] = info
        elif name == "special-visit-place":
            k = _course_key(o)
            if k in members:
                info = dict(((o.get("course_guard") or {}).get("patient") or {}), patient_id=o.get("patient_id"),
                            start_time=str(o.get("start_time") or "")[:5])
                members[k][f"{o.get('patient_id')}|{info['start_time']}"] = info
        elif name == "patch-course-staff":
            k = _course_key(o)
            if isinstance(o.get("course_guard"), dict) and k in members and o.get("staff_id") is not None:
                yield i, o, sorted(members[k].values(), key=lambda v: str(v.get("start_time") or ""))
    yield None, None, members


def course_staff_conflicts(plan: dict) -> list[str]:
    """反映の前に分かる「コースの担当を付けると本体が 422 で断る」組（空なら無し）。"""
    out = []
    for i, o, visits in _walk_courses(plan):
        if o is None:
            continue
        staff = o["course_guard"].get("staff") or {}
        who = staff.get("name") or str(o.get("staff_id"))
        head = f"{COURSE_GUARD_PREFIX}: ops[{i}]（{o.get('label', '')}）— "
        if str(staff.get("id")) != str(o.get("staff_id")):
            out.append(f"{head}担当（staff_id）が、案を作った時の材料（{who}）と違います。材料が古いので判定できません。"
                       "担当を変えるときは rk level で案を作り直してください")
            continue
        # 新人と終日の休みは、了承の印があっても本体が断る（courses.py update_course）
        if staff.get("is_trainee"):
            out.append(f"{head}{who} は新人です（コースの担当にできません）")
            continue
        if staff.get("full_day_off"):
            out.append(f"{head}{who} はこの日終日の休みです（コースの担当にできません）")
            continue
        if o.get("acknowledge_constraint_warnings"):
            continue  # 人が了承して通すと書いた操作（本体は NG・性別の限定だけ通す）
        for v in visits:
            why = _sex_ng(v, staff)
            if why:
                out.append(f"{COURSE_GUARD_PREFIX}: ops[{i}]（{o.get('label', '')}）— このコースに残る "
                           f"{v.get('label') or v.get('patient_id')} は{'・'.join(why)}です。"
                           "入らない訪問の決め方（rk plan resolve）を変えるか、その訪問を先に別のコースへ移すと反映できます")
    return out


KEPT_UNASSIGNED_PREFIX = "担当なしで残す訪問:"


def kept_unassigned_notes(plan: dict) -> list[str]:
    """入らない訪問を「いまのまま残す（keep）」と決めたのに、自分の担当が無い（コースの担当に従う）訪問が、
    担当を付けるコースに残る組。本体はコースの担当を、手の上書きの無い予定へ写す（courses.py）ので、
    その訪問はコースの担当の訪問として表示される。担当なしのまま保つ操作は、画面と同じ API には無い
    （visit-assign-staff-week の staff_id=null は手の上書きを外す）ので、知らせるだけにする。"""
    keep = {r.get("visit_id") for r in plan.get("resolutions") or [] if r.get("choice") == "keep"}
    if not keep:
        return []
    own = {o.get("visit_id") for o in plan.get("ops") or []
           if isinstance(o, dict) and o.get("op") == "assign-staff-week" and o.get("staff_id") is not None}
    out = []
    for _i, o, visits in _walk_courses(plan):
        if o is None:
            continue
        who = (o["course_guard"].get("staff") or {}).get("name") or str(o.get("staff_id"))
        for v in visits:
            vid = v.get("visit_id")
            if vid in keep and vid not in own and not v.get("own_staff"):
                out.append(f"{KEPT_UNASSIGNED_PREFIX} {v.get('label') or vid} は担当なしで残す決めですが、"
                           f"コース（{o.get('course') or ''}）に {who} が付くと、{who} の訪問として表示されます。"
                           "当日までに担当を決めるか、別の決め方を選んでください")
    return out


def not_undoable_ops(plan: dict, registry: Registry | None = None) -> list[int]:
    reg = registry or load_all()
    out = []
    for i, op in enumerate(plan.get("ops") or []):
        impl = reg.get(op.get("op", "")) if isinstance(op, dict) else None
        if impl is not None and impl.undo_note(op) is not None:
            out.append(i)
    return out
