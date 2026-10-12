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
from . import report as TR
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


ALL_LABEL = "全拠点"


def office_ids_of(row: TemplateDraft) -> list[str]:
    """下書きの拠点。1 拠点の下書きは [office_id]、全拠点の下書き (office_id NULL) は params.offices。"""
    if row.office_id is not None:
        return [str(row.office_id)]
    return [str(x) for x in (row.params or {}).get("offices") or []]


def is_all_offices(row: TemplateDraft) -> bool:
    return row.office_id is None


def _route_adds(raw: dict, scenario: dict, offices: list[str], owner: dict[str, str]) -> dict[str, str]:
    """足した方 (コード) → 置く拠点。主担当拠点が下書きの拠点ならそこ、ちがえば最初の拠点。"""
    cands = {c.get("code"): c for c in raw.get("candidates") or [] if c.get("code")}
    out = {}
    for code in (scenario.get("add_patients") or {}):
        c = cands.get(code) or {}
        oid = owner.get(c.get("id") or "") or (c.get("primary_office_id") if c.get("primary_office_id") in offices
                                                 else offices[0])
        out[code] = oid
    return out


async def engine_parts(db: AsyncSession, row: TemplateDraft, raw: dict | None = None) -> list[dict]:
    """DB の下書き → 計算用の dict を拠点ごとに (材料は今のマスターから読み直す)。
    1 拠点の下書きは 1 つ。全拠点の下書きは拠点ごとに 1 つ (枠はその方の拠点の dict に入る)。"""
    raw = raw or await R.build_raw(db)
    offices = office_ids_of(row)
    by_id = {o["id"]: o for o in raw["offices"]}
    if not offices or any(oid not in by_id for oid in offices):
        raise TemplateError("下書きの拠点が見つかりません")
    p = row.params or {}
    scenario = p.get("scenario") or {}
    multi = is_all_offices(row)
    scopes = (p.get("scopes") or {}) if multi else {offices[0]: p.get("scope") or []}
    owner: dict[str, str] = {}
    for oid in offices:
        for pid in scopes.get(oid) or []:
            owner.setdefault(pid, oid)
    adds_to = _route_adds(raw, scenario, offices, owner) if multi else {}
    cand_ids = {c.get("code"): c.get("id") for c in raw.get("candidates") or []}
    for code, oid in adds_to.items():
        if cand_ids.get(code):
            owner.setdefault(cand_ids[code], oid)
    parts = {oid: ({}, [], {}) for oid in offices}
    for r in await slot_rows(db, row.id):
        oid = owner.get(str(r.patient_id), offices[0]) if multi else offices[0]
        assign, held, unplaced = parts[oid]
        k = row_key(r)
        if r.source == "new" and r.course_template_id is None:
            unplaced[k] = r.reason or "入りきらない"
            continue
        assign[k] = {"course": str(r.course_template_id) if r.course_template_id else None,
                     "start": from_time(r.start_time)}
        if r.source == "manual":
            held.append(k)
    out = []
    for oid in offices:
        sc = scenario
        if multi:
            sc = {**scenario, "add_patients": {c: v for c, v in (scenario.get("add_patients") or {}).items()
                                               if adds_to.get(c) == oid}}
        assign, held, unplaced = parts[oid]
        out.append(E.make_draft(by_id[oid]["short_label"], oid, raw, scopes.get(oid) or [], sc,
                                assign, held, unplaced))
    return out


async def engine_draft(db: AsyncSession, row: TemplateDraft, raw: dict | None = None) -> dict:
    """1 拠点の下書きの計算用の dict (全拠点の下書きは engine_parts を使う)。"""
    parts = await engine_parts(db, row, raw)
    if len(parts) != 1:
        raise TemplateError("全拠点の下書きは拠点ごとに計算します")
    return parts[0]


