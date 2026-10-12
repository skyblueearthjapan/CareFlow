"""下書きの出口 2 つ.

① 固定訪問（型）に登録: 患者ごとの PFV を、患者マスタの PUT と **同じ関数** (apply_put_fixed_visits) で
   1 トランザクションに入れる。次に「週を生成」した週から反映される。写した後にマスターが変わっていたら断る。
② この週のコースに入れる: 選んだ ISO 週 1 つだけ。型は変えない。週画面と同じ経路
   (_apply_visit_move_week_only / コース解決 / 訪問の追加) で、1 トランザクション・op-log 1 グループ
   (アプリの「戻る」で 1 回で戻る)。やる前に必ず dry-run (件数と理由) を見せ、週が変わっていたら断る。
"""

# ruff: noqa: N812, N806, N803, N818, B905

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.course import Course
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.schemas.v2.patient_fixed_visit import PatientFixedVisitsBulkPut

from . import engine as E
from . import model as M
from . import problems as P
from . import raw as R
from . import service as S
from .errors import TemplateError


def _t(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ---------------------------------------------------------------------------
# ① 型に登録
# ---------------------------------------------------------------------------


async def _change_rows(db: AsyncSession, row, raw: dict | None = None):
    raw = raw or await R.build_raw(db)
    parts = await S.engine_parts(db, row, raw)
    problems = [(ed, p) for ed in parts for p in P.build_week(ed)]
    chg_all, virt = [], 0
    for ed, p in problems:
        mine = E.draft_assign(ed, p)
        chg = M.changes(p, mine)
        for c in chg:
            s = next(x for x in p["slots"] if x["key"] == c["key"])
            c["dur"] = s["dur"]
            c["code"] = s.get("patient_code")
        chg_all += chg
        virtual = {c["id"] for c in p["courses"] if c.get("virtual")}
        virt += sum(1 for s in p["slots"] if (mine.get(s["key"]) or {}).get("course") in virtual)
    return parts[0], [p for _e, p in problems], chg_all, virt


async def commit_preview(db: AsyncSession, row) -> dict:
    """マスターに登録したときに変わるものの一覧 (dry-run). 何も書かない."""
    raw = await R.build_raw(db)
    ed, problems, chg, virt = await _change_rows(db, row, raw)
    labels = {c["id"]: c["label"] for p in problems for c in p["courses"]}
    by_patient = defaultdict(list)
    for c in chg:
        by_patient[c["patient_id"]].append(c)
    patients = []
    for pid, cs in sorted(by_patient.items(), key=lambda kv: kv[1][0]["patient"]):
        patients.append({
            "patient_id": pid, "name": cs[0]["patient"], "code": cs[0].get("code"),
            "changes": [{
                "weekday": M.WD[c["weekday"]], "new": bool(c.get("new")), "locked": bool(c["locked"]),
                "from_course": c["from_label"], "to_course": labels.get(c["to_course"], c["to_label"]),
                "from_start": M.fmt(c["from_start"]), "to_start": M.fmt(c["to_start"]),
                "course_changed": bool(c["course_changed"]), "time_changed": bool(c["time_changed"]),
            } for c in sorted(cs, key=lambda c: (c["weekday"], c["slot_index"]))],
        })
    n_new = sum(1 for c in chg if c.get("new"))
    blockers = []
    scope = (row.params or {}).get("scope") or []
    now = await R.current_master_hashes(db, scope)
    moved = [pid for pid in scope if now.get(pid) != ((row.params or {}).get("basis") or {}).get(pid)]
    if moved:
        names = {p["id"]: p["name"] for p in raw["patients"]} | {c["id"]: c["name"] for c in raw["candidates"]}
        blockers.append(f"この下書きを作った後に、固定訪問の枠が変わった方がいます（{len(moved)} 名: "
                        f"{'・'.join(M.short_name(names.get(m, '')) + '様' for m in moved[:6])}"
                        f"{'…' if len(moved) > 6 else ''}）。いまの型から下書きを作り直してください")
    red_time = [c for c in chg if c["locked"] and c["time_changed"]]
    if red_time:
        blockers.append(f"赤ピンの枠の時刻を変える枠が {len(red_time)} 件あります（赤を外した試算）。赤の時刻は登録できません")
    if virt:
        blockers.append(f"仮の職員のコースに入れた枠が {virt} 件あります（もしもの職員の数）。本当の人数で最適化し直してください")
    sc = ed["scenario"]
    if sc.get("prefs"):
        blockers.append("希望の時間帯をもしもで仮に変えた下書きです。希望は患者マスタで先に合わせてから、下書きを作り直してください")
    if row.status != "draft":
        blockers.append("この下書きは登録済み／しまってあります")
    if not chg:
        blockers.append("いまの型と違う枠がありません（登録するものがありません）")
    warnings = []
    if sc.get("staff_count") or sc.get("drop_staff"):
        warnings.append("職員の人数・外した職員のもしもで作った型です。本当にその人数で回すときだけ登録してください")
    return {
        "draft_id": str(row.id), "version": row.version, "ready": not blockers, "blockers": blockers, "warnings": warnings,
        "counts": {"patients": len(patients), "slots": len(chg) - n_new,
                   "course": sum(1 for c in chg if c["course_changed"] and not c.get("new")),
                   "time": sum(1 for c in chg if c["time_changed"] and not c.get("new")), "new": n_new},
        "patients": patients,
        "note": "登録すると、次に「週を生成」した週から反映されます。すでにある週の予定は変わりません。",
    }


async def commit_to_master(db: AsyncSession, row, user: User, *, expected_version: int | None) -> dict:
    """①: 下書き → 患者ごとの固定訪問 (PUT と同じ関数・1 トランザクション)。"""
    from app.api.v1.patient_fixed_visits import apply_put_fixed_visits

    S.check_version(row, expected_version)
    raw = await R.build_raw(db)
    prev = await commit_preview(db, row)
    if not prev["ready"]:
        raise TemplateError("まだ登録できません: " + prev["blockers"][0])
    ed, problems, chg, _ = await _change_rows(db, row, raw)
    by_patient = defaultdict(list)
    for c in chg:
        by_patient[c["patient_id"]].append(c)
    audit_after = []
    try:
        for pid, cs in by_patient.items():
            rows = (await db.scalars(select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == UUID(pid), PatientFixedVisit.mode == "normal"))).all()
            items = {}
            for r in rows:
                items[(r.weekday, r.slot_index)] = {
                    "weekday": r.weekday, "start_time": r.start_time, "duration_min": r.duration_min,
                    "course_template_id": r.course_template_id, "sub_office_id": r.sub_office_id,
                    "slot_index": r.slot_index, "is_pinned": r.movability == "locked", "movability": r.movability}
            for c in cs:
                k = (c["weekday"], c["slot_index"])
                if c.get("new"):
                    items[k] = {"weekday": c["weekday"], "start_time": S.to_time(c["to_start"]), "duration_min": c["dur"],
                                "course_template_id": UUID(c["to_course"]) if c["to_course"] else None,
                                "sub_office_id": None, "slot_index": c["slot_index"], "is_pinned": False,
                                "movability": "unknown"}
                elif k in items:
                    items[k]["course_template_id"] = UUID(c["to_course"]) if c["to_course"] else None
                    items[k]["start_time"] = S.to_time(c["to_start"])
            body = PatientFixedVisitsBulkPut.model_validate({
                "mode": "normal", "change_scope": "pattern_only",
                "items": [items[k] for k in sorted(items)]})
            try:
                await apply_put_fixed_visits(db, UUID(pid), body, user, commit=False)
            except HTTPException as e:
                d = e.detail
                msg = d.get("message") if isinstance(d, dict) else str(d)
                raise TemplateError(f"{cs[0]['patient']}様の登録でマスターの決まりに止められました: {msg}") from e
            audit_after.append({"patient_id": pid, "slots": len(cs)})
        row.status = "committed"
        row.committed_at = func_now()
        db.add(AuditLog(actor_user_id=user.id, action="template_draft_commit", target_table="template_drafts",
                        target_id=str(row.id), before=None,
                        after={"name": row.name, "patients": audit_after, "counts": prev["counts"]}))
        await S.add_step(db, row, actor="human", user=user, kind="commit",
                         reason=f"固定訪問（型）に登録しました（{len(by_patient)} 名）", changes=[])
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"committed": True, "counts": prev["counts"], "patients": len(by_patient),
            "note": "次に「週を生成」した週から反映されます"}


