"""型の作業場 (template workspace) の下書きサービス: 作る・読む・動かす・もしも・取り消す/やり直す・比べる.

下書きへの変更はすべてここを通り、編集の履歴 (``template_draft_steps``) に残る。
actor = ``human`` (あなた) | ``agent`` (らく助エージェント)。下書きの編集に承認は要らない
(マスターへ登録するときだけ commit.py の確認が要る)。
変更は ``expected_version`` を送り、下書きの ``version`` と違えば ``TemplateConflict`` (API では 409)。
"""

# ruff: noqa: N812, N806, N803, N818, B905

from __future__ import annotations

import copy
from datetime import time
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.template_draft import TemplateDraft, TemplateDraftSlot, TemplateDraftStep
from app.models.user import User

from . import engine as E
from . import model as M
from . import problems as P
from . import raw as R
from .errors import TemplateError

FEATURE_KEY = "template_workspace"
ACTORS = ("human", "agent")
KINDS = ("move", "optimise", "whatif", "add", "remove")


class TemplateConflict(Exception):
    """下書きが他で変わった (version 違い) / 進行中の計算がある / 登録済み."""


class TemplateNotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# 小さな部品
# ---------------------------------------------------------------------------


async def flag_enabled(db: AsyncSession, office_id) -> bool:
    row = await db.scalar(
        select(OfficeFeatureFlag).where(
            OfficeFeatureFlag.office_id == office_id, OfficeFeatureFlag.feature_key == FEATURE_KEY
        )
    )
    return bool(row and row.enabled_at is not None)


def parse_key(key: str) -> tuple[UUID, int, int, bool]:
    parts = key.split(":")
    is_new = parts[0] == "new"
    if is_new:
        parts = parts[1:]
    return UUID(parts[0]), int(parts[1]), int(parts[2]), is_new


def to_time(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)


def from_time(t: time) -> int:
    return t.hour * 60 + t.minute


def row_key(r: TemplateDraftSlot) -> str:
    return (R.new_slot_key if r.source == "new" else R.slot_key)(r.patient_id, r.weekday, r.slot_index)


def slot_state(r: TemplateDraftSlot | None) -> dict | None:
    if r is None:
        return None
    return {
        "placed": r.course_template_id is not None or r.source != "new",
        "course": str(r.course_template_id) if r.course_template_id else None,
        "start": from_time(r.start_time),
        "source": r.source,
        "reason": r.reason,
    }


async def get_draft(db: AsyncSession, draft_id: UUID, *, for_update: bool = False) -> TemplateDraft:
    stmt = select(TemplateDraft).where(TemplateDraft.id == draft_id)
    if for_update:
        stmt = stmt.with_for_update()
    row = await db.scalar(stmt)
    if row is None:
        raise TemplateNotFound("下書きが見つかりません")
    return row


def check_version(row: TemplateDraft, expected: int | None) -> None:
    if expected is None or expected != row.version:
        raise TemplateConflict(
            f"下書きが他で変わりました（いま {row.version} 版・あなたの画面は {expected} 版）。読み直してからもう一度どうぞ"
        )
    if row.status != "draft":
        raise TemplateConflict("この下書きは登録済み／しまってあるので、変えられません")


async def slot_rows(db: AsyncSession, draft_id: UUID) -> list[TemplateDraftSlot]:
    return list((await db.scalars(select(TemplateDraftSlot).where(TemplateDraftSlot.draft_id == draft_id))).all())