def _sum_eval(evs: list[dict]) -> dict:
    """拠点ごとの数 (slim_eval) を 1 つに足す。件数の並びはつなげ、差は付け直す。"""
    evs = [e for e in evs if e]
    if not evs:
        return {}
    if len(evs) == 1:
        return evs[0]
    out: dict = {}
    for k, v in evs[0].items():
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, int | float):
            out[k] = sum(e.get(k) or 0 for e in evs)
        elif isinstance(v, list):
            out[k] = [x for e in evs for x in (e.get(k) or [])]
        else:
            out[k] = v
    if "regular_counts" in out:
        rc = out["regular_counts"]
        out["count_spread"] = (max(rc) - min(rc)) if rc else 0
    return out


# ---------------------------------------------------------------------------
# 作る・一覧
# ---------------------------------------------------------------------------


async def create_draft(db: AsyncSession, user: User, office_id: UUID | None, name: str,
                       from_draft_id: UUID | None = None, offices: list[UUID] | None = None) -> TemplateDraft:
    """いまの型（マスター）から下書きを作る。office_id=None + offices = 全拠点の下書き (拠点ごとに写す)。"""
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise TemplateError("下書きの名前は 1〜120 文字で入れてください")
    raw = await R.build_raw(db)
    by_id = {o["id"]: o for o in raw["offices"]}
    oids = [str(office_id)] if office_id is not None else list(dict.fromkeys(str(o) for o in offices or []))
    if not oids or any(o not in by_id for o in oids):
        raise TemplateError("拠点が見つかりません")
    scopes: dict[str, list[str]] = {}
    seen: set[str] = set()
    for oid in oids:  # 同じ方が 2 つの拠点に出ても、先の拠点だけに入れる
        scopes[oid] = [pid for pid in P.scope_patients(raw, by_id[oid]["short_label"]) if pid not in seen]
        seen |= set(scopes[oid])
    scope = [pid for oid in oids for pid in scopes[oid]]
    basis = R.master_hashes(raw["pfv"], scope)
    scenario = E.default_scenario()
    src = None
    origin = None
    same: set[str] = set()  # 元の下書きを作った後もマスターが変わっていない患者（その方の枠だけ元の下書きから写す）
    if from_draft_id:
        src = await get_draft(db, from_draft_id)
        if office_ids_of(src) != oids:
            raise TemplateError("別の拠点（または拠点の組み合わせが違う）の下書きからは写せません")
        scenario = copy.deepcopy((src.params or {}).get("scenario") or scenario)
        old_basis = (src.params or {}).get("basis") or {}
        same = {pid for pid, h in basis.items() if old_basis.get(pid) == h}
        origin = src.id
    params = {"scenario": scenario, "scope": scope, "basis": basis}
    if office_id is None:
        params |= {"offices": oids, "scopes": scopes}
    row = TemplateDraft(
        id=uuid4(), office_id=office_id, name=name, base_snapshot_hash=R.combined_hash(basis),
        origin_draft_id=origin, created_by=user.id, status="draft", version=1,
        params=params, metrics={},
    )
    db.add(row)
    await db.flush()
    # まず今のマスターから全部作り、元の下書きの枠は「マスターが変わっていない患者」の分だけ上書きする
    made: dict[str, TemplateDraftSlot] = {}
    for oid in oids:
        ed = E.make_draft(by_id[oid]["short_label"], oid, raw, scopes[oid], scenario, {}, [], {})
        for p in P.build_week(ed):
            for s in p["slots"]:
                if s.get("new") or s["key"] in made:
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


async def list_drafts(db: AsyncSession, office_id: UUID | None = None, include_archived: bool = False,
                      all_offices: bool = False) -> list[TemplateDraft]:
    stmt = select(TemplateDraft).order_by(TemplateDraft.updated_at.desc())
    if all_offices:
        stmt = stmt.where(TemplateDraft.office_id.is_(None))
    elif office_id:
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


def _as_parts(ed: dict | list[dict]) -> list[dict]:
    return ed if isinstance(ed, list) else [ed]