def func_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# ② この週のコースに入れる
# ---------------------------------------------------------------------------


async def _week_state(db: AsyncSession, row, iso_year: int, iso_week: int):
    try:
        monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError:
        raise TemplateError("週の指定が正しくありません") from None
    scope = [UUID(p) for p in ((row.params or {}).get("scope") or [])]
    scope_set = set(scope)
    # 足した方(まだ枠の無い方)も対象
    for s in await S.slot_rows(db, row.id):
        scope_set.add(s.patient_id)
    visits = list((await db.scalars(select(Visit).where(
        Visit.patient_id.in_(scope_set), Visit.deleted_at.is_(None),
        Visit.visit_date >= monday, Visit.visit_date <= monday + timedelta(days=6)))).all())
    return monday, scope_set, visits


async def week_preview(db: AsyncSession, row, iso_year: int, iso_week: int) -> dict:
    from app.services.constraint_override_notify import resolve_week_course_for_template

    monday, scope_set, visits = await _week_state(db, row, iso_year, iso_week)
    if not any(v.status == "planned" for v in visits):
        raise TemplateError("その週はまだ作られていません。先に「週を生成」するか、前の週をコピーしてください")
    raw = await R.build_raw(db)
    parts = await S.engine_parts(db, row, raw)
    labels = {c["id"]: c["label"] for ed in parts for p in P.build_week(ed) for c in p["courses"]}
    olabel = {o["id"]: o["short_label"] for o in raw["offices"]}
    labels |= {t["id"]: f"{olabel.get(t['office_id'], '')}{t['label']}" for t in raw["templates"]}
    names = {p["id"]: p["name"] for p in raw["patients"]} | {c["id"]: c["name"] for c in raw["candidates"]}
    course_rows = {c.id: c for c in (await db.scalars(select(Course).where(
        Course.id.in_({v.course_id for v in visits if v.course_id})))).all()} if visits else {}
    by = defaultdict(list)
    for v in visits:
        by[(v.patient_id, v.visit_date.weekday())].append(v)
    multi_staff: dict = {}
    if visits:
        for vid, _sid in (await db.execute(select(VisitStaffAssignment.visit_id, VisitStaffAssignment.staff_id).where(
                VisitStaffAssignment.visit_id.in_([v.id for v in visits])))).all():
            multi_staff[vid] = multi_staff.get(vid, 0) + 1
    slots = [r for r in await S.slot_rows(db, row.id) if not (r.source == "new" and r.course_template_id is None)]
    two = defaultdict(int)
    for r in slots:
        two[(r.patient_id, r.weekday)] += 1
    actions, skipped = [], []
    course_notes: dict[tuple, str] = {}
    for r in sorted(slots, key=lambda r: (r.weekday, from_t(r.start_time))):
        pname = names.get(str(r.patient_id), str(r.patient_id))
        who = {"patient_id": str(r.patient_id), "patient": pname, "weekday": M.WD[r.weekday]}
        if r.course_template_id is None:
            skipped.append({**who, "reason": "コースが決まっていない枠です"})
            continue
        if two[(r.patient_id, r.weekday)] > 1:
            skipped.append({**who, "reason": "2 名体制の枠は、まだここからは入れられません（週の画面で）"})
            continue
        vs = by.get((r.patient_id, r.weekday), [])
        key = (str(r.course_template_id), r.weekday)
        if key not in course_notes:
            c = await resolve_week_course_for_template(db, course_template_id=r.course_template_id,
                                                       iso_year=iso_year, iso_week=iso_week, weekday=r.weekday)
            course_notes[key] = ("create" if c is None else ("nostaff" if c.assigned_staff_id is None else "ok"))
        start = from_t(r.start_time)
        if r.source == "new":
            if vs:
                skipped.append({**who, "reason": "その日にもう訪問があります（足しません）"})
            else:
                actions.append({**who, "kind": "add", "to_course": str(r.course_template_id), "to_start": start,
                                "dur": r.duration_min, "course_label": labels.get(str(r.course_template_id))})
            continue
        if not vs:
            skipped.append({**who, "reason": "この週にこの方の訪問がありません"})
            continue
        if len(vs) > 1:
            skipped.append({**who, "reason": "同じ日に訪問が複数あります"})
            continue
        v = vs[0]
        if multi_staff.get(v.id, 0) >= 2:
            skipped.append({**who, "reason": "担当が 2 名以上いる訪問です（週の画面で）"})
            continue
        if v.status != "planned":
            skipped.append({**who, "reason": "予定ではない訪問です（打刻済み・取消など）"})
            continue
        cur_tpl = course_rows[v.course_id].template_id if v.course_id in course_rows else None
        cur_start = v.start_time.hour * 60 + v.start_time.minute
        course_changed = cur_tpl != r.course_template_id
        time_changed = cur_start != start
        if course_changed or time_changed:
            actions.append({**who, "kind": "move", "visit_id": str(v.id), "from_start": cur_start, "to_start": start,
                            "from_course": labels.get(str(cur_tpl), "（コースなし）"), "to_course": str(r.course_template_id),
                            "course_label": labels.get(str(r.course_template_id)),
                            "course_changed": course_changed, "time_changed": time_changed})
    notes = []
    for (tpl, wd), st in sorted(course_notes.items()):
        if st == "create":
            notes.append({"course": labels.get(tpl), "weekday": M.WD[wd], "note": "この週にコースを作ります（担当は決まっていません）"})
        elif st == "nostaff":
            notes.append({"course": labels.get(tpl), "weekday": M.WD[wd], "note": "この週のコースに担当がいないので、担当なしのまま入れます"})
    sig = hashlib.sha256(json.dumps(sorted(
        (str(v.id), v.visit_date.isoformat(), v.start_time.strftime("%H:%M"), str(v.course_id), v.status)
        for v in visits)).encode()).hexdigest()[:16]
    cnt = {
        "course": sum(1 for a in actions if a["kind"] == "move" and a["course_changed"]),
        "time": sum(1 for a in actions if a["kind"] == "move" and a["time_changed"]),
        "add": sum(1 for a in actions if a["kind"] == "add"),
        "skipped": len(skipped),
    }
    return {"iso_year": iso_year, "iso_week": iso_week, "week_hash": sig, "counts": cnt, "actions": actions,
            "skipped": skipped, "course_notes": notes, "draft_version": row.version,
            "note": "この週だけです。型（固定訪問）は変わりません。アプリの「戻る」で元に戻せます"}