async def engine_draft(db: AsyncSession, row: TemplateDraft, raw: dict | None = None) -> dict:
    """DB の下書き → 計算用の dict (材料は今のマスターから読み直す)。"""
    raw = raw or await R.build_raw(db)
    ofc = next((o for o in raw["offices"] if o["id"] == str(row.office_id)), None)
    if ofc is None:
        raise TemplateError("下書きの拠点が見つかりません")
    rows = await slot_rows(db, row.id)
    assign, held, unplaced = {}, [], {}
    for r in rows:
        k = row_key(r)
        if r.source == "new" and r.course_template_id is None:
            unplaced[k] = r.reason or "入りきらない"
            continue
        assign[k] = {"course": str(r.course_template_id) if r.course_template_id else None,
                     "start": from_time(r.start_time)}
        if r.source == "manual":
            held.append(k)
    p = row.params or {}
    return E.make_draft(ofc["short_label"], ofc["id"], raw, p.get("scope") or [], p.get("scenario") or {},
                        assign, held, unplaced)


# ---------------------------------------------------------------------------
# 作る・一覧
# ---------------------------------------------------------------------------


async def create_draft(db: AsyncSession, user: User, office_id: UUID, name: str,
                       from_draft_id: UUID | None = None) -> TemplateDraft:
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise TemplateError("下書きの名前は 1〜120 文字で入れてください")
    raw = await R.build_raw(db)
    ofc = next((o for o in raw["offices"] if o["id"] == str(office_id)), None)
    if ofc is None:
        raise TemplateError("拠点が見つかりません")
    scope = P.scope_patients(raw, ofc["short_label"])
    basis = R.master_hashes(raw["pfv"], scope)
    scenario = E.default_scenario()
    src = None
    origin = None
    same: set[str] = set()  # 元の下書きを作った後もマスターが変わっていない患者（その方の枠だけ元の下書きから写す）
    if from_draft_id:
        src = await get_draft(db, from_draft_id)
        if src.office_id != office_id:
            raise TemplateError("別の拠点の下書きからは写せません")
        scenario = copy.deepcopy((src.params or {}).get("scenario") or scenario)
        old_basis = (src.params or {}).get("basis") or {}
        same = {pid for pid, h in basis.items() if old_basis.get(pid) == h}
        origin = src.id
    row = TemplateDraft(
        id=uuid4(), office_id=office_id, name=name, base_snapshot_hash=R.combined_hash(basis),
        origin_draft_id=origin, created_by=user.id, status="draft", version=1,
        params={"scenario": scenario, "scope": scope, "basis": basis}, metrics={},
    )
    db.add(row)
    await db.flush()
    # まず今のマスターから全部作り、元の下書きの枠は「マスターが変わっていない患者」の分だけ上書きする
    ed = E.make_draft(ofc["short_label"], ofc["id"], raw, scope, scenario, {}, [], {})
    made: dict[str, TemplateDraftSlot] = {}
    for p in P.build_week(ed):
        for s in p["slots"]:
            if s.get("new"):
                continue
            made[s["key"]] = TemplateDraftSlot(
                draft_id=row.id, patient_id=UUID(s["patient_id"]), weekday=p["weekday"],
                slot_index=s["slot_index"], start_time=to_time(s["start"]), duration_min=s["dur"],
                course_template_id=UUID(s["cur_course"]) if s["cur_course"] else None,
                source="master", locked=bool(s["locked"]), changed=False, reason=None)
    if src is not None:
        for r in await slot_rows(db, src.id):
            if str(r.patient_id) not in same:
                continue
            made[row_key(r)] = TemplateDraftSlot(
                draft_id=row.id, patient_id=r.patient_id, weekday=r.weekday, slot_index=r.slot_index,
                start_time=r.start_time, duration_min=r.duration_min, course_template_id=r.course_template_id,
                source=r.source, locked=r.locked, changed=r.changed, reason=r.reason)
    db.add_all(made.values())
    await db.flush()
    await refresh_metrics(db, row, raw)
    return row


async def list_drafts(db: AsyncSession, office_id: UUID | None = None, include_archived: bool = False) -> list[TemplateDraft]:
    stmt = select(TemplateDraft).order_by(TemplateDraft.updated_at.desc())
    if office_id:
        stmt = stmt.where(TemplateDraft.office_id == office_id)
    if not include_archived:
        stmt = stmt.where(TemplateDraft.status != "archived")
    return list((await db.scalars(stmt)).all())