def merge_day_metrics(ms: list[dict]) -> dict[int, dict]:
    """拠点ごとの曜日の数 (E.day_metrics) → 曜日ごとに 1 つ (master/draft は slim で足す)。"""
    out: dict[int, dict] = {}
    for m in ms:
        d = out.setdefault(m["weekday"], {"master": [], "draft": [], "changes": 0, "new_placed": 0,
                                          "new_unplaced": 0})
        d["master"].append(E.slim_eval(m["master"]))
        d["draft"].append(E.slim_eval(m["draft"]))
        for k in ("changes", "new_placed", "new_unplaced"):
            d[k] += m[k]
    for d in out.values():
        d["master"], d["draft"] = _sum_eval(d["master"]), _sum_eval(d["draft"])
    return dict(sorted(out.items()))


async def refresh_metrics(db: AsyncSession, row: TemplateDraft, raw: dict | None = None,
                          ed: dict | list[dict] | None = None) -> list[dict]:
    parts = _as_parts(ed) if ed is not None else await engine_parts(db, row, raw)
    ms = [m for e in parts for m in E.metrics_of(e)]
    row.metrics = {"totals": E.totals(ms) if ms else {}, "days": {
        str(wd): {"master": d["master"], "draft": d["draft"], "changes": d["changes"]}
        for wd, d in merge_day_metrics(ms).items()}}
    return ms


async def sync_slots(db: AsyncSession, row: TemplateDraft, ed: dict | list[dict]) -> list[dict]:
    """計算用 dict (拠点ごとならその並び) の割り当て → ``template_draft_slots``。変わった枠の {key, before, after} を返す。"""
    prev = {row_key(r): r for r in await slot_rows(db, row.id)}
    seen: set[str] = set()
    changes = []
    for part in _as_parts(ed):
        changes += _sync_part(db, row, part, prev, seen)
    for k, old in prev.items():  # 対象から外れた新しい枠を消す
        if k not in seen and old.source == "new":
            changes.append({"key": k, "patient_id": str(old.patient_id), "weekday": old.weekday,
                            "slot_index": old.slot_index, "dur": old.duration_min, "locked": False,
                            "before": slot_state(old), "after": None})
            await db.delete(old)
    await db.flush()
    return changes


def _sync_part(db: AsyncSession, row: TemplateDraft, ed: dict, prev: dict, seen: set) -> list[dict]:
    held = set(ed.get("held") or [])
    changes = []
    for p in P.build_week(ed):
        master = E.master_assign(p)
        mine = E.draft_assign(ed, p)
        for s in p["slots"]:
            k = s["key"]
            if k in seen:
                continue
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
    return changes


async def add_step(db: AsyncSession, row: TemplateDraft, *, actor: str, user: User | None, kind: str,
                   reason: str | None, changes: list[dict], params_before: dict | None = None,
                   params_after: dict | None = None, summary: dict | None = None) -> TemplateDraftStep:
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
        params_after=params_after, undone=False, version_after=row.version, summary=summary)
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
        "has_report": s.kind == "optimise",
    }


def report_names(raw: dict, parts: list[dict]) -> tuple[dict, dict, dict]:
    """報告に使う名前: コース ID → 「稲A」・患者 ID → 名前・患者 ID → 拠点 (全拠点の下書きのときだけ)。"""
    olabel = {o["id"]: o["short_label"] for o in raw["offices"]}
    labels = {t["id"]: f"{olabel.get(t['office_id'], '')}{t['label']}" for t in raw["templates"]}
    names = {p["id"]: p["name"] for p in raw["patients"]} | {c["id"]: c["name"] for c in raw["candidates"]}
    office_of: dict[str, str] = {}
    if len(parts) > 1:
        for ed in parts:
            for pid in ed["snapshot"]["scope_patients"]:
                office_of.setdefault(pid, ed["office"])
    return labels, names, office_of


