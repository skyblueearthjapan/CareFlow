"""案（plan.json）の読み込みと検査（設計書 §3-3）。"""

from __future__ import annotations

import json
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
    return errs


def not_undoable_ops(plan: dict, registry: Registry | None = None) -> list[int]:
    reg = registry or load_all()
    out = []
    for i, op in enumerate(plan.get("ops") or []):
        impl = reg.get(op.get("op", "")) if isinstance(op, dict) else None
        if impl is not None and impl.undo_note(op) is not None:
            out.append(i)
    return out