def from_t(t) -> int:
    return t.hour * 60 + t.minute


async def week_apply(db: AsyncSession, row, user: User, *, iso_year: int, iso_week: int, expected_week_hash: str,
                     expected_version: int | None) -> dict:
    from app.api.v1.schedule import _get_or_create_course_for_template_week
    from app.api.v1.schedule_v2 import _apply_visit_move_week_only
    from app.models.visit import VISIT_SOURCE_MANUAL_WEEK
    from app.services.op_log_service import fmt_time, fmt_weekday, record_op

    if expected_version is not None and expected_version != row.version:
        raise S.TemplateConflict("下書きが変わりました。もう一度、内容を確かめてください")
    prev = await week_preview(db, row, iso_year, iso_week)
    if prev["week_hash"] != expected_week_hash:
        raise S.TemplateConflict("確かめたあとに、この週の訪問が変わりました。もう一度、内容を確かめてください")
    group = uuid4()
    first = True
    done = {"course": 0, "time": 0, "add": 0}
    try:
        for a in prev["actions"]:
            pid = UUID(a["patient_id"])
            wd = list(M.WD).index(a["weekday"])
            to_start = S.to_time(a["to_start"])
            if a["kind"] == "move":
                v = await db.scalar(select(Visit).where(Visit.id == UUID(a["visit_id"])))
                old = (v.course_id, v.primary_staff_id, bool(v.manual_staff_override))
                counters = {"visits": 0, "patients": set()}
                await _apply_visit_move_week_only(
                    db, patient_id=pid, old_weekday=wd, old_start=S.to_time(a["from_start"]), new_weekday=wd,
                    new_start=to_start, new_course=UUID(a["to_course"]) if a["course_changed"] else None,
                    iso_year=iso_year, iso_week=iso_week, counters=counters)
                await db.flush()
                label = f"{a['patient']}様 {a['weekday']}曜（型の作業場から）"
                if a["time_changed"]:
                    await record_op(db, user_id=user.id, iso_year=iso_year, iso_week=iso_week, op_group_id=group,
                                    op_kind="move_visit_week_only", label=f"{label} {_t(a['from_start'])}→{_t(a['to_start'])}",
                                    forward_payload={"op": "move_visit_week_only", "patient_id": str(pid), "iso_year": iso_year,
                                                     "iso_week": iso_week, "from_weekday": wd, "from_start": fmt_time(S.to_time(a["from_start"])),
                                                     "to_weekday": wd, "to_start": fmt_time(to_start)},
                                    inverse_payload={"op": "move_visit_week_only", "patient_id": str(pid), "iso_year": iso_year,
                                                     "iso_week": iso_week, "from_weekday": wd, "from_start": fmt_time(to_start),
                                                     "to_weekday": wd, "to_start": fmt_time(S.to_time(a["from_start"]))},
                                    strict=True, clear_redo=first)
                    first = False
                    done["time"] += 1
                if a["course_changed"]:
                    await db.refresh(v)
                    await record_op(db, user_id=user.id, iso_year=iso_year, iso_week=iso_week, op_group_id=group,
                                    op_kind="set_visit_course", label=f"{label} {a['from_course']}→{a['course_label']}",
                                    forward_payload={"op": "set_visit_course", "visit_id": str(v.id),
                                                     "course_id": str(v.course_id) if v.course_id else None,
                                                     "staff_id": str(v.primary_staff_id) if v.primary_staff_id else None, "manual": False},
                                    inverse_payload={"op": "set_visit_course", "visit_id": str(v.id),
                                                     "course_id": str(old[0]) if old[0] else None,
                                                     "staff_id": str(old[1]) if old[1] else None, "manual": old[2]},
                                    strict=True, clear_redo=first)
                    first = False
                    done["course"] += 1
            else:
                course = await _get_or_create_course_for_template_week(
                    db, course_template_id=UUID(a["to_course"]), iso_year=iso_year, iso_week=iso_week, weekday=wd)
                end = a["to_start"] + a["dur"]
                v = Visit(patient_id=pid, primary_staff_id=course.assigned_staff_id,
                          visit_date=date.fromisocalendar(iso_year, iso_week, wd + 1), start_time=to_start,
                          end_time=S.to_time(min(end, 23 * 60 + 59)), type="regular", status="planned",
                          source=VISIT_SOURCE_MANUAL_WEEK, course_id=course.id)
                db.add(v)
                await db.flush()
                if course.assigned_staff_id is not None:
                    db.add(VisitStaffAssignment(visit_id=v.id, staff_id=course.assigned_staff_id))
                await record_op(db, user_id=user.id, iso_year=iso_year, iso_week=iso_week, op_group_id=group,
                                op_kind="restore_visits", label=f"{a['patient']}様 {fmt_weekday(wd)}曜を足す（型の作業場から）",
                                forward_payload={"op": "restore_visits", "visit_ids": [str(v.id)]},
                                inverse_payload={"op": "soft_delete_visits", "visit_ids": [str(v.id)]},
                                strict=True, clear_redo=first)
                first = False
                done["add"] += 1
        db.add(AuditLog(actor_user_id=user.id, action="template_draft_apply_week", target_table="template_drafts",
                        target_id=str(row.id), before=None,
                        after={"iso_year": iso_year, "iso_week": iso_week, "done": done, "skipped": len(prev["skipped"])}))
        await S.add_step(db, row, actor="human", user=user, kind="commit",
                         reason=f"{iso_year}年第{iso_week}週のコースに入れました", changes=[])
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"applied": True, "done": done, "skipped": prev["skipped"], "op_group_id": str(group),
            "note": "アプリの「戻る」で元に戻せます"}