async def step_report(db: AsyncSession, row: TemplateDraft, seq: int | None = None) -> dict:
    """最適化の報告 (seq 省略 = いちばん新しい最適化)。報告を残す前の最適化は、種類ごとの分け方だけ作り直す。"""
    stmt = select(TemplateDraftStep).where(TemplateDraftStep.draft_id == row.id, TemplateDraftStep.kind == "optimise")
    stmt = stmt.where(TemplateDraftStep.seq == seq) if seq is not None else stmt.order_by(TemplateDraftStep.seq.desc())
    step = (await db.scalars(stmt.limit(1))).first()
    if step is None:
        raise TemplateNotFound("この下書きには、その最適化の記録がありません")
    summary = step.summary
    if not summary:
        raw = await R.build_raw(db)
        labels, names, _o = report_names(raw, [])
        summary = TR.fallback_summary(step.changes or [], labels, names)
    return {"draft_id": str(row.id), "draft": row.name, "seq": step.seq,
            "actor_label": "らく助エージェント" if step.actor == "agent" else "あなた", "reason": step.reason,
            "undone": bool(step.undone), "created_at": step.created_at.isoformat() if step.created_at else None,
            **summary}


async def list_steps(db: AsyncSession, draft_id: UUID, limit: int = 50) -> list[TemplateDraftStep]:
    return list((await db.scalars(select(TemplateDraftStep).where(TemplateDraftStep.draft_id == draft_id)
                                  .order_by(TemplateDraftStep.seq.desc()).limit(limit))).all())


# ---------------------------------------------------------------------------
# 手で動かす・もしも・足す/外す
# ---------------------------------------------------------------------------


def part_of_key(parts: list[dict], key: str) -> dict:
    """枠のキー → その枠のある拠点の計算用 dict (1 拠点ならそれ)。"""
    if len(parts) == 1:
        return parts[0]
    wd = int(key.split(":")[2]) if key.startswith("new:") else int(key.split(":")[1])
    for ed in parts:
        p = P.build_day(ed, wd)
        if p is not None and any(s["key"] == key for s in p["slots"]):
            return ed
    raise TemplateError("その枠は今の条件では下書きにありません")


async def move_slot(db: AsyncSession, row: TemplateDraft, user: User, *, actor: str, key: str, course_id: str,
                    start: str, reason: str | None, expected_version: int | None) -> dict:
    check_version(row, expected_version)
    parts = await engine_parts(db, row)
    try:
        st = M.hm(start)
    except ValueError:
        raise TemplateError("時刻は HH:MM で書いてください") from None
    ed = part_of_key(parts, key)
    if len(parts) > 1 and course_id is not None and not any(
            c["id"] == course_id for p in P.build_week(ed) for c in p["courses"]):
        if any(c["id"] == course_id for e in parts if e is not ed for p in P.build_week(e) for c in p["courses"]):
            raise TemplateError("別の拠点のコースへは動かせません（その方の拠点のコースの中で動かしてください）")
    chk = E.check_move(ed, key, course_id, st)
    ed["assign"][key] = {"course": chk["course"], "start": chk["start"]}
    ed["unplaced"].pop(key, None)
    if key not in ed["held"]:
        ed["held"].append(key)
    changes = await sync_slots(db, row, parts)
    await refresh_metrics(db, row, ed=parts)
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
        if is_all_offices(row) and patch["staff_count"]:
            raise TemplateError("全拠点の下書きでは、曜日ごとの職員の数のもしもは使えません。"
                                "拠点ごとの下書きで試してください（職員を外すもしもは使えます）")
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
    await refresh_metrics(db, row, raw)
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
    await refresh_metrics(db, row, raw)
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
    parts = await engine_parts(db, row, raw)
    cand = P._candidates_by_code(raw).get(code) or {}
    # この方の置いた枠・置けなかった枠を消す (まだ枠の無い希望の方は、足さなければ置かれない)
    for ed in parts:
        ed["assign"] = {k: v for k, v in ed["assign"].items() if not (k.startswith("new:") and cand.get("id") in k)}
        ed["unplaced"] = {k: v for k, v in ed["unplaced"].items() if cand.get("id") not in k}
    changes = await sync_slots(db, row, parts)
    await refresh_metrics(db, row, raw, parts)
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