async def rename(db: AsyncSession, row: TemplateDraft, name: str) -> None:
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise TemplateError("下書きの名前は 1〜120 文字で入れてください")
    row.name = name


async def set_archived(db: AsyncSession, row: TemplateDraft, archived: bool) -> None:
    if row.status == "committed":
        raise TemplateConflict("登録済みの下書きはしまえません")
    row.status = "archived" if archived else "draft"


# ---------------------------------------------------------------------------
# 数え直し・枠の保存・履歴
# ---------------------------------------------------------------------------


async def refresh_metrics(db: AsyncSession, row: TemplateDraft, raw: dict | None = None,
                          ed: dict | None = None) -> list[dict]:
    ed = ed or await engine_draft(db, row, raw)
    ms = E.metrics_of(ed)
    row.metrics = {"totals": E.totals(ms) if ms else {}, "days": {str(m["weekday"]): {
        "master": E.slim_eval(m["master"]), "draft": E.slim_eval(m["draft"]), "changes": m["changes"]} for m in ms}}
    return ms


async def sync_slots(db: AsyncSession, row: TemplateDraft, ed: dict) -> list[dict]:
    """計算用 dict の割り当て → ``template_draft_slots``。変わった枠の {key, before, after} を返す。"""
    prev = {row_key(r): r for r in await slot_rows(db, row.id)}
    held = set(ed.get("held") or [])
    seen = set()
    changes = []
    for p in P.build_week(ed):
        master = E.master_assign(p)
        mine = E.draft_assign(ed, p)
        for s in p["slots"]:
            k = s["key"]
            a = mine.get(k)
            old = prev.get(k)
            if s.get("new"):
                if a is not None:
                    state = {"course": a["course"], "start": a["start"], "source": "new", "reason": None}
                elif k in ed["unplaced"]:
                    state = {"course": None, "start": s["start"], "source": "new", "reason": ed["unplaced"][k]}
                else:
                    continue  # 置いていない新しい枠 (対象から外れた方)
            else:
                a = a or master.get(k)
                m = master.get(k)
                src = "manual" if k in held else ("master" if (a == m) else "optimised")
                state = {"course": a["course"], "start": a["start"], "source": src, "reason": None}
            seen.add(k)
            before = slot_state(old)
            changed = not s.get("new") and (state["course"] != s["cur_course"] or state["start"] != s["start"])
            after = {"placed": state["course"] is not None or state["source"] != "new", **state}
            if old is None:
                db.add(TemplateDraftSlot(
                    draft_id=row.id, patient_id=UUID(s["patient_id"]), weekday=p["weekday"], slot_index=s["slot_index"],
                    start_time=to_time(state["start"]), duration_min=s["dur"],
                    course_template_id=UUID(state["course"]) if state["course"] else None,
                    source=state["source"], locked=bool(s["locked"]), changed=changed, reason=state["reason"]))
            else:
                old.start_time = to_time(state["start"])
                old.course_template_id = UUID(state["course"]) if state["course"] else None
                old.source = state["source"]
                old.changed = changed
                old.reason = state["reason"]
            if before != after:
                changes.append({"key": k, "patient_id": s["patient_id"], "weekday": p["weekday"],
                                "slot_index": s["slot_index"], "dur": s["dur"], "locked": bool(s["locked"]),
                                "before": before, "after": after})
    for k, old in prev.items():  # 対象から外れた新しい枠を消す
        if k not in seen and old.source == "new":
            changes.append({"key": k, "patient_id": str(old.patient_id), "weekday": old.weekday,
                            "slot_index": old.slot_index, "dur": old.duration_min, "locked": False,
                            "before": slot_state(old), "after": None})
            await db.delete(old)
    await db.flush()
    return changes