def _course_json(c: dict, office: dict | None = None) -> dict:
    out = {"id": c["id"], "label": c["label"], "manager": bool(c["manager"]), "virtual": bool(c.get("virtual")),
           "staff": M.short_name((c["staff"] or {}).get("name")) if c.get("staff") else "（仮の職員）",
           "shift": c["shift"], "events": c["events"], "cap": c["cap"]}
    if office is not None:
        out |= {"office_id": office["id"], "office": office["label"]}
    return out


def _offices_json(raw: dict, parts: list[dict]) -> list[dict]:
    return [{"id": ed["office_id"], "label": ed["office"]} for ed in parts]


async def _other_parts(db: AsyncSession, raw: dict, other_id: UUID | None) -> tuple[dict[str, dict], str | None]:
    """比べる相手の下書き → 拠点 ID ごとの計算用 dict。相手に無い拠点はマスターと比べる。"""
    if not other_id:
        return {}, None
    orow = await get_draft(db, other_id)
    return {ed["office_id"]: ed for ed in await engine_parts(db, orow, raw)}, orow.name


async def build_view(db: AsyncSession, row: TemplateDraft, compare_with: UUID | None = None) -> dict:
    raw = await R.build_raw(db)
    parts = await engine_parts(db, row, raw)
    others, oname = await _other_parts(db, raw, compare_with)
    other_name = oname or "いまの型（マスター）"
    offices = _offices_json(raw, parts)
    by_wd: dict[int, dict] = {}
    ms_all: list[dict] = []
    heads: list[dict] = []
    tray: list[dict] = []
    for ed, ofc in zip(parts, offices):
        templates = sorted((t for t in raw["templates"] if t["office_id"] == ed["office_id"]
                            and M.COURSE_CODE_RE.match(t["label"] or "")),
                           key=lambda t: (t["label"] == M.MANAGER_LABEL, t["label"]))
        heads += [{"id": t["id"], "label": f"{ed['office']}{t['label']}", "short": t["label"],
                   "manager": t["label"] == M.MANAGER_LABEL, "office_id": ed["office_id"], "office": ed["office"]}
                  for t in templates]
        other = others.get(ed["office_id"])
        part_days = []
        for p in P.build_week(ed):
            om = None
            if other:
                q = P.build_day(other, p["weekday"])
                if q:
                    om = (q, E.draft_assign(other, q))
            cards = E.slot_cards(ed, p, om)
            for c in cards:
                c["office_id"] = ed["office_id"]
            m = E.day_metrics(ed, p)
            ms_all.append(m)
            part_days.append(cards)
            d = by_wd.setdefault(p["weekday"], {"weekday": p["weekday"], "name": M.WD[p["weekday"]], "courses": [],
                                                "slots": [], "notes": [], "_m": []})
            d["courses"] += [_course_json(c, ofc) for c in p["courses"]]
            d["slots"] += cards
            d["notes"] += p["notes"]
            d["_m"].append(m)
        for sp in P.candidate_specs(ed):
            code = sp["patient"].get("code")
            mine = [c for cards in part_days for c in cards if c["new"] and c["code"] == code]
            unpl = [c for c in mine if not c["placed"]]
            if mine and not unpl:
                continue
            tray.append({
                "patient_id": sp["patient"]["id"], "code": code, "name": sp["patient"]["name"],
                "office_id": ed["office_id"], "office": ed["office"],
                "days": [M.WD[d] for d in sp["days"]],
                "reason": sp["reason"] or (unpl[0]["unplaced_reason"] if unpl else None),
                "state": "unplaced" if unpl else ("blocked" if sp["reason"] else "waiting"),
                "added": code in ed["scenario"].get("add_patients", {}),
            })
    days = []
    for wd in sorted(by_wd):
        d = by_wd[wd]
        mm = merge_day_metrics(d.pop("_m"))[wd]
        d["metrics"] = mm
        days.append(d)
    names = {p["id"]: p["name"] for p in raw["patients"]} | {c["id"]: c["name"] for c in raw["candidates"]}
    steps = await list_steps(db, row.id, 30)
    oids = set(office_ids_of(row))
    multi = is_all_offices(row)
    return {
        "id": str(row.id), "name": row.name, "status": row.status, "version": row.version,
        "office": {"id": None, "label": ALL_LABEL} if multi else {"id": str(row.office_id), "label": parts[0]["office"]},
        "offices": offices, "all_offices": multi,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "scenario": parts[0]["scenario"] if not multi else {**parts[0]["scenario"],
                                                             "add_patients": (row.params or {}).get("scenario", {}).get("add_patients", {})},
        "compare": {"name": other_name, "draft_id": str(compare_with) if compare_with else None},
        "courses": heads,
        "days": days, "tray": tray,
        "staff": [{"code": x["code"], "name": M.short_name(x["name"])} for x in sorted(raw["staff"], key=lambda x: x["code"] or "")
                  if x["primary_office_id"] in oids and not x["is_trainee"] and x["code"]],
        "totals": E.totals(ms_all) if ms_all else {"master": {}, "draft": {}},
        "steps": [step_json(s, names) for s in steps],
        "latest_step": step_json(next((s for s in steps if not s.undone), steps[0]), names) if steps else None,
        "can_undo": any(not s.undone for s in steps), "can_redo": any(s.undone for s in steps),
    }


async def compare(db: AsyncSession, row: TemplateDraft, other_id: UUID | None) -> dict:
    """見比べ: 日ごとの数 (相手 → この下書き) と、違う枠の一覧。other_id=None はマスター。全拠点は拠点ごとに比べて足す。"""
    raw = await R.build_raw(db)
    parts = await engine_parts(db, row, raw)
    others, oname = await _other_parts(db, raw, other_id)
    name_b = oname or "いまの型（マスター）"
    by_wd: dict[int, dict] = {}
    rows = []
    for ed in parts:
        oed = others.get(ed["office_id"])
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
                    rows.append({"key": s["key"], "weekday": M.WD[p["weekday"]], "patient": s["patient"],
                                 "red": s["locked"], "office": ed["office"],
                                 "a": {"course": labels_p.get((a or {}).get("course"), "（コースなし）") if a else None,
                                       "start": M.fmt(a["start"]) if a else None},
                                 "b": {"course": labels_q.get((b or {}).get("course"), "（コースなし）") if b else None,
                                       "start": M.fmt(b["start"]) if b else None}})
            d = by_wd.setdefault(p["weekday"], {"differ": 0, "a": [], "b": []})
            d["differ"] += n
            d["a"].append(E.slim_eval(ma))
            d["b"].append(E.slim_eval(mb))
    days = [{"weekday": M.WD[wd], "differ": d["differ"], "a": _sum_eval(d["a"]), "b": _sum_eval(d["b"])}
            for wd, d in sorted(by_wd.items())]
    return {"a": row.name, "b": name_b, "days": days, "changed": rows}


async def inputs_view(db: AsyncSession, row: TemplateDraft, weekday: int, office_id: str | None = None) -> dict:
    """最適化が使う前提を、そのまま見せる (距離・職員の勤務/休み/予定・時刻の決まり方)。全拠点は拠点を選ぶ (省略で最初)。"""
    raw = await R.build_raw(db)
    parts = await engine_parts(db, row, raw)
    ed = next((e for e in parts if e["office_id"] == office_id), parts[0]) if office_id else parts[0]
    p = P.build_day(ed, weekday)
    if p is None:
        raise TemplateError("その曜日は営業日ではありません")
    present = {c["staff"]["id"] for c in p["courses"] if c.get("staff")}
    shifts = {(r["staff_id"], r["weekday"]): r for r in raw["shifts"]}
    off = []
    for s in raw["staff"]:
        if s["primary_office_id"] != ed["office_id"] or s["is_trainee"]:
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
        "office": {"id": ed["office_id"], "label": ed["office"]}, "offices": _offices_json(raw, parts),
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