async def add_step(db: AsyncSession, row: TemplateDraft, *, actor: str, user: User | None, kind: str,
                   reason: str | None, changes: list[dict], params_before: dict | None = None,
                   params_after: dict | None = None) -> TemplateDraftStep:
    if actor not in ACTORS:
        raise TemplateError("actor は human か agent")
    # やり直しの枝 (取り消した履歴) は新しい変更で消える
    await db.execute(delete(TemplateDraftStep).where(TemplateDraftStep.draft_id == row.id,
                                                     TemplateDraftStep.undone.is_(True)))
    seq = (await db.scalar(select(func.max(TemplateDraftStep.seq)).where(TemplateDraftStep.draft_id == row.id))) or 0
    row.version += 1
    step = TemplateDraftStep(
        id=uuid4(), draft_id=row.id, seq=seq + 1, actor=actor, actor_user_id=user.id if user else None, kind=kind,
        reason=(reason or "").strip() or None, changes=changes, params_before=params_before,
        params_after=params_after, undone=False, version_after=row.version)
    db.add(step)
    await db.flush()
    return step


def step_json(s: TemplateDraftStep, patient_names: dict[str, str] | None = None) -> dict:
    return {
        "seq": s.seq, "actor": s.actor, "actor_label": "らく助エージェント" if s.actor == "agent" else "あなた",
        "actor_user_id": str(s.actor_user_id) if s.actor_user_id else None, "kind": s.kind, "reason": s.reason,
        "undone": bool(s.undone), "version_after": s.version_after,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "changed_slots": len(s.changes or []),
        "changes": [{"key": c["key"], "patient_id": c["patient_id"], "weekday": c["weekday"],
                     "patient": (patient_names or {}).get(c["patient_id"]),
                     "before": c.get("before"), "after": c.get("after")} for c in (s.changes or [])][:60],
        "params_changed": s.params_before != s.params_after,
    }


async def list_steps(db: AsyncSession, draft_id: UUID, limit: int = 50) -> list[TemplateDraftStep]:
    return list((await db.scalars(select(TemplateDraftStep).where(TemplateDraftStep.draft_id == draft_id)
                                  .order_by(TemplateDraftStep.seq.desc()).limit(limit))).all())


# ---------------------------------------------------------------------------
# 手で動かす・もしも・足す/外す
# ---------------------------------------------------------------------------


async def move_slot(db: AsyncSession, row: TemplateDraft, user: User, *, actor: str, key: str, course_id: str,
                    start: str, reason: str | None, expected_version: int | None) -> dict:
    check_version(row, expected_version)
    ed = await engine_draft(db, row)
    try:
        st = M.hm(start)
    except ValueError:
        raise TemplateError("時刻は HH:MM で書いてください") from None
    chk = E.check_move(ed, key, course_id, st)
    ed["assign"][key] = {"course": chk["course"], "start": chk["start"]}
    ed["unplaced"].pop(key, None)
    if key not in ed["held"]:
        ed["held"].append(key)
    changes = await sync_slots(db, row, ed)
    await refresh_metrics(db, row, ed=ed)
    step = await add_step(db, row, actor=actor, user=user, kind="move", reason=reason, changes=changes)
    return {"step": step, "check": chk}


async def update_whatif(db: AsyncSession, row: TemplateDraft, user: User, *, actor: str, patch: dict,
                        reason: str | None, expected_version: int | None) -> TemplateDraftStep:
    """もしもの条件 (職員の数・職員を外す・希望の仮置き・赤の扱い) を書く。解き直しは最適化で。"""
    check_version(row, expected_version)
    params = copy.deepcopy(row.params or {})
    sc = {**E.default_scenario(), **(params.get("scenario") or {})}
    before = copy.deepcopy(sc)
    raw = await R.build_raw(db)
    if patch.get("clear"):
        sc = {**E.default_scenario(), **{k: sc[k] for k in ("red", "no_pref", "cap")}}
    if "staff_count" in patch and patch["staff_count"] is not None:
        for k, v in patch["staff_count"].items():
            if v is None:
                sc["staff_count"].pop(str(k), None)
                continue
            if int(v) < 1:
                raise TemplateError("職員の数は 1 人以上です")
            sc["staff_count"][str(P.day_index(k))] = int(v)
    codes = {s.get("code") for s in raw.get("staff") or []}
    if patch.get("drop_staff") is not None:
        for c in patch["drop_staff"]:
            if c not in codes:
                raise TemplateError(f"職員コード {c} が見つかりません")
        sc["drop_staff"] = list(dict.fromkeys(patch["drop_staff"]))
    if patch.get("prefs"):
        pat_codes = {p.get("code") for p in raw.get("patients") or []} | {c.get("code") for c in raw.get("candidates") or []}
        for code, days in patch["prefs"].items():
            if code not in pat_codes:
                raise TemplateError(f"患者コード {code} が見つかりません")
            for wd, rng in (days or {}).items():
                sc["prefs"].setdefault(code, {})[str(P.day_index(wd))] = P.parse_hm_range(rng)
    if patch.get("red") in ("keep", "ignore"):
        sc["red"] = patch["red"]
    if sc == before:
        raise TemplateError("もしもの条件が変わっていません")
    params["scenario"] = sc
    row.params = params
    ed = await engine_draft(db, row, raw)
    await refresh_metrics(db, row, raw, ed)
    return await add_step(db, row, actor=actor, user=user, kind="whatif", reason=reason, changes=[],
                          params_before=before, params_after=sc)


async def add_patient(db: AsyncSession, row: TemplateDraft, user: User, *, actor: str, code: str,
                      days: list[Any] | None, win: str | None, reason: str | None,
                      expected_version: int | None) -> TemplateDraftStep:
    check_version(row, expected_version)
    raw = await R.build_raw(db)
    cands = P._candidates_by_code(raw)
    if code not in cands:
        raise TemplateError(f"{code} の方が見つかりません（稼働中か開始前で、枠の無い方だけ足せます）")
    params = copy.deepcopy(row.params or {})
    sc = {**E.default_scenario(), **(params.get("scenario") or {})}
    before = copy.deepcopy(sc)
    spec: dict = {}
    if days:
        spec["days"] = sorted({P.day_index(d) for d in days})
    if win:
        spec["win"] = P.parse_hm_range(win)
    sc["add_patients"][code] = spec
    params["scenario"] = sc
    row.params = params
    ed = await engine_draft(db, row, raw)
    await refresh_metrics(db, row, raw, ed)
    return await add_step(db, row, actor=actor, user=user, kind="add", reason=reason, changes=[],
                          params_before=before, params_after=sc)


async def remove_patient(db: AsyncSession, row: TemplateDraft, user: User, *, actor: str, code: str,
                         reason: str | None, expected_version: int | None) -> TemplateDraftStep:
    check_version(row, expected_version)
    params = copy.deepcopy(row.params or {})
    sc = {**E.default_scenario(), **(params.get("scenario") or {})}
    if code not in sc["add_patients"]:
        raise TemplateError(f"{code} は足した方ではありません（足した方だけ外せます）")
    before = copy.deepcopy(sc)
    sc["add_patients"].pop(code)
    params["scenario"] = sc
    row.params = params
    raw = await R.build_raw(db)
    ed = await engine_draft(db, row, raw)
    cand = P._candidates_by_code(raw).get(code) or {}
    # この方の置いた枠・置けなかった枠を消す (まだ枠の無い希望の方は、足さなければ置かれない)
    ed["assign"] = {k: v for k, v in ed["assign"].items() if not (k.startswith("new:") and cand.get("id") in k)}
    ed["unplaced"] = {k: v for k, v in ed["unplaced"].items() if cand.get("id") not in k}
    changes = await sync_slots(db, row, ed)
    await refresh_metrics(db, row, raw, ed)
    return await add_step(db, row, actor=actor, user=user, kind="remove", reason=reason, changes=changes,
                          params_before=before, params_after=sc)


# ---------------------------------------------------------------------------
# 取り消す・やり直す (下書きだけの履歴。アプリの op-log とは別)
# ---------------------------------------------------------------------------


async def _apply_states(db: AsyncSession, row: TemplateDraft, changes: list[dict], side: str) -> None:
    rows = {row_key(r): r for r in await slot_rows(db, row.id)}
    for c in changes:
        state = c.get(side)
        k = c["key"]
        cur = rows.get(k)
        pid, wd, si, is_new = parse_key(k)
        if state is None:
            if cur is not None:
                await db.delete(cur)
            continue
        course = UUID(state["course"]) if state.get("course") else None
        if cur is None:
            db.add(TemplateDraftSlot(
                draft_id=row.id, patient_id=pid, weekday=wd, slot_index=si, start_time=to_time(state["start"]),
                duration_min=c.get("dur") or 35, course_template_id=course, source=state["source"],
                locked=bool(c.get("locked")), changed=state["source"] != "master", reason=state.get("reason")))
        else:
            cur.start_time = to_time(state["start"])
            cur.course_template_id = course
            cur.source = state["source"]
            cur.reason = state.get("reason")
            cur.changed = state["source"] != "master"
    await db.flush()


async def undo(db: AsyncSession, row: TemplateDraft, user: User, *, actor: str, expected_version: int | None) -> TemplateDraftStep:
    check_version(row, expected_version)
    step = await db.scalar(select(TemplateDraftStep).where(
        TemplateDraftStep.draft_id == row.id, TemplateDraftStep.undone.is_(False))
        .order_by(TemplateDraftStep.seq.desc()).limit(1))
    if step is None:
        raise TemplateError("取り消せる操作がありません")
    await _apply_states(db, row, step.changes or [], "before")
    if step.params_before is not None:
        row.params = {**(row.params or {}), "scenario": step.params_before}
    step.undone = True
    row.version += 1
    await db.flush()
    await refresh_metrics(db, row)
    return step


async def redo(db: AsyncSession, row: TemplateDraft, user: User, *, actor: str, expected_version: int | None) -> TemplateDraftStep:
    check_version(row, expected_version)
    step = await db.scalar(select(TemplateDraftStep).where(
        TemplateDraftStep.draft_id == row.id, TemplateDraftStep.undone.is_(True))
        .order_by(TemplateDraftStep.seq.asc()).limit(1))
    if step is None:
        raise TemplateError("やり直せる操作がありません")
    await _apply_states(db, row, step.changes or [], "after")
    if step.params_after is not None:
        row.params = {**(row.params or {}), "scenario": step.params_after}
    step.undone = False
    row.version += 1
    await db.flush()
    await refresh_metrics(db, row)
    return step


# ---------------------------------------------------------------------------
# 見える形 (画面用)
# ---------------------------------------------------------------------------


def _course_json(c: dict) -> dict:
    return {"id": c["id"], "label": c["label"], "manager": bool(c["manager"]), "virtual": bool(c.get("virtual")),
            "staff": M.short_name((c["staff"] or {}).get("name")) if c.get("staff") else "（仮の職員）",
            "shift": c["shift"], "events": c["events"], "cap": c["cap"]}


async def build_view(db: AsyncSession, row: TemplateDraft, compare_with: UUID | None = None) -> dict:
    raw = await R.build_raw(db)
    ed = await engine_draft(db, row, raw)
    other = None
    other_name = "いまの型（マスター）"
    if compare_with:
        orow = await get_draft(db, compare_with)
        oed = await engine_draft(db, orow, raw)
        other = oed
        other_name = orow.name
    templates = sorted((t for t in raw["templates"] if t["office_id"] == row.office_id.__str__()
                        and M.COURSE_CODE_RE.match(t["label"] or "")), key=lambda t: (t["label"] == M.MANAGER_LABEL, t["label"]))
    ofc_label = ed["office"]
    days, ms_all = [], []
    for p in P.build_week(ed):
        om = None
        if other:
            q = P.build_day(other, p["weekday"])
            if q:
                om = (q, E.draft_assign(other, q))
        cards = E.slot_cards(ed, p, om)
        m = E.day_metrics(ed, p)
        ms_all.append(m)
        days.append({
            "weekday": p["weekday"], "name": M.WD[p["weekday"]], "courses": [_course_json(c) for c in p["courses"]],
            "slots": cards, "notes": p["notes"],
            "metrics": {"master": E.slim_eval(m["master"]), "draft": E.slim_eval(m["draft"]),
                        "changes": m["changes"], "new_placed": m["new_placed"], "new_unplaced": m["new_unplaced"]},
        })
    tray = []
    placed_codes = set()
    for s in (c for d in days for c in d["slots"] if c["new"]):
        placed_codes.add(s["code"])
    for sp in P.candidate_specs(ed):
        code = sp["patient"].get("code")
        unpl = [c for d in days for c in d["slots"] if c["new"] and c["code"] == code and not c["placed"]]
        placed = [c for d in days for c in d["slots"] if c["new"] and c["code"] == code and c["placed"]]
        if placed and not unpl:
            continue
        tray.append({
            "patient_id": sp["patient"]["id"], "code": code, "name": sp["patient"]["name"],
            "days": [M.WD[d] for d in sp["days"]], "reason": sp["reason"] or (unpl[0]["unplaced_reason"] if unpl else None),
            "state": "unplaced" if unpl else ("blocked" if sp["reason"] else "waiting"),
            "added": code in ed["scenario"].get("add_patients", {}),
        })
    names = {p["id"]: p["name"] for p in raw["patients"]} | {c["id"]: c["name"] for c in raw["candidates"]}
    steps = await list_steps(db, row.id, 30)
    return {
        "id": str(row.id), "name": row.name, "status": row.status, "version": row.version,
        "office": {"id": str(row.office_id), "label": ofc_label},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "scenario": ed["scenario"], "compare": {"name": other_name, "draft_id": str(compare_with) if compare_with else None},
        "courses": [{"id": t["id"], "label": f"{ofc_label}{t['label']}", "short": t["label"],
                     "manager": t["label"] == M.MANAGER_LABEL} for t in templates],
        "days": days, "tray": tray,
        "staff": [{"code": x["code"], "name": M.short_name(x["name"])} for x in sorted(raw["staff"], key=lambda x: x["code"] or "")
                  if x["primary_office_id"] == str(row.office_id) and not x["is_trainee"] and x["code"]],
        "totals": E.totals(ms_all) if ms_all else {"master": {}, "draft": {}},
        "steps": [step_json(s, names) for s in steps],
        "latest_step": step_json(next((s for s in steps if not s.undone), steps[0]), names) if steps else None,
        "can_undo": any(not s.undone for s in steps), "can_redo": any(s.undone for s in steps),
    }


async def compare(db: AsyncSession, row: TemplateDraft, other_id: UUID | None) -> dict:
    """見比べ: 日ごとの数 (相手 → この下書き) と、違う枠の一覧。other_id=None はマスター。"""
    raw = await R.build_raw(db)
    ed = await engine_draft(db, row, raw)
    name_b = "いまの型（マスター）"
    oed = None
    if other_id:
        orow = await get_draft(db, other_id)
        oed = await engine_draft(db, orow, raw)
        name_b = orow.name
    days, rows = [], []
    for p in P.build_week(ed):
        mine = E.draft_assign(ed, p)
        if oed:
            q = P.build_day(oed, p["weekday"]) or p
            theirs = E.draft_assign(oed, q) if q is not p else {}
        else:
            q, theirs = p, E.master_assign(p)
        ma, mb = M.evaluate(p, mine), M.evaluate(q, theirs)
        labels_p = {c["id"]: c["label"] for c in p["courses"]}
        labels_q = {c["id"]: c["label"] for c in q["courses"]}
        n = 0
        for s in p["slots"]:
            a, b = mine.get(s["key"]), theirs.get(s["key"])
            if (a or None) != (b or None):
                n += 1
                rows.append({"key": s["key"], "weekday": M.WD[p["weekday"]], "patient": s["patient"], "red": s["locked"],
                             "a": {"course": labels_p.get((a or {}).get("course"), "（コースなし）") if a else None,
                                   "start": M.fmt(a["start"]) if a else None},
                             "b": {"course": labels_q.get((b or {}).get("course"), "（コースなし）") if b else None,
                                   "start": M.fmt(b["start"]) if b else None}})
        days.append({"weekday": M.WD[p["weekday"]], "differ": n, "a": E.slim_eval(ma), "b": E.slim_eval(mb)})
    return {"a": row.name, "b": name_b, "days": days, "changed": rows}


async def inputs_view(db: AsyncSession, row: TemplateDraft, weekday: int) -> dict:
    """最適化が使う前提を、そのまま見せる (距離・職員の勤務/休み/予定・時刻の決まり方)."""
    raw = await R.build_raw(db)
    ed = await engine_draft(db, row, raw)
    p = P.build_day(ed, weekday)
    if p is None:
        raise TemplateError("その曜日は営業日ではありません")
    present = {c["staff"]["id"] for c in p["courses"] if c.get("staff")}
    shifts = {(r["staff_id"], r["weekday"]): r for r in raw["shifts"]}
    off = []
    for s in raw["staff"]:
        if s["primary_office_id"] != row.office_id.__str__() or s["is_trainee"]:
            continue
        sh = shifts.get((s["id"], weekday))
        if s["id"] not in present and not (sh and sh["is_on"]):
            off.append(M.short_name(s["name"]))
    slots = [s for s in p["slots"] if not s.get("new") or s["key"] in (ed["assign"] or {})]
    labels = {c["id"]: c["label"] for c in p["courses"]}
    uniq = []
    seen = set()
    for s in sorted(slots, key=lambda s: s["start"]):
        if s["patient_id"] in seen:
            continue
        seen.add(s["patient_id"])
        uniq.append(s)
    matrix = [[M.travel_min(a["loc"], b["loc"]) if a is not b else 0 for b in uniq] for a in uniq]
    rules = []
    for s in p["slots"]:
        if s["locked"] and ed["scenario"].get("red", "keep") == "keep":
            rule = "red"
        elif s["win"][0] == s["win"][1]:
            rule = "fixed"
        else:
            rule = "window"
        rules.append({"key": s["key"], "patient": s["patient"], "code": s["patient_code"], "start": s["start"],
                      "dur": s["dur"], "rule": rule, "win": s["win"], "why": s["why"], "new": bool(s.get("new")),
                      "courses": [labels[c] for c in s["allowed"] if c in labels]})
    return {
        "weekday": weekday, "name": M.WD[weekday],
        "rules": {"speed_kmh": M.SPEED_KMH, "buffer_min": M.BUFFER_MIN, "same_address_m": 100,
                  "pair_block_min": M.PAIR_BLOCK_MIN, "lunch_window": list(M.LUNCH_WIN),
                  "lunch_min": M.LUNCH_MIN_HARD, "lunch_target": M.LUNCH_TARGET, "event_buffer_min": M.EVENT_BUFFER_MIN,
                  "cap_regular": p["cap_regular"], "business_end": M.BUSINESS_END,
                  "time_order": "赤ピン=曜日と時刻を守る → 赤でない枠は今の時刻を第一に、だめなら希望の範囲の中だけで動かす（曜日は動かさない）"},
        "courses": [_course_json(c) for c in p["courses"]],
        "off_staff": off, "slots": rules,
        "matrix": {"patients": [{"name": s["patient"], "code": s["patient_code"]} for s in uniq], "minutes": matrix},
        "notes": p["notes"],
    }
